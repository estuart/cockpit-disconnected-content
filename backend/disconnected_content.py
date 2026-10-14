#!/usr/bin/python3
"""Local, root-owned job engine. Runtime state and entitlement material never enter exports."""
from __future__ import annotations

import argparse
import bz2
import configparser
import contextlib
import datetime as dt
import fcntl
import glob
import gzip
import hashlib
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

VERSION = "0.1.0"
CONFIG = Path("/etc/disconnected-content/config.json")
HELPER = "/usr/libexec/disconnected-content"
GIB = 1024 ** 3
ID = re.compile(r"^[a-f0-9]{32}$")
REPO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,180}$")
NS_REPO = "{http://linux.duke.edu/metadata/repo}"
NS_COMMON = "{http://linux.duke.edu/metadata/common}"


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path):
    with open(path) as stream:
        return json.load(stream)


def digest(path, algorithm="sha256"):
    if algorithm not in {"sha256", "sha512", "sha1", "md5"}:
        raise ValueError(f"Unsupported checksum algorithm: {algorithm}")
    h = hashlib.new(algorithm, usedforsecurity=algorithm not in {"sha1", "md5"})
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_path(root, relative):
    """Do not accept URLs, traversal, absolute paths, or symlinks in content."""
    rel = PurePosixPath(relative)
    if not relative or rel.is_absolute() or ".." in rel.parts or ":" in relative or "\\" in relative:
        raise ValueError("Unsafe repository path")
    root = Path(root).resolve()
    candidate = root.joinpath(*rel.parts)
    cursor = root
    for part in rel.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("Symlinks are not accepted in repository content")
    if not candidate.resolve().is_relative_to(root):
        raise ValueError("Repository path escapes content directory")
    return candidate


def host_os():
    result = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            result[k] = v.strip('"')
    return {"name": result.get("PRETTY_NAME", "Linux"),
            "major": result.get("VERSION_ID", "0").split(".")[0], "arch": os.uname().machine}


def repositories():
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read(glob.glob("/etc/yum.repos.d/*.repo"))
    result = []
    for rid in parser.sections():
        if REPO_ID.fullmatch(rid):
            result.append({"id": rid, "name": parser[rid].get("name", rid),
                           "enabled": parser[rid].get("enabled", "0").strip() == "1"})
    return sorted(result, key=lambda r: (not r["enabled"], r["id"]))


@contextlib.contextmanager
def metadata_stream(path):
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as stream:
            yield stream
    elif path.suffix == ".bz2":
        with bz2.open(path, "rb") as stream:
            yield stream
    elif path.suffix == ".xz":
        with lzma.open(path, "rb") as stream:
            yield stream
    elif path.suffix == ".zst":
        with subprocess.Popen(["/usr/bin/zstd", "-dc", "--", str(path)], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL) as process:
            try:
                yield process.stdout
            finally:
                process.stdout.close()
                if process.wait() != 0:
                    raise ValueError("Cannot decompress repository metadata")
    elif path.suffix == ".xml":
        with path.open("rb") as stream:
            yield stream
    else:
        raise ValueError(f"Unsupported primary metadata compression: {path.suffix}")


def validate_repository(root, require_updateinfo=False, check_signatures=True):
    root = Path(root)
    repomd = safe_path(root, "repodata/repomd.xml")
    if repomd.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Repository index is too large")
    index = ET.parse(repomd).getroot()
    primary = None
    metadata_types = []
    for data in index.findall(f"{NS_REPO}data"):
        kind = data.attrib["type"]
        location = data.find(f"{NS_REPO}location")
        checksum = data.find(f"{NS_REPO}checksum")
        if location is None or checksum is None or not checksum.text:
            raise ValueError("Incomplete metadata index")
        path = safe_path(root, location.attrib["href"])
        if digest(path, checksum.attrib["type"]) != checksum.text.strip():
            raise ValueError(f"Metadata checksum mismatch: {kind}")
        metadata_types.append(kind)
        if kind == "primary":
            primary = path
    if primary is None:
        raise ValueError("Primary metadata is missing")
    if require_updateinfo and "updateinfo" not in metadata_types:
        raise ValueError("Security advisory metadata is required for this repository")
    count, total = 0, 0
    package_paths = []
    with metadata_stream(primary) as stream:
        for _, element in ET.iterparse(stream, events=("end",)):
            if element.tag != f"{NS_COMMON}package":
                continue
            location = element.find(f"{NS_COMMON}location")
            checksum = element.find(f"{NS_COMMON}checksum")
            if location is None or checksum is None or not checksum.text:
                raise ValueError("Incomplete package metadata")
            path = safe_path(root, location.attrib["href"])
            if path.suffix != ".rpm" or not path.is_file():
                raise ValueError("Referenced RPM is missing")
            if digest(path, checksum.attrib["type"]) != checksum.text.strip():
                raise ValueError("RPM checksum does not match primary metadata")
            count += 1
            total += path.stat().st_size
            package_paths.append(str(path))
            element.clear()
    if not count:
        raise ValueError("Repository contains no RPM packages")
    if check_signatures:
        for offset in range(0, len(package_paths), 100):
            checked = subprocess.run(["/usr/bin/rpmkeys", "--checksig", *package_paths[offset:offset+100]],
                                     capture_output=True, text=True, check=False)
            lines = checked.stdout.splitlines()
            if checked.returncode or len(lines) != len(package_paths[offset:offset+100]) or any(
                    "signatures OK" not in line for line in lines):
                raise ValueError("RPM signature validation failed or signing key is not trusted")
    return {"packages": count, "package_bytes": total, "metadata_types": sorted(metadata_types),
            "rpm_signatures_verified": check_signatures}


def verify_archive(path):
    """Stream the archive, without extracting it or trusting its file paths."""
    with tarfile.open(path, "r|") as archive:
        first = archive.next()
        if first is None or first.name != "manifest.json" or not first.isfile() or first.size > 32 * 1024 * 1024:
            raise ValueError("Archive must start with a bounded manifest")
        manifest = json.load(archive.extractfile(first))
        if manifest.get("format") != "disconnected-content/v1":
            raise ValueError("Unsupported bundle format")
        expected = manifest["files"]
        if not isinstance(expected, dict) or not expected:
            raise ValueError("Bundle file manifest is empty")
        seen = set()
        for member in iter(archive.next, None):
            safe_path(Path("/bundle"), member.name)
            if not member.isfile() or member.name not in expected or member.name in seen:
                raise ValueError("Unexpected, duplicate, or non-regular archive member")
            entry = expected[member.name]
            if member.size != entry["size"]:
                raise ValueError("Archive member size mismatch")
            h = hashlib.sha256()
            with archive.extractfile(member) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(block)
            if h.hexdigest() != entry["sha256"]:
                raise ValueError("Archive member checksum mismatch")
            seen.add(member.name)
        if seen != set(expected):
            raise ValueError("Archive is missing files")
    return {"files_verified": len(seen), "snapshot_id": manifest["snapshot_id"],
            "authenticated": False, "note": "Unsigned prototype bundle; hashes verify integrity only."}


class Engine:
    def __init__(self, config=None):
        self.config = config if config is not None else read_json(CONFIG)
        self.root = Path(self.config["state_dir"])
        if not self.root.is_absolute():
            raise ValueError("state_dir must be absolute")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.floor = self.config.get("reserve_gib", 5) * GIB
        for sub in ("jobs", "snapshots", "exports"):
            (self.root / sub).mkdir(exist_ok=True, mode=0o700)

    @contextlib.contextmanager
    def lock(self):
        with (self.root / ".lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def job_path(self, jid):
        if not ID.fullmatch(jid):
            raise ValueError("Invalid job identifier")
        return self.root / "jobs" / f"{jid}.json"

    def profile(self):
        path = self.root / "profile.json"
        return read_json(path) if path.exists() else {"repositories": [], "max_gib": 20}

    def save_profile(self, data):
        if self.config.get("role") != "connected":
            raise ValueError("This installation does not permit connected operations")
        repos = data.get("repositories")
        known = {r["id"] for r in repositories()}
        if not isinstance(repos, list) or not 1 <= len(repos) <= 20 or len(set(repos)) != len(repos):
            raise ValueError("Select 1 to 20 unique repositories")
        if any(not isinstance(r, str) or r not in known for r in repos):
            raise ValueError("Select repository IDs already configured on this host")
        budget = data.get("max_gib", 20)
        if isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= 2000:
            raise ValueError("Snapshot budget must be between 1 and 2000 GiB")
        result = {"repositories": repos, "max_gib": budget}
        with self.lock():
            write_json(self.root / "profile.json", result)
        return result

    def jobs(self):
        result = []
        for path in (self.root / "jobs").glob("*.json"):
            job = read_json(path)
            if job["status"] in {"queued", "running"} and time.time() - path.stat().st_mtime > 15:
                active = subprocess.run(["/usr/bin/systemctl", "is-active", "--quiet", self.unit(job["id"])],
                                        check=False).returncode == 0
                if not active:
                    with self.lock():
                        current = read_json(path)
                        if current["status"] in {"queued", "running"}:
                            current.update(status="interrupted", stage="Job stopped before completion", finished=now())
                            write_json(path, current)
                            job = current
            result.append(job)
        return sorted(result, key=lambda j: j["created"], reverse=True)

    @staticmethod
    def unit(jid):
        return f"disconnected-content-job@{jid}.service"

    def status(self):
        jobs = self.jobs()
        snapshots = [read_json(p) for p in (self.root / "snapshots").glob("*/snapshot.json")]
        usage = shutil.disk_usage(self.root)
        return {"version": VERSION, "role": self.config.get("role"), "os": host_os(),
                "storage": {"path": str(self.root), "free": usage.free, "total": usage.total,
                            "reserve": self.floor}, "profile": self.profile(), "repositories": repositories(),
                "jobs": jobs[:50], "snapshots": sorted(snapshots, key=lambda s: s["created"], reverse=True),
                "capabilities": {"import": False, "publish": False, "signed_exports": False}}

    def start(self, operation, snapshot_id=None):
        if self.config.get("role") != "connected":
            raise ValueError("This installation does not permit connected operations")
        if operation not in {"preflight", "sync", "export", "verify-export"}:
            raise ValueError("Unknown operation")
        with self.lock():
            # Read files directly: reconciliation in jobs() acquires the same lock.
            if any(read_json(p)["status"] in {"queued", "running"} for p in (self.root / "jobs").glob("*.json")):
                raise ValueError("Another job is active; wait for it to finish or stop it")
            profile = self.profile()
            if operation in {"preflight", "sync"}:
                self.save_profile_unlocked_validate(profile)
            if operation in {"export", "verify-export"}:
                if not snapshot_id or not ID.fullmatch(snapshot_id):
                    raise ValueError("Select a validated snapshot")
                snapshot = read_json(self.root / "snapshots" / snapshot_id / "snapshot.json")
                if snapshot["status"] != "validated":
                    raise ValueError("Only validated snapshots can be exported")
            jid = uuid.uuid4().hex
            job = {"id": jid, "operation": operation, "status": "queued", "stage": "Waiting for worker",
                   "created": now(), "profile": profile, "snapshot_id": snapshot_id}
            write_json(self.job_path(jid), job)
            started = subprocess.run(["/usr/bin/systemctl", "start", "--no-block", self.unit(jid)],
                                     capture_output=True, text=True, check=False)
            if started.returncode:
                job.update(status="failed", stage="Cannot start worker service", finished=now())
                write_json(self.job_path(jid), job)
                raise ValueError("Cannot start worker service; inspect the service journal")
        return job

    @staticmethod
    def save_profile_unlocked_validate(profile):
        known = {r["id"] for r in repositories()}
        if not profile["repositories"] or any(r not in known for r in profile["repositories"]):
            raise ValueError("Save a profile using repositories configured on this host")

    def update(self, job, **values):
        job.update(values)
        write_json(self.job_path(job["id"]), job)

    def run_command(self, args, job, stage):
        self.update(job, stage=stage)
        # Commands are argument arrays, never shell fragments. Logs stay on this host.
        logpath = self.root / "jobs" / f"{job['id']}.log"
        with logpath.open("ab") as log:
            log.write((stage + "\n").encode())
            log.flush()
            with subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT) as process:
                while process.poll() is None:
                    if shutil.disk_usage(self.root).free < self.floor:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        raise ValueError("Storage reserve reached; operation stopped")
                    time.sleep(1)
                if process.returncode:
                    raise ValueError(f"{stage} failed (exit {process.returncode}); inspect job logs")

    def estimate(self, profile, job):
        self.update(job, stage="Resolving repository sizes; refreshing metadata if needed")
        args = ["/usr/bin/dnf", "-q", "--disablerepo=*"]
        args += [f"--enablerepo={r}" for r in profile["repositories"]]
        args += ["repoquery", "--available", "--show-duplicates", "--disable-modular-filtering", "--qf", "%{downloadsize}"]
        # stderr is local-only; stdout contains just declared RPM byte sizes.
        with (self.root / "jobs" / f"{job['id']}.log").open("ab") as log:
            result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=900)
            log.write(result.stderr.encode())
        if result.returncode:
            raise ValueError("Cannot query repository sizes; inspect job logs")
        sizes = [int(line) for line in result.stdout.splitlines() if line.strip().isdigit()]
        if not sizes:
            raise ValueError("Selected repositories returned no package sizes")
        size = sum(sizes)
        available = shutil.disk_usage(self.root).free
        # Allow metadata and working overhead; leave enough room for a full tar export.
        required = int(size * 2.2) + self.floor + GIB
        plan = {"packages": len(sizes), "package_bytes": size, "required_free": required,
                "free_bytes": available, "budget_gib": profile["max_gib"],
                "within_budget": size <= profile["max_gib"] * GIB,
                "enough_space": available >= required,
                "note": "Declared RPM sizes; includes snapshot/export allowance, not a transfer-time estimate."}
        self.update(job, plan=plan)
        return plan

    def sync(self, job):
        profile = job["profile"]
        plan = self.estimate(profile, job)
        if not plan["within_budget"] or not plan["enough_space"]:
            raise ValueError("Preflight exceeds the snapshot budget or available storage; no RPM sync started")
        sid = job["id"]
        snapshot_dir = self.root / "snapshots" / sid
        content = snapshot_dir / "repositories"
        content.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.update(job, snapshot_id=sid)
        args = ["/usr/bin/dnf", "-y", "--disablerepo=*"]
        for rid in profile["repositories"]:
            args += [f"--enablerepo={rid}", f"--setopt={rid}.gpgcheck=1"]
        args += ["reposync", "--download-metadata", "--gpgcheck", "--download-path", str(content)]
        self.run_command(args, job, "Downloading repository packages and original metadata")
        self.update(job, stage="Verifying metadata, referenced packages, and RPM signatures")
        checked = {}
        for rid in profile["repositories"]:
            checked[rid] = validate_repository(content / rid, require_updateinfo=("baseos" in rid or "appstream" in rid))
        snapshot = {"id": sid, "created": now(), "status": "validated", "os": host_os(),
                    "repositories": checked, "package_bytes": sum(r["package_bytes"] for r in checked.values())}
        write_json(snapshot_dir / "snapshot.json", snapshot)
        self.update(job, result={"snapshot_id": sid, "repositories": checked})

    def export(self, job):
        sid = job["snapshot_id"]
        snapshot_dir = self.root / "snapshots" / sid
        snapshot = read_json(snapshot_dir / "snapshot.json")
        self.update(job, stage="Revalidating snapshot before export")
        for rid in snapshot["repositories"]:
            validate_repository(snapshot_dir / "repositories" / rid,
                                require_updateinfo=("baseos" in rid or "appstream" in rid))
        files = {}
        self.update(job, stage="Hashing content for the transfer manifest")
        for path in sorted((snapshot_dir / "repositories").rglob("*")):
            if path.is_symlink():
                raise ValueError("Snapshot contains a symlink")
            if path.is_file():
                name = path.relative_to(snapshot_dir).as_posix()
                files[name] = {"size": path.stat().st_size, "sha256": digest(path)}
            elif not path.is_dir():
                raise ValueError("Snapshot contains a special file")
        required = sum(f["size"] for f in files.values()) * 1.02 + self.floor
        if shutil.disk_usage(self.root).free < required:
            raise ValueError("Not enough free space for the complete export and storage reserve")
        manifest = {"format": "disconnected-content/v1", "snapshot_id": sid, "created": now(),
                    "os": snapshot["os"], "repositories": snapshot["repositories"], "files": files,
                    "authentication": "unsigned-prototype"}
        import io
        raw = json.dumps(manifest, sort_keys=True, indent=2).encode() + b"\n"
        dest = self.root / "exports" / f"{sid}.tar"
        partial = dest.with_suffix(".tar.partial")
        self.update(job, stage="Writing transfer archive")
        with tarfile.open(partial, "w") as archive:
            header = tarfile.TarInfo("manifest.json")
            header.size = len(raw)
            header.mode = 0o644
            archive.addfile(header, io.BytesIO(raw))
            for name in files:
                path = safe_path(snapshot_dir, name)
                info = archive.gettarinfo(str(path), arcname=name)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
        self.update(job, stage="Verifying every archived file before completing export")
        verified = verify_archive(partial)
        checksum = digest(partial)
        os.replace(partial, dest)
        sidecar = dest.with_suffix(".tar.sha256")
        sidecar.write_text(f"{checksum}  {dest.name}\n")
        self.update(job, result={"path": str(dest), "sha256": checksum, "bytes": dest.stat().st_size, **verified})

    def run(self, jid):
        job = read_json(self.job_path(jid))
        if job["status"] != "queued":
            raise ValueError("Worker accepts queued jobs only")
        def stopped(signum, frame):
            raise InterruptedError("Operation stopped; partial files are not validated snapshots")
        signal.signal(signal.SIGTERM, stopped)
        signal.signal(signal.SIGINT, stopped)
        try:
            self.update(job, status="running", started=now(), stage="Starting worker")
            if job["operation"] == "preflight":
                self.update(job, result=self.estimate(job["profile"], job))
            elif job["operation"] == "sync":
                self.sync(job)
            elif job["operation"] == "export":
                self.export(job)
            elif job["operation"] == "verify-export":
                path = self.root / "exports" / f"{job['snapshot_id']}.tar"
                self.update(job, stage="Verifying exported archive", result=verify_archive(path))
            self.update(job, status="succeeded", stage="Completed", finished=now())
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, InterruptedError)):
                state = "cancelled"
            else:
                state = "failed"
            self.update(job, status=state, stage=str(error)[:500], finished=now())
            raise

    def logs(self, jid):
        self.job_path(jid)
        path = self.root / "jobs" / f"{jid}.log"
        if not path.exists():
            return {"text": "No command output recorded yet."}
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 16000))
            return {"text": stream.read().decode(errors="replace")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "save-profile", "start", "run-job", "logs", "cancel"])
    parser.add_argument("argument", nargs="?")
    parser.add_argument("snapshot", nargs="?")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError("Administrative access is required")
    # Configuration is installed by the administrator, never supplied by the browser.
    if CONFIG.is_symlink() or CONFIG.stat().st_uid != 0 or CONFIG.stat().st_mode & 0o022:
        raise ValueError("Configuration must be root-owned and not writable by group or others")
    engine = Engine()
    if args.command == "status":
        result = engine.status()
    elif args.command == "save-profile":
        raw = sys.stdin.read(16385)
        if len(raw) > 16384:
            raise ValueError("Profile is too large")
        result = engine.save_profile(json.loads(raw))
    elif args.command == "start":
        engine.jobs()
        result = engine.start(args.argument, args.snapshot)
    elif args.command == "run-job":
        engine.run(args.argument)
        result = {"finished": True}
    elif args.command == "logs":
        result = engine.logs(args.argument)
    else:
        path = engine.job_path(args.argument)
        job = read_json(path)
        if job["status"] not in {"queued", "running"}:
            raise ValueError("Job is already finished")
        subprocess.run(["/usr/bin/systemctl", "stop", engine.unit(args.argument)], check=True)
        job = read_json(path)
        if job["status"] in {"queued", "running"}:
            engine.update(job, status="cancelled", stage="Stopped by operator", finished=now())
        result = {"stopped": args.argument}
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)

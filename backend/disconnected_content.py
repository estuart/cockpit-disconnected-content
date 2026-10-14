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
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from urllib.parse import quote, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

VERSION = "0.6.0"
CONFIG = Path("/etc/disconnected-content/config.json")
MOUNTS = Path("/proc/self/mounts")
PUBLISH_ROOT = Path("/var/www/disconnected-content")
PUBLISH_CONF = Path("/etc/httpd/conf.d/disconnected-content.conf")
HELPER = "/usr/libexec/disconnected-content"
GIB = 1024 ** 3
ID = re.compile(r"^[a-f0-9]{32}$")
REPO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,180}$")
BUNDLE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.tar$")
NS_REPO = "{http://linux.duke.edu/metadata/repo}"
NS_COMMON = "{http://linux.duke.edu/metadata/common}"


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


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


class LimitedMetadataReader:
    def __init__(self, stream, limit=512 * 1024 * 1024):
        self.stream, self.limit, self.read_bytes = stream, limit, 0

    def read(self, size=-1):
        size = min(size if size >= 0 else self.limit + 1, self.limit - self.read_bytes + 1)
        data = self.stream.read(size)
        self.read_bytes += len(data)
        if self.read_bytes > self.limit:
            raise ValueError("Decompressed repository metadata exceeds the size limit")
        return data


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


def validate_repository(root, require_updateinfo=False, check_signatures=True, include_files=False):
    root = Path(root).resolve()
    files = {"repodata/repomd.xml"}
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
        files.add(path.relative_to(root).as_posix())
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
        for _, element in ET.iterparse(LimitedMetadataReader(stream), events=("end",)):
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
            files.add(path.relative_to(root).as_posix())
            element.clear()
    if not count:
        raise ValueError("Repository contains no RPM packages")
    if check_signatures:
        for offset in range(0, len(package_paths), 100):
            checked = subprocess.run(["/usr/bin/rpmkeys", "--checksig", *package_paths[offset:offset+100]],
                                     capture_output=True, text=True, check=False,
                                     env={**os.environ, "LC_ALL": "C"})
            lines = checked.stdout.splitlines()
            if checked.returncode or len(lines) != len(package_paths[offset:offset+100]) or any(
                    line.rsplit(": ", 1)[-1] not in {"digests signatures OK", "signatures OK"} for line in lines):
                raise ValueError("RPM signature validation failed or signing key is not trusted")
    result = {"packages": count, "package_bytes": total, "metadata_types": sorted(metadata_types),
              "rpm_signatures_verified": check_signatures}
    if include_files:
        result["files"] = sorted(files)
    return result


def public_origin(value):
    if not isinstance(value, str) or len(value) > 1024 or any(c.isspace() for c in value):
        raise ValueError("Enter an HTTP or HTTPS server URL without credentials, a path, or whitespace")
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError as error:
        raise ValueError("Invalid server URL or port") from error
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username is not None
            or url.password is not None or url.path not in {"", "/"} or url.query or url.fragment
            or not re.fullmatch(r"[A-Za-z0-9.:[\]-]+", url.netloc)
            or port is not None and not 1 <= port <= 65535):
        raise ValueError("Enter an HTTP or HTTPS server URL without credentials, a path, or whitespace")
    return urlunsplit((url.scheme, url.netloc, "", "", ""))


def client_repo_config(origin, snapshot):
    origin = public_origin(origin)
    sid = snapshot["id"]
    if not isinstance(sid, str) or not ID.fullmatch(sid):
        raise ValueError("Invalid snapshot identifier")
    lines = ["# Disconnected Content: pinned to validated snapshot " + sid,
             "# Install on compatible RHEL clients as /etc/yum.repos.d/disconnected-content.repo",
             "# Trust RPM signing keys independently; this file does not enroll keys.", ""]
    for rid in sorted(snapshot["repositories"]):
        if not REPO_ID.fullmatch(rid):
            raise ValueError("Invalid repository identifier")
        lines.extend([f"[dc-{rid}]", f"name=Disconnected Content - {rid}",
                      f"baseurl={origin}/disconnected-content/snapshots/{sid}/repositories/{quote(rid, safe='')}/",
                      "enabled=1", "gpgcheck=1", "repo_gpgcheck=0", "sslverify=1",
                      "gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release",
                      "skip_if_unavailable=0", "metadata_expire=1h", ""])
    return "\n".join(lines)


def yaml_document(value, indent=0):
    """Emit the small JSON-compatible YAML subset used by generated playbooks."""
    pad = " " * indent

    def scalar(item):
        if isinstance(item, str) and "\n" in item and item.endswith("\n") and not item.endswith("\n\n"):
            return " |\n" + "".join(pad + "  " + line + "\n" for line in item.splitlines())
        return " " + json.dumps(item) + "\n"

    if isinstance(value, dict):
        return "".join(pad + json.dumps(key) + ":" + (
            "\n" + yaml_document(item, indent + 2) if isinstance(item, (dict, list)) else
            scalar(item)) for key, item in value.items())
    if isinstance(value, list):
        return "".join(pad + "-" + (
            "\n" + yaml_document(item, indent + 2) if isinstance(item, (dict, list)) else
            scalar(item)) for item in value)
    raise ValueError("Unsupported playbook structure")


def client_automation(publication):
    """Generate inert downloads from one durable publication, never run Ansible."""
    sid = publication["snapshot_id"]
    repos = publication["repositories"]
    platform = publication["os"]
    if (not isinstance(repos, dict) or not 1 <= len(repos) <= 20
            or not re.fullmatch(r"[0-9]{1,2}", platform["major"])
            or not re.fullmatch(r"[A-Za-z0-9_]+", platform["arch"])):
        raise ValueError("Invalid publication platform or repository list")
    config = client_repo_config(publication["base_url"], {"id": sid, "repositories": repos})
    if config != publication["client_config"]:
        raise ValueError("Publication configuration does not match its snapshot")
    ids = ["dc-" + rid for rid in sorted(repos)]
    advisory = all("updateinfo" in repo.get("metadata_types", []) for repo in repos.values())
    common = [{"name": "Require the publication's RHEL platform and Ansible Core 2.16 or newer",
               "ansible.builtin.assert": {"that": [
                   "ansible_version.full is version('2.16', '>=')",
                   "ansible_facts['distribution'] == 'RedHat'",
                   f"ansible_facts['distribution_major_version'] == '{platform['major']}'",
                   f"ansible_facts['architecture'] == '{platform['arch']}'"],
                   "fail_msg": "Use compatible RHEL clients and Ansible Core 2.16 or newer."}},
              {"name": "Check for the independently provisioned Red Hat RPM public keys",
               "ansible.builtin.stat": {"path": "/etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release"},
               "register": "dc_key"},
              {"name": "Require trusted RPM keys before using the repository",
               "ansible.builtin.assert": {"that": ["dc_key.stat.exists", "dc_key.stat.isreg | default(false)"],
                   "fail_msg": "Provision and verify Red Hat RPM keys independently before continuing."}}]
    configure = {"name": "Configure repositories for snapshot " + sid,
                 "hosts": "disconnected_clients", "become": True, "gather_facts": True,
                 "serial": 1, "any_errors_fatal": True, "pre_tasks": common,
                 "tasks": [
                     {"name": "Install the exact pinned repository file (back up an existing file)",
                      "ansible.builtin.copy": {"dest": "/etc/yum.repos.d/disconnected-content.repo",
                          "content": config, "owner": "root", "group": "root", "mode": "0644", "backup": True}},
                     {"name": "Refresh metadata using only this publication's repositories",
                      "ansible.builtin.command": {"argv": ["dnf", "--disablerepo=*", *["--enablerepo=" + rid for rid in ids],
                          "makecache", "--refresh"]}, "changed_when": False, "when": "not ansible_check_mode"},
                     {"name": "Explain repository check mode",
                      "ansible.builtin.debug": {"msg": "Check mode previews the repository file only. Apply this playbook before previewing security updates; metadata refresh is skipped in check mode."},
                      "when": "ansible_check_mode"}]}
    security = {"name": "Apply security updates from snapshot " + sid,
                "hosts": "disconnected_clients", "become": True, "gather_facts": True,
                "serial": 1, "any_errors_fatal": True,
                "pre_tasks": common + [
                    {"name": "Require advisory metadata in every published repository",
                     "ansible.builtin.assert": {"that": [advisory],
                         "fail_msg": "This publication lacks updateinfo metadata. Mirror advisory metadata and publish again before using security-only updates."}},
                    {"name": "Check the pinned repository file without following symlinks",
                     "ansible.builtin.stat": {"path": "/etc/yum.repos.d/disconnected-content.repo",
                         "checksum_algorithm": "sha256", "follow": False}, "register": "dc_repo"},
                    {"name": "Require the exact repository file before updating (run configure-repos.yml first)",
                     "ansible.builtin.assert": {"that": ["dc_repo.stat.isreg | default(false)",
                         "not (dc_repo.stat.islnk | default(false))",
                         "dc_repo.stat.checksum | default('') == '" + hashlib.sha256(config.encode()).hexdigest() + "'"],
                         "fail_msg": "Repository file is missing or differs from this snapshot. Run its configure-repos.yml first."}}],
                "tasks": [
                    {"name": "Apply available security updates to installed packages from the pinned repositories",
                     "ansible.builtin.dnf": {"name": "*", "state": "latest", "security": True,
                         "update_only": True, "disablerepo": ["*"], "enablerepo": ids,
                         "disable_gpg_check": False, "update_cache": True, "use_backend": "dnf4"},
                     "register": "dc_updates"},
                    {"name": "Show the transaction result; reboots remain an operator decision",
                     "ansible.builtin.debug": {"var": "dc_updates"}}]}
    header = "# Generated by Disconnected Content. Review before running.\n# Snapshot: " + sid + "\n---\n"
    inventory = ("# Replace the example host and user. Keep credentials in your existing Ansible setup.\n"
                 "[disconnected_clients]\nclient.example.org ansible_user=admin\n")
    instructions = f"""Disconnected Content client automation
Snapshot: {sid}
Published: {publication['published_at']}
Platform: RHEL {platform['major']} / {platform['arch']}

Run manually from an Ansible Core 2.16+ control node that can SSH to the clients.
Clients require Python 3 with python3-dnf, sudo/become access, independently
trusted Red Hat RPM keys, and network access to the published repository URLs.
Only ansible.builtin modules are used; no Galaxy collection download is needed.

1. Save all four downloads together. Review both playbooks and inventory.ini.
   Replace client.example.org and the SSH user with your approved client inventory.
   Use your existing SSH keys and become credentials. Add --ask-become-pass if needed.
2. Preview repository configuration on a canary host:
   ansible-playbook -i inventory.ini configure-repos.yml --limit client.example.org --check --diff
3. Apply repository configuration and refresh metadata:
   ansible-playbook -i inventory.ini configure-repos.yml --limit client.example.org --diff
4. Preview the security transaction, then apply during your maintenance window:
   ansible-playbook -i inventory.ini security-updates.yml --limit client.example.org --check --diff
   ansible-playbook -i inventory.ini security-updates.yml --limit client.example.org
5. Review the result before expanding --limit to the next approved client group.

Both playbooks run one host at a time and stop the play on a failure. Neither
playbook reboots hosts. Package scripts may restart services during an update.
Check mode can refresh DNF cache metadata, but does not install package updates.
Configuration check mode does not refresh metadata or install the repository file.
The security playbook requires the exact repository file embedded in its paired
configuration playbook and updateinfo metadata in every selected repository.
Security filtering depends on the advisories and packages actually mirrored;
zero updates does not establish that the host is fully patched. Dependencies may
also change. Other repository files are retained; update transactions select only
the exact repository IDs in this publication. Existing DNF policy still applies.
URLs stay pinned to this snapshot. Download a new pair when advancing clients.
Changing repositories does not undo installed updates. No automatic rollback,
remote execution from Cockpit, credential storage, or AAP integration is included.
"""
    return {"snapshot_id": sid, "has_updateinfo": advisory, "files": {
        "configure-repos.yml": header + yaml_document([configure]),
        "security-updates.yml": header + yaml_document([security]),
        "inventory.ini": inventory, "README-client-automation.txt": instructions}}


def bundle_manifest(archive, max_bytes, max_files):
    first = archive.next()
    if first is None or first.name != "manifest.json" or not first.isfile() or not 0 <= first.size <= 32 * 1024 * 1024:
        raise ValueError("Archive must start with a bounded manifest")
    with archive.extractfile(first) as stream:
        manifest = json.load(stream)
    if not isinstance(manifest, dict) or manifest.get("format") != "disconnected-content/v1":
        raise ValueError("Unsupported bundle format")
    expected = manifest.get("files")
    if not isinstance(expected, dict) or not 1 <= len(expected) <= max_files:
        raise ValueError("Bundle file count exceeds the allowed limit or is empty")
    total = 0
    for name, entry in expected.items():
        safe_path(Path("/bundle"), name)
        if PurePosixPath(name).as_posix() != name or len(name) > 512 or any(ord(c) < 32 for c in name):
            raise ValueError("Unsafe or non-canonical bundle path")
        if not isinstance(entry, dict) or type(entry.get("size")) is not int or entry["size"] < 0:
            raise ValueError("Invalid declared file size")
        if not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]):
            raise ValueError("Invalid declared SHA-256 checksum")
        total += entry["size"]
        if total > max_bytes:
            raise ValueError("Bundle exceeds the import size limit")
    if not isinstance(manifest.get("snapshot_id"), str) or not ID.fullmatch(manifest["snapshot_id"]):
        raise ValueError("Invalid bundle snapshot identifier")
    repos = manifest.get("repositories")
    if not isinstance(repos, dict) or not 1 <= len(repos) <= 20 or any(not REPO_ID.fullmatch(r) for r in repos):
        raise ValueError("Invalid bundle repository list")
    for name in expected:
        parts = PurePosixPath(name).parts
        if len(parts) < 3 or len(parts) > 32 or parts[0] != "repositories" or parts[1] not in repos:
            raise ValueError("Bundle content must belong to a declared repository")
    if not isinstance(manifest.get("os"), dict) or not all(isinstance(manifest["os"].get(k), str) for k in ["major", "arch"]):
        raise ValueError("Bundle OS version and architecture are required")
    return manifest, total


def verify_archive(path, extract_to=None, max_bytes=2000 * GIB, max_files=200000, reserve_bytes=0):
    """Verify a bounded stream; optionally write regular files to private staging.

    Never use extractall: archive ownership, mode, links, and paths are untrusted.
    """
    with tarfile.open(path, "r|") as archive:
        manifest, _ = bundle_manifest(archive, max_bytes, max_files)
        expected = manifest["files"]
        seen = set()
        for member in iter(archive.next, None):
            safe_path(Path("/bundle"), member.name)
            if (not member.isfile() or member.name not in expected or member.name in seen
                    or member.sparse is not None or any(k.startswith("GNU.sparse") for k in member.pax_headers)):
                raise ValueError("Unexpected, duplicate, or non-regular archive member")
            entry = expected[member.name]
            if member.size != entry["size"]:
                raise ValueError("Archive member size mismatch")
            h = hashlib.sha256()
            with contextlib.ExitStack() as stack:
                stream = stack.enter_context(archive.extractfile(member))
                output = None
                if extract_to is not None:
                    if shutil.disk_usage(extract_to).free < member.size + reserve_bytes:
                        raise ValueError("Storage reserve reached during import")
                    target = safe_path(extract_to, member.name)
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    output = stack.enter_context(target.open("xb"))
                    os.chmod(target, 0o600)
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(block)
                    if output is not None:
                        output.write(block)
            if h.hexdigest() != entry["sha256"]:
                raise ValueError("Archive member checksum mismatch")
            seen.add(member.name)
        if seen != set(expected):
            raise ValueError("Archive is missing files")
    return {"files_verified": len(seen), "snapshot_id": manifest["snapshot_id"], "verified_at": now(),
            "authenticated": False, "note": "Unsigned prototype bundle; hashes verify integrity only."}


class Engine:
    def __init__(self, config=None):
        self.config = config if config is not None else read_json(CONFIG)
        self.root = Path(self.config["state_dir"])
        if not self.root.is_absolute():
            raise ValueError("state_dir must be absolute")
        self.root = self.root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.floor = self.config.get("reserve_gib", 5) * GIB
        for sub in ("jobs", "snapshots", "exports", "incoming", ".imports"):
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

    def publication_status(self):
        installed = PUBLISH_CONF.is_file() and not PUBLISH_CONF.is_symlink()
        active = False
        if installed:
            active = subprocess.run(["/usr/bin/systemctl", "is-active", "--quiet", "httpd"],
                                    check=False, capture_output=True).returncode == 0
        path = self.root / "publication.json"
        return {"installed": installed, "server_active": active,
                "base_url": self.config.get("public_base_url", ""),
                "current": read_json(path) if path.exists() else None}

    def client_automation(self, publication_id):
        if self.config.get("role") != "disconnected":
            raise ValueError("Client automation requires the disconnected installation role")
        if not isinstance(publication_id, str) or not ID.fullmatch(publication_id):
            raise ValueError("Invalid publication identifier")
        path = self.root / "publications" / f"{publication_id}.json"
        if not path.is_file() or path.is_symlink():
            raise ValueError("Publish a snapshot before downloading client automation")
        return client_automation(read_json(path))

    def save_publication_settings(self, data):
        if self.config.get("role") != "disconnected":
            raise ValueError("Publication requires the disconnected installation role")
        if not isinstance(data, dict) or set(data) != {"base_url"}:
            raise ValueError("Specify only the public server URL")
        origin = public_origin(data["base_url"])
        with self.lock():
            if any(read_json(p)["status"] in {"queued", "running"} for p in (self.root / "jobs").glob("*.json")):
                raise ValueError("Wait for the active job before changing publication settings")
            config = read_json(CONFIG)
            config["public_base_url"] = origin
            write_json(CONFIG, config)
            self.config = config
        return {"base_url": origin}

    def publish(self, job):
        if self.config.get("role") != "disconnected" or not self.publication_status()["installed"]:
            raise ValueError("Install the publisher package on a disconnected host before publishing")
        sid = job["snapshot_id"]
        if not isinstance(sid, str) or not ID.fullmatch(sid):
            raise ValueError("Select a validated snapshot")
        source = safe_path(self.root / "snapshots", sid)
        snapshot = read_json(safe_path(source, "snapshot.json"))
        if snapshot.get("status") != "validated" or snapshot.get("id") != sid:
            raise ValueError("Only validated snapshots can be published")
        origin = public_origin(job["public_base_url"])
        config = client_repo_config(origin, snapshot)
        PUBLISH_ROOT.mkdir(parents=True, exist_ok=True, mode=0o755)
        public = PUBLISH_ROOT / "public"
        for path in [PUBLISH_ROOT, public, public / "snapshots"]:
            if path.is_symlink():
                raise ValueError("Publication storage must not contain symlinks")
            path.mkdir(exist_ok=True, mode=0o755)
            os.chmod(path, 0o755)
        private = PUBLISH_ROOT / ".staging"
        private.mkdir(exist_ok=True, mode=0o700)
        if private.is_symlink():
            raise ValueError("Publication staging must not be a symlink")
        staging = private / job["id"]
        staging.mkdir(mode=0o700)
        destination = safe_path(public / "snapshots", sid)
        try:
            self.update(job, stage="Revalidating source repositories and trusted RPM signatures")
            inventory = {}
            for rid in snapshot["repositories"]:
                repository = safe_path(source, f"repositories/{rid}")
                checked = validate_repository(repository, require_updateinfo=("baseos" in rid or "appstream" in rid),
                                              include_files=True)
                inventory[rid] = checked["files"]
            total = sum(safe_path(source, f"repositories/{rid}/{name}").stat().st_size
                        for rid, names in inventory.items() for name in names)
            if shutil.disk_usage(PUBLISH_ROOT).free < total + self.floor:
                raise ValueError("Not enough free space for a public copy and the storage reserve")
            self.update(job, stage="Copying indexed repository content to private publication staging")
            for rid, names in inventory.items():
                for name in names:
                    original = safe_path(source, f"repositories/{rid}/{name}")
                    if shutil.disk_usage(PUBLISH_ROOT).free < original.stat().st_size + self.floor:
                        raise ValueError("Storage reserve reached while publishing")
                    target = safe_path(staging, f"repositories/{rid}/{name}")
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    shutil.copyfile(original, target)
                    os.chmod(target, 0o644)
            self.update(job, stage="Verifying the public copy before exposing repository URLs")
            checked = {}
            for rid in inventory:
                checked[rid] = validate_repository(staging / "repositories" / rid,
                                                  require_updateinfo=("baseos" in rid or "appstream" in rid))
            if destination.exists():
                for rid in inventory:
                    validate_repository(destination / "repositories" / rid,
                                        require_updateinfo=("baseos" in rid or "appstream" in rid))
                    if digest(destination / "repositories" / rid / "repodata/repomd.xml") != digest(
                            staging / "repositories" / rid / "repodata/repomd.xml"):
                        raise ValueError("Different public content already uses this snapshot identifier")
            verified_at = now()
            for directory, _, _ in os.walk(staging):
                os.chmod(directory, 0o755)
            repo_file = private / f"{job['id']}.repo"
            repo_file.write_text(config)
            os.chmod(repo_file, 0o644)
            self.run_command(["/usr/sbin/restorecon", "-RF", str(PUBLISH_ROOT)], job, "Applying web-content SELinux labels")
            self.run_command(["/usr/sbin/httpd", "-t"], job, "Checking Apache configuration")
            self.run_command(["/usr/bin/systemctl", "enable", "httpd"], job, "Enabling the HTTP publisher")
            self.run_command(["/usr/bin/systemctl", "reload-or-restart", "httpd"], job, "Starting the HTTP publisher")
            if not destination.exists():
                os.rename(staging, destination)
            os.replace(repo_file, public / "client.repo")
            record = {"snapshot_id": sid, "published_at": now(), "verified_at": verified_at,
                      "base_url": origin, "config_url": f"{origin}/disconnected-content/client.repo",
                      "client_config": config, "repositories": checked, "bytes": total,
                      "os": snapshot["os"], "job_id": job["id"]}
            write_json(self.root / "publications" / f"{job['id']}.json", record)
            write_json(self.root / "publication.json", record)
            self.update(job, result=record)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
            (private / f"{job['id']}.repo").unlink(missing_ok=True)

    def incoming_directory(self, value=None):
        value = value if value is not None else self.config.get("incoming_dir", str(self.root / "incoming"))
        if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 32 for c in value):
            raise ValueError("Enter an absolute directory path on this host")
        path = Path(value)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("Enter an absolute directory path without parent traversal")
        # Canonical paths keep removable-media aliases from silently changing the source.
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("Use a real directory path, without symlinks")
        if not path.is_dir():
            raise ValueError("Source directory is unavailable; mount the media or choose an existing directory")
        return path

    def save_import_settings(self, data):
        if self.config.get("role") != "disconnected":
            raise ValueError("Source settings require the disconnected installation role")
        if not isinstance(data, dict) or set(data) != {"incoming_dir"}:
            raise ValueError("Specify only the bundle source directory")
        if not isinstance(data["incoming_dir"], str):
            raise ValueError("Enter an absolute directory path on this host")
        path = self.incoming_directory(data["incoming_dir"])
        # Test readability without creating, moving, or changing source files.
        with os.scandir(path) as entries:
            next(entries, None)
        with self.lock():
            if any(read_json(p)["status"] in {"queued", "running"} for p in (self.root / "jobs").glob("*.json")):
                raise ValueError("Wait for the active job to finish before changing the source directory")
            config = read_json(CONFIG)
            config.update(incoming_dir=str(path), incoming_updated_at=now())
            write_json(CONFIG, config)
            self.config = config
        return {"path": str(path), "updated_at": config["incoming_updated_at"]}

    def directory_locations(self):
        locations = {}

        def add(value, label, kind="folder"):
            try:
                path = self.incoming_directory(value)
                location = {"path": str(path), "label": label, "kind": kind}
                if kind == "mount":
                    locations[str(path)] = location
                else:
                    locations.setdefault(str(path), location)
            except (OSError, ValueError):
                pass  # Unmounted media and absent optional shortcut directories are omitted.

        add(self.config.get("incoming_dir", str(self.root / "incoming")), "Saved bundle source")
        add("/", "Filesystem root")
        for path in ["/mnt", "/media", "/run/media"]:
            add(path, path)
        try:
            for line in MOUNTS.read_text().splitlines():
                fields = line.split()
                if len(fields) < 3:
                    continue
                source, target = [re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)
                                  for value in fields[:2]]
                if source.startswith("/dev/") and target not in {"/", "/boot", "/boot/efi"}:
                    add(target, target, "mount")
        except OSError:
            pass  # Filesystem browsing still works without mount metadata.
        return list(locations.values())

    def browse_directories(self, data):
        if self.config.get("role") != "disconnected":
            raise ValueError("Directory browsing requires the disconnected installation role")
        if not isinstance(data, dict) or set(data) != {"path"} or not isinstance(data["path"], str):
            raise ValueError("Specify only an absolute directory path to browse")
        path = self.incoming_directory(data["path"])
        directories = []
        truncated = False
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    if any(ord(c) < 32 for c in entry.name) or not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                if len(directories) == 500:
                    truncated = True
                    break
                directories.append({"name": entry.name, "path": str(path / entry.name)})
        return {"path": str(path), "parent": str(path.parent) if path != path.parent else None,
                "directories": sorted(directories, key=lambda item: (item["name"].casefold(), item["name"])),
                "locations": self.directory_locations(), "truncated": truncated}

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
                            current.update(status="interrupted", stage="Job stopped before completion", finished=now(), updated=now())
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
        source, incoming = self.scan_incoming() if self.config.get("role") == "disconnected" else (None, [])
        return {"version": VERSION, "role": self.config.get("role"), "os": host_os(),
                "storage": {"path": str(self.root), "free": usage.free, "total": usage.total,
                            "reserve": self.floor}, "profile": self.profile(), "repositories": repositories(),
                "jobs": jobs[:50], "snapshots": sorted(snapshots, key=lambda s: s["created"], reverse=True),
                "incoming": incoming, "import_source": source,
                "publication": self.publication_status() if self.config.get("role") == "disconnected" else None,
                "capabilities": {"import": self.config.get("role") == "disconnected", "publish": self.config.get("role") == "disconnected",
                                 "signed_exports": False, "allow_unsigned_imports": self.config.get("allow_unsigned_imports") is True}}

    def scan_incoming(self):
        source = {"path": self.config.get("incoming_dir", str(self.root / "incoming")),
                  "updated_at": self.config.get("incoming_updated_at"), "scanned_at": now(), "available": False}
        result = []
        try:
            directory = self.incoming_directory()
            with os.scandir(directory) as entries:
                for entry in entries:
                    if not BUNDLE_NAME.fullmatch(entry.name):
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode):
                            continue
                        sidecar = directory / (entry.name + ".sha256")
                        result.append({"name": entry.name, "bytes": info.st_size,
                                       "modified": dt.datetime.fromtimestamp(info.st_mtime, dt.timezone.utc).isoformat(),
                                       "has_checksum": not sidecar.is_symlink() and sidecar.is_file()})
                    except FileNotFoundError:
                        continue  # A transfer may be renamed while the inbox is being scanned.
            source["available"] = True
        except (OSError, ValueError) as error:
            source["error"] = str(error)
            result = []
        return source, sorted(result, key=lambda item: (item["modified"], item["name"]), reverse=True)

    def incoming(self):
        return self.scan_incoming()[1]

    def bundle_path(self, name, directory=None):
        if not isinstance(name, str) or not BUNDLE_NAME.fullmatch(name):
            raise ValueError("Select a transfer archive from the incoming directory")
        path = safe_path(self.incoming_directory(directory), name)
        if not path.is_file():
            raise ValueError("Transfer archive is missing")
        return path

    def start(self, operation, snapshot_id=None):
        if operation not in {"preflight", "sync", "export", "verify-export", "import", "publish"}:
            raise ValueError("Unknown operation")
        if operation in {"import", "publish"}:
            if self.config.get("role") != "disconnected":
                raise ValueError("Import and publication require the disconnected installation role")
        elif self.config.get("role") != "connected":
            raise ValueError("This installation does not permit connected operations")
        with self.lock():
            source = self.incoming_directory() if operation == "import" else None
            if source is not None:
                self.bundle_path(snapshot_id, str(source))
            # Read files directly: reconciliation in jobs() acquires the same lock.
            if any(read_json(p)["status"] in {"queued", "running"} for p in (self.root / "jobs").glob("*.json")):
                raise ValueError("Another job is active; wait for it to finish or stop it")
            profile = self.profile()
            if operation in {"preflight", "sync"}:
                self.save_profile_unlocked_validate(profile)
            if operation in {"export", "verify-export", "publish"}:
                if not snapshot_id or not ID.fullmatch(snapshot_id):
                    raise ValueError("Select a validated snapshot")
                snapshot = read_json(self.root / "snapshots" / snapshot_id / "snapshot.json")
                if snapshot["status"] != "validated":
                    raise ValueError("Only validated snapshots can be exported")
            if operation == "publish":
                if not self.publication_status()["installed"]:
                    raise ValueError("Install cockpit-disconnected-content-publisher before publishing")
                origin = public_origin(self.config.get("public_base_url"))
            jid = uuid.uuid4().hex
            job = {"id": jid, "operation": operation, "status": "queued", "stage": "Waiting for worker",
                   "created": now(), "updated": now(), "profile": profile, "snapshot_id": snapshot_id if operation != "import" else None}
            if operation == "import":
                job["bundle_name"] = snapshot_id
                job["incoming_dir"] = str(source)
            elif operation == "publish":
                job["public_base_url"] = origin
            write_json(self.job_path(jid), job)
            started = subprocess.run(["/usr/bin/systemctl", "start", "--no-block", self.unit(jid)],
                                     capture_output=True, text=True, check=False)
            if started.returncode:
                job.update(status="failed", stage="Cannot start worker service", finished=now(), updated=now())
                write_json(self.job_path(jid), job)
                raise ValueError("Cannot start worker service; inspect the service journal")
        return job

    def import_bundle(self, job):
        if self.config.get("role") != "disconnected":
            raise ValueError("Import requires the disconnected installation role")
        if self.config.get("allow_unsigned_imports") is not True:
            raise ValueError("Unsigned imports are disabled; an administrator must explicitly enable prototype imports")
        path = self.bundle_path(job["bundle_name"], job.get("incoming_dir"))
        limit = int(self.config.get("max_import_gib", 200)) * GIB
        if path.stat().st_size > limit + GIB:
            raise ValueError("Transfer archive exceeds the import size limit")
        sidecar = safe_path(path.parent, path.name + ".sha256")
        if not sidecar.is_file():
            raise ValueError("A regular checksum sidecar is required beside the archive")
        if sidecar.stat().st_size > 1024:
            raise ValueError("Checksum sidecar is too large")
        match = re.fullmatch(r"([a-f0-9]{64}) [ *]" + re.escape(path.name) + r"\n?", sidecar.read_text())
        self.update(job, stage="Verifying transferred archive against its SHA-256 sidecar")
        checksum = digest(path)
        if match is None or match.group(1) != checksum:
            raise ValueError("Transfer checksum mismatch; archive was not imported")
        with tarfile.open(path, "r|") as archive:
            manifest, total = bundle_manifest(archive, limit, 200000)
        target_os = host_os()
        if any(manifest["os"][key] != target_os[key] for key in ["major", "arch"]):
            raise ValueError("Bundle RHEL major version or architecture does not match this host")
        if manifest.get("authentication") != "unsigned-prototype":
            raise ValueError("Bundle authentication scheme is not supported")
        sid = manifest["snapshot_id"]
        destination = self.root / "snapshots" / sid
        if destination.exists():
            existing = read_json(destination / "snapshot.json")
            if existing.get("source_archive_sha256") == checksum:
                self.update(job, snapshot_id=sid, result={"snapshot_id": sid, "already_imported": True,
                                                        "checked_at": now(), "source_archive_sha256": checksum,
                                                        "authenticated": False})
                return
            raise ValueError("A different snapshot already uses this identifier")
        if shutil.disk_usage(self.root).free < total + self.floor + GIB:
            raise ValueError("Not enough free space for import staging and the storage reserve")
        staging = self.root / ".imports" / job["id"]
        staging.mkdir(mode=0o700)
        try:
            self.update(job, stage="Checking archive members and extracting to private staging")
            verified = verify_archive(path, extract_to=staging, max_bytes=limit, reserve_bytes=self.floor)
            self.update(job, stage="Revalidating repository metadata and trusted RPM signatures")
            checked = {}
            for rid in manifest["repositories"]:
                checked[rid] = validate_repository(staging / "repositories" / rid,
                                                  require_updateinfo=("baseos" in rid or "appstream" in rid))
            verified["verified_at"] = now()
            snapshot = {"id": sid, "created": now(), "verified_at": verified["verified_at"],
                        "source_created": manifest.get("created"), "status": "validated", "os": manifest["os"],
                        "repositories": checked, "package_bytes": sum(r["package_bytes"] for r in checked.values()),
                        "imported": True, "source_archive_sha256": checksum, "authenticated": False}
            write_json(staging / "snapshot.json", snapshot)
            # The snapshot becomes visible only after every validation succeeds.
            os.rename(staging, destination)
            self.update(job, snapshot_id=sid, result={"snapshot_id": sid, "repositories": checked,
                                                    "source_archive_sha256": checksum, **verified})
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @staticmethod
    def save_profile_unlocked_validate(profile):
        known = {r["id"] for r in repositories()}
        if not profile["repositories"] or any(r not in known for r in profile["repositories"]):
            raise ValueError("Save a profile using repositories configured on this host")

    def update(self, job, **values):
        job.update(values, updated=now())
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
            elif job["operation"] == "import":
                self.import_bundle(job)
            elif job["operation"] == "publish":
                self.publish(job)
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
    parser.add_argument("command", choices=["status", "client-automation", "save-profile", "save-import-settings", "save-publication-settings", "browse-directories", "start", "run-job", "logs", "cancel"])
    parser.add_argument("argument", nargs="?")
    parser.add_argument("snapshot", nargs="?")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError("Administrative access is required")
    # The browser can update only the source directory; all other policy remains administrator-owned.
    if CONFIG.is_symlink() or CONFIG.stat().st_uid != 0 or CONFIG.stat().st_mode & 0o022:
        raise ValueError("Configuration must be root-owned and not writable by group or others")
    engine = Engine()
    if args.command == "status":
        result = engine.status()
    elif args.command == "client-automation":
        result = engine.client_automation(args.argument)
    elif args.command in {"save-profile", "save-import-settings", "save-publication-settings", "browse-directories"}:
        raw = sys.stdin.read(16385)
        if len(raw) > 16384:
            raise ValueError("Request is too large")
        data = json.loads(raw)
        if args.command == "save-profile":
            result = engine.save_profile(data)
        elif args.command == "save-import-settings":
            result = engine.save_import_settings(data)
        elif args.command == "save-publication-settings":
            result = engine.save_publication_settings(data)
        else:
            result = engine.browse_directories(data)
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

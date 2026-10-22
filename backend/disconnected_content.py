#!/usr/bin/python3
"""Local, root-owned job engine. Runtime state and entitlement material never enter exports."""
from __future__ import annotations

import argparse
import base64
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
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener, url2pathname
import xml.etree.ElementTree as ET

VERSION = "0.11.0"
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
FINGERPRINT = re.compile(r"^(?:[A-F0-9]{40}|[A-F0-9]{64})$")
KEY_LIMIT = 65536


def public_key_blocks(text):
    """Accept public certificate packets only; never export secret/encrypted packets.

    Packet framing follows RFC 9580 sections 4.2 and 10.1. Fingerprints and
    cryptographic validity are still determined by GnuPG during explicit review.
    """
    if not isinstance(text, str) or len(text.encode()) > 1024 * 1024 or 'PRIVATE KEY' in text:
        raise ValueError("Configured signing-key file is too large or contains private key material")
    blocks = re.findall(r"-----BEGIN PGP PUBLIC KEY BLOCK-----[\s\S]*?-----END PGP PUBLIC KEY BLOCK-----", text)
    if (not 1 <= len(blocks) <= 8 or text.count('-----BEGIN PGP PUBLIC KEY BLOCK-----') != len(blocks)
            or text.count('-----END PGP PUBLIC KEY BLOCK-----') != len(blocks)):
        raise ValueError("Expected one to eight armored public signing keys")
    result = []
    for block in blocks:
        armor = block + "\n"
        if len(armor.encode()) > KEY_LIMIT:
            raise ValueError("Public signing key exceeds 64 KiB")
        lines = block.splitlines()[1:-1]
        while lines and (not lines[0].strip() or ':' in lines[0]):
            lines.pop(0)
        payload = ''.join(line.strip() for line in lines if line.strip() and not line.startswith('='))
        try:
            raw = base64.b64decode(payload, validate=True)
            cursor, primary = 0, 0
            while cursor < len(raw):
                header = raw[cursor]; cursor += 1
                if not header & 128:
                    raise ValueError("Invalid public-key packet")
                if header & 64:
                    tag = header & 63
                    size = raw[cursor]; cursor += 1
                    if 192 <= size <= 223:
                        size = (size - 192) * 256 + raw[cursor] + 192; cursor += 1
                    elif size == 255:
                        size = int.from_bytes(raw[cursor:cursor+4], 'big'); cursor += 4
                    elif size >= 224:
                        raise ValueError("Partial public-key packet lengths are not supported")
                else:
                    tag, kind = (header >> 2) & 15, header & 3
                    if kind == 3:
                        raise ValueError("Unbounded public-key packet")
                    width = 1 << kind
                    if cursor + width > len(raw):
                        raise ValueError("Truncated public-key packet")
                    size = int.from_bytes(raw[cursor:cursor+width], 'big'); cursor += width
                if tag not in {2, 6, 13, 14, 17, 21} or not primary and tag != 6:
                    raise ValueError("Only public certificate packets may enter a transfer bundle")
                primary += tag == 6
                if primary > 1 or size <= 0 or cursor + size > len(raw):
                    raise ValueError("Invalid or multiple public signing keys in one armor block")
                cursor += size
            if primary != 1:
                raise ValueError("Public signing key packet is missing")
        except (IndexError, ValueError) as error:
            raise ValueError("Invalid public certificate; private or unsupported packets cannot be exported") from error
        result.append(armor)
    return result


def configured_signing_key_sources(repo_ids):
    """Use DNF's expanded configuration, without exporting URLs or credentials."""
    import dnf
    with dnf.Base() as base:
        base.read_all_repos()
        return {rid: list(base.repos[rid].gpgkey) for rid in repo_ids if rid in base.repos}


def read_public_key_source(value):
    class KeyRedirect(HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, target):
            parsed = urlsplit(target)
            if parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None:
                raise ValueError("Public-key redirects must use HTTPS without credentials")
            return super().redirect_request(request, fp, code, message, headers, target)
    url = urlsplit(value)
    if url.username is not None or url.password is not None or url.fragment:
        raise ValueError("Public signing-key sources must not contain credentials or fragments")
    if url.scheme == 'file' and not url.netloc and not url.query:
        with open(url2pathname(url.path), 'rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
    elif url.scheme == 'https' and url.hostname:
        with build_opener(HTTPSHandler(), KeyRedirect()).open(Request(value), timeout=30) as stream:
            raw = stream.read(1024 * 1024 + 1)
    else:
        raise ValueError("Configure a file:// or HTTPS public signing-key source")
    if len(raw) > 1024 * 1024:
        raise ValueError("Configured signing-key source exceeds 1 MiB")
    return public_key_blocks(raw.decode('ascii'))


def explain_reposync_failure(log):
    """Turn common reposync signature failures into an actionable message; None if unrecognized."""
    unsigned = re.findall(r"Package (\S+\.rpm) is not signed", log)
    if unsigned:
        return (f"{len(set(unsigned))} packages in this repository are not signed (for example {unsigned[0]}). "
                "Signed packages are required, so it can't be mirrored; choose a repository whose packages are all signed.")
    if re.search(r"Public key for \S+ is not installed|GPG check FAILED|NOKEY", log):
        return ("A package signature couldn't be checked because its signing key isn't available on this host. "
                "Check the repository's gpgkey setting and import its key.")
    return None


class SignatureError(ValueError):
    def __init__(self, code, message, diagnostics):
        super().__init__("RPM signature validation: " + message)
        self.details = {"code": code, "message": message, "diagnostics": diagnostics[:20000]}


def inspect_public_key(armor):
    """Inspect one bounded public certificate in an isolated, offline GnuPG home."""
    if (not isinstance(armor, str) or len(armor.encode()) > KEY_LIMIT
            or not armor.strip().startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----")
            or not armor.strip().endswith("-----END PGP PUBLIC KEY BLOCK-----")
            or armor.count("-----BEGIN PGP PUBLIC KEY BLOCK-----") != 1
            or "PRIVATE KEY" in armor or "{{" in armor or "{%" in armor):
        raise ValueError("Select one ASCII-armored public signing key, up to 64 KiB; private keys are not accepted")
    with tempfile.TemporaryDirectory(prefix="dc-key-review-") as temporary:
        path = Path(temporary) / "key.asc"
        path.write_text(armor)
        inspected = subprocess.run(["/usr/bin/gpg2", "--no-options", "--homedir", temporary,
            "--batch", "--no-auto-key-retrieve", "--with-colons", "--fixed-list-mode",
            "--with-fingerprint", "--show-keys", str(path)], capture_output=True, text=True, timeout=15,
            env={**os.environ, "LC_ALL": "C"})
    rows = [line.split(":") for line in inspected.stdout.splitlines()]
    primary = [row for row in rows if row[0] == "pub"]
    fingerprints = [row[9] for row in rows if row[0] == "fpr" and len(row) > 9]
    if inspected.returncode or len(primary) != 1 or not fingerprints or not all(FINGERPRINT.fullmatch(f) for f in fingerprints):
        raise ValueError("Cannot inspect a single valid public signing key")
    key = primary[0]
    expires = int(key[6]) if key[6] else None
    if key[1] in {"r", "e", "d", "i"} or expires and expires <= time.time() or "s" not in key[11].lower():
        raise ValueError("Signing key is expired, revoked, disabled, invalid, or lacks signing capability")
    return {"fingerprint": fingerprints[0], "fingerprints": fingerprints,
            "user_ids": [row[9] for row in rows if row[0] == "uid" and len(row) > 9],
            "expires": dt.datetime.fromtimestamp(expires, dt.timezone.utc).isoformat() if expires else None,
            "sha256": hashlib.sha256(armor.encode()).hexdigest(), "public_key": armor}


def host_rpm_fingerprints():
    """Full primary fingerprints in the host RPM keyring; empty if they cannot be read.

    Used only to avoid prompting for keys the host already trusts. It never grants trust.
    Each key is inspected separately: GnuPG stops reading a file at a key format it
    does not support (for example, version 6 keys), which would hide the others.
    """
    env = {**os.environ, "LC_ALL": "C"}
    try:
        listed = subprocess.run(["/usr/bin/rpm", "-q", "gpg-pubkey", "--qf", "%{DESCRIPTION}\n"],
                                capture_output=True, text=True, timeout=30, env=env)
        if listed.returncode or "PRIVATE KEY" in listed.stdout:
            return set()
        blocks = re.findall(r"-----BEGIN PGP PUBLIC KEY BLOCK-----[\s\S]*?-----END PGP PUBLIC KEY BLOCK-----", listed.stdout)[:64]
        result = set()
        with tempfile.TemporaryDirectory(prefix="dc-host-keys-") as temporary:
            for index, block in enumerate(blocks):
                path = Path(temporary) / f"{index}.asc"
                path.write_text(block + "\n")
                shown = subprocess.run(["/usr/bin/gpg2", "--no-options", "--homedir", temporary, "--batch",
                    "--no-auto-key-retrieve", "--with-colons", "--fixed-list-mode", "--with-fingerprint",
                    "--show-keys", str(path)], capture_output=True, text=True, timeout=30, env=env)
                previous = ""
                for row in (line.split(":") for line in shown.stdout.splitlines()):
                    if row[0] == "fpr" and previous == "pub" and len(row) > 9 and FINGERPRINT.fullmatch(row[9]):
                        result.add(row[9])
                    previous = row[0]
        return result
    except (OSError, subprocess.SubprocessError):
        return set()


def repository_keys(repository):
    keys = repository.get("signing_keys", [])
    if not isinstance(keys, list) or len(keys) > 8:
        raise ValueError("Invalid repository signing keys")
    for key in keys:
        if (not isinstance(key, dict) or not FINGERPRINT.fullmatch(key.get("fingerprint", ""))
                or not isinstance(key.get("public_key"), str) or len(key["public_key"].encode()) > KEY_LIMIT
                or hashlib.sha256(key["public_key"].encode()).hexdigest() != key.get("sha256")
                or "{{" in key["public_key"] or "{%" in key["public_key"]):
            raise ValueError("Repository signing key differs from its reviewed content")
    return keys


def client_key_path(key):
    return "/etc/pki/rpm-gpg/DISCONNECTED-CONTENT-" + key["fingerprint"] + ".asc"


def publication_keys(repositories):
    keys = {}
    for repo in repositories.values():
        for key in repository_keys(repo):
            if key["fingerprint"] in keys and keys[key["fingerprint"]]["sha256"] != key["sha256"]:
                raise ValueError("Conflicting public key content in this publication")
            keys[key["fingerprint"]] = key
    return list(keys.values())


@contextlib.contextmanager
def rpm_key_database(keys):
    if not keys:
        yield []
        return
    with tempfile.TemporaryDirectory(prefix="dc-rpm-keys-") as temporary:
        database = Path(temporary) / "rpmdb"
        database.mkdir()
        args = ["--dbpath", str(database)]
        for key in keys:
            reviewed = inspect_public_key(key["public_key"])
            if reviewed["fingerprint"] != key["fingerprint"] or reviewed["sha256"] != key["sha256"]:
                raise ValueError("Pinned public signing key changed")
            path = Path(temporary) / (key["fingerprint"] + ".asc")
            path.write_text(key["public_key"])
            result = subprocess.run(["/usr/bin/rpmkeys", *args, "--import", str(path)],
                                    capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise ValueError("RPM rejected the reviewed public key: " + result.stderr[:1000])
        yield args


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


def validate_repository(root, require_updateinfo=False, check_signatures=True, include_files=False, signing_keys=None):
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
        with rpm_key_database(signing_keys or []) as key_args:
            for offset in range(0, len(package_paths), 100):
                checked = subprocess.run(["/usr/bin/rpmkeys", *key_args, "--checksig", *package_paths[offset:offset+100]],
                                         capture_output=True, text=True, check=False,
                                         env={**os.environ, "LC_ALL": "C"})
                lines = checked.stdout.splitlines()
                if checked.returncode or len(lines) != len(package_paths[offset:offset+100]) or any(
                        line.rsplit(": ", 1)[-1] not in {"digests signatures OK", "signatures OK"} for line in lines):
                    diagnostic = subprocess.run(["/usr/bin/rpmkeys", *key_args, "--checksig", "--verbose",
                        *package_paths[offset:offset+100]], capture_output=True, text=True,
                        env={**os.environ, "LC_ALL": "C"})
                    details = (diagnostic.stdout + "\n" + diagnostic.stderr).strip()
                    if re.search(r"\bBAD\b|\bNOT OK\b", details):
                        code, message = "invalid_signature", "Package signature or digest verification failed; replace the affected package"
                    elif "NOKEY" in details:
                        code, message = "missing_key", "Missing signing key; review the package key ID and approve its public key before retrying"
                    elif "NOTTRUSTED" in details:
                        code, message = "untrusted_key", "Signature rejected by RPM trust or system crypto policy; review the diagnostic"
                    elif not checked.returncode and any(line.endswith(": digests OK") for line in lines):
                        code, message = "unsigned_package", "Unsigned RPM package; signed packages are required"
                    else:
                        code, message = "verification_failed", "Signature verification failed; review the RPM diagnostic"
                    raise SignatureError(code, message, details)
    result = {"packages": count, "package_bytes": total, "metadata_types": sorted(metadata_types),
              "rpm_signatures_verified": check_signatures}
    if include_files:
        result["files"] = sorted(files)
    if signing_keys:
        result["signing_keys"] = signing_keys
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
        keys = repository_keys(snapshot["repositories"][rid])
        key_urls = ["file://" + client_key_path(k) for k in keys]
        lines.extend([f"[dc-{rid}]", f"name=Disconnected Content - {rid}",
                      f"baseurl={origin}/disconnected-content/snapshots/{sid}/repositories/{quote(rid, safe='')}/",
                      "enabled=1", "gpgcheck=1", "repo_gpgcheck=0", "sslverify=1",
                      "gpgkey=" + " ".join(key_urls or ["file:///etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release"]),
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
    keys = publication_keys(repos)
    common = [{"name": "Require the publication's RHEL platform and Ansible Core 2.16 or newer",
               "ansible.builtin.assert": {"that": [
                   "ansible_version.full is version('2.16', '>=')",
                   "ansible_facts['distribution'] == 'RedHat'",
                   f"ansible_facts['distribution_major_version'] == '{platform['major']}'",
                   f"ansible_facts['architecture'] == '{platform['arch']}'"],
                   "fail_msg": "Use compatible RHEL clients and Ansible Core 2.16 or newer."}}]
    if any(not repository_keys(repo) for repo in repos.values()):
        common += [{"name": "Check for the independently provisioned Red Hat RPM public keys",
               "ansible.builtin.stat": {"path": "/etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release"},
               "register": "dc_key"},
              {"name": "Require trusted RPM keys before using the repository",
               "ansible.builtin.assert": {"that": ["dc_key.stat.exists", "dc_key.stat.isreg | default(false)"],
                   "fail_msg": "Provision and verify Red Hat RPM keys independently before continuing."}}]
    if keys:
        common += [{"name": "Check gnupg2 for full fingerprint verification",
                    "ansible.builtin.stat": {"path": "/usr/bin/gpg2"}, "register": "dc_gpg"},
                   {"name": "Require gnupg2 before configuring reviewed repository keys",
                    "ansible.builtin.assert": {"that": ["dc_gpg.stat.exists", "dc_gpg.stat.executable | default(false)"],
                        "fail_msg": "Provision gnupg2 from approved installation media before enrolling signing keys."}}]
    key_setup, key_checks = [], []
    for key in keys:
        path = client_key_path(key)
        fingerprint_line = "fpr:::::::::" + key["fingerprint"] + ":"
        gpg_args = ["/usr/bin/gpg2", "--no-options", "--homedir", "/var/lib/disconnected-content-key-review",
                    "--batch", "--no-auto-key-retrieve",
                    "--with-colons", "--fixed-list-mode", "--with-fingerprint", "--show-keys"]
        key_setup.extend([
            {"name": "Install reviewed public key " + key["fingerprint"],
             "ansible.builtin.copy": {"dest": path, "content": key["public_key"],
                 "owner": "root", "group": "root", "mode": "0644"}},
            {"name": "Inspect the supplied public key without retrieving keys",
             "ansible.builtin.command": {"argv": gpg_args + [path]},
             "register": "dc_supplied_key", "changed_when": False, "when": "not ansible_check_mode"},
            {"name": "Verify the publication's full signing-key fingerprint before enrollment",
             "ansible.builtin.assert": {"that": [
                 "dc_supplied_key.stdout_lines | select('match', '^pub:') | list | length == 1",
                 "'" + fingerprint_line + "' in dc_supplied_key.stdout_lines"],
                 "fail_msg": "Public key fingerprint does not match this publication; no key was enrolled."},
             "when": "not ansible_check_mode"},
            {"name": "Look up only the matching RPM key ID",
             "ansible.builtin.command": {"argv": ["/usr/bin/rpm", "-q", "gpg-pubkey-" + key["fingerprint"][-8:].lower(),
                                                  "--qf", "%{DESCRIPTION}\n"]},
             "register": "dc_installed_key", "changed_when": False,
             "failed_when": "dc_installed_key.rc not in [0, 1]", "when": "not ansible_check_mode"},
            {"name": "Inspect the matching installed key's full fingerprint",
             "ansible.builtin.command": {"argv": gpg_args + ["-"], "stdin": "{{ dc_installed_key.stdout }}"},
             "register": "dc_installed_fingerprint", "changed_when": False,
             "when": "not ansible_check_mode and dc_installed_key.rc == 0"},
            {"name": "Enroll the fingerprint-verified public key if it is not already trusted",
             "ansible.builtin.command": {"argv": ["/usr/bin/rpmkeys", "--import", path]},
             "when": "not ansible_check_mode and (dc_installed_key.rc == 1 or '" + fingerprint_line +
                     "' not in (dc_installed_fingerprint.stdout_lines | default([])))"}])
        key_checks.extend([
            {"name": "Check pinned public key " + key["fingerprint"],
             "ansible.builtin.stat": {"path": path, "checksum_algorithm": "sha256", "follow": False},
             "register": "dc_public_key"},
            {"name": "Require the exact approved public key before updating",
             "ansible.builtin.assert": {"that": ["dc_public_key.stat.isreg | default(false)",
                 "not (dc_public_key.stat.islnk | default(false))",
                 "dc_public_key.stat.checksum | default('') == '" + key["sha256"] + "'"],
                 "fail_msg": "Public key is missing or changed. Run this publication's configure-repos.yml first."}}])
    configure = {"name": "Configure repositories for snapshot " + sid,
                 "hosts": "disconnected_clients", "become": True, "gather_facts": True,
                 "serial": 1, "any_errors_fatal": True, "pre_tasks": common + ([
                     {"name": "Prepare a private GnuPG home for public-key inspection",
                      "ansible.builtin.file": {"path": "/var/lib/disconnected-content-key-review", "state": "directory",
                          "owner": "root", "group": "root", "mode": "0700"}}] if keys else []),
                 "tasks": key_setup + [
                     {"name": "Install the exact pinned repository file (back up an existing file)",
                      "ansible.builtin.copy": {"dest": "/etc/yum.repos.d/disconnected-content.repo",
                          "content": config, "owner": "root", "group": "root", "mode": "0644", "backup": True}},
                     {"name": "Refresh metadata using only this publication's repositories",
                      "ansible.builtin.command": {"argv": ["dnf", "--disablerepo=*", *["--enablerepo=" + rid for rid in ids],
                          "makecache", "--refresh"]}, "changed_when": False, "when": "not ansible_check_mode"},
                     {"name": "Explain repository check mode",
                      "ansible.builtin.debug": {"msg": "Check mode previews repository and public-key files. Key enrollment and metadata refresh are skipped. Apply this playbook before previewing security updates."},
                      "when": "ansible_check_mode"}]}
    security = {"name": "Apply security updates from snapshot " + sid,
                "hosts": "disconnected_clients", "become": True, "gather_facts": True,
                "serial": 1, "any_errors_fatal": True,
                "pre_tasks": common + key_checks + [
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
trusted RPM keys, and network access to the published repository URLs.
Reviewed-key enrollment also requires gnupg2 on clients; provision it from
approved installation media before configuring repositories.
For repositories with reviewed keys, configure-repos.yml embeds the exact public
key material and verifies its pinned full fingerprint before RPM enrollment. Review this trust
change before running it: RPM key enrollment applies to the client-wide keyring.
Repositories without reviewed keys use the independently provisioned Red Hat keys.
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
Configuration check mode does not enroll keys, refresh metadata, or install files.
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
    instructions += "\nReviewed public signing keys:\n" + "".join(
        key["fingerprint"] + "  SHA256 " + key["sha256"] + "\n" for key in keys)
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
    keys = manifest.get("signing_keys", {})
    if not isinstance(keys, dict) or any(rid not in repos for rid in keys):
        raise ValueError("Invalid bundled signing-key repositories")
    key_paths = set()
    for rid, entries in keys.items():
        if not isinstance(entries, list) or not 1 <= len(entries) <= 8:
            raise ValueError("A bundle repository may supply one to eight public keys")
        for entry in entries:
            if (not isinstance(entry, dict) or set(entry) != {"path", "sha256"}
                    or not isinstance(entry["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])
                    or entry["path"] != f"repositories/{rid}/signing-keys/{entry['sha256']}.asc"
                    or entry["path"] in key_paths or entry["path"] not in expected
                    or expected[entry["path"]]["sha256"] != entry["sha256"]
                    or not 0 < expected[entry["path"]]["size"] <= KEY_LIMIT):
                raise ValueError("Invalid bundled public signing-key entry")
            key_paths.add(entry["path"])
    for name in expected:
        parts = PurePosixPath(name).parts
        if len(parts) < 3 or len(parts) > 32 or parts[0] != "repositories" or parts[1] not in repos:
            raise ValueError("Bundle content must belong to a declared repository")
    if not isinstance(manifest.get("os"), dict) or not all(isinstance(manifest["os"].get(k), str) for k in ["major", "arch"]):
        raise ValueError("Bundle OS version and architecture are required")
    return manifest, total


def verify_archive(path, extract_to=None, max_bytes=2000 * GIB, max_files=200000, reserve_bytes=0, key_contents=None):
    """Verify a bounded stream; optionally write regular files to private staging.

    Never use extractall: archive ownership, mode, links, and paths are untrusted.
    """
    with tarfile.open(path, "r|") as archive:
        manifest, _ = bundle_manifest(archive, max_bytes, max_files)
        expected = manifest["files"]
        key_paths = {key["path"] for entries in manifest.get("signing_keys", {}).values() for key in entries}
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
            key_bytes = bytearray() if key_contents is not None and member.name in key_paths else None
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
                    if key_bytes is not None:
                        key_bytes.extend(block)
                    if output is not None:
                        output.write(block)
            if h.hexdigest() != entry["sha256"]:
                raise ValueError("Archive member checksum mismatch")
            if key_bytes is not None:
                key_contents[member.name] = bytes(key_bytes).decode('ascii')
            seen.add(member.name)
        if seen != set(expected):
            raise ValueError("Archive is missing files")
    return {"files_verified": len(seen), "snapshot_id": manifest["snapshot_id"], "verified_at": now(),
            "authenticated": False, "bundled_signing_keys": sum(len(entries) for entries in manifest.get("signing_keys", {}).values()),
            "note": "Unsigned prototype bundle; hashes verify integrity only. Bundled public keys require explicit review and approval."}


class Engine:
    def __init__(self, config=None):
        self.config = config if config is not None else read_json(CONFIG)
        self.root = Path(self.config["state_dir"])
        if not self.root.is_absolute():
            raise ValueError("state_dir must be absolute")
        self.root = self.root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.floor = self.config.get("reserve_gib", 5) * GIB
        for sub in ("jobs", "snapshots", "exports", "export-records", "import-records", "incoming", ".imports", "key-reviews", "trusted-keys", "bundle-key-candidates"):
            (self.root / sub).mkdir(exist_ok=True, mode=0o700)

    def scan_bundle_keys(self, data):
        if self.config.get("role") != "disconnected":
            raise ValueError("Bundle key review requires the disconnected role")
        if not isinstance(data, dict) or set(data) != {"bundle_name"}:
            raise ValueError("Select one incoming bundle to inspect")
        with self.lock():
            if any(read_json(p)["status"] in {"queued", "running"} for p in (self.root / "jobs").glob("*.json")):
                raise ValueError("Wait for the active job before inspecting bundle keys")
            path = self.bundle_path(data["bundle_name"])
            fingerprint = self.fingerprint(path)
            limit = int(self.config.get("max_import_gib", 200)) * GIB
            if path.stat().st_size > limit + GIB:
                raise ValueError("Transfer archive exceeds the import size limit")
            checksum = digest(path)
            if checksum != self.expected_archive_checksum(path):
                raise ValueError("Transfer checksum mismatch; bundled keys were not staged")
            contents = {}
            verify_archive(path, max_bytes=limit, key_contents=contents)
            with tarfile.open(path, 'r|') as archive:
                manifest, _ = bundle_manifest(archive, limit, 200000)
            if self.fingerprint(path) != fingerprint or digest(path) != checksum:
                raise ValueError("Source archive changed while inspecting bundled signing keys")
            candidates = []
            for rid, entries in manifest.get('signing_keys', {}).items():
                for entry in entries:
                    armor = contents[entry['path']]
                    if public_key_blocks(armor) != [armor]:
                        raise ValueError("Bundle key must contain exactly one public certificate")
                    cid = hashlib.sha256((checksum + rid + entry['sha256']).encode()).hexdigest()[:32]
                    candidates.append({"candidate_id": cid, "repository_id": rid, "public_key": armor,
                                       "sha256": entry['sha256'], "bundle_name": path.name, "bundle_sha256": checksum,
                                       "snapshot_id": manifest['snapshot_id'], "scanned_at": now(), "trusted": False})
            # Candidates are written only after the entire archive and every key pass integrity checks.
            for candidate in candidates:
                write_json(self.root / 'bundle-key-candidates' / (candidate['candidate_id'] + '.json'), candidate)
        host = host_rpm_fingerprints() if candidates else set()
        approved = {(rid, key["sha256"]) for rid in {c["repository_id"] for c in candidates} for key in self.trusted_keys(rid)}
        summaries = []
        for candidate in candidates:
            summary = {"candidate_id": candidate["candidate_id"], "repository_id": candidate["repository_id"],
                       "sha256": candidate["sha256"], "approved": (candidate["repository_id"], candidate["sha256"]) in approved}
            try:
                key = inspect_public_key(candidate["public_key"])
                summary.update(fingerprint=key["fingerprint"], user_ids=key["user_ids"], expires=key["expires"],
                               host_trusted=key["fingerprint"] in host)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                summary.update(fingerprint=None, host_trusted=False, error=str(error))
            summaries.append(summary)
        return {"bundle_name": path.name, "bundle_sha256": checksum, "keys": len(candidates),
                "candidates": summaries, "trusted": False}

    def bundle_info(self, data):
        """Summarize the unverified manifest for display; imports verify everything again."""
        if self.config.get("role") != "disconnected":
            raise ValueError("Bundle details require the disconnected role")
        if not isinstance(data, dict) or set(data) != {"bundle_name"}:
            raise ValueError("Select one incoming bundle")
        path = self.bundle_path(data["bundle_name"])
        limit = int(self.config.get("max_import_gib", 200)) * GIB
        with tarfile.open(path, "r|") as archive:
            manifest, total = bundle_manifest(archive, limit, 200000)
        count = lambda value: value if type(value) is int and value >= 0 else None
        target = host_os()
        return {"bundle_name": path.name, "bytes": path.stat().st_size, "content_bytes": total,
                "snapshot_id": manifest["snapshot_id"],
                "created": manifest["created"] if isinstance(manifest.get("created"), str) else None,
                "os": {"major": manifest["os"]["major"], "arch": manifest["os"]["arch"]},
                "matches_host": all(manifest["os"][k] == target[k] for k in ["major", "arch"]),
                "repositories": {rid: {"packages": count(repo.get("packages")) if isinstance(repo, dict) else None,
                                       "package_bytes": count(repo.get("package_bytes")) if isinstance(repo, dict) else None}
                                 for rid, repo in manifest["repositories"].items()},
                "signing_keys": {rid: len(entries) for rid, entries in manifest.get("signing_keys", {}).items()},
                "verified": False}

    def key_files(self):
        try:
            directory = self.incoming_directory()
            return sorted(p.name for p in directory.iterdir() if not p.is_symlink() and p.is_file()
                          and p.suffix.lower() in {".asc", ".key", ".pub"} and p.stat().st_size <= KEY_LIMIT)[:100]
        except OSError:
            return []
        except ValueError:
            return []

    def trusted_keys(self, rid):
        path = safe_path(self.root / "trusted-keys", rid + ".json")
        return repository_keys(read_json(path)) if path.exists() else []

    def review_signing_key(self, data):
        if self.config.get("role") != "disconnected":
            raise ValueError("Signing key approval requires the disconnected role")
        if isinstance(data, dict) and set(data) == {"bundle_key"}:
            if not isinstance(data['bundle_key'], str) or not ID.fullmatch(data['bundle_key']):
                raise ValueError("Select a bundled key candidate")
            candidate = read_json(safe_path(self.root / 'bundle-key-candidates', data['bundle_key'] + '.json'))
            data = {'repository_id': candidate['repository_id'], 'public_key': candidate['public_key']}
            source = 'Bundle ' + candidate['bundle_name'] + ' SHA256 ' + candidate['bundle_sha256']
        else:
            source = 'Browser public-key selection'
        if (not isinstance(data, dict) or set(data) not in ({"repository_id", "public_key"}, {"repository_id", "key_file"})
                or not isinstance(data.get("repository_id"), str) or not REPO_ID.fullmatch(data["repository_id"])):
            raise ValueError("Specify a repository ID and one public key")
        if "key_file" in data:
            if data["key_file"] not in self.key_files():
                raise ValueError("Select an available public-key file in the saved transfer folder")
            path = safe_path(self.incoming_directory(), data["key_file"])
            with path.open("rb") as stream:
                armor = stream.read(KEY_LIMIT + 1).decode("utf-8")
            source = data["key_file"]
        else:
            armor = data["public_key"]
        key = inspect_public_key(armor)
        review = {**key, "review_id": uuid.uuid4().hex, "repository_id": data["repository_id"],
                  "reviewed_at": now(), "source": source}
        write_json(self.root / "key-reviews" / (review["review_id"] + ".json"), review)
        return review

    def trust_signing_key(self, data):
        if self.config.get("role") != "disconnected":
            raise ValueError("Signing key approval requires the disconnected role")
        if (not isinstance(data, dict) or set(data) != {"review_id", "fingerprint", "confirmed"}
                or not isinstance(data["review_id"], str) or not ID.fullmatch(data["review_id"])
                or data["confirmed"] is not True):
            raise ValueError("Review the public key and explicitly confirm its full fingerprint")
        with self.lock():
            if any(read_json(p)["status"] in {"queued", "running"} for p in (self.root / "jobs").glob("*.json")):
                raise ValueError("Wait for the active job before approving signing keys")
            path = safe_path(self.root / "key-reviews", data["review_id"] + ".json")
            review = read_json(path)
            if review.get("approved_at") or (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(review["reviewed_at"])).total_seconds() > 900:
                raise ValueError("Key review was already approved or expired; review the key again")
            key = inspect_public_key(review["public_key"])
            if key["fingerprint"] != data["fingerprint"] or key["sha256"] != review["sha256"]:
                raise ValueError("Confirmed fingerprint does not match the reviewed public key")
            # Check RPM compatibility without enrolling the key in the host-wide keyring.
            with rpm_key_database([key]):
                pass
            approved = {**key, "approved_at": now(), "source": review["source"], "review_id": review["review_id"]}
            rid = review["repository_id"]
            keys = self.trusted_keys(rid)
            if any(k["fingerprint"] == key["fingerprint"] and k["sha256"] != key["sha256"] for k in keys):
                raise ValueError("This fingerprint already has different approved key material; administrator review is required")
            keys = [k for k in keys if k["fingerprint"] != key["fingerprint"]] + [approved]
            if len(keys) > 8:
                raise ValueError("A repository may have at most eight approved signing keys")
            write_json(self.root / "trusted-keys" / (rid + ".json"), {"repository_id": rid, "signing_keys": keys})
            write_json(path, {**review, "approved_at": approved["approved_at"]})
        return {"repository_id": rid, "key": approved}

    def validate_content(self, path, rid, keys=None, **kwargs):
        try:
            return validate_repository(path, require_updateinfo=("baseos" in rid or "appstream" in rid),
                                       signing_keys=self.trusted_keys(rid) if keys is None else keys, **kwargs)
        except SignatureError as error:
            error.details["repository_id"] = rid
            raise

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
        current = read_json(path) if path.exists() else None
        return {"installed": installed, "server_active": active,
                "base_url": self.config.get("public_base_url", ""),
                "current": current, "availability": self.publication_availability(current)}

    def publication_availability(self, current):
        """Cheap local availability checks; not a new package verification or HTTP probe."""
        if not current:
            return {"state": "not_published", "reason": "No publication has been recorded"}
        try:
            sid = current["snapshot_id"]
            if not ID.fullmatch(sid) or not current.get("repositories"):
                raise ValueError("Publication record is incomplete")
            public = PUBLISH_ROOT / "public"
            if PUBLISH_ROOT.is_symlink() or public.is_symlink():
                raise ValueError("Public content directory must not be a symlink")
            config = safe_path(public, "client.repo")
            if not config.is_file() or config.stat().st_size > 1024 * 1024 or config.read_text() != current["client_config"]:
                raise ValueError("Public client configuration is missing or differs from the publication record")
            for rid in current["repositories"]:
                if not REPO_ID.fullmatch(rid):
                    raise ValueError("Publication record has an invalid repository ID")
                index = safe_path(public, f"snapshots/{sid}/repositories/{rid}/repodata/repomd.xml")
                if not index.is_file():
                    raise ValueError("A public repository index is missing")
            for key in publication_keys(current["repositories"]):
                path = safe_path(public, f"snapshots/{sid}/keys/{key['fingerprint']}.asc")
                if not path.is_file() or path.stat().st_size > KEY_LIMIT or digest(path) != key["sha256"]:
                    raise ValueError("A published signing key is missing or changed")
            return {"state": "available", "reason": "Public repository indexes and client configuration are present"}
        except (OSError, ValueError, KeyError, UnicodeError) as error:
            return {"state": "unavailable", "reason": str(error)}

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
            self.update(job, step="validate", stage="Revalidating source repositories and trusted RPM signatures")
            inventory = {}
            for rid in snapshot["repositories"]:
                repository = safe_path(source, f"repositories/{rid}")
                checked = self.validate_content(repository, rid, keys=repository_keys(snapshot["repositories"][rid]),
                                                include_files=True)
                inventory[rid] = checked["files"]
            total = sum(safe_path(source, f"repositories/{rid}/{name}").stat().st_size
                        for rid, names in inventory.items() for name in names)
            if shutil.disk_usage(PUBLISH_ROOT).free < total + self.floor:
                raise ValueError("Not enough free space for a public copy and the storage reserve")
            self.update(job, step="copy", stage="Copying indexed repository content to private publication staging")
            for rid, names in inventory.items():
                for name in names:
                    original = safe_path(source, f"repositories/{rid}/{name}")
                    if shutil.disk_usage(PUBLISH_ROOT).free < original.stat().st_size + self.floor:
                        raise ValueError("Storage reserve reached while publishing")
                    target = safe_path(staging, f"repositories/{rid}/{name}")
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    shutil.copyfile(original, target)
                    os.chmod(target, 0o644)
            self.update(job, step="verify", stage="Verifying the public copy before exposing repository URLs")
            checked = {}
            for rid in inventory:
                checked[rid] = self.validate_content(staging / "repositories" / rid, rid,
                                                    keys=repository_keys(snapshot["repositories"][rid]))
            for key in publication_keys(checked):
                path = safe_path(staging, f"keys/{key['fingerprint']}.asc")
                path.parent.mkdir(exist_ok=True, mode=0o700)
                path.write_text(key["public_key"])
                os.chmod(path, 0o644)
            if destination.exists():
                for rid in inventory:
                    self.validate_content(destination / "repositories" / rid, rid,
                                          keys=repository_keys(snapshot["repositories"][rid]))
                    if digest(destination / "repositories" / rid / "repodata/repomd.xml") != digest(
                            staging / "repositories" / rid / "repodata/repomd.xml"):
                        raise ValueError("Different public content already uses this snapshot identifier")
                for key in publication_keys(checked):
                    path = safe_path(destination, f"keys/{key['fingerprint']}.asc")
                    if not path.is_file() or path.stat().st_size > KEY_LIMIT or digest(path) != key["sha256"]:
                        raise ValueError("Different or missing public signing key for this snapshot identifier")
            verified_at = now()
            self.update(job, step="serve")
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

    def export_directory(self):
        """Where new bundles are written: the saved export folder, or private state by default."""
        value = self.config.get("export_dir")
        return self.root / "exports" if not value else self.incoming_directory(value)

    def export_archive_path(self, record):
        """Recorded archive path; records from before the export folder setting used private state."""
        sid = record["snapshot_id"]
        value = record.get("path")
        if isinstance(value, str) and Path(value).is_absolute() and Path(value).name == f"{sid}.tar" and ".." not in Path(value).parts:
            return Path(value)
        return self.root / "exports" / f"{sid}.tar"

    def save_export_settings(self, data):
        if self.config.get("role") != "connected":
            raise ValueError("Export settings require the connected installation role")
        if not isinstance(data, dict) or set(data) != {"export_dir"} or not isinstance(data["export_dir"], str):
            raise ValueError("Specify only the export folder")
        default = data["export_dir"] in {"", str(self.root / "exports")}
        path = self.root / "exports" if default else self.incoming_directory(data["export_dir"])
        if not default:
            if path == self.root or self.root in path.parents or path in self.root.parents:
                raise ValueError("Choose a folder outside the private state directory, or reset to the default")
            # Confirm root can create files there without leaving anything behind.
            fd, name = tempfile.mkstemp(prefix=".dc-write-test-", dir=path)
            os.close(fd)
            os.unlink(name)
        with self.lock():
            if any(read_json(p)["status"] in {"queued", "running"} for p in (self.root / "jobs").glob("*.json")):
                raise ValueError("Wait for the active job to finish before changing the export folder")
            config = read_json(CONFIG)
            if default:
                config.pop("export_dir", None)
            else:
                config["export_dir"] = str(path)
            config["export_updated_at"] = now()
            write_json(CONFIG, config)
            self.config = config
        return {"path": str(path), "default": default, "updated_at": config["export_updated_at"]}

    def export_settings(self):
        default = not self.config.get("export_dir")
        result = {"path": self.config.get("export_dir") or str(self.root / "exports"), "default": default,
                  "updated_at": self.config.get("export_updated_at"), "available": False}
        try:
            path = self.export_directory()
            result.update(path=str(path), available=True, free=shutil.disk_usage(path).free)
        except (OSError, ValueError) as error:
            result["error"] = str(error)
        return result

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

        if self.config.get("role") == "connected":
            add(self.config.get("export_dir", str(self.root / "exports")), "Saved export folder")
        else:
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
        if self.config.get("role") not in {"connected", "disconnected"}:
            raise ValueError("Directory browsing requires a configured installation role")
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

    @staticmethod
    def fingerprint(path):
        info = path.stat()
        return {"size": info.st_size, "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
                "inode": info.st_ino, "device": info.st_dev}

    def export_records(self, jobs):
        """Retain artifact evidence independently of the recent jobs returned to the UI.

        Old jobs record verification at creation but cannot establish whether the
        file has changed since then. Migration never silently certifies old files.
        """
        records = {path.stem: read_json(path) for path in (self.root / "export-records").glob("*.json")}
        for job in jobs:  # jobs() is newest first
            sid, result = job.get("snapshot_id"), job.get("result", {})
            if (job.get("operation") != "export" or job.get("status") != "succeeded"
                    or not isinstance(sid, str) or not ID.fullmatch(sid) or sid in records
                    or not re.fullmatch(r"[a-f0-9]{64}", result.get("sha256", ""))
                    or not isinstance(result.get("path"), str)
                    or self.export_archive_path({"snapshot_id": sid, "path": result["path"]}) != Path(result["path"])):
                continue
            record = {"snapshot_id": sid, "exported_at": job.get("finished", job["created"]),
                      "verified_at": result.get("verified_at", job.get("finished")),
                      "job_id": job["id"], "sha256": result["sha256"], "bytes": result["bytes"],
                      "files_verified": result.get("files_verified"), "fingerprint": None, "path": result["path"]}
            path = self.root / "export-records" / f"{sid}.json"
            with self.lock():
                if path.exists():
                    record = read_json(path)
                else:
                    write_json(path, record)
            records[sid] = record
        return records

    def export_summary(self, record):
        sid = record["snapshot_id"]
        if not ID.fullmatch(sid):
            raise ValueError("Invalid export snapshot identifier")
        path = self.export_archive_path(record)
        sidecar = path.with_suffix(".tar.sha256")
        summary = {**record, "path": str(path), "checksum_path": str(sidecar),
                   "state": "missing", "reason": "Archive or checksum file is missing"}
        if path.is_symlink() or sidecar.is_symlink():
            summary.update(state="changed", reason="Transfer files must be regular files, without symlinks")
        elif path.is_file() and sidecar.is_file():
            expected = f"{record['sha256']}  {path.name}\n"
            try:
                matches = sidecar.stat().st_size <= 4096 and sidecar.read_text() == expected
            except (OSError, UnicodeError):
                matches = False
            if not matches or path.stat().st_size != record["bytes"]:
                summary.update(state="changed", reason="Transfer file size or checksum sidecar differs from the export record")
            elif record.get("fingerprint") is None:
                summary.update(state="unverified", reason="Historical export found; re-verify the existing files before transfer")
            elif self.fingerprint(path) != record["fingerprint"]:
                summary.update(state="changed", reason="Archive changed since verification; verify again or rebuild it")
            else:
                summary.update(state="available", reason="Archive and checksum are present; verified at the recorded time")
        return summary

    def connected_status(self, snapshots, jobs):
        profile = self.profile()
        selection = set(profile["repositories"])
        records = self.export_records(jobs)
        exports = {sid: self.export_summary(record) for sid, record in records.items()}
        snapshots = sorted((s for s in snapshots if s["status"] == "validated" and not s.get("imported")),
                           key=lambda s: s["created"], reverse=True)
        for snapshot in snapshots:
            snapshot["export"] = exports.get(snapshot["id"])
        latest = snapshots[0] if snapshots else None
        matching = next((s for s in snapshots if set(s["repositories"]) == selection), None)
        latest_export = max((e for e in exports.values() if e.get("exported_at")),
                            key=lambda e: e["exported_at"], default=None)
        active = next((j for j in jobs if j["status"] in {"queued", "running"}), None)
        last_preflight = next((j for j in jobs if j.get("operation") == "preflight"
                               and j.get("profile") == profile), None)
        relevant = next((j for j in jobs if (
            j.get("operation") in {"sync", "sync-export"} and set(j.get("profile", {}).get("repositories", [])) == selection
            or j is last_preflight and j["status"] in {"failed", "cancelled", "interrupted"}
            or matching and j.get("operation") in {"export", "verify-export"} and j.get("snapshot_id") == matching["id"])), None)
        plan_job = next((j for j in jobs if j.get("plan") and j.get("profile") == profile), None)
        phase, action, reason = "select", None, "Choose the repositories to mirror and save your selection."
        bundle = matching.get("export") if matching else None
        if not selection:
            pass
        elif relevant and relevant["status"] in {"failed", "cancelled", "interrupted"}:
            phase, action, reason = "failed", relevant["operation"], relevant["stage"]
            if action == "verify-export":
                action = "export"  # A failed integrity check needs a fresh bundle, not an endless retry.
            elif action == "sync-export" and matching and relevant.get("snapshot_id") == matching["id"]:
                action = "export"  # The sync completed; only the bundle needs another attempt.
            # A later successful export/verification record can outlive its job.
            if bundle and bundle["state"] == "available" and bundle["verified_at"] > relevant.get("finished", relevant["created"]):
                phase, action, reason = "ready", None, "The verified bundle is available for your approved transfer process."
        elif not matching:
            phase, action, reason = "sync", "sync", "No validated snapshot matches the saved repository selection."
        elif not bundle:
            phase, action, reason = "export", "export", "The saved selection has a validated snapshot that has not been exported."
        elif bundle["state"] == "available":
            phase, action, reason = "ready", None, "The verified bundle is available for your approved transfer process."
        else:
            action = "export" if bundle["state"] == "missing" else "verify-export"
            phase, reason = "verify" if action == "verify-export" else "export", bundle["reason"]
        if active:
            phase, action, reason = "running", None, active["stage"]
        selected = []
        for rid in sorted(selection):
            snapshot = next((s for s in snapshots if rid in s["repositories"]), None)
            selected.append({"id": rid, "snapshot_id": snapshot["id"] if snapshot else None,
                             "synced_at": snapshot["created"] if snapshot else None,
                             "repository": snapshot["repositories"][rid] if snapshot else None,
                             "export": snapshot.get("export") if snapshot else None})
        return {"phase": phase, "action": action, "reason": reason, "active": active,
                "attention_job": relevant if phase == "failed" else None,
                "latest_sync": latest, "selection_snapshot": matching, "latest_export": latest_export,
                "selection_changed": bool(latest and set(latest["repositories"]) != selection),
                "selected_repositories": selected, "plan_job": plan_job}

    def disconnected_status(self, snapshots, jobs, source, incoming, publication):
        imported = sorted((s for s in snapshots if s.get("imported") and s["status"] == "validated"),
                          key=lambda s: s["created"], reverse=True)
        latest = imported[0] if imported else None
        by_checksum = {s["source_archive_sha256"]: s for s in imported if s.get("source_archive_sha256")}
        evidence = {}
        for path in (self.root / "import-records").glob("*.json"):
            record = read_json(path)
            key = (record["source_path"], record["source_archive_sha256"])
            if key not in evidence or record["checked_at"] > evidence[key]["checked_at"]:
                evidence[key] = record
        for bundle in incoming:
            bundle.update(state="awaiting_import", reason="Checksum present; archive still needs validation", snapshot_id=None)
            path = Path(source["path"]) / bundle["name"]
            try:
                checksum = self.expected_archive_checksum(path)
                snapshot = by_checksum.get(checksum)
                if snapshot:
                    bundle["snapshot_id"] = snapshot["id"]
                    record = evidence.get((str(path), checksum))
                    if record and record.get("fingerprint") == self.fingerprint(path):
                        bundle.update(state="imported", reason="Imported content recorded; source file attributes are unchanged")
                    elif record:
                        bundle.update(state="changed", reason="Source archive changed since its last import check; validate it again")
                    else:
                        bundle.update(state="known", reason="Checksum identifies previously imported content; this source file has not been rechecked")
                attempt = next((j for j in jobs if j.get("operation") == "import"
                                and j.get("bundle_name") == bundle["name"]
                                and j.get("incoming_dir", str(self.root / "incoming")) == source["path"]), None)
                if attempt and attempt["status"] in {"failed", "cancelled", "interrupted"}:
                    bundle.update(state="changed", reason="The last import attempt for this file did not complete successfully; validate it again")
            except (ValueError, OSError, UnicodeError) as error:
                bundle.update(state="blocked", reason=str(error))
        pending = [b for b in incoming if b["state"] in {"awaiting_import", "changed", "blocked"}]
        current = publication["current"]
        matches = bool(latest and current and latest["id"] == current["snapshot_id"])
        url_changed = bool(current and publication["base_url"] != current["base_url"])
        serving = bool(current and publication["installed"] and publication["server_active"]
                       and publication["availability"]["state"] == "available")
        active = next((j for j in jobs if j["status"] in {"queued", "running"}), None)
        attempt = next((j for j in jobs if j.get("operation") == "publish" or
                        j.get("operation") == "import" and j.get("incoming_dir", str(self.root / "incoming")) == source["path"]), None)
        phase, reason = "clients", "Published content and client setup downloads are available. Client application is not confirmed."
        target = latest["id"] if latest else None
        if latest and (not matches or url_changed or not serving):
            phase = "publisher_setup" if not publication["installed"] else "server_url" if not publication["base_url"] else "publish"
            reason = ("Install the optional publisher package to serve imported content." if phase == "publisher_setup" else
                      "Save the server URL clients can reach inside the air gap." if phase == "server_url" else
                      "The saved server URL differs from the recorded publication; review and republish." if url_changed else
                      "The latest imported snapshot is not the current publication." if not matches else
                      "Apache is not running; review and republish to restore service." if not publication["server_active"] else
                      publication["availability"]["reason"] + ". Review and republish to restore the public copy.")
        elif not latest and not serving or pending:
            if not source["available"]:
                phase, reason = "source", "The saved transfer folder is unavailable. Mount the media or choose an existing directory."
            elif not self.config.get("allow_unsigned_imports"):
                phase, reason = "import_policy", "Prototype imports are disabled by administrator policy. Review the import requirements."
            elif incoming:
                phase, reason = "import", "Review transferred bundles and validate the content you want to import."
            else:
                phase, reason = "waiting", "No transfer bundles were found. Stage an archive and its matching checksum in the saved source folder."
        attention = None
        # A failed import stops demanding attention once its file is gone from the transfer folder.
        removed = (attempt and attempt.get("operation") == "import" and attempt.get("bundle_name")
                   and attempt["bundle_name"] not in {b["name"] for b in incoming})
        if attempt and attempt["status"] in {"failed", "cancelled", "interrupted"} and not removed:
            milestone = max(latest["created"] if latest else "", current["published_at"] if current else "")
            if attempt.get("finished", attempt["created"]) > milestone:
                phase, reason, attention = "failed", attempt["stage"], attempt
                if attempt["operation"] == "publish":
                    target = attempt.get("snapshot_id")
        if active:
            phase, reason, attention = "running", active["stage"], None
        repository_status = []
        for rid in sorted({rid for snapshot in imported for rid in snapshot["repositories"]}):
            snapshot = next(s for s in imported if rid in s["repositories"])
            repository_status.append({"id": rid, "snapshot_id": snapshot["id"], "imported_at": snapshot["created"],
                                      "repository": snapshot["repositories"][rid],
                                      "publication_state": "current" if serving and current["snapshot_id"] == snapshot["id"] else
                                      "older" if current and rid in current["repositories"] else "not_published"})
        return {"phase": phase, "reason": reason, "active": active, "attention_job": attention,
                "latest_import": latest, "publication_matches_latest": matches, "publication_serving": serving,
                "server_url_changed": url_changed, "target_snapshot_id": target,
                "pending_bundles": len(pending), "known_bundles": len(incoming) - len(pending),
                "repositories": repository_status, "client_application": "unconfirmed"}

    def status(self):
        jobs = self.jobs()
        snapshots = [read_json(p) for p in (self.root / "snapshots").glob("*/snapshot.json")]
        usage = shutil.disk_usage(self.root)
        source, incoming = self.scan_incoming() if self.config.get("role") == "disconnected" else (None, [])
        connected = self.connected_status(snapshots, jobs) if self.config.get("role") == "connected" else None
        publication = self.publication_status() if self.config.get("role") == "disconnected" else None
        disconnected = self.disconnected_status(snapshots, jobs, source, incoming, publication) if publication else None
        return {"version": VERSION, "role": self.config.get("role"), "os": host_os(),
                "storage": {"path": str(self.root), "free": usage.free, "total": usage.total,
                            "reserve": self.floor}, "profile": self.profile(), "repositories": repositories(),
                "jobs": jobs[:50], "snapshots": sorted(snapshots, key=lambda s: s["created"], reverse=True),
                "incoming": incoming, "import_source": source,
                "signing_key_files": self.key_files() if self.config.get("role") == "disconnected" else [],
                "bundle_key_candidates": [{k: v for k, v in read_json(p).items() if k != 'public_key'}
                                          for p in sorted((self.root / 'bundle-key-candidates').glob('*.json'),
                                                          key=lambda p: p.stat().st_mtime, reverse=True)[:200]],
                "trusted_signing_keys": [read_json(p) for p in sorted((self.root / "trusted-keys").glob("*.json"))],
                "publication": publication, "disconnected": disconnected,
                "connected": connected,
                "export_settings": self.export_settings() if self.config.get("role") == "connected" else None,
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

    @staticmethod
    def expected_archive_checksum(path):
        sidecar = safe_path(path.parent, path.name + ".sha256")
        if not sidecar.is_file():
            raise ValueError("A regular checksum sidecar is required beside the archive")
        if sidecar.stat().st_size > 1024:
            raise ValueError("Checksum sidecar is too large")
        match = re.fullmatch(r"([a-f0-9]{64}) [ *]" + re.escape(path.name) + r"\n?", sidecar.read_text())
        if match is None:
            raise ValueError("Checksum sidecar has an invalid format or archive filename")
        return match.group(1)

    def record_import_check(self, job, path, snapshot, fingerprint):
        record = {"snapshot_id": snapshot["id"], "imported_at": snapshot["created"],
                  "checked_at": now(), "source_path": str(path), "source_archive_sha256": snapshot["source_archive_sha256"],
                  "fingerprint": fingerprint}
        write_json(self.root / "import-records" / f"{job['id']}.json", record)

    def start(self, operation, snapshot_id=None):
        if operation not in {"preflight", "sync", "sync-export", "export", "verify-export", "import", "publish"}:
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
            if operation in {"preflight", "sync", "sync-export"}:
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
        source_fingerprint = self.fingerprint(path)
        limit = int(self.config.get("max_import_gib", 200)) * GIB
        if path.stat().st_size > limit + GIB:
            raise ValueError("Transfer archive exceeds the import size limit")
        expected = self.expected_archive_checksum(path)
        self.update(job, step="checksum", stage="Verifying transferred archive against its SHA-256 sidecar")
        checksum = digest(path)
        if expected != checksum:
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
                if self.fingerprint(path) != source_fingerprint:
                    raise ValueError("Source archive changed during verification; scan and validate it again")
                self.record_import_check(job, path, existing, source_fingerprint)
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
            self.update(job, step="extract", stage="Checking archive members and extracting to private staging")
            verified = verify_archive(path, extract_to=staging, max_bytes=limit, reserve_bytes=self.floor)
            self.update(job, step="validate", stage="Revalidating repository metadata and trusted RPM signatures")
            checked = {}
            for rid in manifest["repositories"]:
                checked[rid] = self.validate_content(staging / "repositories" / rid, rid)
            verified["verified_at"] = now()
            snapshot = {"id": sid, "created": now(), "verified_at": verified["verified_at"],
                        "source_created": manifest.get("created"), "status": "validated", "os": manifest["os"],
                        "repositories": checked, "package_bytes": sum(r["package_bytes"] for r in checked.values()),
                        "imported": True, "source_archive_sha256": checksum, "authenticated": False}
            write_json(staging / "snapshot.json", snapshot)
            if self.fingerprint(path) != source_fingerprint:
                raise ValueError("Source archive changed during import; scan and validate it again")
            # The snapshot becomes visible only after every validation succeeds.
            os.rename(staging, destination)
            self.record_import_check(job, path, snapshot, source_fingerprint)
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
        self.update(job, step="space", stage="Resolving repository sizes; refreshing metadata if needed")
        # Host include/exclude filters shape this host's own updates; mirrors keep complete repositories.
        args = ["/usr/bin/dnf", "-q", "--disableexcludes=all", "--disablerepo=*"]
        args += [f"--enablerepo={r}" for r in profile["repositories"]]
        args += ["repoquery", "--available", "--show-duplicates", "--disable-modular-filtering", "--qf", "%{repoid} %{downloadsize}"]
        # stderr is local-only; stdout contains just repository IDs and declared RPM byte sizes.
        with (self.root / "jobs" / f"{job['id']}.log").open("ab") as log:
            result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=900)
            log.write(result.stderr.encode())
        if result.returncode:
            raise ValueError("Cannot query repository sizes; inspect job logs")
        sizes, found = [], set()
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                found.add(parts[0]); sizes.append(int(parts[1]))
        empty = [rid for rid in profile["repositories"] if rid not in found]
        if empty:
            raise ValueError(f"No packages to mirror in {', '.join(empty)}; remove {'it' if len(empty) == 1 else 'them'} from the selection")
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
        if not plan["within_budget"]:
            raise ValueError(f"The selection has {plan['package_bytes'] / GIB:.1f} GiB of packages, over the {profile['max_gib']} GiB size limit; "
                             "no RPM sync started. Select fewer repositories or raise the limit.")
        if not plan["enough_space"]:
            raise ValueError(f"The selection needs {plan['required_free'] / GIB:.1f} GiB free, but {plan['free_bytes'] / GIB:.1f} GiB is available; "
                             "no RPM sync started")
        sid = job["id"]
        snapshot_dir = self.root / "snapshots" / sid
        content = snapshot_dir / "repositories"
        content.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.update(job, snapshot_id=sid, step="download")
        args = ["/usr/bin/dnf", "-y", "--disableexcludes=all", "--disablerepo=*"]
        for rid in profile["repositories"]:
            args += [f"--enablerepo={rid}", f"--setopt={rid}.gpgcheck=1"]
        args += ["reposync", "--download-metadata", "--gpgcheck", "--download-path", str(content)]
        try:
            self.run_command(args, job, "Downloading repository packages and original metadata")
        except ValueError as error:
            raise ValueError(explain_reposync_failure(self.logs(job["id"])["text"]) or str(error)) from None
        self.update(job, step="validate", stage="Verifying metadata, referenced packages, and RPM signatures")
        checked = {}
        for rid in profile["repositories"]:
            try:
                checked[rid] = validate_repository(content / rid, require_updateinfo=("baseos" in rid or "appstream" in rid))
            except ValueError as error:
                raise ValueError(f"{rid}: {error}") from None
        snapshot = {"id": sid, "created": now(), "status": "validated", "os": host_os(),
                    "repositories": checked, "package_bytes": sum(r["package_bytes"] for r in checked.values())}
        write_json(snapshot_dir / "snapshot.json", snapshot)
        self.update(job, result={"snapshot_id": sid, "repositories": checked})

    def export(self, job):
        sid = job["snapshot_id"]
        snapshot_dir = self.root / "snapshots" / sid
        snapshot = read_json(snapshot_dir / "snapshot.json")
        self.update(job, step="bundle", stage="Revalidating snapshot before export")
        for rid in snapshot["repositories"]:
            validate_repository(snapshot_dir / "repositories" / rid,
                                require_updateinfo=("baseos" in rid or "appstream" in rid))
        self.update(job, stage="Collecting configured public signing keys for disconnected review")
        key_payloads, bundled_keys = {}, {}
        for rid, sources in configured_signing_key_sources(snapshot['repositories']).items():
            if len(sources) > 8:
                raise ValueError("Repository has more than eight configured public-key sources")
            for source in sources:
                try:
                    blocks = read_public_key_source(source)
                except Exception as error:
                    raise ValueError(f"Cannot collect public signing keys for {rid}; check its configured file or HTTPS key source") from error
                for armor in blocks:
                    raw_key = armor.encode('ascii')
                    checksum = hashlib.sha256(raw_key).hexdigest()
                    name = f"repositories/{rid}/signing-keys/{checksum}.asc"
                    if name not in key_payloads:
                        key_payloads[name] = raw_key
                        bundled_keys.setdefault(rid, []).append({'path': name, 'sha256': checksum})
                    if len(bundled_keys[rid]) > 8:
                        raise ValueError("Repository has more than eight public signing keys")
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
        for name, raw_key in key_payloads.items():
            if name in files:
                raise ValueError("Snapshot content conflicts with the bundled public-key directory")
            files[name] = {'size': len(raw_key), 'sha256': hashlib.sha256(raw_key).hexdigest()}
        required += sum(len(raw_key) for raw_key in key_payloads.values())
        destination = self.export_directory()
        if shutil.disk_usage(destination).free < required:
            raise ValueError(f"Not enough free space in {destination} for the complete export and storage reserve")
        manifest = {"format": "disconnected-content/v1", "snapshot_id": sid, "created": now(),
                    "os": snapshot["os"], "repositories": snapshot["repositories"], "files": files,
                    "authentication": "unsigned-prototype"}
        manifest['signing_keys'] = bundled_keys
        import io
        raw = json.dumps(manifest, sort_keys=True, indent=2).encode() + b"\n"
        dest = destination / f"{sid}.tar"
        partial = dest.with_suffix(".tar.partial")
        self.update(job, stage="Writing transfer archive")
        with tarfile.open(partial, "w") as archive:
            header = tarfile.TarInfo("manifest.json")
            header.size = len(raw)
            header.mode = 0o644
            archive.addfile(header, io.BytesIO(raw))
            for name in files:
                if name in key_payloads:
                    info = tarfile.TarInfo(name)
                    info.size, info.mode = len(key_payloads[name]), 0o644
                    archive.addfile(info, io.BytesIO(key_payloads[name]))
                    continue
                path = safe_path(snapshot_dir, name)
                info = archive.gettarinfo(str(path), arcname=name)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
        self.update(job, step="verify", stage="Verifying every archived file before completing export")
        verified = verify_archive(partial)
        checksum = digest(partial)
        os.replace(partial, dest)
        sidecar = dest.with_suffix(".tar.sha256")
        sidecar.write_text(f"{checksum}  {dest.name}\n")
        self.record_export(sid, job["id"], verified, checksum, exported_at=now(), path=dest)
        self.update(job, result={"path": str(dest), "sha256": checksum, "bytes": dest.stat().st_size, **verified})

    def record_export(self, sid, job_id, verified, checksum, exported_at=None, path=None):
        record_path = self.root / "export-records" / f"{sid}.json"
        with self.lock():
            previous = read_json(record_path) if record_path.exists() else {}
            path = path or self.export_archive_path({**previous, "snapshot_id": sid})
            record = {"snapshot_id": sid, "exported_at": exported_at or previous.get("exported_at"),
                      "verified_at": verified["verified_at"], "job_id": previous.get("job_id", job_id) if not exported_at else job_id,
                      "verification_job_id": job_id, "sha256": checksum, "bytes": path.stat().st_size,
                      "bundled_signing_keys": verified.get('bundled_signing_keys', 0),
                      "files_verified": verified["files_verified"], "fingerprint": self.fingerprint(path), "path": str(path)}
            write_json(record_path, record)

    def verify_export(self, job):
        sid = job["snapshot_id"]
        record_path = self.root / "export-records" / f"{sid}.json"
        path = self.export_archive_path(read_json(record_path) if record_path.is_file() else {"snapshot_id": sid})
        sidecar = path.with_suffix(".tar.sha256")
        if path.is_symlink() or sidecar.is_symlink() or not path.is_file() or not sidecar.is_file():
            raise ValueError("Archive and checksum must both be present as regular files; rebuild the transfer bundle")
        self.update(job, stage="Verifying exported archive and checksum")
        verified = verify_archive(path)
        if verified["snapshot_id"] != sid:
            raise ValueError("Archive belongs to a different snapshot; rebuild the transfer bundle")
        checksum = digest(path)
        if sidecar.stat().st_size > 4096 or sidecar.read_text() != f"{checksum}  {path.name}\n":
            raise ValueError("Archive checksum does not match its sidecar; rebuild the transfer bundle")
        previous = self.export_records(self.jobs()).get(sid)
        if previous and previous["sha256"] != checksum:
            raise ValueError("Archive differs from the last successful export; rebuild the transfer bundle")
        self.record_export(sid, job["id"], verified, checksum, path=path)
        self.update(job, result={"path": str(path), "sha256": checksum, "bytes": path.stat().st_size, **verified})

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
            elif job["operation"] == "sync-export":
                self.sync(job)
                synced = job["result"]
                self.export(job)
                self.update(job, result={**synced, **job["result"]})
            elif job["operation"] == "export":
                self.export(job)
            elif job["operation"] == "verify-export":
                self.verify_export(job)
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
            if isinstance(error, SignatureError):
                job["signature_failure"] = error.details
                with (self.root / "jobs" / (jid + ".log")).open("a") as stream:
                    stream.write(json.dumps(error.details, indent=2) + "\n")
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
    parser.add_argument("command", choices=["status", "client-automation", "scan-bundle-keys", "review-signing-key", "trust-signing-key", "save-profile", "save-import-settings", "save-publication-settings", "save-export-settings", "browse-directories", "bundle-info", "start", "run-job", "logs", "cancel"])
    parser.add_argument("argument", nargs="?")
    parser.add_argument("snapshot", nargs="?")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError("Administrative access is required")
    # Browser commands expose narrow administrative operations; installation policy remains root-owned.
    if CONFIG.is_symlink() or CONFIG.stat().st_uid != 0 or CONFIG.stat().st_mode & 0o022:
        raise ValueError("Configuration must be root-owned and not writable by group or others")
    engine = Engine()
    if args.command == "status":
        result = engine.status()
    elif args.command == "client-automation":
        result = engine.client_automation(args.argument)
    elif args.command in {"save-profile", "save-import-settings", "save-publication-settings", "save-export-settings", "browse-directories", "bundle-info", "scan-bundle-keys", "review-signing-key", "trust-signing-key"}:
        limit = 128 * 1024 if args.command == "review-signing-key" else 16384
        raw = sys.stdin.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("Request is too large")
        data = json.loads(raw)
        if args.command == "scan-bundle-keys":
            result = engine.scan_bundle_keys(data)
        elif args.command == "review-signing-key":
            result = engine.review_signing_key(data)
        elif args.command == "trust-signing-key":
            result = engine.trust_signing_key(data)
        elif args.command == "save-profile":
            result = engine.save_profile(data)
        elif args.command == "save-import-settings":
            result = engine.save_import_settings(data)
        elif args.command == "save-publication-settings":
            result = engine.save_publication_settings(data)
        elif args.command == "save-export-settings":
            result = engine.save_export_settings(data)
        elif args.command == "bundle-info":
            result = engine.bundle_info(data)
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

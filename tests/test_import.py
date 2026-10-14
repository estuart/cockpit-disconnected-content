"""Disconnected import trust gates, atomic visibility, and hostile archive cases."""
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from test_engine import engine, fixture


class DisconnectedImport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = engine.Engine({"role": "connected", "state_dir": str(root / "source"), "reserve_gib": 0})
        self.target = engine.Engine({"role": "disconnected", "state_dir": str(root / "target"), "reserve_gib": 0,
                                     "allow_unsigned_imports": True, "max_import_gib": 1})
        self.sid = "1" * 32
        self.rid = "rhel-10-for-x86_64-highavailability-rpms"
        snapshot = self.source.root / "snapshots" / self.sid
        fixture(snapshot / "repositories" / self.rid)
        self.summary = {"packages": 1, "package_bytes": 48, "metadata_types": ["primary", "updateinfo"],
                        "rpm_signatures_verified": True}
        engine.write_json(snapshot / "snapshot.json", {"id": self.sid, "os": {"major": "10", "arch": "x86_64"},
                          "repositories": {self.rid: self.summary}, "status": "validated"})
        with patch.object(engine, "validate_repository", return_value=self.summary):
            self.source.export({"id": "2" * 32, "snapshot_id": self.sid})
        self.path = self.target.root / "incoming" / f"{self.sid}.tar"
        self.path.write_bytes((self.source.root / "exports" / self.path.name).read_bytes())
        self.sidecar()
        self.job = {"id": "3" * 32, "bundle_name": self.path.name}
        self.os_patch = patch.object(engine, "host_os", return_value={"major": "10", "arch": "x86_64"})
        self.os_patch.start()
        self.addCleanup(self.os_patch.stop)

    def sidecar(self):
        self.path.with_suffix(".tar.sha256").write_text(f"{engine.digest(self.path)}  {self.path.name}\n")

    def import_valid(self):
        with patch.object(engine, "validate_repository", return_value=self.summary):
            self.target.import_bundle(self.job)

    def test_import_is_verified_and_visible_only_after_success(self):
        destination = self.target.root / "snapshots" / self.sid
        def validate(path, **kwargs):
            self.assertFalse(destination.exists())
            self.assertTrue((path / "repodata/repomd.xml").is_file())
            return self.summary
        with patch.object(engine, "validate_repository", side_effect=validate):
            self.target.import_bundle(self.job)
        saved = engine.read_json(destination / "snapshot.json")
        self.assertTrue(saved["imported"])
        self.assertEqual(saved["repositories"][self.rid]["packages"], 1)
        self.assertFalse(saved["authenticated"])
        self.assertFalse(list((self.target.root / ".imports").iterdir()))

    def test_unsigned_imports_require_explicit_admin_opt_in(self):
        self.target.config.pop("allow_unsigned_imports")
        with self.assertRaisesRegex(ValueError, "Unsigned imports are disabled"):
            self.target.import_bundle(self.job)

    def test_wrong_role_cannot_import(self):
        self.target.config["role"] = "connected"
        with self.assertRaisesRegex(ValueError, "disconnected installation"):
            self.target.import_bundle(self.job)

    def test_changed_transfer_is_rejected_before_extraction(self):
        data = self.path.read_bytes()
        self.path.write_bytes(data[:-1] + b"x")
        with self.assertRaisesRegex(ValueError, "Transfer checksum mismatch"):
            self.target.import_bundle(self.job)
        self.assertFalse(list((self.target.root / "snapshots").iterdir()))

    def test_mismatched_architecture_is_rejected(self):
        with patch.object(engine, "host_os", return_value={"major": "10", "arch": "aarch64"}):
            with self.assertRaisesRegex(ValueError, "architecture does not match"):
                self.target.import_bundle(self.job)

    def test_signature_failure_leaves_no_visible_or_partial_snapshot(self):
        with patch.object(engine, "validate_repository", side_effect=ValueError("RPM signature validation failed")):
            with self.assertRaisesRegex(ValueError, "signature validation failed"):
                self.target.import_bundle(self.job)
        self.assertFalse(list((self.target.root / "snapshots").iterdir()))
        self.assertFalse(list((self.target.root / ".imports").iterdir()))

    def test_identical_reimport_is_idempotent(self):
        self.import_valid()
        self.target.import_bundle(self.job)
        self.assertTrue(self.job["result"]["already_imported"])
        self.assertEqual(len(list((self.target.root / "snapshots").iterdir())), 1)

    def test_symlink_inbox_entry_is_not_accepted(self):
        alias = self.target.root / "incoming" / "alias.tar"
        alias.symlink_to(self.path)
        with self.assertRaisesRegex(ValueError, "Symlinks"):
            self.target.bundle_path("alias.tar")

    def test_declared_size_limit_is_enforced(self):
        with self.assertRaisesRegex(ValueError, "size limit"):
            engine.verify_archive(self.path, max_bytes=1)

    def test_archive_links_and_duplicate_members_are_rejected(self):
        with tarfile.open(self.path) as source:
            members = [(member, source.extractfile(member).read()) for member in source]
        for mode in ["link", "duplicate"]:
            with self.subTest(mode=mode):
                altered = self.target.root / "incoming" / f"{mode}.tar"
                with tarfile.open(altered, "w") as archive:
                    for member, raw in members:
                        archive.addfile(member, io.BytesIO(raw))
                    member, raw = members[-1]
                    if mode == "link":
                        link = tarfile.TarInfo(member.name)
                        link.type = tarfile.SYMTYPE; link.linkname = "/etc/passwd"
                        archive.addfile(link)
                    else:
                        archive.addfile(member, io.BytesIO(raw))
                with self.assertRaisesRegex(ValueError, "duplicate, or non-regular"):
                    engine.verify_archive(altered)

    def test_decompressed_metadata_has_a_bound(self):
        stream = engine.LimitedMetadataReader(io.BytesIO(b"a" * 20), limit=10)
        with self.assertRaisesRegex(ValueError, "size limit"):
            stream.read()


if __name__ == "__main__":
    unittest.main()

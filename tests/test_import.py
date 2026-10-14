"""Disconnected import trust gates, atomic visibility, and hostile archive cases."""
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
import subprocess
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
        with patch.object(engine, "validate_repository", return_value=self.summary), patch.object(engine, "configured_signing_key_sources", return_value={}):
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
        self.assertIn("verified_at", saved)
        self.assertEqual(saved["verified_at"], self.job["result"]["verified_at"])
        self.assertIn("updated", self.job)
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
        self.assertIn("checked_at", self.job["result"])
        self.assertNotIn("verified_at", self.job["result"])
        self.assertEqual(len(list((self.target.root / "snapshots").iterdir())), 1)

    def test_symlink_inbox_entry_is_not_accepted(self):
        alias = self.target.root / "incoming" / "alias.tar"
        alias.symlink_to(self.path)
        with self.assertRaisesRegex(ValueError, "Symlinks"):
            self.target.bundle_path("alias.tar")

    def test_import_from_external_directory_records_lifecycle_and_evidence(self):
        media = (Path(self.temp.name) / "media").resolve()
        media.mkdir()
        self.path.rename(media / self.path.name)
        self.path.with_suffix(".tar.sha256").rename(media / (self.path.name + ".sha256"))
        self.target.config["incoming_dir"] = str(media)
        with patch.object(engine.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            job = self.target.start("import", self.path.name)
        with patch.object(engine, "validate_repository", return_value=self.summary):
            self.target.run(job["id"])
        saved = engine.read_json(self.target.job_path(job["id"]))
        self.assertEqual(saved["status"], "succeeded")
        for key in ["created", "started", "updated", "finished"]:
            self.assertIn(key, saved)
        self.assertIn("verified_at", saved["result"])
        self.assertTrue((media / self.path.name).exists())

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


class ImportSourceSettings(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config_path = self.root / "config.json"
        self.config = {"role": "disconnected", "state_dir": str(self.root / "state"),
                       "reserve_gib": 0, "allow_unsigned_imports": False, "max_import_gib": 2}
        engine.write_json(self.config_path, self.config)
        config_patch = patch.object(engine, "CONFIG", self.config_path)
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.app = engine.Engine()
        self.media = self.root / "transfer media"
        self.media.mkdir()

    def save(self, path=None):
        return self.app.save_import_settings({"incoming_dir": str(path or self.media)})

    def test_source_is_persisted_without_changing_policy_or_moving_content(self):
        old = self.app.root / "incoming" / "old.tar"
        old.write_bytes(b"existing")
        self.save()
        reopened = engine.Engine()
        self.assertEqual(reopened.incoming_directory(), self.media)
        saved = engine.read_json(self.config_path)
        self.assertFalse(saved["allow_unsigned_imports"])
        self.assertEqual(saved["max_import_gib"], 2)
        self.assertIn("incoming_updated_at", saved)
        self.assertEqual(old.read_bytes(), b"existing")
        self.assertEqual(list(self.media.iterdir()), [])

    def test_invalid_sources_do_not_replace_saved_settings(self):
        self.save()
        alias = self.root / "alias"
        alias.symlink_to(self.media, target_is_directory=True)
        ordinary_file = self.root / "file"
        ordinary_file.touch()
        for value in ["relative/path", str(self.root / "missing"), str(alias), str(ordinary_file),
                      str(alias / "child"), str(self.root / ".." / "transfer"), "https://example.invalid", "", None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.app.save_import_settings({"incoming_dir": value})
        self.assertEqual(engine.Engine().incoming_directory(), self.media)

    def test_settings_cannot_change_role_or_import_trust_policy(self):
        with self.assertRaisesRegex(ValueError, "only the bundle source"):
            self.app.save_import_settings({"incoming_dir": str(self.media), "allow_unsigned_imports": True})
        self.app.config["role"] = "connected"
        with self.assertRaisesRegex(ValueError, "disconnected installation"):
            self.save()

    def test_scan_is_shallow_newest_first_with_modification_timestamps(self):
        self.save()
        for name, modified in [("new.tar", 200), ("old.tar", 100)]:
            path = self.media / name
            path.write_bytes(b"bundle")
            os.utime(path, (modified, modified))
        (self.media / "new.tar.sha256").write_text("checksum")
        (self.media / "alias.tar").symlink_to(self.media / "new.tar")
        (self.media / "old.tar.sha256").symlink_to(self.media / "new.tar.sha256")
        (self.media / "subdir").mkdir()
        (self.media / "subdir" / "nested.tar").touch()
        source, bundles = self.app.scan_incoming()
        self.assertTrue(source["available"])
        self.assertIn("scanned_at", source)
        self.assertEqual([b["name"] for b in bundles], ["new.tar", "old.tar"])
        self.assertEqual(bundles[0]["modified"], "1970-01-01T00:03:20+00:00")
        self.assertTrue(bundles[0]["has_checksum"])
        self.assertFalse(bundles[1]["has_checksum"])

    def test_missing_media_does_not_break_status_and_scan_recovers(self):
        self.save()
        self.media.rmdir()
        with patch.object(engine, "repositories", return_value=[]), patch.object(engine, "host_os", return_value={}):
            status = self.app.status()
        self.assertFalse(status["import_source"]["available"])
        self.assertIn("unavailable", status["import_source"]["error"])
        self.assertEqual(status["incoming"], [])
        self.media.mkdir()
        self.assertTrue(self.app.scan_incoming()[0]["available"])

    def test_running_import_pins_source_and_blocks_settings_changes(self):
        self.save()
        (self.media / "bundle.tar").write_bytes(b"bundle")
        with patch.object(engine.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            job = self.app.start("import", "bundle.tar")
        self.assertEqual(job["incoming_dir"], str(self.media))
        self.assertIn("created", job)
        self.assertIn("updated", job)
        with self.assertRaisesRegex(ValueError, "active job"):
            self.save(self.app.root / "incoming")

    def test_worker_uses_recorded_source_directory(self):
        # A queued record remains unambiguous even if configuration is subsequently edited.
        self.save()
        (self.media / "bundle.tar").write_bytes(b"bundle")
        selected = self.app.bundle_path("bundle.tar", str(self.media))
        self.app.config["incoming_dir"] = str(self.app.root / "incoming")
        self.assertEqual(self.app.bundle_path("bundle.tar", str(self.media)), selected)

    def test_nonregular_checksum_is_not_offered(self):
        self.save()
        (self.media / "bundle.tar").touch()
        os.mkfifo(self.media / "bundle.tar.sha256")
        self.assertFalse(self.app.incoming()[0]["has_checksum"])

    def test_directory_browser_lists_only_real_folders_without_changing_settings(self):
        self.save()
        for name in ["zeta", "Transfer bundles", ".hidden", "alpha"]:
            (self.media / name).mkdir()
        (self.media / "alpha" / "nested").mkdir()
        (self.media / "archive.tar").touch()
        (self.media / "alias").symlink_to(self.media / "alpha", target_is_directory=True)
        os.mkfifo(self.media / "pipe")
        before = self.config_path.read_bytes()
        result = self.app.browse_directories({"path": str(self.media)})
        self.assertEqual([directory["name"] for directory in result["directories"]],
                         [".hidden", "alpha", "Transfer bundles", "zeta"])
        self.assertEqual(result["parent"], str(self.root))
        self.assertFalse(result["truncated"])
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(self.app.browse_directories({"path": "/"})["parent"], None)

    def test_directory_browser_rejects_invalid_paths_and_connected_role(self):
        alias = self.root / "alias"
        alias.symlink_to(self.media, target_is_directory=True)
        for path in [None, "", "relative", str(alias), str(self.root / ".."), str(self.root / "missing")]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.app.browse_directories({"path": path})
        with self.assertRaises(ValueError):
            self.app.browse_directories({"path": str(self.media), "command": "ignored"})
        self.app.config["role"] = "connected"
        with self.assertRaisesRegex(ValueError, "disconnected installation"):
            self.app.browse_directories({"path": str(self.media)})

    def test_mounted_locations_decode_spaces_refresh_and_omit_missing_targets(self):
        mounts = self.root / "mounts"
        escaped = str(self.media).replace(" ", r"\040")
        mounts.write_text(f"/dev/test {escaped} ext4 ro 0 0\n"
                          f"/dev/root / xfs rw 0 0\n/dev/missing {self.root}/absent vfat rw 0 0\n")
        with patch.object(engine, "MOUNTS", mounts):
            result = self.app.browse_directories({"path": str(self.media)})
            mounted = [location for location in result["locations"] if location["kind"] == "mount"]
            self.assertEqual([location["path"] for location in mounted], [str(self.media)])
            mounts.write_text("")
            self.assertFalse(any(location["kind"] == "mount" for location in self.app.directory_locations()))

    def test_mounted_saved_source_is_still_listed_as_a_mount(self):
        self.save()
        mounts = self.root / "mounts"
        escaped = str(self.media).replace(" ", r"\040")
        mounts.write_text(f"/dev/test {escaped} ext4 ro 0 0\n")
        with patch.object(engine, "MOUNTS", mounts):
            locations = self.app.directory_locations()
        self.assertEqual(next(location for location in locations if location["path"] == str(self.media))["kind"], "mount")

    def test_directory_browser_bounds_response_size(self):
        for index in range(501):
            (self.media / f"folder-{index:04d}").mkdir()
        result = self.app.browse_directories({"path": str(self.media)})
        self.assertEqual(len(result["directories"]), 500)
        self.assertTrue(result["truncated"])

    def test_directory_browser_works_without_mount_metadata(self):
        with patch.object(engine, "MOUNTS", self.root / "missing-mount-metadata"):
            result = self.app.browse_directories({"path": str(self.media)})
        self.assertIn("/", [location["path"] for location in result["locations"]])


if __name__ == "__main__":
    unittest.main()

"""Publication boundaries, immutable content, generated configuration, and failures."""
import configparser
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_engine import engine, fixture


class Publication(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config_path = self.root / "config.json"
        self.public_root = self.root / "web"
        self.web_conf = self.root / "publisher.conf"
        self.web_conf.write_text("Apache configuration fixture")
        for name, value in [("CONFIG", self.config_path), ("PUBLISH_ROOT", self.public_root), ("PUBLISH_CONF", self.web_conf)]:
            override = patch.object(engine, name, value)
            override.start()
            self.addCleanup(override.stop)
        engine.write_json(self.config_path, {"role": "disconnected", "state_dir": str(self.root / "state"), "reserve_gib": 0})
        self.app = engine.Engine()
        self.sid, self.rid = "a" * 32, "rhel-10-for-x86_64-highavailability-rpms"
        self.source = self.app.root / "snapshots" / self.sid
        fixture(self.source / "repositories" / self.rid)
        self.snapshot = {"id": self.sid, "status": "validated", "os": {"major": "10", "arch": "x86_64"},
                         "repositories": {self.rid: {}}}
        engine.write_json(self.source / "snapshot.json", self.snapshot)
        self.app.save_publication_settings({"base_url": "http://mirror.example.org"})
        self.job = {"id": "b" * 32, "snapshot_id": self.sid, "public_base_url": "http://mirror.example.org"}

        def command(args, **kwargs):
            if args[0] == "/usr/bin/rpmkeys":
                return subprocess.CompletedProcess(args, 0, "\n".join(f"{path}: digests signatures OK" for path in args[2:]), "")
            return subprocess.CompletedProcess(args, 0, "", "")
        runner = patch.object(engine.subprocess, "run", side_effect=command)
        runner.start()
        self.addCleanup(runner.stop)
        self.commands = patch.object(self.app, "run_command")
        self.run_command = self.commands.start()
        self.addCleanup(self.commands.stop)

    def test_publication_exposes_only_indexed_content_after_verification(self):
        (self.source / "repositories" / self.rid / "unindexed-secret.txt").write_text("excluded")
        destination = self.public_root / "public/snapshots" / self.sid
        real_validate = engine.validate_repository

        def validate(path, **kwargs):
            self.assertFalse(destination.exists())
            return real_validate(path, **kwargs)
        with patch.object(engine, "validate_repository", side_effect=validate):
            self.app.publish(self.job)
        repo = destination / "repositories" / self.rid
        self.assertTrue((repo / "Packages/test.rpm").is_file())
        self.assertFalse((repo / "unindexed-secret.txt").exists())
        self.assertFalse((destination / "snapshot.json").exists())
        self.assertEqual((repo / "Packages/test.rpm").stat().st_mode & 0o777, 0o644)
        self.assertEqual(repo.stat().st_mode & 0o777, 0o755)
        self.assertFalse(list((self.public_root / ".staging").iterdir()))
        current = engine.read_json(self.app.root / "publication.json")
        self.assertEqual(current["snapshot_id"], self.sid)
        self.assertIn("published_at", current)
        self.assertIn("verified_at", current)
        self.assertLessEqual(current["verified_at"], current["published_at"])
        self.assertEqual((self.public_root / "public/client.repo").read_text(), current["client_config"])
        self.assertIn(["/usr/sbin/httpd", "-t"], [call.args[0] for call in self.run_command.call_args_list])

    def test_generated_client_config_is_pinned_and_keeps_rpm_trust_checks(self):
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(engine.client_repo_config("https://mirror.example.org:8443", self.snapshot))
        repo = config[f"dc-{self.rid}"]
        self.assertIn(f"/snapshots/{self.sid}/repositories/{self.rid}/", repo["baseurl"])
        self.assertEqual(repo["gpgcheck"], "1")
        self.assertEqual(repo["sslverify"], "1")
        self.assertEqual(repo["repo_gpgcheck"], "0")
        self.assertEqual(repo["gpgkey"], "file:///etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release")
        self.assertEqual(repo["skip_if_unavailable"], "0")

    def test_bad_urls_and_config_injection_are_rejected(self):
        for value in [None, "file:///etc", "http://user:pass@mirror.example.org", "http://mirror.example.org/path",
                      "http://mirror.example.org?x=1", "http://mirror.example.org#x", "http://mirror.example.org:0",
                      "http://mirror.example.org:99999", "http://mirror.example.org\n[gpgcheck=0]", "http://bad host", "http://$host"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                engine.public_origin(value)
        self.assertEqual(engine.public_origin("http://[::1]:8080/"), "http://[::1]:8080")

    def test_settings_are_persisted_without_changing_other_policy(self):
        self.app.save_publication_settings({"base_url": "https://mirror.example.org/"})
        self.assertEqual(engine.Engine().config["public_base_url"], "https://mirror.example.org")
        self.assertEqual(engine.Engine().config["role"], "disconnected")
        with self.assertRaisesRegex(ValueError, "only the public"):
            self.app.save_publication_settings({"base_url": "http://mirror.example.org", "role": "connected"})

    def test_settings_cannot_change_during_a_job(self):
        engine.write_json(self.app.job_path("c" * 32), {"status": "running"})
        with self.assertRaisesRegex(ValueError, "active job"):
            self.app.save_publication_settings({"base_url": "http://other.example.org"})

    def test_publish_requires_disconnected_role_and_publisher_package(self):
        self.app.config["role"] = "connected"
        with self.assertRaises(ValueError):
            self.app.publish(self.job)
        with self.assertRaises(ValueError):
            self.app.start("publish", self.sid)
        self.app.config["role"] = "disconnected"
        self.web_conf.unlink()
        with self.assertRaisesRegex(ValueError, "publisher package"):
            self.app.publish(self.job)

    def test_publish_rejects_incomplete_snapshots(self):
        self.snapshot["status"] = "partial"
        engine.write_json(self.source / "snapshot.json", self.snapshot)
        with self.assertRaisesRegex(ValueError, "validated snapshots"):
            self.app.publish(self.job)

    def test_failed_validation_preserves_previous_publication_and_cleans_staging(self):
        previous = {"snapshot_id": "c" * 32}
        engine.write_json(self.app.root / "publication.json", previous)
        with patch.object(engine, "validate_repository", side_effect=ValueError("RPM signature validation failed")):
            with self.assertRaisesRegex(ValueError, "signature validation"):
                self.app.publish(self.job)
        self.assertEqual(engine.read_json(self.app.root / "publication.json"), previous)
        self.assertFalse((self.public_root / "public/snapshots" / self.sid).exists())
        self.assertFalse(list((self.public_root / ".staging").iterdir()))

    def test_copy_tampering_is_detected_before_public_visibility(self):
        original_copy = engine.shutil.copyfile

        def changed(source, target):
            original_copy(source, target)
            if str(target).endswith("test.rpm"):
                Path(target).write_bytes(b"changed")
        with patch.object(engine.shutil, "copyfile", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "checksum"):
                self.app.publish(self.job)
        self.assertFalse((self.public_root / "public/snapshots" / self.sid).exists())

    def test_space_failure_does_not_publish(self):
        usage = type("DiskUsage", (), {"free": 0})()
        with patch.object(engine.shutil, "disk_usage", return_value=usage):
            with self.assertRaisesRegex(ValueError, "free space"):
                self.app.publish(self.job)
        self.assertFalse((self.app.root / "publication.json").exists())

    def test_server_failure_does_not_promote_new_content(self):
        self.run_command.side_effect = ValueError("Apache configuration failed")
        with self.assertRaisesRegex(ValueError, "Apache"):
            self.app.publish(self.job)
        self.assertFalse((self.public_root / "public/snapshots" / self.sid).exists())
        self.assertFalse((self.app.root / "publication.json").exists())

    def test_republication_does_not_replace_existing_snapshot(self):
        self.app.publish(self.job)
        path = self.public_root / "public/snapshots" / self.sid / "repositories" / self.rid / "Packages/test.rpm"
        inode = path.stat().st_ino
        self.job.update(id="d" * 32, public_base_url="http://new.example.org")
        self.app.publish(self.job)
        self.assertEqual(path.stat().st_ino, inode)
        self.assertIn("http://new.example.org/", (self.public_root / "public/client.repo").read_text())

    def test_queued_job_pins_the_public_origin(self):
        job = self.app.start("publish", self.sid)
        self.assertEqual(job["public_base_url"], "http://mirror.example.org")
        self.assertEqual(job["snapshot_id"], self.sid)

    def test_symlink_publication_storage_is_rejected(self):
        self.public_root.mkdir()
        (self.public_root / "public").symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.app.publish(self.job)


if __name__ == "__main__":
    unittest.main()

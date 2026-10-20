import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import os
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("engine", Path(__file__).parents[1] / "backend/disconnected_content.py")
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def fixture(root, updateinfo=True):
    root = Path(root)
    (root / "repodata").mkdir(parents=True)
    (root / "Packages").mkdir()
    rpm = b"A repository fixture, not an actual signed RPM."
    (root / "Packages/test.rpm").write_bytes(rpm)
    primary = f'''<metadata xmlns="http://linux.duke.edu/metadata/common" packages="1">
<package type="rpm"><name>example</name><checksum type="sha256">{hashlib.sha256(rpm).hexdigest()}</checksum>
<location href="Packages/test.rpm"/></package></metadata>'''
    compressed = gzip.compress(primary.encode())
    (root / "repodata/primary.xml.gz").write_bytes(compressed)
    records = f'''<data type="primary"><checksum type="sha256">{hashlib.sha256(compressed).hexdigest()}</checksum>
<location href="repodata/primary.xml.gz"/></data>'''
    if updateinfo:
        data = gzip.compress(b"<updates/>")
        (root / "repodata/updateinfo.xml.gz").write_bytes(data)
        records += f'''<data type="updateinfo"><checksum type="sha256">{hashlib.sha256(data).hexdigest()}</checksum>
<location href="repodata/updateinfo.xml.gz"/></data>'''
    (root / "repodata/repomd.xml").write_text(f'<repomd xmlns="http://linux.duke.edu/metadata/repo">{records}</repomd>')


class RepositoryValidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        fixture(self.root)

    def test_valid_repository_and_empty_advisories_are_accepted(self):
        result = engine.validate_repository(self.root, require_updateinfo=True, check_signatures=False)
        self.assertEqual(result["packages"], 1)
        self.assertIn("updateinfo", result["metadata_types"])

    def test_missing_package_is_rejected(self):
        (self.root / "Packages/test.rpm").unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            engine.validate_repository(self.root, check_signatures=False)

    def test_corrupted_package_is_rejected(self):
        (self.root / "Packages/test.rpm").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "checksum"):
            engine.validate_repository(self.root, check_signatures=False)

    def test_corrupted_metadata_is_rejected(self):
        (self.root / "repodata/primary.xml.gz").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Metadata checksum"):
            engine.validate_repository(self.root, check_signatures=False)

    def test_required_advisory_metadata_is_enforced(self):
        other = Path(self.temp.name) / "without-advisories"
        fixture(other, updateinfo=False)
        with self.assertRaisesRegex(ValueError, "advisory metadata"):
            engine.validate_repository(other, require_updateinfo=True, check_signatures=False)
        self.assertEqual(engine.validate_repository(other, check_signatures=False)["packages"], 1)

    def test_rpm_signature_failure_is_rejected(self):
        with patch.object(engine.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "NOKEY", "")):
            with self.assertRaisesRegex(ValueError, "signature validation"):
                engine.validate_repository(self.root)

    def test_unsigned_rpm_is_rejected_even_with_success_exit(self):
        with patch.object(engine.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "test.rpm: digests OK\n", "")):
            with self.assertRaisesRegex(ValueError, "signature validation"):
                engine.validate_repository(self.root)

    def test_filename_cannot_spoof_signature_success(self):
        with patch.object(engine.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "/packages/signatures OK.rpm: digests OK\n", "")):
            with self.assertRaisesRegex(ValueError, "signature validation"):
                engine.validate_repository(self.root)

    def test_unsafe_paths_and_symlinks_are_rejected(self):
        for name in ["../escape", "/absolute", "https://example.invalid/file", "a\\b"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                engine.safe_path(self.root, name)
        (self.root / "linked").symlink_to(self.root / "Packages")
        with self.assertRaisesRegex(ValueError, "Symlinks"):
            engine.safe_path(self.root, "linked/test.rpm")


class JobsAndExports(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = engine.Engine({"role": "connected", "state_dir": self.temp.name, "reserve_gib": 0})
        self.rid = "rhel-10-for-x86_64-supplementary-rpms"
        self.repo_patch = patch.object(engine, "repositories", return_value=[{"id": self.rid}])
        self.repo_patch.start()
        self.addCleanup(self.repo_patch.stop)
        self.app.save_profile({"repositories": [self.rid], "max_gib": 1})

    def test_unknown_repository_and_budget_are_rejected(self):
        for profile in [{"repositories": ["--bad"]}, {"repositories": [self.rid], "max_gib": -1},
                        {"repositories": [self.rid, self.rid]}, {"repositories": [self.rid], "max_gib": True}]:
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                self.app.save_profile(profile)

    def test_disconnected_role_cannot_start_connected_jobs(self):
        self.app.config["role"] = "disconnected"
        with self.assertRaisesRegex(ValueError, "does not permit"):
            self.app.start("sync")

    def test_only_one_worker_can_start_at_a_time(self):
        with patch.object(engine.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            job = self.app.start("preflight")
            self.assertEqual(job["status"], "queued")
            self.assertEqual(run.call_args.args[0][:3], ["/usr/bin/systemctl", "start", "--no-block"])
            with self.assertRaisesRegex(ValueError, "Another job"):
                self.app.start("sync")

    def test_cannot_export_an_incomplete_snapshot(self):
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.app.start("export", "a" * 32)
        with self.assertRaisesRegex(ValueError, "identifier"):
            self.app.job_path("../escape")

    def test_service_start_failure_is_persisted(self):
        with patch.object(engine.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "unavailable")):
            with self.assertRaisesRegex(ValueError, "Cannot start"):
                self.app.start("preflight")
        records = list((self.app.root / "jobs").glob("*.json"))
        self.assertEqual(len(records), 1)
        self.assertEqual(engine.read_json(records[0])["status"], "failed")

    def test_stale_running_job_becomes_interrupted_when_service_is_gone(self):
        jid = "e" * 32
        path = self.app.job_path(jid)
        engine.write_json(path, {"id": jid, "status": "running", "created": engine.now()})
        os.utime(path, (time.time() - 60, time.time() - 60))
        with patch.object(engine.subprocess, "run", return_value=subprocess.CompletedProcess([], 3)):
            self.assertEqual(self.app.jobs()[0]["status"], "interrupted")

    def test_completed_worker_cannot_be_replayed(self):
        jid = "f" * 32
        engine.write_json(self.app.job_path(jid), {"id": jid, "status": "succeeded"})
        with self.assertRaisesRegex(ValueError, "queued jobs only"):
            self.app.run(jid)

    def test_over_budget_sync_stops_before_downloading(self):
        job = {"id": "a" * 32, "profile": self.app.profile()}
        with patch.object(self.app, "estimate", return_value={"within_budget": False, "enough_space": True}), \
                patch.object(self.app, "run_command") as run:
            with self.assertRaisesRegex(ValueError, "no RPM sync started"):
                self.app.sync(job)
            run.assert_not_called()

    def test_worker_failure_is_recorded_without_creating_valid_snapshot(self):
        jid = "b" * 32
        job = {"id": jid, "operation": "sync", "status": "queued", "profile": self.app.profile()}
        engine.write_json(self.app.job_path(jid), job)
        with patch.object(self.app, "estimate", side_effect=ValueError("preflight unavailable")):
            with self.assertRaises(ValueError):
                self.app.run(jid)
        self.assertEqual(engine.read_json(self.app.job_path(jid))["status"], "failed")
        self.assertFalse(list((self.app.root / "snapshots").glob("*/snapshot.json")))

    def test_export_round_trip_and_corruption_detection(self):
        sid, jid = "c" * 32, "d" * 32
        snapshot_dir = self.app.root / "snapshots" / sid
        fixture(snapshot_dir / "repositories" / self.rid)
        engine.write_json(snapshot_dir / "snapshot.json", {"id": sid, "os": {"major": "10", "arch": "x86_64"},
            "repositories": {self.rid: {}}, "status": "validated"})
        # Runtime-only data must never be included in transfer media.
        (self.app.root / "local-credential").write_text("local-only")
        job = {"id": jid, "snapshot_id": sid}
        with patch.object(engine, "validate_repository", wraps=lambda *a, **kw: {}), patch.object(engine, "configured_signing_key_sources", return_value={}):
            self.app.export(job)
        path = Path(job["result"]["path"])
        result = engine.verify_archive(path)
        self.assertFalse(result["authenticated"])
        self.assertEqual(result["files_verified"], 4)
        with tarfile.open(path) as archive:
            self.assertNotIn("local-credential", archive.getnames())
            for member in archive:
                self.assertEqual(member.uid, 0)
                self.assertEqual(member.uname, "")
        data = path.read_bytes()
        path.write_bytes(data.replace(b"A repository fixture", b"X repository fixture"))
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            engine.verify_archive(path)

    def test_archive_path_traversal_is_rejected(self):
        dest = Path(self.temp.name) / "bad.tar"
        manifest = {"format": "disconnected-content/v1", "snapshot_id": "a" * 32,
                    "files": {"../escape": {"size": 1, "sha256": hashlib.sha256(b"x").hexdigest()}}}
        with tarfile.open(dest, "w") as archive:
            for name, raw in [("manifest.json", json.dumps(manifest).encode()), ("../escape", b"x")]:
                entry = tarfile.TarInfo(name); entry.size = len(raw)
                archive.addfile(entry, io.BytesIO(raw))
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            engine.verify_archive(dest)


if __name__ == "__main__":
    unittest.main()

import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from test_engine import engine


class RoleSetup(unittest.TestCase):
    """First-run role selection and the disconnected unsigned-import policy."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config_path = self.root / "config.json"
        engine.write_json(self.config_path, {"state_dir": str(self.root / "state"), "reserve_gib": 0, "max_import_gib": 7})
        for name, value in {"CONFIG": self.config_path, "PUBLISH_CONF": self.root / "missing-publisher.conf"}.items():
            patcher = patch.object(engine, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = engine.Engine()

    def saved(self):
        return engine.read_json(self.config_path)

    def backups(self):
        return sorted(self.root.glob("config.json.bak-*"))

    def status(self):
        with patch.object(engine, "host_os", return_value={"name": "RHEL 10", "major": "10", "arch": "x86_64"}), \
                patch.object(engine, "repositories", return_value=[]):
            return self.app.status()

    def test_choosing_a_role_keeps_other_settings_and_backs_up_the_old_file(self):
        before = self.config_path.read_bytes()
        result = self.app.setup({"role": "disconnected"})
        saved = self.saved()
        self.assertEqual(saved["role"], "disconnected")
        self.assertIs(saved["allow_unsigned_imports"], False)
        self.assertEqual(saved["max_import_gib"], 7)
        self.assertIn("configured_at", saved)
        self.assertEqual(oct(self.config_path.stat().st_mode & 0o777), "0o644")
        self.assertEqual([p.read_bytes() for p in self.backups()], [before])
        self.assertEqual(result["backup"], str(self.backups()[0]))
        self.assertEqual(self.app.config["role"], "disconnected")

    def test_connected_setup_does_not_add_an_import_policy(self):
        self.app.setup({"role": "connected"})
        self.assertEqual(self.saved()["role"], "connected")
        self.assertNotIn("allow_unsigned_imports", self.saved())

    def test_disconnected_setup_can_allow_unsigned_imports(self):
        self.app.setup({"role": "disconnected", "allow_unsigned_imports": True})
        self.assertIs(self.saved()["allow_unsigned_imports"], True)
        self.assertTrue(self.status()["capabilities"]["allow_unsigned_imports"])

    def test_invalid_requests_leave_the_config_untouched(self):
        before = self.config_path.read_bytes()
        for request in [None, [], {}, {"role": "both"}, {"role": None}, {"role": "connected", "state_dir": "/tmp"},
                        {"role": "connected", "allow_unsigned_imports": True},
                        {"role": "disconnected", "allow_unsigned_imports": "yes"},
                        {"role": "disconnected", "allow_unsigned_imports": 1}]:
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.app.setup(request)
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(self.backups(), [])

    def test_role_is_chosen_once(self):
        self.app.setup({"role": "connected"})
        before = self.config_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "already set up as connected"):
            self.app.setup({"role": "disconnected"})
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_confirming_the_same_role_changes_nothing(self):
        self.app.setup({"role": "disconnected"})
        before, count = self.config_path.read_bytes(), len(self.backups())
        self.assertFalse(self.app.setup({"role": "disconnected"})["changed"])
        self.assertFalse(self.app.setup({"role": "disconnected", "allow_unsigned_imports": False})["changed"])
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(len(self.backups()), count)

    def test_import_policy_can_be_turned_on_and_off_later(self):
        self.app.setup({"role": "disconnected"})
        self.assertTrue(self.app.setup({"role": "disconnected", "allow_unsigned_imports": True})["allow_unsigned_imports"])
        self.assertIs(self.saved()["allow_unsigned_imports"], True)
        self.assertFalse(self.app.setup({"role": "disconnected", "allow_unsigned_imports": False})["allow_unsigned_imports"])
        self.assertIs(self.saved()["allow_unsigned_imports"], False)
        self.assertEqual(len(self.backups()), 3)

    def test_setup_waits_for_an_active_job(self):
        engine.write_json(self.app.job_path("d" * 32), {"id": "d" * 32, "status": "running", "created": engine.now()})
        before = self.config_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "active job"):
            self.app.setup({"role": "connected"})
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_a_job_left_behind_by_a_crash_does_not_block_setup(self):
        path = self.app.job_path("e" * 32)
        engine.write_json(path, {"id": "e" * 32, "status": "running", "created": engine.now()})
        old = time.time() - 60
        os.utime(path, (old, old))
        stopped = subprocess.CompletedProcess([], 3)
        with patch.object(engine.subprocess, "run", return_value=stopped):
            self.app.setup({"role": "connected"})
        self.assertEqual(engine.read_json(path)["status"], "interrupted")
        self.assertEqual(self.saved()["role"], "connected")

    def test_status_before_setup(self):
        status = self.status()
        self.assertIsNone(status["role"])
        self.assertIsNone(status["setup_hint"])
        self.assertIsNone(status["connected"])
        self.assertIsNone(status["disconnected"])
        self.assertFalse(status["capabilities"]["publisher_installed"])

    def test_status_reports_the_publisher_package(self):
        conf = self.root / "publisher.conf"
        conf.write_text("# publisher\n")
        with patch.object(engine, "PUBLISH_CONF", conf):
            self.assertTrue(self.status()["capabilities"]["publisher_installed"])

    def test_hint_for_a_host_that_already_has_connected_content(self):
        engine.write_json(self.app.root / "export-records" / ("a" * 32 + ".json"), {"snapshot_id": "a" * 32})
        self.assertEqual(self.status()["setup_hint"], "connected")

    def test_hint_for_a_host_that_already_has_disconnected_content(self):
        engine.write_json(self.app.root / "export-records" / ("a" * 32 + ".json"), {"snapshot_id": "a" * 32})
        engine.write_json(self.app.root / "publication.json", {"snapshot_id": "b" * 32})
        self.assertEqual(self.status()["setup_hint"], "disconnected")

    def test_no_hint_once_a_role_is_set(self):
        engine.write_json(self.app.root / "export-records" / ("a" * 32 + ".json"), {"snapshot_id": "a" * 32})
        self.app.setup({"role": "connected"})
        self.assertIsNone(self.status()["setup_hint"])


if __name__ == "__main__":
    unittest.main()

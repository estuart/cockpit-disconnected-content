"""Pinned automation generation and download authorization boundaries."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from copy import deepcopy
from test_engine import engine


def publication():
    record = {"snapshot_id": "a" * 32, "job_id": "b" * 32,
              "published_at": "2026-01-01T00:00:00Z", "base_url": "https://mirror.example.org",
              "os": {"major": "10", "arch": "x86_64"},
              "repositories": {"rhel-baseos": {"metadata_types": ["primary", "updateinfo"]},
                               "rhel-appstream": {"metadata_types": ["primary", "updateinfo"]}}}
    record["client_config"] = engine.client_repo_config(record["base_url"],
        {"id": record["snapshot_id"], "repositories": record["repositories"]})
    return record


class Automation(unittest.TestCase):
    def test_downloads_are_complete_and_bound_to_one_publication(self):
        record = publication()
        files = engine.client_automation(record)["files"]
        self.assertEqual(set(files), {"configure-repos.yml", "security-updates.yml", "inventory.ini", "README-client-automation.txt"})
        self.assertIn(hashlib.sha256(record["client_config"].encode()).hexdigest(), files["security-updates.yml"])
        self.assertIn("/snapshots/" + record["snapshot_id"], files["configure-repos.yml"])
        self.assertNotIn("/client.repo", files["configure-repos.yml"])
        self.assertIn("--check --diff", files["README-client-automation.txt"])
        self.assertIn("[disconnected_clients]", files["inventory.ini"])

    def test_missing_advisories_do_not_offer_a_silent_security_noop(self):
        record = publication()
        record["repositories"]["rhel-baseos"]["metadata_types"] = ["primary"]
        result = engine.client_automation(record)
        self.assertFalse(result["has_updateinfo"])
        self.assertIn('"that":\n          - false', result["files"]["security-updates.yml"])

    def test_rejects_mismatched_or_injected_publication_values(self):
        base = publication()
        mutations = [{"client_config": base["client_config"] + "gpgcheck=0\n"},
                     {"base_url": "http://{{lookup('pipe','id')}}"}, {"snapshot_id": "../jobs"},
                     {"os": {"major": "10' or true", "arch": "x86_64"}},
                     {"os": {"major": "10", "arch": "{{lookup}}"}},
                     {"repositories": {"repo*": {}}}, {"repositories": {}}]
        for changes in mutations:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                engine.client_automation(dict(base, **changes))

    def test_download_uses_requested_history_even_when_latest_changes(self):
        with tempfile.TemporaryDirectory() as root:
            app = engine.Engine({"role": "disconnected", "state_dir": root})
            first = publication()
            engine.write_json(Path(root) / "publications" / (first["job_id"] + ".json"), first)
            latest = deepcopy(first)
            latest["snapshot_id"] = "c" * 32
            engine.write_json(Path(root) / "publication.json", latest)
            self.assertEqual(app.client_automation(first["job_id"])["snapshot_id"], first["snapshot_id"])
            for invalid in [None, "../publication", "x" * 32, "c" * 32]:
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    app.client_automation(invalid)
            app.config["role"] = "connected"
            with self.assertRaisesRegex(ValueError, "disconnected"):
                app.client_automation(first["job_id"])


if __name__ == "__main__":
    unittest.main()

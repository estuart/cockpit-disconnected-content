"""Behavior that has to hold on RHEL 8, 9, and 10 alike."""
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from test_automation import publication
from test_engine import engine

# Captured from `dnf -q config-manager --dump dc-test` on EL 8 (dnf 4.7), EL 9 (dnf 4.14),
# and EL 10 (dnf 4.20); all three print the same line, with variables already expanded.
DUMP = """================================ repo: dc-test =================================
async = 1
baseurl = https://example.invalid/10/x86_64/
enabled = 0
gpgcheck = 1
gpgkey = file:///etc/pki/rpm-gpg/KEY-ONE-10, https://example.invalid/keys/KEY-TWO-x86_64.asc
name = test
repo_gpgcheck = 0
"""


class SigningKeySourcesWithoutDnfBindings(unittest.TestCase):
    def test_dump_lists_every_configured_key(self):
        self.assertEqual(engine.dumped_gpgkeys(DUMP),
                         ["file:///etc/pki/rpm-gpg/KEY-ONE-10", "https://example.invalid/keys/KEY-TWO-x86_64.asc"])

    def test_repository_without_keys(self):
        self.assertEqual(engine.dumped_gpgkeys(DUMP.replace("gpgkey = file:///etc/pki/rpm-gpg/KEY-ONE-10, "
                                                            "https://example.invalid/keys/KEY-TWO-x86_64.asc\n", "")), [])
        self.assertEqual(engine.dumped_gpgkeys("gpgkey = \n"), [])

    def test_other_settings_are_not_mistaken_for_keys(self):
        self.assertEqual(engine.dumped_gpgkeys("repo_gpgcheck = 1\ngpgkey_extra = nope\ngpgkey = file:///k\n"), ["file:///k"])

    def test_each_repository_is_read_separately_and_unknown_ones_are_skipped(self):
        def run(args, **kwargs):
            self.assertEqual(args[:4], ["/usr/bin/dnf", "-q", "config-manager", "--dump"])
            self.assertEqual(kwargs["env"]["LC_ALL"], "C")
            if args[4] == "missing":
                return subprocess.CompletedProcess(args, 1, "", "Error: No matching repo to modify: missing.\n")
            return subprocess.CompletedProcess(args, 0, DUMP.replace("dc-test", args[4]), "")
        with patch.object(engine.subprocess, "run", side_effect=run) as called:
            sources = engine.configured_signing_key_sources(["baseos", "missing"])
        self.assertEqual(called.call_count, 2)
        self.assertEqual(list(sources), ["baseos"])
        self.assertEqual(len(sources["baseos"]), 2)

    def test_helper_does_not_import_distribution_python_bindings(self):
        source = (Path(__file__).parents[1] / "backend/disconnected_content.py").read_text()
        for module in ["dnf", "rpm", "libdnf", "gpg", "selinux"]:
            self.assertNotRegex(source, rf"^\s*(import|from) {module}\b", module)


class Rhel8ClientPlaybooks(unittest.TestCase):
    def files(self, major):
        record = publication()
        record["os"] = {**record["os"], "major": major}
        return engine.client_automation(record)["files"]

    def test_rhel8_playbooks_check_the_control_node_before_gathering_facts(self):
        for name in ["configure-repos.yml", "security-updates.yml"]:
            text = self.files("8")[name]
            first, main = text.index("Check the control node for RHEL 8 clients"), text.index("snapshot " + "a" * 32)
            self.assertLess(first, main, name)
            self.assertIn("version('2.17', '<')", text)
            self.assertIn("Ansible Core 2.16", text)

    def test_other_releases_keep_a_single_play(self):
        for major in ["9", "10"]:
            for name, text in self.files(major).items():
                self.assertNotIn("Check the control node", text, (major, name))


if __name__ == "__main__":
    unittest.main()

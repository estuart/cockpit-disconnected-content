"""Offline installers built on the connected host for a disconnected host of the same release."""
import hashlib
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from test_engine import engine

RELEASE = engine.VERSION + "-1.el10"
MAIN = f"cockpit-disconnected-content-{RELEASE}.noarch.rpm"
PUBLISHER = f"cockpit-disconnected-content-publisher-{RELEASE}.noarch.rpm"


class OfflineInstaller(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.transfer = self.root / "transfer"
        self.transfer.mkdir()
        config = self.root / "config.json"
        engine.write_json(config, {"role": "connected", "state_dir": str(self.root / "state"), "reserve_gib": 0,
                                   "export_dir": str(self.transfer)})
        createrepo = self.root / "createrepo_c"
        createrepo.touch()
        self.signatures = []
        for target, name, value in [(engine, "CONFIG", config), (engine, "CREATEREPO", str(createrepo)),
                              (engine, "host_os", lambda: {"name": "RHEL 10", "major": "10", "arch": "x86_64"}),
                              (engine.Engine, "installed_release", staticmethod(lambda: RELEASE)),
                              (engine.Engine, "host_releasever", staticmethod(lambda major: major)),
                              (engine.Engine, "run_command", self.fake_command),
                              (engine, "check_rpm_signatures", lambda paths, keys=None: self.signatures.append(paths)),
                              (engine.subprocess, "run", self.fake_rpm_query)]:
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = engine.Engine()
        self.commands = []

    def fake_command(self, args, job, stage):
        self.commands.append(args)
        if args[0].endswith("dnf"):
            deps = Path(next(arg for arg in args if arg.startswith("--downloaddir=")).split("=", 1)[1])
            for name in ["python3-3.12-1.el10.x86_64.rpm", "cockpit-bridge-370-1.el10.x86_64.rpm"]:
                (deps / name).write_bytes(name.encode())
        else:
            (Path(args[-1]) / "repodata").mkdir()
            (Path(args[-1]) / "repodata" / "repomd.xml").write_text("<repomd/>")

    @staticmethod
    def fake_rpm_query(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, Path(args[-1]).name, "")

    def stage_local_rpms(self):
        for name in [MAIN, PUBLISHER]:
            (self.transfer / name).write_bytes(name.encode())

    def build(self):
        job = {"id": "a" * 32, "operation": "installer", "status": "running", "created": engine.now()}
        engine.write_json(self.app.job_path(job["id"]), job)
        self.app.build_installer(job)
        return job

    def test_installer_bundles_the_plugin_dependencies_and_a_checked_install_script(self):
        self.stage_local_rpms()
        job = self.build()
        result = job["result"]
        archive = Path(result["path"])
        self.assertEqual(archive.name, f"disconnected-content-installer-{RELEASE}-x86_64.tar.gz")
        self.assertEqual(Path(result["checksum_path"]).read_text(), f"{engine.digest(archive)}  {archive.name}\n")
        self.assertEqual(result["sha256"], engine.digest(archive))
        self.assertEqual((result["release"], result["target"], result["dependencies"]), (RELEASE, {"major": "10", "arch": "x86_64"}, 2))
        self.assertIn("transfer folder", result["source"])
        top = archive.name[:-len(".tar.gz")]
        with tarfile.open(archive) as bundle:
            members = {member.name: member for member in bundle.getmembers()}
            names = {name[len(top) + 1:] for name in members}
            self.assertEqual(names, {"install.sh", "README.txt", "SHA256SUMS", f"plugin/{MAIN}", f"plugin/{PUBLISHER}",
                                     "deps/python3-3.12-1.el10.x86_64.rpm", "deps/cockpit-bridge-370-1.el10.x86_64.rpm",
                                     "deps/repodata/repomd.xml"})
            self.assertEqual(members[f"{top}/install.sh"].mode, 0o755)
            self.assertTrue(all(m.uid == 0 and m.gid == 0 and m.isfile() for m in members.values()))
            script = bundle.extractfile(f"{top}/install.sh").read().decode()
            sums = bundle.extractfile(f"{top}/SHA256SUMS").read().decode()
            listed = dict(reversed(line.split("  ", 1)) for line in sums.splitlines())
            for name in names - {"SHA256SUMS"}:
                self.assertEqual(listed[name], hashlib.sha256(bundle.extractfile(f"{top}/{name}").read()).hexdigest(), name)
        self.assertNotRegex(script, r"@[A-Z]+@")
        self.assertIn('for arg in "$@"; do', script)
        self.assertIn('"$major" != "10"', script)
        self.assertIn(f'packages=("$PWD/plugin/{MAIN}")', script)
        self.assertIn("--setopt=dc-installer.gpgcheck=1", script)
        self.assertIn("--setopt=dc-installer.module_hotfixes=1", script)
        self.assertIn("sha256sum --quiet --strict -c SHA256SUMS", script)
        dnf = self.commands[0]
        self.assertIn("--downloadonly", dnf)
        self.assertTrue(any(arg.startswith("--installroot=") for arg in dnf))
        self.assertIn("--releasever=10", dnf)
        self.assertEqual(len(self.signatures[0]), 2)
        self.assertEqual(list((self.app.root / "installers").iterdir()), [])
        self.assertEqual(self.app.installer_summary()["state"], "available")

    def test_installer_name_is_never_mistaken_for_a_content_bundle(self):
        self.assertIsNone(engine.BUNDLE_NAME.fullmatch(f"disconnected-content-installer-{RELEASE}-x86_64.tar.gz"))
        self.assertIsNone(engine.BUNDLE_NAME.fullmatch(f"disconnected-content-installer-{RELEASE}-x86_64.tar.gz.sha256"))

    def test_release_download_must_match_the_published_checksums(self):
        def fetch(url, dest, limit):
            name = url.rsplit("/", 1)[1]
            content = f"{'0' * 64}  {MAIN}\n{'1' * 64}  {PUBLISHER}\n" if name == "SHA256SUMS" else name
            Path(dest).write_bytes(content.encode())
            return hashlib.sha256(content.encode()).hexdigest()
        with patch.object(engine, "fetch_https", side_effect=fetch) as fetched, self.assertRaisesRegex(ValueError, "doesn't match the checksum"):
            self.build()
        self.assertTrue(fetched.call_args_list[0].args[0].endswith(f"/v{engine.VERSION}/SHA256SUMS"))
        self.assertTrue(fetched.call_args_list[0].args[0].startswith(engine.RELEASE_DOWNLOADS))
        self.assertEqual(list(self.transfer.iterdir()), [])
        self.assertEqual(list((self.app.root / "installers").iterdir()), [])

    def test_release_download_that_matches_is_used(self):
        contents = {MAIN: b"main", PUBLISHER: b"publisher"}
        sums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in contents.items())
        def fetch(url, dest, limit):
            name = url.rsplit("/", 1)[1]
            data = sums.encode() if name == "SHA256SUMS" else contents[name]
            Path(dest).write_bytes(data)
            return hashlib.sha256(data).hexdigest()
        with patch.object(engine, "fetch_https", side_effect=fetch):
            job = self.build()
        self.assertEqual(job["result"]["source"], f"GitHub release v{engine.VERSION}")

    def test_unreachable_github_explains_the_transfer_folder_fallback(self):
        with patch.object(engine, "fetch_https", side_effect=OSError("Network is unreachable")), \
                self.assertRaisesRegex(ValueError, f"Copy {MAIN} and {PUBLISHER} into {self.transfer}"):
            self.build()

    def test_package_whose_header_disagrees_with_its_name_is_rejected(self):
        self.stage_local_rpms()
        with patch.object(engine.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "something-else.rpm", "")), \
                self.assertRaisesRegex(ValueError, "isn't the package its name says"):
            self.build()

    def test_failure_while_archiving_leaves_no_partial_files(self):
        self.stage_local_rpms()
        with patch.object(engine.tarfile, "open", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.build()
        self.assertEqual(sorted(p.name for p in self.transfer.iterdir()), sorted([MAIN, PUBLISHER]))
        self.assertEqual(list((self.app.root / "installers").iterdir()), [])
        self.assertIsNone(self.app.installer_summary())

    def test_missing_createrepo_is_explained(self):
        self.stage_local_rpms()
        with patch.object(engine, "CREATEREPO", str(self.root / "absent")), self.assertRaisesRegex(ValueError, "createrepo_c"):
            self.build()

    def test_summary_notices_a_moved_or_changed_installer(self):
        self.stage_local_rpms()
        archive = Path(self.build()["result"]["path"])
        with archive.open("ab") as stream:
            stream.write(b"!")
        os.utime(archive, ns=(1, 1))
        self.assertEqual(self.app.installer_summary()["state"], "changed")
        archive.unlink()
        self.assertEqual(self.app.installer_summary()["state"], "missing")

    def test_only_connected_hosts_build_installers(self):
        self.app.config["role"] = "disconnected"
        with self.assertRaisesRegex(ValueError, "connected operations"):
            self.app.start("installer")


class InstalledRelease(unittest.TestCase):
    def test_release_must_match_this_helper(self):
        for output, code, message in [("", 1, "Install this extension from its RPM"),
                                      ("0.0.1-1.el9", 0, "doesn't match this helper")]:
            with self.subTest(output=output), patch.object(engine.subprocess, "run", return_value=subprocess.CompletedProcess([], code, output, "")), \
                    self.assertRaisesRegex(ValueError, message):
                engine.Engine.installed_release()
        with patch.object(engine.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, RELEASE, "")):
            self.assertEqual(engine.Engine.installed_release(), RELEASE)

    def test_releasever_comes_from_dnf_with_the_major_version_as_fallback(self):
        with patch.object(engine.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "arch = x86_64\nreleasever = 9.4\n", "")):
            self.assertEqual(engine.Engine.host_releasever("9"), "9.4")
        with patch.object(engine.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "error")):
            self.assertEqual(engine.Engine.host_releasever("9"), "9")


class Sha256Sums(unittest.TestCase):
    def test_parses_both_sha256sum_formats_and_ignores_junk(self):
        text = f"{'a' * 64}  one.rpm\n{'b' * 64} *two.rpm\nnot a line\n{'c' * 63}  short.rpm\n"
        self.assertEqual(engine.parse_sha256sums(text), {"one.rpm": "a" * 64, "two.rpm": "b" * 64})


if __name__ == "__main__":
    unittest.main()

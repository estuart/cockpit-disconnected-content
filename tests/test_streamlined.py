"""Combined sync/export jobs, manifest summaries, and key prompts limited to untrusted keys."""
from copy import deepcopy
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_bundled_keys
import test_dashboard
from test_bundled_keys import inspected_key
from test_engine import engine, fixture


class CombinedJob(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = engine.Engine({'role': 'connected', 'state_dir': self.temp.name, 'reserve_gib': 0})
        self.rid = 'example-baseos'
        patcher = patch.object(engine, 'repositories', return_value=[{'id': self.rid}])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app.save_profile({'repositories': [self.rid], 'max_gib': 1})

    def test_sync_export_is_a_connected_operation(self):
        with patch.object(engine.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            self.assertEqual(self.app.start('sync-export')['operation'], 'sync-export')
        self.app.config['role'] = 'disconnected'
        with self.assertRaisesRegex(ValueError, 'does not permit'):
            self.app.start('sync-export')

    def test_worker_syncs_then_exports_the_new_snapshot(self):
        jid = 'b' * 32
        engine.write_json(self.app.job_path(jid), {'id': jid, 'operation': 'sync-export', 'status': 'queued',
                                                   'profile': self.app.profile(), 'created': engine.now()})

        def sync(job):
            fixture(self.app.root / 'snapshots' / jid / 'repositories' / self.rid)
            engine.write_json(self.app.root / 'snapshots' / jid / 'snapshot.json', {'id': jid, 'status': 'validated',
                'created': engine.now(), 'package_bytes': 1,
                'os': {'major': '10', 'arch': 'x86_64'}, 'repositories': {self.rid: {}}})
            self.app.update(job, snapshot_id=jid, result={'snapshot_id': jid, 'repositories': {self.rid: {}}})

        with patch.object(self.app, 'sync', side_effect=sync), \
                patch.object(engine, 'validate_repository', return_value={}), \
                patch.object(engine, 'configured_signing_key_sources', return_value={}):
            self.app.run(jid)
        job = engine.read_json(self.app.job_path(jid))
        self.assertEqual((job['status'], job['step']), ('succeeded', 'verify'))
        self.assertEqual(job['result']['repositories'], {self.rid: {}})
        self.assertTrue(job['result']['path'].endswith(jid + '.tar'))
        self.assertEqual(self.app.connected_status([engine.read_json(self.app.root / 'snapshots' / jid / 'snapshot.json')],
                                                   self.app.jobs())['phase'], 'ready')

    def test_failed_sync_retries_the_combined_job(self):
        failure = {'id': 'd' * 32, 'operation': 'sync-export', 'status': 'failed', 'stage': 'Download failed',
                   'created': '2026-01-02T10:00:00+00:00', 'profile': self.app.profile()}
        result = self.app.connected_status([], [failure])
        self.assertEqual((result['phase'], result['action']), ('failed', 'sync-export'))

    def test_failed_bundle_after_successful_sync_retries_only_the_export(self):
        sid = 'a' * 32
        snapshot = {'id': sid, 'created': '2026-01-02T10:00:00+00:00', 'status': 'validated',
                    'os': {'major': '10', 'arch': 'x86_64'}, 'package_bytes': 1, 'repositories': {self.rid: {}}}
        failure = {'id': sid, 'operation': 'sync-export', 'status': 'failed', 'snapshot_id': sid,
                   'stage': 'Not enough free space', 'created': '2026-01-02T10:00:00+00:00', 'profile': self.app.profile()}
        result = self.app.connected_status([deepcopy(snapshot)], [failure])
        self.assertEqual((result['phase'], result['action']), ('failed', 'export'))


class ClearFailures(unittest.TestCase):
    def test_unsigned_packages_are_named(self):
        log = 'Removing example-agent-1.4.0.x86_64.rpm: Package example-agent-1.4.0.x86_64.rpm is not signed\nError: GPG signature check failed.'
        self.assertIn('example-agent-1.4.0.x86_64.rpm', engine.explain_reposync_failure(log))
        self.assertIn('not signed', engine.explain_reposync_failure(log))

    def test_missing_key_and_unknown_failures(self):
        self.assertIn('signing key', engine.explain_reposync_failure('Public key for demo.rpm is not installed'))
        self.assertIsNone(engine.explain_reposync_failure('Curl error (6): Could not resolve host'))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = engine.Engine({'role': 'connected', 'state_dir': self.temp.name, 'reserve_gib': 0})
        self.job = {'id': 'a' * 32}
        engine.write_json(self.app.job_path(self.job['id']), self.job)

    def estimate(self, stdout, repositories):
        with patch.object(engine.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout, '')):
            return self.app.estimate({'repositories': repositories, 'max_gib': 1}, self.job)

    def test_an_empty_repository_is_named(self):
        with self.assertRaisesRegex(ValueError, 'No packages to mirror in empty-repo'):
            self.estimate('full-repo 100\n', ['full-repo', 'empty-repo'])
        self.assertEqual(self.estimate('full-repo 100\nfull-repo 50\n', ['full-repo'])['package_bytes'], 150)

    def test_host_package_filters_do_not_shrink_the_mirror(self):
        with patch.object(engine.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'full-repo 100\n', '')) as run:
            self.app.estimate({'repositories': ['full-repo'], 'max_gib': 1}, self.job)
        self.assertIn('--disableexcludes=all', run.call_args.args[0])
        plan = {'within_budget': True, 'enough_space': True, 'package_bytes': 1}
        with patch.object(self.app, 'estimate', return_value=plan), patch.object(self.app, 'run_command', side_effect=ValueError('stop')) as command:
            with self.assertRaises(ValueError):
                self.app.sync({'id': self.job['id'], 'profile': {'repositories': ['full-repo'], 'max_gib': 1}})
        self.assertIn('--disableexcludes=all', command.call_args.args[0])

    def test_over_limit_message_gives_sizes(self):
        plan = {'within_budget': False, 'enough_space': True, 'package_bytes': 3 * engine.GIB}
        with patch.object(self.app, 'estimate', return_value=plan), self.assertRaisesRegex(ValueError, '3.0 GiB of packages, over the 1 GiB'):
            self.app.sync({'id': self.job['id'], 'profile': {'repositories': ['x'], 'max_gib': 1}})


class ExportFolder(unittest.TestCase):
    # Reuse the connected dashboard fixture without rerunning its tests.
    fixture = test_dashboard.Dashboard
    setUp, summary, create_real_export = fixture.setUp, fixture.summary, fixture.create_real_export

    def use_config(self):
        self.config_path = self.app.root.parent / (self.app.root.name + '-config.json')
        engine.write_json(self.config_path, self.app.config)
        patcher = patch.object(engine, 'CONFIG', self.config_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.media = Path(tempfile.mkdtemp(prefix='dc-export-media-')).resolve()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.media, ignore_errors=True))

    def test_export_writes_to_the_saved_folder_and_records_it(self):
        self.use_config()
        result = self.app.save_export_settings({'export_dir': str(self.media)})
        self.assertEqual(result, {'path': str(self.media), 'default': False, 'updated_at': result['updated_at']})
        self.assertEqual(engine.read_json(self.config_path)['export_dir'], str(self.media))
        self.create_real_export()
        archive = self.media / (self.sid + '.tar')
        self.assertTrue(archive.is_file() and archive.with_suffix('.tar.sha256').is_file())
        self.assertFalse((self.app.root / 'exports' / archive.name).exists())
        bundle = self.summary()['latest_export']
        self.assertEqual((bundle['path'], bundle['state']), (str(archive), 'available'))
        # Verification follows the recorded path even after the setting changes back.
        self.app.save_export_settings({'export_dir': ''})
        self.app.verify_export({'id': 'c' * 32, 'snapshot_id': self.sid, 'status': 'running', 'operation': 'verify-export', 'created': engine.now()})
        self.assertEqual(self.summary()['latest_export']['path'], str(archive))
        self.assertNotIn('export_dir', engine.read_json(self.config_path))

    def test_unmounted_folder_is_reported_and_blocks_export(self):
        self.use_config()
        self.app.save_export_settings({'export_dir': str(self.media)})
        self.media.rmdir()
        settings = self.app.export_settings()
        self.assertFalse(settings['available'])
        self.assertIn('unavailable', settings['error'])
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.create_real_export()

    def test_unsafe_folders_and_wrong_role_are_rejected(self):
        self.use_config()
        alias = self.media.parent / (self.media.name + '-alias')
        alias.symlink_to(self.media, target_is_directory=True)
        self.addCleanup(alias.unlink)
        for value in ['relative', str(alias), str(self.app.root / 'snapshots'), str(self.media / 'missing')]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.app.save_export_settings({'export_dir': value})
        with self.assertRaises(ValueError):
            self.app.save_export_settings({'export_dir': str(self.media), 'extra': 1})
        engine.write_json(self.app.job_path('d' * 32), {'status': 'running'})
        with self.assertRaisesRegex(ValueError, 'active job'):
            self.app.save_export_settings({'export_dir': str(self.media)})
        self.app.config['role'] = 'disconnected'
        with self.assertRaisesRegex(ValueError, 'connected installation role'):
            self.app.save_export_settings({'export_dir': str(self.media)})

    def test_records_without_a_path_use_private_state(self):
        record = {'snapshot_id': self.sid, 'path': '/elsewhere/../escape/' + self.sid + '.tar'}
        self.assertEqual(self.app.export_archive_path(record), self.app.root / 'exports' / (self.sid + '.tar'))
        self.assertEqual(self.app.export_archive_path({'snapshot_id': self.sid}), self.app.root / 'exports' / (self.sid + '.tar'))


class StreamlinedImport(unittest.TestCase):
    # Reuse the bundled-key export fixture without rerunning its tests.
    fixture = test_bundled_keys.BundledKeys
    setUp, export, sidecar = fixture.setUp, fixture.export, fixture.sidecar

    def test_bundle_info_summarizes_the_manifest_without_trust(self):
        with patch.object(engine, 'host_os', return_value={'major': '10', 'arch': 'x86_64'}):
            info = self.target.bundle_info({'bundle_name': self.path.name})
        self.assertEqual(info['snapshot_id'], self.sid)
        self.assertEqual(info['repositories'][self.rid], {'packages': 1, 'package_bytes': 48})
        self.assertEqual(info['signing_keys'], {self.rid: 1})
        self.assertTrue(info['matches_host'])
        self.assertFalse(info['verified'])
        self.assertFalse(list((self.target.root / 'bundle-key-candidates').iterdir()))
        with patch.object(engine, 'host_os', return_value={'major': '9', 'arch': 'x86_64'}):
            self.assertFalse(self.target.bundle_info({'bundle_name': self.path.name})['matches_host'])

    def test_bundle_info_rejects_other_roles_and_names(self):
        for data in [{'bundle_name': '../escape.tar'}, {'bundle_name': self.path.name, 'extra': 1}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.target.bundle_info(data)
        self.target.config['role'] = 'connected'
        with self.assertRaises(ValueError):
            self.target.bundle_info({'bundle_name': self.path.name})

    def test_scan_reports_candidates_and_host_trust_without_granting_trust(self):
        key = inspected_key()
        for host, expected in [(set(), False), ({key['fingerprint']}, True)]:
            with self.subTest(host=host), patch.object(engine, 'inspect_public_key', return_value=key), \
                    patch.object(engine, 'host_rpm_fingerprints', return_value=host):
                scan = self.target.scan_bundle_keys({'bundle_name': self.path.name})
            [candidate] = scan['candidates']
            self.assertEqual((candidate['repository_id'], candidate['fingerprint']), (self.rid, key['fingerprint']))
            self.assertEqual(candidate['host_trusted'], expected)
            self.assertFalse(candidate['approved'])
            self.assertNotIn('public_key', candidate)
            self.assertFalse(self.target.trusted_keys(self.rid))

    def test_scan_marks_already_approved_keys(self):
        key = inspected_key()
        engine.write_json(self.target.root / 'trusted-keys' / (self.rid + '.json'),
                          {'repository_id': self.rid, 'signing_keys': [key]})
        with patch.object(engine, 'inspect_public_key', return_value=key), \
                patch.object(engine, 'host_rpm_fingerprints', return_value=set()):
            self.assertTrue(self.target.scan_bundle_keys({'bundle_name': self.path.name})['candidates'][0]['approved'])

    def test_uninspectable_candidate_is_reported_not_hidden(self):
        with patch.object(engine, 'inspect_public_key', side_effect=ValueError('Signing key is expired')), \
                patch.object(engine, 'host_rpm_fingerprints', return_value=set()):
            [candidate] = self.target.scan_bundle_keys({'bundle_name': self.path.name})['candidates']
        self.assertIsNone(candidate['fingerprint'])
        self.assertIn('expired', candidate['error'])
        self.assertFalse(candidate['host_trusted'])


class HostFingerprints(unittest.TestCase):
    def test_only_primary_fingerprints_are_returned(self):
        colons = '\n'.join(['pub:-:4096:1:AAAA:1:::-:::scSC::::::23::0:', 'fpr:::::::::' + 'A' * 40 + ':',
                            'sub:-:4096:1:BBBB:1:::::e::::::23:', 'fpr:::::::::' + 'B' * 40 + ':'])
        results = [subprocess.CompletedProcess([], 0, test_bundled_keys.armor(), ''), subprocess.CompletedProcess([], 0, colons, '')]
        with patch.object(engine.subprocess, 'run', side_effect=results):
            self.assertEqual(engine.host_rpm_fingerprints(), {'A' * 40})

    def test_an_unsupported_key_does_not_hide_the_others(self):
        # GnuPG 2.4 rejects version 6 keys; each key is inspected on its own.
        keyring = test_bundled_keys.armor() * 3
        results = [subprocess.CompletedProcess([], 0, keyring, ''),
                   subprocess.CompletedProcess([], 0, 'pub:-:4096:1:AAAA:1:::-:::scSC::::::23::0:\nfpr:::::::::' + 'A' * 40 + ':', ''),
                   subprocess.CompletedProcess([], 2, '', 'gpg: packet(6) with unknown version 6'),
                   subprocess.CompletedProcess([], 0, 'pub:-:4096:1:CCCC:1:::-:::scSC::::::23::0:\nfpr:::::::::' + 'C' * 40 + ':', '')]
        with patch.object(engine.subprocess, 'run', side_effect=results):
            self.assertEqual(engine.host_rpm_fingerprints(), {'A' * 40, 'C' * 40})

    def test_unreadable_keyring_means_prompting(self):
        with patch.object(engine.subprocess, 'run', side_effect=OSError('rpm missing')):
            self.assertEqual(engine.host_rpm_fingerprints(), set())
        with patch.object(engine.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', '')):
            self.assertEqual(engine.host_rpm_fingerprints(), set())


if __name__ == '__main__':
    unittest.main()

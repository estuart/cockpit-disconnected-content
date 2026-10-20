"""Connected workflow, history migration, artifact availability, and verification."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_engine import engine, fixture


class Dashboard(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = engine.Engine({'role': 'connected', 'state_dir': self.temp.name, 'reserve_gib': 0})
        self.sid = 'a' * 32
        engine.write_json(self.app.root / 'profile.json', {'repositories': ['example-baseos'], 'max_gib': 1})
        self.snapshot = {'id': self.sid, 'created': '2026-01-01T10:00:00+00:00', 'status': 'validated',
                         'os': {'major': '10', 'arch': 'x86_64'}, 'package_bytes': 10,
                         'repositories': {'example-baseos': {'packages': 1, 'package_bytes': 10, 'metadata_types': ['updateinfo']}}}

    def summary(self, snapshots=None, jobs=None):
        return self.app.connected_status(deepcopy([self.snapshot] if snapshots is None else snapshots), jobs or [])

    def archive_record(self):
        path = self.app.root / 'exports' / (self.sid + '.tar')
        path.write_bytes(b'archive fixture for availability checks')
        sha = engine.digest(path)
        path.with_suffix('.tar.sha256').write_text(f'{sha}  {path.name}\n')
        record = {'snapshot_id': self.sid, 'exported_at': '2026-01-01T11:00:00+00:00',
                  'verified_at': '2026-01-01T11:00:00+00:00', 'job_id': 'b' * 32,
                  'sha256': sha, 'bytes': path.stat().st_size, 'fingerprint': self.app.fingerprint(path)}
        engine.write_json(self.app.root / 'export-records' / (self.sid + '.json'), record)
        return path, record

    def test_empty_selection_and_unsynced_selection_have_distinct_actions(self):
        self.assertEqual(self.summary([])['phase'], 'sync')
        engine.write_json(self.app.root / 'profile.json', {'repositories': [], 'max_gib': 1})
        self.assertEqual(self.summary()['phase'], 'select')

    def test_completed_sync_requires_export(self):
        result = self.summary()
        self.assertEqual((result['phase'], result['action']), ('export', 'export'))
        self.assertEqual(result['selected_repositories'][0]['synced_at'], self.snapshot['created'])

    def test_changed_selection_requires_a_new_matching_snapshot(self):
        engine.write_json(self.app.root / 'profile.json', {'repositories': ['example-baseos', 'example-appstream'], 'max_gib': 1})
        result = self.summary()
        self.assertTrue(result['selection_changed'])
        self.assertEqual(result['phase'], 'sync')
        self.assertIsNone(next(r for r in result['selected_repositories'] if r['id'] == 'example-appstream')['synced_at'])

    def test_an_older_matching_selection_remains_usable(self):
        newer = {**self.snapshot, 'id': 'c' * 32, 'created': '2026-01-02T10:00:00+00:00', 'repositories': {'other': {}}}
        result = self.summary([newer, self.snapshot])
        self.assertEqual(result['latest_sync']['id'], newer['id'])
        self.assertEqual(result['selection_snapshot']['id'], self.sid)
        self.assertEqual(result['phase'], 'export')

    def test_available_bundle_is_ready_without_rehashing_on_status(self):
        self.archive_record()
        with patch.object(engine, 'digest', side_effect=AssertionError('Status must not hash archives')):
            result = self.summary()
        self.assertEqual(result['phase'], 'ready')
        self.assertEqual(result['latest_export']['exported_at'], '2026-01-01T11:00:00+00:00')

    def test_deleted_file_and_missing_sidecar_require_rebuild_without_losing_history(self):
        for which in ['archive', 'sidecar']:
            path, record = self.archive_record()
            (path if which == 'archive' else path.with_suffix('.tar.sha256')).unlink()
            result = self.summary()
            self.assertEqual((result['phase'], result['action']), ('export', 'export'))
            self.assertEqual(result['latest_export']['state'], 'missing')
            self.assertEqual(result['latest_export']['exported_at'], record['exported_at'])

    def test_same_size_tampering_and_bad_sidecar_do_not_show_ready(self):
        path, record = self.archive_record()
        path.write_bytes(b'x' * record['bytes'])
        self.assertEqual(self.summary()['phase'], 'verify')
        path, _ = self.archive_record()
        path.with_suffix('.tar.sha256').write_text('wrong checksum')
        self.assertEqual(self.summary()['latest_export']['state'], 'changed')

    def test_symlinked_transfer_files_are_not_available(self):
        path, record = self.archive_record()
        target = self.app.root / 'saved.tar'; path.rename(target); path.symlink_to(target)
        self.assertEqual(self.app.export_summary(record)['state'], 'changed')

    def test_new_sync_does_not_inherit_an_older_snapshots_export(self):
        self.archive_record()
        newer = {**self.snapshot, 'id': 'c' * 32, 'created': '2026-01-02T10:00:00+00:00'}
        result = self.summary([newer, self.snapshot])
        self.assertEqual(result['phase'], 'export')
        self.assertEqual(result['latest_export']['snapshot_id'], self.sid)
        self.assertIsNone(result['selection_snapshot']['export'])

    def test_latest_failure_is_visible_while_previous_content_remains_available(self):
        self.archive_record()
        failure = {'id': 'd' * 32, 'operation': 'sync', 'status': 'failed', 'stage': 'Size exceeds budget',
                   'created': '2026-01-02T10:00:00+00:00', 'profile': self.app.profile()}
        result = self.summary(jobs=[failure])
        self.assertEqual(result['phase'], 'failed')
        self.assertEqual(result['latest_export']['state'], 'available')
        self.assertEqual(result['attention_job']['id'], failure['id'])

    def test_failure_from_a_different_selection_does_not_block_current_workflow(self):
        failure = {'operation': 'sync', 'status': 'failed', 'created': '2026-01-02T10:00:00+00:00',
                   'stage': 'Failed', 'profile': {'repositories': ['other'], 'max_gib': 1}}
        self.assertEqual(self.summary(jobs=[failure])['phase'], 'export')

    def test_failed_integrity_check_offers_a_rebuild_and_preserves_export_evidence(self):
        self.archive_record()
        failure = {'id': 'd' * 32, 'operation': 'verify-export', 'snapshot_id': self.sid,
                   'status': 'failed', 'stage': 'Archive checksum mismatch', 'created': '2026-01-02T10:00:00+00:00'}
        result = self.summary(jobs=[failure])
        self.assertEqual((result['phase'], result['action']), ('failed', 'export'))
        self.assertEqual(result['latest_export']['exported_at'], '2026-01-01T11:00:00+00:00')

    def test_running_operation_and_matching_size_plan_are_scoped_correctly(self):
        active = {'operation': 'sync', 'status': 'running', 'stage': 'Downloading', 'profile': self.app.profile(), 'plan': {'packages': 1}}
        other = {'operation': 'preflight', 'status': 'succeeded', 'profile': {'repositories': ['other'], 'max_gib': 1}, 'plan': {'packages': 99}}
        self.assertEqual(self.summary(jobs=[other, active])['plan_job']['plan']['packages'], 1)
        self.assertEqual(self.summary(jobs=[other, active])['phase'], 'running')
        self.assertIsNone(self.summary(jobs=[other])['plan_job'])

    def test_failed_preflight_is_visible_until_a_newer_successful_check(self):
        failed = {'operation': 'preflight', 'status': 'failed', 'stage': 'Metadata query failed',
                  'created': '2026-01-02T10:00:00+00:00', 'profile': self.app.profile()}
        self.assertEqual(self.summary(jobs=[failed])['phase'], 'failed')
        succeeded = {**failed, 'status': 'succeeded', 'created': '2026-01-02T11:00:00+00:00'}
        self.assertEqual(self.summary(jobs=[succeeded, failed])['phase'], 'export')

    def test_legacy_export_migrates_once_and_survives_loss_of_old_job_history(self):
        path, record = self.archive_record()
        (self.app.root / 'export-records' / (self.sid + '.json')).unlink()
        job = {'id': 'e' * 32, 'operation': 'export', 'snapshot_id': self.sid, 'status': 'succeeded',
               'created': record['exported_at'], 'finished': record['exported_at'],
               'result': {'path': str(path), 'sha256': record['sha256'], 'bytes': record['bytes']}}
        result = self.summary(jobs=[job])
        self.assertEqual(result['latest_export']['state'], 'unverified')
        self.assertEqual(result['phase'], 'verify')
        self.assertEqual(self.summary()['latest_export']['exported_at'], record['exported_at'])
        self.assertIsNone(self.summary()['latest_export']['fingerprint'])

    def test_status_retains_export_older_than_fifty_recent_jobs(self):
        path, record = self.archive_record()
        (self.app.root / 'export-records' / (self.sid + '.json')).unlink()
        engine.write_json(self.app.root / 'snapshots' / self.sid / 'snapshot.json', self.snapshot)
        export = {'id': 'e' * 32, 'operation': 'export', 'snapshot_id': self.sid, 'status': 'succeeded',
                  'created': record['exported_at'], 'result': {'path': str(path), 'sha256': record['sha256'], 'bytes': record['bytes']}}
        engine.write_json(self.app.job_path(export['id']), export)
        for i in range(51):
            jid = f'{i:032x}'
            engine.write_json(self.app.job_path(jid), {'id': jid, 'operation': 'preflight', 'status': 'succeeded', 'created': '2026-01-02T00:00:00+00:00'})
        with patch.object(engine, 'host_os', return_value={'major': '10', 'arch': 'x86_64'}):
            result = self.app.status()
        self.assertEqual(len(result['jobs']), 50)
        self.assertEqual(result['connected']['latest_export']['snapshot_id'], self.sid)

    def create_real_export(self):
        root = self.app.root / 'snapshots' / self.sid
        fixture(root / 'repositories' / 'example-baseos')
        engine.write_json(root / 'snapshot.json', self.snapshot)
        with patch.object(engine, 'validate_repository', return_value={}), patch.object(engine, 'configured_signing_key_sources', return_value={}):
            self.app.export({'id': 'b' * 32, 'snapshot_id': self.sid, 'status': 'succeeded',
                             'operation': 'export', 'created': self.snapshot['created']})
        return root

    def test_explicit_verification_records_time_without_advancing_export_time(self):
        self.create_real_export()
        before = self.summary()['latest_export']
        self.app.verify_export({'id': 'c' * 32, 'snapshot_id': self.sid, 'status': 'running',
                                'operation': 'verify-export', 'created': engine.now()})
        after = self.summary()['latest_export']
        self.assertEqual(after['exported_at'], before['exported_at'])
        self.assertGreaterEqual(after['verified_at'], before['verified_at'])
        self.assertEqual(after['state'], 'available')

    def test_corrupt_archive_and_wrong_snapshot_cannot_be_certified(self):
        self.create_real_export()
        path = self.app.root / 'exports' / (self.sid + '.tar')
        wrong = self.app.root / 'exports' / ('d' * 32 + '.tar')
        wrong.write_bytes(path.read_bytes())
        wrong.with_suffix('.tar.sha256').write_text(f'{engine.digest(wrong)}  {wrong.name}\n')
        with self.assertRaisesRegex(ValueError, 'different snapshot'):
            self.app.verify_export({'id': 'c' * 32, 'snapshot_id': 'd' * 32})
        raw = path.read_bytes(); path.write_bytes(raw.replace(b'A repository fixture', b'X repository fixture'))
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.app.verify_export({'id': 'e' * 32, 'snapshot_id': self.sid})

    def test_verification_rejects_wrong_sidecar_and_missing_sidecar(self):
        self.create_real_export()
        path = self.app.root / 'exports' / (self.sid + '.tar.sha256')
        path.write_text('wrong\n')
        with self.assertRaisesRegex(ValueError, 'sidecar'):
            self.app.verify_export({'id': 'c' * 32, 'snapshot_id': self.sid})
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'both be present'):
            self.app.verify_export({'id': 'd' * 32, 'snapshot_id': self.sid})


if __name__ == '__main__':
    unittest.main()

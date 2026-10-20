"""Disconnected workflow, inexpensive availability checks, and honest client status."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch
from test_engine import engine


class DisconnectedDashboard(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = engine.Engine({'role': 'disconnected', 'state_dir': self.temp.name, 'reserve_gib': 0,
                                  'allow_unsigned_imports': True})
        self.sid = 'a' * 32
        self.snapshot = {'id': self.sid, 'created': '2026-01-01T10:00:00+00:00', 'status': 'validated', 'imported': True,
                         'source_archive_sha256': 'c' * 64, 'package_bytes': 10,
                         'repositories': {'example-baseos': {'packages': 1, 'package_bytes': 10, 'metadata_types': ['updateinfo']}}}
        self.publication = {'installed': True, 'server_active': True, 'base_url': 'http://mirror.example.org', 'current': None,
                            'availability': {'state': 'not_published', 'reason': 'Not published'}}

    def published(self):
        self.publication.update(current={'snapshot_id': self.sid, 'published_at': '2026-01-01T11:00:00+00:00',
                                         'base_url': self.publication['base_url'], 'repositories': self.snapshot['repositories']},
                                availability={'state': 'available', 'reason': 'Files present'})

    def state(self, snapshots=None, jobs=None, available=True):
        source, incoming = self.app.scan_incoming()
        source['available'] = available
        return self.app.disconnected_status(deepcopy([self.snapshot] if snapshots is None else snapshots), jobs or [],
                                            source, incoming, deepcopy(self.publication)), incoming

    def bundle(self, data=b'new content'):
        path = self.app.root / 'incoming' / 'transfer.tar'
        path.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        path.with_suffix('.tar.sha256').write_text(f'{sha}  {path.name}\n')
        return path, sha

    def test_first_use_source_and_waiting_are_distinct(self):
        self.assertEqual(self.state([], available=False)[0]['phase'], 'source')
        self.assertEqual(self.state([])[0]['phase'], 'waiting')

    def test_pending_content_requires_review_without_certifying_the_archive(self):
        self.bundle()
        state, incoming = self.state([])
        self.assertEqual(state['phase'], 'import')
        self.assertEqual(incoming[0]['state'], 'awaiting_import')
        self.assertEqual(state['pending_bundles'], 1)
        self.assertEqual(state['client_application'], 'unconfirmed')

    def test_bad_or_missing_checksum_is_visible_as_blocked(self):
        path, _ = self.bundle()
        for data in [None, 'invalid checksum', 'x' * 1025]:
            sidecar = path.with_suffix('.tar.sha256')
            if data is None:
                sidecar.unlink()
            else:
                sidecar.write_text(data)
            state, incoming = self.state([])
            self.assertEqual(incoming[0]['state'], 'blocked')
            self.assertEqual(state['pending_bundles'], 1)

    def test_import_policy_is_a_blocker_for_new_imports(self):
        self.app.config['allow_unsigned_imports'] = False
        self.bundle()
        self.assertEqual(self.state([])[0]['phase'], 'import_policy')

    def test_publisher_setup_and_server_url_are_distinct_prerequisites(self):
        self.publication['installed'] = False
        self.assertEqual(self.state()[0]['phase'], 'publisher_setup')
        self.publication['installed'] = True
        self.publication['base_url'] = ''
        self.assertEqual(self.state()[0]['phase'], 'server_url')

    def test_imported_content_needs_publication_before_client_setup(self):
        self.assertEqual(self.state()[0]['phase'], 'publish')
        self.published()
        self.assertEqual(self.state()[0]['phase'], 'clients')
        self.assertEqual(self.state()[0]['repositories'][0]['publication_state'], 'current')

    def test_latest_import_does_not_inherit_the_previous_publication(self):
        self.published()
        newer = {**self.snapshot, 'id': 'd' * 32, 'created': '2026-01-02T10:00:00+00:00'}
        state, _ = self.state([newer, self.snapshot])
        self.assertEqual(state['phase'], 'publish')
        self.assertEqual(state['target_snapshot_id'], newer['id'])
        self.assertFalse(state['publication_matches_latest'])
        self.assertEqual(state['repositories'][0]['publication_state'], 'older')

    def test_pending_files_do_not_skip_an_unfinished_publication(self):
        self.bundle()
        self.assertEqual(self.state()[0]['phase'], 'publish')
        self.published()
        self.assertEqual(self.state()[0]['phase'], 'import')

    def test_unmounted_source_and_disabled_imports_do_not_block_existing_clients(self):
        self.published()
        self.app.config['allow_unsigned_imports'] = False
        self.assertEqual(self.state(available=False)[0]['phase'], 'clients')

    def test_apache_or_local_files_unavailable_requires_republication(self):
        self.published()
        self.publication['server_active'] = False
        self.assertEqual(self.state()[0]['phase'], 'publish')
        self.publication['server_active'] = True
        self.publication['availability'] = {'state': 'unavailable', 'reason': 'Index missing'}
        self.assertEqual(self.state()[0]['phase'], 'publish')

    def test_saved_url_change_requires_review_and_preserves_current_record(self):
        self.published()
        self.publication['base_url'] = 'https://new.example.org'
        state, _ = self.state()
        self.assertEqual(state['phase'], 'publish')
        self.assertTrue(state['server_url_changed'])
        self.assertEqual(self.publication['current']['base_url'], 'http://mirror.example.org')

    def test_legacy_checksum_match_is_not_presented_as_a_fresh_file_verification(self):
        path, sha = self.bundle()
        self.snapshot['source_archive_sha256'] = sha
        self.published()
        state, incoming = self.state()
        self.assertEqual(state['phase'], 'clients')
        self.assertEqual(incoming[0]['state'], 'known')
        self.assertNotEqual(incoming[0]['state'], 'imported')

    def test_import_evidence_tracks_attributes_without_hashing_status_and_survives_jobs(self):
        path, sha = self.bundle()
        self.snapshot['source_archive_sha256'] = sha
        self.app.record_import_check({'id': 'e' * 32}, path, self.snapshot, self.app.fingerprint(path))
        with patch.object(engine, 'digest', side_effect=AssertionError('No archive hashing during status')):
            _, incoming = self.state()
        self.assertEqual(incoming[0]['state'], 'imported')
        path.write_bytes(b'x' * len(path.read_bytes()))
        _, incoming = self.state()
        self.assertEqual(incoming[0]['state'], 'changed')
        self.assertEqual(self.snapshot['created'], '2026-01-01T10:00:00+00:00')

    def test_a_claimed_known_checksum_does_not_hide_a_failed_import_of_that_file(self):
        path, sha = self.bundle()
        self.snapshot['source_archive_sha256'] = sha
        self.published()
        failed = {'operation': 'import', 'bundle_name': path.name, 'incoming_dir': str(path.parent),
                  'status': 'failed', 'stage': 'Checksum mismatch', 'created': '2026-01-01T09:00:00+00:00'}
        state, incoming = self.state(jobs=[failed])
        self.assertEqual(incoming[0]['state'], 'changed')
        self.assertEqual(state['pending_bundles'], 1)
        self.assertEqual(state['phase'], 'import')

    def test_latest_failure_is_visible_with_prior_publication_preserved(self):
        self.published()
        job = {'id': 'e' * 32, 'operation': 'publish', 'snapshot_id': self.sid, 'status': 'failed',
               'created': '2026-01-02T10:00:00+00:00', 'stage': 'Copy failed'}
        state, _ = self.state(jobs=[job])
        self.assertEqual(state['phase'], 'failed')
        self.assertTrue(state['publication_serving'])
        self.assertEqual(state['attention_job']['id'], job['id'])

    def test_old_source_failures_do_not_block_the_saved_source(self):
        job = {'operation': 'import', 'incoming_dir': '/different-source', 'status': 'failed',
               'created': '2026-01-02T10:00:00+00:00', 'stage': 'Checksum failed'}
        self.assertEqual(self.state(jobs=[job])[0]['phase'], 'publish')

    def test_active_import_and_publish_take_precedence(self):
        for operation in ['import', 'publish']:
            job = {'operation': operation, 'status': 'running', 'stage': 'Working', 'created': engine.now()}
            self.assertEqual(self.state(jobs=[job])[0]['phase'], 'running')

    def test_publication_checks_local_files_without_full_revalidation(self):
        public = self.app.root / 'public'
        index = public / 'snapshots' / self.sid / 'repositories/example-baseos/repodata/repomd.xml'
        index.parent.mkdir(parents=True)
        index.write_text('<repomd/>')
        (public / 'client.repo').write_text('configuration')
        record = {'snapshot_id': self.sid, 'repositories': {'example-baseos': {}}, 'client_config': 'configuration'}
        with patch.object(engine, 'PUBLISH_ROOT', self.app.root), patch.object(engine, 'validate_repository', side_effect=AssertionError('No full verification during status')):
            self.assertEqual(self.app.publication_availability(record)['state'], 'available')
            index.unlink()
            self.assertEqual(self.app.publication_availability(record)['state'], 'unavailable')

    def test_changed_public_config_and_symlinked_index_are_unavailable(self):
        public = self.app.root / 'public'
        public.mkdir()
        (public / 'client.repo').write_text('changed')
        record = {'snapshot_id': self.sid, 'repositories': {'example-baseos': {}}, 'client_config': 'expected'}
        with patch.object(engine, 'PUBLISH_ROOT', self.app.root):
            self.assertEqual(self.app.publication_availability(record)['state'], 'unavailable')
            (public / 'client.repo').write_text('expected')
            index = public / 'snapshots' / self.sid / 'repositories/example-baseos/repodata/repomd.xml'
            index.parent.mkdir(parents=True)
            index.symlink_to(public / 'client.repo')
            self.assertEqual(self.app.publication_availability(record)['state'], 'unavailable')


if __name__ == '__main__':
    unittest.main()

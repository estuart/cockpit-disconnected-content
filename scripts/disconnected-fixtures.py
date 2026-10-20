#!/usr/bin/env python3
"""Generate generic disconnected UI states from the backend workflow model."""
import importlib.util
import json
from pathlib import Path
import tempfile

spec = importlib.util.spec_from_file_location('engine', Path(__file__).resolve().parents[1] / 'backend/disconnected_content.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
states = {}
cases = ['source', 'waiting', 'incoming', 'policy', 'checksum', 'publisher', 'url', 'publish', 'clients',
         'pending', 'older', 'server-down', 'public-missing', 'url-changed', 'failed-import', 'failed-publish',
         'running-import', 'running-publish', 'known']
for case in cases:
    with tempfile.TemporaryDirectory() as temporary:
        app = engine.Engine({'role': 'disconnected', 'state_dir': temporary, 'allow_unsigned_imports': case != 'policy'})
        sid = 'a' * 32
        repositories = {'example-repo': {'packages': 146, 'package_bytes': 90000000, 'metadata_types': ['primary', 'updateinfo']}}
        snapshot = {'id': sid, 'created': '2026-01-01T10:00:00+00:00', 'source_created': '2025-12-31T10:00:00+00:00',
                    'verified_at': '2026-01-01T10:00:00+00:00', 'status': 'validated', 'imported': True,
                    'os': {'major': '10', 'arch': 'x86_64'}, 'package_bytes': 90000000,
                    'source_archive_sha256': 'c' * 64, 'repositories': repositories}
        snapshots = [] if case in {'source', 'waiting', 'incoming', 'policy', 'checksum'} else [snapshot]
        if case in {'incoming', 'policy', 'checksum', 'pending', 'known'}:
            path = app.root / 'incoming/transfer.tar'
            path.write_bytes(b'Generic archive fixture')
            checksum = engine.digest(path)
            path.with_suffix('.tar.sha256').write_text(f'{checksum}  {path.name}\n' if case != 'checksum' else 'invalid\n')
            if case == 'known':
                snapshot['source_archive_sha256'] = checksum
        publication = {'installed': case != 'publisher', 'server_active': case != 'server-down',
                       'base_url': '' if case == 'url' else 'http://mirror.example.org', 'current': None,
                       'availability': {'state': 'not_published', 'reason': 'No publication'}}
        if case in {'clients', 'pending', 'older', 'server-down', 'public-missing', 'url-changed',
                    'failed-import', 'failed-publish', 'running-import', 'running-publish', 'known'}:
            record = {'snapshot_id': sid, 'published_at': '2026-01-01T11:00:00+00:00',
                      'verified_at': '2026-01-01T10:59:00+00:00', 'base_url': 'http://mirror.example.org',
                      'config_url': 'http://mirror.example.org/disconnected-content/client.repo',
                      'job_id': 'b' * 32, 'bytes': 91000000, 'os': snapshot['os'], 'repositories': repositories}
            record['client_config'] = engine.client_repo_config(record['base_url'], {'id': sid, 'repositories': repositories})
            publication.update(current=record, availability={'state': 'available', 'reason': 'Public indexes and configuration present'})
            if case == 'public-missing':
                publication['availability'] = {'state': 'unavailable', 'reason': 'Public repository index missing'}
            if case == 'url-changed':
                publication['base_url'] = 'https://new.example.org'
            if case == 'older':
                snapshots = [{**snapshot, 'id': 'd' * 32, 'created': '2026-01-02T10:00:00+00:00'}, snapshot]
        source, incoming = app.scan_incoming()
        if case == 'source':
            source.update(available=False, error='Mount transfer media or select an existing folder')
        jobs = []
        if case.startswith(('failed-', 'running-')):
            operation = 'publish' if case.endswith('publish') else 'import'
            jobs = [{'id': 'e' * 32, 'operation': operation, 'status': 'running' if case.startswith('running') else 'failed',
                     'incoming_dir': source['path'], 'snapshot_id': sid, 'created': '2026-01-03T10:00:00+00:00',
                     'started': '2026-01-03T10:00:00+00:00', 'updated': '2026-01-03T10:00:01+00:00',
                     'stage': 'Verifying content' if case.startswith('running') else 'Example operation failed'}]
        flow = app.disconnected_status(snapshots, jobs, source, incoming, publication)
        state = {'version': engine.VERSION, 'role': 'disconnected', 'os': {'name': 'RHEL 10', 'major': '10', 'arch': 'x86_64'},
                 'storage': {'path': temporary, 'free': 100000000000, 'total': 150000000000, 'reserve': 5000000000},
                 'profile': app.profile(), 'repositories': [], 'snapshots': snapshots, 'jobs': jobs, 'incoming': incoming,
                 'import_source': source, 'publication': publication, 'connected': None, 'disconnected': flow,
                 'capabilities': {'allow_unsigned_imports': app.config['allow_unsigned_imports']},
                 'automation': engine.client_automation(publication['current']) if publication['current'] else None}
        states['disconnected-' + case] = json.loads(json.dumps(state).replace(temporary, '/var/lib/disconnected-content'))
print(json.dumps(states))

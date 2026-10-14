#!/usr/bin/env python3
"""Generate generic UI fixtures using the real connected workflow model."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

spec = importlib.util.spec_from_file_location('engine', Path(__file__).resolve().parents[1] / 'backend/disconnected_content.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

states = {}
for case in ['select', 'sync', 'export', 'ready', 'historical', 'missing', 'changed', 'failed', 'running', 'preflight-failed']:
    with tempfile.TemporaryDirectory() as temporary:
        app = engine.Engine({'role': 'connected', 'state_dir': temporary, 'reserve_gib': 0})
        sid = 'a' * 32
        repos = ['example-baseos'] if case != 'select' else []
        if case == 'changed':
            repos.append('example-appstream')
        engine.write_json(app.root / 'profile.json', {'repositories': repos, 'max_gib': 1})
        snapshots = [] if case in {'select', 'sync'} else [{'id': sid, 'created': '2026-01-01T10:00:00+00:00',
            'status': 'validated', 'os': {'major': '10', 'arch': 'x86_64'}, 'package_bytes': 90000000,
            'repositories': {'example-baseos': {'packages': 146, 'package_bytes': 90000000, 'metadata_types': ['updateinfo']}}}]
        if case in {'ready', 'historical', 'missing'}:
            archive = app.root / 'exports' / (sid + '.tar')
            archive.write_bytes(b'UI fixture')
            sha = engine.digest(archive)
            archive.with_suffix('.tar.sha256').write_text(f'{sha}  {archive.name}\n')
            engine.write_json(app.root / 'export-records' / (sid + '.json'), {'snapshot_id': sid,
                'exported_at': '2026-01-01T11:00:00+00:00', 'verified_at': '2026-01-01T11:00:00+00:00',
                'job_id': 'b' * 32, 'sha256': sha, 'bytes': archive.stat().st_size,
                'fingerprint': app.fingerprint(archive) if case != 'historical' else None})
            if case == 'missing':
                archive.unlink()
        jobs = []
        if case in {'failed', 'running', 'preflight-failed'}:
            jobs = [{'id': 'c' * 32, 'operation': 'preflight' if case == 'preflight-failed' else 'sync', 'status': 'running' if case == 'running' else 'failed',
                     'profile': app.profile(), 'stage': 'Example download failed' if case == 'failed' else 'Downloading original packages and metadata',
                     'created': '2026-01-02T10:00:00+00:00', 'started': '2026-01-02T10:00:00+00:00', 'updated': '2026-01-02T10:01:00+00:00'}]
        connected = app.connected_status(snapshots, jobs)
        states[case] = json.loads(json.dumps({'version': engine.VERSION, 'role': 'connected',
            'os': {'name': 'RHEL 10', 'major': '10', 'arch': 'x86_64'},
            'storage': {'path': '/var/lib/disconnected-content', 'free': 100000000000, 'total': 150000000000, 'reserve': 5000000000},
            'repositories': [{'id': 'example-baseos', 'name': 'RHEL 10 BaseOS', 'enabled': True},
                             {'id': 'example-appstream', 'name': 'RHEL 10 AppStream', 'enabled': False}],
            'profile': app.profile(), 'jobs': jobs, 'snapshots': snapshots, 'incoming': [],
            'import_source': None, 'publication': None, 'connected': connected,
            'capabilities': {'allow_unsigned_imports': False}}).replace(temporary, '/var/lib/disconnected-content'))
print(json.dumps(states))

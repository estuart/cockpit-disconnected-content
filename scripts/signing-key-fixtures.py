#!/usr/bin/env python3
"""Generic browser fixtures with backend-generated signed-repository downloads."""
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tests'))
from test_signing_keys import key, engine

state = json.loads(subprocess.check_output([sys.executable, str(root / 'scripts/disconnected-fixtures.py')], text=True))['disconnected-clients']
review = dict(key(), review_id='d' * 32, repository_id='vendor-example', reviewed_at='2026-01-01T00:00:00Z', source='vendor.asc')
record = state['publication']['current']
record['repositories'] = {'vendor-example': {'packages': 1, 'metadata_types': ['primary'], 'signing_keys': [key()]}}
record['client_config'] = engine.client_repo_config(record['base_url'], {'id': record['snapshot_id'], 'repositories': record['repositories']})
state['automation'] = engine.client_automation(record)
state['signing_key_files'] = ['vendor.asc']
state['trusted_signing_keys'] = []
print(json.dumps({'state': state, 'review': review}))

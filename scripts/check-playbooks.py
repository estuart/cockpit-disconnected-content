#!/usr/bin/env python3
"""Parse generated YAML and run real Ansible syntax checks with generic fixtures."""
from copy import deepcopy
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from test_automation import engine, publication
from test_signing_keys import key
from ansible.parsing.dataloader import DataLoader

native = publication()
reviewed = deepcopy(native)
reviewed['repositories'] = {'vendor-example': {'metadata_types': ['primary'], 'signing_keys': [key()]}}
reviewed['client_config'] = engine.client_repo_config(reviewed['base_url'],
    {'id': reviewed['snapshot_id'], 'repositories': reviewed['repositories']})
for record in [native, reviewed]:
    result = engine.client_automation(record)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for name, content in result['files'].items():
            (root / name).write_text(content)
        for name in ['configure-repos.yml', 'security-updates.yml']:
            play = DataLoader().load(result['files'][name])[0]
            assert play['serial'] == 1 and play['any_errors_fatal'] is True
            assert play['hosts'] == 'disconnected_clients' and play['become'] is True
            if name == 'configure-repos.yml':
                repo_task = next(task for task in play['tasks'] if task.get('ansible.builtin.copy', {}).get('dest', '').endswith('/disconnected-content.repo'))
                assert repo_task['ansible.builtin.copy']['content'] == record['client_config']
                if record is reviewed:
                    verification = next(task for task in play['tasks'] if task['name'].startswith("Verify the publication's full signing-key"))
                    assert key()['fingerprint'] in verification['ansible.builtin.assert']['that'][1]
                    assert verification['when'] == 'not ansible_check_mode'
            else:
                task = play['tasks'][0]['ansible.builtin.dnf']
                assert task['security'] is True and task['update_only'] is True
                assert task['disable_gpg_check'] is False and task['disablerepo'] == ['*']
                assert task['enablerepo'] == ['dc-' + rid for rid in sorted(record['repositories'])]
                if record is reviewed:
                    assert key()['sha256'] in result['files'][name]
            subprocess.run(['ansible-playbook', '-i', str(root / 'inventory.ini'), '--syntax-check', str(root / name)], check=True)
print('Native and reviewed-key playbooks: parsing, transaction scope, fingerprints, and Ansible syntax checks passed')

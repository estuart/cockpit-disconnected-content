#!/usr/bin/env python3
"""Parse generated YAML and run real Ansible syntax checks with a generic fixture."""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from test_automation import engine, publication
from ansible.parsing.dataloader import DataLoader

result = engine.client_automation(publication())
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    for name, content in result['files'].items():
        (root / name).write_text(content)
    for name in ['configure-repos.yml', 'security-updates.yml']:
        play = DataLoader().load(result['files'][name])[0]
        assert play['serial'] == 1 and play['any_errors_fatal'] is True
        assert play['hosts'] == 'disconnected_clients' and play['become'] is True
        if name == 'configure-repos.yml':
            assert play['tasks'][0]['ansible.builtin.copy']['content'] == publication()['client_config']
        else:
            task = play['tasks'][0]['ansible.builtin.dnf']
            assert task['security'] is True and task['update_only'] is True
            assert task['disable_gpg_check'] is False and task['disablerepo'] == ['*']
            assert task['enablerepo'] == ['dc-rhel-appstream', 'dc-rhel-baseos']
        subprocess.run(['ansible-playbook', '-i', str(root / 'inventory.ini'), '--syntax-check', str(root / name)], check=True)
print('Generated playbook parsing, transaction scope, and Ansible syntax checks passed')

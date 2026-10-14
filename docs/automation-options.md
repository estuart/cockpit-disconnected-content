# Downloadable client automation

The **Client setup → Ansible client automation** section provides four downloads
after a snapshot has been published (introduced in version 0.6 and moved to its
own tab in version 0.8):

| Download | Purpose |
| --- | --- |
| `configure-repos.yml` | Check client compatibility, install the exact pinned `.repo` file with a backup, and refresh metadata |
| `security-updates.yml` | Require that exact repository file, then preview or apply security updates using only its repository IDs |
| `inventory.ini` | Example `disconnected_clients` group; replace the example hostname and SSH user |
| `README-client-automation.txt` | Publication details, prerequisites, commands, and rollout behavior |

The GUI also previews every file. A download request identifies a durable
publication record, so publishing a newer snapshot while someone is downloading
does not substitute newer repository content. The playbook embeds the pinned
configuration; it never fetches the mutable `client.repo` URL. Download the pair
from the same publication and keep the files together. When advancing clients,
download and review a new pair.

## Prerequisites and use

Use an existing Ansible Core 2.16+ control node with SSH access to the clients.
Only `ansible.builtin` modules are used. Clients need compatible RHEL major
version and architecture, Python with `python3-dnf`, administrative become access,
independently provisioned and verified Red Hat RPM public keys, and access to
the published URLs. The playbooks do not enroll signing keys. The built-in DNF4
backend is selected explicitly for RHEL's Python DNF bindings.

Edit `inventory.ini` and start with a canary host. Add `--ask-become-pass` when
required by your existing sudo policy; keep SSH keys and credentials in your
normal Ansible setup.

```sh
ansible-playbook -i inventory.ini configure-repos.yml --limit client.example.org --check --diff
ansible-playbook -i inventory.ini configure-repos.yml --limit client.example.org --diff
ansible-playbook -i inventory.ini security-updates.yml --limit client.example.org --check --diff
ansible-playbook -i inventory.ini security-updates.yml --limit client.example.org
```

Configuration check mode previews the file change and skips metadata refresh;
apply configuration before previewing security updates. Security check mode can
refresh DNF cache metadata but does not install package updates. Review the
transaction before applying during the maintenance window, then expand the host
limit deliberately. Both playbooks process one host at a time and stop the play
on failure. They do not reboot hosts; package scripts can restart services.

The security playbook checks the installed file's SHA-256 and rejects missing,
modified, or symlinked configuration before the transaction. Every selected
repository must contain `updateinfo` metadata, otherwise it stops with an
explanation. `security: true`, `state: latest`, and `update_only: true` select
security updates to installed packages; dependency packages can also change.
RPM signature verification stays enabled. The playbooks retain unrelated repo
files and explicitly select the exact publication repository IDs for their DNF
operations. Existing DNF policy, exclusions, and version locks still apply.

Security coverage depends on the mirrored packages and advisory metadata. A
successful transaction with zero updates does not establish full patch coverage.
There is no automatic package rollback; switching repository files back to an
older snapshot does not undo installed updates.

## Later AAP integration

Cockpit currently generates downloads only. It does not launch remote jobs,
store client credentials, track external playbook runs, or connect to AAP.
A later integration can reuse an existing Automation Controller inventory,
credential, and job template, show the snapshot and host limit before launch,
and link results back to the publication. Embedded ansible-runner remains a
separate option with additional inventory, credential, execution environment,
cancellation, and artifact retention requirements.

References:
- [Ansible DNF module](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/dnf_module.html)
- [Ansible copy module](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/copy_module.html)
- [Check and diff modes](https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_checkmode.html)

# Client automation options

Publication and client rollout are separate operations. Version 0.5 publishes
validated repositories and generates a client `.repo` file; it does not launch
Ansible or execute commands on remote hosts.

## Suggested next steps

1. Generate a downloadable playbook for installing the selected publication's
   exact `.repo` content and refreshing metadata on compatible RHEL clients.
   Keep inventory and credentials in the customer's existing automation setup.
2. Offer an optional integration with an existing Automation Controller job
   template. Display the selected inventory, host limit, snapshot, and job mode
   before launching, then link its job result back to the publication.
3. Add a distinct update workflow after repository configuration is proven.
   Begin with an explicit check/preview, canary hosts, and an operator-selected
   batch size. Updating packages and rebooting should be separate decisions.

The repository configuration step should check RHEL major version/architecture,
retain package signature verification, and avoid silently disabling unrelated
repository definitions. Scoped DNF commands can explicitly select the published
repositories. Clients stay pinned to a snapshot until the operator advances the
configuration, which makes staged rollout and investigation reproducible.

For updates, select security-only or all applicable updates explicitly, define
maintenance windows and failure thresholds, and decide how to handle services
and reboot requirements. Switching repositories back to an older snapshot does
not roll back packages already installed.

## Integration tradeoffs

| Option | Benefit | Work introduced |
| --- | --- | --- |
| Downloadable playbooks | Works with existing Ansible workflows without storing remote credentials in Cockpit | Customer runs and tracks the jobs |
| Existing Automation Controller | Reuses inventories, credentials, job templates, and job history | Controller connection, launch permissions, host limits, and results integration |
| Embedded ansible-runner | Can support sites without a controller | Inventory and credential handling, execution environments, cancellation, artifact retention, and authorization |

An existing controller integration is the preferred first way to add a launch
button. Embedded execution can remain optional so the repository publisher does
not become a mandatory automation platform.

References:
- [Ansible DNF module](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/dnf_module.html)
- [Automation Controller job templates](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.5/html/using_automation_execution/controller-job-templates)

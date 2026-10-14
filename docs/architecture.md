# Architecture

The Cockpit page uses the documented `cockpit.spawn` API to call a root-owned
helper. Each call uses an argument array and requires administrative access.
The helper reads a root-owned configuration file at a fixed path. An administrator
can save only a local import source directory through the GUI; destination
storage and trust policy remain in administrator-managed configuration. Browser
requests cannot choose an executable or a download-source URL. Publication
settings accept a validated HTTP(S) origin used in generated client files; the
helper does not connect to that origin.

The disconnected role also exposes an administrator-only directory browser. It
returns at most 500 immediate real subdirectories, a parent path, and shortcuts
from the current Linux mount table and existing common transfer directories.
Mount-table path escapes are decoded; absent locations and symlinks are omitted.
It returns folder names and paths, without reading file contents or modifying
source settings. The GUI applies a chosen folder only after Save & scan.

Starting a job writes a durable JSON record under an exclusive lock and starts a
systemd template service. The service owns the process tree. State is replaced
atomically; command output is stored locally. Jobs survive browser disconnects.
After a machine restart, a stale active record is reconciled with systemd and
shown as interrupted. Interrupted content is never listed as a validated snapshot.

The connected pipeline is:

```
saved repository IDs -> declared-size preflight -> reposync with metadata
-> validate indexed metadata -> validate referenced RPMs and signatures
-> validated snapshot -> hash manifest -> tar -> read-back verification -> export
```

Each sync creates a new directory. No mutation, pruning, or deletion of an older
validated snapshot is performed. Partial content is retained for diagnosis but is
not exposed as a valid snapshot. Initial retention and cleanup are manual.

The disconnected importer accepts bundles only from the saved local source
directory, defaulting to the state's `incoming/` folder. Source settings are
atomically saved under the job lock and cannot change while a job is active.
Each queued import records its source path, which the worker uses. Scans reject
symlink source components and nonregular archive/checksum entries, do not recurse,
and report unavailable media without breaking the rest of the status response. It
verifies the outer checksum, bounds the manifest, checks major version and
architecture, streams regular files into private staging, and independently
validates repository contents and RPM signatures. A same-filesystem rename makes
the snapshot visible only after all checks pass. Failed imports remove staging.
No tar extraction API is used to apply archive paths, ownership, permissions, or
links. Archive members are written explicitly with restrictive local permissions.

Unsigned imports require explicit administrator configuration. A provisioned
manifest signing policy remains future work.

## HTTP publication

The optional publisher subpackage installs a fixed Apache alias for
`/var/www/disconnected-content/public/`. The engine's private state remains
outside the document tree. Listings, symlink following, content handlers,
overrides, and methods other than GET/HEAD are disabled for this alias.

Publishing is a systemd job restricted to the disconnected role and a validated
snapshot. The queue captures the client-facing origin. Only files indexed by
repomd.xml and primary metadata are copied to private staging on the publication
filesystem. The publisher independently revalidates both source and copy,
including RPM signatures, applies web-content SELinux labels, and checks/starts
Apache before an atomic rename exposes a complete snapshot directory.

An atomic file replacement updates the downloadable client configuration after
the snapshot is available. Its URLs pin that immutable snapshot. Private records
capture each publication and the most recent publication. A process failure
between replacing the client file and recording private history can leave the
download ahead of the GUI record; republishing repairs that bookkeeping without
replacing repository content. Earlier snapshots are retained. Publication does
not change firewall policy, configure clients, run remote jobs, or roll back
installed packages.

## Transfer format v1 (experimental)

An uncompressed tar starts with `manifest.json`. The remaining members are regular
files under `repositories/<repo-id>/`. The manifest records each member's size and
SHA-256, repository validation summaries, a snapshot ID, and source OS metadata.
User/group names are cleared in archive headers. Machine identity, private keys,
credentials, source repository definitions, runtime configuration, and job logs
are excluded. The schema may change before a stable release.

## Future privilege model

The prototype assumes an existing system administrator. A later limited-operator
mode should expose narrowly defined backend methods and enforce authorization in
the backend, with polkit as appropriate. Cockpit UI permission checks are for
presentation and are never an authorization boundary.

## Downloadable client automation

The root helper's read-only `client-automation <publication-job-id>` command loads
one durable publication record and generates two YAML playbooks, an inventory
example, and instructions. It accepts only a validated publication identifier
and the disconnected role. The frontend uses the publication ID shown in the
current publication card, so a newer publication cannot change an in-progress
download request. The helper regenerates the pinned repository configuration
and checks it matches the recorded file before emitting artifacts. Runtime
serialization uses the Python standard library; Ansible is only a development
validation dependency and runs on the customer's existing control node.

Configuration embeds exact repository URLs and checks RHEL platform and signing
key prerequisites. Security updates require a regular, non-symlink repository
file with the expected SHA-256, advisory metadata for every selected repository,
and the same platform. DNF operations explicitly select only that publication's
repository IDs. Both plays use serial execution and stop on a host failure.
Cockpit never executes the generated playbooks, stores remote credentials, or
claims external playbook runs as local job evidence. AAP integration is deferred.

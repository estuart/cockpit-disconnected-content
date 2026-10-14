# Architecture

The Cockpit page uses the documented `cockpit.spawn` API to call a root-owned
helper. Each call uses an argument array and requires administrative access.
The helper reads only a root-owned configuration file at a fixed path. Browser
requests cannot choose an executable, destination directory, or arbitrary URL.

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

The disconnected importer accepts bundles only from the fixed local inbox. It
verifies the outer checksum, bounds the manifest, checks major version and
architecture, streams regular files into private staging, and independently
validates repository contents and RPM signatures. A same-filesystem rename makes
the snapshot visible only after all checks pass. Failed imports remove staging.
No tar extraction API is used to apply archive paths, ownership, permissions, or
links. Archive members are written explicitly with restrictive local permissions.

Unsigned imports require explicit administrator configuration. A provisioned
manifest signing policy remains future work. Immutable HTTP publication and
promotion of clients are separate future capabilities.

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

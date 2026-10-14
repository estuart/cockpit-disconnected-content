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

A future disconnected importer will accept the versioned transfer format, validate
it against a provisioned signing policy, reconstruct a snapshot in staging, and
publish immutable versioned URLs only after successful validation. It must reject
unsafe paths, unexpected members, incompatible formats, missing data, and untrusted
signatures. Promotion of clients is separate from publication.

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

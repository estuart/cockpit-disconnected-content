# Disconnected Content for Cockpit

A connected-side prototype for building, validating, and exporting RPM repository
snapshots through the RHEL web console. Install on a registered RHEL sync host;
repositories are selected from that host's existing configuration.

## Prototype capabilities

- Cockpit UI with repository selection, a snapshot size budget, and storage checks.
- Size preflight using DNF's declared package sizes before every sync.
- Full repository sync with original metadata and forced RPM signature checking.
- Persistent systemd jobs that continue when a browser tab is closed.
- Metadata checksums, referenced RPM checksums, trusted RPM signature validation,
  and required advisory metadata for BaseOS/AppStream.
- Versioned snapshots, uncompressed tar transfer bundles, per-file manifests,
  archive read-back verification, and SHA-256 sidecars.
- Job history, local command output, and explicit failed/interrupted states.

Import, publication, incremental transfers, signed bundle manifests, and host
patching are not implemented. This is an independently maintained prototype;
the extension does not acquire a Red Hat product support commitment by running
inside Cockpit. RHEL subscriptions and access rights remain the operator's responsibility.

## Build and test

The frontend is compiled on a build machine. Runtime hosts do not need npm.

```sh
npm ci
npm run typecheck
npm run build
python3 -m unittest discover -s tests -v
```

On a RHEL build machine with RPM build tools, GNU make, and GNU tar:

```sh
make rpm
sudo dnf install ./build/rpmbuild/RPMS/noarch/cockpit-disconnected-content-*.rpm
sudo systemctl enable --now cockpit.socket
```

The RPM contains compiled frontend assets. `make dist` creates a portable source
archive using Python; the target build host needs RPM build tools and GNU make.

Open the existing RHEL web console and select **Disconnected Content**. Use an
account authorized for administrative access. The prototype deliberately requires
administrative access for all backend calls.

## Storage configuration

The root-owned `/etc/disconnected-content/config.json` selects the local state and
content directory. Choose a filesystem sized for repository content plus exports:

```json
{
  "role": "connected",
  "state_dir": "/srv/disconnected-content",
  "reserve_gib": 5
}
```

Keep this file owned by root with mode `0644` or stricter. Let the engine create
the state directory, or precreate it owned by root with mode `0700`. Do not use a
directory writable by other users. Configure storage before the first job; moving
existing state is an administrator task, not an automatic migration.

The size check estimates declared RPM bytes plus a full export and working
allowance. It is not a storage quota and cannot predict concurrent use by other
workloads. DNF metadata caches also consume space on their configured filesystem.
Start with a small repository and a conservative snapshot budget.

## First cycle

1. Select a repository in **Repositories** and save the profile.
2. Run **Check size** and inspect the result.
3. Run **Sync snapshot**. Return later to see the persisted job result.
4. Open **Snapshots** and build a transfer bundle from a validated snapshot.
5. Open **Jobs & evidence** for the server-side archive path and digest.
6. Carry the `.tar` and matching `.tar.sha256` through the approved transfer process.

The source host's enabled repositories are preserved. Each command enables only
the profile's repositories for that invocation. The application does not export
subscription certificates, repository configuration, or local job logs.

Bundles are **unsigned** in this version. Their hashes detect content corruption;
they do not establish who produced the bundle. RPM signatures remain independently
verified against the host's trusted RPM keyring. Do not treat a transferred key
and an accompanying bundle as an independent trust anchor.

## Development and publication hygiene

Keep hostnames, addresses, SSH aliases, credentials, entitlement files, mirror
content, screenshots of real inventories, local logs, and deployment notes out
of Git. `.local/` is ignored for machine-specific working notes. Use generic
examples in issues and pull requests. Review staged content before every commit.

No open-source license grant has been selected yet. Select a license and review
third-party notices before a public release. Generated frontend bundles preserve
dependency license notices.

See [architecture](docs/architecture.md) and [prototype limitations](docs/limitations.md).

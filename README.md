# Disconnected Content for Cockpit

A prototype for building, validating, exporting, and importing RPM repository
snapshots through the RHEL web console. Install the connected role on a registered
RHEL sync host and the disconnected role on a separate RHEL system.

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
- A disconnected transfer inbox with bounded archive validation, compatibility
  checks, independent RPM signature checks, and atomic snapshot visibility.
- A GUI-configured bundle source directory with newest-first file timestamps,
  job lifecycle times, elapsed duration, and timestamped verification evidence.
- A host directory picker with folder navigation, breadcrumbs, filtering, and
  shortcuts to mounted filesystems and common transfer locations.
- Optional Apache publication with separately verified public copies, immutable
  snapshot URLs, and generated client `.repo` files and setup commands.

Incremental transfers, signed bundle manifests, and remote fleet patching are
not implemented. This is an independently maintained prototype;
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

## Connected cycle

The connected **Overview** is an operations dashboard. It shows the last
successful sync and export with relative ages and exact local timestamps, a
recommended next action, the saved selection's workflow, per-repository sync and
export status, a matching size check, recent activity, and available storage.

1. Select repositories in **Repositories**, set a budget, and save the profile.
2. Run **Check saved selection size** and inspect the timestamped result.
3. Run **Sync saved selection**. The workflow shows the operation's stage and
   elapsed time; jobs continue in a system service if the browser is closed.
4. Use **Build & verify transfer bundle** when the saved selection has a validated
   snapshot. A new sync never inherits an older snapshot's export status.
5. Open **Snapshots & exports** or **Show transfer files** for archive and checksum
   paths, creation and verification times, file availability, and detailed evidence.
6. Carry the `.tar` and matching `.tar.sha256` through the approved transfer process.
   The connected host does not claim that transfer or disconnected import succeeded.

Dashboard actions use the saved selection. Unsaved edits remain in the repository
form until saved. Sync times describe when content was captured, not whether newer
upstream packages exist. Size results are scoped to the saved selection and budget;
each sync checks capacity again. **Activity & evidence** retains detailed job logs.

Export records persist independently of the recent 50 jobs returned to the UI.
Older successful export jobs are migrated without changing their export dates;
legacy bundles require an explicit verification before being shown ready. The
dashboard checks archive attributes and the checksum sidecar without hashing
large archives every poll. Missing or changed files require verification or a
rebuild. Explicit verification checks archive contents, snapshot identity, and
the archive digest against its sidecar; it updates verification time while
preserving the original export time.

The source host's enabled repositories are preserved. Each command enables only
the profile's repositories for that invocation. The application does not export
subscription certificates, repository configuration, or local job logs.

Bundles are **unsigned** in this version. Their hashes detect content corruption;
they do not establish who produced the bundle. RPM signatures remain independently
verified against the host's trusted RPM keyring. Do not treat a transferred key
and an accompanying bundle as an independent trust anchor.

## Disconnected import

Install the application and its runtime dependencies before isolating the host,
or provide them through an approved offline bootstrap repository. The importer
does not download packages or require access to the sync host.

Configure the root-owned `/etc/disconnected-content/config.json` for this role:

```json
{
  "role": "disconnected",
  "state_dir": "/var/lib/disconnected-content",
  "reserve_gib": 5,
  "max_import_gib": 200,
  "allow_unsigned_imports": false
}
```

This version produces unsigned bundles. Import is rejected by default. For an
explicitly approved prototype test, set `allow_unsigned_imports` to `true` as an
administrator. This does not authenticate the manifest; RPM signatures are still
required and validated against the host's independently provisioned RPM keyring.

1. Open **Incoming bundles**, choose **Browse folders**, and navigate to the
   transfer folder on the disconnected host. Mounted drives appear under
   **Mounted filesystems**; **Quick locations** includes the saved source,
   filesystem root, and existing common mount directories. Use breadcrumbs,
   **Up one folder**, and the folder filter to navigate, then choose
   **Use this folder**. You can also enter an absolute path directly.
2. Choose **Save & scan** to persist the source. Stage the `.tar` and matching
   `.tar.sha256` there using the approved transfer process, then **Scan now**.
   The source must be a real directory without symlink components. Only that
   directory is scanned; subdirectories are not searched. Browsing and choosing
   a folder do not change the saved source until **Save & scan** is selected.
   Mounting media and transferring files are currently administrator tasks
   outside the application.
3. Choose **Import bundle**. The application never imports automatically.
4. Follow the job under **Jobs & evidence**. A failed import leaves no usable
   snapshot and removes its private extraction staging directory.
5. Open **Snapshots** for validation results and local `file://` repository URLs.

The importer checks the outer checksum, manifest schema, size/file-count limits,
safe paths, file checksums, RHEL major version, architecture, repository metadata,
and trusted RPM signatures. Archive ownership, modes, links, special files, sparse
files, duplicate members, and unexpected paths are not accepted or applied.
An identical bundle can be imported again without duplicating or replacing its
snapshot. Publishing and client rollout are explicit subsequent steps.

The source directory is separate from managed snapshot storage. The GUI saves
only `incoming_dir` and its settings timestamp in the root-owned configuration;
it cannot change the role, state directory, size limits, or unsigned-import policy.
Saving does not create, move, delete, or change permissions on source files.
It requires administrative access and is blocked while a job is active. Each
import records its selected source directory in the job. Missing media is shown
as unavailable without hiding existing snapshots or job history.

Incoming bundles are sorted by filesystem modification time, newest first.
Copying may preserve this time, so it is not an arrival timestamp. Jobs show
queued, started, last-updated, and finished times where recorded, plus duration.
Successful archive checks record a verification timestamp; identical reimports
record when the archive matched the existing snapshot. Imported snapshots also
show their import time and source bundle creation time. Dates include the
browser's local timezone; hovering a timestamp reveals its original ISO value.
Older records retain the evidence they actually captured without invented dates.

## Publish and configure client hosts

Install the optional `cockpit-disconnected-content-publisher` RPM and its Apache
dependencies on the disconnected host before isolation or from an offline
bootstrap repository. Connected sync hosts do not need this subpackage.

1. Open **Publish & clients**, enter the server URL clients can reach, such as
   `http://mirror.example.org`, and select **Save server URL**.
2. Select an imported, validated snapshot and choose **Publish snapshot**. This
   creates a separate copy of indexed repository metadata and RPMs under
   `/var/www/disconnected-content/public/snapshots/<snapshot-id>/`, verifies it,
   applies SELinux labels, checks Apache configuration, and starts the server.
3. Permit inbound TCP port 80 from approved client networks through your firewall
   policy. Publication does not modify firewall rules or network isolation.
4. Download the displayed `.repo` file and install it on compatible client hosts
   as `/etc/yum.repos.d/disconnected-content.repo`. Review the displayed setup
   commands to refresh metadata, inspect security advisories, and preview updates.

The GUI shows the current publication and timestamp, complete configuration, and
client commands. Package GPG checks stay enabled, with keys provisioned and
trusted independently on clients. Original repository metadata is not signed by
this prototype (`repo_gpgcheck=0`). The default Apache setup provides HTTP; HTTPS
and certificate trust require separately configured TLS. Entering an HTTPS URL
does not configure the server's TLS listener.

Every generated repository URL pins a specific published snapshot. Publishing
new content updates `/disconnected-content/client.repo`, while older snapshot
URLs remain usable. Existing clients advance only when an administrator installs
the newer configuration. This avoids changing repository content during an
in-progress package transaction. Switching repositories does not undo installed
updates. HTTP content is readable by hosts allowed through the network policy;
web-client authentication is not included in the initial publisher.

Publication copies repository content rather than exposing private engine state.
It requires additional storage for the public copy; the reserve is checked on
the publication filesystem. Logs, jobs, inbox archives, snapshot state files, and
unindexed files are excluded. Private staging is outside the Apache alias.

The **Ansible client automation** section provides two downloadable playbooks,
an example inventory, and run instructions. Configure a canary client's pinned
repositories, preview security updates with `--check --diff`, then apply during
a maintenance window. The update playbook requires the exact paired repository
file and advisory metadata, selects only its publication's repository IDs, runs
one host at a time, and stops on failure. Neither playbook reboots clients.

Run the downloads from your existing Ansible Core 2.16+ control node. Cockpit does
not store client credentials or execute Ansible. See [client automation](docs/automation-options.md)
for prerequisites, commands, and the later AAP integration direction.

## Development and publication hygiene

Keep hostnames, addresses, SSH aliases, credentials, entitlement files, mirror
content, screenshots of real inventories, local logs, and deployment notes out
of Git. `.local/` is ignored for machine-specific working notes. Use generic
examples in issues and pull requests. Review staged content before every commit.

No open-source license grant has been selected yet. Select a license and review
third-party notices before a public release. Generated frontend bundles preserve
dependency license notices.

See [architecture](docs/architecture.md) and [prototype limitations](docs/limitations.md).

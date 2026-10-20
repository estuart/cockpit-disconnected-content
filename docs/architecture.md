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

## Connected operations dashboard

Connected status derives its workflow from the saved repository IDs, validated
snapshots, durable export records, and relevant job attempts. The latest global
sync and export remain visible independently of the latest snapshot matching the
saved selection. Per-repository history can show older available content without
implying that a newly saved selection has been synced. Size results match both
repository selection and budget. Dashboard sync/preflight actions do not save
unsaved frontend drafts.

`export-records/<snapshot-id>.json` retains the latest export and verification
for each archive, independently of the UI's 50-job history window. Export jobs
persist the record after archive verification and promotion. Existing successful
export jobs backfill missing records under the engine lock; their historical
export dates are retained and their file fingerprint remains unknown until an
explicit verification. Migration does not rehash or certify files on a status poll.

Availability inspects regular-file presence, size, checksum-sidecar contents, and
the recorded size/mtime/ctime/inode/device fingerprint. It is an inexpensive
change detector, not a fresh content checksum. Verification streams every archive
member, checks the expected snapshot ID and archive SHA-256 against its sidecar
and prior export record, and records a new fingerprint without advancing export
time. Missing files, changed attributes, old unverifiable history, and failed
integrity checks are surfaced as actions. Export and checksum promotion are not
one filesystem transaction; an interrupted replacement is detected by mismatched
records/files and must be rebuilt. Physical transfer and remote import are never
inferred from export completion.

## Disconnected operations dashboard

Disconnected status derives its workflow from saved source settings, bounded
checksum-sidecar reads, validated imported snapshots, durable publication state,
local publication availability, and relevant import/publication attempts. Import
and publication dates are independent; a newer import never inherits the older
publication's identity. Source bundle creation time is shown separately from
local import time. Existing published content remains usable if transfer media
is later unmounted or new imports are disabled by policy.

`import-records/<job-id>.json` retains successful source-file checks, including
source path, archive digest, original import time, check time, and file attributes.
The dashboard does not hash archives on each poll. A claimed sidecar checksum
matching prior imported content is explicitly distinguished from a source file
whose attributes match a completed import check. Changed source files and prior
failed imports remain review items; a known checksum cannot hide a failed check
of that file. Every explicit import/recheck still verifies the archive digest,
and source attribute changes during verification prevent its new evidence from
being recorded. Rechecking existing content does not advance its import date.

Publication availability checks local regular repository indexes, exact client
configuration, publisher installation, and Apache activity. It does not probe
client network paths or revalidate every package on a status poll. Missing files,
stopped service, a different latest import, or a changed saved server URL direct
operators to review publication. Publication remains an explicit action.

Client setup is a dedicated tab; downloads are pinned to the recorded publication.
No client is marked configured or updated from a download, and the workflow's
last step remains unconfirmed. The application does not execute remote Ansible,
store client credentials, or add AAP integration.

## Repository signing-key trust

Administrative key review uses a bounded ASCII-armored public certificate and an
isolated offline GnuPG home. Review records expire after 15 minutes; approval
rechecks the same bytes and full fingerprint while holding the engine lock and
rejects changes during active jobs. Approved key sets are stored per repository
in private state, separate from the host RPM database. RPM signature validation
uses temporary key databases for explicitly approved repositories. Snapshots pin
key material and approval evidence. Publication validates source and staged RPMs
against those pinned keys and includes only the reviewed public keys in its web
copy. Existing snapshot URLs cannot silently acquire different key material.
Client configuration references local fingerprint-named keys. Downloaded Ansible
configuration embeds exact public keys and uses full fingerprint verification before enrollment;
security updates check the same pinned key files and keep DNF GPG verification.
Keys from unsigned transfer bundles never become trusted automatically.

## Bundled public-key candidates

Exports read expanded DNF gpgkey sources and collect bounded public certificate
blocks. A packet-type filter rejects secret, compressed, or encrypted packets;
GnuPG remains responsible for fingerprint and validity checks at explicit review.
The optional v1 signing_keys manifest maps repository IDs to public-key file paths
and SHA-256 values. Old manifests remain valid. No source URL or approval evidence
is exported by key collection. Bundle-key inspection verifies the complete archive
and sidecar before staging candidate bytes in private state. Candidate review binds
to those bytes, repository ID, and archive digest. The existing expiring review and
explicit confirmation remain the only route to approval. Public-key candidates
never enter import validation, publication, or client trust automatically.

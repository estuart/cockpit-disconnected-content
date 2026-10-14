# Prototype validation

These checks describe the tested boundary, not a general RHEL support statement.
Deployment-specific identifiers, addresses, logs, and entitlement configuration
are intentionally excluded.

## Initial connected-host validation

- RHEL 10.2 x86_64 with Cockpit 356.2, SELinux enforcing.
- Built and installed a noarch RPM containing compiled frontend assets and the
  systemd worker template; no frontend build tooling required at runtime.
- Queried a configured, entitled High Availability repository without persistently
  enabling it for the operating system.
- Preflight reported 146 RPMs totaling 89,883,382 declared bytes (about 85.7 MiB).
- Synced all 146 RPMs and original repository metadata from the CDN.
- Validated metadata references/checksums, RPM checksums, and trusted signatures.
- Preserved 42 advisory records, including 11 security advisories. Advisory presence
  is distinct from applicability to the sync host's installed packages.
- Created a 91,033,600-byte tar archive and SHA-256 sidecar; streamed all 156
  archived content files through manifest verification before marking it complete.
- DNF successfully queried the local mirror without installing or updating host
  packages. A separate disconnected-client installation has not yet been tested.
- Browser smoke test loaded the installed extension through Cockpit, enabled normal
  administrative access, read live backend state, and navigated repository screens.
- Browser cycle test started a real CDN sync, closed the page while it ran,
  reopened the page, observed success, then exported the newly created snapshot.
- Normal web-console authentication was preserved. An isolated loopback test
  session was used for automation; deployment details and screenshots are local-only.

## Automated checks

Backend tests cover valid/empty-advisory content, missing and corrupted RPMs,
corrupted metadata, required advisory metadata, rejected signature failures and
unsigned RPMs, unsafe paths/symlinks, profile validation, role restrictions,
single-job locking, failed worker launch, interrupted-job reconciliation, replay
prevention, budget enforcement before download, failed-job state, archive round
trip, archive corruption, and archive path traversal.

TypeScript checking and a production frontend build run alongside the backend
tests in GitHub Actions. The browser checks require an explicitly supplied,
authenticated Cockpit test session; they are not simulated by the CI unit tests.

## Disconnected import and security-update validation

Validated on a separate RHEL 10.2 x86_64 VM with the RHEL Cockpit 356 series and
SELinux enforcing. Dependencies were prepared before isolation. The application
RPM and transfer archive were then copied over the retained management channel.

- Removed IPv4/IPv6 default routes and applied a persistent guest firewall that
  permits loopback and responses to inbound management connections, while
  rejecting guest-initiated network traffic.
- Disabled remote repositories and confirmed that public HTTPS, the Red Hat CDN,
  and an internal subscription content server were unreachable before testing.
- Installed the application RPM with all repositories disabled.
- Explicitly enabled unsigned prototype imports on the test host; this is not
  the default configuration and does not authenticate a bundle's provenance.
- Matched the transferred archive's SHA-256 and 91,033,600-byte size.
- Rejected a deliberately corrupted archive, leaving zero visible snapshots.
- Imported the valid archive through Cockpit, verifying 156 content files,
  repository metadata, and signatures of all 146 RPMs before exposing a snapshot.
- Reimported the identical archive without duplicating the snapshot.
- Cleared DNF caches and configured only the imported local `file://` repository.
- Installed `pacemaker-doc-3.0.0-5.el10.noarch` from that repository.
- DNF reported security advisory `RHSA-2026:40833` for the installed package.
- `dnf upgrade --security pacemaker-doc` updated it to
  `3.0.1-5.el10_2.1.noarch`, using only the imported content.
- DNF then reported no outstanding security advisories applicable to that test
  package from the imported snapshot. This is not a statement about all RHEL CVEs.
- Confirmed public HTTPS and the internal subscription server remained blocked
  after the update. SSH/Cockpit management access was retained.
- Rebooted the test VM and confirmed a changed boot ID, active isolation service,
  no IPv4/IPv6 default routes, preserved imported snapshot and installed package,
  and continued rejection of public HTTPS, CDN, and internal content-server access.

This is logical disconnection with inbound management access retained, rather
than a physical air gap. It proves a transferred mirror can be imported and used for an offline
advisory-driven package update. The package selected was documentation with no
additional dependencies. Full BaseOS/AppStream patching, cross-repository
dependency closure, HTTP distribution, and fleet operations remain separate tests.

The browser harness attributes JavaScript exceptions by source URL. On the test
image, the stock Cockpit system overview emitted a background `not-found`
exception; the extension's import/navigation checks completed without extension
exceptions. Browser artifacts and machine-specific diagnostics remain local-only.

## Still to validate

HTTP publication, separate network client consumption, BaseOS/AppStream at full
scale, other RHEL/Cockpit releases, FIPS
mode, manifest signing and trust provisioning, storage exhaustion under concurrent
workloads, interrupted-worker recovery across a reboot, and upgrade/uninstallation behavior with
active jobs.

## Import source workflow and chronology

Version 0.3.0 adds source-directory configuration and timestamped evidence.

- Forty backend tests pass, including source persistence without policy changes,
  invalid/missing/symlink paths, shallow newest-first scans, symlink/nonregular
  checksum handling, missing-media recovery, active-job settings protection,
  imports from an external directory, and lifecycle/verification timestamps.
- TypeScript checking and the production frontend build pass.
- Built an updated noarch RPM on the connected RHEL host and installed it on the
  disconnected VM with all repositories disabled.
- Through Cockpit, rejected a relative source path without changing the saved
  source, saved a separate staging directory, and confirmed persistence after a
  full browser reload. Bundle modification times were visible with timezone labels.
- Started an import from that directory through the GUI. The identical archive
  matched the existing validated snapshot without creating a duplicate. The job
  displayed queued/start/update/finish times, duration, its recorded source path,
  archive SHA-256, and when the match was checked.
- Restored the default inbox after the browser test. Existing snapshots and
  historical jobs remained accessible; earlier records were not backfilled with
  invented verification timestamps.

This GUI check used a local staging directory, not physical removable media.
Mounting media and moving bundle files remain administrator tasks.

## Host directory picker

Version 0.4.0 adds a directory picker to the disconnected transfer inbox.

- All 46 backend tests pass, along with TypeScript checking and the production
  frontend build. New cases cover real-directory-only listings, sorting, parent
  navigation, invalid paths, role restrictions, mount-table escape decoding,
  mount refresh, saved sources on mounted filesystems, bounded listings, and
  operation without mount metadata.
- Built and installed the updated RPM on the disconnected RHEL VM with all
  package repositories disabled.
- Created a temporary loopback filesystem, copied a valid transfer bundle and
  checksum into a folder whose name contained spaces, and remounted it read-only.
- Through Cockpit, discovered the mount shortcut and navigated to its transfer
  folder without typing the source path. Verified filtering, parent navigation,
  cancellation, and folder selection without changing the saved source.
- Save & scan persisted the chosen path, confirmed by a fresh backend process.
  An import started through the GUI successfully read that source and matched
  the already validated snapshot; it did not duplicate the snapshot.
- Restored the default inbox, removed the temporary test mount and image, and
  removed the loopback test session. Normal web-console authentication was
  preserved throughout.

This validates browsing a mounted read-only filesystem, not physical USB device
detection, mounting, or hot-unplug behavior. Mounting transfer media remains an
administrator task. Browser artifacts and host-specific details are local-only.

## HTTP publication and isolated client consumption

Version 0.5.0 adds an optional Apache publisher and generated client configuration.

- All 60 backend tests pass, together with TypeScript checking and the production
  build. Publication tests cover role/package prerequisites, pinned URLs and
  package trust checks, settings validation, source/copy verification, indexed
  content only, copy tampering, storage failure, server failure, cleanup,
  preservation of existing publications, and reuse without replacing public data.
- Built a main RPM and optional publisher RPM. Prepared Apache dependencies on
  the connected build host, checked dependency signatures on both hosts, and
  installed from a transferred file repository while outbound access stayed blocked.
- Published the imported 146-RPM repository through Cockpit. Verified server
  status, current-publication timestamps, pinned client configuration, setup
  commands, and the browser's `.repo` download. SELinux remained enforcing.
- Created a separate network namespace and RPM installroot for the client test.
  It had no default route and could reach the publisher through a specific route.
  It downloaded the exact generated configuration over HTTP, with `gpgcheck=1`
  and independently provisioned Red Hat RPM keys.
- Using only the HTTP repository, installed `pacemaker-doc-3.0.0-5.el10.noarch`,
  observed `RHSA-2026:40833`, and applied its security update to
  `pacemaker-doc-3.0.1-5.el10_2.1.noarch`. The publisher's own package state was
  not used as the client installroot.
- Confirmed private job/inbox/staging/state paths were denied over HTTP and
  directory listings were disabled. GET/HEAD downloads succeeded while POST,
  PUT, and DELETE requests were denied. The client could not reach the internet
  before or after the transaction.
- Removed the temporary client namespace, link, and its runtime-only firewall
  allowance. HTTP remains permitted from the approved internal client network;
  outbound isolation is preserved. Temporary browser access was removed.

This validates a network-isolated installroot client, not a second complete
client VM or full BaseOS/AppStream patching. TLS, HTTP client authentication,
client rollout automation, reboot policy, cross-repository dependency closure,
and production-scale retention remain further work. Public repository content
and the current client configuration remain available for inspection.

## Downloadable Ansible client playbooks

Version 0.6.0 adds paired, publication-pinned repository and security-update
playbooks, an example inventory, and run instructions.

- All 64 backend tests pass, along with TypeScript checking and the production
  build. New coverage includes durable-publication selection, role and identifier
  restrictions, missing advisory metadata, and rejection of mismatched or injected
  publication values.
- Parsed generated YAML with Ansible's loader and ran `ansible-playbook
  --syntax-check` for both playbooks using Ansible Core 2.16.16. CI additionally
  checks generated playbooks with Ansible Core 2.19.
- Deployed both updated RPMs from local files on the isolated publisher. Browser
  checks downloaded all four artifacts, compared their exact contents with the
  durable publication, verified filenames and run commands, opened previews,
  and observed no extension JavaScript exceptions.
- Ran the actual generated playbooks in a disposable RHEL 10.2 UBI client
  container on the connected build host. The container had no default IPv4 or
  IPv6 route, a specific route to the publisher, and failed internet reachability
  checks before and after the transaction. Its test-only Ansible controller used
  a local connection inside that container; no SSH inventory or remote credentials
  were used for this execution test.
- Confirmed the security playbook rejected missing configuration. Repository
  check mode predicted the file change without writing it; applying configured
  the repositories and refreshed their HTTP metadata. Re-running configuration
  reported no change. A modified configuration was rejected before updating.
- Seeded a signed `pacemaker-doc-3.0.0-5.el10.noarch` from the published repository.
  Security check mode predicted an update and left that installed version intact.
  Applying the playbook installed `pacemaker-doc-3.0.1-5.el10_2.1.noarch`; a second
  security run reported no change. Other repositories were excluded from the
  transaction, and RPM signature checking remained enabled.
- Removed the disposable client container, temporary HTTP firewall allowance,
  browser session, and local tunnel. The normal publisher and Cockpit services
  remain available with outbound isolation preserved.

This validates the generated tasks and a real security transaction in an isolated
RHEL container. A separate full client VM, customer SSH/become policies, broad
BaseOS/AppStream coverage, multi-host failure handling, reboot orchestration,
and AAP job launch remain outside the tested boundary.

## Connected operations dashboard

Version 0.7.0 adds the connected dashboard, visible workflow, per-repository
status, and combined snapshots/export history.

- All 83 backend tests pass on the development host and RHEL build host, together
  with TypeScript checks, production build, and generated Ansible syntax checks.
  Workflow tests cover initial selection, unsynced or changed selections,
  historical exports, old matching snapshots, new snapshots awaiting export,
  missing/changed/symlinked transfer files, failed and running operations,
  selection/budget-scoped size results, and export history beyond 50 recent jobs.
- Browser checks render ten states generated by the actual backend model,
  exercise sync/export/verification actions, confirm dashboard actions do not
  save repository drafts, inspect transfer files, and check narrow viewports.
- Upgraded an existing connected installation from staged RPMs, preserving its
  root configuration, saved selection and budget, and prior snapshots/archives.
  Existing export jobs migrated to durable records with their original dates;
  migrated files required re-verification before being shown ready for transfer.
- Through the deployed Cockpit UI, re-verified an existing bundle and confirmed
  that its export timestamp stayed unchanged. Then ran a size check and a fresh
  real repository sync using the saved selection. The dashboard showed the new
  snapshot awaiting export while retaining the prior successful export time.
- Started archive export through the dashboard and confirmed the transition to
  ready only after verification completed. The combined history showed both
  archive and sidecar paths, export time, verification time, and availability.
  No extension JavaScript exceptions occurred.
- Confirmed that old snapshots remained present and the saved selection/budget
  were unchanged. Temporary loopback browser access and its SSH tunnel were
  removed; normal Cockpit access remains available.

Availability is an inexpensive filesystem change check, not a fresh archive hash
on every poll. Transfer and disconnected import remain unconfirmed by this host.
The live UI cycle used an existing small configured repository, not a full
BaseOS/AppStream mirror or a production-scale performance test.

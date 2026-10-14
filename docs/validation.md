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

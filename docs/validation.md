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

## Still to validate

Disconnected import/publication, independent client package installation and errata
applicability, BaseOS/AppStream at full scale, other RHEL/Cockpit releases, FIPS
mode, manifest signing and trust provisioning, storage exhaustion under concurrent
workloads, actual host reboot recovery, and upgrade/uninstallation behavior with
active jobs.

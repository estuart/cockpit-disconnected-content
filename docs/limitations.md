# Prototype boundaries

- Initial deployment target: RHEL 10 x86_64. Other RHEL/Cockpit combinations require
  their own validation before being advertised as supported.
- Connected role only. No disconnected machine is provisioned by this project.
- The profile mirrors existing repositories using the host's existing release and
  entitlement configuration. Cross-major mirroring, EUS selection, architectures,
  and release-policy management are not implemented.
- A complete repository is mirrored; package filtering and newest-only sync are
  deliberately excluded to avoid inconsistent original metadata.
- Each cycle is a new full snapshot. Resume, deduplication, deltas, scheduled jobs,
  automatic cleanup, and retention policies are future work.
- Storage preflight is an estimate, not a hard quota. Runtime reserve monitoring
  applies during the download command; other validation/export stages preflight
  space but are not quota-enforced. Other workloads can consume disk concurrently.
- Querying sizes can refresh DNF caches. It uses the host's DNF cache location.
- Empty advisory results do not prove missing metadata or complete CVE coverage.
- RPM signing keys must already be trusted in the host's RPM database. No automatic
  key enrollment is added by this application.
- Bundle checksums establish integrity, not provenance. Signed manifests, trust
  provisioning, and FIPS-mode validation remain future release requirements.
- Importer hardening and hostile-input resource limits require additional work
  before accepting bundles from another machine. Verification here is for bundles
  produced by the local prototype; the UI does not accept arbitrary archive paths.
- Failed jobs retain their partial snapshot/archive files. Root can inspect and
  remove them when no related job is active. Never remove an active job's files.
- The helper currently requires full administrative access. Limited operator
  authorization and a D-Bus interface are future work.
- No promise of system rollback. Repository selection does not revert installed
  updates, and a complete host recovery plan is outside this prototype.

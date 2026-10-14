# Prototype boundaries

- Initial deployment target: RHEL 10 x86_64. Other RHEL/Cockpit combinations require
  their own validation before being advertised as supported.
- Connected sync/export and disconnected import roles are implemented. The
  application does not provision VMs or change network isolation policies.
- HTTP publication requires the optional publisher package and a separately
  permitted inbound network path. Default HTTP has no transport encryption or
  web-client authentication. TLS, client certificate policy, and server access
  controls require administrator configuration.
- Clients are pinned to published snapshots and must receive a new `.repo` file
  to advance. Publishing does not initiate remote updates or Ansible jobs. A
  public copy consumes additional space; retention and deletion remain manual.
- Downloadable Ansible playbooks run manually on an existing control node. AAP
  integration, credential storage, external job tracking, reboot orchestration,
  and automatic rollback are not implemented. Security filtering depends on the
  mirrored advisory coverage and does not establish complete CVE remediation.
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
- Imports are disabled by default because bundles are unsigned. An explicit
  administrator opt-in is required for this prototype. Archive bounds and safe
  extraction reduce input risk; they do not authenticate metadata or prevent a
  trusted RPM's old version from being replayed. Signing and release policy remain
  necessary before a production release.
- The importer requires matching RHEL major version/architecture and an existing
  trusted RPM keyring. It never downloads missing keys or dependencies.
- The GUI selects an existing directory on the server. It does not mount USB
  media, browse a workstation's files, or upload bundles. Scans are shallow and
  imports are explicit. A mounted path is not bound to a particular device UUID;
  operators must ensure the expected media is mounted there.
- The directory picker shows real subdirectories only, omitting symlinks. It
  lists at most 500 child folders at a time. Mount shortcuts are drawn from
  mounted device filesystems; generic folder navigation remains available for
  other mount types and unusual locations. Refresh updates mounted locations.
- File modification times may be preserved during transfer and do not establish
  arrival time or content freshness. Source creation times are unsigned manifest
  data. Historical jobs may lack timestamps introduced by later engine versions.
- Failed sync/export jobs retain partial content. Failed imports remove private
  staging and do not expose a snapshot. Root can inspect and
  remove them when no related job is active. Never remove an active job's files.
- The helper currently requires full administrative access. Limited operator
  authorization and a D-Bus interface are future work.
- No promise of system rollback. Repository selection does not revert installed
  updates, and a complete host recovery plan is outside this prototype.

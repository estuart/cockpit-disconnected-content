// Types mirror the helper's JSON; the helper remains the authorization and validation boundary.
export type Repo = { id: string; name: string; enabled: boolean };
export type Plan = { packages: number; package_bytes: number; required_free: number;
  free_bytes: number; within_budget: boolean; enough_space: boolean };
export type Job = { id: string; operation: string; status: string; stage: string; step?: string; created: string;
  signature_failure?: { code: string; message: string; repository_id: string; diagnostics: string };
  started?: string; finished?: string; updated?: string; bundle_name?: string; incoming_dir?: string;
  profile?: { repositories: string[]; max_gib: number };
  snapshot_id?: string; plan?: Plan; result?: { path?: string; sha256?: string; bytes?: number;
  files_verified?: number; authenticated?: boolean; verified_at?: string; checked_at?: string;
  source_archive_sha256?: string; already_imported?: boolean; published_at?: string; config_url?: string } };
export type SigningKey = { fingerprint: string; fingerprints: string[]; user_ids: string[]; expires: string | null;
  sha256: string; public_key: string; approved_at?: string; source?: string };
export type KeyReview = SigningKey & { review_id: string; repository_id: string; reviewed_at: string };
export type Publication = { snapshot_id: string; published_at: string; verified_at: string; base_url: string;
  job_id: string; config_url: string; client_config: string; bytes: number; os: { major: string; arch: string };
  repositories: Record<string, { packages: number; metadata_types: string[]; signing_keys?: SigningKey[] }> };
export type Automation = { snapshot_id: string; has_updateinfo: boolean; files: Record<string, string> };
export type ExportRecord = { snapshot_id: string; exported_at: string | null; verified_at?: string; bytes: number;
  path: string; checksum_path: string; sha256: string; bundled_signing_keys?: number;
  state: 'available' | 'missing' | 'changed' | 'unverified'; reason: string };
export type Snapshot = { id: string; created: string; status: string; package_bytes: number;
  imported?: boolean; source_created?: string; verified_at?: string; export?: ExportRecord | null;
  repositories: Record<string, { packages: number; package_bytes?: number; metadata_types: string[] }> };
export type Connected = { phase: 'select' | 'sync' | 'export' | 'verify' | 'ready' | 'failed' | 'running';
  action: string | null; reason: string; active: Job | null; attention_job: Job | null;
  latest_sync: Snapshot | null; selection_snapshot: Snapshot | null; latest_export: ExportRecord | null;
  selection_changed: boolean; plan_job: Job | null;
  selected_repositories: { id: string; snapshot_id: string | null; synced_at: string | null;
    repository: { packages: number; package_bytes: number } | null; export: ExportRecord | null }[] };
export type IncomingBundle = { name: string; bytes: number; modified: string; has_checksum: boolean;
  state?: 'awaiting_import' | 'imported' | 'known' | 'changed' | 'blocked'; reason?: string; snapshot_id?: string | null };
export type Disconnected = { phase: 'source' | 'waiting' | 'import_policy' | 'import' | 'publisher_setup' | 'server_url' | 'publish' | 'clients' | 'failed' | 'running';
  reason: string; active: Job | null; attention_job: Job | null; latest_import: Snapshot | null;
  publication_matches_latest: boolean; publication_serving: boolean; server_url_changed: boolean;
  target_snapshot_id: string | null; pending_bundles: number; known_bundles: number; client_application: 'unconfirmed';
  repositories: { id: string; snapshot_id: string; imported_at: string; repository: { packages: number; metadata_types: string[] };
    publication_state: 'current' | 'older' | 'not_published' }[] };
export type State = { version: string; role: string; os: { name: string; major: string; arch: string };
  storage: { path: string; free: number; total: number; reserve: number };
  repositories: Repo[]; profile: { repositories: string[]; max_gib: number };
  jobs: Job[]; snapshots: Snapshot[]; incoming: IncomingBundle[];
  signing_key_files?: string[]; trusted_signing_keys?: { repository_id: string; signing_keys: SigningKey[] }[];
  import_source: { path: string; available: boolean; error?: string; scanned_at: string; updated_at?: string } | null;
  publication: { installed: boolean; server_active: boolean; base_url: string; current: Publication | null;
    availability: { state: 'not_published' | 'available' | 'unavailable'; reason: string } } | null;
  connected: Connected | null; disconnected: Disconnected | null;
  export_settings?: { path: string; default: boolean; available: boolean; error?: string; free?: number; updated_at?: string } | null;
  capabilities: { allow_unsigned_imports: boolean } };
export type DirectoryListing = { path: string; parent: string | null; truncated: boolean;
  directories: { name: string; path: string }[];
  locations: { label: string; path: string; kind: 'folder' | 'mount' }[] };
// Manifest summary read before verification; display only.
export type BundleInfo = { bundle_name: string; bytes: number; snapshot_id: string; created: string | null;
  os: { major: string; arch: string }; matches_host: boolean; verified: false;
  repositories: Record<string, { packages: number | null; package_bytes: number | null }>;
  signing_keys: Record<string, number> };
export type KeyCandidate = { candidate_id: string; repository_id: string; sha256: string; approved: boolean;
  fingerprint: string | null; user_ids?: string[]; expires?: string | null; host_trusted: boolean; error?: string };
export type KeyScan = { bundle_name: string; bundle_sha256: string; keys: number; candidates: KeyCandidate[] };

type CockpitProcess = Promise<string> & { input: (data: string) => void };
declare global { interface Window { cockpit: {
  spawn: (args: string[], opts: Record<string, unknown>) => CockpitProcess;
} } }

const helper = '/usr/libexec/disconnected-content';
export function call<T>(args: string[], input?: unknown): Promise<T> {
  const process = window.cockpit.spawn([helper, ...args], { superuser: 'require', err: 'message' });
  if (input !== undefined) process.input(JSON.stringify(input));
  return Promise.resolve(process).then(raw => JSON.parse(raw));
}
export function errorText(error: unknown) { return error instanceof Error ? error.message : String(error); }

export function bytes(value: number) {
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${(value / 1024 ** 3).toFixed(1)} GiB`;
}
export function plural(count: number, one: string, many = `${one}s`) { return `${count.toLocaleString()} ${count === 1 ? one : many}`; }
export function groupFingerprint(value: string) { return value.match(/.{1,4}/g)?.join(' ') ?? value; }
export function shortDate(value?: string | null) {
  if (!value || !Number.isFinite(Date.parse(value))) return 'Unknown date';
  const date = new Date(value);
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric',
    ...(date.getFullYear() !== new Date().getFullYear() ? { year: 'numeric' } : {}) });
}
/** Date name for a snapshot, with the time added when another snapshot shares that date. */
export function snapshotLabel(snapshot: { id: string; created: string } | null | undefined, all: { id: string; created: string }[]) {
  if (!snapshot) return 'none';
  const day = shortDate(snapshot.created);
  if (!all.some(other => other.id !== snapshot.id && shortDate(other.created) === day)) return day;
  return `${day}, ${new Date(snapshot.created).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`;
}
export const operationNames: Record<string, string> = { preflight: 'Size check', sync: 'Sync', 'sync-export': 'Sync & build bundle',
  export: 'Build bundle', 'verify-export': 'Verify bundle', import: 'Import', publish: 'Publish' };

function blobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href = url; link.download = filename; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function downloadFile(content: string, filename: string) {
  blobDownload(new Blob([content], { type: 'text/plain;charset=utf-8' }), filename);
}

// Minimal uncompressed ZIP so the Ansible files arrive together; no third-party dependency.
const crcTable = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(data: Uint8Array) {
  let crc = 0xFFFFFFFF;
  for (const byte of data) crc = crcTable[(crc ^ byte) & 0xFF] ^ (crc >>> 8);
  return (crc ^ 0xFFFFFFFF) >>> 0;
}
export function zipFiles(files: Record<string, string>) {
  const encoder = new TextEncoder();
  const local: Uint8Array[] = [], central: Uint8Array[] = [];
  let offset = 0;
  for (const [name, content] of Object.entries(files)) {
    const path = encoder.encode(name), data = encoder.encode(content), crc = crc32(data);
    const header = new DataView(new ArrayBuffer(30));
    [[0, 0x04034b50, 4], [4, 20, 2], [8, 0, 2], [10, 0, 2], [12, 0x21, 2], [14, crc, 4], [18, data.length, 4],
      [22, data.length, 4], [26, path.length, 2]].forEach(([at, value, size]) =>
      size === 4 ? header.setUint32(at, value, true) : header.setUint16(at, value, true));
    const entry = new DataView(new ArrayBuffer(46));
    [[0, 0x02014b50, 4], [4, 0x0314, 2], [6, 20, 2], [10, 0, 2], [12, 0, 2], [14, 0x21, 2], [16, crc, 4], [20, data.length, 4],
      [24, data.length, 4], [28, path.length, 2], [38, 0o100644 << 16 >>> 0, 4], [42, offset, 4]].forEach(([at, value, size]) =>
      size === 4 ? entry.setUint32(at, value, true) : entry.setUint16(at, value, true));
    local.push(new Uint8Array(header.buffer), path, data);
    central.push(new Uint8Array(entry.buffer), path);
    offset += 30 + path.length + data.length;
  }
  const size = central.reduce((total, part) => total + part.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  const count = Object.keys(files).length;
  [[0, 0x06054b50, 4], [8, count, 2], [10, count, 2], [12, size, 4], [16, offset, 4]].forEach(([at, value, bytes]) =>
    bytes === 4 ? end.setUint32(at, value, true) : end.setUint16(at, value, true));
  return new Blob([...local, ...central, new Uint8Array(end.buffer)] as BlobPart[], { type: 'application/zip' });
}
export function downloadZip(files: Record<string, string>, filename: string) { blobDownload(zipFiles(files), filename); }

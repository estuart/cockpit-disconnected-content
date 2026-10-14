import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Alert, Badge, Button, Card, CardBody, CardTitle, Checkbox, Label, Modal, ModalBody, ModalFooter, ModalHeader, Spinner,
  Tab, Tabs, TabTitleText, TextInput, Title } from '@patternfly/react-core';
import '@patternfly/patternfly/patternfly.css';
import './style.css';

type Repo = { id: string; name: string; enabled: boolean };
type Plan = { packages: number; package_bytes: number; required_free: number;
  free_bytes: number; within_budget: boolean; enough_space: boolean };
type Job = { id: string; operation: string; status: string; stage: string; created: string;
  started?: string; finished?: string; updated?: string; bundle_name?: string; incoming_dir?: string;
  profile?: { repositories: string[]; max_gib: number };
  snapshot_id?: string; plan?: Plan; result?: { path?: string; sha256?: string; bytes?: number;
  files_verified?: number; authenticated?: boolean; verified_at?: string; checked_at?: string;
  source_archive_sha256?: string; already_imported?: boolean; published_at?: string; config_url?: string } };
type Publication = { snapshot_id: string; published_at: string; verified_at: string; base_url: string;
  job_id: string; config_url: string; client_config: string; bytes: number; os: { major: string; arch: string };
  repositories: Record<string, { packages: number; metadata_types: string[] }> };
type Automation = { snapshot_id: string; has_updateinfo: boolean; files: Record<string, string> };
type Snapshot = { id: string; created: string; status: string; package_bytes: number;
  imported?: boolean; source_created?: string; verified_at?: string;
  export?: ExportRecord | null;
  repositories: Record<string, { packages: number; metadata_types: string[] }> };
type ExportRecord = { snapshot_id: string; exported_at: string | null; verified_at?: string; bytes: number;
  path: string; checksum_path: string; sha256: string; state: 'available' | 'missing' | 'changed' | 'unverified'; reason: string };
type Connected = { phase: 'select' | 'sync' | 'export' | 'verify' | 'ready' | 'failed' | 'running';
  action: string | null; reason: string; active: Job | null; attention_job: Job | null;
  latest_sync: Snapshot | null; selection_snapshot: Snapshot | null; latest_export: ExportRecord | null;
  selection_changed: boolean; plan_job: Job | null;
  selected_repositories: { id: string; snapshot_id: string | null; synced_at: string | null;
    repository: { packages: number; package_bytes: number } | null; export: ExportRecord | null }[] };
type IncomingBundle = { name: string; bytes: number; modified: string; has_checksum: boolean;
  state?: 'awaiting_import' | 'imported' | 'known' | 'changed' | 'blocked'; reason?: string; snapshot_id?: string | null };
type Disconnected = { phase: 'source' | 'waiting' | 'import_policy' | 'import' | 'publisher_setup' | 'server_url' | 'publish' | 'clients' | 'failed' | 'running';
  reason: string; active: Job | null; attention_job: Job | null; latest_import: Snapshot | null;
  publication_matches_latest: boolean; publication_serving: boolean; server_url_changed: boolean;
  target_snapshot_id: string | null; pending_bundles: number; known_bundles: number; client_application: 'unconfirmed';
  repositories: { id: string; snapshot_id: string; imported_at: string; repository: { packages: number; metadata_types: string[] };
    publication_state: 'current' | 'older' | 'not_published' }[] };
type State = { version: string; role: string; os: { name: string; major: string; arch: string };
  storage: { path: string; free: number; total: number; reserve: number };
  repositories: Repo[]; profile: { repositories: string[]; max_gib: number };
  jobs: Job[]; snapshots: Snapshot[]; incoming: IncomingBundle[];
  import_source: { path: string; available: boolean; error?: string; scanned_at: string; updated_at?: string } | null;
  publication: { installed: boolean; server_active: boolean; base_url: string; current: Publication | null;
    availability: { state: 'not_published' | 'available' | 'unavailable'; reason: string } } | null;
  connected: Connected | null; disconnected: Disconnected | null;
  capabilities: { allow_unsigned_imports: boolean } };
type CockpitProcess = Promise<string> & { input: (data: string) => void };
type DirectoryListing = { path: string; parent: string | null; truncated: boolean;
  directories: { name: string; path: string }[];
  locations: { label: string; path: string; kind: 'folder' | 'mount' }[] };
declare global { interface Window { cockpit: {
  spawn: (args: string[], opts: Record<string, unknown>) => CockpitProcess;
} } }
const helper = '/usr/libexec/disconnected-content';
function call<T>(args: string[], input?: unknown): Promise<T> {
  const process = window.cockpit.spawn([helper, ...args], { superuser: 'require', err: 'message' });
  if (input !== undefined) process.input(JSON.stringify(input));
  return Promise.resolve(process).then(raw => JSON.parse(raw));
}
function bytes(value: number) {
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${(value / 1024 ** 3).toFixed(1)} GiB`;
}
function repositoryCount(count: number) { return `${count} ${count === 1 ? 'repository' : 'repositories'}`; }
function downloadFile(content: string, filename: string) {
  const url = URL.createObjectURL(new Blob([content], { type: 'text/plain;charset=utf-8' }));
  const link = document.createElement('a'); link.href = url; link.download = filename; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function ClientAutomation({ publication }: { publication: Publication }) {
  const [automation, setAutomation] = useState<Automation>();
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let mounted = true;
    setAutomation(undefined); setError('');
    void call<Automation>(['client-automation', publication.job_id]).then(result => {
      if (mounted) setAutomation(result);
    }).catch(e => { if (mounted) setError(errorText(e)); });
    return () => { mounted = false; };
  }, [publication.job_id, revision]);
  return <div>
    <Title headingLevel="h2" size="lg">Ansible client automation</Title>
    <p>Download the paired playbooks for this publication and run them from your Ansible control node. Start with a canary host, configure its repositories, then preview and apply security updates.</p>
    {error ? <Alert variant="danger" isInline title="Unable to generate playbooks">{error}
      <Button variant="link" onClick={() => setRevision(value => value + 1)}>Retry downloads</Button></Alert> : !automation ? <Spinner size="md" aria-label="Generating playbook downloads" /> : <>
      {!automation.has_updateinfo && <Alert variant="warning" isInline title="Security advisory metadata is missing">
        Repository configuration is available. The security playbook will stop until every selected repository supplies updateinfo metadata. Mirror that metadata and publish again.</Alert>}
      <div className="dc-actions">{[
        ['configure-repos.yml', 'Download configure repos playbook'],
        ['security-updates.yml', 'Download security updates playbook'],
        ['inventory.ini', 'Download example inventory'],
        ['README-client-automation.txt', 'Download run instructions'],
      ].map(([filename, label]) => <Button key={filename} variant="secondary"
        onClick={() => downloadFile(automation.files[filename], filename)}>{label}</Button>)}</div>
      <p>Requires Ansible Core 2.16 or newer, SSH and administrative access to compatible RHEL clients, Python DNF bindings, and independently trusted RPM signing keys. Playbooks use built-in modules, process one host at a time, and stop on failure.</p>
      <pre className="dc-config" aria-label="Ansible run commands">{`# Edit inventory.ini, then preview and configure a canary host
ansible-playbook -i inventory.ini configure-repos.yml --limit client.example.org --check --diff
ansible-playbook -i inventory.ini configure-repos.yml --limit client.example.org --diff
# Preview security updates before applying them
ansible-playbook -i inventory.ini security-updates.yml --limit client.example.org --check --diff
ansible-playbook -i inventory.ini security-updates.yml --limit client.example.org`}</pre>
      <p className="dc-muted">These downloads stay pinned to snapshot {automation.snapshot_id}. The update playbook requires the exact paired repository configuration and selects only its repository IDs. Check mode can refresh DNF cache metadata. Neither playbook reboots hosts; package scripts may restart services. Cockpit does not run the playbooks or store client credentials.</p>
      <details><summary>Preview playbooks and instructions</summary>{Object.entries(automation.files).map(([name, content]) => <div key={name}>
        <h3>{name}</h3><pre className="dc-config" aria-label={name}>{content}</pre></div>)}</details>
    </>}
  </div>;
}
function Timestamp({ value }: { value: string }) {
  if (typeof value !== 'string' || !Number.isFinite(Date.parse(value))) return <span>Not recorded</span>;
  return <time dateTime={value} title={value}>{new Date(value).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short',
  })}</time>;
}
function relativeTime(value: string) {
  const seconds = (Date.parse(value) - Date.now()) / 1000;
  if (!Number.isFinite(seconds)) return 'Not recorded';
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  for (const [unit, divisor] of [['year', 31536000], ['month', 2592000], ['day', 86400], ['hour', 3600], ['minute', 60]] as const) {
    if (Math.abs(seconds) >= divisor) return formatter.format(Math.trunc(seconds / divisor), unit);
  }
  return seconds > 5 ? formatter.format(Math.ceil(seconds), 'second') : 'Just now';
}
function FreshTime({ value }: { value?: string | null }) {
  return value ? <span className="dc-fresh-time"><strong>{relativeTime(value)}</strong><span><Timestamp value={value} /></span></span> : <span>Never</span>;
}
function exportLabel(bundle?: ExportRecord | null) {
  if (!bundle) return 'Not exported';
  return { available: 'Bundle available', missing: 'Transfer files missing', changed: 'Re-verification needed', unverified: 'Historical bundle — verify before transfer' }[bundle.state];
}
function incomingLabel(bundle: IncomingBundle) {
  return { awaiting_import: 'Awaiting validation', imported: 'Imported · file attributes unchanged',
    known: 'Previously imported checksum', changed: 'Source file changed', blocked: 'Checksum needs attention' }[bundle.state ?? 'awaiting_import'];
}

function DisconnectedDashboard({ state, busy, sourceDraft, publicDraft, onTab, onPublish, onStop }: {
  state: State; busy: boolean; sourceDraft: boolean; publicDraft: boolean; onTab: (tab: string) => void;
  onPublish: (sid: string) => void; onStop: (id: string) => void;
}) {
  const flow = state.disconnected!;
  const { phase, latest_import: latest, active, attention_job: attention } = flow;
  const publication = state.publication!;
  const current = publication.current;
  const source = state.import_source!;
  const publishing = ['publisher_setup', 'server_url', 'publish'].includes(phase) || phase === 'failed' && attention?.operation === 'publish';
  const step = active ? active.operation === 'publish' ? 2 : 1 : publishing ? 2 : phase === 'clients' ? 3 :
    phase === 'source' ? 0 : 1;
  const title = { source: 'Choose an available transfer folder', waiting: 'Waiting for transferred bundles', import_policy: 'Review import policy',
    import: 'Transferred bundles need review', publisher_setup: 'Set up the repository publisher', server_url: 'Configure the client-facing server URL',
    publish: 'Review imported content for publication', clients: 'Published content ready for client setup',
    failed: `Latest ${attention?.operation === 'publish' ? 'publication' : 'import'} ${attention?.status ?? 'failed'}`, running: 'Current operation' }[phase];
  const importComplete = Boolean(latest) && !(step === 1 && (flow.pending_bundles > 0 || active?.operation === 'import' || phase === 'failed'));
  const publishComplete = flow.publication_serving && flow.publication_matches_latest && !flow.server_url_changed && step === 3;
  const primary = () => {
    if (publishing && flow.target_snapshot_id) onPublish(flow.target_snapshot_id);
    else onTab(phase === 'clients' ? 'clients' : 'incoming');
  };
  return <>
    {(sourceDraft || publicDraft) && <Alert variant="info" isInline title="Unsaved settings edits">
      This dashboard uses the saved transfer folder and server URL. Review and save changes in {sourceDraft ? 'Incoming bundles' : 'Publish content'} before starting a new operation.</Alert>}
    <div className="dc-freshness">
      <Card><CardTitle>Last successful import</CardTitle><CardBody>
        <div className="dc-fresh-metric"><FreshTime value={latest?.created} /></div>
        <p>{latest ? `${repositoryCount(Object.keys(latest.repositories).length)} · ${bytes(latest.package_bytes)} of RPM content` : 'No validated imported content has been recorded.'}</p>
        {latest?.source_created && <p className="dc-muted">Bundle created on the connected side <Timestamp value={latest.source_created} /></p>}
      </CardBody></Card>
      <Card><CardTitle>Last successful publication</CardTitle><CardBody>
        <div className="dc-fresh-metric"><FreshTime value={current?.published_at} /></div>
        <p>{!current ? 'No publication has been recorded.' : flow.publication_serving ? 'Public files present · Apache running' : 'Publication needs attention'}</p>
        {latest && current && !flow.publication_matches_latest && <p className="dc-muted">Clients are being offered an older or different imported snapshot.</p>}
        {flow.server_url_changed && <p className="dc-muted">The saved server URL has changed since publication.</p>}
      </CardBody></Card>
    </div>
    <Card className={`dc-next-action ${phase === 'failed' ? 'dc-next-failed' : ''}`}><CardTitle>{title}</CardTitle><CardBody>
      {active ? <div className="dc-current"><Spinner size="md" /><div><strong>{active.operation}</strong><p>{active.stage}</p>
        <p className="dc-muted">Elapsed {duration(active) ?? 'Waiting'} · Started {active.started ? <Timestamp value={active.started} /> : 'Waiting for worker'}</p>
        {active.updated && <p className="dc-muted">Last stage update <FreshTime value={active.updated} /></p>}
        <small>Runs in a system service. You can close this page and return later.</small></div>
        <Button variant="danger" isDisabled={busy} onClick={() => onStop(active.id)}>Stop job</Button></div> : <>
        <p>{flow.reason}</p>
        {latest && publishing && <p className="dc-muted">Imported <Timestamp value={latest.created} /> · Snapshot {flow.target_snapshot_id?.slice(0, 8)}</p>}
        <div className="dc-actions"><Button isDisabled={busy} onClick={primary}>
          {publishing ? phase === 'publisher_setup' ? 'Review publisher setup' : phase === 'server_url' ? 'Set server URL' : 'Review publication' :
            phase === 'clients' ? 'Configure clients' : phase === 'source' ? 'Choose transfer folder' : phase === 'waiting' ? 'Open transfer inbox' : phase === 'import_policy' ? 'Review import requirements' : 'Review incoming bundles'}</Button>
          {phase === 'failed' && <Button variant="secondary" onClick={() => onTab('jobs')}>View failed attempt</Button>}
          {current && phase !== 'clients' && <Button variant="secondary" onClick={() => onTab('clients')}>Client setup for recorded publication</Button>}
          {phase === 'clients' && <Button variant="secondary" onClick={() => onTab('incoming')}>Review incoming bundles</Button>}
        </div>
      </>}
    </CardBody></Card>
    <ol className="dc-workflow" aria-label="Disconnected content workflow">{[
      ['Choose source', source.available ? 'Saved transfer folder available' : 'Saved folder unavailable', source.available],
      ['Import & validate', importComplete ? 'Validated import available' : latest ? 'Previous import retained' : 'Validate a transferred bundle', importComplete],
      ['Publish content', publishComplete ? 'Latest import served locally' : current ? 'Recorded publication retained' : 'Publish a validated snapshot', publishComplete],
      ['Configure clients', 'Manual action · application unconfirmed', false],
    ].map(([name, detail, complete], index) => <li key={String(name)} className={`${complete ? 'dc-step-complete' : ''} ${step === index ? 'dc-step-current' : ''}`}
      aria-current={step === index ? 'step' : undefined}><span className="dc-step-number">{index + 1} · {step === index && active ? 'In progress' : step === index && phase === 'failed' ? 'Needs attention' : complete ? 'Complete' : step === index ? 'Next step' : 'Remaining'}</span>
      <strong>{name}</strong><span>{detail}</span></li>)}</ol>
    <div className="dc-freshness">
      <Card><CardTitle>Transfer inbox</CardTitle><CardBody>
        <p><strong>{flow.pending_bundles}</strong> {flow.pending_bundles === 1 ? 'bundle needs' : 'bundles need'} review · {flow.known_bundles} with previously imported checksums</p>
        <p><code>{source.path}</code></p><p className="dc-muted">Last scanned <Timestamp value={source.scanned_at} /></p>
        {!source.available && <p>{source.error}</p>}
        <Button variant="link" isInline onClick={() => onTab('incoming')}>{source.available ? 'Browse incoming bundles' : 'Choose or mount a transfer folder'}</Button>
      </CardBody></Card>
      <Card><CardTitle>Client setup</CardTitle><CardBody>
        <p>{current ? 'Repository configuration and paired Ansible playbooks are available for the recorded publication.' : 'Publish a snapshot to generate client setup downloads.'}</p>
        {current && <p className="dc-muted">Snapshot {current.snapshot_id.slice(0, 8)} · RHEL {current.os.major} · {current.os.arch}</p>}
        <p className="dc-muted">Client configuration and security-update application are not confirmed by this host.</p>
        <div className="dc-actions"><Button variant="secondary" onClick={() => onTab('clients')}>Open client setup</Button>
          {current && <Button variant="link" onClick={() => downloadFile(current.client_config, 'disconnected-content.repo')}>Download .repo file</Button>}</div>
      </CardBody></Card>
    </div>
    <Card><CardTitle>Imported repositories</CardTitle><CardBody>
      {!flow.repositories.length ? <p>No validated repositories have been imported yet.</p> : <div className="dc-table-scroll"><table className="dc-status-table" aria-label="Imported repository status">
        <thead><tr><th>Repository</th><th>Last imported</th><th>Publication status</th></tr></thead><tbody>{flow.repositories.map(repo => <tr key={repo.id}>
          <td><strong>{repo.id}</strong><span className="dc-muted">{repo.repository.packages.toLocaleString()} {repo.repository.packages === 1 ? 'package' : 'packages'} · Advisory metadata {repo.repository.metadata_types.includes('updateinfo') ? 'present' : 'not supplied'}</span></td>
          <td><FreshTime value={repo.imported_at} /></td><td>{{ current: 'Latest imported snapshot served', older: 'Older or unavailable publication', not_published: 'Not published' }[repo.publication_state]}</td>
        </tr>)}</tbody></table></div>}
      <div className="dc-actions"><Button variant="link" isInline onClick={() => onTab('snapshots')}>View imported snapshots</Button></div>
    </CardBody></Card>
    <Card><CardTitle>Recent activity</CardTitle><CardBody>
      {!state.jobs.length ? <p>No operations have run yet.</p> : <ol className="dc-recent-activity">{state.jobs.slice(0, 5).map(job => <li key={job.id}>
        <span><strong>{job.operation}</strong> <Label color={color(job.status)}>{job.status}</Label></span>
        <span><FreshTime value={job.finished ?? job.created} /></span><span className="dc-muted">{job.stage}</span></li>)}</ol>}
      <Button variant="link" isInline onClick={() => onTab('jobs')}>View activity &amp; evidence</Button>
    </CardBody></Card>
    <div className="dc-dashboard-footer"><span><strong>Available storage</strong> {bytes(state.storage.free)} · {bytes(state.storage.reserve)} reserved</span>
      <span>{state.snapshots.length} validated {state.snapshots.length === 1 ? 'snapshot' : 'snapshots'}</span></div>
    <p className="dc-muted">Publication availability checks local files and Apache state, not connectivity from other hosts or a fresh checksum of every package. Prototype bundles are unsigned; RPM signature checks remain enabled.</p>
  </>;
}
function ExportDetails({ bundle }: { bundle: ExportRecord }) {
  return <div className="dc-export dc-transfer-files" aria-label="Transfer bundle details">
    <div><Label color={bundle.state === 'available' ? 'green' : 'orange'}>{exportLabel(bundle)}</Label> · {bytes(bundle.bytes)}</div>
    <p>{bundle.reason}</p>
    <div><strong>Last exported</strong><FreshTime value={bundle.exported_at} /></div>
    {bundle.verified_at && <div><strong>Last verified</strong><FreshTime value={bundle.verified_at} /></div>}
    <strong>Transfer both files</strong><code>{bundle.path}</code><code>{bundle.checksum_path}</code>
    <details><summary>Checksum and transfer evidence</summary><p>SHA-256</p><code>{bundle.sha256}</code>
      <p>Transfer and disconnected import are not confirmed by this host. Use your approved transfer process.</p>
      <p>Availability checks file attributes and the checksum sidecar. Use Verify existing bundle to check archive contents again.</p></details>
  </div>;
}

function ConnectedDashboard({ state, busy, draft, onTab, onStart, onStop }: {
  state: State; busy: boolean; draft: boolean; onTab: (tab: string) => void;
  onStart: (operation: string, sid?: string) => void; onStop: (id: string) => void;
}) {
  const connected = state.connected!;
  const { phase, active, latest_sync: latest, selection_snapshot: snapshot, latest_export: lastExport, attention_job: attention } = connected;
  const bundle = snapshot?.export;
  const failureOperation = attention?.operation;
  const budgetFailure = phase === 'failed' && failureOperation === 'sync' && attention?.plan &&
    attention.profile?.max_gib === state.profile.max_gib &&
    (!attention.plan.within_budget || !attention.plan.enough_space);
  const title = { select: 'Choose content to mirror', sync: 'Saved selection needs a sync', export: 'Build a transfer bundle',
    verify: 'Verify the existing transfer bundle', ready: 'Verified bundle ready for transfer',
    failed: `Latest ${failureOperation === 'verify-export' ? 'bundle verification' : failureOperation ?? 'operation'} ${attention?.status ?? 'failed'}`,
    running: 'Current operation' }[phase];
  const retryingSync = active?.operation === 'sync' || phase === 'failed' && failureOperation === 'sync';
  const synced = Boolean(snapshot) && !retryingSync;
  const exported = bundle?.state === 'available' && synced && phase !== 'failed' &&
    active?.operation !== 'export' && active?.operation !== 'verify-export';
  const currentStep = active ? active.operation === 'sync' || active.operation === 'preflight' ? 1 : 2 :
    phase === 'select' ? 0 : phase === 'sync' || phase === 'failed' && ['sync', 'preflight'].includes(failureOperation ?? '') ? 1 : phase === 'ready' ? 3 : 2;
  return <>
    {draft && <Alert variant="info" isInline title="Unsaved repository edits">
      Dashboard actions use the saved selection. <Button variant="link" isInline onClick={() => onTab('profile')}>Review and save your changes</Button></Alert>}
    <div className="dc-freshness">
      <Card><CardTitle>Last successful sync</CardTitle><CardBody>
        <div className="dc-fresh-metric"><FreshTime value={latest?.created} /></div>
        <p>{latest ? `${repositoryCount(Object.keys(latest.repositories).length)} · ${bytes(latest.package_bytes)} of RPM content` : 'No validated snapshot has been created.'}</p>
        {connected.selection_changed && <p className="dc-muted">The latest snapshot differs from the saved repository selection.</p>}
      </CardBody></Card>
      <Card><CardTitle>Last successful export</CardTitle><CardBody>
        <div className="dc-fresh-metric"><FreshTime value={lastExport?.exported_at} /></div>
        <p>{lastExport ? exportLabel(lastExport) : 'No completed export has been recorded.'}</p>
        {lastExport && latest && lastExport.snapshot_id !== latest.id && <p className="dc-muted">This export contains an older or different snapshot.</p>}
      </CardBody></Card>
    </div>
    <Card className={`dc-next-action ${phase === 'failed' ? 'dc-next-failed' : ''}`}><CardTitle>{title}</CardTitle><CardBody>
      {active ? <>
        <div className="dc-current"><Spinner size="md" /><div><strong>{active.operation}</strong><p>{active.stage}</p>
          <p className="dc-muted">Elapsed {duration(active) ?? 'Waiting'} · Started {active.started ? <Timestamp value={active.started} /> : 'Waiting for worker'}</p>
          {active.updated && <p className="dc-muted">Last stage update <FreshTime value={active.updated} /></p>}
          <small>Runs in a system service. You can close this page and return later.</small></div>
          <Button variant="danger" isDisabled={busy} onClick={() => onStop(active.id)}>Stop job</Button></div>
      </> : <>
        <p>{connected.reason}</p>
        {snapshot && <p className="dc-muted">Saved selection: {repositoryCount(Object.keys(snapshot.repositories).length)} · Content captured <Timestamp value={snapshot.created} /></p>}
        <div className="dc-actions">
          {phase === 'select' || budgetFailure ? <Button onClick={() => onTab('profile')}>{budgetFailure ? 'Review sync settings' : 'Configure repositories'}</Button> :
            phase === 'ready' ? <Button onClick={() => onTab('snapshots')}>Show transfer files</Button> :
              <Button isDisabled={busy} onClick={() => onStart(connected.action!, snapshot?.id)}>
                {phase === 'failed' ? failureOperation === 'verify-export' ? 'Rebuild transfer bundle' : `Retry ${failureOperation}` :
                  connected.action === 'sync' ? 'Sync saved selection' : connected.action === 'export' ? 'Build & verify transfer bundle' : 'Verify existing bundle'}</Button>}
          {phase === 'failed' && <Button variant="secondary" onClick={() => onTab('jobs')}>View failed attempt</Button>}
          {snapshot && phase !== 'ready' && <Button variant="secondary" onClick={() => onTab('snapshots')}>View snapshots &amp; exports</Button>}
          {phase === 'ready' && <Button variant="secondary" isDisabled={busy} onClick={() => onStart('sync')}>Sync saved selection</Button>}
          {state.profile.repositories.length > 0 && <Button variant="link" isDisabled={busy} onClick={() => onStart('preflight')}>Check saved selection size</Button>}
        </div>
      </>}
    </CardBody></Card>
    <ol className="dc-workflow" aria-label="Saved selection workflow">{[
      ['Select repositories', state.profile.repositories.length ? 'Saved selection' : 'Needed', Boolean(state.profile.repositories.length)],
      ['Sync & validate', synced ? 'Validated snapshot available' : snapshot ? 'Previous snapshot retained' : 'Needed', synced],
      ['Build & verify bundle', exported ? 'Verified bundle available' : retryingSync && bundle ? 'Previous bundle retained' : bundle ? exportLabel(bundle) : 'Needed', exported],
      ['Transfer content', 'Manual handoff · import not confirmed', false],
    ].map(([name, detail, complete], index) => <li key={String(name)} className={`${complete ? 'dc-step-complete' : ''} ${currentStep === index ? 'dc-step-current' : ''}`}
      aria-current={currentStep === index ? 'step' : undefined}><span className="dc-step-number">{index + 1} · {currentStep === index && active ? 'In progress' : currentStep === index && phase === 'failed' ? 'Needs attention' : complete ? 'Complete' : currentStep === index ? 'Next step' : 'Remaining'}</span>
      <strong>{name}</strong><span>{detail}</span></li>)}</ol>
    <Card><CardTitle>Selected repositories</CardTitle><CardBody>
      {!connected.selected_repositories.length ? <p>Save a repository selection to begin.</p> : <div className="dc-table-scroll"><table className="dc-status-table" aria-label="Selected repository status">
        <thead><tr><th>Repository</th><th>Last successful sync</th><th>Export status</th></tr></thead><tbody>{connected.selected_repositories.map(repo => <tr key={repo.id}>
          <td><strong>{state.repositories.find(known => known.id === repo.id)?.name ?? repo.id}</strong><code>{repo.id}</code>
            {repo.repository && <span className="dc-muted">{repo.repository.packages.toLocaleString()} {repo.repository.packages === 1 ? 'package' : 'packages'} · {bytes(repo.repository.package_bytes)}</span>}</td>
          <td><FreshTime value={repo.synced_at} /></td><td>{exportLabel(repo.export)}
            {repo.export?.exported_at && <span className="dc-muted">Exported {relativeTime(repo.export.exported_at)}</span>}</td>
        </tr>)}</tbody></table></div>}
      <div className="dc-actions"><Button variant="link" isInline onClick={() => onTab('profile')}>Edit repository selection</Button></div>
    </CardBody></Card>
    {connected.plan_job?.plan && <Card><CardTitle>Size check for the saved selection</CardTitle><CardBody>
      <p><FreshTime value={connected.plan_job.finished ?? connected.plan_job.created} /></p>
      <p>{connected.plan_job.plan.packages.toLocaleString()} packages · {bytes(connected.plan_job.plan.package_bytes)} declared RPM content · {bytes(connected.plan_job.plan.required_free)} free space required</p>
      <Label color={connected.plan_job.plan.within_budget && connected.plan_job.plan.enough_space ? 'green' : 'red'}>
        {connected.plan_job.plan.within_budget && connected.plan_job.plan.enough_space ? 'Fit the budget and storage when checked' : 'Exceeded the budget or available storage when checked'}</Label>
      <p className="dc-muted">Every sync checks sizes and free space again. This result applies to the saved repository selection and budget.</p>
    </CardBody></Card>}
    <Card><CardTitle>Recent activity</CardTitle><CardBody>
      {!state.jobs.length ? <p>No operations have run yet.</p> : <ol className="dc-recent-activity">{state.jobs.slice(0, 5).map(job => <li key={job.id}>
        <span><strong>{job.operation}</strong> <Label color={color(job.status)}>{job.status}</Label></span>
        <span><FreshTime value={job.finished ?? job.created} /></span><span className="dc-muted">{job.stage}</span></li>)}</ol>}
      <Button variant="link" isInline onClick={() => onTab('jobs')}>View activity &amp; evidence</Button>
    </CardBody></Card>
    <div className="dc-dashboard-footer"><span><strong>Available storage</strong> {bytes(state.storage.free)} · {bytes(state.storage.reserve)} reserved</span>
      <span>{state.snapshots.length} validated {state.snapshots.length === 1 ? 'snapshot' : 'snapshots'}</span></div>
    <p className="dc-muted">Sync times record when content was captured, not whether newer upstream updates exist. Transfer bundles remain unsigned prototype artifacts.</p>
  </>;
}
function duration(job: Job) {
  if (!job.started) return undefined;
  const seconds = Math.max(0, Math.floor(((job.finished ? Date.parse(job.finished) : Date.now()) - Date.parse(job.started)) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
}
function errorText(error: unknown) { return error instanceof Error ? error.message : String(error); }
function color(status: string): 'green' | 'red' | 'blue' | 'orange' | 'grey' {
  return status === 'succeeded' || status === 'validated' ? 'green' :
    status === 'failed' ? 'red' : status === 'running' ? 'blue' :
      status === 'interrupted' || status === 'cancelled' ? 'orange' : 'grey';
}

function DirectoryPicker({ initialPath, isDisabled, onSelect, onClose }: {
  initialPath: string; isDisabled: boolean; onSelect: (path: string) => void; onClose: () => void;
}) {
  const [path, setPath] = useState(initialPath);
  const [listing, setListing] = useState<DirectoryListing>();
  const [locations, setLocations] = useState<DirectoryListing['locations']>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let mounted = true;
    setLoading(true); setError(''); setListing(undefined);
    void call<DirectoryListing>(['browse-directories'], { path }).then(next => {
      if (mounted) { setListing(next); setLocations(next.locations); }
    }).catch(e => { if (mounted) setError(errorText(e)); })
      .finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
  }, [path, revision]);
  const navigate = (next: string) => { setPath(next); setFilter(''); };
  const parts = path.split('/').filter(Boolean);
  const directories = listing?.directories.filter(directory => directory.name.toLowerCase().includes(filter.toLowerCase())) ?? [];
  return <Modal isOpen variant="large" aria-labelledby="dc-picker-title" aria-describedby="dc-picker-description" onClose={onClose}>
    <ModalHeader title="Choose transfer folder" labelId="dc-picker-title" descriptorId="dc-picker-description"
      description="Browse folders on this disconnected host. Mounted drives appear under Mounted filesystems." />
    <ModalBody className="dc-picker-body">
      <div className="dc-picker-layout">
        <nav className="dc-picker-locations" aria-label="Directory locations">
          <strong>Quick locations</strong>
          <Button variant="link" onClick={() => navigate('/')}>Filesystem root</Button>
          {locations.filter(location => location.kind === 'folder' && location.path !== '/').map(location =>
            <Button key={location.path} variant="link" title={location.path} onClick={() => navigate(location.path)}>{location.label}</Button>)}
          <strong>Mounted filesystems</strong>
          {locations.filter(location => location.kind === 'mount').map(location =>
            <Button key={location.path} variant="link" onClick={() => navigate(location.path)}>{location.label}</Button>)}
          {!locations.some(location => location.kind === 'mount') && <p className="dc-muted">Mount your transfer drive, then refresh to see it here.</p>}
        </nav>
        <div className="dc-picker-folders">
          <nav className="dc-picker-breadcrumbs" aria-label="Current folder path">
            <Button variant="link" aria-label="Go to filesystem root" onClick={() => navigate('/')}>/</Button>
            {parts.map((part, index) => {
              const target = '/' + parts.slice(0, index + 1).join('/');
              return <React.Fragment key={target}>{index > 0 && <span aria-hidden="true">/</span>}
                <Button variant="link" aria-label={`Go to ${target}`} onClick={() => navigate(target)}>{part}</Button></React.Fragment>;
            })}
          </nav>
          <div className="dc-picker-toolbar"><Button variant="secondary" isDisabled={loading || !listing?.parent}
            onClick={() => { if (listing?.parent) navigate(listing.parent); }}>Up one folder</Button>
            <Button variant="secondary" isDisabled={loading} onClick={() => setRevision(value => value + 1)}>Refresh folders</Button></div>
          <TextInput aria-label="Filter folders" placeholder="Filter folders" value={filter} onChange={(_, value) => setFilter(value)} />
          {error && <Alert variant="warning" isInline title="Cannot open this folder"><p>{error}</p>
            <p>Choose another location or mount the drive and refresh.</p></Alert>}
          <div className="dc-picker-directory-list" aria-busy={loading}>
            {loading ? <Spinner size="md" aria-label="Loading folders" /> : <>
              {listing && !directories.length && <p className="dc-muted">{filter ? 'No folders match this filter.' : 'No subfolders. You can use this folder.'}</p>}
              <ul aria-label="Folders">{directories.map(directory => <li key={directory.path}>
                <Button variant="link" aria-label={`Open folder ${directory.name}`} onClick={() => navigate(directory.path)}>
                  <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M3 6h6l2 2h10v12H3z" /></svg>
                  <span>{directory.name}</span><span aria-hidden="true">›</span></Button></li>)}</ul>
            </>}
          </div>
          {listing?.truncated && <p className="dc-muted">This folder has more than 500 subfolders. Showing the first 500; you can enter a full path in the source field if needed.</p>}
        </div>
      </div>
      <div className="dc-picker-selection"><strong>Selected folder</strong><code>{listing?.path ?? path}</code>
        <p className="dc-muted">After choosing a folder, use Save &amp; scan to apply it.</p></div>
    </ModalBody>
    <ModalFooter><Button isDisabled={loading || !listing || isDisabled} onClick={() => { if (listing) onSelect(listing.path); }}>Use this folder</Button>
      <Button variant="link" onClick={onClose}>Cancel</Button></ModalFooter>
  </Modal>;
}

function App() {
  const [state, setState] = useState<State>();
  const [tab, setTab] = useState<string | number>('overview');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [budget, setBudget] = useState('20');
  const [filter, setFilter] = useState('');
  const [dirty, setDirty] = useState(false);
  const [sourcePath, setSourcePath] = useState('');
  const [sourceDirty, setSourceDirty] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [publicUrl, setPublicUrl] = useState('');
  const [publicDirty, setPublicDirty] = useState(false);
  const [publishSnapshot, setPublishSnapshot] = useState('');
  const [log, setLog] = useState<{ id: string; text: string }>();
  const [ready, setReady] = useState(false);
  const refresh = async () => {
    const next = await call<State>(['status']);
    setState(next);
    return next;
  };
  useEffect(() => {
    if (!sourceDirty && state?.import_source) setSourcePath(state.import_source.path);
  }, [state?.import_source?.path, sourceDirty]);
  useEffect(() => {
    if (!publicDirty && state?.publication) setPublicUrl(state.publication.base_url || `http://${window.location.hostname}`);
  }, [state?.publication?.base_url, publicDirty]);
  useEffect(() => {
    let mounted = true;
    let inFlight = false;
    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const next = await call<State>(['status']);
        if (!mounted) return;
        setState(next);
        if (!ready) {
          setSelected(next.profile.repositories); setBudget(String(next.profile.max_gib)); setReady(true);
        }
      } catch (e) { if (mounted) setError(errorText(e)); }
      finally { inFlight = false; }
    };
    void poll();
    const timer = window.setInterval(() => { void poll(); }, 4000);
    return () => { mounted = false; window.clearInterval(timer); };
  }, [ready]);
  const act = async (fn: () => Promise<unknown>, message?: string) => {
    setBusy(true); setError(''); setNotice('');
    try { await fn(); await refresh(); if (message) setNotice(message); }
    catch (e) { setError(errorText(e)); }
    finally { setBusy(false); }
  };
  const active = state?.jobs.find(j => ['queued', 'running'].includes(j.status));
  const disconnected = state?.role === 'disconnected';
  const latestPlan = state?.jobs.find(j => j.plan)?.plan;
  const disabled = busy || Boolean(active);
  const save = async () => {
    await call(['save-profile'], { repositories: selected, max_gib: Number(budget) });
    setDirty(false);
  };
  const start = (operation: string, sid?: string, saveDraft = true) => act(async () => {
    if (saveDraft && (operation === 'preflight' || operation === 'sync')) await save();
    await call(['start', operation, ...(sid ? [sid] : [])]);
    setTab('overview');
  });
  const filtered = state?.repositories.filter(r =>
    `${r.id} ${r.name}`.toLowerCase().includes(filter.toLowerCase())) ?? [];
  return <main className="dc-app">
    {pickerOpen && <DirectoryPicker initialPath={sourcePath || state?.import_source?.path || '/'} isDisabled={disabled}
      onClose={() => setPickerOpen(false)} onSelect={path => {
        setSourcePath(path); setSourceDirty(path !== state?.import_source?.path); setPickerOpen(false);
      }} />}
    <header className="dc-header"><div><div className="dc-eyebrow">CONTENT OPERATIONS</div>
      <Title headingLevel="h1" size="2xl">Disconnected Content</Title>
      <p>Build verified repository snapshots for transfer across the air gap.</p></div>
      <div className="dc-labels"><Label color="blue">{disconnected ? 'Disconnected side' : 'Connected side'}</Label><Label color="orange">Prototype {state?.version ?? '0.8.0'}</Label></div>
    </header>
    {error && <Alert variant="danger" title="Operation could not complete" isInline><p>{error}</p></Alert>}
    {notice && <Alert variant="success" title={notice} isInline />}
    {!state ? <div className="dc-loading">{!error && <Spinner size="lg" />}<p>Enable administrative access in the web console to connect to the local engine.</p>
      <Button variant="secondary" onClick={() => { void act(refresh); }}>Retry connection</Button></div> : <>
      <div className="dc-host">{state.os.name} · {state.os.arch} · {state.storage.path}</div>
      <Tabs activeKey={tab} onSelect={(_, key) => setTab(key)} aria-label="Disconnected content sections">
        <Tab eventKey="overview" title={<TabTitleText>Overview</TabTitleText>} />
        {!disconnected && <Tab eventKey="profile" title={<TabTitleText>Repositories</TabTitleText>} />}
        {disconnected && <Tab eventKey="incoming" title={<TabTitleText>Incoming bundles</TabTitleText>} />}
        {disconnected && <Tab eventKey="publish" title={<TabTitleText>Publish content</TabTitleText>} />}
        {disconnected && <Tab eventKey="clients" title={<TabTitleText>Client setup</TabTitleText>} />}
        <Tab eventKey="snapshots" title={<TabTitleText>{disconnected ? 'Snapshots' : 'Snapshots & exports'} <Badge isRead>{state.snapshots.length}</Badge></TabTitleText>} />
        <Tab eventKey="jobs" title={<TabTitleText>Activity &amp; evidence</TabTitleText>} />
      </Tabs>
      <section className="dc-content">
        {tab === 'overview' && !disconnected && state.connected && <ConnectedDashboard state={state} busy={busy} draft={dirty}
          onTab={setTab} onStart={(operation, sid) => { void start(operation, sid, false); }}
          onStop={id => { void act(() => call(['cancel', id])); }} />}
        {tab === 'overview' && disconnected && state.disconnected && <DisconnectedDashboard state={state} busy={busy}
          sourceDraft={sourceDirty} publicDraft={publicDirty} onTab={setTab}
          onPublish={sid => { setPublishSnapshot(sid); setTab('publish'); }}
          onStop={id => { void act(() => call(['cancel', id])); }} />}
        {tab === 'overview' && (disconnected ? !state.disconnected : !state.connected) && <>
          <div className="dc-stats">
            <Card><CardTitle>Available storage</CardTitle><CardBody><div className="dc-metric">{bytes(state.storage.free)}</div>
              <p>{bytes(state.storage.reserve)} reserved for the operating system and other workloads</p></CardBody></Card>
            <Card><CardTitle>{disconnected ? 'Incoming transfer bundles' : 'Repository profile'}</CardTitle><CardBody><div className="dc-metric">
              {disconnected ? state.incoming.length : `${state.profile.repositories.length} ${state.profile.repositories.length === 1 ? 'repository' : 'repositories'}`}</div>
              <p>{disconnected ? 'Archives staged locally for verification and import' : `${state.profile.max_gib} GiB maximum declared RPM content per snapshot`}</p></CardBody></Card>
            <Card><CardTitle>Validated snapshots</CardTitle><CardBody><div className="dc-metric">{state.snapshots.length}</div>
              <p>{disconnected ? 'Verified content ready for local use or HTTP publication' : 'Only completed, verified snapshots can be exported'}</p></CardBody></Card>
          </div>
          <Card><CardTitle>{active ? 'Current operation' : 'Ready for the next transfer'}</CardTitle><CardBody>
            {active ? <div className="dc-current"><Spinner size="md" /><div><strong>{active.operation}</strong><p>{active.stage}</p>
              <small>Runs in a system service. You can close this page and return later.</small></div>
              <Button variant="danger" isDisabled={busy} onClick={() => { void act(() => call(['cancel', active.id])); }}>Stop job</Button></div> : <>
              <p>{disconnected ? 'Review a transferred bundle, verify its contents, and import a complete snapshot.' : 'Choose repositories, check their size, then sync a complete snapshot with the original metadata.'}</p>
              {disconnected ? <div className="dc-actions"><Button onClick={() => setTab('incoming')}>Review incoming bundles</Button></div> : <div className="dc-actions"><Button onClick={() => setTab('profile')}>Configure repositories</Button>
                <Button variant="secondary" isDisabled={disabled || !selected.length} onClick={() => { void start('preflight'); }}>Check size</Button>
                <Button variant="secondary" isDisabled={disabled || !selected.length} onClick={() => { void start('sync'); }}>Sync snapshot</Button></div>}</>}
          </CardBody></Card>
          {latestPlan && <Card><CardTitle>Latest size preflight</CardTitle><CardBody>
            <div className="dc-plan"><span><strong>{latestPlan.packages.toLocaleString()}</strong> packages</span>
              <span><strong>{bytes(latestPlan.package_bytes)}</strong> declared RPM content</span>
              <span><strong>{bytes(latestPlan.required_free)}</strong> free space required, including export allowance</span></div>
            <Label color={latestPlan.within_budget && latestPlan.enough_space ? 'green' : 'red'}>
              {latestPlan.within_budget && latestPlan.enough_space ? 'Fits the saved budget and available storage' : 'Exceeds the saved budget or available storage'}</Label>
            <p className="dc-muted">A new size preflight is performed for every sync. Metadata refresh may use additional cache space.</p>
          </CardBody></Card>}
          <Alert variant="info" isInline title={disconnected ? 'Disconnected import prototype' : 'Connected sync prototype'}>
            <p>Transfer bundles carry checksums, without a cryptographic signature. {disconnected ? 'Use Publish content to serve validated snapshots and obtain client configuration. Client configuration and updates are explicit administrator actions.' : 'Transfer a verified bundle to a disconnected host for import and publication.'}</p>
          </Alert>
        </>}
        {tab === 'incoming' && disconnected && <>
          <Alert variant={state.capabilities.allow_unsigned_imports ? 'warning' : 'info'} isInline
            title={state.capabilities.allow_unsigned_imports ? 'Unsigned prototype imports enabled by the administrator' : 'Unsigned prototype imports are disabled'}>
            <p>Checksums verify integrity, not provenance. Every imported RPM is independently checked against the host's trusted signing keys.</p>
          </Alert>
          <Card><CardTitle>Transfer inbox</CardTitle><CardBody>
            <Title headingLevel="h2" size="lg">1. Choose a bundle source</Title>
            <p>Browse to a transfer folder on this disconnected host or choose a mounted drive. The app scans the selected folder only; it does not search subdirectories.</p>
            <div className="dc-source-controls"><label htmlFor="bundle-source">Bundle source directory</label>
              <div className="dc-source-input"><TextInput id="bundle-source" value={sourcePath} isDisabled={disabled} placeholder="/mnt/transfer"
                onChange={(_, value) => { setSourcePath(value); setSourceDirty(true); }} />
                <Button variant="secondary" isDisabled={disabled} onClick={() => setPickerOpen(true)}>Browse folders</Button></div>
              <div className="dc-actions"><Button isDisabled={disabled || !sourcePath.trim()}
                onClick={() => { void act(async () => {
                  await call(['save-import-settings'], { incoming_dir: sourcePath }); setSourceDirty(false);
                }, 'Bundle source saved and scanned'); }}>Save &amp; scan</Button>
                <Button variant="secondary" isDisabled={busy || sourceDirty} onClick={() => { void act(refresh); }}>Scan now</Button>
                {sourceDirty && <><Label color="orange">Unsaved source changes</Label>
                  <Button variant="link" onClick={() => { setSourcePath(state.import_source?.path ?? ''); setSourceDirty(false); }}>Discard changes</Button></>}
              </div></div>
            {state.import_source && <div className="dc-source-status">
              <p><strong>Scanning:</strong> <code>{state.import_source.path}</code></p>
              <p className="dc-muted">Last scanned <Timestamp value={state.import_source.scanned_at} />
                {state.import_source.updated_at && <> · Source saved <Timestamp value={state.import_source.updated_at} /></>}</p>
              {!state.import_source.available && <Alert variant="warning" isInline title="Bundle source unavailable">
                <p>{state.import_source.error}</p><p>Mount the media at the saved path or save another existing directory, then scan again.</p></Alert>}
            </div>}
            <Title headingLevel="h2" size="lg">2. Stage and import a bundle</Title>
            <p>Place the archive and matching <code>.tar.sha256</code> file in the saved source directory using your approved transfer process. Import copies verified content into managed snapshot storage at <code>{state.storage.path}/snapshots</code>; source files remain in place.</p>
            <p className="dc-muted">Newest file modification first. Modification times come from the source filesystem and may be preserved during copying. Times are shown in your browser's local timezone.</p>
            {!state.incoming.length && state.import_source?.available && <p>No transfer archives were found in this directory.</p>}
            {state.incoming.map(bundle => <div className="dc-repo" key={bundle.name}><div><strong>{bundle.name}</strong>
              <p>{bytes(bundle.bytes)} · {bundle.has_checksum ? 'Checksum sidecar present' : 'Checksum sidecar missing'}</p>
              <p className="dc-muted">File modified <Timestamp value={bundle.modified} /></p>
              {bundle.state && <><Label color={['known', 'imported'].includes(bundle.state) ? 'blue' : bundle.state === 'blocked' ? 'orange' : 'grey'}>{incomingLabel(bundle)}</Label>
                <p className="dc-muted">{bundle.reason}</p></>}</div>
              <Button isDisabled={disabled || sourceDirty || !bundle.has_checksum || bundle.state === 'blocked' || !state.capabilities.allow_unsigned_imports}
                onClick={() => { void start('import', bundle.name); }}>{['known', 'imported'].includes(bundle.state ?? '') ? 'Recheck bundle' : 'Import bundle'}</Button></div>)}
          </CardBody></Card>
        </>}
        {tab === 'profile' && <Card><CardTitle>Choose content to mirror</CardTitle><CardBody>
          <p>Repositories already configured on this host are available here. Selections apply only to mirror jobs; the host's enabled repositories are preserved.</p>
          <div className="dc-profile-controls"><label htmlFor="budget">Snapshot budget (GiB)</label>
            <TextInput id="budget" aria-label="Snapshot budget in GiB" type="number" min={1} max={2000} value={budget}
              isDisabled={disabled} onChange={(_, v) => { setBudget(v); setDirty(true); }} />
            <TextInput aria-label="Filter repositories" placeholder="Filter repositories, for example supplementary" value={filter}
              onChange={(_, v) => setFilter(v)} /></div>
          <div className="dc-selection">{selected.length} selected {dirty && <Label color="orange">Unsaved changes</Label>}</div>
          <div className="dc-repos">{filtered.map(repo => {
            const history = state.connected?.selected_repositories.find(known => known.id === repo.id);
            return <div key={repo.id} className="dc-repo"><div className="dc-repo-selection">
              <Checkbox id={`repo-${repo.id}`} label={repo.name} description={repo.id} isChecked={selected.includes(repo.id)}
                isDisabled={disabled} onChange={(_, checked) => { setSelected(old => checked ? [...old, repo.id] : old.filter(r => r !== repo.id)); setDirty(true); }} />
              {history && <p className="dc-repo-history">Last synced: <FreshTime value={history.synced_at} /> · {exportLabel(history.export)}</p>}
            </div><div className="dc-repo-labels">{state.profile.repositories.includes(repo.id) && <Label isCompact color="blue">Saved for mirroring</Label>}
              {repo.enabled && <Label isCompact color="grey">Enabled on this host</Label>}</div></div>;
          })}</div>
          <div className="dc-actions"><Button isDisabled={disabled || !selected.length} onClick={() => { void act(save, 'Repository profile saved'); }}>Save profile</Button>
            <Button variant="secondary" isDisabled={disabled || !selected.length} onClick={() => { void start('preflight'); }}>Save &amp; check size</Button>
            <Button variant="secondary" isDisabled={disabled || !selected.length} onClick={() => { void start('sync'); }}>Save &amp; sync snapshot</Button></div>
        </CardBody></Card>}
        {tab === 'publish' && disconnected && <>
          <Card><CardTitle>Web server settings</CardTitle><CardBody>
            <p>Publish validated content through this host's Apache web server. Enter the server URL that client hosts can reach inside the air gap.</p>
            {!state.publication?.installed && <Alert variant="info" isInline title="Publisher package required">
              <p>Install <code>cockpit-disconnected-content-publisher</code> and its Apache dependencies before isolation or from an offline bootstrap repository.</p></Alert>}
            <div className="dc-source-controls"><label htmlFor="public-url">Public server URL</label>
              <TextInput id="public-url" value={publicUrl} placeholder="http://mirror.example.org" isDisabled={disabled}
                onChange={(_, value) => { setPublicUrl(value); setPublicDirty(true); }} />
              <div className="dc-actions"><Button isDisabled={disabled || !publicUrl.trim()} onClick={() => { void act(async () => {
                await call(['save-publication-settings'], { base_url: publicUrl }); setPublicDirty(false);
              }, 'Publication settings saved'); }}>Save server URL</Button>
              {publicDirty && <Label color="orange">Unsaved URL changes</Label>}
              <Label color={state.publication?.server_active ? 'green' : 'grey'}>{state.publication?.server_active ? 'Apache running' : 'Apache not running'}</Label></div>
            </div>
            <p className="dc-muted">Publishing enables and starts Apache. The supplied configuration serves HTTP on port 80. Allow inbound HTTP from approved client networks using your firewall policy. HTTPS requires separately configured TLS; saving an HTTPS URL does not enable TLS.</p>
          </CardBody></Card>
          <Card><CardTitle>Publish a validated snapshot</CardTitle><CardBody>
            <p>Publication creates and verifies a separate public copy. Existing snapshot URLs remain available when newer content is published.</p>
            <label htmlFor="publish-snapshot">Snapshot to publish</label>
            <select id="publish-snapshot" className="dc-snapshot-select" value={publishSnapshot} disabled={disabled}
              onChange={event => setPublishSnapshot(event.target.value)}>
              <option value="">Choose a validated snapshot</option>
              {state.snapshots.map(snapshot => <option key={snapshot.id} value={snapshot.id}>{snapshot.id.slice(0, 8)} · {new Date(snapshot.created).toLocaleString()}</option>)}
            </select>
            <div className="dc-actions"><Button isDisabled={disabled || publicDirty || !publishSnapshot || !state.publication?.installed || !state.publication.base_url}
              onClick={() => { void start('publish', publishSnapshot); }}>Publish snapshot</Button></div>
          </CardBody></Card>
          {state.publication?.current && <Card><CardTitle>Currently published snapshot</CardTitle><CardBody>
            <p><strong>{state.publication.current.snapshot_id}</strong> · Published <FreshTime value={state.publication.current.published_at} /></p>
            <p>Public copy verified <Timestamp value={state.publication.current.verified_at} /> · {bytes(state.publication.current.bytes)}</p>
            <p>{state.publication.availability.reason} · {state.publication.server_active ? 'Apache running' : 'Apache not running'}</p>
            <p>Client configuration: <code>{state.publication.current.config_url}</code></p>
            {state.disconnected?.latest_import && !state.disconnected.publication_matches_latest && <p className="dc-muted">The latest imported snapshot has not replaced this publication.</p>}
            <div className="dc-actions"><Button variant="secondary" onClick={() => setTab('clients')}>Open client setup</Button></div>
          </CardBody></Card>}
        </>}
        {tab === 'clients' && disconnected && <>
          {state.publication?.current ? <Card><CardTitle>Client setup for recorded publication</CardTitle><CardBody>
            {!state.disconnected?.publication_serving && <Alert variant="warning" isInline title="Publication availability needs attention">
              Review Publish content before using these URLs. Downloads reflect the recorded publication; current web availability is not confirmed.</Alert>}
            {state.disconnected?.server_url_changed && <Alert variant="info" isInline title="Saved server URL differs from these downloads">
              Republish content to generate client configuration for the saved URL.</Alert>}
            <p><strong>{state.publication.current.snapshot_id}</strong> · Published <Timestamp value={state.publication.current.published_at} /></p>
            <p>Public copy verified <Timestamp value={state.publication.current.verified_at} /> · {bytes(state.publication.current.bytes)}</p>
            <p>Client platform: RHEL {state.publication.current.os.major} · {state.publication.current.os.arch}</p>
            <p>Latest client configuration: <code>{state.publication.current.config_url}</code></p>
            <p>Install this file on compatible client hosts as <code>/etc/yum.repos.d/disconnected-content.repo</code>. Each repository URL is pinned to this snapshot. To adopt newly published content, install its new configuration file.</p>
            <div className="dc-actions"><Button variant="secondary" onClick={() => downloadFile(state.publication!.current!.client_config, 'disconnected-content.repo')}>Download .repo file</Button>
              <Button variant="secondary" onClick={() => { void act(() => navigator.clipboard.writeText(state.publication!.current!.client_config), 'Repository configuration copied'); }}>Copy .repo contents</Button></div>
            <pre className="dc-config" aria-label="Client repository configuration">{state.publication.current.client_config}</pre>
            <Title headingLevel="h2" size="lg">Configure and check client hosts</Title>
            <p>Review the downloaded file, install it, then refresh metadata and preview security updates. These commands select only the published repositories. Clients need independently trusted Red Hat RPM keys.</p>
            <pre className="dc-config" aria-label="Client setup commands">{`curl --fail --output disconnected-content.repo '${state.publication.current.config_url}'
cat disconnected-content.repo
sudo install -m 0644 disconnected-content.repo /etc/yum.repos.d/disconnected-content.repo
sudo dnf --disablerepo='*' --enablerepo='dc-*' makecache --refresh
sudo dnf --disablerepo='*' --enablerepo='dc-*' updateinfo list security
sudo dnf --disablerepo='*' --enablerepo='dc-*' upgrade --security --assumeno`}</pre>
            <p className="dc-muted">The preview does not apply updates. After reviewing the transaction and your maintenance policy, an administrator can run the upgrade without --assumeno. Publication does not configure clients, initiate updates, or reboot hosts.</p>
            <ClientAutomation key={state.publication.current.job_id} publication={state.publication.current} />
          </CardBody></Card> : <Card><CardBody>Publish a validated snapshot to generate repository configuration and client playbooks. <Button variant="link" onClick={() => setTab('publish')}>Open Publish content</Button></CardBody></Card>}
        </>}
        {tab === 'snapshots' && <>
          {!disconnected && <p className="dc-muted">Newest content first. Each snapshot retains its own export and verification record. Availability does not confirm transfer or disconnected import.</p>}
          {!state.snapshots.length && <Card><CardTitle>No validated snapshots yet</CardTitle><CardBody>{disconnected ? 'Import a transfer bundle to create the first snapshot.' : 'Complete a sync to create the first snapshot.'}</CardBody></Card>}
          {state.snapshots.map(snapshot => <Card key={snapshot.id}><CardTitle><span>{disconnected ? `Snapshot ${snapshot.id.slice(0, 8)}` : <>Content captured <Timestamp value={snapshot.created} /></>}</span> <Label color="green">Validated</Label></CardTitle><CardBody>
            {!disconnected && <><p className="dc-muted">Snapshot {snapshot.id.slice(0, 8)} · {relativeTime(snapshot.created)}</p>
              {state.connected?.selection_snapshot?.id === snapshot.id && <Label color="blue">Latest for saved selection</Label>}
              {snapshot.export ? <ExportDetails bundle={snapshot.export} /> : <Alert variant="info" isInline title="Not exported yet">Build and verify a transfer bundle from this validated snapshot.</Alert>}</>}
            {state.publication?.current?.snapshot_id === snapshot.id && <Label color="blue">Currently published</Label>}
            <p>{disconnected && <>{snapshot.imported ? 'Imported' : 'Created'} <Timestamp value={snapshot.created} /> · </>}{bytes(snapshot.package_bytes)} of RPM content</p>
            {snapshot.source_created && <p className="dc-muted">Bundle created on source <Timestamp value={snapshot.source_created} /></p>}
            {snapshot.verified_at && <p className="dc-muted">Content verified <Timestamp value={snapshot.verified_at} /></p>}
            <ul className="dc-snapshot-repos">{Object.entries(snapshot.repositories).map(([rid, result]) => <li key={rid}>
              <strong>{rid}</strong><br />{result.packages.toLocaleString()} packages · metadata and package checksums verified · RPM signatures verified<br />
              <span className="dc-muted">Advisory metadata: {result.metadata_types.includes('updateinfo') ? 'present' : 'not supplied by this repository'}</span></li>)}</ul>
            {disconnected && <div className="dc-local-repos"><strong>Local repository URLs</strong>
              {Object.keys(snapshot.repositories).map(rid => <code key={rid}>{`file://${state.storage.path}/snapshots/${snapshot.id}/repositories/${rid}`}</code>)}</div>}
            {disconnected && <div className="dc-actions"><Button variant="secondary" isDisabled={disabled} onClick={() => { setPublishSnapshot(snapshot.id); setTab('publish'); }}>Publish over HTTP</Button></div>}
            {!disconnected && <div className="dc-actions"><Button isDisabled={disabled} onClick={() => { void start('export', snapshot.id); }}>{snapshot.export ? 'Rebuild transfer bundle' : 'Build transfer bundle'}</Button>
              <Button variant="secondary" isDisabled={disabled || !snapshot.export || snapshot.export.state === 'missing'} onClick={() => { void start('verify-export', snapshot.id); }}>Verify existing bundle</Button></div>}
          </CardBody></Card>)}
        </>}
        {tab === 'jobs' && <>
          <p className="dc-muted">Newest jobs first · Times shown in your browser's local timezone. Earlier jobs display the timestamps recorded by their version of the engine.</p>
          {!state.jobs.length && <Card><CardBody>No jobs have run yet.</CardBody></Card>}
          {state.jobs.map(job => <Card key={job.id}><CardTitle><span className="dc-job-name">{job.operation}</span> <Label color={color(job.status)}>{job.status}</Label></CardTitle><CardBody>
            <p>{job.stage}</p>
            <dl className="dc-timeline"><div><dt>Queued</dt><dd><Timestamp value={job.created} /></dd></div>
              {job.started && <div><dt>Started</dt><dd><Timestamp value={job.started} /></dd></div>}
              {job.updated && <div><dt>Last updated</dt><dd><Timestamp value={job.updated} /></dd></div>}
              {job.finished && <div><dt>Finished</dt><dd><Timestamp value={job.finished} /></dd></div>}
              {job.started && <div><dt>{job.finished ? 'Duration' : 'Elapsed'}</dt><dd>{duration(job)}</dd></div>}</dl>
            <p className="dc-muted">Job {job.id}</p>
            {job.bundle_name && <p>Source bundle: <code>{job.incoming_dir ? `${job.incoming_dir}/` : ''}{job.bundle_name}</code></p>}
            {job.result?.verified_at && <p>{job.operation === 'publish' ? 'Public copy verified' : 'Evidence verified'} <Timestamp value={job.result.verified_at} />
              {job.result.files_verified !== undefined && <> · {job.result.files_verified} archive files checked</>}</p>}
            {job.result?.checked_at && <p>Archive matched existing snapshot <Timestamp value={job.result.checked_at} /></p>}
            {job.result?.published_at && <p>Published <Timestamp value={job.result.published_at} /> · Client configuration: <code>{job.result.config_url}</code></p>}
            {job.result?.source_archive_sha256 && <div className="dc-export"><strong>Imported archive SHA-256</strong><code>{job.result.source_archive_sha256}</code></div>}
            {job.plan && <p>{job.plan.packages.toLocaleString()} packages · {bytes(job.plan.package_bytes)} declared content</p>}
            {job.result?.path && <div className="dc-export"><strong>Transfer bundle</strong><code>{job.result.path}</code>
              <strong>SHA-256</strong><code>{job.result.sha256}</code>
              <p>{bytes(job.result.bytes ?? 0)} · {job.result.files_verified} archived files verified</p>
              <p>Carry the archive and its matching .sha256 file through your approved transfer process. This prototype bundle is unsigned.</p></div>}
            <Button variant="link" isInline onClick={() => { void act(async () => {
              const value = await call<{ text: string }>(['logs', job.id]); setLog({ id: job.id, text: value.text });
            }); }}>View command output</Button>
            {log?.id === job.id && <pre className="dc-log">{log.text}</pre>}
          </CardBody></Card>)}
        </>}
      </section>
    </>}
  </main>;
}
createRoot(document.getElementById('root')!).render(<App />);

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
  snapshot_id?: string; plan?: Plan; result?: { path?: string; sha256?: string; bytes?: number;
  files_verified?: number; authenticated?: boolean; verified_at?: string; checked_at?: string;
  source_archive_sha256?: string; already_imported?: boolean } };
type Snapshot = { id: string; created: string; status: string; package_bytes: number;
  imported?: boolean; source_created?: string; verified_at?: string;
  repositories: Record<string, { packages: number; metadata_types: string[] }> };
type State = { version: string; role: string; os: { name: string; major: string; arch: string };
  storage: { path: string; free: number; total: number; reserve: number };
  repositories: Repo[]; profile: { repositories: string[]; max_gib: number };
  jobs: Job[]; snapshots: Snapshot[]; incoming: { name: string; bytes: number; modified: string; has_checksum: boolean }[];
  import_source: { path: string; available: boolean; error?: string; scanned_at: string; updated_at?: string } | null;
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
function Timestamp({ value }: { value: string }) {
  if (typeof value !== 'string' || !Number.isFinite(Date.parse(value))) return <span>Not recorded</span>;
  return <time dateTime={value} title={value}>{new Date(value).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short',
  })}</time>;
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
  const start = (operation: string, sid?: string) => act(async () => {
    if (operation === 'preflight' || operation === 'sync') await save();
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
      <div className="dc-labels"><Label color="blue">{disconnected ? 'Disconnected side' : 'Connected side'}</Label><Label color="orange">Prototype {state?.version ?? '0.4.0'}</Label></div>
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
        <Tab eventKey="snapshots" title={<TabTitleText>Snapshots <Badge isRead>{state.snapshots.length}</Badge></TabTitleText>} />
        <Tab eventKey="jobs" title={<TabTitleText>Jobs &amp; evidence</TabTitleText>} />
      </Tabs>
      <section className="dc-content">
        {tab === 'overview' && <>
          <div className="dc-stats">
            <Card><CardTitle>Available storage</CardTitle><CardBody><div className="dc-metric">{bytes(state.storage.free)}</div>
              <p>{bytes(state.storage.reserve)} reserved for the operating system and other workloads</p></CardBody></Card>
            <Card><CardTitle>{disconnected ? 'Incoming transfer bundles' : 'Repository profile'}</CardTitle><CardBody><div className="dc-metric">
              {disconnected ? state.incoming.length : `${state.profile.repositories.length} ${state.profile.repositories.length === 1 ? 'repository' : 'repositories'}`}</div>
              <p>{disconnected ? 'Archives staged locally for verification and import' : `${state.profile.max_gib} GiB maximum declared RPM content per snapshot`}</p></CardBody></Card>
            <Card><CardTitle>Validated snapshots</CardTitle><CardBody><div className="dc-metric">{state.snapshots.length}</div>
              <p>Only completed, verified snapshots can be exported</p></CardBody></Card>
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
            <p>Transfer bundles carry checksums, without a cryptographic signature. Imported repositories are available locally; HTTP publication and fleet patching are future capabilities.</p>
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
              <p className="dc-muted">File modified <Timestamp value={bundle.modified} /></p></div>
              <Button isDisabled={disabled || sourceDirty || !bundle.has_checksum || !state.capabilities.allow_unsigned_imports}
                onClick={() => { void start('import', bundle.name); }}>Import bundle</Button></div>)}
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
          <div className="dc-repos">{filtered.map(repo => <div key={repo.id} className="dc-repo">
            <Checkbox id={`repo-${repo.id}`} label={repo.name} description={repo.id} isChecked={selected.includes(repo.id)}
              isDisabled={disabled} onChange={(_, checked) => { setSelected(old => checked ? [...old, repo.id] : old.filter(r => r !== repo.id)); setDirty(true); }} />
            {repo.enabled && <Label isCompact color="blue">Enabled on host</Label>}</div>)}</div>
          <div className="dc-actions"><Button isDisabled={disabled || !selected.length} onClick={() => { void act(save, 'Repository profile saved'); }}>Save profile</Button>
            <Button variant="secondary" isDisabled={disabled || !selected.length} onClick={() => { void start('preflight'); }}>Save &amp; check size</Button>
            <Button variant="secondary" isDisabled={disabled || !selected.length} onClick={() => { void start('sync'); }}>Save &amp; sync snapshot</Button></div>
        </CardBody></Card>}
        {tab === 'snapshots' && <>
          {!state.snapshots.length && <Card><CardTitle>No validated snapshots yet</CardTitle><CardBody>{disconnected ? 'Import a transfer bundle to create the first snapshot.' : 'Complete a sync to create the first snapshot.'}</CardBody></Card>}
          {state.snapshots.map(snapshot => <Card key={snapshot.id}><CardTitle><span>Snapshot {snapshot.id.slice(0, 8)}</span> <Label color="green">Validated</Label></CardTitle><CardBody>
            <p>{snapshot.imported ? 'Imported' : 'Created'} <Timestamp value={snapshot.created} /> · {bytes(snapshot.package_bytes)} of RPM content</p>
            {snapshot.source_created && <p className="dc-muted">Bundle created on source <Timestamp value={snapshot.source_created} /></p>}
            {snapshot.verified_at && <p className="dc-muted">Content verified <Timestamp value={snapshot.verified_at} /></p>}
            <ul className="dc-snapshot-repos">{Object.entries(snapshot.repositories).map(([rid, result]) => <li key={rid}>
              <strong>{rid}</strong><br />{result.packages.toLocaleString()} packages · metadata and package checksums verified · RPM signatures verified<br />
              <span className="dc-muted">Advisory metadata: {result.metadata_types.includes('updateinfo') ? 'present' : 'not supplied by this repository'}</span></li>)}</ul>
            {disconnected && <div className="dc-local-repos"><strong>Local repository URLs</strong>
              {Object.keys(snapshot.repositories).map(rid => <code key={rid}>{`file://${state.storage.path}/snapshots/${snapshot.id}/repositories/${rid}`}</code>)}</div>}
            {!disconnected && <div className="dc-actions"><Button isDisabled={disabled} onClick={() => { void start('export', snapshot.id); }}>Build transfer bundle</Button>
              <Button variant="secondary" isDisabled={disabled} onClick={() => { void start('verify-export', snapshot.id); }}>Verify existing bundle</Button></div>}
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
            {job.result?.verified_at && <p>Evidence verified <Timestamp value={job.result.verified_at} /> · {job.result.files_verified} archive files checked</p>}
            {job.result?.checked_at && <p>Archive matched existing snapshot <Timestamp value={job.result.checked_at} /></p>}
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

import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Alert, Badge, Button, Card, CardBody, CardTitle, Checkbox, Label, Spinner,
  Tab, Tabs, TabTitleText, TextInput, Title } from '@patternfly/react-core';
import '@patternfly/patternfly/patternfly.css';
import './style.css';

type Repo = { id: string; name: string; enabled: boolean };
type Plan = { packages: number; package_bytes: number; required_free: number;
  free_bytes: number; within_budget: boolean; enough_space: boolean };
type Job = { id: string; operation: string; status: string; stage: string; created: string;
  snapshot_id?: string; plan?: Plan; result?: { path?: string; sha256?: string; bytes?: number;
  files_verified?: number; authenticated?: boolean } };
type Snapshot = { id: string; created: string; status: string; package_bytes: number;
  repositories: Record<string, { packages: number; metadata_types: string[] }> };
type State = { version: string; role: string; os: { name: string; major: string; arch: string };
  storage: { path: string; free: number; total: number; reserve: number };
  repositories: Repo[]; profile: { repositories: string[]; max_gib: number };
  jobs: Job[]; snapshots: Snapshot[] };
type CockpitProcess = Promise<string> & { input: (data: string) => void };
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
function date(value: string) { return new Date(value).toLocaleString(); }
function errorText(error: unknown) { return error instanceof Error ? error.message : String(error); }
function color(status: string): 'green' | 'red' | 'blue' | 'orange' | 'grey' {
  return status === 'succeeded' || status === 'validated' ? 'green' :
    status === 'failed' ? 'red' : status === 'running' ? 'blue' :
      status === 'interrupted' || status === 'cancelled' ? 'orange' : 'grey';
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
  const [log, setLog] = useState<{ id: string; text: string }>();
  const [ready, setReady] = useState(false);
  const refresh = async () => {
    const next = await call<State>(['status']);
    setState(next);
    return next;
  };
  useEffect(() => {
    let mounted = true;
    let inFlight = false;
    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const next = await call<State>(['status']);
        if (!mounted) return;
        setState(next); setError('');
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
    <header className="dc-header"><div><div className="dc-eyebrow">CONTENT OPERATIONS</div>
      <Title headingLevel="h1" size="2xl">Disconnected Content</Title>
      <p>Build verified repository snapshots for transfer across the air gap.</p></div>
      <div className="dc-labels"><Label color="blue">Connected side</Label><Label color="orange">Prototype {state?.version ?? '0.1.0'}</Label></div>
    </header>
    {error && <Alert variant="danger" title="Operation could not complete" isInline><p>{error}</p></Alert>}
    {notice && <Alert variant="success" title={notice} isInline />}
    {!state ? <div className="dc-loading">{!error && <Spinner size="lg" />}<p>Enable administrative access in the web console to connect to the local engine.</p>
      <Button variant="secondary" onClick={() => { void act(refresh); }}>Retry connection</Button></div> : <>
      <div className="dc-host">{state.os.name} · {state.os.arch} · {state.storage.path}</div>
      <Tabs activeKey={tab} onSelect={(_, key) => setTab(key)} aria-label="Disconnected content sections">
        <Tab eventKey="overview" title={<TabTitleText>Overview</TabTitleText>} />
        <Tab eventKey="profile" title={<TabTitleText>Repositories</TabTitleText>} />
        <Tab eventKey="snapshots" title={<TabTitleText>Snapshots <Badge isRead>{state.snapshots.length}</Badge></TabTitleText>} />
        <Tab eventKey="jobs" title={<TabTitleText>Jobs &amp; evidence</TabTitleText>} />
      </Tabs>
      <section className="dc-content">
        {tab === 'overview' && <>
          <div className="dc-stats">
            <Card><CardTitle>Available storage</CardTitle><CardBody><div className="dc-metric">{bytes(state.storage.free)}</div>
              <p>{bytes(state.storage.reserve)} reserved for the operating system and other workloads</p></CardBody></Card>
            <Card><CardTitle>Repository profile</CardTitle><CardBody><div className="dc-metric">{state.profile.repositories.length} {state.profile.repositories.length === 1 ? 'repository' : 'repositories'}</div>
              <p>{state.profile.max_gib} GiB maximum declared RPM content per snapshot</p></CardBody></Card>
            <Card><CardTitle>Validated snapshots</CardTitle><CardBody><div className="dc-metric">{state.snapshots.length}</div>
              <p>Only completed, verified snapshots can be exported</p></CardBody></Card>
          </div>
          <Card><CardTitle>{active ? 'Current operation' : 'Ready for the next transfer'}</CardTitle><CardBody>
            {active ? <div className="dc-current"><Spinner size="md" /><div><strong>{active.operation}</strong><p>{active.stage}</p>
              <small>Runs in a system service. You can close this page and return later.</small></div>
              <Button variant="danger" isDisabled={busy} onClick={() => { void act(() => call(['cancel', active.id])); }}>Stop job</Button></div> : <>
              <p>Choose repositories, check their size, then sync a complete snapshot with the original metadata.</p>
              <div className="dc-actions"><Button onClick={() => setTab('profile')}>Configure repositories</Button>
                <Button variant="secondary" isDisabled={disabled || !selected.length} onClick={() => { void start('preflight'); }}>Check size</Button>
                <Button variant="secondary" isDisabled={disabled || !selected.length} onClick={() => { void start('sync'); }}>Sync snapshot</Button></div></>}
          </CardBody></Card>
          {latestPlan && <Card><CardTitle>Latest size preflight</CardTitle><CardBody>
            <div className="dc-plan"><span><strong>{latestPlan.packages.toLocaleString()}</strong> packages</span>
              <span><strong>{bytes(latestPlan.package_bytes)}</strong> declared RPM content</span>
              <span><strong>{bytes(latestPlan.required_free)}</strong> free space required, including export allowance</span></div>
            <Label color={latestPlan.within_budget && latestPlan.enough_space ? 'green' : 'red'}>
              {latestPlan.within_budget && latestPlan.enough_space ? 'Fits the saved budget and available storage' : 'Exceeds the saved budget or available storage'}</Label>
            <p className="dc-muted">A new size preflight is performed for every sync. Metadata refresh may use additional cache space.</p>
          </CardBody></Card>}
          <Alert variant="info" isInline title="Connected-side prototype">
            <p>Import, publication, and fleet patching will be added after testing on a disconnected host. Transfer bundles currently carry checksums, without a cryptographic signature.</p>
          </Alert>
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
          {!state.snapshots.length && <Card><CardTitle>No validated snapshots yet</CardTitle><CardBody>Complete a sync to create the first snapshot.</CardBody></Card>}
          {state.snapshots.map(snapshot => <Card key={snapshot.id}><CardTitle><span>Snapshot {snapshot.id.slice(0, 8)}</span> <Label color="green">Validated</Label></CardTitle><CardBody>
            <p>{date(snapshot.created)} · {bytes(snapshot.package_bytes)} of RPM content</p>
            <ul className="dc-snapshot-repos">{Object.entries(snapshot.repositories).map(([rid, result]) => <li key={rid}>
              <strong>{rid}</strong><br />{result.packages.toLocaleString()} packages · metadata and package checksums verified · RPM signatures verified<br />
              <span className="dc-muted">Advisory metadata: {result.metadata_types.includes('updateinfo') ? 'present' : 'not supplied by this repository'}</span></li>)}</ul>
            <div className="dc-actions"><Button isDisabled={disabled} onClick={() => { void start('export', snapshot.id); }}>Build transfer bundle</Button>
              <Button variant="secondary" isDisabled={disabled} onClick={() => { void start('verify-export', snapshot.id); }}>Verify existing bundle</Button></div>
          </CardBody></Card>)}
        </>}
        {tab === 'jobs' && <>
          {!state.jobs.length && <Card><CardBody>No jobs have run yet.</CardBody></Card>}
          {state.jobs.map(job => <Card key={job.id}><CardTitle><span className="dc-job-name">{job.operation}</span> <Label color={color(job.status)}>{job.status}</Label></CardTitle><CardBody>
            <p>{job.stage}</p><p className="dc-muted">{date(job.created)} · {job.id}</p>
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

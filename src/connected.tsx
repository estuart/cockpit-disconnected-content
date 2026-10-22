import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, CardBody, CardTitle, Checkbox, Dropdown, DropdownItem, DropdownList, Label,
  MenuToggle, TextInput } from '@patternfly/react-core';
import { ExportRecord, Job, State, bytes, call, errorText, plural, shortDate } from './api';
import { DirectoryPicker } from './picker';
import { Ago, Hero, JobProgress, Steps, StorageLine, Timestamp, runningLead } from './ui';

export type Ctx = { state: State; busy: boolean; active?: Job;
  act: (fn: () => Promise<unknown>, message?: string) => Promise<void>;
  start: (operation: string, target?: string) => Promise<void>; stop: (id: string) => void; go: (tab: string) => void;
  refresh: () => Promise<unknown> };

const jobSteps: Record<string, [string, string][]> = {
  'sync-export': [['space', 'Check space'], ['download', 'Download packages'], ['validate', 'Validate signatures'], ['bundle', 'Build bundle'], ['verify', 'Verify bundle']],
  sync: [['space', 'Check space'], ['download', 'Download packages'], ['validate', 'Validate signatures']],
  export: [['bundle', 'Build bundle'], ['verify', 'Verify bundle']],
  'verify-export': [['verify', 'Verify bundle']],
  preflight: [['space', 'Check repository sizes']],
};
const runningTitles: Record<string, string> = { 'sync-export': 'Syncing & building bundle…', sync: 'Syncing…', export: 'Building bundle…',
  'verify-export': 'Verifying bundle…', preflight: 'Checking repository sizes…' };
const failureTitles: Record<string, string> = { 'sync-export': 'Sync & build failed', sync: 'Sync failed', export: 'Bundle build failed',
  'verify-export': 'Bundle verification failed', preflight: 'Size check failed' };

function exportLabel(bundle?: ExportRecord | null) {
  if (!bundle) return 'Not bundled';
  return { available: 'Bundle ready', missing: 'Bundle files missing', changed: 'Bundle changed · verify again', unverified: 'Older bundle · verify before transfer' }[bundle.state];
}

function CopyPath({ path, ctx }: { path: string; ctx: Ctx }) {
  return <div className="dc-file"><code>{path}</code>
    <Button variant="secondary" size="sm" aria-label={`Copy ${path}`}
      onClick={() => { void ctx.act(() => navigator.clipboard.writeText(path), 'Path copied'); }}>Copy path</Button></div>;
}

function MoreActions({ ctx, snapshotId }: { ctx: Ctx; snapshotId: string }) {
  const [open, setOpen] = useState(false);
  const disabled = ctx.busy || Boolean(ctx.active);
  return <Dropdown isOpen={open} onOpenChange={setOpen} popperProps={{ position: 'right' }}
    toggle={ref => <MenuToggle ref={ref} variant="plain" aria-label="More bundle actions" onClick={() => setOpen(!open)}>⋮</MenuToggle>}>
    <DropdownList>
      <DropdownItem isDisabled={disabled} onClick={() => { setOpen(false); void ctx.start('verify-export', snapshotId); }}>Verify bundle again</DropdownItem>
      <DropdownItem isDisabled={disabled} onClick={() => { setOpen(false); void ctx.start('export', snapshotId); }}>Rebuild bundle from this snapshot</DropdownItem>
      <DropdownItem isDisabled={disabled} onClick={() => { setOpen(false); void ctx.start('preflight'); }}>Check repository sizes</DropdownItem>
    </DropdownList>
  </Dropdown>;
}

export function ConnectedOverview({ ctx }: { ctx: Ctx }) {
  const { state, busy } = ctx;
  const flow = state.connected!;
  const { phase, active, selection_snapshot: snapshot, attention_job: attention, latest_export: lastExport } = flow;
  const bundle = snapshot?.export;
  const selected = state.profile.repositories.length;
  const failed = attention?.operation ?? '';
  const budgetFailure = phase === 'failed' && ['sync', 'sync-export', 'preflight'].includes(failed) && attention?.plan &&
    (!attention.plan.within_budget || !attention.plan.enough_space);
  const retry = flow.action === 'sync' || flow.action === 'sync-export' ? 'sync-export' : flow.action;
  const syncing = active ? ['sync', 'sync-export', 'export', 'verify-export', 'preflight'].includes(active.operation) : false;
  const steps = [
    { title: 'Choose repositories', detail: selected ? `${selected} selected` : 'Nothing selected yet', state: selected ? 'done' : 'now' },
    { title: 'Sync & build bundle', detail: phase === 'ready' && bundle?.verified_at ? `Verified ${new Date(bundle.verified_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}` :
      syncing ? 'In progress' : phase === 'failed' ? 'Needs attention' : 'Next', state: phase === 'ready' ? 'done' : phase === 'failed' ? 'failed' : selected ? 'now' : 'todo' },
    { title: 'Transfer both files', detail: 'Use your approved transfer process', state: phase === 'ready' ? 'now' : 'todo' },
  ] as const;
  let hero: React.ReactNode;
  if (active) {
    hero = <Hero title={runningTitles[active.operation] ?? 'Working…'} lead={runningLead(active)}>
      <Steps label="Connected workflow" steps={[...steps]} />
      <JobProgress job={active} steps={jobSteps[active.operation] ?? [['', active.stage]]} busy={busy} onStop={() => ctx.stop(active.id)} />
    </Hero>;
  } else if (phase === 'ready' && bundle && snapshot) {
    hero = <Hero tone="done" title="✓ Bundle ready to transfer"
      lead={<>Verified <Ago value={bundle.verified_at} /> from content synced <Ago value={snapshot.created} /> · {plural(Object.keys(snapshot.repositories).length, 'repository', 'repositories')} · {bytes(bundle.bytes)}</>}
      actions={<><Button variant="secondary" isDisabled={busy} onClick={() => { void ctx.start('sync-export'); }}>Sync &amp; build new bundle</Button>
        <MoreActions ctx={ctx} snapshotId={snapshot.id} /></>}>
      <Steps label="Connected workflow" steps={[...steps]} />
      <div className="dc-files" aria-label="Transfer files"><CopyPath path={bundle.path} ctx={ctx} /><CopyPath path={bundle.checksum_path} ctx={ctx} /></div>
      <p className="dc-muted">{bundle.bundled_signing_keys ? `Includes ${plural(bundle.bundled_signing_keys, 'public signing key')}. The disconnected host asks for approval of any it doesn't already trust.` :
        'Copy both files together. The disconnected host checks the archive against its checksum file.'}</p>
    </Hero>;
  } else if (phase === 'failed') {
    hero = <Hero tone="failed" title={failureTitles[failed] ?? 'Last job failed'} lead={attention?.stage}
      actions={<>{budgetFailure ? <Button onClick={() => ctx.go('profile')}>Review repositories</Button> :
        <Button isDisabled={busy} onClick={() => { void ctx.start(retry ?? 'sync-export', retry === 'export' ? snapshot?.id : undefined); }}>
          {retry === 'export' ? 'Rebuild bundle' : retry === 'preflight' ? 'Check sizes again' : 'Retry sync & build'}</Button>}
        <Button variant="secondary" onClick={() => ctx.go('jobs')}>View details</Button></>}>
      <Steps label="Connected workflow" steps={[...steps]} />
    </Hero>;
  } else if (phase === 'select') {
    hero = <Hero title="Choose repositories to mirror" lead="Pick the repositories to carry across the air gap. Your selection saves automatically."
      actions={<Button onClick={() => ctx.go('profile')}>Choose repositories</Button>}>
      <Steps label="Connected workflow" steps={[...steps]} />
    </Hero>;
  } else if (phase === 'export' && snapshot) {
    hero = <Hero title="Build a transfer bundle" lead={<>Content synced <Ago value={snapshot.created} /> hasn't been bundled yet.</>}
      actions={<Button isDisabled={busy} onClick={() => { void ctx.start('export', snapshot.id); }}>Build bundle</Button>}>
      <Steps label="Connected workflow" steps={[...steps]} />
    </Hero>;
  } else if (phase === 'verify' && snapshot) {
    hero = <Hero title="Check the existing bundle" lead={bundle?.reason}
      actions={<><Button isDisabled={busy} onClick={() => { void ctx.start('verify-export', snapshot.id); }}>Verify bundle</Button>
        <Button variant="secondary" isDisabled={busy} onClick={() => { void ctx.start('export', snapshot.id); }}>Rebuild bundle</Button></>}>
      <Steps label="Connected workflow" steps={[...steps]} />
    </Hero>;
  } else {
    hero = <Hero title="Ready to sync" lead={flow.selection_changed ? 'Your repository selection changed since the last sync.' : 'Download the selected repositories and build a verified transfer bundle in one step.'}
      actions={<Button isDisabled={busy} onClick={() => { void ctx.start('sync-export'); }}>Sync &amp; build bundle</Button>}>
      <Steps label="Connected workflow" steps={[...steps]} />
    </Hero>;
  }
  const older = state.snapshots.filter(item => item.id !== snapshot?.id);
  return <>
    {hero}
    {active && lastExport?.state === 'available' && <Alert variant="info" isInline isPlain
      title={`Your previous bundle (${shortDate(lastExport.exported_at)}) stays available until this one finishes.`} />}
    {flow.selected_repositories.length > 0 && <Card><CardTitle><span className="dc-card-title-row">{phase === 'ready' ? 'Repositories in this bundle' : 'Selected repositories'}
      <Button variant="link" isInline onClick={() => ctx.go('profile')}>Change selection</Button></span></CardTitle><CardBody>
      <div className="dc-table-scroll"><table className="dc-table" aria-label="Selected repository status">
        <thead><tr><th>Repository</th><th>Packages</th><th>Size</th><th>Synced</th></tr></thead>
        <tbody>{flow.selected_repositories.map(repo => <tr key={repo.id}>
          <td><strong>{state.repositories.find(known => known.id === repo.id)?.name ?? repo.id}</strong><code>{repo.id}</code></td>
          <td>{repo.repository ? repo.repository.packages.toLocaleString() : '—'}</td>
          <td>{repo.repository ? bytes(repo.repository.package_bytes) : '—'}</td>
          <td><Ago value={repo.synced_at} /></td></tr>)}</tbody></table></div>
    </CardBody></Card>}
    {older.length > 0 && <details className="dc-details dc-card-details"><summary>Older snapshots ({older.length})</summary>
      {older.map(item => <div className="dc-row" key={item.id}><div><strong><Timestamp value={item.created} /></strong>
        <span className="dc-muted">{plural(Object.keys(item.repositories).length, 'repository', 'repositories')} · {bytes(item.package_bytes)} · {exportLabel(item.export)}</span>
        {item.export && item.export.state !== 'missing' && <code>{item.export.path}</code>}</div>
        <div className="dc-actions">
          <Button variant="secondary" size="sm" isDisabled={busy || Boolean(active)} onClick={() => { void ctx.start('export', item.id); }}>{item.export ? 'Rebuild bundle' : 'Build bundle'}</Button>
          {item.export && item.export.state !== 'missing' && <Button variant="link" size="sm" isDisabled={busy || Boolean(active)}
            onClick={() => { void ctx.start('verify-export', item.id); }}>Verify</Button>}</div></div>)}
    </details>}
    <StorageLine state={state} />
  </>;
}

export function Repositories({ ctx }: { ctx: Ctx }) {
  const { state, busy, active } = ctx;
  const [selected, setSelected] = useState(state.profile.repositories);
  const [filter, setFilter] = useState('');
  const [save, setSave] = useState<{ state: 'idle' | 'saving' | 'saved' | 'error'; message?: string }>({ state: 'idle' });
  const queue = useRef(Promise.resolve());
  const persist = (repositories: string[], maxGib = state.profile.max_gib) => {
    if (!repositories.length) { setSave({ state: 'error', message: 'Select at least one repository' }); return; }
    setSave({ state: 'saving' });
    // Serialize saves so a fast series of clicks lands in order.
    queue.current = queue.current.then(() => call(['save-profile'], { repositories, max_gib: maxGib }))
      .then(() => ctx.refresh()).then(() => setSave({ state: 'saved' })).catch(e => setSave({ state: 'error', message: errorText(e) }));
  };
  const toggle = (id: string, checked: boolean) => {
    const next = checked ? [...selected, id] : selected.filter(r => r !== id);
    setSelected(next); persist(next);
  };
  const saved = state.profile.repositories;
  const plan = state.connected?.plan_job?.plan;
  const planFits = plan && plan.within_budget && plan.enough_space;
  const unsaved = save.state === 'saving' || selected.join() !== saved.join();
  const history = (id: string) => state.connected?.selected_repositories.find(known => known.id === id);
  const shown = state.repositories.filter(r => `${r.id} ${r.name}`.toLowerCase().includes(filter.toLowerCase()));
  return <div className="dc-split">
    <Card><CardBody>
      <div className="dc-card-title-row"><h2 className="dc-hero-title">Choose repositories to mirror</h2>
        <span className={`dc-save dc-save-${save.state}`} role="status">{save.state === 'saving' ? 'Saving…' : save.state === 'saved' ? '✓ Selection saved' : save.state === 'error' ? save.message : ''}</span></div>
      <p className="dc-muted">Repositories configured on this host. Mirroring doesn't change which repositories this host uses for its own updates.</p>
      {active && <Alert variant="info" isInline isPlain title="Selection is locked while a job runs." />}
      <TextInput aria-label="Filter repositories" placeholder="Filter repositories" value={filter} onChange={(_, v) => setFilter(v)} className="dc-filter" />
      <div className="dc-repos">{shown.map(repo => {
        const known = history(repo.id);
        return <div key={repo.id} className="dc-repo">
          <Checkbox id={`repo-${repo.id}`} label={repo.name} description={<code>{repo.id}</code>} isChecked={selected.includes(repo.id)}
            isDisabled={Boolean(active)} onChange={(_, checked) => toggle(repo.id, checked)} />
          <span className="dc-repo-meta">{known?.repository ? <>{plural(known.repository.packages, 'pkg')}<br />{bytes(known.repository.package_bytes)}</> :
            repo.enabled ? <Label isCompact color="grey">Enabled on this host</Label> : null}</span></div>;
      })}</div>
    </CardBody></Card>
    <Card className="dc-summary"><CardBody>
      <h3>Selected</h3>
      <div className="dc-big">{plural(selected.length, 'repository', 'repositories')}</div>
      {plan && !unsaved ? <p><span className={planFits ? 'dc-ok' : 'dc-bad'}>{planFits ? '✓ Fits' : '✕ Too large'}</span>{' '}
        <span className="dc-muted">{bytes(plan.package_bytes)} of packages · needs {bytes(plan.required_free)} free, {bytes(plan.free_bytes)} available{!plan.within_budget ? ` · over the ${state.profile.max_gib} GiB limit` : ''}</span></p> :
        <p className="dc-muted">Size not checked yet. <Button variant="link" isInline isDisabled={busy || Boolean(active) || unsaved || !saved.length}
          onClick={() => { void ctx.start('preflight'); }}>Check size</Button></p>}
      <Button isBlock isDisabled={busy || Boolean(active) || unsaved || !saved.length} onClick={() => { void ctx.start('sync-export'); }}>Sync &amp; build bundle</Button>
      <p className="dc-muted">Downloads packages, checks their signatures, then builds and verifies a transfer bundle as one job. Every run checks free space first.</p>
      <p className="dc-muted">Size limit {state.profile.max_gib} GiB per snapshot · bundles go to <code>{state.export_settings?.path}</code> ·{' '}
        <Button variant="link" isInline onClick={() => ctx.go('settings')}>Change in Settings</Button></p>
    </CardBody></Card>
  </div>;
}

export function ConnectedSettings({ ctx }: { ctx: Ctx }) {
  const { state, busy, active } = ctx;
  const settings = state.export_settings;
  const [path, setPath] = useState(settings?.path ?? '');
  const [budget, setBudget] = useState(String(state.profile.max_gib));
  const [picker, setPicker] = useState(false);
  useEffect(() => { setBudget(String(state.profile.max_gib)); }, [state.profile.max_gib]);
  const disabled = busy || Boolean(active);
  const saveFolder = (value: string) => ctx.act(async () => {
    const result = await call<{ path?: string }>(['save-export-settings'], { export_dir: value }); setPath(result.path ?? value);
  }, 'Export folder saved');
  return <Card><CardBody>
    {picker && <DirectoryPicker title="Choose export folder" note="Choosing a folder saves it. New bundles are written there."
      initialPath={settings?.available ? settings.path : '/'} isDisabled={disabled} onClose={() => setPicker(false)}
      onSelect={value => { setPicker(false); setPath(value); void saveFolder(value); }} />}
    {active && <Alert variant="info" isInline isPlain title="Settings are locked while a job runs." />}
    <div className="dc-setting"><div><h3>Export folder</h3><p className="dc-muted">Where new transfer bundles and their checksum files are written, such as a mounted transfer drive.</p></div>
      <div><div className="dc-inline-form"><TextInput aria-label="Export folder" value={path} isDisabled={disabled} onChange={(_, v) => setPath(v)} />
        <Button variant="secondary" isDisabled={disabled} onClick={() => setPicker(true)}>Browse…</Button>
        {path !== settings?.path && <Button isDisabled={disabled || !path.trim()} onClick={() => { void saveFolder(path); }}>Save</Button>}</div>
        <p className="dc-muted"><span className={settings?.available ? 'dc-ok' : 'dc-bad'}>● {settings?.available ? `Available · ${bytes(settings.free ?? 0)} free` : settings?.error ?? 'Unavailable'}</span>
          {settings && !settings.default && <> · <Button variant="link" isInline isDisabled={disabled} onClick={() => { void saveFolder(''); }}>Use the default folder</Button></>}</p>
        <p className="dc-muted">Existing bundles stay where they were written. Picking a folder with Browse saves it right away.</p></div></div>
    <div className="dc-setting"><div><h3>Size limit per snapshot</h3><p className="dc-muted">A sync stops before downloading if the selection is larger than this.</p></div>
      <div><div className="dc-inline-form dc-narrow-form"><TextInput aria-label="Size limit per snapshot (GiB)" type="number" min={1} max={2000} value={budget}
        isDisabled={disabled} onChange={(_, v) => setBudget(v)} /><span>GiB</span>
        <Button variant="secondary" isDisabled={disabled || !state.profile.repositories.length || Number(budget) === state.profile.max_gib}
          onClick={() => { void ctx.act(() => call(['save-profile'], { repositories: state.profile.repositories, max_gib: Number(budget) }), 'Size limit saved'); }}>Save</Button></div>
        {!state.profile.repositories.length && <p className="dc-muted">Choose repositories first; the limit is saved with the selection.</p>}</div></div>
    <div className="dc-setting"><div><h3>Storage</h3><p className="dc-muted">Set by the administrator in <code>/etc/disconnected-content/config.json</code></p></div>
      <div><p>Snapshots are kept in <code>{state.storage.path}</code> · {bytes(state.storage.free)} free</p>
        <p className="dc-muted">{bytes(state.storage.reserve)} is always left free for the operating system and other workloads.</p></div></div>
    <div className="dc-setting"><div><h3>Repository sources</h3><p className="dc-muted">Where packages and signing keys come from.</p></div>
      <div><p>Packages come from this host's DNF repositories in <code>/etc/yum.repos.d</code>. Signing keys come from each repository's <code>gpgkey</code> setting.</p>
        <p className="dc-muted">HTTPS certificates are always verified. To use a repository behind a private certificate authority,
          add the CA to this host's trust store (<code>update-ca-trust</code>) instead of turning verification off.</p></div></div>
  </CardBody></Card>;
}

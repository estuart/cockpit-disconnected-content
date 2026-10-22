import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, CardBody, CardTitle, Checkbox, Label, Modal, ModalBody, ModalFooter, ModalHeader,
  Spinner, TextInput } from '@patternfly/react-core';
import { BundleInfo, IncomingBundle, KeyCandidate, KeyReview, KeyScan, bytes, call, errorText,
  groupFingerprint, plural, shortDate, snapshotLabel } from './api';
import { Ctx } from './connected';
import { DirectoryPicker } from './picker';
import { Ago, Hero, JobProgress, Steps, StorageLine, Timestamp, runningLead } from './ui';

const jobSteps: Record<string, [string, string][]> = {
  import: [['checksum', 'Check archive checksum'], ['extract', 'Unpack to private staging'], ['validate', 'Validate signatures']],
  publish: [['validate', 'Revalidate packages'], ['copy', 'Copy to web folder'], ['verify', 'Verify public copy'], ['serve', 'Start web server']],
};

/** Bundles that haven't been tried yet and have a checksum file. */
export function newBundles(incoming: IncomingBundle[]) {
  return incoming.filter(b => (b.state ?? 'awaiting_import') === 'awaiting_import' && b.has_checksum);
}
function bundleKey(bundle: IncomingBundle) { return `${bundle.name}:${bundle.bytes}:${bundle.modified}`; }

/** Manifest summaries are fetched once per file version; they are display-only until import verifies everything. */
export function useBundleInfo(bundles: IncomingBundle[]) {
  const [info, setInfo] = useState<Record<string, BundleInfo | { error: string }>>({});
  useEffect(() => {
    for (const bundle of bundles) {
      const key = bundleKey(bundle);
      if (info[key] || !bundle.has_checksum || bundle.state === 'blocked') continue;
      setInfo(old => ({ ...old, [key]: { error: '' } }));
      void call<BundleInfo>(['bundle-info'], { bundle_name: bundle.name })
        .then(result => setInfo(old => ({ ...old, [key]: result })))
        .catch(e => setInfo(old => ({ ...old, [key]: { error: errorText(e) } })));
    }
  }, [bundles.map(bundleKey).join('|')]);
  return (bundle: IncomingBundle) => { const value = info[bundleKey(bundle)]; return value && 'snapshot_id' in value ? value : undefined; };
}

function BundleSummary({ bundle, info }: { bundle: IncomingBundle; info?: BundleInfo }) {
  const keys = info ? Object.values(info.signing_keys).reduce((a, b) => a + b, 0) : 0;
  return <div>
    <strong className="dc-bundle-name">{bundle.name}</strong>
    <p className="dc-muted">{bytes(bundle.bytes)}{info?.created && <> · built on the connected host <Timestamp value={info.created} /></>}
      {info && <> · {plural(Object.keys(info.repositories).length, 'repository', 'repositories')}</>}
      {' · '}{bundle.has_checksum ? 'checksum file present' : 'checksum file missing'}</p>
    {info && !info.matches_host && <p className="dc-bad">Built for RHEL {info.os.major} {info.os.arch}, which doesn't match this host.</p>}
    {keys > 0 && !['imported', 'known'].includes(bundle.state ?? '') && <p>🔑 Includes {plural(keys, 'public signing key')}. You'll confirm any this host doesn't already trust.</p>}
    {bundle.state && !['awaiting_import'].includes(bundle.state) && <p className="dc-muted">{bundle.reason}</p>}
  </div>;
}

type Flow = { bundle: string; stage: 'scanning' | 'review' | 'starting' | 'error'; queue: KeyCandidate[];
  skipped: KeyCandidate[]; review?: KeyReview; error?: string; total: number };

/** Verify the archive, ask only about keys that are neither approved nor host-trusted, then start the import. */
export function ImportFlow({ ctx, bundle, onDone }: { ctx: Ctx; bundle: { name: string; keys: number }; onDone: () => void }) {
  const [flow, setFlow] = useState<Flow>({ bundle: bundle.name, stage: 'scanning', queue: [], skipped: [], total: 0 });
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const begin = async () => {
    setFlow(old => ({ ...old, stage: 'starting' }));
    try { await ctx.start('import', bundle.name); onDone(); } catch (e) { setFlow(old => ({ ...old, stage: 'error', error: errorText(e) })); }
  };
  const next = async (queue: KeyCandidate[], skipped: KeyCandidate[], total: number) => {
    setConfirmed(false);
    if (!queue.length) { setFlow(old => ({ ...old, queue, skipped })); await begin(); return; }
    const review = await call<KeyReview>(['review-signing-key'], { bundle_key: queue[0].candidate_id });
    setFlow({ bundle: bundle.name, stage: 'review', queue, skipped, review, total });
  };
  useEffect(() => {
    let mounted = true;
    (async () => {
      if (!bundle.keys) { await begin(); return; }
      const scan = await call<KeyScan>(['scan-bundle-keys'], { bundle_name: bundle.name });
      if (!mounted) return;
      const ask = scan.candidates.filter(c => !c.approved && !c.host_trusted && c.fingerprint);
      const unreadable = scan.candidates.filter(c => !c.fingerprint);
      await next(ask, unreadable, ask.length);
    })().catch(e => { if (mounted) setFlow(old => ({ ...old, stage: 'error', error: errorText(e) })); });
    return () => { mounted = false; };
  }, []);
  const step = async (approve: boolean) => {
    const [current, ...rest] = flow.queue;
    setBusy(true);
    try {
      if (approve && flow.review) await call(['trust-signing-key'], { review_id: flow.review.review_id, fingerprint: flow.review.fingerprint, confirmed: true });
      await next(rest, approve ? flow.skipped : [...flow.skipped, current], flow.total);
    } catch (e) { setFlow(old => ({ ...old, stage: 'error', error: errorText(e) })); } finally { setBusy(false); }
  };
  const review = flow.review;
  const position = flow.total - flow.queue.length + 1;
  const last = flow.queue.length === 1;
  return <Modal isOpen variant="medium" onClose={onDone} aria-labelledby="dc-import-title">
    <ModalHeader labelId="dc-import-title" title={flow.stage === 'review' && review ? <>Approve signing key for <code>{review.repository_id}</code></> : `Import ${bundle.name}`}
      description={flow.stage === 'review' ? `Import ${bundle.name} · key ${position} of ${flow.total}` : undefined} />
    <ModalBody>
      {flow.stage === 'scanning' && <div className="dc-wait"><Spinner size="lg" /><p>Verifying the bundle before showing its signing keys. Large bundles can take a few minutes.</p></div>}
      {flow.stage === 'starting' && <div className="dc-wait"><Spinner size="lg" /><p>Starting the import…</p></div>}
      {flow.stage === 'error' && <Alert variant="danger" isInline title="The import didn't start">{flow.error}</Alert>}
      {flow.stage === 'review' && review && <>
        <dl className="dc-kv">
          <dt>Identity</dt><dd>{review.user_ids.join(' · ') || 'No user ID'}</dd>
          <dt>Expires</dt><dd>{review.expires ? <Timestamp value={review.expires} /> : 'No expiration'}</dd>
          <dt>Source</dt><dd>Included in this bundle</dd>
        </dl>
        <p className="dc-label-text">Full fingerprint</p>
        <div className="dc-fingerprint" aria-label="Full fingerprint">{groupFingerprint(review.fingerprint)}</div>
        <Alert variant="warning" isInline isPlain title="Being in the bundle doesn't prove who made this key.">
          Compare the full fingerprint with the vendor's published value or your organization's records.</Alert>
        <Checkbox id="confirm-signing-key" isChecked={confirmed} isDisabled={busy} onChange={(_, checked) => setConfirmed(checked)}
          label={<>I compared the full fingerprint with a trusted source and approve this key for <strong>{review.repository_id}</strong>.</>} />
        <p className="dc-muted">Approval applies only inside Disconnected Content for this repository. It isn't added to this host's RPM keyring.
          If you skip this key, the import fails if any package needs it.</p>
      </>}
    </ModalBody>
    <ModalFooter>
      {flow.stage === 'review' && <><Button isDisabled={!confirmed || busy} onClick={() => { void step(true); }}>{last ? 'Approve & import' : 'Approve & continue'}</Button>
        <Button variant="secondary" isDisabled={busy} onClick={() => { void step(false); }}>Skip this key</Button></>}
      <Button variant="link" onClick={onDone}>{flow.stage === 'error' ? 'Close' : 'Cancel'}</Button>
    </ModalFooter>
  </Modal>;
}

function ImportButton({ ctx, bundle, info, primary = true, onImport }: { ctx: Ctx; bundle: IncomingBundle; info?: BundleInfo; primary?: boolean;
  onImport: (bundle: { name: string; keys: number }) => void }) {
  const blocked = !bundle.has_checksum || bundle.state === 'blocked' || !ctx.state.capabilities.allow_unsigned_imports;
  const recheck = bundle.state === 'known' || bundle.state === 'imported';
  const keys = info ? Object.values(info.signing_keys).reduce((a, b) => a + b, 0) : 0;
  return <Button variant={primary ? 'primary' : 'secondary'} isDisabled={ctx.busy || Boolean(ctx.active) || blocked}
    onClick={() => recheck ? void ctx.start('import', bundle.name) : onImport({ name: bundle.name, keys: info ? keys : 1 })}>
    {recheck ? 'Check again' : keys ? 'Review & import' : 'Import'}</Button>;
}

export function DisconnectedOverview({ ctx, onImport }: { ctx: Ctx; onImport: (bundle: { name: string; keys: number }) => void }) {
  const { state, busy } = ctx;
  const flow = state.disconnected!;
  const publication = state.publication!;
  const current = publication.current;
  const source = state.import_source!;
  const { phase, active, attention_job: attention, latest_import: latest } = flow;
  const pending = state.incoming.filter(b => ['awaiting_import', 'changed', 'blocked'].includes(b.state ?? 'awaiting_import'));
  // New bundles come first; files that failed before or can't be imported only lead when nothing new is waiting.
  const fresh = newBundles(state.incoming);
  const shown = fresh.length ? fresh : pending;
  const info = useBundleInfo(shown);
  const importFirst = !active && phase !== 'failed' && fresh.length > 0 && state.capabilities.allow_unsigned_imports;
  // Files that failed before don't take over the overview while current content is being served.
  const leftoversOnly = !fresh.length && pending.length > 0 && Boolean(publication.current) && flow.publication_serving &&
    flow.publication_matches_latest && !flow.server_url_changed;
  const [url, setUrl] = useState(publication.base_url || `http://${window.location.hostname}`);
  const [picker, setPicker] = useState(false);
  const target = state.snapshots.find(s => s.id === flow.target_snapshot_id) ?? latest;
  const servedSnapshot = state.snapshots.find(s => s.id === current?.snapshot_id);
  const publishing = !importFirst && (['publisher_setup', 'server_url', 'publish'].includes(phase) || phase === 'failed' && attention?.operation === 'publish' ||
    active?.operation === 'publish');
  const steps = [
    { title: 'Import', detail: active?.operation === 'import' ? 'In progress' : fresh.length ? plural(fresh.length, 'new bundle') :
      pending.length && phase === 'import' && !leftoversOnly ? `${plural(pending.length, 'bundle')} need attention` :
      latest ? `Imported ${shortDate(latest.created)}` : 'Waiting for a bundle',
      state: phase === 'failed' && attention?.operation === 'import' ? 'failed' : importFirst ? 'now' : publishing || phase === 'clients' || leftoversOnly ? 'done' : 'now' },
    { title: 'Publish', detail: active?.operation === 'publish' ? 'In progress' : flow.publication_matches_latest && flow.publication_serving ? `Serving ${snapshotLabel(servedSnapshot, state.snapshots)}` :
      current ? `Clients still get ${snapshotLabel(servedSnapshot, state.snapshots) === 'none' ? 'an older snapshot' : snapshotLabel(servedSnapshot, state.snapshots)}` : 'Nothing published yet',
      state: phase === 'failed' && attention?.operation === 'publish' ? 'failed' : importFirst ? 'todo' : phase === 'clients' || leftoversOnly ? 'done' : publishing ? 'now' : 'todo' },
    { title: 'Configure clients', detail: phase === 'clients' || leftoversOnly ? 'Download setup files' : 'After publishing', state: phase === 'clients' || leftoversOnly ? 'now' : 'todo' },
  ] as const;
  const stepper = <Steps label="Disconnected workflow" steps={[...steps]} />;
  let hero: React.ReactNode;
  if (active) {
    hero = <Hero title={active.operation === 'publish' ? 'Publishing…' : 'Importing bundle…'} lead={runningLead(active)}>
      {stepper}<JobProgress job={active} steps={jobSteps[active.operation] ?? [['', active.stage]]} busy={busy} onStop={() => ctx.stop(active.id)} />
    </Hero>;
  } else if (phase === 'failed' && attention) {
    const failure = attention.signature_failure;
    const bundle = state.incoming.find(b => b.name === attention.bundle_name);
    hero = <Hero tone="failed" title={attention.operation === 'publish' ? 'Publishing failed' : 'Import failed'}
      lead={failure ? <><strong>{failure.repository_id}</strong>: {failure.message}</> : attention.stage}
      actions={<>{attention.operation === 'publish' && flow.target_snapshot_id ?
        <Button isDisabled={busy} onClick={() => { void ctx.start('publish', flow.target_snapshot_id!); }}>Try again</Button> :
        bundle && <ImportButton ctx={ctx} bundle={{ ...bundle, state: 'awaiting_import' }} info={info(bundle)} onImport={onImport} />}
        {failure?.code === 'missing_key' && <Button variant="secondary" onClick={() => ctx.go('settings')}>Add a key manually</Button>}
        <Button variant="secondary" onClick={() => ctx.go('jobs')}>View details</Button></>}>
      {stepper}
    </Hero>;
  } else if (importFirst || phase === 'import' && !leftoversOnly) {
    hero = <Hero title={!fresh.length ? (shown.length === 1 ? 'A bundle needs attention' : 'Bundles need attention') :
      fresh.length === 1 ? 'New bundle ready to import' : `${fresh.length} bundles ready to import`}
      lead={<>Found in <code>{source.path}</code> · checked <Ago value={source.scanned_at} /></>}>
      {stepper}
      {shown.map(bundle => <div className="dc-files dc-bundle" key={bundle.name}><BundleSummary bundle={bundle} info={info(bundle)} />
        <ImportButton ctx={ctx} bundle={bundle} info={info(bundle)} onImport={onImport} /></div>)}
    </Hero>;
  } else if (phase === 'import_policy') {
    hero = <Hero title="Imports are turned off" lead={<>An administrator must set <code>"allow_unsigned_imports": true</code> in <code>/etc/disconnected-content/config.json</code> before this prototype can import unsigned bundles.</>}>
      {stepper}</Hero>;
  } else if (phase === 'source') {
    hero = <Hero tone="failed" title="Transfer folder isn't available" lead={source.error}
      actions={<Button onClick={() => setPicker(true)}>Choose transfer folder</Button>}>{stepper}</Hero>;
  } else if (phase === 'waiting') {
    hero = <Hero title="Waiting for a transfer bundle"
      lead={<>Copy the <code>.tar</code> and its <code>.tar.sha256</code> into <code>{source.path}</code>. This page checks automatically.</>}
      actions={<Button variant="secondary" onClick={() => setPicker(true)}>Change folder</Button>}>{stepper}</Hero>;
  } else if (phase === 'publisher_setup') {
    hero = <Hero title="Install the web publisher" lead={<>Install <code>cockpit-disconnected-content-publisher</code> to serve repositories to clients.
      Provision it before isolation or from an approved offline repository.</>}>{stepper}</Hero>;
  } else if (phase === 'server_url') {
    hero = <Hero title="Set the address clients will use" lead="Enter the URL that client hosts can reach inside the air gap.">
      {stepper}
      <div className="dc-inline-form"><TextInput aria-label="Client-facing address" value={url} onChange={(_, v) => setUrl(v)} />
        <Button isDisabled={busy || !url.trim()} onClick={() => { void ctx.act(() => call(['save-publication-settings'], { base_url: url }), 'Address saved'); }}>Save address</Button></div>
    </Hero>;
  } else if (phase === 'publish' && target) {
    const title = flow.server_url_changed ? 'Address changed. Publish again so clients get it.' : !flow.publication_matches_latest ? 'Imported and validated. Ready to publish.' :
      'Publication needs attention';
    hero = <Hero title={title} lead={flow.publication_matches_latest ? flow.reason : <>Snapshot <strong>{snapshotLabel(target, state.snapshots)}</strong> · {plural(Object.keys(target.repositories).length, 'repository', 'repositories')} ·{' '}
      {plural(Object.values(target.repositories).reduce((a, r) => a + r.packages, 0), 'package')} · all RPM signatures verified</>}>
      {stepper}
      <div className="dc-actions"><Button isDisabled={busy} onClick={() => { void ctx.start('publish', target.id); }}>{flow.publication_matches_latest ? 'Publish again' : `Publish ${snapshotLabel(target, state.snapshots)} snapshot`}</Button>
        <span className="dc-muted">Serves it at {publication.base_url}.{current && !flow.publication_matches_latest ? ' Older snapshots stay available for clients that haven\'t moved yet.' : ''}</span></div>
    </Hero>;
  } else {
    hero = <Hero tone="done" title={`✓ Clients can use the ${snapshotLabel(servedSnapshot, state.snapshots)} snapshot`}
      lead={current ? <>Published <Ago value={current.published_at} /> at {current.base_url}. Clients move to it when you give them its setup files.</> : flow.reason}
      actions={<Button onClick={() => ctx.go('clients')}>Configure clients</Button>}>{stepper}</Hero>;
  }
  return <>
    {picker && <DirectoryPicker initialPath={source.path} isDisabled={busy || Boolean(active)} onClose={() => setPicker(false)}
      onSelect={path => { setPicker(false); void ctx.act(() => call(['save-import-settings'], { incoming_dir: path }), 'Transfer folder saved'); }} />}
    {hero}
    {leftoversOnly && !active && phase === 'import' && <Alert variant="warning" isInline isPlain
      title={`${plural(pending.length, 'file')} in the transfer folder ${pending.length === 1 ? 'needs' : 'need'} attention`}>
      <Button variant="link" isInline onClick={() => ctx.go('content')}>Review in Content</Button></Alert>}
    <div className="dc-two">
      <Card><CardTitle>{current ? 'Clients are being served' : 'Nothing published yet'}</CardTitle><CardBody>
        {current ? <><p>Snapshot from <strong>{snapshotLabel(servedSnapshot, state.snapshots)}</strong> · {plural(Object.keys(current.repositories).length, 'repository', 'repositories')}</p>
          <p className="dc-muted"><span className={flow.publication_serving ? 'dc-ok' : 'dc-bad'}>●</span>{' '}
            {flow.publication_serving ? `Web server running at ${current.base_url}` : publication.availability.reason}</p></> :
          <p className="dc-muted">Import a bundle, then publish it to give clients a repository.</p>}
      </CardBody></Card>
      <Card><CardTitle>Transfer folder</CardTitle><CardBody>
        <p><code>{source.path}</code> <span className={source.available ? 'dc-ok' : 'dc-bad'}>· {source.available ? 'available' : 'unavailable'}</span></p>
        <p className="dc-muted">{plural(fresh.length, 'new bundle')} · checked automatically · <Button variant="link" isInline onClick={() => ctx.go('settings')}>Change in Settings</Button></p>
      </CardBody></Card>
    </div>
    <StorageLine state={state} />
  </>;
}

const bundleLabels: Record<string, [string, 'grey' | 'blue' | 'orange' | 'green']> = {
  awaiting_import: ['New', 'grey'], imported: ['Imported', 'green'], known: ['Already imported', 'blue'],
  changed: ['Needs another check', 'orange'], blocked: ['Can\'t import', 'orange'] };

export function Content({ ctx, onImport }: { ctx: Ctx; onImport: (bundle: { name: string; keys: number }) => void }) {
  const { state, busy, active } = ctx;
  const info = useBundleInfo(state.incoming);
  const current = state.publication?.current;
  return <>
    <Card><CardTitle><span className="dc-card-title-row">Incoming bundles
      <Button variant="link" isInline onClick={() => ctx.go('settings')}>Change folder</Button></span></CardTitle><CardBody>
      <p className="dc-muted"><code>{state.import_source?.path}</code> · checked <Ago value={state.import_source?.scanned_at} /></p>
      {state.import_source && !state.import_source.available && <Alert variant="warning" isInline title="Transfer folder unavailable">{state.import_source.error}</Alert>}
      {!state.capabilities.allow_unsigned_imports && <Alert variant="info" isInline isPlain title="Imports are turned off by the administrator." />}
      {!state.incoming.length && state.import_source?.available && <p>No bundles in this folder yet.</p>}
      {state.incoming.map(bundle => {
        const [label, color] = bundleLabels[bundle.state ?? 'awaiting_import'];
        return <div className="dc-row" key={bundle.name}><div><Label color={color} isCompact>{label}</Label>
          <BundleSummary bundle={bundle} info={info(bundle)} /></div>
          <ImportButton ctx={ctx} bundle={bundle} info={info(bundle)} primary={false} onImport={onImport} /></div>;
      })}
    </CardBody></Card>
    <Card><CardTitle>Snapshots</CardTitle><CardBody>
      {!state.snapshots.length ? <p>Import a bundle to create the first snapshot.</p> :
        <div className="dc-table-scroll"><table className="dc-table" aria-label="Snapshots">
          <thead><tr><th>Snapshot</th><th>Repositories</th><th>Size</th><th>Status</th><th /></tr></thead>
          <tbody>{state.snapshots.map(snapshot => <tr key={snapshot.id}>
            <td><strong>{snapshotLabel(snapshot, state.snapshots)}</strong><span className="dc-muted">Imported <Ago value={snapshot.created} /> · <code className="dc-inline-code">{snapshot.id.slice(0, 8)}</code></span></td>
            <td>{Object.keys(snapshot.repositories).length}</td><td>{bytes(snapshot.package_bytes)}</td>
            <td>{current?.snapshot_id === snapshot.id ? <Label color="blue">Serving clients</Label> : <Label color="green">Validated · not published</Label>}</td>
            <td>{current?.snapshot_id !== snapshot.id && <Button variant="secondary" size="sm" isDisabled={busy || Boolean(active) || !state.publication?.installed || !state.publication.base_url}
              onClick={() => { void ctx.start('publish', snapshot.id); }}>Publish</Button>}</td></tr>)}</tbody></table></div>}
      {state.snapshots.length > 0 && <details className="dc-details"><summary>Repository details and local paths</summary>
        {state.snapshots.map(snapshot => <div key={snapshot.id} className="dc-snapshot-detail"><strong>{snapshotLabel(snapshot, state.snapshots)} · {snapshot.id}</strong>
          <ul>{Object.entries(snapshot.repositories).map(([rid, repo]) => <li key={rid}><strong>{rid}</strong> · {plural(repo.packages, 'package')} ·
            advisory metadata {repo.metadata_types.includes('updateinfo') ? 'present' : 'not supplied'}<br />
            <code>{`file://${state.storage.path}/snapshots/${snapshot.id}/repositories/${rid}`}</code></li>)}</ul></div>)}
      </details>}
    </CardBody></Card>
  </>;
}

function ManualKey({ ctx }: { ctx: Ctx }) {
  const { state } = ctx;
  const [repository, setRepository] = useState('');
  const [file, setFile] = useState('');
  const [armor, setArmor] = useState('');
  const [review, setReview] = useState<KeyReview>();
  const [confirmed, setConfirmed] = useState(false);
  const reset = () => { setReview(undefined); setConfirmed(false); };
  const disabled = ctx.busy || Boolean(ctx.active);
  return <div className="dc-key-form">
    <label htmlFor="key-repository">Repository ID</label>
    <TextInput id="key-repository" value={repository} isDisabled={disabled} placeholder="example-vendor-repo" onChange={(_, v) => { setRepository(v); reset(); }} />
    {Boolean(state.signing_key_files?.length) && <><label htmlFor="host-key-file">Key file in the transfer folder</label>
      <select id="host-key-file" value={file} disabled={disabled} onChange={e => { setFile(e.target.value); setArmor(''); reset(); }}>
        <option value="">Choose a file, or paste a key below</option>{state.signing_key_files?.map(name => <option key={name}>{name}</option>)}</select></>}
    <label htmlFor="upload-key-file">Key file on your computer</label>
    <input id="upload-key-file" type="file" accept=".asc,.key,.pub" disabled={disabled} onChange={event => {
      const selected = event.target.files?.[0]; reset(); setFile('');
      if (selected) void ctx.act(async () => { if (selected.size > 65536) throw new Error('Public keys must be at most 64 KiB.'); setArmor(await selected.text()); });
    }} />
    <label htmlFor="public-key-armor">Or paste an ASCII-armored public key</label>
    <textarea id="public-key-armor" rows={5} value={armor} disabled={disabled || Boolean(file)} placeholder="-----BEGIN PGP PUBLIC KEY BLOCK-----"
      onChange={e => { setArmor(e.target.value); reset(); }} />
    <div><Button variant="secondary" isDisabled={disabled || !repository.trim() || (!file && !armor.trim())} onClick={() => { void ctx.act(async () => {
      reset(); setReview(await call<KeyReview>(['review-signing-key'], { repository_id: repository, ...(file ? { key_file: file } : { public_key: armor }) }));
    }); }}>Review key</Button></div>
    {review && <section className="dc-key-review" aria-label="Public signing key review">
      <dl className="dc-kv"><dt>Repository</dt><dd><strong>{review.repository_id}</strong></dd>
        <dt>Identity</dt><dd>{review.user_ids.join(' · ') || 'No user ID'}</dd>
        <dt>Expires</dt><dd>{review.expires ? <Timestamp value={review.expires} /> : 'No expiration'}</dd></dl>
      <div className="dc-fingerprint">{groupFingerprint(review.fingerprint)}</div>
      <Checkbox id="confirm-manual-key" isChecked={confirmed} onChange={(_, checked) => setConfirmed(checked)}
        label="I compared the full fingerprint with a trusted source and approve this key for this repository." />
      <Button isDisabled={!confirmed || disabled} onClick={() => { void ctx.act(async () => {
        await call(['trust-signing-key'], { review_id: review.review_id, fingerprint: review.fingerprint, confirmed: true });
        reset(); setArmor(''); setFile('');
      }, 'Signing key approved'); }}>Approve key</Button>
    </section>}
  </div>;
}

export function Settings({ ctx }: { ctx: Ctx }) {
  const { state, busy, active } = ctx;
  const publication = state.publication!;
  const [path, setPath] = useState(state.import_source?.path ?? '');
  const [url, setUrl] = useState(publication.base_url || `http://${window.location.hostname}`);
  const [picker, setPicker] = useState(false);
  const disabled = busy || Boolean(active);
  const saveFolder = (value: string) => ctx.act(() => call(['save-import-settings'], { incoming_dir: value }), 'Transfer folder saved');
  return <Card><CardBody>
    {picker && <DirectoryPicker initialPath={path || '/'} isDisabled={disabled} onClose={() => setPicker(false)}
      onSelect={value => { setPicker(false); setPath(value); void saveFolder(value); }} />}
    <div className="dc-setting"><div><h3>Transfer folder</h3><p className="dc-muted">Where bundles arrive. Checked automatically.</p></div>
      <div><div className="dc-inline-form"><TextInput aria-label="Transfer folder" value={path} isDisabled={disabled} onChange={(_, v) => setPath(v)} />
        <Button variant="secondary" isDisabled={disabled} onClick={() => setPicker(true)}>Browse…</Button>
        {path !== state.import_source?.path && <Button isDisabled={disabled || !path.trim()} onClick={() => { void saveFolder(path); }}>Save</Button>}</div>
        <p className="dc-muted"><span className={state.import_source?.available ? 'dc-ok' : 'dc-bad'}>● {state.import_source?.available ? 'Available' : state.import_source?.error ?? 'Unavailable'}</span>
          {' '}· picking a folder with Browse saves it right away</p></div></div>
    <div className="dc-setting"><div><h3>Client-facing address</h3><p className="dc-muted">The URL clients use to reach this host.</p></div>
      <div><div className="dc-inline-form"><TextInput aria-label="Client-facing address" value={url} isDisabled={disabled} onChange={(_, v) => setUrl(v)} />
        <Button variant="secondary" isDisabled={disabled || !url.trim() || url === publication.base_url}
          onClick={() => { void ctx.act(() => call(['save-publication-settings'], { base_url: url }), 'Address saved'); }}>Save</Button></div>
        <p className="dc-muted">Takes effect the next time you publish. HTTPS requires TLS that you configure separately.</p></div></div>
    <div className="dc-setting"><div><h3>Web publisher</h3><p className="dc-muted">Serves repositories over HTTP.</p></div>
      <div>{publication.installed ? <p className={publication.server_active ? 'dc-ok' : ''}>{publication.server_active ? '✓ Installed · web server running' : 'Installed · web server starts when you publish'}</p> :
        <p>Not installed. Install <code>cockpit-disconnected-content-publisher</code> to serve clients.</p>}
        <p className="dc-muted">Allow inbound HTTP from client networks in your firewall policy.</p></div></div>
    <div className="dc-setting"><div><h3>Approved signing keys</h3><p className="dc-muted">Keys you approved for specific repositories.</p></div>
      <div>{!state.trusted_signing_keys?.length ? <p className="dc-muted">None yet. Repositories without approved keys use this host's RPM keyring.</p> :
        <table className="dc-table" aria-label="Approved signing keys"><tbody>{state.trusted_signing_keys.flatMap(repo => repo.signing_keys.map(key =>
          <tr key={repo.repository_id + key.fingerprint}><td><strong>{repo.repository_id}</strong><span className="dc-muted">{key.user_ids.join(' · ')}</span></td>
            <td><code className="dc-wrap">{groupFingerprint(key.fingerprint)}</code></td>
            <td className="dc-muted">Approved {key.approved_at ? shortDate(key.approved_at) : 'unknown'} · {key.expires ? `expires ${shortDate(key.expires)}` : 'no expiration'}</td></tr>))}</tbody></table>}
        <details className="dc-details"><summary>Add a key manually</summary><ManualKey ctx={ctx} /></details></div></div>
    <div className="dc-setting"><div><h3>Import policy</h3><p className="dc-muted">Set by the administrator in <code>/etc/disconnected-content/config.json</code></p></div>
      <div><p>Unsigned bundles: <strong>{state.capabilities.allow_unsigned_imports ? 'allowed (prototype)' : 'not allowed'}</strong></p>
        <p className="dc-muted">Bundle checksums show that the file wasn't damaged, not who made it. RPM signatures are always checked.</p></div></div>
  </CardBody></Card>;
}

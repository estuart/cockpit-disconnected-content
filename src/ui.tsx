import React, { useState } from 'react';
import { Button, Card, CardBody, Label, Spinner } from '@patternfly/react-core';
import { Job, State, bytes, call, errorText, operationNames, plural } from './api';

export function Timestamp({ value }: { value?: string | null }) {
  if (typeof value !== 'string' || !Number.isFinite(Date.parse(value))) return <span>Not recorded</span>;
  return <time dateTime={value} title={value}>{new Date(value).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
  })}</time>;
}
export function relativeTime(value?: string | null) {
  const seconds = ((value ? Date.parse(value) : NaN) - Date.now()) / 1000;
  if (!Number.isFinite(seconds)) return 'Not recorded';
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  for (const [unit, divisor] of [['year', 31536000], ['month', 2592000], ['day', 86400], ['hour', 3600], ['minute', 60]] as const) {
    if (Math.abs(seconds) >= divisor) return formatter.format(Math.trunc(seconds / divisor), unit);
  }
  return 'just now';
}
/** Relative time with the exact timestamp on hover. */
export function Ago({ value }: { value?: string | null }) {
  if (!value || !Number.isFinite(Date.parse(value))) return <span>never</span>;
  return <time dateTime={value} title={new Date(value).toLocaleString()}>{relativeTime(value)}</time>;
}
export function duration(job: Job) {
  if (!job.started) return undefined;
  const seconds = Math.max(0, Math.floor(((job.finished ? Date.parse(job.finished) : Date.now()) - Date.parse(job.started)) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
}
export function statusColor(status: string): 'green' | 'red' | 'blue' | 'orange' | 'grey' {
  return status === 'succeeded' ? 'green' : status === 'failed' ? 'red' : status === 'running' ? 'blue' :
    status === 'interrupted' || status === 'cancelled' ? 'orange' : 'grey';
}

export type Step = { title: string; detail: string; state: 'done' | 'now' | 'todo' | 'failed' };
export function Steps({ steps, label }: { steps: Step[]; label: string }) {
  return <ol className="dc-steps" aria-label={label}>{steps.map((step, index) =>
    <li key={step.title} className={`dc-step dc-step-${step.state}`} aria-current={step.state === 'now' || step.state === 'failed' ? 'step' : undefined}>
      <span className="dc-step-dot" aria-hidden="true">{step.state === 'done' ? '✓' : step.state === 'failed' ? '!' : index + 1}</span>
      <span><strong>{step.title}</strong><small>{step.detail}</small></span>
    </li>)}</ol>;
}

export function Hero({ tone = 'next', title, lead, actions, children }: {
  tone?: 'next' | 'done' | 'failed'; title: React.ReactNode; lead?: React.ReactNode; actions?: React.ReactNode; children?: React.ReactNode;
}) {
  return <Card className={`dc-hero dc-hero-${tone}`}><CardBody>
    <div className="dc-hero-head"><div><h2 className="dc-hero-title">{title}</h2>{lead && <p className="dc-lead">{lead}</p>}</div>
      {actions && <div className="dc-actions">{actions}</div>}</div>
    {children}
  </CardBody></Card>;
}

/** Progress for a running job. Steps come from the helper's `step` field, not parsed stage text. */
export function JobProgress({ job, steps, onStop, busy }: { job: Job; steps: [string, string][]; onStop: () => void; busy: boolean }) {
  const current = Math.max(0, steps.findIndex(([key]) => key === job.step));
  return <>
    <ol className="dc-substeps" aria-label="Job progress">{steps.map(([key, label], index) =>
      <li key={key} className={index < current ? 'dc-sub-done' : index === current ? 'dc-sub-now' : ''}
        aria-current={index === current ? 'step' : undefined}>{label}</li>)}</ol>
    <div className="dc-progress" role="progressbar" aria-label="Job progress" aria-valuemin={0} aria-valuemax={steps.length}
      aria-valuenow={current}><i style={{ width: `${Math.round((current + 0.5) / steps.length * 100)}%` }} /></div>
    <div className="dc-hero-foot"><span className="dc-muted">{job.stage}</span>
      <Button variant="secondary" isDanger isDisabled={busy} onClick={onStop}>Stop</Button></div>
    <LiveOutput job={job} />
  </>;
}
export function runningLead(job: Job) {
  return <>Started <Ago value={job.started ?? job.created} />{duration(job) ? ` · ${duration(job)} elapsed` : ''} · You can close this page. The job runs as a system service.</>;
}

export function LiveOutput({ job }: { job: Job }) {
  const [text, setText] = useState<string>();
  const [error, setError] = useState('');
  const load = () => { setError(''); call<{ text: string }>(['logs', job.id]).then(value => setText(value.text)).catch(e => setError(errorText(e))); };
  return <details className="dc-details" onToggle={event => { if ((event.target as HTMLDetailsElement).open) load(); }}>
    <summary>Show command output</summary>
    {error ? <p className="dc-muted">{error}</p> : text === undefined ? <Spinner size="md" aria-label="Loading output" /> :
      <><pre className="dc-log">{text}</pre><Button variant="link" isInline onClick={load}>Refresh output</Button></>}
  </details>;
}

export function StorageLine({ state }: { state: State }) {
  const used = state.storage.total - state.storage.free;
  return <div className="dc-storage"><strong>Storage</strong>
    <span className="dc-bar" role="meter" aria-label="Storage used" aria-valuemin={0} aria-valuemax={state.storage.total} aria-valuenow={used}>
      <i style={{ width: `${Math.min(100, Math.round(used / Math.max(1, state.storage.total) * 100))}%` }} /></span>
    <span>{bytes(used)} used · {bytes(state.storage.free)} free · {plural(state.snapshots.length, 'snapshot')}</span></div>;
}

export function Activity({ state }: { state: State }) {
  const [log, setLog] = useState<{ id: string; text: string }>();
  if (!state.jobs.length) return <Card><CardBody>No jobs have run yet.</CardBody></Card>;
  return <>
    <p className="dc-muted">Newest first. Times are shown in your browser's time zone.</p>
    {state.jobs.map(job => <Card key={job.id}><CardBody>
      <div className="dc-job-head"><strong>{operationNames[job.operation] ?? job.operation}</strong>
        <Label color={statusColor(job.status)}>{job.status}</Label><span className="dc-muted"><Timestamp value={job.finished ?? job.created} /></span></div>
      <p>{job.stage}</p>
      {job.signature_failure && <details className="dc-details"><summary>RPM signature diagnostic</summary>
        <pre className="dc-log">{job.signature_failure.diagnostics}</pre></details>}
      <details className="dc-details"><summary>Details</summary>
        <dl className="dc-kv">
          <dt>Job</dt><dd><code>{job.id}</code></dd>
          <dt>Queued</dt><dd><Timestamp value={job.created} /></dd>
          {job.started && <><dt>{job.finished ? 'Duration' : 'Elapsed'}</dt><dd>{duration(job)}</dd></>}
          {job.bundle_name && <><dt>Source bundle</dt><dd><code>{job.incoming_dir ? `${job.incoming_dir}/` : ''}{job.bundle_name}</code></dd></>}
          {job.snapshot_id && <><dt>Snapshot</dt><dd><code>{job.snapshot_id}</code></dd></>}
          {job.plan && <><dt>Size</dt><dd>{plural(job.plan.packages, 'package')} · {bytes(job.plan.package_bytes)}</dd></>}
          {job.result?.path && <><dt>Bundle</dt><dd><code>{job.result.path}</code></dd></>}
          {job.result?.sha256 && <><dt>SHA-256</dt><dd><code>{job.result.sha256}</code></dd></>}
          {job.result?.source_archive_sha256 && <><dt>Archive SHA-256</dt><dd><code>{job.result.source_archive_sha256}</code></dd></>}
          {job.result?.files_verified !== undefined && <><dt>Files verified</dt><dd>{job.result.files_verified.toLocaleString()}</dd></>}
          {job.result?.config_url && <><dt>Client configuration</dt><dd><code>{job.result.config_url}</code></dd></>}
        </dl>
        <Button variant="link" isInline onClick={() => { void call<{ text: string }>(['logs', job.id]).then(value => setLog({ id: job.id, text: value.text })); }}>
          View command output</Button>
        {log?.id === job.id && <pre className="dc-log">{log.text}</pre>}
      </details>
    </CardBody></Card>)}
  </>;
}

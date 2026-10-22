import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Alert, AlertActionCloseButton, Button, Label, Popover, Spinner, Tab, Tabs, TabTitleText, Title } from '@patternfly/react-core';
import '@patternfly/patternfly/patternfly.css';
import './style.css';
import { State, call, errorText } from './api';
import { Clients } from './clients';
import { ConnectedOverview, ConnectedSettings, Ctx, Repositories } from './connected';
import { Content, DisconnectedOverview, ImportFlow, Settings, newBundles } from './disconnected';
import { Activity } from './ui';

function PrototypeNotes({ version }: { version: string }) {
  return <Popover aria-label="What this prototype verifies" headerContent="What this prototype verifies" bodyContent={<ul className="dc-notes">
    <li>Transfer bundles are unsigned. Checksums show that a file wasn't damaged, not who made it.</li>
    <li>RPM signatures are always checked. Unsigned packages and invalid signatures are blocked.</li>
    <li>Signing keys from a bundle are never trusted until you approve them.</li>
    <li>This host doesn't confirm transfers, client configuration, or updates.</li>
    <li>Independently maintained prototype. See docs/limitations.md for details.</li></ul>}>
    <button type="button" className="dc-proto"><Label color="orange">Prototype {version} ⓘ</Label></button>
  </Popover>;
}

function App() {
  const [state, setState] = useState<State>();
  const [tab, setTab] = useState<string | number>('overview');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState<{ name: string; keys: number }>();
  const mounted = useRef(true);
  const refresh = async () => {
    const next = await call<State>(['status']);
    if (mounted.current) setState(next);
    return next;
  };
  useEffect(() => {
    mounted.current = true;
    let inFlight = false;
    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      try { await refresh(); } catch (e) { if (mounted.current) setError(errorText(e)); } finally { inFlight = false; }
    };
    void poll();
    const timer = window.setInterval(() => { void poll(); }, 4000);
    return () => { mounted.current = false; window.clearInterval(timer); };
  }, []);
  const act = async (fn: () => Promise<unknown>, message?: string) => {
    setBusy(true); setError(''); setNotice('');
    try { await fn(); await refresh(); if (message) setNotice(message); }
    catch (e) { setError(errorText(e)); }
    finally { setBusy(false); }
  };
  const go = (next: string) => { setTab(next); setNotice(''); window.scrollTo?.({ top: 0 }); };
  if (!state) return <main className="dc-app"><div className="dc-loading">{!error && <Spinner size="lg" />}
    {error && <Alert variant="danger" isInline title="Can't reach the local engine">{error}</Alert>}
    <p>Turn on administrative access in the web console to connect to the local engine.</p>
    <Button variant="secondary" onClick={() => { setError(''); void refresh().catch(e => setError(errorText(e))); }}>Try again</Button></div></main>;
  const disconnected = state.role === 'disconnected';
  const active = state.jobs.find(j => ['queued', 'running'].includes(j.status));
  const ctx: Ctx = { state, busy, active, act, go, refresh,
    start: (operation, target) => act(async () => { await call(['start', operation, ...(target ? [target] : [])]); setTab('overview'); }),
    stop: id => { void act(() => call(['cancel', id]), 'Job stopped'); } };
  const pending = disconnected ? newBundles(state.incoming).length : 0;
  const tabs: [string, React.ReactNode][] = disconnected ?
    [['overview', 'Overview'], ['content', <>Content{pending > 0 && <span className="dc-tab-badge" aria-label={`${pending} new`}>{pending}</span>}</>],
      ['clients', 'Clients'], ['jobs', 'Activity'], ['settings', 'Settings']] :
    [['overview', 'Overview'], ['profile', 'Repositories'], ['jobs', 'Activity'], ['settings', 'Settings']];
  return <main className="dc-app">
    {importing && <ImportFlow ctx={{ ...ctx, start: async (operation, target) => {
      await call(['start', operation, ...(target ? [target] : [])]); await refresh(); setTab('overview'); setNotice('Import started');
    } }} bundle={importing} onDone={() => { setImporting(undefined); void refresh(); }} />}
    <header className="dc-header"><div>
      <Title headingLevel="h1" size="2xl">Disconnected Content</Title>
      <p>{disconnected ? 'Disconnected host' : 'Connected host'} · {state.os.name} {state.os.arch} ·{' '}
        {disconnected ? 'Import bundles and serve repositories to clients' : 'Mirror repositories and build transfer bundles'}</p></div>
      <div className="dc-labels"><Label color={disconnected ? 'purple' : 'blue'}>{disconnected ? 'Disconnected host' : 'Connected host'}</Label>
        <PrototypeNotes version={state.version} /></div>
    </header>
    <Tabs activeKey={tab} onSelect={(_, key) => go(String(key))} aria-label="Disconnected content sections">
      {tabs.map(([key, title]) => <Tab key={key} eventKey={key} title={<TabTitleText>{title}</TabTitleText>} />)}
    </Tabs>
    {error && <Alert variant="danger" title="That didn't work" isInline actionClose={<AlertActionCloseButton onClose={() => setError('')} />}><p>{error}</p></Alert>}
    {notice && <Alert variant="success" title={notice} isInline actionClose={<AlertActionCloseButton onClose={() => setNotice('')} />} />}
    <section className="dc-content">
      {tab === 'overview' && !disconnected && state.connected && <ConnectedOverview ctx={ctx} />}
      {tab === 'profile' && !disconnected && <Repositories ctx={ctx} />}
      {tab === 'settings' && !disconnected && <ConnectedSettings ctx={ctx} />}
      {tab === 'overview' && disconnected && state.disconnected && <DisconnectedOverview ctx={ctx} onImport={setImporting} />}
      {tab === 'content' && disconnected && <Content ctx={ctx} onImport={setImporting} />}
      {tab === 'clients' && disconnected && <Clients ctx={ctx} />}
      {tab === 'settings' && disconnected && <Settings ctx={ctx} />}
      {tab === 'jobs' && <Activity state={state} />}
    </section>
  </main>;
}
createRoot(document.getElementById('root')!).render(<App />);

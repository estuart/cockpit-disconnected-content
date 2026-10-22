import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, CardBody, CardTitle, Label, Spinner } from '@patternfly/react-core';
import { Automation, Publication, SigningKey, call, downloadFile, downloadZip, errorText, groupFingerprint, snapshotLabel } from './api';
import { Ctx } from './connected';
import { Ago } from './ui';

function clientKeys(publication: Publication) {
  return [...new Map(Object.values(publication.repositories).flatMap(repo => repo.signing_keys ?? [])
    .map(key => [key.fingerprint, key])).values()];
}
function keyPath(key: SigningKey) { return `/etc/pki/rpm-gpg/DISCONNECTED-CONTENT-${key.fingerprint}.asc`; }
function keyUrl(publication: Publication, key: SigningKey) {
  return `${publication.base_url}/disconnected-content/snapshots/${publication.snapshot_id}/keys/${key.fingerprint}.asc`;
}

/** Manual setup keeps the existing checks: SHA-256 and full fingerprint before RPM enrollment. */
function manualCommands(publication: Publication) {
  const keys = clientKeys(publication).map(key => [
    `curl --fail --output '${key.fingerprint}.asc' '${keyUrl(publication, key)}'`,
    `echo '${key.sha256}  ${key.fingerprint}.asc' | sha256sum --check`,
    `gpg2 --show-keys --with-fingerprint '${key.fingerprint}.asc'   # must show ${groupFingerprint(key.fingerprint)}`,
    `sudo install -m 0644 '${key.fingerprint}.asc' '${keyPath(key)}' && sudo rpmkeys --import '${keyPath(key)}'`].join('\n'));
  return [...keys,
    `curl --fail --output disconnected-content.repo '${publication.config_url}'\nless disconnected-content.repo   # review it\nsudo install -m 0644 disconnected-content.repo /etc/yum.repos.d/disconnected-content.repo`,
    `sudo dnf --disablerepo='*' --enablerepo='dc-*' upgrade --security --assumeno`].join('\n\n');
}

export function Clients({ ctx }: { ctx: Ctx }) {
  const { state } = ctx;
  const publication = state.publication?.current;
  const [automation, setAutomation] = useState<Automation>();
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!publication) return;
    let mounted = true;
    setAutomation(undefined); setError('');
    void call<Automation>(['client-automation', publication.job_id]).then(result => { if (mounted) setAutomation(result); })
      .catch(e => { if (mounted) setError(errorText(e)); });
    return () => { mounted = false; };
  }, [publication?.job_id, revision]);
  if (!publication) return <Card><CardBody>Publish a snapshot to get client setup files.{' '}
    <Button variant="link" isInline onClick={() => ctx.go('overview')}>Go to Overview</Button></CardBody></Card>;
  const snapshot = state.snapshots.find(s => s.id === publication.snapshot_id);
  const keys = clientKeys(publication);
  const commands = manualCommands(publication);
  const sid = publication.snapshot_id.slice(0, 8);
  return <>
    <div><h2 className="dc-page-title">Point clients at the {snapshot ? snapshotLabel(snapshot, state.snapshots) : sid} snapshot</h2>
      <p className="dc-muted">Published <Ago value={publication.published_at} /> at {publication.base_url} · RHEL {publication.os.major} {publication.os.arch} ·
        Clients stay on this snapshot until you give them a newer one.</p></div>
    {!state.disconnected?.publication_serving && <Alert variant="warning" isInline title="The web server isn't serving this snapshot right now">
      Check the Overview tab before using these files.</Alert>}
    {state.disconnected?.server_url_changed && <Alert variant="info" isInline title="These files use the previous address">
      Publish again to get files with the new client-facing address.</Alert>}
    <div className="dc-two">
      <Card className="dc-option dc-option-recommended"><CardBody>
        <h2 className="dc-hero-title">Ansible <Label color="blue" isCompact>Recommended</Label></h2>
        <p className="dc-muted">For many clients. Installs the signing keys and repository file, then applies security updates one host at a time.</p>
        {error ? <Alert variant="danger" isInline title="Couldn't generate playbooks">{error}
          <Button variant="link" onClick={() => setRevision(value => value + 1)}>Try again</Button></Alert> :
          !automation ? <Spinner size="md" aria-label="Generating playbooks" /> : <>
            {!automation.has_updateinfo && <Alert variant="warning" isInline isPlain title="Security updates need advisory metadata">
              Repository setup works, but security-updates.yml stops until every repository includes updateinfo. Mirror it and publish again.</Alert>}
            <Button onClick={() => downloadZip(automation.files, `disconnected-content-${sid}-ansible.zip`)}>Download Ansible kit (.zip)</Button>
            <pre className="dc-cmd" aria-label="Ansible run commands">{`# Edit inventory.ini, then try one host first
ansible-playbook -i inventory.ini configure-repos.yml --limit client.example.org --check
ansible-playbook -i inventory.ini configure-repos.yml --limit client.example.org
ansible-playbook -i inventory.ini security-updates.yml --limit client.example.org --check`}</pre>
            <p className="dc-muted">Needs ansible-core 2.16 or newer on your control node. Playbooks stop on the first failure and never reboot hosts.</p>
            <details className="dc-details"><summary>Download files individually or preview them</summary>
              <div className="dc-actions">{Object.keys(automation.files).map(name =>
                <Button key={name} variant="secondary" size="sm" onClick={() => downloadFile(automation.files[name], name)}>{name}</Button>)}</div>
              {Object.entries(automation.files).map(([name, content]) => <div key={name}><h4>{name}</h4><pre className="dc-config" aria-label={name}>{content}</pre></div>)}
            </details></>}
      </CardBody></Card>
      <Card className="dc-option"><CardBody>
        <h2 className="dc-hero-title">Manual</h2>
        <p className="dc-muted">For one or two clients. Run these on each client.</p>
        <Button variant="secondary" onClick={() => { void ctx.act(() => navigator.clipboard.writeText(commands), 'Setup commands copied'); }}>Copy setup commands</Button>
        <pre className="dc-cmd" aria-label="Client setup commands">{commands}</pre>
        <p className="dc-muted">Remove <code>--assumeno</code> to apply the updates.</p>
        <details className="dc-details"><summary>Download the .repo file and signing keys</summary>
          <div className="dc-actions"><Button variant="secondary" size="sm" onClick={() => downloadFile(publication.client_config, 'disconnected-content.repo')}>disconnected-content.repo</Button>
            {keys.map(key => <Button key={key.fingerprint} variant="secondary" size="sm" onClick={() => downloadFile(key.public_key, `${key.fingerprint}.asc`)}>{key.fingerprint.slice(-16)}.asc</Button>)}</div>
          <pre className="dc-config" aria-label="Client repository configuration">{publication.client_config}</pre>
        </details>
      </CardBody></Card>
    </div>
    <Card><CardTitle>Signing keys clients will trust</CardTitle><CardBody>
      <table className="dc-table" aria-label="Published signing keys"><tbody>
        {Object.entries(publication.repositories).filter(([, repo]) => repo.signing_keys?.length).flatMap(([rid, repo]) => repo.signing_keys!.map(key =>
          <tr key={rid + key.fingerprint}><td><strong>{rid}</strong></td><td>{key.user_ids.join(' · ')}</td><td><code className="dc-wrap">{groupFingerprint(key.fingerprint)}</code></td></tr>))}
        {Object.values(publication.repositories).some(repo => !repo.signing_keys?.length) &&
          <tr><td><strong>Other repositories</strong></td><td colSpan={2} className="dc-muted">Use the Red Hat keys already installed on RHEL clients</td></tr>}
      </tbody></table>
      <p className="dc-muted">Enrolling a key changes the client's system-wide RPM trust. This host doesn't confirm which clients were configured or updated.</p>
    </CardBody></Card>
  </>;
}

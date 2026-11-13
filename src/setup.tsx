// First-run setup: choose the host's role, plus the disconnected prototype import policy.
// The helper validates and writes the root-owned configuration; this view only collects the choice.
import React, { useState } from 'react';
import { Alert, Button, Checkbox, Modal, ModalBody, ModalFooter, ModalHeader } from '@patternfly/react-core';
import { State } from './api';
import { Hero } from './ui';

export type Role = 'connected' | 'disconnected';
export type SetupRequest = { role: Role; allow_unsigned_imports?: boolean };

const choices: { role: Role; title: string; lead: string; points: string[] }[] = [
  { role: 'connected', title: 'Connected host', lead: 'This host can reach Red Hat’s content delivery network.',
    points: ['Mirrors the repositories you choose', 'Checks every RPM signature', 'Builds transfer bundles to carry across the air gap'] },
  { role: 'disconnected', title: 'Disconnected host', lead: 'This host is inside the air gap and never reaches the internet.',
    points: ['Imports and re-checks transfer bundles', 'Serves repositories to clients over HTTP', 'Gives you an Ansible kit for your clients'] },
];

const unsignedHelp = 'Transfer bundles from this prototype aren’t signed. Their checksums show that a file wasn’t damaged, ' +
  'not who made it. RPM signatures are always checked.';

export function FirstRun({ state, busy, onSetup }: { state: State; busy: boolean; onSetup: (request: SetupRequest) => void }) {
  const [role, setRole] = useState<Role | undefined>(state.setup_hint ?? undefined);
  const [unsigned, setUnsigned] = useState(false);
  const publisher = state.capabilities.publisher_installed;
  return <Hero title="Set up this host" lead="Choose what this host does. You only do this once.">
    {state.setup_hint && <Alert variant="info" isInline title={`This host already has ${state.setup_hint} content`}>
      It looks like it was used before the setup step existed, so <strong>{state.setup_hint === 'connected' ? 'Connected host' : 'Disconnected host'}</strong> is
      selected. Existing content is kept.</Alert>}
    <div className="dc-two dc-choices" role="radiogroup" aria-label="Host role">
      {choices.map(choice => <label key={choice.role} className={`dc-choice${role === choice.role ? ' dc-choice-selected' : ''}`}>
        <input type="radio" name="dc-role" value={choice.role} checked={role === choice.role} disabled={busy}
          onChange={() => setRole(choice.role)} />
        <span className="dc-choice-body"><strong className="dc-choice-title">{choice.title}</strong>
          <span className="dc-muted">{choice.lead}</span>
          <ul>{choice.points.map(point => <li key={point}>{point}</li>)}</ul></span>
      </label>)}
    </div>
    {role === 'disconnected' && <div className="dc-choice-extra">
      <p className={publisher ? 'dc-ok' : ''}>{publisher ? '✓ The web publisher is installed, so this host can serve repositories to clients.' :
        <>The web publisher isn’t installed. To serve repositories to clients, install <code>cockpit-disconnected-content-publisher</code>. You can do that later.</>}</p>
      <Checkbox id="dc-setup-unsigned" isChecked={unsigned} isDisabled={busy} onChange={(_, checked) => setUnsigned(checked)}
        label="Allow imports of unsigned transfer bundles (prototype)"
        description={<>{unsignedHelp} Leave this off to keep imports turned off. You can change it later in Settings.</>} />
    </div>}
    <p className="dc-muted dc-setup-note">Content is stored in <code>{state.storage.path}</code>. To use a different location, change{' '}
      <code>state_dir</code> in <code>/etc/disconnected-content/config.json</code> before you set up.</p>
    <div className="dc-actions">
      <Button isDisabled={!role || busy} isLoading={busy || undefined}
        onClick={() => role && onSetup(role === 'disconnected' ? { role, allow_unsigned_imports: unsigned } : { role })}>
        {role ? `Set up as a ${role} host` : 'Choose a role to continue'}</Button>
    </div>
  </Hero>;
}

/** Confirmation before a disconnected host starts accepting unsigned transfer bundles. */
export function UnsignedImportsDialog({ busy, onConfirm, onClose }: { busy: boolean; onConfirm: () => void; onClose: () => void }) {
  return <Modal isOpen variant="small" onClose={onClose} aria-labelledby="dc-unsigned-title">
    <ModalHeader labelId="dc-unsigned-title" title="Allow unsigned transfer bundles?" />
    <ModalBody><p>{unsignedHelp}</p>
      <p>Only allow this if your transfer process already controls who can put files on the transfer media.</p></ModalBody>
    <ModalFooter>
      <Button isDisabled={busy} onClick={onConfirm}>Allow unsigned bundles</Button>
      <Button variant="link" onClick={onClose}>Cancel</Button>
    </ModalFooter>
  </Modal>;
}

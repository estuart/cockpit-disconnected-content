import React, { useEffect, useState } from 'react';
import { Alert, Button, Modal, ModalBody, ModalFooter, ModalHeader, Spinner, TextInput } from '@patternfly/react-core';
import { DirectoryListing, call, errorText } from './api';

export function DirectoryPicker({ initialPath, isDisabled, onSelect, onClose, title = 'Choose transfer folder', note }: {
  title?: string; note?: string;
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
    <ModalHeader title={title} labelId="dc-picker-title" descriptorId="dc-picker-description"
      description="Browse folders on this host. Mounted drives appear under Mounted filesystems." />
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
        <p className="dc-muted">{note ?? 'Choosing a folder saves it. This page then checks it for bundles automatically.'}</p></div>
    </ModalBody>
    <ModalFooter><Button isDisabled={loading || !listing || isDisabled} onClick={() => { if (listing) onSelect(listing.path); }}>Use this folder</Button>
      <Button variant="link" onClick={onClose}>Cancel</Button></ModalFooter>
  </Modal>;
}

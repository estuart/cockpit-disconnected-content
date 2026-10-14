// Run against an already authenticated, isolated Cockpit test session.
// Deployment addresses and screenshots belong in ignored local state.
import { chromium, expect } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';

const url = process.env.COCKPIT_TEST_URL;
if (!url) throw new Error('Set COCKPIT_TEST_URL to the isolated Cockpit test session');
await mkdir('.local', { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
let app;
let restoreSource;
try {
  const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1500, height: 1100 } });
  let page = await context.newPage();
  const errors = [];
  const backgroundErrors = new Set();
  const monitor = async target => {
    const client = await context.newCDPSession(target);
    await client.send('Runtime.enable');
    client.on('Runtime.exceptionThrown', event => {
      const details = event.exceptionDetails;
      const frames = details.stackTrace?.callFrames ?? [];
      const sources = [details.url, ...frames.map(frame => frame.url)].filter(Boolean);
      if (sources.some(source => source.includes('/disconnected-content/'))) {
        errors.push(`${details.text}: ${details.exception?.description ?? ''}`);
      } else {
        for (const source of sources) backgroundErrors.add(new URL(source).pathname);
      }
    });
  };
  await monitor(page);
  await page.goto(url);
  await expect(page.getByRole('link', { name: 'Disconnected Content', exact: true })).toBeVisible();
  // The extension requests administrative access through cockpit.spawn itself.
  await page.getByRole('link', { name: 'Disconnected Content', exact: true }).click();
  // Cockpit may have several loaded frames; resolve the extension by its URL.
  await expect.poll(() => page.frames().some(f => f.url().includes('/disconnected-content/'))).toBe(true);
  app = page.frames().find(f => f.url().includes('/disconnected-content/'));
  await expect(app.getByRole('heading', { name: 'Disconnected Content', exact: true })).toBeVisible();
  await expect(app.getByText('Available storage', { exact: true })).toBeVisible({ timeout: 15000 });
  const initialState = await app.evaluate(async () => JSON.parse(await window.cockpit.spawn(
    ['/usr/libexec/disconnected-content', 'status'], { superuser: 'require', err: 'message' })));
  const disconnected = initialState.role === 'disconnected';
  await app.getByRole('tab', { name: disconnected ? 'Incoming bundles' : 'Repositories', exact: true }).click();
  await expect(app.getByText(disconnected ? 'Transfer inbox' : 'Choose content to mirror', { exact: true })).toBeVisible();
  if (process.argv.includes('--check-picker')) {
    const mount = process.env.COCKPIT_TEST_MOUNT;
    const source = process.env.COCKPIT_TEST_SOURCE;
    if (!disconnected || !mount || !source) throw new Error('Picker check requires the disconnected role, COCKPIT_TEST_MOUNT and COCKPIT_TEST_SOURCE');
    restoreSource = initialState.import_source.path;
    const field = app.getByRole('textbox', { name: 'Bundle source directory', exact: true });
    const picker = app.getByRole('dialog', { name: 'Choose transfer folder', exact: true });
    const browse = app.getByRole('button', { name: 'Browse folders', exact: true });
    const status = () => app.evaluate(async () => JSON.parse(await window.cockpit.spawn(
      ['/usr/libexec/disconnected-content', 'status'], { superuser: 'require', err: 'message' })));
    await expect(field).toHaveValue(restoreSource);
    await browse.click();
    await expect(picker).toBeVisible();
    await picker.getByRole('button', { name: mount, exact: true }).click();
    await expect(picker.locator('.dc-picker-selection code')).toHaveText(mount);
    await picker.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(picker).toBeHidden();
    await expect(field).toHaveValue(restoreSource);
    expect((await status()).import_source.path).toBe(restoreSource);
    await browse.click();
    await picker.getByRole('button', { name: mount, exact: true }).click();
    const folderName = source.slice(source.lastIndexOf('/') + 1);
    await picker.getByRole('textbox', { name: 'Filter folders', exact: true }).fill('no-folder-matches-this');
    await expect(picker.getByText('No folders match this filter.', { exact: true })).toBeVisible();
    await picker.getByRole('textbox', { name: 'Filter folders', exact: true }).fill(folderName);
    await picker.getByRole('button', { name: `Open folder ${folderName}`, exact: true }).click();
    await expect(picker.locator('.dc-picker-selection code')).toHaveText(source);
    await picker.getByRole('button', { name: 'Up one folder', exact: true }).click();
    await expect(picker.locator('.dc-picker-selection code')).toHaveText(mount);
    await picker.getByRole('button', { name: `Open folder ${folderName}`, exact: true }).click();
    await expect(picker.locator('.dc-picker-selection code')).toHaveText(source);
    await expect(picker.getByRole('button', { name: 'Use this folder', exact: true })).toBeEnabled();
    await page.screenshot({ path: '.local/cockpit-directory-picker.png', fullPage: true });
    await picker.getByRole('button', { name: 'Use this folder', exact: true }).click();
    await expect(field).toHaveValue(source);
    expect((await status()).import_source.path).toBe(restoreSource);
    await app.getByRole('button', { name: 'Save & scan', exact: true }).click();
    await expect.poll(async () => (await status()).import_source.path).toBe(source);
    // status() starts a fresh helper, so this checks the persisted configuration.
    await expect(app.getByRole('textbox', { name: 'Bundle source directory', exact: true })).toHaveValue(source);
    await expect(app.locator('.dc-repo time').first()).toBeVisible();
    console.log('Mounted-filesystem picker navigation, filtering, cancel, parent navigation, selection without saving, and persistence after Save & scan passed');
  }
  if (process.argv.includes('--check-source')) {
    const source = process.env.COCKPIT_TEST_SOURCE;
    if (!disconnected || !source) throw new Error('Source workflow requires the disconnected role and COCKPIT_TEST_SOURCE');
    restoreSource = initialState.import_source.path;
    const status = () => app.evaluate(async () => JSON.parse(await window.cockpit.spawn(
      ['/usr/libexec/disconnected-content', 'status'], { superuser: 'require', err: 'message' })));
    await app.getByRole('textbox', { name: 'Bundle source directory', exact: true }).fill('relative/path');
    await app.getByRole('button', { name: 'Save & scan', exact: true }).click();
    await expect(app.getByText('Enter an absolute directory path without parent traversal', { exact: true })).toBeVisible();
    expect((await status()).import_source.path).toBe(restoreSource);
    await app.getByRole('textbox', { name: 'Bundle source directory', exact: true }).fill(source);
    await app.getByRole('button', { name: 'Save & scan', exact: true }).click();
    await expect.poll(async () => (await status()).import_source.path).toBe(source);
    await expect(app.getByText('Bundle source saved and scanned', { exact: false })).toBeVisible();
    await page.reload();
    await page.getByRole('link', { name: 'Disconnected Content', exact: true }).click();
    await expect.poll(() => page.frames().some(f => f.url().includes('/disconnected-content/'))).toBe(true);
    app = page.frames().find(f => f.url().includes('/disconnected-content/'));
    await app.getByRole('tab', { name: 'Incoming bundles', exact: true }).click();
    await expect(app.getByRole('textbox', { name: 'Bundle source directory', exact: true })).toHaveValue(source);
    expect((await status()).import_source.available).toBe(true);
    await expect(app.locator('.dc-repo time').first()).toBeVisible();
    await page.screenshot({ path: '.local/cockpit-source.png', fullPage: true });
    console.log('GUI rejected an invalid source, saved and scanned the selected directory, persisted it across reload, and displayed bundle timestamps');
  }
  await app.getByRole('tab', { name: 'Overview', exact: true }).click();
  if (process.argv.includes('--check-connected')) {
    if (disconnected || !initialState.connected) throw new Error('Connected dashboard backend required');
    const status = () => app.evaluate(async () => JSON.parse(await window.cockpit.spawn(
      ['/usr/libexec/disconnected-content', 'status'], { superuser: 'require', err: 'message' })));
    await expect(app.locator('.dc-freshness').getByText('Last successful sync', { exact: true })).toBeVisible();
    await expect(app.getByRole('list', { name: 'Saved selection workflow' })).toBeVisible();
    const originalExport = initialState.connected.latest_export;
    if (initialState.connected.phase === 'verify') {
      const previous = initialState.jobs.map(job => job.id);
      await app.getByRole('button', { name: 'Verify existing bundle', exact: true }).click();
      await expect.poll(async () => (await status()).jobs.find(job => job.operation === 'verify-export' && !previous.includes(job.id))?.status,
        { timeout: 120000, intervals: [1000, 2000] }).toBe('succeeded');
      expect((await status()).connected.latest_export.exported_at).toBe(originalExport.exported_at);
      await expect(app.getByText('Verified bundle ready for transfer', { exact: true })).toBeVisible({ timeout: 15000 });
    }
    if (process.argv.includes('--run-dashboard-cycle')) {
      const before = await status();
      if (before.profile.repositories.length !== 1 || before.profile.max_gib > 5)
        throw new Error('Live dashboard cycle requires a single repository and budget <= 5 GiB');
      const previous = before.jobs.map(job => job.id);
      await app.getByRole('button', { name: 'Check saved selection size', exact: true }).click();
      let sized;
      await expect.poll(async () => {
        sized = (await status()).jobs.find(job => job.operation === 'preflight' && !previous.includes(job.id));
        return sized?.status;
      }, { timeout: 120000, intervals: [1000, 2000] }).toBe('succeeded');
      if (!sized.plan.within_budget || !sized.plan.enough_space || sized.plan.package_bytes > 1024 ** 3)
        throw new Error('Live sync test is limited to content under 1 GiB that fits the saved budget/storage');
      await expect(app.getByRole('button', { name: 'Sync saved selection', exact: true })).toBeVisible({ timeout: 15000 });
      await app.getByRole('button', { name: 'Sync saved selection', exact: true }).click();
      let synced;
      await expect.poll(async () => {
        synced = (await status()).jobs.find(job => job.operation === 'sync' && !previous.includes(job.id));
        return synced?.status;
      }, { timeout: 240000, intervals: [1000, 2000, 4000] }).toBe('succeeded');
      await expect(app.getByText('Build a transfer bundle', { exact: true })).toBeVisible({ timeout: 15000 });
      const afterSync = await status();
      expect(afterSync.connected.selection_snapshot.id).toBe(synced.snapshot_id);
      expect(afterSync.connected.selection_snapshot.export).toBeNull();
      expect(afterSync.connected.latest_export.snapshot_id).not.toBe(synced.snapshot_id);
      await page.screenshot({ path: '.local/cockpit-connected-awaiting-export.png', fullPage: true });
      await app.getByRole('button', { name: 'Build & verify transfer bundle', exact: true }).click();
      let exported;
      await expect.poll(async () => {
        exported = (await status()).jobs.find(job => job.operation === 'export' && job.snapshot_id === synced.snapshot_id);
        return exported?.status;
      }, { timeout: 120000, intervals: [1000, 2000] }).toBe('succeeded');
      await expect(app.getByText('Verified bundle ready for transfer', { exact: true })).toBeVisible({ timeout: 15000 });
      const afterExport = await status();
      expect(afterExport.connected.selection_snapshot.export.state).toBe('available');
      expect(afterExport.connected.latest_export.snapshot_id).toBe(synced.snapshot_id);
      expect(afterExport.profile).toEqual(initialState.profile);
      await writeFile('.local/dashboard-cycle.json', JSON.stringify({ sync: synced, export: exported, connected: afterExport.connected }, null, 2));
    }
    await page.screenshot({ path: '.local/cockpit-connected-dashboard.png', fullPage: true });
    await app.getByRole('button', { name: 'Show transfer files', exact: true }).click();
    await expect(app.getByLabel('Transfer bundle details', { exact: true }).first()).toContainText('.tar.sha256');
    await page.screenshot({ path: '.local/cockpit-connected-exports.png', fullPage: true });
    await app.getByRole('tab', { name: 'Overview', exact: true }).click();
    console.log('Connected dashboard, durable historical export, preserved export age, saved selection, workflow transitions, and transfer-file details passed');
  }
  if (process.argv.includes('--check-automation')) {
    if (!disconnected || !initialState.publication?.current) throw new Error('Publish a snapshot before checking automation downloads');
    const publication = initialState.publication.current;
    await app.getByRole('tab', { name: 'Publish & clients', exact: true }).click();
    await expect(app.getByRole('heading', { name: 'Ansible client automation', exact: true })).toBeVisible();
    const expected = await app.evaluate(async jobId => JSON.parse(await window.cockpit.spawn(
      ['/usr/libexec/disconnected-content', 'client-automation', jobId], { superuser: 'require', err: 'message' })), publication.job_id);
    for (const [filename, label] of [
      ['configure-repos.yml', 'Download configure repos playbook'],
      ['security-updates.yml', 'Download security updates playbook'],
      ['inventory.ini', 'Download example inventory'],
      ['README-client-automation.txt', 'Download run instructions'],
    ]) {
      const event = page.waitForEvent('download');
      await app.getByRole('button', { name: label, exact: true }).click();
      const download = await event;
      expect(download.suggestedFilename()).toBe(filename);
      const path = `.local/browser-${filename}`;
      await download.saveAs(path);
      expect(await readFile(path, 'utf8')).toBe(expected.files[filename]);
    }
    await expect(app.getByLabel('Ansible run commands', { exact: true })).toContainText('security-updates.yml --limit client.example.org --check --diff');
    await app.getByText('Preview playbooks and instructions', { exact: true }).click();
    await expect(app.getByLabel('configure-repos.yml', { exact: true })).toContainText(`/snapshots/${publication.snapshot_id}/`);
    await expect(app.getByLabel('security-updates.yml', { exact: true })).toContainText('"security": true');
    await app.getByText('Preview playbooks and instructions', { exact: true }).click();
    await app.getByRole('button', { name: 'Download configure repos playbook', exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: '.local/cockpit-ansible-downloads.png', fullPage: true });
    console.log('All four automation downloads matched the durable publication, filenames and run commands passed, and playbook previews show the pinned snapshot and security filter');
    await app.getByRole('tab', { name: 'Overview', exact: true }).click();
  }
  if (process.argv.includes('--run-publish')) {
    const origin = process.env.COCKPIT_TEST_PUBLIC_URL;
    if (!disconnected || !origin) throw new Error('Publication check requires the disconnected role and COCKPIT_TEST_PUBLIC_URL');
    const status = () => app.evaluate(async () => JSON.parse(await window.cockpit.spawn(
      ['/usr/libexec/disconnected-content', 'status'], { superuser: 'require', err: 'message' })));
    const before = await status();
    if (!before.snapshots.length) throw new Error('Import a validated snapshot before the publication test');
    const previous = before.jobs.map(job => job.id);
    const snapshot = before.snapshots[0];
    await app.getByRole('tab', { name: 'Publish & clients', exact: true }).click();
    await app.getByRole('textbox', { name: 'Public server URL', exact: true }).fill(origin);
    await app.getByRole('button', { name: 'Save server URL', exact: true }).click();
    await expect.poll(async () => (await status()).publication.base_url).toBe(origin);
    await app.getByLabel('Snapshot to publish', { exact: true }).selectOption(snapshot.id);
    await app.getByRole('button', { name: 'Publish snapshot', exact: true }).click();
    await expect.poll(async () => (await status()).jobs.find(job => job.operation === 'publish' && !previous.includes(job.id))?.status,
      { timeout: 120000, intervals: [1000, 2000] }).toBe('succeeded');
    await app.getByRole('tab', { name: 'Publish & clients', exact: true }).click();
    const config = app.getByLabel('Client repository configuration', { exact: true });
    await expect(config).toContainText(`baseurl=${origin}/disconnected-content/snapshots/${snapshot.id}/`);
    await expect(config).toContainText('gpgcheck=1');
    await expect(app.getByText('Apache running', { exact: true })).toBeVisible();
    await expect(app.getByLabel('Client setup commands', { exact: true })).toContainText('upgrade --security --assumeno');
    const downloadEvent = page.waitForEvent('download');
    await app.getByRole('button', { name: 'Download .repo file', exact: true }).click();
    const download = await downloadEvent;
    await download.saveAs('.local/browser-client.repo');
    expect(download.suggestedFilename()).toBe('disconnected-content.repo');
    const configUrl = `${origin}/disconnected-content/client.repo`;
    const response = await fetch(configUrl);
    expect(response.status).toBe(200);
    expect(await response.text()).toBe(await config.textContent());
    expect((await fetch(configUrl, { method: 'HEAD' })).status).toBe(200);
    for (const method of ['POST', 'PUT', 'DELETE']) {
      expect([403, 405]).toContain((await fetch(configUrl, { method })).status);
    }
    await page.screenshot({ path: '.local/cockpit-publication.png', fullPage: true });
    console.log('Browser-started publication, pinned client configuration, download, setup commands, and active Apache status passed');
    await app.getByRole('tab', { name: 'Overview', exact: true }).click();
  }
  if (process.argv.includes('--run-import')) {
    if (!disconnected) throw new Error('Import browser check requires the disconnected role');
    const bundle = process.env.COCKPIT_TEST_BUNDLE;
    if (!bundle) throw new Error('Set COCKPIT_TEST_BUNDLE to the staged test archive name');
    await app.getByRole('tab', { name: 'Incoming bundles', exact: true }).click();
    const row = app.locator('.dc-repo').filter({ hasText: bundle });
    const previousJobs = await app.evaluate(async () => JSON.parse(await window.cockpit.spawn(
      ['/usr/libexec/disconnected-content', 'status'], { superuser: 'require', err: 'message' })).jobs.map(j => j.id));
    await row.getByRole('button', { name: 'Import bundle', exact: true }).click();
    let imported;
    await expect.poll(async () => {
      const state = await app.evaluate(async () => JSON.parse(await window.cockpit.spawn(
        ['/usr/libexec/disconnected-content', 'status'], { superuser: 'require', err: 'message' })));
      imported = state.jobs.find(j => j.operation === 'import' && j.bundle_name === bundle && !previousJobs.includes(j.id));
      return imported?.status;
    }, { timeout: 120000, intervals: [1000, 2000] }).toBe('succeeded');
    await app.getByRole('tab', { name: /^Snapshots/ }).click();
    await expect(app.getByText('Local repository URLs', { exact: true }).first()).toBeVisible({ timeout: 15000 });
    await page.screenshot({ path: '.local/cockpit-import.png', fullPage: true });
    await app.getByRole('tab', { name: 'Jobs & evidence', exact: true }).click();
    await expect(app.getByText(/Evidence verified|Archive matched existing snapshot/).first()).toBeVisible({ timeout: 15000 });
    const latestJob = app.locator('.pf-v6-c-card').filter({ hasText: `Job ${imported.id}` });
    await expect(latestJob).toBeVisible({ timeout: 15000 });
    for (const label of ['Queued', 'Started', 'Last updated', 'Finished', 'Duration']) {
      await expect(latestJob.locator('dt').filter({ hasText: new RegExp(`^${label}$`) })).toBeVisible();
    }
    await page.screenshot({ path: '.local/cockpit-job-timestamps.png', fullPage: true });
    console.log('Browser-started disconnected import completed; snapshot visibility and timestamped job evidence passed');
    await app.getByRole('tab', { name: 'Overview', exact: true }).click();
  }
  if (process.argv.includes('--run-cycle')) {
    const status = () => app.evaluate(async () => JSON.parse(await window.cockpit.spawn(
      ['/usr/libexec/disconnected-content', 'status'], { superuser: 'require', err: 'message' })));
    const before = await status();
    if (before.profile.max_gib > 1 || before.profile.repositories.length !== 1)
      throw new Error('Browser cycle requires one preselected repository with a budget of at most 1 GiB');
    const previous = before.jobs.map(j => j.id);
    await app.getByRole('button', { name: 'Sync saved selection', exact: true }).click();
    let started;
    await expect.poll(async () => {
      started = (await status()).jobs.find(j => j.operation === 'sync' && !previous.includes(j.id));
      return Boolean(started);
    }, { timeout: 15000 }).toBe(true);
    await page.close(); // The job must be independent of the browser page.
    page = await context.newPage();
    await monitor(page);
    await page.goto(url);
    await page.getByRole('link', { name: 'Disconnected Content', exact: true }).click();
    await expect.poll(() => page.frames().some(f => f.url().includes('/disconnected-content/'))).toBe(true);
    app = page.frames().find(f => f.url().includes('/disconnected-content/'));
    await expect(app.getByText('Available storage', { exact: true })).toBeVisible({ timeout: 15000 });
    await expect.poll(async () => (await status()).jobs.find(j => j.id === started.id)?.status,
      { timeout: 120000, intervals: [1000, 2000] }).toBe('succeeded');
    await app.getByRole('tab', { name: /^Snapshots/ }).click();
    const snapshotCard = app.locator('.pf-v6-c-card').filter({ hasText: `Snapshot ${started.id.slice(0, 8)}` });
    await expect(snapshotCard).toBeVisible({ timeout: 15000 });
    await snapshotCard.getByRole('button', { name: 'Build transfer bundle', exact: true }).click();
    let exported;
    await expect.poll(async () => {
      exported = (await status()).jobs.find(j => j.operation === 'export' && j.snapshot_id === started.id);
      return exported?.status;
    }, { timeout: 30000, intervals: [1000] }).toBe('succeeded');
    await writeFile('.local/browser-cycle.json', JSON.stringify({ sync: started.id, export: exported }, null, 2));
    console.log('Browser-started CDN sync survived page closure; snapshot export and archive verification passed');
    await app.getByRole('tab', { name: disconnected ? 'Jobs & evidence' : 'Activity & evidence', exact: true }).click();
    await page.screenshot({ path: '.local/cockpit-jobs.png', fullPage: true });
    await app.getByRole('tab', { name: 'Overview', exact: true }).click();
  }
  await page.screenshot({ path: '.local/cockpit-overview.png', fullPage: true });
  if (errors.length) throw new Error(errors.join('\n'));
  if (backgroundErrors.size) console.log('Other Cockpit pages reported background exceptions:', [...backgroundErrors].join(', '));
  console.log('Cockpit navigation, privileged backend status, repository screen, and overview rendering passed');
} finally {
  if (restoreSource && app) {
    try {
      await expect.poll(async () => app.evaluate(async () => JSON.parse(await window.cockpit.spawn(
        ['/usr/libexec/disconnected-content', 'status'], { superuser: 'require', err: 'message' }))
        .jobs.some(job => ['queued', 'running'].includes(job.status))), { timeout: 120000 }).toBe(false);
      await app.evaluate(async path => {
        const process = window.cockpit.spawn(['/usr/libexec/disconnected-content', 'save-import-settings'],
          { superuser: 'require', err: 'message' });
        process.input(JSON.stringify({ incoming_dir: path }));
        await process;
      }, restoreSource);
    } catch (error) { console.error('Source restoration requires attention:', error); process.exitCode = 1; }
  }
  await browser.close();
}

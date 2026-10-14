// Run against an already authenticated, isolated Cockpit test session.
// Deployment addresses and screenshots belong in ignored local state.
import { chromium, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';

const url = process.env.COCKPIT_TEST_URL;
if (!url) throw new Error('Set COCKPIT_TEST_URL to the isolated Cockpit test session');
await mkdir('.local', { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
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
  const limited = page.getByRole('button', { name: 'Limited access', exact: true });
  if (await limited.isVisible()) {
    await limited.click();
    await expect(limited).toBeHidden({ timeout: 15000 });
  }
  await page.getByRole('link', { name: 'Disconnected Content', exact: true }).click();
  // Cockpit may have several loaded frames; resolve the extension by its URL.
  await expect.poll(() => page.frames().some(f => f.url().includes('/disconnected-content/'))).toBe(true);
  let app = page.frames().find(f => f.url().includes('/disconnected-content/'));
  await expect(app.getByRole('heading', { name: 'Disconnected Content', exact: true })).toBeVisible();
  await expect(app.getByText('Available storage', { exact: true })).toBeVisible({ timeout: 15000 });
  const initialState = await app.evaluate(async () => JSON.parse(await window.cockpit.spawn(
    ['/usr/libexec/disconnected-content', 'status'], { superuser: 'require', err: 'message' })));
  const disconnected = initialState.role === 'disconnected';
  await app.getByRole('tab', { name: disconnected ? 'Incoming bundles' : 'Repositories', exact: true }).click();
  await expect(app.getByText(disconnected ? 'Transfer inbox' : 'Choose content to mirror', { exact: true })).toBeVisible();
  await app.getByRole('tab', { name: 'Overview', exact: true }).click();
  if (process.argv.includes('--run-import')) {
    if (!disconnected) throw new Error('Import browser check requires the disconnected role');
    const bundle = process.env.COCKPIT_TEST_BUNDLE;
    if (!bundle) throw new Error('Set COCKPIT_TEST_BUNDLE to the staged test archive name');
    await app.getByRole('tab', { name: 'Incoming bundles', exact: true }).click();
    const row = app.locator('.dc-repo').filter({ hasText: bundle });
    await row.getByRole('button', { name: 'Import bundle', exact: true }).click();
    await expect.poll(async () => {
      const state = await app.evaluate(async () => JSON.parse(await window.cockpit.spawn(
        ['/usr/libexec/disconnected-content', 'status'], { superuser: 'require', err: 'message' })));
      return state.jobs.find(j => j.operation === 'import' && j.bundle_name === bundle)?.status;
    }, { timeout: 120000, intervals: [1000, 2000] }).toBe('succeeded');
    await app.getByRole('tab', { name: /^Snapshots/ }).click();
    await expect(app.getByText('Local repository URLs', { exact: true }).first()).toBeVisible({ timeout: 15000 });
    await page.screenshot({ path: '.local/cockpit-import.png', fullPage: true });
    console.log('Browser-started disconnected import, signature verification, and local snapshot visibility passed');
    await app.getByRole('tab', { name: 'Overview', exact: true }).click();
  }
  if (process.argv.includes('--run-cycle')) {
    const status = () => app.evaluate(async () => JSON.parse(await window.cockpit.spawn(
      ['/usr/libexec/disconnected-content', 'status'], { superuser: 'require', err: 'message' })));
    const before = await status();
    if (before.profile.max_gib > 1 || before.profile.repositories.length !== 1)
      throw new Error('Browser cycle requires one preselected repository with a budget of at most 1 GiB');
    const previous = before.jobs.map(j => j.id);
    await app.getByRole('button', { name: 'Sync snapshot', exact: true }).click();
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
    await app.getByRole('tab', { name: 'Jobs & evidence', exact: true }).click();
    await page.screenshot({ path: '.local/cockpit-jobs.png', fullPage: true });
    await app.getByRole('tab', { name: 'Overview', exact: true }).click();
  }
  await page.screenshot({ path: '.local/cockpit-overview.png', fullPage: true });
  if (errors.length) throw new Error(errors.join('\n'));
  if (backgroundErrors.size) console.log('Other Cockpit pages reported background exceptions:', [...backgroundErrors].join(', '));
  console.log('Cockpit navigation, privileged backend status, repository screen, and overview rendering passed');
  await context.close();
} finally { await browser.close(); }

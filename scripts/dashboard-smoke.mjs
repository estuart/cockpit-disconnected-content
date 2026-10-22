// Render real backend-derived workflow states and verify user actions stay scoped.
import { chromium, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { readFile, mkdir } from 'node:fs/promises';
import { createServer } from 'node:http';

const fixtures = Object.assign({}, ...['scripts/dashboard-fixtures.py', 'scripts/disconnected-fixtures.py'].map(script => JSON.parse(execFileSync('python3', [script], { encoding: 'utf8' }))));
const assets = new Map(await Promise.all(['index.html', 'index.js', 'index.css'].map(async name => [name, await readFile(`dist/${name}`)])));
const server = createServer((request, response) => {
  const name = request.url.split('/').pop() || 'index.html';
  if (name === 'cockpit.js') { response.end(''); return; }
  if (!assets.has(name)) { response.writeHead(404); response.end(); return; }
  response.setHeader('Content-Type', name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html');
  response.end(assets.get(name));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const url = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ channel: process.env.CI ? undefined : 'chrome', headless: true });
// Manifest summary returned by the mocked bundle-info call.
const bundleInfo = { bundle_name: 'transfer.tar', bytes: 23, snapshot_id: 'a'.repeat(32), created: '2025-12-31T10:00:00+00:00',
  os: { major: '10', arch: 'x86_64' }, matches_host: true, verified: false,
  repositories: { 'example-repo': { packages: 146, package_bytes: 90000000 } }, signing_keys: {} };
const connectedTitles = { select: 'Choose repositories to mirror', sync: 'Ready to sync', export: 'Build a transfer bundle',
  ready: '✓ Bundle ready to transfer', historical: 'Check the existing bundle', missing: 'Build a transfer bundle',
  changed: 'Ready to sync', failed: 'Sync failed', running: 'Syncing…', 'preflight-failed': 'Size check failed' };
const disconnectedTitles = { source: "Transfer folder isn't available", waiting: 'Waiting for a transfer bundle',
  incoming: 'New bundle ready to import', policy: 'Imports are turned off', checksum: 'A bundle needs attention',
  publisher: 'Install the web publisher', url: 'Set the address clients will use',
  publish: 'Imported and validated. Ready to publish.', clients: /^✓ Clients can use the .+ snapshot$/,
  pending: 'New bundle ready to import', older: 'Imported and validated. Ready to publish.',
  'server-down': 'Publication needs attention', 'public-missing': 'Publication needs attention',
  'url-changed': 'Address changed. Publish again so clients get it.', 'failed-import': 'Import failed',
  'failed-publish': 'Publishing failed', 'running-import': 'Importing bundle…', 'running-publish': 'Publishing…',
  known: /^✓ Clients can use the .+ snapshot$/,
  // An unpublished import must not hide a newer bundle that hasn't been imported yet.
  'new-over-publish': 'New bundle ready to import',
  // A file whose last import failed doesn't take over while current content is served.
  leftover: /^✓ Clients can use the .+ snapshot$/ };
const calls = page => page.evaluate(() => window.testCalls);
try {
  for (const [name, fixture] of Object.entries(fixtures)) {
    const page = await browser.newPage({ viewport: { width: 1400, height: 1100 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(({ state, info }) => {
      window.testCalls = [];
      window.cockpit = { spawn(args) {
        window.testCalls.push(args.slice(1));
        const reply = { status: state, 'client-automation': state.automation, 'bundle-info': info }[args[1]] ?? {};
        const result = Promise.resolve(JSON.stringify(reply));
        result.input = input => window.testCalls.push(['input', args[1], JSON.parse(input)]);
        return result;
      } };
    }, { state: fixture, info: bundleInfo });
    await page.goto(url);
    const hero = page.locator('.dc-hero-title').first();
    if (fixture.role === 'disconnected') {
      const caseName = name.replace('disconnected-', '');
      await expect(hero).toHaveText(disconnectedTitles[caseName]);
      await expect(page.getByRole('list', { name: 'Disconnected workflow' })).toBeVisible();
      if (caseName === 'waiting') await expect(page.locator('.dc-steps [aria-current=step]')).toContainText('Import');
      if (caseName.startsWith('running')) {
        await expect(page.getByRole('button', { name: 'Stop', exact: true })).toBeEnabled();
        await expect(page.locator('.dc-steps [aria-current=step]')).toContainText(caseName.endsWith('publish') ? 'Publish' : 'Import');
      }
      if (caseName === 'incoming') {
        // A bundle without keys imports in one click.
        await page.getByRole('button', { name: 'Import', exact: true }).click();
        await expect.poll(async () => (await calls(page)).some(call => call[0] === 'start' && call[1] === 'import' && call[2] === 'transfer.tar')).toBe(true);
        expect((await calls(page)).some(call => call[0] === 'scan-bundle-keys')).toBe(false);
      }
      if (caseName === 'clients') {
        await mkdir('.local', { recursive: true });
        await page.screenshot({ path: '.local/disconnected-dashboard-fixture.png', fullPage: true });
        await page.getByRole('button', { name: 'Configure clients', exact: true }).click();
        await expect(page.getByRole('heading', { name: /Point clients at the/ })).toBeVisible();
        let event = page.waitForEvent('download');
        await page.getByRole('button', { name: 'Download Ansible kit (.zip)', exact: true }).click();
        let download = await event;
        expect(download.suggestedFilename()).toBe('disconnected-content-aaaaaaaa-ansible.zip');
        const zip = await readFile(await download.path());
        for (const [file, content] of Object.entries(fixture.automation.files)) {
          // Entries are stored uncompressed, so names and exact contents appear verbatim.
          expect(zip.includes(Buffer.from(file))).toBe(true);
          expect(zip.includes(Buffer.from(content))).toBe(true);
        }
        await page.getByText('Download files individually or preview them').click();
        event = page.waitForEvent('download');
        await page.getByRole('button', { name: 'security-updates.yml', exact: true }).click();
        download = await event;
        expect(await readFile(await download.path(), 'utf8')).toBe(fixture.automation.files['security-updates.yml']);
        expect((await calls(page)).some(call => call[0] === 'client-automation' && call[1] === fixture.publication.current.job_id)).toBe(true);
        await expect(page.getByLabel('Client setup commands')).toContainText('--assumeno');
      }
      if (caseName === 'publish' || caseName === 'older') {
        await page.getByRole('button', { name: /^Publish .+ snapshot$/ }).click();
        await expect.poll(async () => (await calls(page)).some(call => call[0] === 'start' && call[1] === 'publish' && call[2] === fixture.disconnected.target_snapshot_id)).toBe(true);
      }
      if (caseName === 'failed-publish') {
        await page.getByRole('button', { name: 'Try again', exact: true }).click();
        await expect.poll(async () => (await calls(page)).some(call => call[0] === 'start' && call[1] === 'publish')).toBe(true);
      }
      if (caseName === 'url') {
        await page.getByRole('textbox', { name: 'Client-facing address', exact: true }).fill('http://mirror.example.org');
        await page.getByRole('button', { name: 'Save address', exact: true }).click();
        await expect.poll(async () => (await calls(page)).find(call => call[0] === 'input' && call[1] === 'save-publication-settings')?.[2])
          .toEqual({ base_url: 'http://mirror.example.org' });
      }
      if (caseName === 'leftover') {
        await expect(page.locator('.pf-v6-c-alert__title').filter({ hasText: '1 file in the transfer folder needs attention' })).toBeVisible();
      }
      if (caseName === 'checksum' || caseName === 'known') {
        await page.getByRole('tab', { name: 'Content', exact: false }).click();
        await expect(page.getByText(caseName === 'checksum' ? "Can't import" : 'Already imported', { exact: true })).toBeVisible();
        await expect(page.getByRole('button', { name: caseName === 'checksum' ? 'Import' : 'Check again', exact: true }))[caseName === 'checksum' ? 'toBeDisabled' : 'toBeEnabled']();
      }
      if (caseName === 'incoming') {
        await page.getByRole('tab', { name: 'Settings', exact: true }).click();
        await expect(page.getByRole('textbox', { name: 'Transfer folder', exact: true })).toHaveValue(fixture.import_source.path);
        expect((await calls(page)).some(call => call[0] === 'save-import-settings')).toBe(false);
      }
    } else {
      await expect(hero).toHaveText(connectedTitles[name]);
      await expect(page.getByRole('list', { name: 'Connected workflow', exact: true })).toBeVisible();
      if (name === 'ready') {
        await expect(page.getByLabel('Transfer files', { exact: true })).toContainText('.tar.sha256');
        await page.getByRole('button', { name: 'Sync & build new bundle', exact: true }).click();
        await expect.poll(async () => (await calls(page)).some(call => call[0] === 'start' && call[1] === 'sync-export')).toBe(true);
        expect((await calls(page)).some(call => call[0] === 'save-profile')).toBe(false);
        // Selection changes save immediately, with the saved size limit.
        await page.getByRole('tab', { name: 'Repositories', exact: true }).click();
        await page.getByRole('checkbox', { name: 'RHEL 10 AppStream', exact: true }).check();
        await expect.poll(async () => (await calls(page)).find(call => call[0] === 'input' && call[1] === 'save-profile')?.[2])
          .toEqual({ repositories: ['example-baseos', 'example-appstream'], max_gib: 1 });
        await expect(page.getByText('✓ Selection saved', { exact: true })).toBeVisible();
        // Settings: the export folder and size limit save only on explicit actions.
        await page.getByRole('tab', { name: 'Settings', exact: true }).click();
        await expect(page.getByRole('textbox', { name: 'Export folder', exact: true })).toHaveValue(fixture.export_settings.path);
        await page.getByRole('textbox', { name: 'Export folder', exact: true }).fill('/mnt/transfer');
        expect((await calls(page)).some(call => call[0] === 'save-export-settings')).toBe(false);
        await page.getByRole('button', { name: 'Save', exact: true }).first().click();
        await expect.poll(async () => (await calls(page)).find(call => call[0] === 'input' && call[1] === 'save-export-settings')?.[2])
          .toEqual({ export_dir: '/mnt/transfer' });
        await page.getByLabel('Size limit per snapshot (GiB)', { exact: true }).fill('3');
        await page.locator('.dc-setting').filter({ hasText: 'Size limit per snapshot' }).getByRole('button', { name: 'Save', exact: true }).click();
        await expect.poll(async () => (await calls(page)).filter(call => call[0] === 'input' && call[1] === 'save-profile').pop()?.[2])
          .toEqual({ repositories: ['example-baseos'], max_gib: 3 });
        await expect(page.getByText(/update-ca-trust/)).toBeVisible();
      }
      if (name === 'export') {
        await page.getByRole('button', { name: 'Build bundle', exact: true }).click();
        await expect.poll(async () => (await calls(page)).some(call => call[0] === 'start' && call[1] === 'export' && call[2] === 'a'.repeat(32))).toBe(true);
        await mkdir('.local', { recursive: true });
        await page.screenshot({ path: '.local/connected-dashboard-fixture.png', fullPage: true });
      }
      if (name === 'historical') {
        await page.getByRole('button', { name: 'Verify bundle', exact: true }).click();
        await expect.poll(async () => (await calls(page)).some(call => call[0] === 'start' && call[1] === 'verify-export')).toBe(true);
      }
      if (name === 'failed') {
        await page.getByRole('button', { name: 'Retry sync & build', exact: true }).click();
        await expect.poll(async () => (await calls(page)).some(call => call[0] === 'start' && call[1] === 'sync-export')).toBe(true);
      }
      if (name === 'changed') {
        await expect(page.getByRole('table', { name: 'Selected repository status' })).toContainText('never');
      }
      if (name === 'preflight-failed') {
        await expect(page.locator('.dc-steps [aria-current=step]')).toContainText('Sync & build bundle');
      }
      if (name === 'running') {
        await expect(page.getByRole('button', { name: 'Stop', exact: true })).toBeEnabled();
        await expect(page.getByRole('list', { name: 'Job progress' })).toBeVisible();
      }
      if (name === 'select') {
        await page.getByRole('tab', { name: 'Repositories', exact: true }).click();
        await expect(page.getByRole('button', { name: 'Sync & build bundle', exact: true })).toBeDisabled();
      }
    }
    for (const tab of await page.getByRole('tab').all()) {
      await tab.click();
      await page.setViewportSize({ width: 390, height: 1100 });
      const widths = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }));
      expect(widths.scroll, `${name} ${await tab.textContent()} fits a narrow screen`).toBeLessThanOrEqual(widths.width + 1);
      await page.setViewportSize({ width: 1400, height: 1100 });
    }
    expect(errors).toEqual([]);
    await page.close();
  }
  console.log('Ten connected and twenty-one disconnected workflow states, scoped actions, auto-saved selection, downloads, and responsive layouts passed');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}

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
try {
  for (const [name, fixture] of Object.entries(fixtures)) {
    const page = await browser.newPage({ viewport: { width: 1400, height: 1100 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(state => {
      window.testCalls = [];
      window.cockpit = { spawn(args) {
        window.testCalls.push(args.slice(1));
        const result = Promise.resolve(JSON.stringify(args[1] === 'status' ? state : args[1] === 'client-automation' ? state.automation : {}));
        result.input = input => window.testCalls.push(['input', JSON.parse(input)]);
        return result;
      } };
    }, fixture);
    await page.goto(url);
    if (fixture.role === 'disconnected') {
      await expect(page.locator('.dc-freshness').getByText('Last successful import', { exact: true })).toBeVisible();
      await expect(page.getByRole('list', { name: 'Disconnected content workflow' })).toBeVisible();
      const caseName = name.replace('disconnected-', '');
      const titles = { source: 'Choose an available transfer folder', waiting: 'Waiting for transferred bundles',
        incoming: 'Transferred bundles need review', policy: 'Review import policy', checksum: 'Transferred bundles need review',
        publisher: 'Set up the repository publisher', url: 'Configure the client-facing server URL',
        publish: 'Review imported content for publication', clients: 'Published content ready for client setup',
        pending: 'Transferred bundles need review', older: 'Review imported content for publication',
        'server-down': 'Review imported content for publication', 'public-missing': 'Review imported content for publication',
        'url-changed': 'Review imported content for publication', 'failed-import': 'Latest import failed',
        'failed-publish': 'Latest publication failed', 'running-import': 'Current operation', 'running-publish': 'Current operation',
        known: 'Published content ready for client setup' };
      await expect(page.getByText(titles[caseName], { exact: true })).toBeVisible();
      if (caseName === 'waiting') await expect(page.locator('.dc-workflow [aria-current=step]')).toContainText('Import & validate');
      if (caseName.startsWith('running')) {
        await expect(page.getByRole('button', { name: 'Stop job', exact: true })).toBeEnabled();
        await expect(page.locator('.dc-workflow [aria-current=step]')).toContainText(caseName.endsWith('publish') ? 'Publish content' : 'Import & validate');
      }
      if (caseName === 'clients') {
        await mkdir('.local', { recursive: true });
        await page.screenshot({ path: '.local/disconnected-dashboard-fixture.png', fullPage: true });
        await page.getByRole('button', { name: 'Configure clients', exact: true }).click();
        await expect(page.getByRole('heading', { name: 'Ansible client automation' })).toBeVisible();
        const event = page.waitForEvent('download');
        await page.getByRole('button', { name: 'Download security updates playbook', exact: true }).click();
        const download = await event;
        expect(download.suggestedFilename()).toBe('security-updates.yml');
        expect(await readFile(await download.path(), 'utf8')).toBe(fixture.automation.files['security-updates.yml']);
        expect((await page.evaluate(() => window.testCalls)).some(call => call[0] === 'client-automation' && call[1] === fixture.publication.current.job_id)).toBe(true);
        await page.getByRole('tab', { name: 'Overview', exact: true }).click();
        await expect(page.locator('.dc-workflow li').last()).toContainText('application unconfirmed');
        await expect(page.locator('.dc-workflow li').last()).not.toHaveClass(/dc-step-complete/);
      }
      if (caseName === 'publish' || caseName === 'older') {
        await page.getByRole('button', { name: 'Review publication', exact: true }).click();
        await expect(page.getByLabel('Snapshot to publish', { exact: true })).toHaveValue(fixture.disconnected.target_snapshot_id);
        expect((await page.evaluate(() => window.testCalls)).some(call => call[0] === 'start')).toBe(false);
        await page.getByRole('textbox', { name: 'Public server URL', exact: true }).fill('https://draft.example.org');
        await page.getByRole('tab', { name: 'Overview', exact: true }).click();
        await expect(page.locator('.pf-v6-c-alert__title').filter({ hasText: 'Unsaved settings edits' })).toBeVisible();
        expect((await page.evaluate(() => window.testCalls)).some(call => call[0] === 'save-publication-settings')).toBe(false);
      }
      if (caseName === 'checksum' || caseName === 'known') {
        await page.getByRole('tab', { name: 'Incoming bundles', exact: true }).click();
        await expect(page.getByText(caseName === 'checksum' ? 'Checksum needs attention' : 'Previously imported checksum', { exact: true })).toBeVisible();
        await expect(page.getByRole('button', { name: caseName === 'checksum' ? 'Import bundle' : 'Recheck bundle', exact: true }))[caseName === 'checksum' ? 'toBeDisabled' : 'toBeEnabled']();
      }
      await page.setViewportSize({ width: 390, height: 1100 });
      const widths = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }));
      expect(widths.scroll).toBeLessThanOrEqual(widths.width + 1);
      expect(errors).toEqual([]);
      await page.close();
      continue;
    }
    await expect(page.locator('.dc-freshness').getByText('Last successful sync', { exact: true })).toBeVisible();
    await expect(page.getByRole('list', { name: 'Saved selection workflow', exact: true })).toBeVisible();
    const titles = { select: 'Choose content to mirror', sync: 'Saved selection needs a sync', export: 'Build a transfer bundle',
      ready: 'Verified bundle ready for transfer', historical: 'Verify the existing transfer bundle', missing: 'Build a transfer bundle',
      changed: 'Saved selection needs a sync', failed: 'Latest sync failed', running: 'Current operation', 'preflight-failed': 'Latest preflight failed' };
    await expect(page.getByText(titles[name], { exact: true })).toBeVisible();
    if (name === 'ready') {
      // Draft edits must not be silently saved by a dashboard action.
      await page.getByRole('tab', { name: 'Repositories', exact: true }).click();
      await page.getByRole('checkbox', { name: 'RHEL 10 AppStream', exact: true }).check();
      await expect(page.getByText('Unsaved changes', { exact: true })).toBeVisible();
      await page.getByRole('tab', { name: 'Overview', exact: true }).click();
      await expect(page.locator('.pf-v6-c-alert__title').filter({ hasText: 'Unsaved repository edits' })).toBeVisible();
      await page.getByRole('button', { name: 'Sync saved selection', exact: true }).click();
      const calls = await page.evaluate(() => window.testCalls);
      expect(calls.some(call => call[0] === 'start' && call[1] === 'sync')).toBe(true);
      expect(calls.some(call => call[0] === 'save-profile')).toBe(false);
      await page.getByRole('button', { name: 'Show transfer files', exact: true }).click();
      await expect(page.getByLabel('Transfer bundle details', { exact: true })).toContainText('.tar.sha256');
    }
    if (name === 'export') {
      await page.getByRole('button', { name: 'Build & verify transfer bundle', exact: true }).click();
      expect((await page.evaluate(() => window.testCalls)).some(call => call[0] === 'start' && call[1] === 'export' && call[2] === 'a'.repeat(32))).toBe(true);
      await mkdir('.local', { recursive: true });
      await page.screenshot({ path: '.local/connected-dashboard-fixture.png', fullPage: true });
    }
    if (name === 'historical') {
      await page.getByRole('button', { name: 'Verify existing bundle', exact: true }).click();
      expect((await page.evaluate(() => window.testCalls)).some(call => call[0] === 'start' && call[1] === 'verify-export')).toBe(true);
    }
    if (name === 'changed') {
      await expect(page.getByRole('table', { name: 'Selected repository status' })).toContainText('Never');
    }
    if (name === 'preflight-failed') {
      await expect(page.locator('.dc-workflow [aria-current=step]')).toContainText('Sync & validate');
    }
    if (name === 'running') {
      await expect(page.getByRole('button', { name: 'Stop job', exact: true })).toBeEnabled();
      await expect(page.getByText('In progress', { exact: false })).toBeVisible();
    }
    await page.setViewportSize({ width: 390, height: 1100 });
    const dimensions = await page.evaluate(() => ({ viewport: innerWidth, content: document.documentElement.scrollWidth }));
    expect(dimensions.content).toBeLessThanOrEqual(dimensions.viewport + 1);
    expect(errors).toEqual([]);
    await page.close();
  }
  console.log('Ten connected and nineteen disconnected workflow states, scoped actions, unconfirmed client application, downloads, drafts, and responsive layouts passed');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}

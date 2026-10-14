// Render real backend-derived workflow states and verify user actions stay scoped.
import { chromium, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { readFile, mkdir } from 'node:fs/promises';
import { createServer } from 'node:http';

const fixtures = JSON.parse(execFileSync('python3', ['scripts/dashboard-fixtures.py'], { encoding: 'utf8' }));
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
        const result = Promise.resolve(JSON.stringify(args[1] === 'status' ? state : {}));
        result.input = input => window.testCalls.push(['input', JSON.parse(input)]);
        return result;
      } };
    }, fixture);
    await page.goto(url);
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
  console.log('Ten connected workflow states, scoped dashboard actions, unsaved drafts, transfer details, and responsive layouts passed');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}

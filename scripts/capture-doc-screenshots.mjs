// Render the real frontend with generic fixtures; never capture live host data.
import { chromium, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { readFile, mkdir } from 'node:fs/promises';
import { createServer } from 'node:http';

const connected = JSON.parse(execFileSync('python3', ['scripts/dashboard-fixtures.py'], { encoding: 'utf8' })).ready;
const disconnected = JSON.parse(execFileSync('python3', ['scripts/disconnected-fixtures.py'], { encoding: 'utf8' }))['disconnected-clients'];
const signing = JSON.parse(execFileSync('python3', ['scripts/signing-key-fixtures.py'], { encoding: 'utf8' }));
// Fixed demo time avoids screenshots that grow stale every day.
const demoTime = Date.parse('2026-01-01T12:00:00Z');
connected.connected.latest_export.bytes = 91033600;
connected.connected.latest_export.bundled_signing_keys = 1;
connected.snapshots[0].export.bytes = 91033600;
connected.snapshots[0].export.bundled_signing_keys = 1;
signing.state.bundle_key_candidates = [signing.candidate];
signing.review.source = `Bundle ${signing.candidate.bundle_name} SHA256 ${signing.candidate.bundle_sha256}`;
const assets = new Map(await Promise.all(['index.html', 'index.js', 'index.css'].map(async name => [name, await readFile(`dist/${name}`)])));
const server = createServer((request, response) => {
  const name = request.url.split('/').pop() || 'index.html';
  if (name === 'cockpit.js') { response.end(''); return; }
  if (!assets.has(name)) { response.writeHead(404); response.end(); return; }
  response.setHeader('Content-Type', name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html');
  response.end(assets.get(name));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({ channel: process.env.CI ? undefined : 'chrome', headless: true });
const output = 'docs/screenshots';
await mkdir(output, { recursive: true });
async function pageFor(state) {
  state = JSON.parse(JSON.stringify(state).replaceAll('/private/var/lib/disconnected-content', '/var/lib/disconnected-content'));
  if (state.import_source) state.import_source.scanned_at = '2026-01-01T11:55:00Z';
  const page = await browser.newPage({ viewport: { width: 1440, height: 1080 }, timezoneId: 'UTC' });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.clock.install({ time: demoTime });
  await page.addInitScript(({ state, review }) => {
    window.cockpit = { spawn(args) {
      let finish;
      const result = new Promise(resolve => { finish = resolve; });
      if (args[1] === 'status') finish(JSON.stringify(state));
      if (args[1] === 'client-automation') finish(JSON.stringify(state.automation));
      result.input = () => finish(JSON.stringify(review));
      return result;
    } };
  }, { state, review: signing.review });
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await expect(page.getByRole('heading', { name: 'Disconnected Content', exact: true })).toBeVisible();
  return { page, errors };
}
try {
  const connectedPage = await pageFor(connected);
  await expect(connectedPage.page.getByText('Verified bundle ready for transfer', { exact: true })).toBeVisible();
  await connectedPage.page.screenshot({ path: `${output}/connected-dashboard.png`, fullPage: true });
  expect(connectedPage.errors).toEqual([]);
  await connectedPage.page.close();

  const disconnectedPage = await pageFor(disconnected);
  await expect(disconnectedPage.page.getByText('Published content ready for client setup', { exact: true })).toBeVisible();
  await disconnectedPage.page.screenshot({ path: `${output}/disconnected-dashboard.png`, fullPage: true });
  expect(disconnectedPage.errors).toEqual([]);
  await disconnectedPage.page.close();

  const keysPage = await pageFor(signing.state);
  await keysPage.page.getByRole('tab', { name: 'Incoming bundles', exact: true }).click();
  await expect(keysPage.page.getByRole('button', { name: 'Review bundled keys', exact: true })).toBeVisible();
  await keysPage.page.screenshot({ path: `${output}/incoming-bundles.png`, fullPage: true });
  await keysPage.page.getByRole('tab', { name: 'Signing keys', exact: true }).click();
  await expect(keysPage.page.getByText('Untrusted candidate', { exact: true })).toBeVisible();
  await keysPage.page.screenshot({ path: `${output}/bundled-key-candidates.png`, fullPage: true });
  await keysPage.page.getByRole('button', { name: 'Review bundled public key', exact: true }).click();
  const review = keysPage.page.getByRole('region', { name: 'Public signing key review' });
  await expect(review).toBeVisible();
  await expect(keysPage.page.getByRole('button', { name: 'Approve signing key', exact: true })).toBeDisabled();
  await review.screenshot({ path: `${output}/signing-key-review.png` });
  await keysPage.page.getByRole('tab', { name: 'Client setup', exact: true }).click();
  await expect(keysPage.page.getByRole('heading', { name: 'Ansible client automation', exact: true })).toBeVisible();
  await keysPage.page.getByRole('heading', { name: 'Ansible client automation', exact: true }).locator('..').screenshot({ path: `${output}/client-automation.png` });
  expect(keysPage.errors).toEqual([]);
  await keysPage.page.close();
  console.log('Six documentation screenshots captured from generic frontend fixtures');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}

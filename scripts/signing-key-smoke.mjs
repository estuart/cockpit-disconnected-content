// Exercise key review, explicit approval, pinned client downloads, and narrow layouts.
import { chromium, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { readFile, mkdir } from 'node:fs/promises';
import { createServer } from 'node:http';
const fixture = JSON.parse(execFileSync('python3', ['scripts/signing-key-fixtures.py'], { encoding: 'utf8' }));
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
try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 1100 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(({ state, review }) => {
    window.testCalls = [];
    window.cockpit = { spawn(args) {
      let finish;
      const result = new Promise(resolve => { finish = resolve; });
      window.testCalls.push(args.slice(1));
      if (args[1] === 'status') finish(JSON.stringify(state));
      if (args[1] === 'client-automation') finish(JSON.stringify(state.automation));
      result.input = input => {
        window.testCalls.push(['input', args[1], JSON.parse(input)]);
        if (args[1] === 'trust-signing-key') {
          state.trusted_signing_keys = [{ repository_id: review.repository_id, signing_keys: [Object.assign({}, review, { approved_at: review.reviewed_at })] }];
          finish('{}');
        } else finish(JSON.stringify(review));
      };
      return result;
    } };
  }, fixture);
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.getByRole('tab', { name: 'Signing keys', exact: true }).click();
  await page.getByRole('textbox', { name: 'Repository ID', exact: true }).fill('vendor-example');
  await page.getByLabel('Public key in saved transfer folder').selectOption('vendor.asc');
  await page.getByRole('button', { name: 'Review public key', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Public signing key review' })).toContainText(fixture.review.fingerprint);
  await expect(page.getByRole('button', { name: 'Approve signing key', exact: true })).toBeDisabled();
  await page.getByRole('textbox', { name: 'Repository ID', exact: true }).fill('another-repo');
  await expect(page.getByRole('button', { name: 'Approve signing key', exact: true })).toHaveCount(0);
  await page.getByRole('textbox', { name: 'Repository ID', exact: true }).fill('vendor-example');
  await page.getByRole('button', { name: 'Review public key', exact: true }).click();
  await page.getByRole('checkbox', { name: /I verified the full fingerprint/ }).check();
  await page.getByRole('button', { name: 'Approve signing key', exact: true }).click();
  await expect(page.locator('.pf-v6-c-alert__title').filter({ hasText: 'Signing key approved. Return to Incoming bundles and retry the import.' })).toBeVisible();
  const calls = await page.evaluate(() => window.testCalls);
  const approval = calls.find(call => call[0] === 'input' && call[1] === 'trust-signing-key')[2];
  expect(approval).toEqual({ review_id: fixture.review.review_id, fingerprint: fixture.review.fingerprint, confirmed: true });
  expect(calls.some(call => call[0] === 'start')).toBe(false);
  await mkdir('.local', { recursive: true });
  await page.screenshot({ path: '.local/signing-key-fixture.png', fullPage: true });
  await page.getByRole('tab', { name: 'Client setup', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Published signing keys' })).toContainText(fixture.review.sha256);
  await expect(page.getByLabel('Client repository configuration')).toContainText(`gpgkey=file:///etc/pki/rpm-gpg/DISCONNECTED-CONTENT-${fixture.review.fingerprint}.asc`);
  const event = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download configure repos playbook', exact: true }).click();
  const download = await event;
  expect(await readFile(await download.path(), 'utf8')).toBe(fixture.state.automation.files['configure-repos.yml']);
  await page.screenshot({ path: '.local/signing-key-client-fixture.png', fullPage: true });
  for (const tab of ['Signing keys', 'Client setup']) {
    await page.getByRole('tab', { name: tab, exact: true }).click();
    await page.setViewportSize({ width: 390, height: 1100 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  }
  expect(errors).toEqual([]);
  console.log('Signing key review, explicit fingerprint approval, draft invalidation, pinned client downloads, and responsive layouts passed');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}

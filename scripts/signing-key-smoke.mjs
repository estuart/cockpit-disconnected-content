// Exercise inline key approval during import, manual key approval, pinned client downloads, and narrow layouts.
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
const grouped = fixture.review.fingerprint.match(/.{1,4}/g).join(' ');
try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 1100 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(({ state, review, scan, info }) => {
    window.testCalls = [];
    window.cockpit = { spawn(args) {
      let finish;
      const result = new Promise(resolve => { finish = resolve; });
      window.testCalls.push(args.slice(1));
      if (args[1] === 'status') finish(JSON.stringify(state));
      if (args[1] === 'client-automation') finish(JSON.stringify(state.automation));
      if (args[1] === 'start') finish('{}');
      result.input = input => {
        window.testCalls.push(['input', args[1], JSON.parse(input)]);
        if (args[1] === 'bundle-info') finish(JSON.stringify(info));
        else if (args[1] === 'scan-bundle-keys') finish(JSON.stringify(scan));
        else if (args[1] === 'trust-signing-key') {
          state.trusted_signing_keys = [{ repository_id: review.repository_id, signing_keys: [Object.assign({}, review, { approved_at: review.reviewed_at })] }];
          finish('{}');
        } else finish(JSON.stringify(review));
      };
      return result;
    } };
  }, fixture);
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const calls = () => page.evaluate(() => window.testCalls);

  // Import: only the key that is neither approved nor host-trusted is shown, and approval precedes the import.
  await page.getByRole('tab', { name: 'Content', exact: false }).click();
  await expect(page.getByText(/Includes 2 public signing keys/)).toBeVisible();
  await page.getByRole('button', { name: 'Review & import', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByLabel('Full fingerprint', { exact: true })).toHaveText(grouped);
  await expect(dialog).toContainText('key 1 of 1');
  const approve = dialog.getByRole('button', { name: 'Approve & import', exact: true });
  await expect(approve).toBeDisabled();
  expect((await calls()).some(call => call[0] === 'trust-signing-key' || call[0] === 'start')).toBe(false);
  const reviews = (await calls()).filter(call => call[0] === 'input' && call[1] === 'review-signing-key');
  expect(reviews.map(call => call[2])).toEqual([{ bundle_key: fixture.scan.candidates[0].candidate_id }]);
  await mkdir('.local', { recursive: true });
  await page.screenshot({ path: '.local/signing-key-fixture.png', fullPage: true });
  await dialog.getByRole('checkbox', { name: /I compared the full fingerprint/ }).check();
  await approve.click();
  await expect(dialog).toHaveCount(0);
  await expect(page.locator('.pf-v6-c-alert__title').filter({ hasText: 'Import started' })).toBeVisible();
  let log = await calls();
  const approval = log.findIndex(call => call[0] === 'input' && call[1] === 'trust-signing-key');
  expect(log[approval][2]).toEqual({ review_id: fixture.review.review_id, fingerprint: fixture.review.fingerprint, confirmed: true });
  const start = log.findIndex(call => call[0] === 'start' && call[1] === 'import' && call[2] === 'example.tar');
  expect(start).toBeGreaterThan(approval);

  // Cancelling before approval neither trusts a key nor starts an import.
  await page.getByRole('tab', { name: 'Content', exact: false }).click();
  const before = (await calls()).length;
  await page.getByRole('button', { name: 'Review & import', exact: true }).click();
  await expect(page.getByRole('dialog').getByLabel('Full fingerprint', { exact: true })).toBeVisible();
  await page.getByRole('dialog').getByRole('button', { name: 'Cancel', exact: true }).click();
  log = (await calls()).slice(before);
  expect(log.some(call => call[0] === 'trust-signing-key' || call[0] === 'start')).toBe(false);

  // Manual key entry in Settings keeps the same explicit confirmation.
  await page.getByRole('tab', { name: 'Settings', exact: true }).click();
  await page.getByText('Add a key manually').click();
  await page.getByRole('textbox', { name: 'Repository ID', exact: true }).fill('vendor-example');
  await page.getByLabel('Key file in the transfer folder').selectOption('vendor.asc');
  await page.getByRole('button', { name: 'Review key', exact: true }).click();
  const region = page.getByRole('region', { name: 'Public signing key review' });
  await expect(region).toContainText(grouped);
  await expect(region.getByRole('button', { name: 'Approve key', exact: true })).toBeDisabled();
  await page.getByRole('textbox', { name: 'Repository ID', exact: true }).fill('another-repo');
  await expect(region).toHaveCount(0);
  await page.getByRole('textbox', { name: 'Repository ID', exact: true }).fill('vendor-example');
  await page.getByRole('button', { name: 'Review key', exact: true }).click();
  await region.getByRole('checkbox').check();
  await region.getByRole('button', { name: 'Approve key', exact: true }).click();
  await expect(page.locator('.pf-v6-c-alert__title').filter({ hasText: 'Signing key approved' })).toBeVisible();
  const manual = (await calls()).filter(call => call[0] === 'input' && call[1] === 'review-signing-key').pop()[2];
  expect(manual).toEqual({ repository_id: 'vendor-example', key_file: 'vendor.asc' });
  await expect(page.getByRole('table', { name: 'Approved signing keys' })).toContainText(grouped);

  // Client downloads stay pinned to the publication's reviewed keys.
  await page.getByRole('tab', { name: 'Clients', exact: true }).click();
  await expect(page.getByRole('table', { name: 'Published signing keys' })).toContainText(grouped);
  await expect(page.getByLabel('Client repository configuration')).toContainText(`gpgkey=file:///etc/pki/rpm-gpg/DISCONNECTED-CONTENT-${fixture.review.fingerprint}.asc`);
  await expect(page.getByLabel('Client setup commands')).toContainText(`echo '${fixture.review.sha256}  ${fixture.review.fingerprint}.asc' | sha256sum --check`);
  await page.getByText('Download files individually or preview them').click();
  const event = page.waitForEvent('download');
  await page.getByRole('button', { name: 'configure-repos.yml', exact: true }).click();
  const download = await event;
  expect(await readFile(await download.path(), 'utf8')).toBe(fixture.state.automation.files['configure-repos.yml']);
  await page.screenshot({ path: '.local/signing-key-client-fixture.png', fullPage: true });
  for (const tab of ['Settings', 'Clients', 'Content']) {
    await page.getByRole('tab', { name: tab, exact: false }).click();
    await page.setViewportSize({ width: 390, height: 1100 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.setViewportSize({ width: 1400, height: 1100 });
  }
  expect(errors).toEqual([]);
  console.log('Inline key approval before import, cancel without trust, manual approval, pinned client downloads, and responsive layouts passed');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}

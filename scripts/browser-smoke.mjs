// Run against an already authenticated, isolated Cockpit test session.
// Deployment addresses and screenshots belong in ignored local state.
//   COCKPIT_TEST_URL=<session URL> node scripts/browser-smoke.mjs [flags]
// Flags (each changes lab state; the default run is read-only):
//   --check-picker           Open the folder picker, navigate, cancel (COCKPIT_TEST_MOUNT optional)
//   --run-connected-cycle    Sync & build bundle for a single saved repository (<= 5 GiB limit)
//   --run-import             Import COCKPIT_TEST_BUNDLE; approve COCKPIT_TEST_FINGERPRINT if asked
//   --publish-older          Publish the previous snapshot from Content (exercises the advanced path)
//   --run-publish            Publish the latest import from the overview and check client downloads
import { chromium, expect } from '@playwright/test';
import { mkdir, readFile } from 'node:fs/promises';

const url = process.env.COCKPIT_TEST_URL;
if (!url) throw new Error('Set COCKPIT_TEST_URL to the isolated Cockpit test session');
const flag = name => process.argv.includes(name);
await mkdir('.local', { recursive: true });
const browser = await chromium.launch({ channel: process.env.CI ? undefined : 'chrome', headless: true });
const helper = '/usr/libexec/disconnected-content';
try {
  const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1500, height: 1100 }, acceptDownloads: true });
  const page = await context.newPage();
  const errors = [];
  // Only exceptions from this extension fail the run; other Cockpit pages may log their own.
  const client = await context.newCDPSession(page);
  await client.send('Runtime.enable');
  client.on('Runtime.exceptionThrown', event => {
    const details = event.exceptionDetails;
    const sources = [details.url, ...(details.stackTrace?.callFrames ?? []).map(frame => frame.url)].filter(Boolean);
    if (sources.some(source => source.includes('/disconnected-content/'))) errors.push(`${details.text}: ${details.exception?.description ?? ''}`);
  });
  page.on('pageerror', error => { if (String(error.stack).includes('/disconnected-content/')) errors.push(error.message); });
  await page.goto(url);
  // A fresh session starts in limited access; switch when the test account can do so without a prompt.
  const limited = page.getByRole('button', { name: 'Limited access', exact: true });
  if (await limited.isVisible({ timeout: 5000 }).catch(() => false)) {
    await limited.click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('You now have administrative access', { timeout: 15000 });
    await dialog.getByRole('button', { name: 'Close', exact: true }).last().click();
  }
  await page.getByRole('link', { name: 'Disconnected Content', exact: true }).click();
  await expect.poll(() => page.frames().some(f => f.url().includes('/disconnected-content/'))).toBe(true);
  const app = page.frames().find(f => f.url().includes('/disconnected-content/'));
  // The app retries its status call every few seconds, so it recovers once administrative access is on.
  await expect(app.getByRole('heading', { name: 'Disconnected Content', exact: true })).toBeVisible({ timeout: 20000 });
  await expect(app.locator('.dc-hero-title').first()).toBeVisible({ timeout: 20000 });
  const status = () => app.evaluate(async helperPath => JSON.parse(await window.cockpit.spawn(
    [helperPath, 'status'], { superuser: 'require', err: 'message' })), helper);
  const waitForJob = async (operation, previous, timeout) => {
    const steps = new Set();
    let job;
    await expect.poll(async () => {
      job = (await status()).jobs.find(item => item.operation === operation && !previous.includes(item.id));
      if (job?.step) steps.add(job.step);
      return job?.status;
    }, { timeout, intervals: [2000, 5000] }).toMatch(/succeeded|failed|cancelled|interrupted/);
    if (job.status !== 'succeeded') throw new Error(`${operation} ${job.status}: ${job.stage}`);
    return { job, steps: [...steps] };
  };
  const initial = await status();
  const disconnected = initial.role === 'disconnected';
  console.log(`Connected to ${initial.role} host running ${initial.version}; overview shows "${await app.locator('.dc-hero-title').first().textContent()}"`);
  await page.screenshot({ path: `.local/live-${initial.role}-overview.png`, fullPage: true });

  // Every tab renders without extension errors, at full and narrow widths.
  const tabs = disconnected ? ['Overview', 'Content', 'Clients', 'Activity', 'Settings'] : ['Overview', 'Repositories', 'Activity', 'Settings'];
  for (const tab of tabs) {
    await app.getByRole('tab', { name: tab, exact: false }).click();
    await page.waitForTimeout(500);
    await page.setViewportSize({ width: 390, height: 1100 });
    const fits = await app.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1);
    if (!fits) throw new Error(`${tab} overflows a narrow screen`);
    await page.setViewportSize({ width: 1500, height: 1100 });
  }
  await app.getByRole('tab', { name: 'Overview', exact: true }).click();

  if (flag('--check-picker')) {
    if (!disconnected) throw new Error('The folder picker requires the disconnected role');
    await app.getByRole('tab', { name: 'Settings', exact: true }).click();
    await app.getByRole('button', { name: 'Browse…', exact: true }).click();
    const picker = app.getByRole('dialog', { name: 'Choose transfer folder', exact: true });
    await expect(picker).toBeVisible();
    const mount = process.env.COCKPIT_TEST_MOUNT;
    if (mount) await picker.getByRole('button', { name: mount, exact: true }).click();
    await picker.getByRole('button', { name: 'Up one folder', exact: true }).click();
    await picker.getByRole('button', { name: 'Cancel', exact: true }).click();
    expect((await status()).import_source.path).toBe(initial.import_source.path);
    console.log('Folder picker opened, navigated, and cancelled without changing the saved folder');
  }

  if (flag('--run-connected-cycle')) {
    if (disconnected) throw new Error('The connected cycle requires the connected role');
    if (initial.profile.repositories.length !== 1 || initial.profile.max_gib > 5)
      throw new Error('The live cycle requires a single saved repository and a size limit of 5 GiB or less');
    const previous = initial.jobs.map(job => job.id);
    await app.getByRole('tab', { name: 'Repositories', exact: true }).click();
    await app.getByRole('button', { name: 'Sync & build bundle', exact: true }).click();
    await expect(app.getByRole('list', { name: 'Job progress' })).toBeVisible({ timeout: 20000 });
    await page.screenshot({ path: '.local/live-connected-running.png', fullPage: true });
    const { job, steps } = await waitForJob('sync-export', previous, 45 * 60 * 1000);
    await expect(app.locator('.dc-hero-title').first()).toHaveText('✓ Bundle ready to transfer', { timeout: 20000 });
    await expect(app.getByLabel('Transfer files', { exact: true })).toContainText(job.result.path);
    await expect(app.getByLabel('Transfer files', { exact: true })).toContainText(`${job.result.path}.sha256`);
    await page.screenshot({ path: '.local/live-connected-ready.png', fullPage: true });
    console.log(JSON.stringify({ cycle: 'sync-export', job: job.id, snapshot: job.snapshot_id, steps_seen: steps,
      bundle: job.result.path, sha256: job.result.sha256, bundled_signing_keys: job.result.bundled_signing_keys }));
  }

  if (flag('--run-import')) {
    const bundle = process.env.COCKPIT_TEST_BUNDLE;
    if (!disconnected || !bundle) throw new Error('Import requires the disconnected role and COCKPIT_TEST_BUNDLE');
    const before = await status();
    const previous = before.jobs.map(job => job.id);
    await app.getByRole('tab', { name: 'Content', exact: false }).click();
    const row = app.locator('.dc-row').filter({ hasText: bundle });
    await expect(row).toBeVisible();
    await row.getByRole('button').click();
    const dialog = app.getByRole('dialog');
    // A key prompt appears only for keys that are neither approved nor in the host keyring.
    const prompted = await Promise.race([
      dialog.getByLabel('Full fingerprint', { exact: true }).waitFor({ timeout: 15 * 60 * 1000 }).then(() => true, () => false),
      expect.poll(async () => (await status()).jobs.some(job => job.operation === 'import' && !previous.includes(job.id)),
        { timeout: 15 * 60 * 1000, intervals: [2000] }).toBe(true).then(() => false, () => false)]);
    if (prompted) {
      const expected = process.env.COCKPIT_TEST_FINGERPRINT;
      const shown = (await dialog.getByLabel('Full fingerprint', { exact: true }).textContent()).replaceAll(' ', '');
      if (!expected || shown !== expected) throw new Error(`Unexpected key prompt for ${shown}; set COCKPIT_TEST_FINGERPRINT after verifying it independently`);
      await expect(dialog.getByRole('button', { name: /^Approve & / })).toBeDisabled();
      await page.screenshot({ path: '.local/live-key-review.png', fullPage: true });
      await dialog.getByRole('checkbox').check();
      await dialog.getByRole('button', { name: /^Approve & / }).click();
    }
    const { job, steps } = await waitForJob('import', previous, 45 * 60 * 1000);
    await page.screenshot({ path: '.local/live-imported.png', fullPage: true });
    const after = await status();
    console.log(JSON.stringify({ import: job.id, snapshot: job.snapshot_id, prompted_for_key: prompted, steps_seen: steps,
      already_imported: Boolean(job.result?.already_imported),
      approved_keys: after.trusted_signing_keys.flatMap(repo => repo.signing_keys.map(key => `${repo.repository_id}:${key.fingerprint}`)) }));
  }

  if (flag('--publish-older')) {
    if (!disconnected) throw new Error('Publishing requires the disconnected role');
    const before = await status();
    const older = before.snapshots.find(snapshot => snapshot.id !== before.publication.current?.snapshot_id && snapshot.id !== before.disconnected.latest_import?.id);
    if (!older) throw new Error('No older snapshot is available to publish');
    await app.getByRole('tab', { name: 'Content', exact: false }).click();
    await app.getByRole('row').filter({ hasText: older.id.slice(0, 8) }).getByRole('button', { name: 'Publish', exact: true }).click();
    const { job } = await waitForJob('publish', before.jobs.map(item => item.id), 45 * 60 * 1000);
    if (job.snapshot_id !== older.id) throw new Error('Content published a different snapshot');
    await app.getByRole('tab', { name: 'Overview', exact: true }).click();
    await expect(app.locator('.dc-hero-title').first()).toHaveText('Imported and validated. Ready to publish.', { timeout: 20000 });
    console.log(JSON.stringify({ published_older: older.id, overview_offers_latest: true }));
  }

  if (flag('--run-publish')) {
    if (!disconnected) throw new Error('Publishing requires the disconnected role');
    const before = await status();
    const previous = before.jobs.map(job => job.id);
    await app.getByRole('tab', { name: 'Overview', exact: true }).click();
    await app.getByRole('button', { name: /^Publish (.+ snapshot|again)$/ }).click();
    const { job, steps } = await waitForJob('publish', previous, 45 * 60 * 1000);
    await expect(app.locator('.dc-hero-title').first()).toHaveText(/^✓ Clients can use the .+ snapshot$/, { timeout: 20000 });
    await page.screenshot({ path: '.local/live-published.png', fullPage: true });
    await app.getByRole('button', { name: 'Configure clients', exact: true }).click();
    const download = page.waitForEvent('download');
    await app.getByRole('button', { name: 'Download Ansible kit (.zip)', exact: true }).click();
    const zip = await readFile(await (await download).path());
    for (const name of ['configure-repos.yml', 'security-updates.yml', 'inventory.ini', 'README-client-automation.txt'])
      if (!zip.includes(Buffer.from(name))) throw new Error(`Ansible kit lacks ${name}`);
    if (!zip.includes(Buffer.from(job.snapshot_id))) throw new Error('Ansible kit is not pinned to the published snapshot');
    await expect(app.getByLabel('Client setup commands')).toContainText(job.result.config_url);
    await page.screenshot({ path: '.local/live-clients.png', fullPage: true });
    console.log(JSON.stringify({ publish: job.id, snapshot: job.snapshot_id, steps_seen: steps, config_url_checked: true, ansible_kit_bytes: zip.length }));
  }
  if (errors.length) throw new Error('Extension errors: ' + errors.join('\n'));
  console.log(`Live ${initial.role} checks passed`);
} finally {
  await browser.close();
}

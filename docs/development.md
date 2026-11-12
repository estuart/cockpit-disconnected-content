# Development and building RPMs

The tested build path uses Node.js 22 and Python 3.12, with RPM builds on RHEL 10.
Runtime hosts use the packaged frontend and do not need Node.js or npm.

## Build and test

The frontend is compiled on a build machine. Runtime hosts do not need npm.

On the RHEL build host, install `rpm-build`, `make`, and
`systemd-rpm-macros` before `make rpm`. Node.js is needed where the frontend is
built; the portable source archive includes its compiled assets.

```sh
npm ci
npm run typecheck
npm run build
python3 -m unittest discover -s tests -v
```

On a RHEL build machine with RPM build tools, GNU make, and GNU tar:

```sh
make rpm
sudo systemctl enable --now cockpit.socket
```

The RPM contains compiled frontend assets. `make dist` creates a portable source
archive using Python; the target build host needs RPM build tools and GNU make.

## Test the UI and generated automation

After `npm run build`:

```sh
npx playwright install chromium
node scripts/dashboard-smoke.mjs
node scripts/signing-key-smoke.mjs
python3 scripts/check-playbooks.py
```

The dashboard smoke test renders every backend fixture state, checks the next
action and its helper call, and checks narrow layouts. The signing-key smoke
test drives inline key approval during import, including cancel without trust.

The playbook checker requires Ansible Core on the development host. CI uses
Ansible Core 2.19; generated playbooks have also been exercised with Core 2.16
on RHEL. Browser scripts use Chrome locally and Playwright Chromium in CI. On a development
host without Chrome, use `CI=true node scripts/dashboard-smoke.mjs` (and the same
prefix for the signing-key and documentation screenshot scripts) to use the
Chromium installed by Playwright.

## Reproduce documentation screenshots

```sh
npm run build
node scripts/capture-doc-screenshots.mjs
```

This captures eight views of the real frontend with generic backend-derived test
fixtures, UTC timestamps, and a fixed demo clock. It writes `docs/screenshots/`.
It does not log into hosts, contact repository servers, or capture live inventories.
Review the images before committing. The example fingerprint is a placeholder,
not a key to trust or use with RPM.

## Development and publication hygiene

Keep hostnames, addresses, SSH aliases, credentials, entitlement files, mirror
content, screenshots of real inventories, local logs, and deployment notes out
of Git. `.local/` is ignored for machine-specific working notes. Use generic
examples in issues and pull requests. Review staged content before every commit.

The project is MIT licensed (see `LICENSE`). Generated frontend bundles preserve
dependency license notices.

## Publish a release

Releases are built by `.github/workflows/release.yml`, not on a workstation.

1. Set the new version in all five places: `package.json`, `Makefile`,
   `backend/disconnected_content.py`, `scripts/release.py`, and the spec's
   `Version`. Add a `%changelog` entry to the spec. The first entry becomes the
   release notes.
2. Merge the change to `main`.
3. Tag the merge commit and push the tag:

   ```sh
   git tag v0.12.0 origin/main
   git push origin v0.12.0
   ```

The workflow fails if the tag and the five version strings disagree. It builds the
RPMs on CentOS Stream 10 and installs them in a clean container: the main package
first (checking that it doesn't pull in Apache), then the publisher. It runs
`disconnected-content status`, then publishes the release with both RPMs,
unversioned copies for the `releases/latest/download/` URL, the source archive,
and `SHA256SUMS`. Pull requests that change packaging run the same build and
install test without publishing.

See [architecture](architecture.md) and [prototype limitations](limitations.md).

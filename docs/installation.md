# Installation and role configuration

The current tested target is RHEL 10.2 x86_64. Other RHEL versions and architectures
have not been established by the current validation. Use matching RHEL major
version and architecture for a connected/disconnected content pair and its clients.

## Packages and modes

| Package | Contents | Install on |
| --- | --- | --- |
| `cockpit-disconnected-content` | Both UI modes, Python engine, systemd worker unit, and root-owned configuration | Connected mirror and disconnected import hosts |
| `cockpit-disconnected-content-publisher` | Apache configuration, public content directories, and dependencies on Apache/SELinux tools | Disconnected hosts that will serve repositories |

The main package defaults to `"role": "connected"`. The publisher subpackage does
not switch roles. It requires the same version and release of the main package.
There is no role-selecting install script in this version. DNF installs packages;
`/etc/disconnected-content/config.json` selects the mode. Backend commands enforce
that role; hiding UI tabs is not the access-control mechanism.

## Obtain or build artifacts

Prebuilt GitHub releases are not currently published. Build with
[the development guide](development.md) or obtain RPMs from an approved build
process. The examples below use version 0.11.0, release 1 for RHEL 10. Adjust the
filenames to the exact artifacts you built. The two packages must match.

Install the main package explicitly. Avoid a wildcard such as
`cockpit-disconnected-content-*.rpm`: it also matches the publisher and can install
Apache on a host that only needs to mirror content.

## Connected host

Use a registered RHEL host with authorized access to the repositories to mirror.
DNF must already have repository access and the necessary trusted package-signing
keys. The application does not register the system or obtain subscription rights.

```sh
sudo dnf install ./cockpit-disconnected-content-0.11.0-1.el10.noarch.rpm
sudoedit /etc/disconnected-content/config.json
```

For a new host, the configuration can be:

```json
{
  "role": "connected",
  "state_dir": "/var/lib/disconnected-content",
  "reserve_gib": 5
}
```

Keep any existing custom settings when editing an already configured host.
Then check ownership and enable the existing web console:

```sh
sudo chown root:root /etc/disconnected-content/config.json
sudo chmod 0644 /etc/disconnected-content/config.json
sudo systemctl enable --now cockpit.socket
sudo /usr/libexec/disconnected-content status
```

Open `https://connected.example.org:9090`, sign in with a normal system account
that can obtain administrative access, and select **Disconnected Content**. The
extension does not create a separate username or password. Access to the web
console and TCP port 9090 follows your existing certificate and firewall policy.

Start with a small configured repository and conservative snapshot budget.

## Disconnected import host

Install the main package and all dependencies before isolation, or from an
approved offline bootstrap repository. The transferred content bundles are not an
installer for Cockpit, this extension, or its runtime dependencies.

For import without HTTP publication:

```sh
sudo dnf install ./cockpit-disconnected-content-0.11.0-1.el10.noarch.rpm
```

For import and HTTP publication:

```sh
sudo dnf install \
  ./cockpit-disconnected-content-0.11.0-1.el10.noarch.rpm \
  ./cockpit-disconnected-content-publisher-0.11.0-1.el10.noarch.rpm
```

Edit `/etc/disconnected-content/config.json` before the first operation:

```json
{
  "role": "disconnected",
  "state_dir": "/var/lib/disconnected-content",
  "reserve_gib": 5,
  "max_import_gib": 200,
  "allow_unsigned_imports": false
}
```

`allow_unsigned_imports` refers to **unsigned transfer manifests**, not unsigned
RPM packages. Imports are disabled by default in this prototype. For an explicitly
approved prototype evaluation, set this field to `true`. RPM signatures are still
mandatory; this setting never bypasses package signature validation.

Apply the same configuration ownership, permissions, and `cockpit.socket` setup
shown for the connected host. Open `https://disconnected.example.org:9090`.
Confirm the UI shows **Disconnected host** and the status output reports the
expected role and storage directory before starting work.

Choose the transfer directory in **Settings → Transfer folder → Browse…**; picking
a folder saves it. Mounting media and copying files are separate administrator
operations. **Review & import** asks you to approve any bundled key that isn't
already approved or trusted by this host before the import starts.

### Offline dependencies

The main RPM declares dependencies including Cockpit, Python, DNF plugins, RPM,
GnuPG, zstd, and systemd. The publisher adds Apache and SELinux labeling tools.
Provision the complete dependency closure for the target RHEL release and
architecture. An already installed package on a staging host does not establish
that a fresh offline host has it.

Preferred evaluation path: install dependencies while the future disconnected
host still has approved repository access, then apply isolation. For an already
isolated host, build or use an approved offline bootstrap repository with the
matching dependency RPMs. Do not disable package-signature checks to compensate
for missing bootstrap dependencies or keys. No automated bootstrap-media builder
is included yet.

Runtime hosts do not need Node.js or npm. Ansible is not required on the publisher
for generating downloads; it is required on the separate control node when the
operator runs those downloads.

## Storage and publication

Choose storage before the first job. The state directory must be owned by root
and inaccessible to other users; let the engine create it with mode `0700`, or
precreate it with that ownership and mode. Do not change `state_dir` expecting an
automatic migration of existing snapshots, job history, approvals, or exports.

Allow capacity for:

- Connected snapshots, a complete export archive, and DNF metadata caches.
- Disconnected transfer files, import staging, managed snapshots, and the reserve.
- A separate public repository copy under `/var/www/disconnected-content` when
  publishing. The public copy may be on a different filesystem from private state.

The budget and reserve checks are estimates and guardrails, not quotas. Keep
headroom for concurrent workloads and retained older snapshots.

To publish, install the optional publisher, save the client-facing address when the
overview asks (or in **Settings**), and explicitly publish a validated snapshot. The application
checks Apache configuration, applies web-content SELinux labels, and starts Apache
when publishing. It does not open firewall ports or change network isolation.
Allow inbound HTTP from approved client networks through your existing policy.
The supplied publisher configuration uses HTTP on port 80; HTTPS needs separately
configured certificates and a TLS listener. Entering an HTTPS URL does not set up TLS.

## Upgrades and changing roles

Upgrade the main package on both kinds of host. On a publisher, upgrade both
packages together to matching version/release. The root configuration uses RPM
`%config(noreplace)` handling; review any `.rpmnew` file and preserve custom settings.
Package upgrades do not migrate content directories or adopt newly published
content on client hosts.

Role changes are manual in this version. Stop or finish active work first and
plan the change around existing state. Changing a string does not move content,
undo a publication, enable subscriptions, or create a network air gap. The
recommended deployment uses separate hosts with roles selected before first use.

See [the packaging decision](deployment-design.md) for the proposed setup command
and future safeguards. See [operator procedures](operator-guide.md) for the
complete sync, key review, import, publication, and client workflow.

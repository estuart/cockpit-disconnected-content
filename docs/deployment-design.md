# Packaging and role selection

## Current implementation

One repository builds one source archive and two binary RPMs:

- `cockpit-disconnected-content` contains the shared frontend, backend, worker
  unit, and configuration. It implements connected and disconnected behavior.
- `cockpit-disconnected-content-publisher` adds Apache publication configuration,
  public directories, and the server dependencies. It depends on the exact same
  version/release of the main package.

The root-owned JSON configuration selects `connected` or `disconnected` at
runtime. Backend operations check the role. A fresh installation has no role. The
publisher does not change the role or start serving content until an
administrator publishes a snapshot. Both modes and the publisher spec are tracked
together in this repository.

## Setup

The role is chosen with the helper's `setup` command. The web console calls it
from the first-run screen, and administrators can run it from a shell:

```text
/usr/libexec/disconnected-content setup connected
/usr/libexec/disconnected-content setup disconnected
```

The web console also uses `setup` to turn the disconnected prototype import
policy (`allow_unsigned_imports`) on or off for a host that already has the
disconnected role. Setup:

- Requires an explicit role. It never guesses, though status reports a
  `setup_hint` when a host without a role already holds content, so the first-run
  screen can pre-select it.
- Refuses to switch an already configured host to the other role. Changing roles
  with existing content needs a deliberate migration path.
- Refuses to run during active work, after clearing jobs a crash left behind.
- Re-reads the configuration under the engine lock, preserves every other key,
  saves a timestamped `config.json.bak-*` copy, writes atomically, and keeps the
  file root-owned with mode `0644`.
- Never turns on unsigned manifests unless asked to, approves signing keys,
  changes firewall policy, weakens network isolation, or moves state. Role
  selection is not a replacement for the network's air-gap controls.

Setup doesn't change `state_dir`. Choose storage in the configuration file before
setting up a host. `make install` is the build system's installation target; it is
also used when assembling RPMs and is not part of setup.

## Recommended direction

Retain **one application RPM, explicit role configuration, and an optional
publisher subpackage**. This keeps one tested implementation of archive checks,
job evidence, key review, and client configuration. It avoids conflicting files
or duplicate builds when connected and disconnected behavior share most code.
Apache remains optional for hosts that only mirror or import content.

The source-of-truth role remains the root-owned configuration, so Cockpit, CLI
operations, and systemd workers agree. Setup does not install packages: the
publisher is installed with DNF, and the first-run screen only reports whether it
is present. Installing it must keep working from local or offline repositories
without assuming the importer can reach the internet.

If dependency size later becomes material, split connected download tooling from
the shared engine into an optional subpackage while keeping a single codebase.
The current main package includes DNF tooling on both sides; no dependency split
is being claimed here. Small role metapackages could eventually simplify fleet
installation, but separate application builds are not needed for this prototype.

Disconnected hosts can be installed from an offline installer that the connected
host builds. It contains this version's RPMs and the dependency closure for a
fresh host of the same RHEL release and architecture, with repository metadata,
`SHA256SUMS`, and an `install.sh` that installs only missing packages with DNF
signature checks. See [the installation guide](installation.md#offline-installer-recommended).

Metapackages, a `state_dir` migration, and role migration are proposals. This
documentation does not introduce them.

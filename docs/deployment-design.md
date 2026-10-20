# Packaging and role selection

## Current implementation

One repository builds one source archive and two binary RPMs:

- `cockpit-disconnected-content` contains the shared frontend, backend, worker
  unit, and configuration. It implements connected and disconnected behavior.
- `cockpit-disconnected-content-publisher` adds Apache publication configuration,
  public directories, and the server dependencies. It depends on the exact same
  version/release of the main package.

The root-owned JSON configuration selects `connected` or `disconnected` at
runtime. Backend operations check the role. A fresh installation defaults to
connected mode. The publisher does not change the role or start serving content
until an administrator publishes a snapshot. Both modes and the publisher spec
are tracked together in this repository.

There is no `install.sh --role ...` or setup CLI today. Follow the
[installation guide](installation.md) for the implemented package/configuration
steps. `make install` is the build system's installation target; it is also used
when assembling RPMs and is not a customer role-selection wizard.

## Recommended direction

Retain **one application RPM, explicit role configuration, and an optional
publisher subpackage**. This keeps one tested implementation of archive checks,
job evidence, key review, and client configuration. It avoids conflicting files
or duplicate builds when connected and disconnected behavior share most code.
Apache remains optional for hosts that only mirror or import content.

Add a small setup command around standard DNF installation and root-owned
configuration. A proposed interface is:

```text
# Proposed interface only; these commands do not exist in version 0.10.0.
disconnected-content-setup --role connected --state-dir /srv/disconnected-content
disconnected-content-setup --role disconnected --with-publisher --state-dir /srv/disconnected-content
```

The role should be an explicit required choice. The publisher option should check
that the matching package is present, or clearly present the DNF transaction it
would require. It must support local/offline repositories and must not assume
that an importer can reach the internet. The source-of-truth role remains the
root-owned configuration so Cockpit, CLI operations, and systemd workers agree.

Setup should show a reviewable plan, preserve existing settings, write atomically,
back up the configuration, and refuse a role change during active work. Changing
roles with existing content needs a deliberate migration path. It should not
silently opt into unsigned manifests, approve signing keys, change firewall
policy, weaken network isolation, or overwrite state. Role selection is not a
replacement for the network's air-gap controls.

If dependency size later becomes material, split connected download tooling from
the shared engine into an optional subpackage while keeping a single codebase.
The current main package includes DNF tooling on both sides; no dependency split
is being claimed here. Small role metapackages could eventually simplify fleet
installation, but separate application builds are not needed for this prototype.

The setup command, metapackages, bootstrap-media builder, and role migration are
proposals. This documentation does not introduce them or alter installed host roles.

Name: cockpit-disconnected-content
Version: 0.2.0
Release: 1%{?dist}
Summary: Disconnected RPM content workflows in the RHEL web console
License: LicenseRef-Proprietary
URL: https://github.com/estuart/cockpit-disconnected-content
Source0: %{name}-%{version}.tar.gz
BuildArch: noarch
BuildRequires: make
BuildRequires: systemd-rpm-macros
Requires: cockpit-system >= 300
Requires: cockpit-bridge >= 300
Requires: python3 >= 3.9
Requires: dnf-plugins-core
Requires: rpm
Requires: zstd
Requires: systemd

%description
A prototype for syncing, validating, exporting, and importing RPM repository
snapshots through the Cockpit web console. HTTP publication is a planned capability.

%prep
%autosetup

%build
# The release source archive includes compiled frontend assets.

%install
%make_install

%post
%systemd_post disconnected-content-job@.service

%preun
%systemd_preun disconnected-content-job@.service

%postun
%systemd_postun disconnected-content-job@.service

%files
%doc README.md docs/
%config(noreplace) /etc/disconnected-content/config.json
%{_libexecdir}/disconnected-content
%{_datadir}/cockpit/disconnected-content/
%{_unitdir}/disconnected-content-job@.service

%changelog
* Wed Oct 14 2026 Project contributors - 0.2.0-1
- Add validated disconnected imports and a transfer inbox
* Wed Oct 14 2026 Project contributors - 0.1.0-1
- Initial connected-side prototype

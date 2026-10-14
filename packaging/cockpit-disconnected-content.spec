Name: cockpit-disconnected-content
Version: 0.5.0
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
A prototype for syncing, validating, exporting, importing, and publishing RPM
repository snapshots through the Cockpit web console.

%package publisher
Summary: Apache static publication for validated disconnected repository snapshots
Requires: %{name} = %{version}-%{release}
Requires: httpd
Requires: policycoreutils

%description publisher
Optional Apache configuration and a dedicated public content tree. Install this
subpackage on disconnected publishing hosts. It does not change firewall policy
or enable the server until an administrator publishes a snapshot through Cockpit.

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

%files publisher
%config(noreplace) /etc/httpd/conf.d/disconnected-content.conf
%dir /var/www/disconnected-content
%dir /var/www/disconnected-content/public
%dir /var/www/disconnected-content/public/snapshots
%dir %attr(0700,root,root) /var/www/disconnected-content/.staging

%changelog
* Wed Oct 14 2026 Project contributors - 0.5.0-1
- Add Apache publication and generated client repository configuration
* Wed Oct 14 2026 Project contributors - 0.4.0-1
- Add a host directory picker with mounted-filesystem shortcuts
* Wed Oct 14 2026 Project contributors - 0.3.0-1
- Add configurable import sources and timestamped job evidence
* Wed Oct 14 2026 Project contributors - 0.2.0-1
- Add validated disconnected imports and a transfer inbox
* Wed Oct 14 2026 Project contributors - 0.1.0-1
- Initial connected-side prototype

VERSION := 0.8.0
PREFIX ?= /usr
DESTDIR ?=

.PHONY: build check install dist rpm
build:
	npm run build
check:
	npm run typecheck
	python3 -m unittest discover -s tests -v
install:
	install -d $(DESTDIR)$(PREFIX)/share/cockpit/disconnected-content $(DESTDIR)$(PREFIX)/libexec
	cp -r dist/. $(DESTDIR)$(PREFIX)/share/cockpit/disconnected-content/
	install -m 0755 backend/disconnected_content.py $(DESTDIR)$(PREFIX)/libexec/disconnected-content
	install -Dm0644 packaging/config.json $(DESTDIR)/etc/disconnected-content/config.json
	install -Dm0644 packaging/disconnected-content-job@.service $(DESTDIR)$(PREFIX)/lib/systemd/system/disconnected-content-job@.service
	install -Dm0644 packaging/disconnected-content.conf $(DESTDIR)/etc/httpd/conf.d/disconnected-content.conf
	install -d -m 0755 $(DESTDIR)/var/www/disconnected-content/public/snapshots
	install -d -m 0700 $(DESTDIR)/var/www/disconnected-content/.staging
dist: build
	python3 scripts/release.py
rpm: dist
	mkdir -p build/rpmbuild/BUILD build/rpmbuild/BUILDROOT build/rpmbuild/RPMS build/rpmbuild/SOURCES build/rpmbuild/SPECS build/rpmbuild/SRPMS
	rpmbuild -tb build/cockpit-disconnected-content-$(VERSION).tar.gz --define '_topdir $(CURDIR)/build/rpmbuild'

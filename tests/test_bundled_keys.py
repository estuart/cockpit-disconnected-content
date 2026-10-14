"""Public-only exports, hostile manifests, and explicit trust for bundled keys."""
import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from test_engine import engine, fixture


def armor(packets=b'\xc6\x01\x04\xcd\x07Example\xc2\x01\x04'):
    return '-----BEGIN PGP PUBLIC KEY BLOCK-----\n\n' + base64.b64encode(packets).decode() + '\n-----END PGP PUBLIC KEY BLOCK-----\n'


def inspected_key():
    return {'fingerprint': 'A' * 40, 'fingerprints': ['A' * 40], 'user_ids': ['Example'],
            'expires': None, 'public_key': armor(), 'sha256': hashlib.sha256(armor().encode()).hexdigest()}


class PublicKeySources(unittest.TestCase):
    def test_splits_public_keys_without_exporting_surrounding_configuration(self):
        text = '# example source comment\n' + armor() + '# not part of certificate\n' + armor(b'\xc6\x01\x06')
        self.assertEqual(engine.public_key_blocks(text), [armor(), armor(b'\xc6\x01\x06')])

    def test_secret_packets_disguised_as_public_armor_are_rejected(self):
        for packets in [b'\xc5\x01\x04', b'\xc6\x01\x04\xc7\x01\x04', b'\xc6\x01\x04\xc8\x01\x04',
                        b'\xc6\xff\x00', b'\xc6\xe0x', b'\xc6\x01\x04\xc6\x01\x04', b'']:
            with self.subTest(packets=packets), self.assertRaises(ValueError):
                engine.public_key_blocks(armor(packets))
        with self.assertRaises(ValueError):
            engine.public_key_blocks(armor() + '\n-----BEGIN PGP PRIVATE KEY BLOCK-----')

    def test_file_sources_are_bounded_and_only_public_certificates_are_returned(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'vendor.asc'
            path.write_text('# source-only comment\n' + armor())
            self.assertEqual(engine.read_public_key_source(path.as_uri()), [armor()])
            path.write_bytes(b'x' * (1024 * 1024 + 1))
            with self.assertRaises(ValueError):
                engine.read_public_key_source(path.as_uri())
        for source in ['http://mirror.example.org/key.asc', 'https://user:password@mirror.example.org/key.asc', 'file://other/etc/key.asc']:
            with self.subTest(source=source), self.assertRaises(ValueError):
                engine.read_public_key_source(source)


class BundledKeys(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = engine.Engine({'role': 'connected', 'state_dir': str(root / 'source'), 'reserve_gib': 0})
        self.target = engine.Engine({'role': 'disconnected', 'state_dir': str(root / 'target'),
                                   'reserve_gib': 0, 'max_import_gib': 1, 'allow_unsigned_imports': True})
        self.sid, self.rid = 'a' * 32, 'vendor-example'
        self.summary = {'packages': 1, 'package_bytes': 48, 'metadata_types': ['primary'], 'rpm_signatures_verified': True}
        snapshot = self.source.root / 'snapshots' / self.sid
        fixture(snapshot / 'repositories' / self.rid)
        engine.write_json(snapshot / 'snapshot.json', {'id': self.sid, 'status': 'validated',
            'os': {'major': '10', 'arch': 'x86_64'}, 'repositories': {self.rid: self.summary}})
        self.keyfile = root / 'source-key.asc'
        self.keyfile.write_text(armor())
        self.path = self.target.root / 'incoming' / (self.sid + '.tar')
        self.export()

    def export(self):
        with patch.object(engine, 'configured_signing_key_sources', return_value={self.rid: [self.keyfile.as_uri()]}), \
                patch.object(engine, 'validate_repository', return_value=self.summary):
            self.source.export({'id': 'b' * 32, 'snapshot_id': self.sid})
        shutil.copyfile(self.source.root / 'exports' / self.path.name, self.path)
        self.sidecar()

    def sidecar(self):
        self.path.with_suffix('.tar.sha256').write_text(f'{engine.digest(self.path)}  {self.path.name}\n')

    def rewrite(self, mutate):
        with tarfile.open(self.path) as archive:
            entries = [(member.name, archive.extractfile(member).read()) for member in archive]
        manifest = json.loads(entries[0][1])
        mutate(manifest)
        entries[0] = ('manifest.json', json.dumps(manifest).encode())
        with tarfile.open(self.path, 'w') as archive:
            for name, raw in entries:
                info = tarfile.TarInfo(name); info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
        self.sidecar()

    def test_export_bundles_only_public_key_content_and_hashes(self):
        with tarfile.open(self.path) as archive:
            manifest, _ = engine.bundle_manifest(archive, engine.GIB, 100)
            entry = manifest['signing_keys'][self.rid][0]
            self.assertEqual(entry['sha256'], inspected_key()['sha256'])
            self.assertNotIn(str(self.keyfile), json.dumps(manifest))
            self.assertEqual(archive.extractfile(entry['path']).read(), armor().encode())
        self.assertEqual(engine.verify_archive(self.path)['bundled_signing_keys'], 1)

    def test_scan_and_review_never_grant_trust_then_approval_allows_import(self):
        scan = self.target.scan_bundle_keys({'bundle_name': self.path.name})
        self.assertEqual(scan['keys'], 1)
        self.assertFalse(scan['trusted'])
        self.assertFalse(list((self.target.root / 'trusted-keys').iterdir()))
        candidate = engine.read_json(next((self.target.root / 'bundle-key-candidates').glob('*.json')))
        self.assertEqual(candidate['bundle_sha256'], engine.digest(self.path))
        def validate(path, **options):
            if not options.get('signing_keys'):
                raise engine.SignatureError('missing_key', 'Missing key', 'NOKEY')
            return dict(self.summary, signing_keys=options['signing_keys'])
        job = {'id': 'c' * 32, 'bundle_name': self.path.name, 'status': 'failed'}
        with patch.object(engine, 'host_os', return_value={'major': '10', 'arch': 'x86_64'}), \
                patch.object(engine, 'validate_repository', side_effect=validate):
            with self.assertRaises(engine.SignatureError):
                self.target.import_bundle(job)
            with patch.object(engine, 'inspect_public_key', return_value=inspected_key()), \
                    patch.object(engine.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')):
                review = self.target.review_signing_key({'bundle_key': candidate['candidate_id']})
                self.assertIn(candidate['bundle_sha256'], review['source'])
                self.assertFalse(self.target.trusted_keys(self.rid))
                self.target.trust_signing_key({'review_id': review['review_id'], 'fingerprint': review['fingerprint'], 'confirmed': True})
            self.target.import_bundle(job)
        snapshot = engine.read_json(self.target.root / 'snapshots' / self.sid / 'snapshot.json')
        self.assertEqual(snapshot['repositories'][self.rid]['signing_keys'][0]['sha256'], inspected_key()['sha256'])

    def test_key_and_archive_tampering_leave_no_candidates_or_trust(self):
        # Tamper a package byte while preserving the same byte count and updating the outer sidecar.
        raw = self.path.read_bytes().replace(b'A repository fixture', b'X repository fixture')
        self.path.write_bytes(raw); self.sidecar()
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.target.scan_bundle_keys({'bundle_name': self.path.name})
        self.assertFalse(list((self.target.root / 'bundle-key-candidates').iterdir()))
        self.assertFalse(self.target.trusted_keys(self.rid))

    def test_key_byte_tampering_is_rejected_before_staging(self):
        original = armor().encode()
        # Change an actual base64 byte without changing the certificate file length.
        changed = original.replace(base64.b64encode(b'\xc6\x01\x04\xcd\x07Example\xc2\x01\x04'),
                                   base64.b64encode(b'\xc6\x01\x04\xcd\x07Changed\xc2\x01\x04'))
        self.path.write_bytes(self.path.read_bytes().replace(original, changed))
        self.sidecar()
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.target.scan_bundle_keys({'bundle_name': self.path.name})
        self.assertFalse(list((self.target.root / 'bundle-key-candidates').iterdir()))

    def test_bad_configured_key_does_not_replace_previous_export(self):
        before = engine.digest(self.source.root / 'exports' / self.path.name)
        self.keyfile.write_text('-----BEGIN PGP PRIVATE KEY BLOCK-----')
        with self.assertRaisesRegex(ValueError, 'Cannot collect public signing keys'):
            self.export()
        self.assertEqual(engine.digest(self.source.root / 'exports' / self.path.name), before)

    def test_hostile_key_manifest_fields_are_rejected(self):
        for mutation in [lambda m: m['signing_keys'][self.rid][0].update(trusted=True),
                         lambda m: m['signing_keys'][self.rid][0].update(path='../outside'),
                         lambda m: m['signing_keys'].update({'undeclared-repo': m['signing_keys'][self.rid]}),
                         lambda m: m['signing_keys'][self.rid].append(m['signing_keys'][self.rid][0])]:
            self.export(); self.rewrite(mutation)
            with self.assertRaises(ValueError):
                self.target.scan_bundle_keys({'bundle_name': self.path.name})
        self.assertFalse(list((self.target.root / 'bundle-key-candidates').iterdir()))

    def test_legacy_bundle_without_keys_stays_usable(self):
        self.rewrite(lambda m: m.pop('signing_keys'))
        self.assertEqual(self.target.scan_bundle_keys({'bundle_name': self.path.name})['keys'], 0)
        self.assertFalse(self.target.trusted_keys(self.rid))

    def test_wrong_role_and_active_job_block_inspection(self):
        self.target.config['role'] = 'connected'
        with self.assertRaises(ValueError):
            self.target.scan_bundle_keys({'bundle_name': self.path.name})
        self.target.config['role'] = 'disconnected'
        engine.write_json(self.target.job_path('d' * 32), {'status': 'running'})
        with self.assertRaisesRegex(ValueError, 'active job'):
            self.target.scan_bundle_keys({'bundle_name': self.path.name})


if __name__ == '__main__':
    unittest.main()

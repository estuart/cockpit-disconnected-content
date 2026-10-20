"""Signing-key review, scoped trust, pinned publication, and client enrollment."""
from copy import deepcopy
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_engine import engine, fixture
from test_automation import publication
import test_publish as publication_tests


ARMOR = '-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nexample\n-----END PGP PUBLIC KEY BLOCK-----\n'
FPR = 'A' * 40


def key():
    return {'fingerprint': FPR, 'fingerprints': [FPR], 'public_key': ARMOR,
            'sha256': hashlib.sha256(ARMOR.encode()).hexdigest(), 'user_ids': ['Example vendor'], 'expires': None}


def listing(validity='-', expires='', extra=''):
    return ':'.join(['pub', validity, '4096', '1', FPR[-16:], '1', expires, '', '', '-', '', 'scSC', '']) + f'\nfpr:::::::::{FPR}:\nuid:-::::::::Example vendor:\n' + extra


class KeyInspection(unittest.TestCase):
    def test_inspects_full_fingerprint_offline_in_isolated_home(self):
        with patch.object(engine.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, listing(), '')) as run:
            result = engine.inspect_public_key(ARMOR)
        self.assertEqual(result['fingerprint'], FPR)
        self.assertEqual(result['sha256'], key()['sha256'])
        self.assertIn('--no-options', run.call_args.args[0])
        self.assertIn('--no-auto-key-retrieve', run.call_args.args[0])
        self.assertIn('--homedir', run.call_args.args[0])

    def test_rejects_revoked_expired_multiple_and_malformed_keys(self):
        for output in [listing('r'), listing('e'), listing(expires='2'), listing(extra=listing()), 'invalid']:
            with self.subTest(output=output), patch.object(engine.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output, '')):
                with self.assertRaises(ValueError):
                    engine.inspect_public_key(ARMOR)
        for value in ['PRIVATE KEY', ARMOR * 2, ARMOR + 'extra', ARMOR.replace('example', 'x' * 70000), ARMOR.replace('example', '{{lookup}}')]:
            with self.subTest(value=value[:40]), self.assertRaises(ValueError):
                engine.inspect_public_key(value)

    def test_repository_key_substitution_is_rejected(self):
        changed = dict(key(), public_key=ARMOR.replace('example', 'different'))
        with self.assertRaisesRegex(ValueError, 'reviewed content'):
            engine.repository_keys({'signing_keys': [changed]})


class KeyApproval(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = engine.Engine({'state_dir': self.temp.name, 'role': 'disconnected'})
        inspection = patch.object(engine, 'inspect_public_key', return_value=key())
        inspection.start()
        self.addCleanup(inspection.stop)
        rpm = patch.object(engine.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', ''))
        self.rpm = rpm.start()
        self.addCleanup(rpm.stop)

    def review(self):
        return self.app.review_signing_key({'repository_id': 'vendor-example', 'public_key': ARMOR})

    def approve(self, review, **changes):
        return self.app.trust_signing_key(dict(review_id=review['review_id'], fingerprint=FPR, confirmed=True, **changes))

    def test_review_does_not_trust_and_confirmation_is_bound_to_review(self):
        review = self.review()
        self.assertEqual(self.app.trusted_keys('vendor-example'), [])
        for data in [{'confirmed': False}, {'fingerprint': 'B' * 40}]:
            request = dict(review_id=review['review_id'], fingerprint=FPR, confirmed=True)
            request.update(data)
            with self.assertRaises(ValueError):
                self.app.trust_signing_key(request)
        self.approve(review)
        self.assertEqual(self.app.trusted_keys('vendor-example')[0]['fingerprint'], FPR)
        self.assertEqual(self.app.trusted_keys('different-repo'), [])
        calls = [call.args[0] for call in self.rpm.call_args_list]
        self.assertTrue(all('--dbpath' in args for args in calls))
        with self.assertRaisesRegex(ValueError, 'already approved'):
            self.approve(review)

    def test_expired_reviews_active_jobs_and_wrong_roles_are_rejected(self):
        review = self.review()
        path = self.app.root / 'key-reviews' / (review['review_id'] + '.json')
        engine.write_json(path, dict(review, reviewed_at='2000-01-01T00:00:00+00:00'))
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.approve(review)
        review = self.review()
        engine.write_json(self.app.job_path('c' * 32), {'status': 'running'})
        with self.assertRaisesRegex(ValueError, 'active job'):
            self.approve(review)
        self.app.config['role'] = 'connected'
        with self.assertRaisesRegex(ValueError, 'disconnected'):
            self.review()
        with self.assertRaisesRegex(ValueError, 'disconnected'):
            self.approve(review)

    def test_host_file_is_bounded_and_symlinks_are_rejected(self):
        incoming = self.app.root / 'incoming'
        (incoming / 'vendor.asc').write_text(ARMOR)
        (incoming / 'link.asc').symlink_to(incoming / 'vendor.asc')
        (incoming / 'huge.asc').write_bytes(b'x' * 70000)
        self.assertEqual(self.app.key_files(), ['vendor.asc'])
        self.assertEqual(self.app.review_signing_key({'repository_id': 'vendor', 'key_file': 'vendor.asc'})['source'], 'vendor.asc')
        for name in ['link.asc', 'huge.asc', '../vendor.asc']:
            with self.assertRaises(ValueError):
                self.app.review_signing_key({'repository_id': 'vendor', 'key_file': name})

    def test_scoped_validation_passes_only_repository_keys(self):
        self.approve(self.review())
        with patch.object(engine, 'validate_repository', return_value={}) as validate:
            self.app.validate_content(Path('/unused'), 'vendor-example')
            self.assertEqual(validate.call_args.kwargs['signing_keys'][0]['fingerprint'], FPR)
            self.app.validate_content(Path('/unused'), 'other')
            self.assertEqual(validate.call_args.kwargs['signing_keys'], [])


class Diagnostics(unittest.TestCase):
    def test_missing_invalid_unsigned_and_policy_errors_are_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture(Path(temp) / 'repo')
            for output, returncode, code in [('Header Signature, key ID abcdef12: NOKEY', 1, 'missing_key'),
                    ('Header Signature: BAD', 1, 'invalid_signature'),
                    ('Header Signature: NOTTRUSTED', 1, 'untrusted_key'),
                    ('test.rpm: digests OK', 0, 'unsigned_package')]:
                with self.subTest(code=code), patch.object(engine.subprocess, 'run', return_value=subprocess.CompletedProcess([], returncode, output, '')):
                    with self.assertRaises(engine.SignatureError) as error:
                        engine.validate_repository(Path(temp) / 'repo')
                    self.assertEqual(error.exception.details['code'], code)
                    self.assertIn(output, error.exception.details['diagnostics'])

    def test_job_retains_signature_details_in_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            app = engine.Engine({'state_dir': temp, 'role': 'disconnected'})
            jid = 'a' * 32
            engine.write_json(app.job_path(jid), {'id': jid, 'status': 'queued', 'operation': 'import'})
            error = engine.SignatureError('missing_key', 'Missing signing key', 'key ID abcdef12: NOKEY')
            error.details['repository_id'] = 'vendor'
            with patch.object(app, 'import_bundle', side_effect=error), self.assertRaises(engine.SignatureError):
                app.run(jid)
            job = engine.read_json(app.job_path(jid))
            self.assertEqual(job['signature_failure']['repository_id'], 'vendor')
            self.assertIn('abcdef12', app.logs(jid)['text'])


class KeyPublication(publication_tests.Publication):
    def setUp(self):
        super().setUp()
        self.snapshot['repositories'][self.rid]['signing_keys'] = [key()]
        engine.write_json(self.source / 'snapshot.json', self.snapshot)
        inspection = patch.object(engine, 'inspect_public_key', return_value=key())
        inspection.start()
        self.addCleanup(inspection.stop)
        def command(args, **kwargs):
            if '--checksig' in args:
                paths = args[args.index('--checksig') + 1:]
                return subprocess.CompletedProcess(args, 0, '\n'.join(f'{p}: digests signatures OK' for p in paths), '')
            return subprocess.CompletedProcess(args, 0, '', '')
        runner = patch.object(engine.subprocess, 'run', side_effect=command)
        runner.start()
        self.addCleanup(runner.stop)

    def test_published_key_is_pinned_and_client_config_uses_exact_local_key(self):
        # Later registry changes cannot alter an already imported snapshot's keys.
        engine.write_json(self.app.root / 'trusted-keys' / (self.rid + '.json'), {'signing_keys': []})
        self.app.publish(self.job)
        path = self.public_root / 'public/snapshots' / self.sid / 'keys' / (FPR + '.asc')
        self.assertEqual(path.read_text(), ARMOR)
        record = engine.read_json(self.app.root / 'publication.json')
        self.assertEqual(record['repositories'][self.rid]['signing_keys'][0]['sha256'], key()['sha256'])
        self.assertIn('gpgkey=file://' + engine.client_key_path(key()), record['client_config'])
        self.assertNotIn('RPM-GPG-KEY-redhat-release', record['client_config'])
        self.assertEqual(self.app.publication_availability(record)['state'], 'available')
        path.write_text('tampered')
        self.assertEqual(self.app.publication_availability(record)['state'], 'unavailable')
        with self.assertRaisesRegex(ValueError, 'public signing key'):
            self.app.publish(self.job)

    def test_generated_client_config_is_pinned_and_keeps_rpm_trust_checks(self):
        config = engine.client_repo_config('https://mirror.example.org', self.snapshot)
        self.assertIn('gpgcheck=1', config)
        self.assertIn('gpgkey=file://' + engine.client_key_path(key()), config)


class KeyAutomation(unittest.TestCase):
    def test_configuration_embeds_keys_and_checks_full_fingerprint(self):
        record = publication()
        record['repositories'] = {'vendor': {'metadata_types': ['primary'], 'signing_keys': [key()]}}
        record['client_config'] = engine.client_repo_config(record['base_url'], {'id': record['snapshot_id'], 'repositories': record['repositories']})
        generated = engine.client_automation(record)
        configure = generated['files']['configure-repos.yml']
        security = generated['files']['security-updates.yml']
        self.assertIn("Verify the publication's full signing-key fingerprint before enrollment", configure)
        self.assertIn('fpr:::::::::' + FPR + ':', configure)
        self.assertIn(FPR, configure)
        self.assertIn('BEGIN PGP PUBLIC KEY BLOCK', configure)
        self.assertIn('not ansible_check_mode', configure)
        self.assertIn(key()['sha256'], security)
        self.assertIn('"disable_gpg_check": false', security)
        self.assertNotIn('ansible.builtin.rpm_key', security)
        self.assertNotIn('RPM-GPG-KEY-redhat-release', configure)
        self.assertFalse(generated['has_updateinfo'])
        self.assertIn(FPR, generated['files']['README-client-automation.txt'])
        changed = deepcopy(record)
        changed['repositories']['vendor']['signing_keys'][0]['public_key'] += 'changed'
        with self.assertRaises(ValueError):
            engine.client_automation(changed)


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""Exercise version bumps and failure rollback in disposable Git repositories; never contacts GitHub."""
import importlib.util
import os
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ru_release', Path(__file__).with_name('release.py'))
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseSafetyTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='lumen-ru-release-test-')
        self.root = Path(self.directory.name)
        self.previous_cwd = Path.cwd()
        self.root_patch = patch.object(release, 'ROOT', self.root)
        self.root_patch.start()
        os.chdir(self.root)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Release tests')
        self.git('config', 'user.email', 'release-tests@example.invalid')
        (self.root / '.gitignore').write_text('.release.lock\nout/\n')
        self.write_version('1.0.0', 1)
        self.git('add', '.')
        self.git('commit', '-qm', 'Reviewed source changes')

    def tearDown(self):
        os.chdir(self.previous_cwd)
        self.root_patch.stop()
        self.directory.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', *args], text=True).strip()

    def write_version(self, name, code):
        (self.root / 'version.properties').write_text(f'versionName={name}\nversionCode={code}\n')

    def fake_build(self, sdk):
        version = release.verify_apk.properties(self.root / 'version.properties')
        out = self.root / 'out' / version['versionName']
        out.mkdir(parents=True, exist_ok=True)
        return out, {'versionName': version['versionName'], 'versionCode': int(version['versionCode'])}

    def execute(self, mode, builder=None):
        with patch.object(sys, 'argv', ['release.py', mode]), patch.object(release, 'environment', return_value=self.root), patch.object(release, 'check_identity'), patch.object(release, 'build', side_effect=builder or self.fake_build), patch.object(release, 'publish') as publish:
            status = release.main()
            return status, publish

    def test_patch_increments_code_and_version_once(self):
        self.git('tag', 'v1.0.0')
        # Account state is stubbed: v1.0.0 is already a stable published release.
        real_run = subprocess.run
        def checked_run(args, **kwargs):
            if args[:3] == ['gh', 'release', 'view']:
                return subprocess.CompletedProcess(args, 0, '{"isDraft":false}', '')
            return real_run(args, **kwargs)
        with patch.object(subprocess, 'run', side_effect=checked_run):
            status, publish = self.execute('patch')
        self.assertEqual(0, status)
        self.assertEqual({'versionName': '1.0.1', 'versionCode': '2'}, release.verify_apk.properties(self.root / 'version.properties'))
        self.assertEqual('Release Lumen RU 1.0.1', self.git('log', '-1', '--format=%s'))
        publish.assert_called_once()

    def test_minor_resets_patch_and_increments_code(self):
        self.write_version('1.3.7', 42)
        self.git('add', 'version.properties')
        self.git('commit', '-qm', 'Reviewed functional update')
        status, publish = self.execute('minor')
        self.assertEqual(0, status)
        self.assertEqual({'versionName': '1.4.0', 'versionCode': '43'}, release.verify_apk.properties(self.root / 'version.properties'))
        publish.assert_called_once()

    def test_failed_build_restores_version_without_commit_or_tag(self):
        before = self.git('rev-parse', 'HEAD')
        def failed_build(sdk):
            raise RuntimeError('Intentional build failure')
        with self.assertRaisesRegex(RuntimeError, 'Intentional build failure'):
            self.execute('patch', failed_build)
        self.assertEqual(before, self.git('rev-parse', 'HEAD'))
        self.assertEqual('', self.git('tag', '--list'))
        self.assertEqual('', self.git('status', '--porcelain'))
        self.assertEqual('1.0.0', release.verify_apk.properties(self.root / 'version.properties')['versionName'])

    def test_draft_assets_are_requested_by_release_id_and_uploaded_before_publication(self):
        out = self.root / 'out/1.0.0'
        out.mkdir(parents=True)
        apk = out / 'Lumen-RU-1.0.0-universal.apk'
        apk.write_bytes(b'dummy test artifact; never uploaded')
        (out / 'SHA256SUMS.txt').write_text('dummy checksum for local unit test')
        (out / 'BUILD.json').write_text('{}')
        report = {'versionName': '1.0.0', 'versionCode': 1, 'unitTests': 16,
                  'sha256': hashlib.sha256(apk.read_bytes()).hexdigest(),
                  'signingCertificateSha256': '0' * 64,
                  'upstream': {'version': '0.4.2', 'commit': '0' * 40}}
        real_run = release.run
        calls = []
        release_api = 'https://api.github.com/repos/' + release.REPO + '/releases/123'
        def local_or_fake(*args, capture=False, **kwargs):
            calls.append(args)
            if args[0] == 'gh':
                if args[1:3] == ('release', 'view') and args[-1] == 'apiUrl':
                    return json.dumps({'apiUrl': release_api})
                if args[1] == 'api':
                    self.assertEqual(release_api, args[2])
                    return '{"assets":[]}'
                return ''
            if args[:2] == ('git', 'push'):
                return ''
            return real_run(*args, capture=capture, **kwargs)
        draft = subprocess.CompletedProcess([], 0, '{"isDraft":true}', '')
        real_subprocess_run = subprocess.run
        def draft_or_local(args, **kwargs):
            if args[:3] == ['gh', 'release', 'view']:
                return draft
            return real_subprocess_run(args, **kwargs)
        with patch.object(release, 'run', side_effect=local_or_fake), patch.object(subprocess, 'run', side_effect=draft_or_local):
            # All Git mutations are inside this disposable repository; gh and push are mocked.
            release.publish(out, report)
        uploads = [i for i, command in enumerate(calls) if command[:3] == ('gh', 'release', 'upload')]
        edits = [i for i, command in enumerate(calls) if command[:3] == ('gh', 'release', 'edit')]
        self.assertEqual(3, len(uploads))
        self.assertEqual(1, len(edits))
        self.assertGreater(edits[0], max(uploads))

    def test_build_only_never_publishes_or_changes_version(self):
        before = self.git('rev-parse', 'HEAD')
        status, publish = self.execute('--build-only')
        self.assertEqual(0, status)
        publish.assert_not_called()
        self.assertEqual(before, self.git('rev-parse', 'HEAD'))
        self.assertEqual('', self.git('tag', '--list'))
        self.assertEqual('1.0.0', release.verify_apk.properties(self.root / 'version.properties')['versionName'])


if __name__ == '__main__':
    unittest.main()

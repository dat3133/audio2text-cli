"""Exercise publication decisions without network access or registry writes."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def publisher(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'publish-{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture():
    work = ROOT / '.test-data' / ('publish-' + uuid.uuid4().hex)
    (work / 'candidate').mkdir(parents=True)
    return work


class PublishTests(unittest.TestCase):
    def github_case(self, value, existing=None, corrupt=False):
        module = publisher('github')
        work = fixture()
        candidate = work / 'candidate'
        (candidate / 'payload').write_bytes(b'immutable binary')
        (candidate / 'SHA256SUMS').write_bytes(b'fixture manifest')
        commands = []
        verifications = []

        def run(args):
            args = list(map(str, args))
            commands.append(args)
            if args[1:3] == ['release', 'download']:
                target = Path(args[args.index('--dir') + 1])
                for source in candidate.iterdir():
                    shutil.copy2(source, target / source.name)
                if corrupt:
                    (target / 'payload').write_bytes(b'different binary')

        query = subprocess.CompletedProcess([], 1, '', 'HTTP 404') if existing is None else subprocess.CompletedProcess([], 0, json.dumps(existing), '')
        error = None
        with patch.multiple(module, ROOT=work, validate_version=Mock(return_value=value), expected_assets=Mock(return_value={'payload'}), verify_release=Mock(side_effect=lambda path: verifications.append(Path(path))), run=run), patch.object(module.subprocess, 'run', return_value=query):
            try:
                module.main(candidate)
            except SystemExit as exc:
                error = str(exc)
        return commands, verifications, error

    def test_github_draft_checks_then_channel_specific_publication(self):
        for value, preview in [('0.1.0', False), ('0.1.0-beta.6', True)]:
            with self.subTest(version=value):
                commands, checks, error = self.github_case(value)
                self.assertIsNone(error)
                create = next(c for c in commands if c[2] == 'create')
                edit = next(c for c in commands if c[2] == 'edit')
                for command in (create, edit):
                    self.assertIn(f'--prerelease={str(preview).lower()}', command)
                self.assertIn('--latest=false', create)
                self.assertIn(f'--latest={str(not preview).lower()}', edit)
                self.assertIn('--draft', create)
                self.assertIn('--verify-tag', create)
                self.assertIn('--draft=false', edit)
                self.assertEqual(commands[-2][2], 'download')
                self.assertEqual(commands[-1][2], 'edit')
                self.assertEqual(len(checks), 2)

    def test_github_existing_public_release_is_reused_without_mutation(self):
        for value, preview in [('0.1.0', False), ('0.1.0-beta.6', True)]:
            with self.subTest(version=value):
                release = {'draft': False, 'prerelease': preview, 'assets': [{'name': name} for name in ('payload', 'SHA256SUMS')]}
                commands, checks, error = self.github_case(value, release)
                self.assertIsNone(error)
                self.assertEqual([c[2] for c in commands], ['download'])

    def test_github_existing_content_or_channel_conflict_stops(self):
        release = {'draft': False, 'prerelease': False, 'assets': [{'name': name} for name in ('payload', 'SHA256SUMS')]}
        commands, checks, error = self.github_case('0.1.0', release, corrupt=True)
        self.assertIn('Existing release content differs', error)
        self.assertEqual([c[2] for c in commands], ['download'])
        release['prerelease'] = True
        commands, checks, error = self.github_case('0.1.0', release)
        self.assertIn('identity/asset set differs', error)
        self.assertEqual([c[2] for c in commands], ['download'])

    def npm_case(self, value, already_exists=False, bad_integrity=False, change_other=False):
        module = publisher('npm')
        work = fixture()
        archive = work / 'candidate' / f'audio2text-cli-{value}.tgz'
        archive.write_bytes(b'immutable npm tarball')
        integrity = 'sha512-' + base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
        before = {'latest': '0.0.9', 'beta': '0.1.0-beta.6'}
        channel = 'beta' if '-beta.' in value else 'latest'
        tags = {**before, channel: value}
        if change_other:
            tags['latest' if channel == 'beta' else 'beta'] = '9.9.9'
        published = {'dist': {'integrity': 'sha512-different' if bad_integrity else integrity}, 'dist-tags': tags}
        fetched = 0

        def query(args, **kwargs):
            nonlocal fetched
            if 'dist-tags' in args:
                return subprocess.CompletedProcess(args, 0, json.dumps(before), '')
            fetched += 1
            if fetched == 1 and not already_exists:
                return subprocess.CompletedProcess(args, 1, '', 'E404')
            return subprocess.CompletedProcess(args, 0, json.dumps(published), '')

        upload = Mock()
        error = None
        with patch.multiple(module, validate_version=Mock(return_value=value), verify_release=Mock(), run=upload), patch.object(module.subprocess, 'run', side_effect=query):
            try:
                module.main([], directory=archive.parent)
            except RuntimeError as exc:
                error = str(exc)
        return upload, error

    def test_npm_publication_selects_channel_and_preserves_other_tag(self):
        for value, channel in [('0.1.0', 'latest'), ('0.1.1-beta.1', 'beta')]:
            with self.subTest(version=value):
                upload, error = self.npm_case(value)
                self.assertIsNone(error)
                upload.assert_called_once()
                args = upload.call_args.args[0]
                self.assertEqual(args[args.index('--tag') + 1], channel)
                self.assertEqual(args[args.index('--access') + 1], 'public')

    def test_npm_existing_identical_version_is_not_published_again(self):
        upload, error = self.npm_case('0.1.0', already_exists=True)
        self.assertIsNone(error)
        upload.assert_not_called()

    def test_npm_existing_different_tarball_stops_before_publication(self):
        upload, error = self.npm_case('0.1.0', already_exists=True, bad_integrity=True)
        self.assertIn('different bytes', error)
        upload.assert_not_called()

    def test_npm_changed_other_channel_is_reported(self):
        for value in ('0.1.0', '0.1.1-beta.1'):
            with self.subTest(version=value):
                upload, error = self.npm_case(value, change_other=True)
                self.assertIn('tag changed during publication', error)


if __name__ == '__main__':
    unittest.main()

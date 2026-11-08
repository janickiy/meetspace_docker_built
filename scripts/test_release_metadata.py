#!/usr/bin/env python3
"""Reject changed archive bytes, mixed sources and incomplete cross-architecture releases."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('release_metadata', Path(__file__).with_name('release_metadata.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ReleaseMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.version = 'v0.6.1-prebuilt.test.1'
        self.commit = 'a' * 40

    def fixture(self, arch):
        payload = ('already scanned fixture ' + arch).encode()
        name = m.archive_name(self.version, arch)
        sha = hashlib.sha256(payload).hexdigest()
        (self.root / name).write_bytes(payload)
        (self.root / (name + '.sha256')).write_text(sha + '  ' + name + '\n')
        item = dict(schemaVersion=1, serviceInventoryVersion=2, repository=m.SOURCE_REPOSITORY,
                    version=self.version, commit=self.commit, platform='linux/' + arch,
                    archive=dict(url=m.archive_url(m.SOURCE_REPOSITORY, self.version, arch),
                                 sizeBytes=len(payload), sha256=sha))
        m.write_json(self.root / ('prebuilt-' + arch + '.json'), item)
        return item

    def both(self):
        for arch in m.ARCHITECTURES:
            self.fixture(arch)
            m.rebind(self.root, self.version, self.commit, arch)
        return m.build_index(self.root, self.version, self.commit)

    def test_rebind_changes_only_external_location_and_preserves_backend_identity(self):
        original = self.fixture('amd64')
        archive = self.root / m.archive_name(self.version, 'amd64')
        raw = archive.read_bytes()
        result = m.rebind(self.root, self.version, self.commit, 'amd64')
        self.assertEqual(raw, archive.read_bytes())
        self.assertEqual(m.SOURCE_REPOSITORY, result['sourceRepository'])
        self.assertEqual(self.commit, result['commit'])
        self.assertEqual(original['archive']['sha256'], result['archive']['sha256'])
        self.assertEqual(m.archive_url(m.REPOSITORY, self.version, 'amd64'), result['archive']['url'])

    def test_changed_bytes_fail_before_metadata_is_rewritten(self):
        self.fixture('arm64')
        metadata = self.root / 'prebuilt-arm64.json'
        before = metadata.read_bytes()
        archive = self.root / m.archive_name(self.version, 'arm64')
        archive.write_bytes(b'X' * archive.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'checksum'):
            m.rebind(self.root, self.version, self.commit, 'arm64')
        self.assertEqual(before, metadata.read_bytes())

    def test_foreign_source_or_url_is_rejected(self):
        original = self.fixture('amd64')
        for location, value in [('repository', 'attacker/source'), ('url', 'https://example.org/images.tar.gz')]:
            with self.subTest(location=location):
                item = copy.deepcopy(original)
                (item['archive'] if location == 'url' else item)[location] = value
                m.write_json(self.root / 'prebuilt-amd64.json', item)
                with self.assertRaises(ValueError):
                    m.rebind(self.root, self.version, self.commit, 'amd64')

    def test_missing_architecture_never_produces_index(self):
        self.fixture('amd64')
        m.rebind(self.root, self.version, self.commit, 'amd64')
        with self.assertRaises(ValueError):
            m.build_index(self.root, self.version, self.commit)
        self.assertFalse((self.root / 'prebuilt.json').exists())

    def test_mixed_backend_commits_cannot_be_published(self):
        self.both()
        path = self.root / 'prebuilt-arm64.json'
        item = m.read_json(path)
        item['commit'] = 'b' * 40
        m.write_json(path, item)
        with self.assertRaisesRegex(ValueError, 'backend release'):
            m.build_index(self.root, self.version, self.commit)

    def test_github_asset_digest_size_and_origin_must_match(self):
        index = self.both()
        assets = [dict(name=m.archive_name(self.version, arch), state='uploaded',
                       size=row['sizeBytes'], digest='sha256:' + row['sha256'], browser_download_url=row['url'])
                  for arch in m.ARCHITECTURES for row in [index['platforms']['linux/' + arch]]]
        release = dict(tag_name=self.version, assets=assets)
        self.assertEqual(index, m.build_index(self.root, self.version, self.commit, release))
        for key, value in [('size', 1), ('digest', 'sha256:' + '0' * 64),
                           ('browser_download_url', 'https://github.com/other/repo/archive.tar.gz'), ('state', 'new')]:
            with self.subTest(key=key):
                changed = copy.deepcopy(release)
                changed['assets'][0][key] = value
                with self.assertRaises(ValueError):
                    m.build_index(self.root, self.version, self.commit, changed)
        with self.assertRaises(ValueError):
            m.build_index(self.root, self.version, self.commit, dict(release, assets=assets[:1]))

    def test_concrete_versions_and_unambiguous_json_required(self):
        for version in ('latest', 'v1.0.0/../../other', '--help'):
            with self.assertRaises(ValueError):
                m.identity(version, self.commit)
        path = self.root / 'duplicate.json'
        path.write_text('{"commit":"a", "commit":"b"}')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            m.read_json(path)


if __name__ == '__main__':
    unittest.main()

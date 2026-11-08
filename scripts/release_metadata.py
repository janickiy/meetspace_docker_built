#!/usr/bin/env python3
"""Bind verified backend archives to this repository without changing archive bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import re

REPOSITORY = 'janickiy/meetspace_docker_built'
SOURCE_REPOSITORY = 'janickiy/meet-space'
ARCHITECTURES = ('amd64', 'arm64')
MAX_ARCHIVE_BYTES = 2 * 1024**3


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identity(version, commit):
    require(isinstance(version, str) and len(version) <= 64 and
            re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?', version),
            'A concrete v-prefixed release version is required')
    require(isinstance(commit, str) and re.fullmatch(r'[a-f0-9]{40}', commit),
            'A full backend commit is required')


def archive_name(version, arch):
    require(arch in ARCHITECTURES, 'Unsupported architecture')
    return f'meetspace-{version}-linux-{arch}.tar.gz'


def archive_url(repository, version, arch):
    return f'https://github.com/{repository}/releases/download/{version}/{archive_name(version, arch)}'


def read_json(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 4 * 1024**2,
            'Metadata is missing, linked, or oversized')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate metadata key')
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=unique)


def write_json(path, value):
    require(not path.is_symlink(), 'Refusing a linked metadata destination')
    path.write_text(json.dumps(value, indent=2) + '\n')


def validate_descriptor(item, version, commit, arch, *, source=False):
    identity(version, commit)
    repository = SOURCE_REPOSITORY if source else REPOSITORY
    require(isinstance(item, dict) and type(item.get('schemaVersion')) is int and item['schemaVersion'] == 1
            and type(item.get('serviceInventoryVersion')) is int and item['serviceInventoryVersion'] == 2
            and item.get('version') == version and item.get('commit') == commit
            and item.get('platform') == 'linux/' + arch and item.get('repository') == repository,
            'Architecture descriptor differs from the requested backend release')
    if not source:
        require(item.get('sourceRepository') == SOURCE_REPOSITORY, 'Unexpected backend source repository')
    archive = item.get('archive')
    require(isinstance(archive, dict) and set(archive) == {'url', 'sha256', 'sizeBytes'},
            'Invalid archive metadata fields')
    require(archive['url'] == archive_url(repository, version, arch)
            and isinstance(archive['sha256'], str) and re.fullmatch(r'[a-f0-9]{64}', archive['sha256'])
            and type(archive['sizeBytes']) is int and 0 < archive['sizeBytes'] < MAX_ARCHIVE_BYTES,
            'Invalid archive URL, checksum, or size')
    return archive


def rebind(directory, version, commit, arch):
    """Check actual archive bytes before changing only its external JSON descriptor."""
    descriptor_path = directory / ('prebuilt-' + arch + '.json')
    item = read_json(descriptor_path)
    metadata = validate_descriptor(item, version, commit, arch, source=True)
    archive = directory / archive_name(version, arch)
    require(archive.is_file() and not archive.is_symlink() and archive.stat().st_size == metadata['sizeBytes'],
            'Archive size differs from the backend descriptor')
    hasher = hashlib.sha256()
    with archive.open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            hasher.update(block)
    require(hasher.hexdigest() == metadata['sha256'], 'Archive checksum differs from the backend descriptor')
    checksum_path = directory / (archive.name + '.sha256')
    require(checksum_path.is_file() and not checksum_path.is_symlink()
            and checksum_path.read_text() == metadata['sha256'] + '  ' + archive.name + '\n',
            'Archive checksum sidecar differs')
    item.update(repository=REPOSITORY, sourceRepository=SOURCE_REPOSITORY)
    item['archive'] = dict(metadata, url=archive_url(REPOSITORY, version, arch))
    validate_descriptor(item, version, commit, arch)
    write_json(descriptor_path, item)
    return item


def validate_uploaded_asset(release, version, arch, archive):
    require(isinstance(release, dict) and release.get('tag_name') == version
            and isinstance(release.get('assets'), list), 'Unexpected GitHub release metadata')
    matches = [asset for asset in release['assets'] if asset.get('name') == archive_name(version, arch)]
    require(len(matches) == 1, 'Published archive is missing or duplicated')
    asset = matches[0]
    require(asset.get('state') == 'uploaded' and asset.get('size') == archive['sizeBytes']
            and asset.get('browser_download_url') == archive['url']
            and asset.get('digest') == 'sha256:' + archive['sha256'],
            'GitHub archive size, digest, or URL differs from the verified descriptor')


def build_index(directory, version, commit, release=None):
    identity(version, commit)
    index = dict(schemaVersion=1, serviceInventoryVersion=2, installationMode='prebuilt',
                 repository=REPOSITORY, sourceRepository=SOURCE_REPOSITORY,
                 version=version, commit=commit, platforms={})
    for arch in ARCHITECTURES:
        item = read_json(directory / ('prebuilt-' + arch + '.json'))
        archive = validate_descriptor(item, version, commit, arch)
        if release is not None:
            validate_uploaded_asset(release, version, arch, archive)
        index['platforms']['linux/' + arch] = archive
    write_json(directory / 'prebuilt.json', index)
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('rebind', 'index'))
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--arch', choices=ARCHITECTURES)
    parser.add_argument('--release-metadata', type=Path)
    args = parser.parse_args()
    if args.action == 'rebind':
        require(args.arch is not None and args.release_metadata is None, 'rebind requires --arch only')
        rebind(args.directory, args.version, args.commit, args.arch)
    else:
        require(args.arch is None, 'index requires both architectures')
        release = read_json(args.release_metadata) if args.release_metadata else None
        build_index(args.directory, args.version, args.commit, release)
    print('Verified release metadata:', args.action, args.version)


if __name__ == '__main__':
    main()

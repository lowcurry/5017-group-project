"""Verify the exact file inventory and hashes of a freshly extracted submission."""
import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath


MANIFEST = 'DELIVERY_MANIFEST.json'
CHECKSUMS = 'DELIVERY_SHA256SUMS.txt'
INTEGRITY_FILES = {MANIFEST, CHECKSUMS}


def verify(folder):
    try:
        manifest_path = folder / MANIFEST
        if manifest_path.is_symlink():
            raise ValueError('Symlink manifest')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        files = manifest['files']
        if manifest['schema_version'] != 'course-submission-1' or not isinstance(files, dict) or not files:
            raise ValueError('Invalid or empty submission manifest')
    except (KeyError, OSError, TypeError, ValueError):
        return {'pass': False, 'manifest_files': 0, 'mismatch_paths': [MANIFEST], 'unexpected_paths': []}

    paths = {path.relative_to(folder).as_posix(): path for path in folder.rglob('*')
             if path.is_symlink() or not path.is_dir()}
    unexpected = sorted(paths.keys() - files.keys() - INTEGRITY_FILES)
    mismatches = []
    expected_checksums = []
    for name, item in files.items():
        relative = PurePosixPath(name)
        if (relative.is_absolute() or '..' in relative.parts or relative.as_posix() != name
                or '\\' in name or name in INTEGRITY_FILES or not isinstance(item, dict)
                or item.get('type') != 'file' or not isinstance(item.get('sha256'), str)):
            mismatches.append(name)
            continue
        path = paths.get(name)
        expected_checksums.append(f"{item['sha256']}  {name}\n")
        if (path is None or path.is_symlink() or not path.is_file()
                or path.stat().st_size != item.get('bytes') or sha256(path.read_bytes()).hexdigest() != item['sha256']):
            mismatches.append(name)
    checksum_path = paths.get(CHECKSUMS)
    if (checksum_path is None or checksum_path.is_symlink() or not checksum_path.is_file()
            or checksum_path.read_bytes() != ''.join(expected_checksums).encode('utf-8')):
        mismatches.append(CHECKSUMS)

    return {'pass': not mismatches and not unexpected, 'manifest_files': len(files),
            'mismatch_paths': mismatches, 'unexpected_paths': unexpected}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    result = verify(parser.parse_args().folder)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['pass'] else 1)

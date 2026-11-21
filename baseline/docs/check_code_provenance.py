"""Verify the documented differences between frozen and packaged modules."""
import argparse
from hashlib import sha256
import json
from pathlib import Path


def check(root):
    freeze = json.loads((root / 'experiments/formal_v1/freeze.json').read_bytes())
    note = json.loads((root / 'docs/code_provenance.json').read_bytes())
    frozen = freeze['code']
    expected = note['expected_differences']
    additions = note.get('post_freeze_additions', {})
    if (note['freeze_sha256'] != freeze['freeze_sha256'] or len(frozen) != note['modules']
            or expected.keys() - frozen.keys() or additions.keys() & frozen.keys()):
        raise ValueError('Code provenance does not match the recorded freeze')
    errors = []
    for name, original in sorted(frozen.items()):
        if Path(name).name != name:
            raise ValueError(f'Invalid module path: {name}')
        path = root / 'curve_audit' / name
        known = expected.get(name)
        if known and (known['frozen_sha256'] != original or known['packaged_sha256'] == original):
            errors.append(f'Invalid documented difference: {name}')
        if not path.is_file() or path.is_symlink():
            errors.append(f'Missing or unsafe module: {name}')
        elif sha256(path.read_bytes()).hexdigest() != (known['packaged_sha256'] if known else original):
            errors.append(f'Unexpected module digest: {name}')
    for name, entry in sorted(additions.items()):
        path = root / 'curve_audit' / name
        if Path(name).name != name or not path.is_file() or path.is_symlink():
            errors.append(f'Missing or unsafe added module: {name}')
        elif sha256(path.read_bytes()).hexdigest() != entry['packaged_sha256']:
            errors.append(f'Unexpected added module digest: {name}')
    return {'pass': not errors, 'modules': len(frozen), 'expected_differences': sorted(expected),
            'post_freeze_additions': sorted(additions), 'errors': errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', nargs='?', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        result = check(args.root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f'{exc}\n')
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['pass'] else 1)


if __name__ == '__main__':
    main()

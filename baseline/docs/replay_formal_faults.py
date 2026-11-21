"""Verify the 32 frozen fault cases and optionally save the corrected results."""
import argparse
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', nargs='?', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, help='New output file outside the package')
    args = parser.parse_args()
    root = args.root.resolve()
    if args.output is not None and args.output.resolve().is_relative_to(root):
        parser.error('--output must be outside the package')
    sys.path.insert(0, str(root))
    from curve_audit.common import read_json
    from curve_audit.current_study import digest
    from curve_audit.current_verify import bound_recorded_sources
    from curve_audit.formal_replay import replay_formal_faults, require

    formal = root / 'experiments/formal_v1'
    freeze = read_json(formal / 'freeze.json')
    require(digest({key: value for key, value in freeze.items() if key != 'freeze_sha256'})
            == freeze['freeze_sha256'], 'Freeze hash mismatch')
    binding = freeze['binding']
    tasks = read_json(root / 'results/current_preparation/tasks.json')
    require(digest(tasks) == binding['tasks_sha256'], 'Frozen tasks hash mismatch')
    primary, secondary = bound_recorded_sources(binding, root / 'data/current_study')
    count = replay_formal_faults(formal, {task['task_id']: task for task in tasks}, primary, secondary)

    # The shared replay checks every recomputed observation against this record.
    data = (formal / 'verifier_replay_20261105.json').read_bytes()
    if args.output is None:
        sys.stdout.buffer.write(data)
    else:
        with args.output.open('xb') as output:
            output.write(data)
        print(f'Verified {count} cases and {2 * count} verdicts. Wrote {args.output}.')


if __name__ == '__main__':
    main()

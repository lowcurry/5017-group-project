"""Commands for the delivered study and its historical prototype."""
import argparse
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(prog='curve-audit', description='MSBD 5017 Curve 3pool study')
    parser.add_argument('command', choices=('replay', 'verify', 'legacy'), help=(
        'replay the frozen study, verify a report, or use the historical prototype'))
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    if args.command == 'replay':
        from .formal_replay import main as replay

        options = argparse.ArgumentParser(prog='curve-audit replay', description='Replay the frozen study offline')
        options.add_argument('--root', type=Path, default=Path.cwd(), help='extracted submission root')
        root = options.parse_args(args.arguments).root
        try:
            replay(root)
        except (OSError, ValueError) as exc:
            options.exit(1, f'curve-audit replay: {exc}\n')
        return 0
    if args.command == 'verify':
        from .current_verify import main as verify

        return verify(args.arguments)

    from .confirmation_cli import main as legacy

    return legacy(args.arguments)


if __name__ == '__main__':
    raise SystemExit(main())

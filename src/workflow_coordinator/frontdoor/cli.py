"""Minimal installed dispatcher; legacy arguments belong to the operator."""
import json
import sys

from ..implementation_identity import implementation_identity


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        identity = implementation_identity()
    except (OSError, RuntimeError) as error:
        print(f'wfc: {error}', file=sys.stderr)
        return 2
    if args in (['--version'], ['--version', '--json'], ['--json', '--version']):
        print(json.dumps(identity, sort_keys=True) if '--json' in args else f"wfc {identity['version']}")
        return 0
    if args[:1] == ['init']:
        from .init import main as init_main
        return init_main(args[1:])
    if args[:1] == ['plan']:
        from .plan import main as plan_main
        return plan_main(args[1:])
    if args[:1] == ['archive']:
        from .archive import main as archive_main
        return archive_main(args[1:])
    from ..operator import main as operator_main
    return operator_main(args)

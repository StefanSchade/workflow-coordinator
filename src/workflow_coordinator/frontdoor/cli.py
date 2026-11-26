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
    if args[:1] == ['request']:
        from .emit_request import main as request_main
        return request_main(args[1:])
    if args[:1] == ['archive']:
        from .archive import main as archive_main
        return archive_main(args[1:])
    if args[:1] == ['audit']:
        from .audit import main as audit_main
        return audit_main(args[1:])
    if args[:1] == ['report']:
        from .report import main as report_main
        return report_main(args[1:])
    if args[:1] in (['run'], ['resume']):
        from .lifecycle import main as lifecycle_main
        return lifecycle_main(args)
    if args[:1] == ['status'] and not any(
        arg == '--state-root' or arg.startswith('--state-root=') for arg in args[1:]
    ):
        from .lifecycle import main as lifecycle_main
        return lifecycle_main(args)
    if args[:1] == ['skill']:
        from .skill import main as skill_main
        return skill_main(args[1:])
    from ..operator import main as operator_main
    return operator_main(args)

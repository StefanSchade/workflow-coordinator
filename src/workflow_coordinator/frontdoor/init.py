"""Installed ``wfc init`` -- create a neutral consumer's Consumer Config only.

This deliberately never commits what it writes and never executes any
project command.  It materializes exactly the one durable, per-repository
input BP2b's compiler needs (``frontdoor.config.ConsumerConfig``), refusing
to overwrite an existing one, and leaves committing it to the maintainer.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path, PurePosixPath

import yaml

from ..commissioning import CommissionError
from ..git_facts import GitPreflightError, repository_identity
from .config import consumer_config_from_yaml

DEFAULT_CONFIG_PATH = ".wfc/config.yaml"
DEFAULT_GOVERNANCE_DIR = ".wfc/governance"


def _validated_config_destination(repository: Path, config_path: str) -> Path:
    """Resolve ``--config-path`` strictly inside ``repository``, before any mutation.

    Refuses an absolute path, a ``..``-escaping path, and a symlink escape (an
    existing parent component that resolves outside the repository) up front --
    none of those may ever reach ``mkdir``/``write_text`` below.
    """

    if not config_path or config_path != config_path.strip() or "\\" in config_path:
        raise CommissionError("--config-path must be a normalized repository-relative path")
    candidate = PurePosixPath(config_path)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise CommissionError("--config-path must be a repository-relative path that does not escape the repository")
    destination = repository / candidate
    try:
        resolved = destination.resolve()
        resolved.relative_to(repository)
    except ValueError as error:
        raise CommissionError("--config-path must resolve inside the repository") from error
    return destination


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wfc init",
        description=(
            "Create the per-repository Consumer Config a future `wfc plan`/compile "
            "needs. Writes exactly one new file; never commits it, never runs a "
            "project command."
        ),
    )
    parser.add_argument("--repository", default=".", help="consumer repository root (default: current directory)")
    parser.add_argument(
        "--scope-ceiling", action="append", default=[], required=True, metavar="PATH",
        help="repository-relative path a future Commission's scope may never exceed (repeatable, at least one)",
    )
    parser.add_argument(
        "--governance-dir", default=DEFAULT_GOVERNANCE_DIR,
        help=f"directory a compiled Commission is later written under (default: {DEFAULT_GOVERNANCE_DIR})",
    )
    parser.add_argument("--repository-name", help="defaults to the repository directory name")
    parser.add_argument("--max-autonomous-hops", type=int, default=5)
    parser.add_argument(
        "--config-path", default=DEFAULT_CONFIG_PATH,
        help=f"where to write the Consumer Config, relative to --repository (default: {DEFAULT_CONFIG_PATH})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        repository = repository_identity(Path(args.repository))
    except GitPreflightError as error:
        print(f"wfc init: {error}", file=sys.stderr)
        return 2

    try:
        destination = _validated_config_destination(repository, args.config_path)
    except CommissionError as error:
        print(f"wfc init: {error}", file=sys.stderr)
        return 2
    if destination.exists():
        print(f"wfc init: refusing to overwrite existing Consumer Config: {destination}", file=sys.stderr)
        return 2

    config_data = {
        "schema_version": 1,
        "repository_name": args.repository_name or repository.name,
        "governance_dir": args.governance_dir,
        "work_item_scope_ceiling": list(args.scope_ceiling),
        "max_autonomous_hops": args.max_autonomous_hops,
        "default_execution": {},
    }
    text = yaml.safe_dump(config_data, sort_keys=False, allow_unicode=True)
    try:
        consumer_config_from_yaml(text)
    except CommissionError as error:
        print(f"wfc init: generated Consumer Config is invalid: {error}", file=sys.stderr)
        return 2

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    relative = destination.relative_to(repository) if destination.is_relative_to(repository) else destination
    print(f"wrote Consumer Config: {relative}")
    print(
        "NEXT EXPLICIT ACTION: review this file, fill in default_execution for your "
        "roles, and commit it yourself -- `wfc init` never commits it."
    )
    return 0

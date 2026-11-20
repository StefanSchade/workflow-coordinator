"""Installed ``wfc init`` -- create a neutral consumer's Consumer Config,
and (BP6b, additive) create or adopt the Evidence repository.

The Consumer Config write is unchanged from BP3: it is never committed and
never overwrites an existing file. The Evidence-repository step that follows
it never touches the payload repository -- it only creates or adopts a
separate Git repository at the resolved Evidence location, with one initial
commit and no remote.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

import yaml

from ..commissioning import CommissionError
from ..git_facts import GitPreflightError, repository_identity
from .config import (
    EVIDENCE_RAW_CHOICES,
    consumer_config_from_yaml,
    evidence_gitignore_block,
    resolve_evidence,
)

DEFAULT_CONFIG_PATH = ".wfc/config.yaml"
DEFAULT_GOVERNANCE_DIR = ".wfc/governance"
_DEFAULT_EVIDENCE_RAW = "local"


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


def _trusted_bootstrap_environment() -> dict[str, str]:
    return {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}


def _run_bootstrap_git(repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """One Evidence-repository bootstrap Git command, under the same trusted
    execution policy ``git_facts`` uses for Coordinator commits (no hooks, no
    signing, no fsmonitor) -- duplicated here rather than imported, because
    this creates a brand-new repository's very first commit, before any HEAD
    exists for ``bounded_path_commit``/``trusted_commit`` to anchor on.
    """

    configuration = ("-c", f"core.hooksPath={os.devnull}", "-c", "commit.gpgSign=false", "-c", "core.fsmonitor=false")
    return subprocess.run(
        ("git", "-C", str(repository), *configuration, *args),
        check=False, text=True, capture_output=True, env=_trusted_bootstrap_environment(),
    )


def _create_evidence_repository(path: Path) -> None:
    archive_dir = path / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / ".gitkeep").write_text("", encoding="utf-8")
    (path / ".gitignore").write_text(evidence_gitignore_block(), encoding="utf-8")

    result = _run_bootstrap_git(path, "init", "-q", "-b", "main")
    if result.returncode:
        raise CommissionError(f"failed to initialize Evidence repository at {path}: {result.stderr.strip()}")
    result = _run_bootstrap_git(path, "add", "--", "archive/.gitkeep", ".gitignore")
    if result.returncode:
        raise CommissionError(f"failed to stage the new Evidence repository's content: {result.stderr.strip()}")
    result = _run_bootstrap_git(path, "commit", "--no-verify", "-m", "chore: initialize WFC Evidence repository")
    if result.returncode:
        raise CommissionError(f"failed to create the initial Evidence repository commit: {result.stderr.strip()}")


def ensure_evidence_repository(path: Path) -> str:
    """Create a new Evidence repository at ``path``, or adopt an existing valid one.

    Returns ``"created"`` or ``"reused"``. A path that exists, is non-empty,
    and is not already a WFC Evidence repository (a Git repository root with
    an ``archive/`` directory) is refused before anything is written.
    """

    if path.exists():
        if not path.is_dir():
            raise CommissionError(f"Evidence location exists and is not a directory: {path}")
        if (path / "archive").is_dir() and (path / ".git").exists():
            try:
                identity = repository_identity(path)
            except GitPreflightError as error:
                raise CommissionError(
                    f"Evidence location is not a usable Git repository: {path}: {error}"
                ) from error
            if identity != path.resolve():
                raise CommissionError(f"Evidence location is not a Git repository root: {path}")
            return "reused"
        if any(path.iterdir()):
            raise CommissionError(
                f"refusing: Evidence location {path} exists, is non-empty, and is not a WFC "
                "Evidence repository (no Git repository root with an archive/ directory); "
                "nothing was written"
            )
    else:
        path.mkdir(parents=True)
    _create_evidence_repository(path)
    return "created"


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
    parser.add_argument(
        "--evidence-repository", default=None, metavar="PATH",
        help="Evidence repository location, absolute or relative to --repository "
             "(default: <data home>/wfc/evidence/<repository-name>)",
    )
    parser.add_argument(
        "--evidence-raw", choices=EVIDENCE_RAW_CHOICES, default=None,
        help=f"raw-file retention mode (default: {_DEFAULT_EVIDENCE_RAW})",
    )
    parser.add_argument(
        "--no-evidence-repository", action="store_true",
        help="write the Consumer Config only; do not create or adopt an Evidence repository",
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
    evidence_overrides: dict[str, str] = {}
    if args.evidence_repository is not None:
        evidence_overrides["repository"] = args.evidence_repository
    if args.evidence_raw is not None and args.evidence_raw != _DEFAULT_EVIDENCE_RAW:
        evidence_overrides["raw"] = args.evidence_raw
    if evidence_overrides:
        config_data["evidence"] = evidence_overrides

    text = yaml.safe_dump(config_data, sort_keys=False, allow_unicode=True)
    try:
        config = consumer_config_from_yaml(text)
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

    if args.no_evidence_repository:
        return 0

    try:
        settings = resolve_evidence(config, repository, os.environ)
        outcome = ensure_evidence_repository(settings.path)
    except CommissionError as error:
        print(f"wfc init: {error}", file=sys.stderr)
        return 2

    verb = "created new" if outcome == "created" else "reusing existing"
    print(f"Evidence repository ({verb}): {settings.path}  (raw retention: {settings.raw})")
    print("NOTHING WAS PUSHED: the Evidence repository has no remote configured.")
    return 0

"""Installed ``wfc skill`` -- install, inspect and print the packaged wfc
Agent Skill (FTR-WFC-AGENT-SKILL-DISTRIBUTION, slice S2).

Standard library only; the one package import allowed is
``implementation_identity.VERSION`` (SI-10: no module but ``frontdoor/cli.py``
imports this one). Every byte this module writes or prints comes from the
*installed* package's own ``agent_skill/wfc`` files (``importlib.resources``),
never from a checkout path (SI-8). This module never starts, resumes or
authorizes a Run, never imports the engine, and never reads or writes
``.wfc/``, the journal or Evidence (SI-4); it never commits or stages, and it
never writes ``CLAUDE.md``, ``AGENTS.md``, ``.gitignore``, host settings or
permission files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from importlib.resources import files as _resource_files

from ..implementation_identity import VERSION

MARKER_NAME = ".wfc-skill.json"
MARKER_SCHEMA = 1
HOSTS = ("claude", "codex")
SCOPES = ("user", "project")
INVOKE = {"claude": "/wfc", "codex": "$wfc"}
_SHA256_HEX_LENGTH = 64


class SkillError(Exception):
    """A truthful, step-bounded refusal -- never a fabricated guarantee."""

    def __init__(self, message: str, *, code: int):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------
# Packaged content (SI-8: installed package only, never a checkout path).
# --------------------------------------------------------------------------

def _packaged_root() -> Path:
    return Path(str(_resource_files("workflow_coordinator"))).resolve() / "agent_skill" / "wfc"


def _packaged_files() -> dict[str, bytes]:
    root = _packaged_root()
    data: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            data[path.relative_to(root).as_posix()] = path.read_bytes()
    return data


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _packaged_hashes(packaged: dict[str, bytes]) -> dict[str, str]:
    return {relative: _sha256_hex(data) for relative, data in packaged.items()}


# --------------------------------------------------------------------------
# Destination resolution.
# --------------------------------------------------------------------------

def _claude_user_root() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override) / "skills"
    return Path.home() / ".claude" / "skills"


def _codex_user_root() -> Path:
    return Path.home() / ".agents" / "skills"


def _project_subpath(host: str) -> str:
    return ".claude/skills/wfc" if host == "claude" else ".agents/skills/wfc"


def _git_toplevel(repository: Path) -> Path | None:
    completed = subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "--show-toplevel"),
        capture_output=True, text=True,
    )
    if completed.returncode != 0:
        return None
    return Path(completed.stdout.strip()).resolve()


def resolve_destination(
    host: str, scope: str | None, repository: Path, skills_dir: str | None,
) -> tuple[Path, str]:
    """Returns ``(destination, scope_label)``; ``scope_label`` is one of
    ``user``, ``project`` or ``custom``. Raises ``SkillError`` (code 2) for
    ``--scope project`` outside a Git work tree."""

    if skills_dir is not None:
        return Path(skills_dir).resolve() / "wfc", "custom"
    if scope == "project":
        toplevel = _git_toplevel(repository)
        if toplevel is None:
            raise SkillError(
                f"--scope project requires {repository} to be inside a Git work tree", code=2,
            )
        return toplevel / _project_subpath(host), "project"
    root = _claude_user_root() if host == "claude" else _codex_user_root()
    return root / "wfc", "user"


# --------------------------------------------------------------------------
# Installed-state classification.
# --------------------------------------------------------------------------

def _hash_tree(destination: Path) -> dict[str, str]:
    """Never follows a symbolic link, at the destination or within it."""

    hashes: dict[str, str] = {}
    for root, dirs, filenames in os.walk(destination, followlinks=False):
        root_path = Path(root)
        for filename in filenames:
            file_path = root_path / filename
            if file_path.is_symlink():
                continue
            relative = file_path.relative_to(destination).as_posix()
            if relative == MARKER_NAME:
                continue
            hashes[relative] = _sha256_hex(file_path.read_bytes())
    return hashes


def _read_marker(destination: Path) -> dict[str, Any] | None:
    marker_path = destination / MARKER_NAME
    if marker_path.is_symlink() or not marker_path.is_file():
        return None
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _marker_is_valid(marker: dict[str, Any] | None) -> bool:
    if marker is None:
        return False
    if marker.get("schema") != MARKER_SCHEMA:
        return False
    if not isinstance(marker.get("wfc_version"), str):
        return False
    files = marker.get("files")
    if not isinstance(files, dict):
        return False
    return all(
        isinstance(key, str) and isinstance(value, str) and len(value) == _SHA256_HEX_LENGTH
        for key, value in files.items()
    )


def _marker_bytes(file_hashes: dict[str, str], version: str) -> bytes:
    marker = {"schema": MARKER_SCHEMA, "wfc_version": version, "files": file_hashes}
    return (json.dumps(marker, sort_keys=True, indent=2) + "\n").encode("utf-8")


def evaluate_state(
    destination: Path, packaged_hashes: dict[str, str],
) -> tuple[str, list[str], str | None]:
    """Returns ``(state, differing_files, installed_version)``.

    ``state`` is one of ``absent``, ``current``, ``stale``, ``modified`` or
    ``foreign``. A symbolic-link destination is never followed: it is
    reported as ``foreign`` and its content is never read.
    """

    if destination.is_symlink():
        return "foreign", [], None
    if not destination.exists():
        return "absent", [], None
    if not destination.is_dir():
        return "foreign", [], None

    actual_hashes = _hash_tree(destination)
    marker = _read_marker(destination)
    marker_valid = _marker_is_valid(marker)
    installed_version = marker.get("wfc_version") if marker_valid else None

    if actual_hashes == packaged_hashes:
        return "current", [], installed_version

    if marker_valid and marker["files"] == actual_hashes:
        return "stale", [], installed_version

    if marker_valid:
        differing = sorted(set(actual_hashes) | set(marker["files"]))
        differing = [
            relative for relative in differing
            if actual_hashes.get(relative) != marker["files"].get(relative)
        ]
        return "modified", differing, installed_version

    return "foreign", sorted(actual_hashes), None


# --------------------------------------------------------------------------
# Staged, atomic install (never a staged copy inside the skills root).
# --------------------------------------------------------------------------

def _remove_destination(destination: Path) -> None:
    if destination.is_dir() and not destination.is_symlink():
        shutil.rmtree(destination)
    else:
        destination.unlink()


def _stage_and_install(
    destination: Path, packaged: dict[str, bytes], marker_bytes: bytes, *, replace_existing: bool,
) -> None:
    skills_root = destination.parent
    skills_root.mkdir(parents=True, exist_ok=True)
    parent_of_skills_root = skills_root.parent
    parent_of_skills_root.mkdir(parents=True, exist_ok=True)

    staging_dir = Path(tempfile.mkdtemp(prefix=".wfc-skill-staging-", dir=parent_of_skills_root))
    try:
        staged_target = staging_dir / "wfc"
        staged_target.mkdir()
        for relative, data in packaged.items():
            out_path = staged_target / relative
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(data)
        (staged_target / MARKER_NAME).write_bytes(marker_bytes)
        if replace_existing:
            _remove_destination(destination)
        os.rename(staged_target, destination)
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)


def _install_action(state: str, *, force: bool) -> tuple[str, int]:
    """Returns ``(action, exit_code)``; ``action`` is ``noop``, ``write`` or ``refuse``."""

    if state in ("absent", "stale"):
        return "write", 0
    if state == "current":
        return "noop", 0
    if force:
        return "write", 0
    return "refuse", 3


# --------------------------------------------------------------------------
# argparse.
# --------------------------------------------------------------------------

def _add_destination_arguments(parser: argparse.ArgumentParser, *, default_scope: str | None) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--scope", choices=SCOPES, default=default_scope)
    group.add_argument("--skills-dir", default=None, metavar="DIR")
    parser.add_argument("--repository", default=".", help="consumer repository root (default: current directory)")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wfc skill",
        description="Install, inspect or print the packaged wfc Agent Skill.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    install_parser = subparsers.add_parser(
        "install", help="install the packaged skill into one host's skill directory",
    )
    install_parser.add_argument("host", choices=HOSTS)
    _add_destination_arguments(install_parser, default_scope="user")
    install_parser.add_argument("--dry-run", action="store_true", help="print the detected state and planned action; write nothing")
    install_parser.add_argument("--force", action="store_true", help="replace an existing modified or foreign installation")

    status_parser = subparsers.add_parser("status", help="report the installed state for one host, read-only")
    status_parser.add_argument("host", choices=HOSTS)
    _add_destination_arguments(status_parser, default_scope=None)
    status_parser.add_argument("--json", action="store_true")

    show_parser = subparsers.add_parser("show", help="print one packaged skill file, or list them with digests")
    show_parser.add_argument("file", nargs="?", default=None, metavar="FILE")
    show_parser.add_argument("--list", action="store_true")

    return parser


def _parse_args(argv: list[str]) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


# --------------------------------------------------------------------------
# ``install``.
# --------------------------------------------------------------------------

def cmd_install(args: argparse.Namespace) -> int:
    repository = Path(args.repository)
    try:
        destination, scope_label = resolve_destination(args.host, args.scope, repository, args.skills_dir)
    except SkillError as error:
        print(f"wfc skill install: {error}", file=sys.stderr)
        return error.code

    if destination.is_symlink():
        print(
            f"wfc skill install: refusing to follow or replace a symbolic-link destination: {destination}",
            file=sys.stderr,
        )
        return 2

    packaged = _packaged_files()
    packaged_hashes = _packaged_hashes(packaged)
    state, differing, _installed_version = evaluate_state(destination, packaged_hashes)
    action, exit_code = _install_action(state, force=args.force)

    if action == "refuse":
        print(f"STATE: {state}")
        print("DIFFERING FILES:")
        for relative in differing:
            print(f"  {relative}")
        print(
            f"wfc skill install: refusing to replace an existing {state} installation without "
            f"--force: {destination}",
            file=sys.stderr,
        )
        return exit_code

    if action == "write" and not args.dry_run:
        marker_bytes = _marker_bytes(packaged_hashes, VERSION)
        _stage_and_install(destination, packaged, marker_bytes, replace_existing=(state != "absent"))

    label = "WOULD INSTALL" if (action == "write" and args.dry_run) else "INSTALLED"
    print(f"STATE: {state}")
    print(f"{label}: {destination}")
    print(f"WFC VERSION: {VERSION}")
    print(f"INVOKE: {INVOKE[args.host]}")
    if scope_label == "project":
        print(
            "NEXT EXPLICIT ACTION: commit this directory yourself -- `wfc skill install` never "
            "commits, and an uncommitted copy makes `wfc run` refuse the tree."
        )
    print("NOTHING ELSE WAS CHANGED")
    return exit_code


# --------------------------------------------------------------------------
# ``status``.
# --------------------------------------------------------------------------

def _status_installations(args: argparse.Namespace, repository: Path) -> list[tuple[str, Path]]:
    if args.skills_dir is not None or args.scope is not None:
        destination, scope_label = resolve_destination(args.host, args.scope, repository, args.skills_dir)
        return [(scope_label, destination)]
    user_destination, _ = resolve_destination(args.host, "user", repository, None)
    installations = [("user", user_destination)]
    try:
        project_destination, _ = resolve_destination(args.host, "project", repository, None)
        installations.append(("project", project_destination))
    except SkillError:
        pass  # outside a Git work tree: omitted, not an error.
    return installations


def _format_status_text(host: str, packaged_version: str, installations: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for installation in installations:
        lines.append(f"HOST: {host}")
        lines.append(f"SCOPE: {installation['scope']}")
        lines.append(f"PATH: {installation['path']}")
        lines.append(f"STATE: {installation['state']}")
        lines.append(f"INSTALLED VERSION: {installation['installed_version'] or '-'}")
        lines.append("")
    lines.append(f"PACKAGED VERSION: {packaged_version}")
    return "\n".join(lines) + "\n"


def cmd_status(args: argparse.Namespace) -> int:
    repository = Path(args.repository)
    try:
        installations = _status_installations(args, repository)
    except SkillError as error:
        print(f"wfc skill status: {error}", file=sys.stderr)
        return error.code

    packaged_hashes = _packaged_hashes(_packaged_files())
    results = []
    for scope_label, destination in installations:
        state, _differing, installed_version = evaluate_state(destination, packaged_hashes)
        results.append({
            "scope": scope_label, "path": str(destination),
            "state": state, "installed_version": installed_version,
        })

    if args.json:
        payload = {"host": args.host, "packaged_version": VERSION, "installations": results}
        print(json.dumps(payload, sort_keys=True))
    else:
        sys.stdout.write(_format_status_text(args.host, VERSION, results))
    return 0


# --------------------------------------------------------------------------
# ``show``.
# --------------------------------------------------------------------------

def cmd_show(args: argparse.Namespace) -> int:
    packaged = _packaged_files()
    if args.list:
        for relative in sorted(packaged):
            print(f"{relative}  sha256:{_sha256_hex(packaged[relative])}")
        return 0

    file_name = args.file or "SKILL.md"
    if file_name not in packaged:
        print(f"wfc skill show: not a packaged file: {file_name}", file=sys.stderr)
        return 2
    sys.stdout.write(packaged[file_name].decode("utf-8"))
    return 0


# --------------------------------------------------------------------------
# Entry point.
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.subcommand == "install":
        return cmd_install(args)
    if args.subcommand == "status":
        return cmd_status(args)
    return cmd_show(args)

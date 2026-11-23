#!/usr/bin/env python3
"""Create a disposable WFC consumer demo workspace.

Standard library only. Writes exclusively inside the workspace directory
named on the command line; never deletes or overwrites anything; refuses a
target inside any Git work tree. Calls the installed ``wfc`` for every
WFC-owned write (``wfc init``); this script never writes a Consumer Config
or a Commission itself. See ``docs/backlog/FTR-WFC-CONSUMER-DEMO-WORKSPACE.adoc``
for the specification this implements (slice S1).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

KIT_DIR = Path(__file__).resolve().parent
SEED_DIR = KIT_DIR / "seed"
REQUESTS_TEMPLATE_DIR = KIT_DIR / "requests"
MARKER_NAME = "WFC-DEMO-WORKSPACE"
GUIDANCE_PATH = ".wfc/governance/repository-policy.md"
DEMO_IDENTITY_NAME = "WFC Demo"
DEMO_IDENTITY_EMAIL = "wfc-demo@example.invalid"


class PreflightError(Exception):
    """A preflight check failed; nothing was created."""


class StepError(Exception):
    """A later step failed; the partial workspace is left in place."""


def _resolve_wfc(explicit: str | None) -> str:
    if explicit:
        return explicit
    from_env = os.environ.get("WFC_BIN")
    if from_env:
        return from_env
    return "wfc"


def _wfc_version_json(wfc_bin: str) -> str:
    try:
        result = subprocess.run(
            [wfc_bin, "--version", "--json"], capture_output=True, text=True
        )
    except OSError as error:
        raise PreflightError(f"could not run `{wfc_bin} --version --json`: {error}") from error
    if result.returncode != 0:
        raise PreflightError(
            f"`{wfc_bin} --version --json` exited {result.returncode}: {result.stderr.strip()}"
        )
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise PreflightError(
            f"`{wfc_bin} --version --json` did not print a JSON object: {error}"
        ) from error
    if not isinstance(parsed, dict):
        raise PreflightError(f"`{wfc_bin} --version --json` did not print a JSON object")
    return result.stdout.strip()


def _nearest_existing_ancestor(path: Path) -> Path:
    candidate = path
    while not candidate.exists():
        if candidate.parent == candidate:
            return candidate
        candidate = candidate.parent
    return candidate


def _is_inside_git_work_tree(path: Path) -> bool:
    ancestor = _nearest_existing_ancestor(path)
    result = subprocess.run(
        ["git", "-C", str(ancestor), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _preflight(workspace: Path, wfc_bin: str) -> str:
    if sys.version_info < (3, 10):
        raise PreflightError(f"Python 3.10 or newer is required (found {sys.version.split()[0]})")
    if shutil.which("git") is None:
        raise PreflightError("`git` was not found on PATH")
    version_json = _wfc_version_json(wfc_bin)
    if workspace.exists():
        if not workspace.is_dir():
            raise PreflightError(f"workspace target exists and is not a directory: {workspace}")
        if any(workspace.iterdir()):
            raise PreflightError(f"workspace target exists and is not empty: {workspace}")
    if _is_inside_git_work_tree(workspace):
        raise PreflightError(f"workspace target is inside a Git work tree: {workspace}")
    return version_json


def _git(repository: Path, *args: str) -> None:
    result = subprocess.run(
        ("git", "-C", str(repository), "-c", "commit.gpgsign=false", *args),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise StepError(f"`git {' '.join(args)}` failed in {repository}: {result.stderr.strip()}")


def _create_greeter_repository(workspace: Path) -> Path:
    greeter = workspace / "greeter"
    greeter.mkdir()
    _git(greeter, "init", "-q", "-b", "main")
    _git(greeter, "config", "user.name", DEMO_IDENTITY_NAME)
    _git(greeter, "config", "user.email", DEMO_IDENTITY_EMAIL)
    shutil.copytree(SEED_DIR, greeter, dirs_exist_ok=True)
    _git(greeter, "add", "-A")
    _git(greeter, "commit", "-q", "-m", "chore: seed demo repository")
    return greeter


def _run_wfc_init(wfc_bin: str, greeter: Path) -> None:
    result = subprocess.run(
        [
            wfc_bin, "init",
            "--repository", str(greeter),
            "--scope-ceiling", "greeter",
            "--scope-ceiling", "tests",
            "--scope-ceiling", "docs",
            "--max-autonomous-hops", "6",
            "--evidence-repository", "../greeter-evidence",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise StepError(f"`wfc init` failed: {result.stdout.strip()}\n{result.stderr.strip()}")
    _git(greeter, "add", ".wfc/config.yaml")
    _git(greeter, "commit", "-q", "-m", "chore: add WFC Consumer Config")


def _run_baseline_check(greeter: Path) -> None:
    result = subprocess.run(["sh", "checks/check.sh"], cwd=greeter, capture_output=True, text=True)
    if result.returncode != 0:
        raise StepError(
            f"`sh checks/check.sh` failed in the seeded repository: {result.stdout.strip()}\n"
            f"{result.stderr.strip()}"
        )
    status = subprocess.run(
        ("git", "-C", str(greeter), "status", "--porcelain"), capture_output=True, text=True,
    )
    if status.stdout.strip():
        raise StepError(
            "`sh checks/check.sh` left the tree unclean (a file Git does not ignore changed): "
            f"{status.stdout.strip()}"
        )


def _guidance_commit(greeter: Path) -> str:
    result = subprocess.run(
        ("git", "-C", str(greeter), "log", "-1", "--format=%H", "--", GUIDANCE_PATH),
        capture_output=True, text=True,
    )
    commit = result.stdout.strip()
    if result.returncode != 0 or not commit:
        raise StepError(f"could not resolve the guidance source commit for {GUIDANCE_PATH}")
    return commit


def _render_requests(workspace: Path) -> Path:
    destination = workspace / "requests"
    destination.mkdir()
    commit = _guidance_commit(workspace / "greeter")
    for template in sorted(REQUESTS_TEMPLATE_DIR.iterdir()):
        text = template.read_text(encoding="utf-8")
        text = text.replace("{{GUIDANCE_COMMIT}}", commit)
        (destination / template.name).write_text(text, encoding="utf-8")
    return destination


def _write_marker(workspace: Path, version_json: str) -> None:
    marker = workspace / MARKER_NAME
    marker.write_text(f"kit: {KIT_DIR}\nwfc --version --json: {version_json}\n", encoding="utf-8")


def _git_filter_notice(greeter: Path) -> str | None:
    result = subprocess.run(
        ("git", "config", "--get-regexp", r"^filter\..*\.(clean|process)$"),
        cwd=greeter, capture_output=True, text=True,
    )
    if not result.stdout.strip():
        return None
    return (
        "NOTICE: a Git filter (Git LFS included) is configured on this machine:\n"
        f"{result.stdout.strip()}\n"
        "A live Run will refuse its trusted commits while such a filter is configured. "
        "A rehearsal is affected only when the filter is configured at system level."
    )


def _print_layout(workspace: Path, version_json: str) -> None:
    print(f"{workspace}/")
    print(f"├── {MARKER_NAME}       marker: kit path, `wfc --version --json` output")
    print("├── greeter/                 consumer repository, branch main, no remote")
    print("│   └── .wfc/config.yaml     written by `wfc init`, committed by the kit")
    print("├── greeter-evidence/        Evidence repository created by `wfc init`, no remote")
    print("├── requests/                rendered task documents and Consumer Requests")
    print("└── rehearsal-home/          HOME of rehearsals only; WFC lifecycle state lands here")
    print()
    print(f"installed WFC version: {json.loads(version_json)['version']}")
    print()
    print("Next steps (live, with your own provider credentials; rehearse.py does not exist yet):")
    print("  wfc plan requests/<name>.task.md --repository greeter ... "
          "(see requests/<name>.request.yaml for the exact fields)")
    print("  wfc run --repository greeter --request requests/<name>.request.yaml "
          "--execution-override worker.adapter=<adapter> "
          "--execution-override worker.model=<model> "
          "--execution-override worker.reasoning=<reasoning> --yes")


def create_workspace(workspace: Path, wfc_bin: str) -> int:
    try:
        version_json = _preflight(workspace, wfc_bin)
    except PreflightError as error:
        print(f"make_workspace.py: {error}", file=sys.stderr)
        return 2

    workspace.mkdir(exist_ok=True)
    try:
        greeter = _create_greeter_repository(workspace)
        _run_wfc_init(wfc_bin, greeter)
        _run_baseline_check(greeter)
        _render_requests(workspace)
        (workspace / "rehearsal-home").mkdir()
        _write_marker(workspace, version_json)
    except StepError as error:
        print(f"make_workspace.py: {error}", file=sys.stderr)
        print(f"the partial workspace was left in place: {workspace}", file=sys.stderr)
        print("remove it and start again; nothing is cleaned up automatically.", file=sys.stderr)
        return 1

    _print_layout(workspace, version_json)
    notice = _git_filter_notice(workspace / "greeter")
    if notice:
        print()
        print(notice)
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path, help="path to the workspace directory to create")
    parser.add_argument("--wfc", default=None, help="the `wfc` executable (else $WFC_BIN, else PATH)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    wfc_bin = _resolve_wfc(args.wfc)
    return create_workspace(args.workspace.resolve(), wfc_bin)


if __name__ == "__main__":
    raise SystemExit(main())

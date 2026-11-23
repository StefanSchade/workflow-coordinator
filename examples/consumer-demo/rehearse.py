#!/usr/bin/env python3
"""Run one complete, offline, scripted WFC Run in a disposable demo workspace.

Standard library only. Drives the real installed ``wfc`` (the same
resolution as ``make_workspace.py``: ``--wfc``, else ``$WFC_BIN``, else
``wfc`` on ``PATH``) against the kit's own scripted provider
(``scripted_provider.py``); never an internal shortcut, and never a real
provider. A rehearsal is clearly labelled: the banner below is printed
before and after WFC's own output, and the model name bound to every Hop is
``rehearsal-no-model``. See
``docs/backlog/FTR-WFC-CONSUMER-DEMO-WORKSPACE.adoc`` ("rehearse.py") for the
specification this implements (slice S2).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

KIT_DIR = Path(__file__).resolve().parent
SCENARIOS_DIR = KIT_DIR / "scenarios"
SCRIPTED_PROVIDER = KIT_DIR / "scripted_provider.py"
MARKER_NAME = "WFC-DEMO-WORKSPACE"
REHEARSAL_MODEL = "rehearsal-no-model"
BANNER = (
    'REHEARSAL -- no model was called. A script played the worker and the '
    'reviewer. This shows WFC\'s control flow, commits and evidence, not '
    'what a model would do. The archived record names the model '
    '"rehearsal-no-model".'
)

_CLASSIFICATION_RE = re.compile(r"^CLASSIFICATION: (.+)$", re.MULTILINE)
# ``wfc run``'s own output carries the archive destination only inside its
# consolidated report's "archive     <destination_relative>[@<commit>]" line,
# never as a bare ``destination_relative:`` key (that is ``wfc status``'s).
_ARCHIVE_REPORT_LINE_RE = re.compile(r"^archive\s+(\S+)$", re.MULTILINE)


class PreconditionError(Exception):
    """A usage or precondition check failed; nothing was created or run."""


def _resolve_wfc(explicit: str | None) -> str:
    if explicit:
        return explicit
    from_env = os.environ.get("WFC_BIN")
    if from_env:
        return from_env
    return "wfc"


def _known_scenarios() -> list[str]:
    return sorted(path.stem for path in SCENARIOS_DIR.glob("*.json"))


def _check_preconditions(workspace: Path, scenario: str) -> Path:
    if not (workspace / MARKER_NAME).is_file():
        raise PreconditionError(
            f"{workspace} is not a demo workspace (missing {MARKER_NAME}); run make_workspace.py first"
        )
    known = _known_scenarios()
    if scenario not in known:
        raise PreconditionError(f"unknown scenario {scenario!r}; known scenarios: {', '.join(known)}")
    worktree = workspace / f"greeter-run-{scenario}"
    if worktree.exists():
        raise PreconditionError(
            f"{worktree} already exists; a checkout holds one lifecycle, so create a new workspace "
            "(make_workspace.py) to rehearse this scenario again"
        )
    return worktree


def _create_worktree(workspace: Path, worktree: Path, scenario: str) -> None:
    result = subprocess.run(
        (
            "git", "-C", str(workspace / "greeter"), "worktree", "add",
            str(worktree), "-b", f"demo/{scenario}", "main",
        ),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise PreconditionError(f"`git worktree add` failed: {result.stdout.strip()}\n{result.stderr.strip()}")


def _copy_script(workspace: Path, scenario: str) -> Path:
    destination_dir = workspace / "rehearsal-home" / "scripts"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{scenario}.json"
    shutil.copyfile(SCENARIOS_DIR / f"{scenario}.json", destination)
    return destination


def _rehearsal_env(workspace: Path, script_path: Path) -> dict[str, str]:
    home = workspace / "rehearsal-home"
    tmp = home / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["TMPDIR"] = str(tmp)
    env["WFC_DEMO_SCRIPT"] = str(script_path)
    for name in ("XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        env.pop(name, None)
    return env


def _run_argv(wfc_bin: str, workspace: Path, worktree: Path, scenario: str, roles: list[str]) -> list[str]:
    argv = [
        wfc_bin, "run",
        "--repository", str(worktree),
        "--request", str(workspace / "requests" / f"{scenario}.request.yaml"),
        "--executable", str(SCRIPTED_PROVIDER),
    ]
    for role in roles:
        argv += [
            "--execution-override", f"{role}.adapter=codex_cli",
            "--execution-override", f"{role}.model={REHEARSAL_MODEL}",
            "--execution-override", f"{role}.reasoning=medium",
        ]
    argv += ["--hop-timeout-seconds", "60", "--yes"]
    return argv


def _print_command(label: str) -> None:
    print(f"$ {label}")


def _attempt_dir_from_run_output(workspace: Path, stdout: str) -> Path | None:
    match = _ARCHIVE_REPORT_LINE_RE.search(stdout)
    if match is None:
        return None
    destination_relative = match.group(1).split("@", 1)[0]
    return workspace / "greeter-evidence" / destination_relative


def _run_inspection_commands(wfc_bin: str, workspace: Path, worktree: Path, env: dict[str, str], run_stdout: str) -> None:
    git_log_argv = ["git", "-C", str(worktree), "log", "--format=%h %an %s"]
    _print_command(f"git -C {worktree} log --format='%h %an %s'")
    log = subprocess.run(git_log_argv, capture_output=True, text=True)
    sys.stdout.write(log.stdout)
    sys.stdout.write(log.stderr)

    attempt_dir = _attempt_dir_from_run_output(workspace, run_stdout)
    if attempt_dir is not None:
        report_argv = [wfc_bin, "report", str(attempt_dir)]
        _print_command(f"wfc report {attempt_dir}")
        report = subprocess.run(report_argv, capture_output=True, text=True)
        sys.stdout.write(report.stdout)
        sys.stdout.write(report.stderr)
    else:
        print("(no archived Attempt to report)")

    status_argv = [wfc_bin, "status", "--repository", str(worktree)]
    _print_command(f"HOME={env['HOME']} wfc status --repository {worktree}")
    status = subprocess.run(status_argv, capture_output=True, text=True, env=env)
    sys.stdout.write(status.stdout)
    sys.stdout.write(status.stderr)


def rehearse(workspace: Path, scenario: str, wfc_bin: str) -> int:
    try:
        worktree = _check_preconditions(workspace, scenario)
    except PreconditionError as error:
        print(f"rehearse.py: {error}", file=sys.stderr)
        return 2

    scenario_data = json.loads((SCENARIOS_DIR / f"{scenario}.json").read_text(encoding="utf-8"))
    roles = scenario_data["roles"]
    expected_exit_code = scenario_data["expected_exit_code"]
    expected_classification = scenario_data["expected_classification"]

    try:
        _create_worktree(workspace, worktree, scenario)
        script_path = _copy_script(workspace, scenario)
    except PreconditionError as error:
        print(f"rehearse.py: {error}", file=sys.stderr)
        return 2

    env = _rehearsal_env(workspace, script_path)
    argv = _run_argv(wfc_bin, workspace, worktree, scenario, roles)

    print(BANNER)
    run = subprocess.run(argv, capture_output=True, text=True, env=env)
    sys.stdout.write(run.stdout)
    sys.stdout.write(run.stderr)
    print(BANNER)

    _run_inspection_commands(wfc_bin, workspace, worktree, env, run.stdout)

    match = _CLASSIFICATION_RE.search(run.stdout)
    actual_classification = match.group(1) if match else None
    classification_ok = actual_classification is None or actual_classification == expected_classification
    if run.returncode == expected_exit_code and classification_ok:
        return 0

    print(
        f"rehearse.py: expected exit code {expected_exit_code}, classification "
        f"{expected_classification!r}; got exit code {run.returncode}, classification "
        f"{actual_classification!r} -- the rehearsal no longer matches this WFC version.",
        file=sys.stderr,
    )
    return 1


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path, help="path to an existing demo workspace")
    parser.add_argument("scenario", help="one of: " + ", ".join(_known_scenarios()))
    parser.add_argument("--wfc", default=None, help="the `wfc` executable (else $WFC_BIN, else PATH)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    wfc_bin = _resolve_wfc(args.wfc)
    return rehearse(args.workspace.resolve(), args.scenario, wfc_bin)


if __name__ == "__main__":
    raise SystemExit(main())

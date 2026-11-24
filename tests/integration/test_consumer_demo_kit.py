"""examples/consumer-demo/ -- Consumer Demo Workspace kit, slice S1.

Exercises ``make_workspace.py`` and the installed ``wfc`` against the kit's
own ``seed/`` and ``requests/`` templates. No test calls a provider or the
network; every test builds its workspace under ``tmp_path``, sets ``HOME``
to a throwaway directory, and puts a ``wfc`` wrapper for the working tree on
``PATH``, exactly as ``tests/integration/test_beta_e2e_fake_provider.py``
does with ``tests/fixtures/bp9/wfc_entry.py``. See
``docs/backlog/FTR-WFC-CONSUMER-DEMO-WORKSPACE.adoc`` for the specification.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "bp9"
WFC_ENTRY = FIXTURES / "wfc_entry.py"
KIT_DIR = REPOSITORY_ROOT / "examples" / "consumer-demo"
MAKE_WORKSPACE = KIT_DIR / "make_workspace.py"
REHEARSE = KIT_DIR / "rehearse.py"
SCRIPTED_PROVIDER = KIT_DIR / "scripted_provider.py"
SCENARIOS_DIR = KIT_DIR / "scenarios"
TASK_REQUEST_NAMES = ("t1-hello-page", "t2-farewell", "t3-scope-stop")
SCENARIO_NAMES = ("t1-hello-page", "t2-farewell", "t3-scope-stop", "t3-probe-refusal")
REHEARSAL_BANNER = (
    'REHEARSAL -- no model was called. A script played the worker and the '
    'reviewer. This shows WFC\'s control flow, commits and evidence, not '
    'what a model would do. The archived record names the model '
    '"rehearsal-no-model".'
)


def _wfc_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Put a literal ``wfc`` on ``PATH`` wrapping the working tree's entry shim."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    wrapper = bin_dir / "wfc"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable!r} {str(WFC_ENTRY)!r} \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


@pytest.fixture
def throwaway_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home


def _disable_git_identity_fallback(home: Path) -> None:
    """BUG-WFC-CONSUMER-DEMO-KIT-GIT-IDENTITY: disable Git's account-name
    fallback in ``home``'s own global configuration file, so a missing
    identity fails the same way on every host and no longer depends on the
    password database -- never a ``GIT_*`` environment variable, which
    WFC's trusted Git execution strips before every commit it makes."""
    (home / ".gitconfig").write_text("[user]\n\tuseConfigOnly = true\n", encoding="utf-8")


def _run_make_workspace(workspace: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(MAKE_WORKSPACE), str(workspace), *extra],
        capture_output=True, text=True,
    )


def _run_rehearse(workspace: Path, scenario: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REHEARSE), str(workspace), scenario, *extra],
        capture_output=True, text=True,
    )


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *args), capture_output=True, text=True, check=True,
    ).stdout


# ==========================================================================
# Workspace (AC-1).
# ==========================================================================

def test_workspace_layout_two_commits_clean_tree_no_remote_and_check_passes(
    tmp_path: Path, throwaway_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    _wfc_on_path(tmp_path, monkeypatch)
    workspace = tmp_path / "ws"
    result = _run_make_workspace(workspace)
    assert result.returncode == 0, result.stdout + result.stderr

    assert (workspace / "WFC-DEMO-WORKSPACE").is_file()
    greeter = workspace / "greeter"
    assert (greeter / ".wfc" / "config.yaml").is_file()
    assert (workspace / "greeter-evidence").is_dir()
    assert (workspace / "requests").is_dir()
    assert (workspace / "rehearsal-home").is_dir()

    log = _git(greeter, "log", "--format=%s").strip().splitlines()
    assert log == ["chore: add WFC Consumer Config", "chore: seed demo repository"]
    assert _git(greeter, "status", "--porcelain") == ""
    assert _git(greeter, "remote").strip() == ""

    check = subprocess.run(["sh", "checks/check.sh"], cwd=greeter, capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr
    assert _git(greeter, "status", "--porcelain") == ""


def test_workspace_renders_every_request_template_with_the_guidance_commit_substituted(
    tmp_path: Path, throwaway_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    _wfc_on_path(tmp_path, monkeypatch)
    workspace = tmp_path / "ws"
    result = _run_make_workspace(workspace)
    assert result.returncode == 0, result.stdout + result.stderr

    rendered_names = {path.name for path in (workspace / "requests").iterdir()}
    template_names = {path.name for path in (KIT_DIR / "requests").iterdir()}
    assert rendered_names == template_names

    guidance_commit = _git(
        workspace / "greeter", "log", "-1", "--format=%H", "--", ".wfc/governance/repository-policy.md"
    ).strip()
    for name in template_names:
        text = (workspace / "requests" / name).read_text(encoding="utf-8")
        assert "{{GUIDANCE_COMMIT}}" not in text
        if name.endswith(".request.yaml"):
            assert guidance_commit in text


def test_workspace_creation_and_evidence_identity_do_not_depend_on_a_host_git_identity(
    tmp_path: Path, throwaway_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    # BUG-WFC-CONSUMER-DEMO-KIT-GIT-IDENTITY (AC-1, AC-2): disabling Git's
    # account-name fallback reproduces the hosted failure on every host, not
    # only a runner whose account has no name in the password database.
    _wfc_on_path(tmp_path, monkeypatch)
    _disable_git_identity_fallback(throwaway_home)
    workspace = tmp_path / "ws"

    result = _run_make_workspace(workspace)

    assert result.returncode == 0, result.stdout + result.stderr
    greeter = workspace / "greeter"
    evidence = workspace / "greeter-evidence"
    assert evidence.is_dir()
    assert (workspace / "requests").is_dir()
    assert (workspace / "rehearsal-home").is_dir()
    assert _git(evidence, "status", "--porcelain") == ""
    assert len(_git(evidence, "log", "--format=%H").strip().splitlines()) == 1
    assert _git(evidence, "config", "user.name").strip() == _git(greeter, "config", "user.name").strip()
    assert _git(evidence, "config", "user.email").strip() == _git(greeter, "config", "user.email").strip()


# ==========================================================================
# Preflight refusals (AC-2): each returns 2 and creates nothing.
# ==========================================================================

def test_preflight_refuses_a_non_empty_target(
    tmp_path: Path, throwaway_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    _wfc_on_path(tmp_path, monkeypatch)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "already-here.txt").write_text("x", encoding="utf-8")

    result = _run_make_workspace(workspace)

    assert result.returncode == 2
    assert "is not empty" in result.stderr
    assert [path.name for path in workspace.iterdir()] == ["already-here.txt"]


def test_preflight_refuses_a_target_inside_a_git_work_tree(
    tmp_path: Path, throwaway_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    _wfc_on_path(tmp_path, monkeypatch)
    enclosing_repo = tmp_path / "enclosing-repo"
    enclosing_repo.mkdir()
    subprocess.run(["git", "init", "-q", str(enclosing_repo)], check=True)
    workspace = enclosing_repo / "demo"

    result = _run_make_workspace(workspace)

    assert result.returncode == 2
    assert "inside a Git work tree" in result.stderr
    assert not workspace.exists()


def test_preflight_refuses_when_wfc_is_not_found(tmp_path: Path, throwaway_home: Path):
    # Deliberately no `_wfc_on_path`: a bare `wfc` must not resolve on PATH here,
    # and `--wfc` names a path with nothing there either.
    workspace = tmp_path / "ws"
    missing_bin = tmp_path / "no-such-wfc"

    result = _run_make_workspace(workspace, "--wfc", str(missing_bin))

    assert result.returncode == 2
    assert "could not run" in result.stderr
    assert not workspace.exists()


def test_preflight_refuses_a_target_whose_parent_directory_does_not_exist(
    tmp_path: Path, throwaway_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    # O-1 (A2/S1 broad review): a missing parent must end in a one-line
    # message, not a traceback, and must create nothing outside the named
    # workspace -- so the kit never creates the missing parent either.
    _wfc_on_path(tmp_path, monkeypatch)
    workspace = tmp_path / "does-not-exist-yet" / "ws"

    result = _run_make_workspace(workspace)

    assert result.returncode == 2
    assert "parent directory does not exist" in result.stderr
    assert not (tmp_path / "does-not-exist-yet").exists()


# ==========================================================================
# Plan (AC-3): each of the three task requests previews cleanly.
# ==========================================================================

def _plan_args_for(requests_dir: Path, greeter: Path, name: str) -> list[str]:
    request = yaml.safe_load((requests_dir / f"{name}.request.yaml").read_text(encoding="utf-8"))
    args = [
        str(requests_dir / f"{name}.task.md"),
        "--repository", str(greeter),
        "--commission-id", request["commission_id"],
        "--work-item-id", request["work_item_id"],
        "--authority-source", request["authority_source"],
        "--program", f"{request['program']['id']}@{request['program']['revision']}",
        "--evidence-timeout", str(request["evidence"]["timeout_seconds"]),
        "--max-autonomous-hops", str(request["max_autonomous_hops"]),
        "--guidance-path", request["guidance"]["path"],
        "--guidance-commit", request["guidance"]["source_commit"],
    ]
    for scope in request["scope"]:
        args += ["--scope", scope]
    for token in request["evidence"]["argv"]:
        args += ["--evidence-argv", token]
    for role, persona in request["personas"].items():
        args += [
            "--persona", f"{role}={persona}",
            "--execution-override", f"{role}.adapter=codex_cli",
            "--execution-override", f"{role}.model=demo-model",
            "--execution-override", f"{role}.reasoning=medium",
        ]
    for phase, paths in request.get("durable_artifacts", {}).items():
        for path in paths:
            args += ["--durable-artifact", f"{phase}={path}"]
    return args


@pytest.mark.parametrize("name", TASK_REQUEST_NAMES)
def test_plan_exits_zero_for_each_task_request_in_a_fresh_worktree(
    tmp_path: Path, throwaway_home: Path, monkeypatch: pytest.MonkeyPatch, name: str,
):
    _wfc_on_path(tmp_path, monkeypatch)
    workspace = tmp_path / "ws"
    created = _run_make_workspace(workspace)
    assert created.returncode == 0, created.stdout + created.stderr

    args = _plan_args_for(workspace / "requests", workspace / "greeter", name)
    plan = subprocess.run(
        [sys.executable, str(WFC_ENTRY), "plan", *args], capture_output=True, text=True,
    )

    assert plan.returncode == 0, plan.stdout + plan.stderr
    assert "nothing was run or written by this preview" in plan.stdout


# ==========================================================================
# Containment (AC-8, as far as S1 reaches).
# ==========================================================================

def test_throwaway_home_stays_empty_after_workspace_creation_and_every_plan(
    tmp_path: Path, throwaway_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    _wfc_on_path(tmp_path, monkeypatch)
    workspace = tmp_path / "ws"
    created = _run_make_workspace(workspace)
    assert created.returncode == 0, created.stdout + created.stderr

    for name in TASK_REQUEST_NAMES:
        args = _plan_args_for(workspace / "requests", workspace / "greeter", name)
        plan = subprocess.run(
            [sys.executable, str(WFC_ENTRY), "plan", *args], capture_output=True, text=True,
        )
        assert plan.returncode == 0, plan.stdout + plan.stderr

    assert list(throwaway_home.iterdir()) == []


# ==========================================================================
# Boundary: every kit module present imports only the standard library.
# ==========================================================================

def test_kit_modules_import_only_the_standard_library():
    modules = sorted(KIT_DIR.glob("*.py"))
    assert modules  # the kit ships at least one module.
    stdlib_names = sys.stdlib_module_names
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in imported:
                assert name in stdlib_names, f"{path.name} imports non-stdlib module: {name}"


# ==========================================================================
# Scenarios (AC-4 to AC-7).
# ==========================================================================

@pytest.mark.parametrize("scenario", SCENARIO_NAMES)
def test_rehearse_each_scenario_returns_zero_with_the_expected_classification_and_exit_code(
    tmp_path: Path, throwaway_home: Path, monkeypatch: pytest.MonkeyPatch, scenario: str,
):
    _wfc_on_path(tmp_path, monkeypatch)
    workspace = tmp_path / "ws"
    created = _run_make_workspace(workspace)
    assert created.returncode == 0, created.stdout + created.stderr

    scenario_data = json.loads((SCENARIOS_DIR / f"{scenario}.json").read_text(encoding="utf-8"))
    rehearsed = _run_rehearse(workspace, scenario)

    # ``rehearse.py`` itself returns 0 only when WFC's own exit code equals
    # ``expected_exit_code`` and (when present) its ``CLASSIFICATION:`` line
    # equals ``expected_classification`` -- see the non-zero-exit assertion
    # it would otherwise print to stderr.
    assert rehearsed.returncode == 0, rehearsed.stdout + rehearsed.stderr
    # The banner brackets WFC's own ``wfc run`` output; a later
    # ``CLASSIFICATION:`` line from the trailing ``wfc status`` inspection
    # command can legitimately differ (for example after a probe refusal,
    # which never reaches a classified lifecycle state) and must not be
    # mistaken for ``wfc run``'s own result.
    banner_parts = rehearsed.stdout.split(REHEARSAL_BANNER)
    assert len(banner_parts) >= 3
    run_output = banner_parts[1]
    classification = re.search(r"^CLASSIFICATION: (.+)$", run_output, re.MULTILINE)
    if classification is not None:
        assert classification.group(1) == scenario_data["expected_classification"]


@pytest.mark.parametrize("scenario", SCENARIO_NAMES)
def test_rehearsal_does_not_depend_on_a_host_git_identity(
    tmp_path: Path, throwaway_home: Path, monkeypatch: pytest.MonkeyPatch, scenario: str,
):
    # BUG-WFC-CONSUMER-DEMO-KIT-GIT-IDENTITY (AC-3): the fallback is disabled
    # for *both* HOMEs in play -- the one `make_workspace.py` ran under, and
    # the rehearsal's own `rehearsal-home`, which `rehearse.py` points `HOME`
    # at for the `wfc run` it drives.
    _wfc_on_path(tmp_path, monkeypatch)
    _disable_git_identity_fallback(throwaway_home)
    workspace = tmp_path / "ws"
    created = _run_make_workspace(workspace)
    assert created.returncode == 0, created.stdout + created.stderr

    _disable_git_identity_fallback(workspace / "rehearsal-home")

    scenario_data = json.loads((SCENARIOS_DIR / f"{scenario}.json").read_text(encoding="utf-8"))
    rehearsed = _run_rehearse(workspace, scenario)

    assert rehearsed.returncode == 0, rehearsed.stdout + rehearsed.stderr
    banner_parts = rehearsed.stdout.split(REHEARSAL_BANNER)
    assert len(banner_parts) >= 3
    run_output = banner_parts[1]
    classification = re.search(r"^CLASSIFICATION: (.+)$", run_output, re.MULTILINE)
    if classification is not None:
        assert classification.group(1) == scenario_data["expected_classification"]

    destination_relative = _destination_relative_from(rehearsed.stdout)
    if scenario == "t3-probe-refusal":
        assert destination_relative is None
    else:
        assert destination_relative is not None
        evidence = workspace / "greeter-evidence"
        commit = _git(evidence, "log", "-1", "--format=%H", "--", destination_relative).strip()
        assert commit != ""


def _destination_relative_from(stdout: str) -> str | None:
    # ``wfc run``'s own output carries the consolidated report's own
    # "archive     <destination_relative>[@<commit>]" line on a successful
    # archive -- never a bare ``destination_relative:`` key (that is ``wfc
    # status``'s separate inspection-command output).
    match = re.search(r"^archive\s+(\S+)$", stdout, re.MULTILINE)
    return match.group(1).split("@", 1)[0] if match else None


def test_t2_farewell_end_state_matches_ac5(
    tmp_path: Path, throwaway_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    _wfc_on_path(tmp_path, monkeypatch)
    workspace = tmp_path / "ws"
    created = _run_make_workspace(workspace)
    assert created.returncode == 0, created.stdout + created.stderr

    rehearsed = _run_rehearse(workspace, "t2-farewell")
    assert rehearsed.returncode == 0, rehearsed.stdout + rehearsed.stderr

    worktree = workspace / "greeter-run-t2-farewell"
    commits = _git(worktree, "log", "--format=%H", "main..HEAD").strip().splitlines()
    assert len(commits) == 6

    changed = sorted(filter(None, _git(worktree, "diff", "--name-only", "main..HEAD").splitlines()))
    request = yaml.safe_load((workspace / "requests" / "t2-farewell.request.yaml").read_text(encoding="utf-8"))
    commission_prefix = f".wfc/governance/{request['commission_id']}/"
    outside_commission = {path for path in changed if not path.startswith(commission_prefix)}
    assert outside_commission == set(request["scope"])
    assert any(path.startswith(commission_prefix) for path in changed)

    greet = subprocess.run(
        [sys.executable, "-m", "greeter", "Ada"], cwd=worktree, capture_output=True, text=True,
    )
    assert greet.returncode == 0, greet.stdout + greet.stderr
    assert greet.stdout.strip() == "Hello, Ada!"

    assert "(no archived Attempt to report)" not in rehearsed.stdout
    assert "$ wfc report " in rehearsed.stdout
    destination_relative = _destination_relative_from(rehearsed.stdout)
    assert destination_relative is not None
    attempt_dir = workspace / "greeter-evidence" / destination_relative
    audit = json.loads((attempt_dir / "audit.json").read_text(encoding="utf-8"))
    resolved_findings = [finding for finding in audit["findings"] if finding["state"] == "resolved"]
    assert len(resolved_findings) == 1
    assert len(audit["hops"]) == 5
    assert all(hop["model"] == "rehearsal-no-model" for hop in audit["hops"])


def test_t3_scope_stop_and_probe_refusal_end_states_match_ac6(
    tmp_path: Path, throwaway_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    _wfc_on_path(tmp_path, monkeypatch)
    workspace = tmp_path / "ws"
    created = _run_make_workspace(workspace)
    assert created.returncode == 0, created.stdout + created.stderr

    stop = _run_rehearse(workspace, "t3-scope-stop")
    assert stop.returncode == 0, stop.stdout + stop.stderr
    stop_worktree = workspace / "greeter-run-t3-scope-stop"
    stop_request = yaml.safe_load((workspace / "requests" / "t3-scope-stop.request.yaml").read_text(encoding="utf-8"))
    stop_commission_prefix = f".wfc/governance/{stop_request['commission_id']}/"
    stop_changed = sorted(filter(None, _git(stop_worktree, "diff", "--name-only", "main..HEAD").splitlines()))
    assert stop_changed  # the Commission commit itself changed something.
    assert all(path.startswith(stop_commission_prefix) for path in stop_changed)

    probe = _run_rehearse(workspace, "t3-probe-refusal")
    assert probe.returncode == 0, probe.stdout + probe.stderr
    probe_worktree = workspace / "greeter-run-t3-probe-refusal"
    assert _git(probe_worktree, "rev-parse", "HEAD").strip() == _git(probe_worktree, "rev-parse", "main").strip()
    assert _destination_relative_from(probe.stdout) is None


def test_second_rehearsal_of_the_same_scenario_returns_2_and_changes_nothing(
    tmp_path: Path, throwaway_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    _wfc_on_path(tmp_path, monkeypatch)
    workspace = tmp_path / "ws"
    created = _run_make_workspace(workspace)
    assert created.returncode == 0, created.stdout + created.stderr

    first = _run_rehearse(workspace, "t1-hello-page")
    assert first.returncode == 0, first.stdout + first.stderr
    worktree = workspace / "greeter-run-t1-hello-page"
    head_before = _git(worktree, "rev-parse", "HEAD").strip()

    second = _run_rehearse(workspace, "t1-hello-page")
    assert second.returncode == 2
    assert _git(worktree, "rev-parse", "HEAD").strip() == head_before
    worktrees_after = sorted(path.name for path in workspace.glob("greeter-run-*"))
    assert worktrees_after == ["greeter-run-t1-hello-page"]


def test_rehearse_refuses_when_wfc_is_not_found_and_a_retry_with_a_working_wfc_still_succeeds(
    tmp_path: Path, throwaway_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    # F001 (S2 broad review): an unresolvable `wfc` must be caught by a
    # preflight check before the worktree and branch are created, so the
    # scenario's single lifecycle survives for a retry with a working `wfc`.
    _wfc_on_path(tmp_path, monkeypatch)
    workspace = tmp_path / "ws"
    created = _run_make_workspace(workspace)
    assert created.returncode == 0, created.stdout + created.stderr

    missing_bin = tmp_path / "no-such-wfc"
    failed = _run_rehearse(workspace, "t1-hello-page", "--wfc", str(missing_bin))
    assert failed.returncode == 2
    assert "could not run" in failed.stderr
    assert not (workspace / "greeter-run-t1-hello-page").exists()
    assert _git(workspace / "greeter", "branch", "--list", "demo/t1-hello-page").strip() == ""

    retried = _run_rehearse(workspace, "t1-hello-page")
    assert retried.returncode == 0, retried.stdout + retried.stderr


def test_throwaway_home_stays_empty_through_a_full_rehearsal_sequence(
    tmp_path: Path, throwaway_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    _wfc_on_path(tmp_path, monkeypatch)
    workspace = tmp_path / "ws"
    created = _run_make_workspace(workspace)
    assert created.returncode == 0, created.stdout + created.stderr

    for scenario in SCENARIO_NAMES:
        rehearsed = _run_rehearse(workspace, scenario)
        assert rehearsed.returncode == 0, rehearsed.stdout + rehearsed.stderr

    assert list(throwaway_home.iterdir()) == []


# ==========================================================================
# Containment: the scripted provider's safety refusals (unit, no WFC Run).
# ==========================================================================

def _write_provider_script(tmp_path: Path, steps: list[dict]) -> Path:
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"steps": steps}), encoding="utf-8")
    return script


def _run_provider(cd: Path, output: Path, script: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "WFC_DEMO_SCRIPT": str(script)}
    return subprocess.run(
        [sys.executable, str(SCRIPTED_PROVIDER), "--cd", str(cd), "--output-last-message", str(output)],
        input="phase=implement\n\n", capture_output=True, text=True, env=env,
    )


def test_scripted_provider_refuses_an_absolute_write_path(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    script = _write_provider_script(tmp_path, [{"write": {str(tmp_path / "outside.txt"): "x"}}])
    output = tmp_path / "output.json"

    result = _run_provider(repository, output, script)

    assert result.returncode != 0
    assert "absolute" in result.stderr
    assert not (tmp_path / "outside.txt").exists()


def test_scripted_provider_refuses_a_dotdot_write_path(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    script = _write_provider_script(tmp_path, [{"write": {"../escape.txt": "x"}}])
    output = tmp_path / "output.json"

    result = _run_provider(repository, output, script)

    assert result.returncode != 0
    assert "'..'" in result.stderr
    assert not (tmp_path / "escape.txt").exists()


def test_scripted_provider_refuses_a_write_path_resolving_outside_cd(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repository / "link").symlink_to(outside)
    script = _write_provider_script(tmp_path, [{"write": {"link/escape.txt": "x"}}])
    output = tmp_path / "output.json"

    result = _run_provider(repository, output, script)

    assert result.returncode != 0
    assert "outside --cd" in result.stderr
    assert not (outside / "escape.txt").exists()

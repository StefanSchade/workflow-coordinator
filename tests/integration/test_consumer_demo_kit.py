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
import os
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
TASK_REQUEST_NAMES = ("t1-hello-page", "t2-farewell", "t3-scope-stop")


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


def _run_make_workspace(workspace: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(MAKE_WORKSPACE), str(workspace), *extra],
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
    assert not workspace.exists()


def test_preflight_refuses_when_wfc_is_not_found(tmp_path: Path, throwaway_home: Path):
    # Deliberately no `_wfc_on_path`: a bare `wfc` must not resolve on PATH here,
    # and `--wfc` names a path with nothing there either.
    workspace = tmp_path / "ws"
    missing_bin = tmp_path / "no-such-wfc"

    result = _run_make_workspace(workspace, "--wfc", str(missing_bin))

    assert result.returncode == 2
    assert not workspace.exists()


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
# Boundary: the kit's own module(s) import only the standard library, and
# no slice-S2 file exists yet.
# ==========================================================================

def test_kit_modules_import_only_the_standard_library():
    modules = sorted(KIT_DIR.glob("*.py"))
    assert [path.name for path in modules] == ["make_workspace.py"]
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


def test_slice_s2_files_do_not_exist_yet():
    assert not (KIT_DIR / "scripted_provider.py").exists()
    assert not (KIT_DIR / "rehearse.py").exists()
    assert not (KIT_DIR / "scenarios").exists()

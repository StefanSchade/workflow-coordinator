"""BP3 ``wfc init``/``wfc plan`` coverage against a real non-Python repository.

Every fixture repository here is a disposable, from-scratch Git repository
with no WFC governance/self-development files and no Python-project
assumptions, exactly like ``tests/unit/test_consumer_compiler.py``'s BP2b
fixtures. ``main`` is dispatched through ``frontdoor.cli.main`` -- the real
installed CLI path -- not a stub simulation of either command.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from workflow_coordinator.frontdoor.cli import main


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *args), text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """A from-scratch, non-Python, non-WFC consumer repository."""

    repo = tmp_path / "consumer-repo"
    repo.mkdir()
    subprocess.run(("git", "init", "-q", str(repo)), check=True)
    git(repo, "config", "user.email", "consumer@example.invalid")
    git(repo, "config", "user.name", "Consumer test")
    (repo / "GUIDANCE.md").write_text("Keep changes small and reviewed.\n", encoding="utf-8")
    (repo / "checks").mkdir()
    check = repo / "checks/check.sh"
    check.write_text("#!/bin/sh\necho marker > MARKER_WAS_RUN\nexit 0\n", encoding="utf-8")
    check.chmod(0o755)
    (repo / "src").mkdir()
    (repo / "src/app.txt").write_text("hello\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests/app_test.txt").write_text("hello\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: seed disposable non-Python consumer repository")
    assert not list(repo.rglob("*.py")) and not (repo / "pyproject.toml").exists()
    assert not (repo / "governance").exists()
    return repo


_TASK_TEXT = """# Bounded plan-preview smoke task

Preview a bounded task against this neutral consumer repository.

## Acceptance

The preview prints a summary and leaves the repository byte-identical.

## Withheld authority

No project command, provider call, Commission write or Git mutation.

## Default escalation

Stop if a required input is missing or unclear.
"""


def _tree_snapshot(repository: Path) -> dict[str, tuple[bytes, int]]:
    """Every worktree path below ``repository``, tracked or not, excluding ``.git``."""

    snapshot: dict[str, tuple[bytes, int]] = {}
    for path in repository.rglob("*"):
        if ".git" in path.relative_to(repository).parts:
            continue
        if path.is_file():
            relative = path.relative_to(repository).as_posix()
            snapshot[relative] = (path.read_bytes(), path.stat().st_mode)
    return snapshot


def _index_bytes(repository: Path) -> bytes:
    index = repository / ".git" / "index"
    return index.read_bytes() if index.exists() else b""


def _run(args: list[str]) -> tuple[int, str, str]:
    import io
    import contextlib

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(args)
    return code, out.getvalue(), err.getvalue()


def test_init_writes_only_the_consumer_config_and_never_commits(repository: Path):
    head = git(repository, "rev-parse", "HEAD")
    code, out, err = _run([
        "init", "--repository", str(repository), "--scope-ceiling", "src", "--scope-ceiling", "tests",
    ])
    assert code == 0, err
    config_path = repository / ".wfc" / "config.yaml"
    assert config_path.exists()
    assert git(repository, "rev-parse", "HEAD") == head
    status = git(repository, "status", "--porcelain", "--untracked-files=all")
    assert ".wfc/config.yaml" in status
    assert git(repository, "diff", "--cached", "--name-only") == ""

    # Idempotent refusal: a second call must not overwrite the existing config.
    code, out, err = _run(["init", "--repository", str(repository), "--scope-ceiling", "src"])
    assert code != 0
    assert "refusing to overwrite" in err


def _plan_args(repository: Path, *, task_path: Path) -> list[str]:
    return [
        "plan", str(task_path),
        "--repository", str(repository),
        "--scope", "src", "--scope", "tests",
        "--commission-id", "consumer-plan-smoke",
        "--work-item-id", "WI-CONSUMER-PLAN-SMOKE",
        "--authority-source", "Maintainer authorizes this bounded preview only.",
        "--program", "simple@v1",
        "--persona", "worker=Bounded worker persona.",
        "--evidence-argv", "sh", "--evidence-argv", "checks/check.sh",
        "--guidance-path", "GUIDANCE.md",
        "--execution-override", "worker.adapter=codex_cli",
        "--execution-override", "worker.model=model-a",
        "--execution-override", "worker.reasoning=medium",
    ]


def test_plan_previews_without_any_mutation_and_without_running_the_project_command(repository: Path):
    _run(["init", "--repository", str(repository), "--scope-ceiling", "src", "--scope-ceiling", "tests"])
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "chore: commit Consumer Config")

    task_path = repository / "task.md"
    task_path.write_text(_TASK_TEXT, encoding="utf-8")

    # A hostile worktree hook must never fire: plan performs no commit/checkout.
    git(repository, "config", "core.hooksPath", ".githooks")
    hook_dir = repository / ".githooks"
    hook_dir.mkdir()
    hook = hook_dir / "post-checkout"
    hook.write_text("#!/bin/sh\necho hook > HOOK_WAS_RUN\n", encoding="utf-8")
    hook.chmod(0o755)

    head_before = git(repository, "rev-parse", "HEAD")
    tree_before = _tree_snapshot(repository)
    index_before = _index_bytes(repository)

    code, out, err = _run(_plan_args(repository, task_path=task_path))
    assert code == 0, err
    assert out and "plan preview" in out
    assert "consumer-plan-smoke" in out
    assert "WI-CONSUMER-PLAN-SMOKE" in out

    assert git(repository, "rev-parse", "HEAD") == head_before
    assert _index_bytes(repository) == index_before
    assert _tree_snapshot(repository) == tree_before
    assert not (repository / "MARKER_WAS_RUN").exists()
    assert not (repository / "HOOK_WAS_RUN").exists()
    assert not (repository / ".wfc" / "governance").exists()


def test_plan_output_is_stable_across_repeated_calls(repository: Path):
    _run(["init", "--repository", str(repository), "--scope-ceiling", "src", "--scope-ceiling", "tests"])
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "chore: commit Consumer Config")
    task_path = repository / "task.md"
    task_path.write_text(_TASK_TEXT, encoding="utf-8")

    code_a, out_a, _ = _run(_plan_args(repository, task_path=task_path))
    code_b, out_b, _ = _run(_plan_args(repository, task_path=task_path))
    assert code_a == code_b == 0
    assert out_a == out_b


def test_plan_reports_missing_config_clearly(repository: Path):
    task_path = repository / "task.md"
    task_path.write_text(_TASK_TEXT, encoding="utf-8")
    code, out, err = _run(_plan_args(repository, task_path=task_path))
    assert code != 0
    assert "Consumer Config" in err
    assert "wfc init" in err


def test_plan_reports_missing_persona_as_an_argparse_error(repository: Path, capsys):
    _run(["init", "--repository", str(repository), "--scope-ceiling", "src", "--scope-ceiling", "tests"])
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "chore: commit Consumer Config")
    task_path = repository / "task.md"
    task_path.write_text(_TASK_TEXT, encoding="utf-8")
    reduced = []
    skip_next = False
    for part in _plan_args(repository, task_path=task_path):
        if skip_next:
            skip_next = False
            continue
        if part == "--persona":
            skip_next = True
            continue
        reduced.append(part)
    with pytest.raises(SystemExit) as excinfo:
        main(reduced)
    assert excinfo.value.code == 2
    assert "--persona" in capsys.readouterr().err


def test_plan_reports_missing_execution_binding_clearly(repository: Path):
    _run(["init", "--repository", str(repository), "--scope-ceiling", "src", "--scope-ceiling", "tests"])
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "chore: commit Consumer Config")
    task_path = repository / "task.md"
    task_path.write_text(_TASK_TEXT, encoding="utf-8")
    head_before = git(repository, "rev-parse", "HEAD")

    args = [
        part for part in _plan_args(repository, task_path=task_path)
        if not part.startswith("worker.") and part != "--execution-override"
    ]
    code, out, err = _run(args)
    assert code != 0
    assert "execution binding for role worker is missing" in err
    assert out == ""
    assert git(repository, "rev-parse", "HEAD") == head_before


def test_plan_reports_malformed_guidance_commit_clearly(repository: Path):
    _run(["init", "--repository", str(repository), "--scope-ceiling", "src", "--scope-ceiling", "tests"])
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "chore: commit Consumer Config")
    task_path = repository / "task.md"
    task_path.write_text(_TASK_TEXT, encoding="utf-8")
    code, out, err = _run([*_plan_args(repository, task_path=task_path), "--guidance-commit", "not-a-sha"])
    assert code != 0
    assert "40-character SHA" in err


def test_plan_matches_direct_bp2b_compile_and_resolve(repository: Path):
    from workflow_coordinator.commissioning import compile_commission
    from workflow_coordinator.frontdoor.config import consumer_config_from_yaml, resolve_execution
    from workflow_coordinator.frontdoor.plan import consumer_request_from_plan_inputs, task_document_from_markdown

    _run(["init", "--repository", str(repository), "--scope-ceiling", "src", "--scope-ceiling", "tests"])
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "chore: commit Consumer Config")
    head = git(repository, "rev-parse", "HEAD")
    task_path = repository / "task.md"
    task_path.write_text(_TASK_TEXT, encoding="utf-8")

    code, out, err = _run([*_plan_args(repository, task_path=task_path), "--guidance-commit", head])
    assert code == 0, err

    task = task_document_from_markdown(_TASK_TEXT)
    config = consumer_config_from_yaml((repository / ".wfc/config.yaml").read_text(encoding="utf-8"))
    request = consumer_request_from_plan_inputs(
        task=task, commission_id="consumer-plan-smoke", work_item_id="WI-CONSUMER-PLAN-SMOKE",
        authority_source="Maintainer authorizes this bounded preview only.", program="simple@v1",
        scope=["src", "tests"], personas=["worker=Bounded worker persona."],
        evidence_argv=["sh", "checks/check.sh"], evidence_timeout=900, max_autonomous_hops=5,
        durable_artifact=[], guidance_path="GUIDANCE.md", guidance_commit=head,
    )
    compiled = compile_commission(request, config, repository=repository)
    envelope = resolve_execution(
        config, {"worker": {"adapter": "codex_cli", "model": "model-a", "reasoning": "medium"}}, ("worker",)
    )

    for path, text in compiled.texts.items():
        import hashlib
        assert f"{path}  sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}" in out
    assert f"execution envelope digest: sha256:{envelope.digest}" in out

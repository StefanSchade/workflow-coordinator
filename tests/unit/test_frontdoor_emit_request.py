"""``wfc request`` (D1 Option B, WI-WFC-BETA2-O5-001): emits the Consumer
Request ``wfc plan`` only previews, through the identical shared input
resolution and request-construction path.

Fixtures mirror ``tests/unit/test_frontdoor_init_plan.py``: a disposable,
non-Python, non-WFC consumer repository, with ``main`` dispatched through
``frontdoor.cli.main`` -- the real installed CLI path.
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
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: seed disposable non-Python consumer repository")
    _run(["init", "--repository", str(repo), "--scope-ceiling", "src"])
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: commit Consumer Config")
    return repo


_TASK_TEXT = """# Bounded request-emission smoke task

Preview, then emit, a bounded task's Consumer Request against this neutral
consumer repository.

## Acceptance

The emitted request reproduces the previewed digests exactly.

## Withheld authority

No project command, provider call, Commission write or Git mutation.

## Default escalation

Stop if a required input is missing or unclear.
"""


def _tree_snapshot(repository: Path) -> dict[str, tuple[bytes, int]]:
    snapshot: dict[str, tuple[bytes, int]] = {}
    for path in repository.rglob("*"):
        if ".git" in path.relative_to(repository).parts:
            continue
        if path.is_file():
            relative = path.relative_to(repository).as_posix()
            snapshot[relative] = (path.read_bytes(), path.stat().st_mode)
    return snapshot


def _run(args: list[str]) -> tuple[int, str, str]:
    import io
    import contextlib

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(args)
    return code, out.getvalue(), err.getvalue()


def _shared_args(repository: Path, *, task_path: Path) -> list[str]:
    return [
        str(task_path),
        "--repository", str(repository),
        "--scope", "src",
        "--commission-id", "consumer-request-smoke",
        "--work-item-id", "WI-CONSUMER-REQUEST-SMOKE",
        "--authority-source", "Maintainer authorizes this bounded preview only.",
        "--program", "simple@v1",
        "--persona", "worker=Bounded worker persona.",
        "--evidence-argv", "sh", "--evidence-argv", "checks/check.sh",
        "--guidance-path", "GUIDANCE.md",
        "--execution-override", "worker.adapter=codex_cli",
        "--execution-override", "worker.model=model-a",
        "--execution-override", "worker.reasoning=medium",
    ]


def _digest_lines(text: str) -> list[str]:
    return [
        line for line in text.splitlines()
        if line.startswith("projected Commission manifest digest:") or line.startswith("execution envelope digest:")
    ]


def _write_task(repository: Path) -> Path:
    task_path = repository.parent / "task.md"
    task_path.write_text(_TASK_TEXT, encoding="utf-8")
    return task_path


class TestDigestEqualityBetweenPreviewAndEmission:
    """The central O5 acceptance test: ``wfc request`` prints the identical
    projected digests ``wfc plan`` prints for the same inputs, and the file
    it writes is exactly the request those digests were computed from."""

    def test_plan_and_request_project_identical_digests_for_identical_inputs(self, repository: Path, tmp_path: Path):
        task_path = _write_task(repository)
        output_path = tmp_path / "scratch" / "request.yaml"

        plan_code, plan_out, plan_err = _run(["plan", *_shared_args(repository, task_path=task_path)])
        assert plan_code == 0, plan_err

        request_code, request_out, request_err = _run(
            ["request", *_shared_args(repository, task_path=task_path), "--output", str(output_path)]
        )
        assert request_code == 0, request_err

        plan_digests = _digest_lines(plan_out)
        request_digests = _digest_lines(request_out)
        assert len(plan_digests) == 2
        assert plan_digests == request_digests

    def test_emitted_file_matches_the_direct_bp2b_construction_byte_for_byte(self, repository: Path, tmp_path: Path):
        from workflow_coordinator.frontdoor.plan import consumer_request_from_plan_inputs, task_document_from_markdown

        task_path = _write_task(repository)
        output_path = tmp_path / "scratch" / "request.yaml"
        head = git(repository, "rev-parse", "HEAD")

        code, out, err = _run([
            "request", *_shared_args(repository, task_path=task_path),
            "--guidance-commit", head, "--output", str(output_path),
        ])
        assert code == 0, err

        task = task_document_from_markdown(_TASK_TEXT)
        expected = consumer_request_from_plan_inputs(
            task=task, commission_id="consumer-request-smoke", work_item_id="WI-CONSUMER-REQUEST-SMOKE",
            authority_source="Maintainer authorizes this bounded preview only.", program="simple@v1",
            scope=["src"], personas=["worker=Bounded worker persona."],
            evidence_argv=["sh", "checks/check.sh"], evidence_timeout=900, max_autonomous_hops=5,
            durable_artifact=[], guidance_path="GUIDANCE.md", guidance_commit=head,
        )
        assert output_path.read_text(encoding="utf-8") == expected.raw_text

    def test_request_states_nothing_was_authorized_and_names_the_operator_judgement_fields(
        self, repository: Path, tmp_path: Path,
    ):
        task_path = _write_task(repository)
        output_path = tmp_path / "scratch" / "request.yaml"
        code, out, err = _run(
            ["request", *_shared_args(repository, task_path=task_path), "--output", str(output_path)]
        )
        assert code == 0, err
        assert "nothing was authorized" in out
        for field in ("scope", "program", "personas", "max_autonomous_hops", "evidence.argv", "authority_source"):
            assert field in out


class TestOutputPathRefusals:
    def test_refuses_to_overwrite_an_existing_output_without_force(self, repository: Path, tmp_path: Path):
        task_path = _write_task(repository)
        output_path = tmp_path / "scratch" / "request.yaml"
        output_path.parent.mkdir(parents=True)
        output_path.write_text("preexisting\n", encoding="utf-8")

        code, out, err = _run(
            ["request", *_shared_args(repository, task_path=task_path), "--output", str(output_path)]
        )
        assert code != 0
        assert out == ""
        assert "refusing to overwrite" in err
        assert output_path.read_text(encoding="utf-8") == "preexisting\n"

    def test_force_overwrites_an_existing_output(self, repository: Path, tmp_path: Path):
        task_path = _write_task(repository)
        output_path = tmp_path / "scratch" / "request.yaml"
        output_path.parent.mkdir(parents=True)
        output_path.write_text("preexisting\n", encoding="utf-8")

        code, out, err = _run([
            "request", *_shared_args(repository, task_path=task_path), "--output", str(output_path), "--force",
        ])
        assert code == 0, err
        assert output_path.read_text(encoding="utf-8") != "preexisting\n"

    def test_refuses_an_output_path_inside_the_repository_by_default(self, repository: Path, tmp_path: Path):
        task_path = _write_task(repository)
        output_path = repository / "request.yaml"
        head_before = git(repository, "rev-parse", "HEAD")
        status_before = git(repository, "status", "--porcelain", "--untracked-files=all")

        code, out, err = _run(
            ["request", *_shared_args(repository, task_path=task_path), "--output", str(output_path)]
        )
        assert code != 0
        assert out == ""
        assert "inside the consumer repository" in err
        assert not output_path.exists()
        assert git(repository, "rev-parse", "HEAD") == head_before
        assert git(repository, "status", "--porcelain", "--untracked-files=all") == status_before

    def test_allow_inside_repository_writes_there_when_explicitly_passed(self, repository: Path, tmp_path: Path):
        task_path = _write_task(repository)
        output_path = repository / "request.yaml"

        code, out, err = _run([
            "request", *_shared_args(repository, task_path=task_path),
            "--output", str(output_path), "--allow-inside-repository",
        ])
        assert code == 0, err
        assert output_path.exists()

    def test_writes_nothing_but_the_one_output_file(self, repository: Path, tmp_path: Path):
        task_path = _write_task(repository)
        output_path = tmp_path / "scratch" / "request.yaml"
        tree_before = _tree_snapshot(repository)
        head_before = git(repository, "rev-parse", "HEAD")

        code, out, err = _run(
            ["request", *_shared_args(repository, task_path=task_path), "--output", str(output_path)]
        )
        assert code == 0, err
        assert git(repository, "rev-parse", "HEAD") == head_before
        assert _tree_snapshot(repository) == tree_before
        assert output_path.is_file()
        assert list(output_path.parent.iterdir()) == [output_path]

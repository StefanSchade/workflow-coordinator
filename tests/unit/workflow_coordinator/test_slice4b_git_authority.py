"""Real-Git Slice-4B authority tests; no provider or agent-owned commit is used."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.workflow_coordinator.adapter import AgentExecutionOutcome
from scripts.workflow_coordinator.git_facts import (
    GitPreflightError,
    TrustedCommitError,
    observe_hop,
    trusted_commit,
)
from scripts.workflow_coordinator.model import CommitIntent
from scripts.workflow_coordinator.prompt import PromptComponent
from scripts.workflow_coordinator.vertical import run_one_hop
from tests.utils.workflow_coordinator import phase, profile, route, run_for


pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *args), text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(("git", "init", str(repo)), check=True, capture_output=True)
    for key, value in (("user.email", "fixture@example.invalid"), ("user.name", "Fixture")):
        subprocess.run(("git", "-C", str(repo), "config", key, value), check=True)
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("before\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "unrelated.adoc").write_text("before\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "test: baseline")
    return repo


def _result(*, intent: dict[str, object] | None, outcome: str = "completed") -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "run_id": "RUN-1",
            "hop_id": "H001",
            "invocation_id": "INV-1",
            "result": {
                "schema_version": 1,
                "outcome": outcome,
                "requested_route": "done",
                "scope_changed": False,
                "requires_human": False,
                "escalation": None,
                "findings": [],
                "claimed_commits": [],
                "commit_intent": intent,
                "evidence_refs": ["git_version"],
                "summary": "worker completed worktree mutation",
            },
        }
    )


class WorktreeOnlyAdapter:
    """The fake agent has no Git operation: it only writes files and returns intent."""

    def __init__(self, output: str, changes: dict[str, str]) -> None:
        self.output = output
        self.changes = changes

    def execute(self, request) -> AgentExecutionOutcome:
        for relative, content in self.changes.items():
            path = request.repository / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return AgentExecutionOutcome(self.output, 0, False, "", "")


def _components() -> dict[str, PromptComponent]:
    return {
        "worker": PromptComponent("worker", "worker.txt", "PIN", "worker"),
        "testing": PromptComponent("testing", "testing.txt", "PIN", "testing"),
    }


def _vertical_run(repository: Path, adapter: WorktreeOnlyAdapter, *, scope: tuple[str, ...] = ("src",)):
    workflow = profile({"work": replace(phase({"done": route("complete")}), authorized_scope=scope, evidence=("git_version",))})
    base = git(repository, "rev-parse", "HEAD")
    run = replace(run_for(workflow, budget=1), current_head=base, baseline_head=base)
    return run_one_hop(run, workflow, repository, adapter, _components(), task_payload="fixture", invocation_id="INV-1")


def test_coordinator_creates_one_authoritative_commit_from_worktree_only_agent(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    subject = "feat(workflow): WI-1 H001 coordinator commit"
    completed = _vertical_run(
        repository,
        WorktreeOnlyAdapter(_result(intent={"paths": ["src/a.py"], "subject": subject}), {"src/a.py": "after\n"}),
    )
    head = git(repository, "rev-parse", "HEAD")
    delta = observe_hop(repository, base_head=base, allowed_paths=("src",), claims_existing_commits=False)
    assert completed.reduction.run.status.value == "completed"
    assert head != base and delta.actual_commits == (head,)
    assert git(repository, "show", "-s", "--format=%s", head) == subject
    assert delta.changed_paths == ("src/a.py",) and delta.claim_mismatch is False
    assert git(repository, "status", "--porcelain") == ""
    assert git(repository, "remote") == ""
    assert completed.reduction.run.hops[0].actual_commits == (head,)


def test_unrelated_dirty_tracked_or_untracked_state_fails_closed_without_cleaning(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    subject = "fix(workflow): WI-1 H001 bounded change"
    stopped = _vertical_run(
        repository,
        WorktreeOnlyAdapter(
            _result(intent={"paths": ["src/a.py"], "subject": subject}),
            {"src/a.py": "after\n", "docs/unrelated.adoc": "keep dirty\n", "loose.txt": "keep untracked\n"},
        ),
    )
    assert stopped.reduction.run.status.value == "awaiting_human"
    assert git(repository, "rev-parse", "HEAD") == base
    assert (repository / "docs" / "unrelated.adoc").read_text() == "keep dirty\n"
    assert (repository / "loose.txt").exists()
    assert "unexpected dirty path" in (stopped.reduction.run.stop_reason or "")


@pytest.mark.parametrize("path", [".", "/tmp/escape", "../escape", "src/../a.py", "src//a.py"])
def test_escape_or_ambiguous_intent_path_fails_before_staging(repository: Path, path: str):
    base = git(repository, "rev-parse", "HEAD")
    intent = CommitIntent((path,), "fix(workflow): WI-1 H001 bounded change")
    (repository / "src" / "a.py").write_text("after\n", encoding="utf-8")
    with pytest.raises(TrustedCommitError):
        trusted_commit(repository, expected_repository=repository, base_head=base, allowed_paths=("src",), intent=intent, work_item_id="WI-1", hop_id="H001")
    assert git(repository, "rev-parse", "HEAD") == base
    assert git(repository, "diff", "--cached", "--name-only") == ""


def test_duplicate_intent_paths_fail_before_staging(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    (repository / "src" / "a.py").write_text("after\n", encoding="utf-8")
    with pytest.raises(TrustedCommitError, match="duplicate"):
        trusted_commit(repository, expected_repository=repository, base_head=base, allowed_paths=("src",), intent=CommitIntent(("src/a.py", "src/a.py"), "fix(workflow): WI-1 H001 bounded change"), work_item_id="WI-1", hop_id="H001")
    assert git(repository, "diff", "--cached", "--name-only") == ""


@pytest.mark.parametrize(
    "subject",
    ["", "fix(workflow): missing identity", "not conventional: WI-1 H001 change"],
)
def test_out_of_scope_and_invalid_subject_fail_before_git_mutation(repository: Path, subject: str):
    base = git(repository, "rev-parse", "HEAD")
    (repository / "src" / "a.py").write_text("after\n", encoding="utf-8")
    with pytest.raises(TrustedCommitError, match="outside authorized scope"):
        trusted_commit(repository, expected_repository=repository, base_head=base, allowed_paths=("docs",), intent=CommitIntent(("src/a.py",), "fix(workflow): WI-1 H001 bounded change"), work_item_id="WI-1", hop_id="H001")
    with pytest.raises(TrustedCommitError):
        trusted_commit(repository, expected_repository=repository, base_head=base, allowed_paths=("src",), intent=CommitIntent(("src/a.py",), subject), work_item_id="WI-1", hop_id="H001")
    assert git(repository, "rev-parse", "HEAD") == base
    assert git(repository, "diff", "--cached", "--name-only") == ""


def test_explicit_new_authorized_file_is_committed_and_clean(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    (repository / "src" / "new.py").write_text("new\n", encoding="utf-8")
    committed = trusted_commit(
        repository, expected_repository=repository, base_head=base, allowed_paths=("src",),
        intent=CommitIntent(("src/new.py",), "feat(workflow): WI-1 H001 add file"),
        work_item_id="WI-1", hop_id="H001",
    )
    assert committed.commit == git(repository, "rev-parse", "HEAD")
    assert committed.delta.changed_paths == ("src/new.py",)
    assert git(repository, "status", "--porcelain") == ""


def test_head_and_registered_repository_preconditions_refuse_before_commit(repository: Path, tmp_path: Path):
    base = git(repository, "rev-parse", "HEAD")
    (repository / "src" / "a.py").write_text("after\n", encoding="utf-8")
    with pytest.raises(TrustedCommitError, match="repository identity mismatch"):
        trusted_commit(repository, expected_repository=tmp_path / "other", base_head=base, allowed_paths=("src",), intent=CommitIntent(("src/a.py",), "fix(workflow): WI-1 H001 change"), work_item_id="WI-1", hop_id="H001")
    git(repository, "add", "src/a.py")
    git(repository, "commit", "-m", "test: external movement")
    with pytest.raises(TrustedCommitError, match="unexpected HEAD movement"):
        trusted_commit(repository, expected_repository=repository, base_head=base, allowed_paths=("src",), intent=CommitIntent(("src/a.py",), "fix(workflow): WI-1 H001 change"), work_item_id="WI-1", hop_id="H001")


def test_deterministic_staging_failure_reports_truthfully_without_reset(repository: Path, monkeypatch):
    import scripts.workflow_coordinator.git_facts as git_facts

    base = git(repository, "rev-parse", "HEAD")
    original = git_facts._trusted_git

    def fail_add(repo: Path, *args: str, **kwargs: object) -> str:
        if args[:1] == ("add",):
            raise GitPreflightError("injected stage failure")
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(git_facts, "_trusted_git", fail_add)
    stopped = _vertical_run(
        repository,
        WorktreeOnlyAdapter(_result(intent={"paths": ["src/a.py"], "subject": "fix(workflow): WI-1 H001 change"}), {"src/a.py": "after\n"}),
    )
    assert stopped.reduction.run.status.value == "awaiting_human"
    assert "trusted_commit:staging failed: injected stage failure" in (stopped.reduction.run.stop_reason or "")
    assert git(repository, "rev-parse", "HEAD") == base
    assert (repository / "src" / "a.py").read_text() == "after\n"


def _write_hook(repository: Path, directory: str, side_effect: str) -> str:
    hook = repository / directory / "pre-commit"
    hook.parent.mkdir()
    hook.write_text(f"#!/bin/sh\nprintf hook > {side_effect}\n", encoding="utf-8")
    hook.chmod(0o755)
    return hook.relative_to(repository).as_posix()


def test_trusted_commit_neutralizes_agent_authored_worktree_hook(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    git(repository, "config", "core.hooksPath", ".githooks")
    hook_path = _write_hook(repository, ".githooks", "hook-executed")
    (repository / "src" / "a.py").write_text("after\n", encoding="utf-8")

    committed = trusted_commit(
        repository,
        expected_repository=repository,
        base_head=base,
        allowed_paths=("src", ".githooks"),
        intent=CommitIntent(
            ("src/a.py", hook_path),
            "fix(workflow): WI-1 H001 neutralize worktree hook",
        ),
        work_item_id="WI-1",
        hop_id="H001",
    )

    assert committed.commit == git(repository, "rev-parse", "HEAD")
    assert not (repository / "hook-executed").exists()


def test_trusted_commit_strips_inherited_git_config_hook_injection(repository: Path, monkeypatch):
    base = git(repository, "rev-parse", "HEAD")
    hook_path = _write_hook(repository, ".injected-hooks", "injected-hook-executed")
    (repository / "src" / "a.py").write_text("after\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", ".injected-hooks")

    committed = trusted_commit(
        repository,
        expected_repository=repository,
        base_head=base,
        allowed_paths=("src", ".injected-hooks"),
        intent=CommitIntent(
            ("src/a.py", hook_path),
            "fix(workflow): WI-1 H001 ignore Git config injection",
        ),
        work_item_id="WI-1",
        hop_id="H001",
    )

    assert committed.commit == git(repository, "rev-parse", "HEAD")
    assert not (repository / "injected-hook-executed").exists()


def test_trusted_commit_refuses_configured_clean_filter_before_staging(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    git(repository, "config", "filter.agent.clean", "touch filter-executed")
    (repository / ".gitattributes").write_text("src/a.py filter=agent\n", encoding="utf-8")
    (repository / "src" / "a.py").write_text("after\n", encoding="utf-8")

    with pytest.raises(TrustedCommitError, match="configured clean/process filter"):
        trusted_commit(
            repository,
            expected_repository=repository,
            base_head=base,
            allowed_paths=("src", ".gitattributes"),
            intent=CommitIntent(
                ("src/a.py", ".gitattributes"),
                "fix(workflow): WI-1 H001 reject selected filter",
            ),
            work_item_id="WI-1",
            hop_id="H001",
        )

    assert git(repository, "rev-parse", "HEAD") == base
    assert not (repository / "filter-executed").exists()


def test_clean_observation_only_is_valid_and_dirty_without_intent_is_not(repository: Path):
    clean = _vertical_run(repository, WorktreeOnlyAdapter(_result(intent=None), {}))
    assert clean.reduction.run.status.value == "completed"
    dirty = _vertical_run(repository, WorktreeOnlyAdapter(_result(intent=None), {"src/a.py": "after\n"}))
    assert dirty.reduction.run.status.value == "awaiting_human"
    assert "dirty tracked state" in (dirty.reduction.run.stop_reason or "")


def test_failed_result_with_intent_never_synthesizes_a_commit(repository: Path):
    base = git(repository, "rev-parse", "HEAD")
    stopped = _vertical_run(
        repository,
        WorktreeOnlyAdapter(
            _result(intent={"paths": ["src/a.py"], "subject": "fix(workflow): WI-1 H001 change"}, outcome="blocked"),
            {"src/a.py": "after\n"},
        ),
    )
    assert stopped.reduction.run.status.value == "awaiting_human"
    assert git(repository, "rev-parse", "HEAD") == base
    assert "commit intent is not permitted" in (stopped.reduction.run.stop_reason or "")

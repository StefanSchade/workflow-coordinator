"""Real-Git acceptance fixtures for coordinator Slice 2."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.workflow_coordinator.evidence import run_evidence
from scripts.workflow_coordinator.git_facts import (
    GitPreflightError,
    observe_hop,
    observe_repository,
    preflight_hop,
    preflight_state_root,
)
from scripts.workflow_coordinator.model import DispatchMarker, RunStatus
from scripts.workflow_coordinator.persistence import recover_uncertain_dispatch
from scripts.workflow_coordinator.reducer import resume_after_reconcile
from tests.utils.workflow_coordinator import phase, profile, route, run_for

pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args), check=True, text=True, capture_output=True
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "fixture"
    git(tmp_path, "init", str(repo))
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    (repo / "src").mkdir()
    (repo / "src" / "base.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "test: baseline")
    return repo


def commit(repo: Path, path: str, content: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    git(repo, "add", path)
    git(repo, "commit", "-m", f"test: {path}")
    return git(repo, "rev-parse", "HEAD")


def test_preflight_requires_clean_expected_repository_and_head(repository: Path):
    facts = observe_repository(repository)
    assert preflight_hop(repository, expected_repository=repository, expected_head=facts.head) == facts
    (repository / "src" / "base.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(GitPreflightError, match="dirty tracked state"):
        preflight_hop(repository, expected_repository=repository, expected_head=facts.head)
    assert (repository / "src" / "base.txt").read_text(encoding="utf-8") == "dirty\n"


def test_preflight_rejects_nonignored_untracked_without_cleaning(repository: Path):
    head = observe_repository(repository).head
    (repository / "loose.txt").write_text("keep me\n", encoding="utf-8")
    with pytest.raises(GitPreflightError, match="non-ignored untracked state"):
        preflight_hop(repository, expected_repository=repository, expected_head=head)
    assert (repository / "loose.txt").exists()


def test_state_root_requires_tracked_ignore_not_local_exclude(repository: Path):
    (repository / ".gitignore").write_text(".workflow-state/\n", encoding="utf-8")
    git(repository, "add", ".gitignore")
    git(repository, "commit", "-m", "test: ignore coordinator state")
    preflight_state_root(repository, Path(".workflow-state"))


@pytest.mark.parametrize("staged", [False, True])
def test_state_root_rejects_uncommitted_tracked_ignore_authority(repository: Path, staged: bool):
    (repository / ".gitignore").write_text("other-state/\n", encoding="utf-8")
    git(repository, "add", ".gitignore")
    git(repository, "commit", "-m", "test: unrelated ignored root")
    (repository / ".gitignore").write_text(".workflow-state/\n", encoding="utf-8")
    if staged:
        git(repository, "add", ".gitignore")
    with pytest.raises(GitPreflightError, match="must be committed and clean"):
        preflight_state_root(repository, Path(".workflow-state"))
    assert not (repository / ".workflow-state").exists()


@pytest.mark.parametrize("local_exclude", [False, True])
def test_state_root_unignored_or_local_only_fails_before_write(repository: Path, local_exclude: bool):
    if local_exclude:
        (repository / ".git" / "info" / "exclude").write_text(
            ".workflow-state/\n", encoding="utf-8"
        )
    with pytest.raises(GitPreflightError, match="tracked repository .gitignore rule"):
        preflight_state_root(repository, Path(".workflow-state"))
    assert not (repository / ".workflow-state").exists()


def test_observation_proves_zero_one_and_many_commits(repository: Path):
    base = observe_repository(repository).head
    zero = observe_hop(repository, base_head=base, allowed_paths=("src",))
    assert zero.base_head == zero.end_head and zero.actual_commits == ()
    one_head = commit(repository, "src/one.txt", "one\n")
    one = observe_hop(repository, base_head=base, allowed_paths=("src",))
    assert one.actual_commits == (one_head,)
    two_head = commit(repository, "src/two.txt", "two\n")
    many = observe_hop(repository, base_head=base, allowed_paths=("src",))
    assert many.actual_commits == (one_head, two_head)


def test_actual_commits_override_correct_omitted_and_invented_claims(repository: Path):
    base = observe_repository(repository).head
    actual = commit(repository, "src/change.txt", "change\n")
    correct = observe_hop(repository, base_head=base, claimed_commits=(actual,), allowed_paths=("src",))
    omitted = observe_hop(repository, base_head=base, claimed_commits=(), allowed_paths=("src",))
    invented = observe_hop(repository, base_head=base, claimed_commits=("deadbeef",), allowed_paths=("src",))
    assert correct.claim_mismatch is False
    assert omitted.claim_mismatch is True and omitted.actual_commits == (actual,)
    assert invented.claim_mismatch is True and invented.actual_commits == (actual,)


def test_hidden_scope_policy_and_safety_anomalies_are_git_facts(repository: Path):
    base = observe_repository(repository).head
    commit(repository, "secret/policy.yaml", "changed\n")
    delta = observe_hop(
        repository,
        base_head=base,
        claimed_commits=(),
        allowed_paths=("src",),
        pinned_paths=("secret/policy.yaml",),
        max_commits=0,
    )
    assert delta.claim_mismatch is True
    assert "out-of-scope changed path: secret/policy.yaml" in delta.anomalies
    assert "pinned policy/profile mutation: secret/policy.yaml" in delta.anomalies
    assert "commit safety-cap breach" in delta.anomalies


def test_divergence_and_forbidden_merge_are_detected(repository: Path):
    base = observe_repository(repository).head
    git(repository, "checkout", "-b", "other")
    commit(repository, "src/other.txt", "other\n")
    git(repository, "checkout", "-")
    commit(repository, "src/main.txt", "main\n")
    git(repository, "merge", "--no-ff", "other", "-m", "test: merge")
    merged = observe_hop(repository, base_head=base, allowed_paths=("src",))
    assert "forbidden merge commit" in merged.anomalies
    git(repository, "checkout", "other")
    divergent = observe_hop(repository, base_head=merged.end_head)
    assert "divergent or rewritten ancestry" in divergent.anomalies


@pytest.mark.parametrize("base", ["not-a-real-object", "blob"])
def test_invalid_base_anchor_is_not_reported_as_divergence(repository: Path, base: str):
    if base == "blob":
        base = subprocess.run(
            ("git", "-C", str(repository), "hash-object", "-w", "--stdin"),
            input="not a commit\n",
            text=True,
            check=True,
            capture_output=True,
        ).stdout.strip()
    delta = observe_hop(repository, base_head=base)
    assert "unresolved base anchor" in delta.anomalies
    assert "divergent or rewritten ancestry" not in delta.anomalies


def test_postcondition_requires_clean_final_state(repository: Path):
    base = observe_repository(repository).head
    commit(repository, "src/committed.txt", "ok\n")
    (repository / "src" / "committed.txt").write_text("dirty\n", encoding="utf-8")
    delta = observe_hop(repository, base_head=base, allowed_paths=("src",))
    assert "dirty tracked state" in delta.anomalies


def test_fixed_evidence_covers_pass_fail_error_and_not_run(repository: Path, monkeypatch):
    passed = run_evidence("git_version", repository)
    skipped = run_evidence("git_version", repository, selected=False)
    assert passed.status == "pass" and passed.command == ("git", "--version") and passed.digest
    assert skipped.status == "not_run"
    (repository / "src" / "base.txt").write_text("line   \n", encoding="utf-8")
    failed = run_evidence("diff_hygiene", repository)
    assert failed.status == "fail"
    monkeypatch.setattr("scripts.workflow_coordinator.evidence.subprocess.run", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    errored = run_evidence("git_version", repository)
    assert errored.status == "error"
    with pytest.raises(ValueError, match="unknown trusted"):
        run_evidence("agent-selected-command", repository)


def test_marker_recovery_preserves_actual_commits_requires_resume_and_charges_once(repository: Path):
    workflow = profile({"work": phase({"done": route("complete")})})
    run = run_for(workflow, budget=2)
    base = observe_repository(repository).head
    run = replace(run, current_head=base, baseline_head=base)
    marker = DispatchMarker(1, run.run_id, "H001", 1, "INV-1", base, 1)
    actual = commit(repository, "src/provider.txt", "provider did work\n")
    facts = observe_hop(repository, base_head=base, allowed_paths=("src",))
    reconciled = recover_uncertain_dispatch(
        run, marker, current_head=facts.end_head, actual_commits=facts.actual_commits
    )
    repeated = recover_uncertain_dispatch(reconciled, marker, current_head=actual)
    assert reconciled.status is RunStatus.RECONCILE_REQUIRED and reconciled.hop_used == 1
    assert reconciled.hops[0].actual_commits == (actual,)
    assert repeated.hop_used == 1 and repeated.current_head == actual
    assert resume_after_reconcile(repeated, workflow, current_head=actual).status is RunStatus.READY


def test_marker_without_mutation_is_charged_once_and_marker_absence_is_free(repository: Path):
    workflow = profile({"work": phase({"done": route("complete")})})
    run = run_for(workflow, budget=2)
    head = observe_repository(repository).head
    run = replace(run, current_head=head, baseline_head=head)
    assert recover_uncertain_dispatch(run, None, current_head=head) == run
    marker = DispatchMarker(1, run.run_id, "H001", 1, "INV-1", head, 1)
    recovered = recover_uncertain_dispatch(run, marker, current_head=head)
    assert recovered.hop_used == 1 and recovered.hops[0].actual_commits == ()

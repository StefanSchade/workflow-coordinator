"""Production-preflight proof for current self-development governance."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from workflow_coordinator.operator import (
    OperatorPreflightError,
    _preflight,
    _selected_ids,
    _within_scope,
)
from workflow_coordinator.model import COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GOVERNING_PATHS = (
    "governance/work-items/developer-ergonomics.yaml",
    "governance/self-development/standalone-development-profile.yaml",
    "governance/self-development/repository-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/developer-ergonomics-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CONTINUATION_GOVERNING_PATHS = (
    "governance/work-items/developer-ergonomics-review.yaml",
    "governance/self-development/standalone-development-profile.yaml",
    "governance/self-development/repository-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/developer-ergonomics-review-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
ATTEMPT_4_GOVERNING_PATHS = (
    "governance/work-items/developer-ergonomics-review-002.yaml",
    "governance/self-development/standalone-development-profile.yaml",
    "governance/self-development/repository-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/developer-ergonomics-review-002-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
ATTEMPT_5_GOVERNING_PATHS = (
    "governance/work-items/developer-ergonomics-review-003.yaml",
    "governance/self-development/standalone-development-profile.yaml",
    "governance/self-development/repository-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/developer-ergonomics-review-003-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
ATTEMPT_6_GOVERNING_PATHS = (
    "governance/work-items/developer-ergonomics-review-004.yaml",
    "governance/self-development/standalone-development-profile.yaml",
    "governance/self-development/repository-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/developer-ergonomics-review-004-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
ATTEMPT_7_GOVERNING_PATHS = (
    "governance/work-items/developer-ergonomics-review-005.yaml",
    "governance/self-development/standalone-development-profile.yaml",
    "governance/self-development/repository-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/developer-ergonomics-review-005-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
GENERATIVE_ECHO_AUDIT_GOVERNING_PATHS = (
    "governance/work-items/generative-echo-audit.yaml",
    "governance/self-development/standalone-development-profile.yaml",
    "governance/self-development/repository-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/generative-echo-audit-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
IMPLEMENTATION_PATHS = (
    "Makefile",
    "README.md",
    "pyproject.toml",
    "scripts/wfc_snapshot.py",
    "tests/unit/test_wfc_snapshot.py",
    "tests/architecture/test_package_boundaries.py",
)
REVIEW_NAMESPACE = "docs/backlog/wfc_reviews"
REVIEW_ARTIFACT = f"{REVIEW_NAMESPACE}/developer_ergonomics_review.adoc"
HISTORICAL_REVIEW_ARTIFACT = "docs/backlog/first_wfc_on_wfc_dogfood_review.adoc"


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _current_text(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def _materialize_bundle(
    tmp_path: Path,
    *,
    governing_paths: tuple[str, ...] = GOVERNING_PATHS,
    replacements: dict[str, str] | None = None,
) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--object-format=sha1")
    _git(repository, "config", "user.email", "preflight@example.invalid")
    _git(repository, "config", "user.name", "B2 Preflight")
    for relative in governing_paths:
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        text = (replacements or {}).get(relative, _current_text(relative))
        target.write_text(text, encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "test: exact governing bundle")
    expected_head = _git(repository, "rev-parse", "HEAD")
    assert len(expected_head) == 40
    return repository, expected_head


def _preflight_args(
    repository: Path,
    expected_head: str,
    state_root: Path,
    *,
    governing_paths: tuple[str, ...] = GOVERNING_PATHS,
) -> argparse.Namespace:
    binding_source = state_root.parent / f"{state_root.name}-bindings.yml"
    binding_source.parent.mkdir(parents=True, exist_ok=True)
    binding_source.write_text(
        "schema_version: 1\nbindings:\n"
        "  worker: {adapter: codex_cli, model: worker-model, reasoning: medium}\n"
        "  independent_reviewer: {adapter: codex_cli, model: reviewer-model, reasoning: high}\n",
        encoding="utf-8",
    )
    return argparse.Namespace(
        repository=str(repository),
        state_root=str(state_root),
        expected_head=expected_head,
        work_item=governing_paths[0],
        profile=governing_paths[1],
        policy=governing_paths[2],
        components=governing_paths[3],
        task=governing_paths[4],
        execution_bindings=str(binding_source),
        durable_artifact=[],
        timeout_seconds=900.0,
        executable="unused-before-dispatch",
    )


@pytest.fixture
def preflight_python_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Expose this test environment's invoked interpreter as policy `python`."""

    monkeypatch.setenv(
        "PATH",
        os.pathsep.join((str(Path(sys.executable).parent), os.environ.get("PATH", ""))),
    )


def test_current_self_development_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    repository, expected_head = _materialize_bundle(tmp_path)

    state_root = tmp_path / "external-state" / "new-run"
    args = _preflight_args(repository, expected_head, state_root)

    assert not state_root.exists()
    result = _preflight(args)
    work_item, profile, policy = result[5:8]
    components, durable_artifacts = result[8:10]
    prepared_evidence, environment = result[13:15]
    exact_scope = (*IMPLEMENTATION_PATHS, REVIEW_NAMESPACE)

    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert profile.revision == "4"
    assert policy.revision == "3"
    assert work_item.max_autonomous_hops == 4
    assert policy.max_autonomous_hops == 5
    assert tuple(profile.phases) == ("investigate", "review", "correct", "rereview")
    assert durable_artifacts == {"review": (REVIEW_ARTIFACT,)}
    assert tuple(components) == _selected_ids(profile) == ("worker", "reviewer")
    investigate = profile.phases["investigate"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]
    assert investigate.authorized_scope == correct.authorized_scope == IMPLEMENTATION_PATHS
    assert investigate.authorized_actions == correct.authorized_actions == ("edit", "commit")
    assert review.authorized_scope == rereview.authorized_scope == (REVIEW_NAMESPACE,)
    assert HISTORICAL_REVIEW_ARTIFACT not in review.authorized_scope
    assert HISTORICAL_REVIEW_ARTIFACT not in rereview.authorized_scope
    assert review.authorized_scope != ("docs/backlog",)
    assert not _within_scope("docs/backlog/unrelated.adoc", review.authorized_scope)
    assert review.authorized_actions == ("review", "edit", "commit")
    assert rereview.authorized_actions == ("review",)
    assert investigate.durable_artifact.value == correct.durable_artifact.value == "optional"
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "none"
    assert _within_scope(REVIEW_ARTIFACT, work_item.scope)
    assert _within_scope(REVIEW_ARTIFACT, review.authorized_scope)
    assert set(IMPLEMENTATION_PATHS).isdisjoint(review.authorized_scope)
    assert REVIEW_NAMESPACE not in investigate.authorized_scope
    assert REVIEW_NAMESPACE not in correct.authorized_scope
    assert {
        phase: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase, value in profile.phases.items()
    } == {
        "investigate": (("review", "goto", "review"),),
        "review": (
            ("rereview", "goto", "rereview"),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (("accept", "complete", None), ("correct", "goto", "correct")),
    }
    assert review.routes["rereview"].target == "rereview"
    assert review.role == rereview.role == "independent_reviewer"
    assert policy.governance_paths == ("governance",)
    assert policy.actions == ("edit", "commit", "review")
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(prepared_evidence) == ("pytest",)
    prepared = prepared_evidence["pytest"]
    assert prepared.authored_argv == ("python", "-m", "pytest", "-q")
    assert prepared.admission_resolution.invocation_path == str(Path(sys.executable).parent / "python")
    assert prepared.admission_resolution.canonical_path == str(Path(sys.executable).resolve())
    assert environment.values["PATH"].split(":", 1)[0] == str(Path(sys.executable).parent)
    assert not state_root.exists()


def test_production_preflight_identifies_different_python_interpreter_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preflight_python_path: None,
):
    alternate_bin = tmp_path / "alternate-bin"
    alternate_bin.mkdir()
    alternate_python = alternate_bin / "python"
    shutil.copy2(sys.executable, alternate_python)

    monkeypatch.setenv(
        "PATH",
        os.pathsep.join((str(alternate_bin), os.environ["PATH"])),
    )
    repository, expected_head = _materialize_bundle(tmp_path)
    state_root = tmp_path / "external-state" / "different-interpreter"

    result = _preflight(_preflight_args(repository, expected_head, state_root))
    prepared = result[13]["pytest"]

    assert prepared.admission_resolution.canonical_path == str(alternate_python.resolve())
    assert prepared.admission_resolution.canonical_path != str(Path(sys.executable).resolve())
    assert not state_root.exists()


def test_review_continuation_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CONTINUATION_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "review-continuation"

    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CONTINUATION_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    review = profile.phases["review"]

    artifact = f"{REVIEW_NAMESPACE}/WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-001/review.adoc"
    assert work_item.work_item_id == "WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-001"
    assert work_item.initial_phase == "review"
    assert work_item.max_autonomous_hops == 3
    assert work_item.scope == policy.work_item_scope_ceiling == (
        *IMPLEMENTATION_PATHS,
        REVIEW_NAMESPACE,
    )
    assert profile.revision == "4"
    assert policy.revision == "3"
    assert durable_artifacts == {"review": (artifact,)}
    assert _within_scope(artifact, work_item.scope)
    assert _within_scope(artifact, review.authorized_scope)
    assert tuple(review.routes) == ("rereview", "correct")
    assert "accept" not in review.routes
    assert tuple(profile.phases["correct"].routes) == ("rereview",)
    assert tuple(profile.phases["rereview"].routes) == ("accept", "correct")
    assert not state_root.exists()


def test_attempt_4_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=ATTEMPT_4_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "attempt-4"

    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=ATTEMPT_4_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    review = profile.phases["review"]

    artifact = f"{REVIEW_NAMESPACE}/WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-002/review.adoc"
    assert work_item.work_item_id == "WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-002"
    assert work_item.initial_phase == "review"
    assert work_item.max_autonomous_hops == 3
    assert profile.revision == "4"
    assert policy.revision == "3"
    assert work_item.scope == policy.work_item_scope_ceiling == (
        *IMPLEMENTATION_PATHS,
        REVIEW_NAMESPACE,
    )
    assert durable_artifacts == {"review": (artifact,)}
    assert _within_scope(artifact, work_item.scope)
    assert _within_scope(artifact, review.authorized_scope)
    assert tuple(review.routes) == ("rereview", "correct")
    assert "accept" not in review.routes
    assert tuple(profile.phases["correct"].routes) == ("rereview",)
    assert tuple(profile.phases["rereview"].routes) == ("accept", "correct")
    assert not state_root.exists()


def test_attempt_5_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=ATTEMPT_5_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "attempt-5"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=ATTEMPT_5_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    review = profile.phases["review"]

    artifact = f"{REVIEW_NAMESPACE}/WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-003/review.adoc"
    assert work_item.work_item_id == "WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-003"
    assert work_item.initial_phase == "review"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 5
    assert COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS == 20
    assert profile.revision == "4"
    assert policy.revision == "3"
    assert work_item.scope == policy.work_item_scope_ceiling == (
        *IMPLEMENTATION_PATHS,
        REVIEW_NAMESPACE,
    )
    assert durable_artifacts == {"review": (artifact,)}
    assert _within_scope(artifact, work_item.scope)
    assert _within_scope(artifact, review.authorized_scope)
    assert tuple(review.routes) == ("rereview", "correct")
    assert "accept" not in review.routes
    assert tuple(profile.phases["correct"].routes) == ("rereview",)
    assert tuple(profile.phases["rereview"].routes) == ("accept", "correct")
    assert not state_root.exists()


def test_attempt_6_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=ATTEMPT_6_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "attempt-6"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=ATTEMPT_6_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]

    assert work_item.work_item_id == "WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-004"
    assert work_item.initial_phase == "rereview"
    assert work_item.max_autonomous_hops == 3
    assert profile.revision == "4"
    assert policy.revision == "3"
    assert policy.max_autonomous_hops == 5
    assert work_item.scope == policy.work_item_scope_ceiling == (
        *IMPLEMENTATION_PATHS,
        REVIEW_NAMESPACE,
    )
    assert durable_artifacts == {
        "review": (
            f"{REVIEW_NAMESPACE}/WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-004/review.adoc",
        ),
    }
    assert tuple(profile.phases["review"].routes) == ("rereview", "correct")
    assert "accept" not in profile.phases["review"].routes
    assert tuple(profile.phases["correct"].routes) == ("rereview",)
    assert tuple(profile.phases["rereview"].routes) == ("accept", "correct")
    assert not state_root.exists()


def test_attempt_7_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=ATTEMPT_7_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "attempt-7"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=ATTEMPT_7_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]

    assert work_item.work_item_id == "WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-005"
    assert work_item.initial_phase == "rereview"
    assert work_item.max_autonomous_hops == 3
    assert profile.revision == "4"
    assert policy.revision == "3"
    assert policy.max_autonomous_hops == 5
    assert work_item.scope == policy.work_item_scope_ceiling == (
        *IMPLEMENTATION_PATHS,
        REVIEW_NAMESPACE,
    )
    assert durable_artifacts == {
        "review": (
            f"{REVIEW_NAMESPACE}/WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-005/review.adoc",
        ),
    }
    assert tuple(profile.phases["review"].routes) == ("rereview", "correct")
    assert "accept" not in profile.phases["review"].routes
    assert tuple(profile.phases["correct"].routes) == ("rereview",)
    assert tuple(profile.phases["rereview"].routes) == ("accept", "correct")
    assert not state_root.exists()


def test_generative_echo_audit_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Bounded architecture-assessment WorkItem: preflight-only proof.

    This governing bundle commissions an investigation, not an
    implementation: it must parse, stay within policy/profile authority,
    start at ``review``, carry a two-Hop-happy-path/three-Hop budget, name
    exactly one durable artifact, and hold no mutation authority over any
    inspected production source. WorkItem.scope carries the same ceiling as
    the developer-ergonomics chain -- the shared profile's ``investigate``/
    ``correct`` phases mechanically require it (their authorized_scope must
    lie within WorkItem.scope even though this WorkItem never reaches those
    phases in practice) -- but the phases this WorkItem actually starts and
    can reach, ``review``/``rereview``, are independently scoped by the
    profile to the review namespace alone, so actual mutation authority is
    narrower than the ceiling in practice.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=GENERATIVE_ECHO_AUDIT_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "generative-echo-audit"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=GENERATIVE_ECHO_AUDIT_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    review = profile.phases["review"]
    rereview = profile.phases["rereview"]

    artifact = f"{REVIEW_NAMESPACE}/WI-WFC-GENERATIVE-ECHO-AUDIT-001/review.adoc"

    assert work_item.work_item_id == "WI-WFC-GENERATIVE-ECHO-AUDIT-001"
    assert work_item.initial_phase == "review"
    assert work_item.max_autonomous_hops == 3
    assert profile.revision == "4"
    assert policy.revision == "3"

    # WorkItem.scope equals the shared policy ceiling: the profile's
    # `investigate`/`correct` phases mechanically require their
    # authorized_scope (IMPLEMENTATION_PATHS) to lie within WorkItem.scope,
    # so a narrower WorkItem.scope is preflight-rejected even though this
    # WorkItem never reaches those phases in practice.
    assert work_item.scope == policy.work_item_scope_ceiling == (
        *IMPLEMENTATION_PATHS,
        REVIEW_NAMESPACE,
    )

    # No mutation authority is granted over any inspected production source:
    # `src/` is not part of the ceiling at all, and this WorkItem's only
    # reachable phases (review/rereview) are independently scoped by the
    # profile to the review namespace alone.
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("src/workflow_coordinator/reducer.py", work_item.scope)
    assert not _within_scope("src/workflow_coordinator/model.py", work_item.scope)
    assert review.authorized_scope == rereview.authorized_scope == (REVIEW_NAMESPACE,)
    assert set(IMPLEMENTATION_PATHS).isdisjoint(review.authorized_scope)

    assert durable_artifacts == {"review": (artifact,)}
    assert _within_scope(artifact, work_item.scope)
    assert _within_scope(artifact, review.authorized_scope)
    assert tuple(review.routes) == ("rereview", "correct")
    assert "accept" not in review.routes
    assert tuple(rereview.routes) == ("accept", "correct")
    assert review.role == rereview.role == "independent_reviewer"
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "none"

    assert not state_root.exists()


def test_production_preflight_rejects_consumed_isolated_state_root(
    tmp_path: Path,
    preflight_python_path: None,
):
    repository, expected_head = _materialize_bundle(tmp_path)
    state_root = tmp_path / "external-state" / "consumed-run"
    state_root.mkdir(parents=True)

    with pytest.raises(
        OperatorPreflightError,
        match="state root already exists; one-shot operator roots are never overwritten",
    ):
        _preflight(_preflight_args(repository, expected_head, state_root))


def test_work_item_mapping_cannot_enlarge_review_phase_authority(
    tmp_path: Path,
    preflight_python_path: None,
):
    outside_artifact = "docs/backlog/outside_wfc_review.adoc"
    work_item = yaml.safe_load(_current_text(GOVERNING_PATHS[0]))
    work_item["scope"].append(outside_artifact)
    work_item["durable_artifacts"]["review"] = [outside_artifact]
    policy = yaml.safe_load(_current_text(GOVERNING_PATHS[2]))
    policy["work_item_scope_ceiling"].append(outside_artifact)
    replacements = {
        GOVERNING_PATHS[0]: yaml.safe_dump(work_item, sort_keys=False),
        GOVERNING_PATHS[2]: yaml.safe_dump(policy, sort_keys=False),
    }
    repository, expected_head = _materialize_bundle(
        tmp_path,
        replacements=replacements,
    )
    state_root = tmp_path / "external-state" / "rejected-run"

    with pytest.raises(
        OperatorPreflightError,
        match="durable artifact for phase review is outside WorkItem or phase scope",
    ):
        _preflight(_preflight_args(repository, expected_head, state_root))

    assert not state_root.exists()

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
from workflow_coordinator.model import (
    COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS,
    RunStatus,
    WorkflowRole,
)
from workflow_coordinator.reducer import reduce_result
from tests.support import charge_hop, phase, profile, result, route, run_for

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
IMMUTABLE_FINDING_ECHO_FIX_GOVERNING_PATHS = (
    "governance/work-items/immutable-finding-echo-fix.yaml",
    "governance/self-development/immutable-finding-echo-profile.yaml",
    "governance/self-development/immutable-finding-echo-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/immutable-finding-echo-fix-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
IMMUTABLE_FINDING_ECHO_FIX_002_GOVERNING_PATHS = (
    "governance/work-items/immutable-finding-echo-fix-002.yaml",
    "governance/self-development/immutable-finding-echo-profile-002.yaml",
    "governance/self-development/immutable-finding-echo-policy-002.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/immutable-finding-echo-fix-002-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
SCOPE_ESCALATION_CONTINUATION_DESIGN_GOVERNING_PATHS = (
    "governance/work-items/scope-escalation-continuation-design.yaml",
    "governance/self-development/scope-escalation-continuation-design-profile.yaml",
    "governance/self-development/scope-escalation-continuation-design-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/scope-escalation-continuation-design-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
SCOPE_ESCALATION_CONTINUATION_DESIGN_SCOPE = (
    "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION.adoc",
)
SCOPE_ESCALATION_CONTINUATION_DESIGN_REVIEW_ARTIFACT = (
    "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION_review_001.adoc"
)
CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-design-001.yaml",
    "governance/self-development/correction-convergence-escape-design-001-profile.yaml",
    "governance/self-development/correction-convergence-escape-design-001-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-design-001-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_SCOPE = (
    "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc",
)
CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT = (
    "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_review_001.adoc"
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_REVIEW_ARTIFACT = (
    "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_implementation_review_001.adoc"
)
CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-design-revision-001.yaml",
    "governance/self-development/correction-convergence-escape-design-revision-001-profile.yaml",
    "governance/self-development/correction-convergence-escape-design-revision-001-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-design-revision-001-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE = (
    "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001.adoc",
)
CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT = (
    "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001_review_001.adoc"
)
CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_CONTINUATION_001_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-design-revision-001-continuation-001.yaml",
    "governance/self-development/correction-convergence-escape-design-revision-001-continuation-001-profile.yaml",
    "governance/self-development/correction-convergence-escape-design-revision-001-continuation-001-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-design-revision-001-continuation-001-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_GOVERNING_PATHS = (
    "governance/work-items/scope-escalation-continuation-implementation.yaml",
    "governance/self-development/scope-escalation-continuation-implementation-profile.yaml",
    "governance/self-development/scope-escalation-continuation-implementation-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/scope-escalation-continuation-implementation-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE = (
    "src/workflow_coordinator/model.py",
    "src/workflow_coordinator/serialization.py",
    "src/workflow_coordinator/reducer.py",
    "src/workflow_coordinator/persistence.py",
    "src/workflow_coordinator/operator.py",
    "src/workflow_coordinator/git_facts.py",
    "tests/unit/test_reducer.py",
    "tests/unit/test_serialization.py",
    "tests/unit/test_persistence.py",
    "tests/unit/test_operator.py",
    "tests/unit/test_git_facts.py",
    "tests/architecture/test_replays.py",
)
SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT = (
    "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION_implementation_review_001.adoc"
)
SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_GOVERNING_PATHS = (
    "governance/work-items/scope-escalation-continuation-implementation-002.yaml",
    "governance/self-development/scope-escalation-continuation-implementation-002-profile.yaml",
    "governance/self-development/scope-escalation-continuation-implementation-002-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/scope-escalation-continuation-implementation-002-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_REVIEW_ARTIFACT = (
    "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION_implementation_review_002.adoc"
)
SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_GOVERNING_PATHS = (
    "governance/work-items/scope-escalation-continuation-implementation-003.yaml",
    "governance/self-development/scope-escalation-continuation-implementation-003-profile.yaml",
    "governance/self-development/scope-escalation-continuation-implementation-003-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/scope-escalation-continuation-implementation-003-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_REVIEW_ARTIFACT = (
    "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION_implementation_review_003.adoc"
)
SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_GOVERNING_PATHS = (
    "governance/work-items/scope-escalation-continuation-implementation-004.yaml",
    "governance/self-development/scope-escalation-continuation-implementation-004-profile.yaml",
    "governance/self-development/scope-escalation-continuation-implementation-004-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/scope-escalation-continuation-implementation-004-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_REVIEW_ARTIFACT = (
    "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION_implementation_review_004.adoc"
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-implementation-s1-001.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s1-001-profile.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s1-001-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-implementation-s1-001-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_SCOPE = (
    "src/workflow_coordinator/model.py",
    "src/workflow_coordinator/serialization.py",
    "src/workflow_coordinator/adapter.py",
    "src/workflow_coordinator/codex_adapter.py",
    "src/workflow_coordinator/reducer.py",
    "src/workflow_coordinator/persistence.py",
    "tests/support.py",
    "tests/unit/test_serialization.py",
    "tests/unit/test_reducer.py",
    "tests/unit/test_persistence.py",
    "tests/unit/test_codex_adapter_conformance.py",
    "tests/architecture/test_replays.py",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT = (
    "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_implementation_review_001.adoc"
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-implementation-s1-002.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s1-002-profile.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s1-002-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-implementation-s1-002-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_SCOPE = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_SCOPE
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-implementation-s1-003.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s1-003-profile.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s1-003-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-implementation-s1-003-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_SCOPE = (
    "src/workflow_coordinator/reducer.py",
    "tests/unit/test_reducer.py",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_REVIEW_ARTIFACT = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-implementation-s2-001.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s2-001-profile.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s2-001-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-implementation-s2-001-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_SCOPE = (
    "src/workflow_coordinator/model.py",
    "src/workflow_coordinator/reducer.py",
    "src/workflow_coordinator/persistence.py",
    "tests/support.py",
    "tests/unit/test_reducer.py",
    "tests/unit/test_persistence.py",
    "tests/architecture/test_replays.py",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_REVIEW_ARTIFACT
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
IMMUTABLE_FINDING_ECHO_FIX_CORRECTION_SCOPE = (
    "src/workflow_coordinator/model.py",
    "src/workflow_coordinator/serialization.py",
    "src/workflow_coordinator/reducer.py",
    "src/workflow_coordinator/codex_adapter.py",
    "tests/unit/test_reducer.py",
    "tests/unit/test_serialization.py",
    "tests/unit/test_codex_adapter_conformance.py",
    "tests/unit/test_slice3_vertical.py",
    "tests/unit/test_slice5b.py",
    "tests/architecture/test_replays.py",
)
IMMUTABLE_FINDING_ECHO_FIX_REVIEW_ARTIFACT = (
    "docs/backlog/BUG-WFC-IMMUTABLE-FINDING-ECHO_review_001.adoc"
)
IMMUTABLE_FINDING_ECHO_FIX_002_CORRECTION_SCOPE = (
    *IMMUTABLE_FINDING_ECHO_FIX_CORRECTION_SCOPE,
    "tests/unit/test_slice4.py",
)
IMMUTABLE_FINDING_ECHO_FIX_002_REVIEW_ARTIFACT = (
    "docs/backlog/BUG-WFC-IMMUTABLE-FINDING-ECHO_review_002.adoc"
)


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


def test_immutable_finding_echo_fix_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Bounded self-development correction commission: preflight-only proof.

    This governing bundle commissions the correction implementation
    authorized by ``docs/backlog/BUG-WFC-IMMUTABLE-FINDING-ECHO.adoc``,
    "Authorized correction scope". Unlike every other WorkItem exercised
    above, it deliberately uses its *own* dedicated profile and repository
    policy (not the shared ``standalone-development-profile.yaml`` /
    ``repository-policy.yaml``), because the shared policy's
    ``work_item_scope_ceiling`` is fixed to developer-ergonomics files plus
    the review namespace and structurally excludes ``src`` -- see that BUG
    record's "Why this is not a WFC self-development WorkItem" for why a
    WorkItem naming the correction's source files could never admit under
    the shared ceiling. The dedicated policy narrows admission to exactly
    the bounded correction scope instead of widening the shared one.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=IMMUTABLE_FINDING_ECHO_FIX_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "immutable-finding-echo-fix"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=IMMUTABLE_FINDING_ECHO_FIX_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implement = profile.phases["implement"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (*IMMUTABLE_FINDING_ECHO_FIX_CORRECTION_SCOPE, IMMUTABLE_FINDING_ECHO_FIX_REVIEW_ARTIFACT)

    assert work_item.work_item_id == "WI-WFC-IMMUTABLE-FINDING-ECHO-FIX-001"
    assert work_item.initial_phase == "implement"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 4
    assert profile.profile_id == "immutable-finding-echo-fix"
    assert policy.policy_id == "workflow-coordinator-immutable-finding-echo-fix"
    assert tuple(profile.phases) == ("implement", "review", "correct", "rereview")

    # WorkItem.scope is exactly the bounded correction scope plus the single
    # durable review artifact -- the smallest ceiling this commission needs,
    # not the shared developer-ergonomics ceiling.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("Makefile", work_item.scope)
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    for path in IMMUTABLE_FINDING_ECHO_FIX_CORRECTION_SCOPE:
        assert _within_scope(path, work_item.scope)

    # Non-scope: files the accepted BUG record explicitly withholds from any
    # correction commission remain outside this WorkItem's authority.
    for withheld in (
        "src/workflow_coordinator/adapter.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/resources/agent_operating_contract_v6.txt",
        "tests/architecture/test_self_development_governance.py",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # implement/correct share the exact bounded correction scope; review and
    # rereview are independently scoped by the profile to the single durable
    # review artifact alone -- no phase's authorized_scope grants both.
    assert implement.authorized_scope == correct.authorized_scope == IMMUTABLE_FINDING_ECHO_FIX_CORRECTION_SCOPE
    assert review.authorized_scope == rereview.authorized_scope == (IMMUTABLE_FINDING_ECHO_FIX_REVIEW_ARTIFACT,)
    assert set(IMMUTABLE_FINDING_ECHO_FIX_CORRECTION_SCOPE).isdisjoint(review.authorized_scope)
    assert IMMUTABLE_FINDING_ECHO_FIX_REVIEW_ARTIFACT not in implement.authorized_scope
    assert IMMUTABLE_FINDING_ECHO_FIX_REVIEW_ARTIFACT not in correct.authorized_scope

    assert durable_artifacts == {"review": (IMMUTABLE_FINDING_ECHO_FIX_REVIEW_ARTIFACT,)}
    assert _within_scope(IMMUTABLE_FINDING_ECHO_FIX_REVIEW_ARTIFACT, review.authorized_scope)
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "none"
    assert implement.durable_artifact.value == correct.durable_artifact.value == "none"

    # Topology: implement -> review -> {accept: complete, correct} ->
    # correct -> rereview -> {accept: complete, correct}. No
    # investigate/planning/supervisor phase was added.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "implement": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert implement.role == correct.role == "worker"
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_immutable_finding_echo_fix_002_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Successor bounded self-development correction commission: preflight-only proof.

    ``WI-WFC-IMMUTABLE-FINDING-ECHO-FIX-002`` continues predecessor
    ``WI-WFC-IMMUTABLE-FINDING-ECHO-FIX-001`` (attempt
    ``wfc-immutable-finding-echo-fix-20260930-01``, Run
    ``RUN-76e153b40ef1459db4587446b18e02d5``), which stopped
    ``awaiting_human`` after H001 with no trusted commit. This bundle uses
    its own dedicated ``-002`` WorkItem/profile/policy/task files -- the
    predecessor's governing files remain untouched frozen historical
    evidence -- and its scope ceiling is exactly the predecessor's ten
    correction paths plus the single additional path justified by that
    Run's own legitimate scope escalation, ``tests/unit/test_slice4.py``
    (see ``docs/backlog/BUG-WFC-SCOPE-ESCALATION-CONTINUATION-GAP.adoc``),
    plus a fresh review artifact, ``..._review_002.adoc`` -- never the
    predecessor's ``..._review_001.adoc``.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=IMMUTABLE_FINDING_ECHO_FIX_002_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "immutable-finding-echo-fix-002"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=IMMUTABLE_FINDING_ECHO_FIX_002_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implement = profile.phases["implement"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *IMMUTABLE_FINDING_ECHO_FIX_002_CORRECTION_SCOPE,
        IMMUTABLE_FINDING_ECHO_FIX_002_REVIEW_ARTIFACT,
    )
    assert len(IMMUTABLE_FINDING_ECHO_FIX_002_CORRECTION_SCOPE) == 11

    assert work_item.work_item_id == "WI-WFC-IMMUTABLE-FINDING-ECHO-FIX-002"
    assert work_item.initial_phase == "implement"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 4
    assert profile.profile_id == "immutable-finding-echo-fix-002"
    assert policy.policy_id == "workflow-coordinator-immutable-finding-echo-fix-002"
    assert tuple(profile.phases) == ("implement", "review", "correct", "rereview")

    # WorkItem.scope is exactly the eleven correction paths (the
    # predecessor's ten plus the one newly authorized Slice-4 test) plus the
    # single fresh durable review artifact -- the smallest authority
    # extension this successor commission needs.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("Makefile", work_item.scope)
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    for path in IMMUTABLE_FINDING_ECHO_FIX_002_CORRECTION_SCOPE:
        assert _within_scope(path, work_item.scope)

    # Exact delta versus predecessor authority: precisely one newly
    # authorized product/test path, and nothing withdrawn.
    assert set(IMMUTABLE_FINDING_ECHO_FIX_002_CORRECTION_SCOPE) - set(
        IMMUTABLE_FINDING_ECHO_FIX_CORRECTION_SCOPE
    ) == {"tests/unit/test_slice4.py"}
    assert set(IMMUTABLE_FINDING_ECHO_FIX_CORRECTION_SCOPE) - set(
        IMMUTABLE_FINDING_ECHO_FIX_002_CORRECTION_SCOPE
    ) == set()

    # Non-scope: files the accepted BUG record explicitly withholds from any
    # correction commission remain outside this successor WorkItem's
    # authority, and so does every predecessor governing file and the
    # predecessor's own review artifact -- this successor neither mutates
    # nor reuses them.
    for withheld in (
        "src/workflow_coordinator/adapter.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/resources/agent_operating_contract_v6.txt",
        "tests/architecture/test_self_development_governance.py",
        "governance/work-items/immutable-finding-echo-fix.yaml",
        "governance/work-items/immutable-finding-echo-fix-task.txt",
        "governance/self-development/immutable-finding-echo-profile.yaml",
        "governance/self-development/immutable-finding-echo-policy.yaml",
        IMMUTABLE_FINDING_ECHO_FIX_REVIEW_ARTIFACT,
    ):
        assert not _within_scope(withheld, work_item.scope)
    assert IMMUTABLE_FINDING_ECHO_FIX_REVIEW_ARTIFACT not in work_item.scope

    # implement/correct share the exact eleven-path correction scope; review
    # and rereview are independently scoped by the profile to the single
    # fresh durable review artifact alone -- no phase's authorized_scope
    # grants both.
    assert (
        implement.authorized_scope
        == correct.authorized_scope
        == IMMUTABLE_FINDING_ECHO_FIX_002_CORRECTION_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        IMMUTABLE_FINDING_ECHO_FIX_002_REVIEW_ARTIFACT,
    )
    assert set(IMMUTABLE_FINDING_ECHO_FIX_002_CORRECTION_SCOPE).isdisjoint(review.authorized_scope)
    assert IMMUTABLE_FINDING_ECHO_FIX_002_REVIEW_ARTIFACT not in implement.authorized_scope
    assert IMMUTABLE_FINDING_ECHO_FIX_002_REVIEW_ARTIFACT not in correct.authorized_scope

    assert durable_artifacts == {"review": (IMMUTABLE_FINDING_ECHO_FIX_002_REVIEW_ARTIFACT,)}
    assert _within_scope(IMMUTABLE_FINDING_ECHO_FIX_002_REVIEW_ARTIFACT, review.authorized_scope)
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "none"
    assert implement.durable_artifact.value == correct.durable_artifact.value == "none"

    # Topology is exactly the predecessor's bounded bootstrap-safe topology:
    # implement -> review -> {accept: complete, correct} -> correct ->
    # rereview -> {accept: complete, correct}. No investigate/planning/
    # supervisor phase was added.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "implement": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert implement.role == correct.role == "worker"

    # Bootstrap PRE-FIX Runtime property preserved: review/rereview remain
    # gate reviews with no Finding-disposition authority at all. The
    # generic provider-free proof that a gate-authority Hop may legally
    # request `changes_required` / `route: correct` with an empty findings
    # tuple already covers this exact review/rereview shape -- see
    # `test_bootstrap_changes_required_correct_route_is_legal_with_empty_finding_set`
    # and its rereview counterpart below, which reproduce this shape
    # directly against the unmodified production reducer.
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_scope_escalation_continuation_design_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Bounded design/review-only commission: preflight-only proof.

    This governing bundle commissions an independently reviewed,
    implementation-ready design for a successor-Run semantic continuation
    mechanism (docs/backlog/BUG-WFC-SCOPE-ESCALATION-CONTINUATION-GAP.adoc).
    It must parse, stay within policy/profile authority, use the bounded
    four-phase bootstrap-safe topology (design -> review ->
    {accept, correct} -> correct -> rereview -> {accept, correct}) with
    max_autonomous_hops == 6, and hold no mutation authority over
    src/workflow_coordinator/**, governance/, workflow-coordinator-runtime,
    or any other file: its only authorized scope is the design artifact and
    its single durable review artifact.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_DESIGN_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "scope-escalation-continuation-design"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_DESIGN_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    design = profile.phases["design"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *SCOPE_ESCALATION_CONTINUATION_DESIGN_SCOPE,
        SCOPE_ESCALATION_CONTINUATION_DESIGN_REVIEW_ARTIFACT,
    )

    assert work_item.work_item_id == "WI-WFC-SCOPE-ESCALATION-CONTINUATION-DESIGN-001"
    assert work_item.initial_phase == "design"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 6
    assert profile.profile_id == "scope-escalation-continuation-design"
    assert policy.policy_id == "workflow-coordinator-scope-escalation-continuation-design"
    assert tuple(profile.phases) == ("design", "review", "correct", "rereview")

    # WorkItem.scope is exactly the design artifact plus its single durable
    # review artifact -- the smallest ceiling this commission needs.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("src/workflow_coordinator", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope(
        "docs/backlog/BUG-WFC-SCOPE-ESCALATION-CONTINUATION-GAP.adoc", work_item.scope
    )
    for path in SCOPE_ESCALATION_CONTINUATION_DESIGN_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over production source, ordinary product tests,
    # or any other governance bundle.
    for withheld in (
        "src/workflow_coordinator/model.py",
        "src/workflow_coordinator/reducer.py",
        "src/workflow_coordinator/operator.py",
        "src/workflow_coordinator/serialization.py",
        "src/workflow_coordinator/persistence.py",
        "tests/architecture/test_self_development_governance.py",
        "tests/unit/test_reducer.py",
        "workflow-coordinator-runtime",
        "governance/work-items/immutable-finding-echo-fix-002.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # design/correct share the exact single-path design scope; review and
    # rereview are independently scoped by the profile to the single durable
    # review artifact alone -- no phase's authorized_scope grants both, and
    # neither ever grants the BUG record itself.
    assert (
        design.authorized_scope
        == correct.authorized_scope
        == SCOPE_ESCALATION_CONTINUATION_DESIGN_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        SCOPE_ESCALATION_CONTINUATION_DESIGN_REVIEW_ARTIFACT,
    )
    assert set(SCOPE_ESCALATION_CONTINUATION_DESIGN_SCOPE).isdisjoint(review.authorized_scope)
    assert SCOPE_ESCALATION_CONTINUATION_DESIGN_REVIEW_ARTIFACT not in design.authorized_scope
    assert SCOPE_ESCALATION_CONTINUATION_DESIGN_REVIEW_ARTIFACT not in correct.authorized_scope

    assert durable_artifacts == {"review": (SCOPE_ESCALATION_CONTINUATION_DESIGN_REVIEW_ARTIFACT,)}
    assert _within_scope(SCOPE_ESCALATION_CONTINUATION_DESIGN_REVIEW_ARTIFACT, review.authorized_scope)
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "none"
    assert design.durable_artifact.value == correct.durable_artifact.value == "none"

    # Topology: design -> review -> {accept: complete, correct} -> correct ->
    # rereview -> {accept: complete, correct}. No investigate/planning/
    # supervisor phase was added, and no phase carries implementation
    # authority over product source.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "design": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert design.role == correct.role == "worker"
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    # Role/persona separation: distinct fresh independent-review persona,
    # never the design/correct persona.
    assert work_item.personas[WorkflowRole.WORKER] == "senior software architect"
    assert (
        work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]
        == "independent adversarial architecture reviewer"
    )
    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_correction_convergence_escape_design_001_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Bounded design/review-only commission: preflight-only proof.

    This governing bundle commissions an independently reviewed,
    implementation-ready design for a generic, domain-neutral
    correction-loop convergence-escape mechanism
    (docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc). It must
    parse, stay within policy/profile authority, use the bounded four-phase
    bootstrap-safe topology (design -> review -> {accept, correct} ->
    correct -> rereview -> {accept, correct}) with max_autonomous_hops == 6,
    and hold no mutation authority over src/workflow_coordinator/**,
    governance/, workflow-coordinator-runtime, the
    workflow-coordinator-run-evidence repository, canonical Project Memory,
    the source BUG itself, any historical FTR-WFC-SCOPE-ESCALATION-
    CONTINUATION review artifact, or any other governance bundle: its only
    authorized scope is the new design artifact and its single dedicated
    durable review artifact.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-design-001"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    design = profile.phases["design"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT,
    )

    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-DESIGN-001"
    assert work_item.initial_phase == "design"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 6
    assert profile.profile_id == "correction-convergence-escape-design-001"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-design-001"
    assert tuple(profile.phases) == ("design", "review", "correct", "rereview")

    # WorkItem.scope is exactly the new design artifact plus its single
    # dedicated durable review artifact -- the smallest ceiling this
    # commission needs.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("src/workflow_coordinator", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope("workflow-coordinator-run-evidence", work_item.scope)
    assert not _within_scope("project_memory/canonical", work_item.scope)
    assert not _within_scope(
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc", work_item.scope
    )
    assert not _within_scope(
        "docs/backlog/BUG-WFC-SCOPE-ESCALATION-CONTINUATION-GAP.adoc", work_item.scope
    )
    assert not _within_scope(
        "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION.adoc", work_item.scope
    )
    for historical_review_artifact in (
        SCOPE_ESCALATION_CONTINUATION_DESIGN_REVIEW_ARTIFACT,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_REVIEW_ARTIFACT,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_REVIEW_ARTIFACT,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_REVIEW_ARTIFACT,
    ):
        assert not _within_scope(historical_review_artifact, work_item.scope)
    for path in CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over production source, ordinary product tests,
    # or any other governance bundle (including this BUG's own motivating
    # predecessor commission).
    for withheld in (
        "src/workflow_coordinator/model.py",
        "src/workflow_coordinator/reducer.py",
        "src/workflow_coordinator/operator.py",
        "src/workflow_coordinator/serialization.py",
        "src/workflow_coordinator/persistence.py",
        "tests/architecture/test_self_development_governance.py",
        "tests/unit/test_reducer.py",
        "workflow-coordinator-runtime",
        "workflow-coordinator-run-evidence",
        "governance/work-items/scope-escalation-continuation-design.yaml",
        "governance/self-development/scope-escalation-continuation-design-profile.yaml",
        "governance/work-items/scope-escalation-continuation-implementation-004.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # design/correct share the exact single-path design scope; review and
    # rereview are independently scoped by the profile to the single
    # dedicated durable review artifact alone -- no phase's authorized_scope
    # grants both, and neither ever grants the source BUG itself.
    assert (
        design.authorized_scope
        == correct.authorized_scope
        == CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_SCOPE).isdisjoint(review.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT not in design.authorized_scope
    assert CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT not in correct.authorized_scope

    assert durable_artifacts == {
        "review": (CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT,),
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT, review.authorized_scope
    )
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    # Both reviewer phases durably mutate exactly the dedicated review
    # artifact -- `rereview` preserves and appends to the same durable
    # history `review` started, rather than being unable to record its own
    # fresh disposition.
    assert review.durable_artifact.value == rereview.durable_artifact.value == "required"
    assert design.durable_artifact.value == correct.durable_artifact.value == "none"
    assert review.authorized_actions == rereview.authorized_actions == ("review", "edit", "commit")
    assert design.authorized_actions == correct.authorized_actions == ("edit", "commit")
    # Neither reviewer phase may edit the FTR design artifact itself.
    assert "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc" not in review.authorized_scope
    assert "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc" not in rereview.authorized_scope

    # Topology: design -> review -> {accept: complete, correct} -> correct ->
    # rereview -> {accept: complete, correct}. No investigate/planning/
    # supervisor/diagnosis phase was added, and no phase carries
    # implementation authority over product source.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "design": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert design.role == correct.role == "worker"
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    # Role/persona separation: distinct fresh independent-review persona,
    # never the design/correct persona. The worker persona is
    # domain-neutral wording -- the mechanism being designed must plausibly
    # apply outside software development, so the commissioned role avoids
    # "software architect" in favor of "workflow-systems architect".
    assert work_item.personas[WorkflowRole.WORKER] == "senior workflow-systems architect"
    assert (
        work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]
        == "independent adversarial architecture reviewer"
    )
    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-run-evidence", policy.work_item_scope_ceiling)
    assert not _within_scope("project_memory/canonical", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    # Admission happened entirely through provider-free preflight: no
    # provider/executable was ever invoked (the placeholder `executable`
    # value is never resolved or run by `_preflight`), and the state root
    # the launch would eventually use was never created by this process.
    assert not state_root.exists()


def test_correction_convergence_escape_design_revision_001_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Bounded Design-Revision-only commission: preflight-only proof.

    This governing bundle commissions an independently reviewed,
    implementation-ready Design Revision of the narrow convergence-fact
    derivation authority surface within the accepted convergence-escape
    architecture, prompted by the empirical Slice-2 blocker S2-IR-001. It
    must parse, stay within policy/profile authority, use the bounded
    four-phase topology (design -> review -> {accept, revise} -> revise ->
    rereview -> {accept, revise}) with max_autonomous_hops == 2, and hold no
    mutation authority over src/workflow_coordinator/**, governance/,
    workflow-coordinator-runtime, the workflow-coordinator-run-evidence
    repository, canonical Project Memory, the original accepted FTR and its
    review artifact, the Slice-2 implementation-review artifact, or any
    other governance bundle: its only authorized scope is the new revision
    artifact and its single dedicated durable review artifact.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-design-revision-001"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_GOVERNING_PATHS,
    ))
    texts = result[4]
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    design = profile.phases["design"]
    review = profile.phases["review"]
    revise = profile.phases["revise"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,
    )

    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-DESIGN-REVISION-001"
    assert work_item.initial_phase == "design"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 2
    assert profile.profile_id == "correction-convergence-escape-design-revision-001"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-design-revision-001"
    assert tuple(profile.phases) == ("design", "review", "revise", "rereview")

    # WorkItem.scope is exactly the new revision artifact plus its single
    # dedicated durable review artifact -- the smallest ceiling this
    # commission needs.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("src/workflow_coordinator", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope("workflow-coordinator-run-evidence", work_item.scope)
    assert not _within_scope("project_memory/canonical", work_item.scope)
    assert not _within_scope(
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc", work_item.scope
    )
    assert not _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT[0], work_item.scope
    )
    assert not _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_REVIEW_ARTIFACT, work_item.scope
    )
    assert not _within_scope(
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc", work_item.scope
    )
    for path in CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over production source, ordinary product tests,
    # or any other governance bundle (including the original accepted design
    # and its review/implementation-review history).
    for withheld in (
        "src/workflow_coordinator/model.py",
        "src/workflow_coordinator/reducer.py",
        "src/workflow_coordinator/persistence.py",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/operator.py",
        "src/workflow_coordinator/serialization.py",
        "tests/architecture/test_self_development_governance.py",
        "tests/unit/test_reducer.py",
        "workflow-coordinator-runtime",
        "workflow-coordinator-run-evidence",
        "governance/work-items/correction-convergence-escape-implementation-s2-001.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s2-001-profile.yaml",
        "governance/work-items/correction-convergence-escape-design-001.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # design/revise share the exact single-path revision scope; review and
    # rereview are independently scoped by the profile to the single
    # dedicated durable review artifact alone -- no phase's authorized_scope
    # grants both, and neither ever grants the original accepted FTR.
    assert (
        design.authorized_scope
        == revise.authorized_scope
        == CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE).isdisjoint(
        review.authorized_scope
    )
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT
        not in design.authorized_scope
    )
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT
        not in revise.authorized_scope
    )

    assert durable_artifacts == {
        "review": (CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,),
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,
        review.authorized_scope,
    )
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,
        rereview.authorized_scope,
    )
    assert review.durable_artifact.value == rereview.durable_artifact.value == "required"
    assert design.durable_artifact.value == revise.durable_artifact.value == "none"
    assert review.authorized_actions == rereview.authorized_actions == ("review", "edit", "commit")
    assert design.authorized_actions == revise.authorized_actions == ("edit", "commit")
    # Neither reviewer phase may edit the revision artifact itself.
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE[0]
        not in review.authorized_scope
    )
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE[0]
        not in rereview.authorized_scope
    )

    # Topology: design -> review -> {accept: complete, revise} -> revise ->
    # rereview -> {accept: complete, revise}. No production-implementation
    # authority, escape routing, or S2-002 commissioning phase was added.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "design": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("revise", "goto", "revise"),
        ),
        "revise": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("revise", "goto", "revise"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert design.role == revise.role == "worker"
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    # The task text pins the exact empirical motivation this commission
    # exists for: S2-001 H002 and its blocker S2-IR-001.
    task_text = texts["task"]
    assert "S2-IR-001" in task_text
    assert "H002" in task_text
    assert "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S2-001" in task_text
    assert "830bdf5536e28a52502bb6faf9172e57093150cb" in task_text

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-run-evidence", policy.work_item_scope_ceiling)
    assert not _within_scope("project_memory/canonical", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    # Admission happened entirely through provider-free preflight: no
    # provider/executable was ever invoked, no Runtime/evidence/state/
    # Project-Memory mutation occurred, and the state root the launch would
    # eventually use was never created by this process.
    assert not state_root.exists()


def test_correction_convergence_escape_design_revision_001_continuation_001_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Bounded successor commission: correct and rereview DR1-IR-001 only.

    This governing bundle authorizes a fresh bounded successor Commission,
    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-DESIGN-REVISION-001-CONTINUATION-001``,
    continuing the frozen predecessor Design-Revision Commission
    (``RUN-5979c331d8ed4cc0a0ab0166eb47f4f8``) that stopped
    ``awaiting_human_budget`` after H002 validly requested ``revise`` against
    stable blocker ``DR1-IR-001``. It must parse, stay within policy/profile
    authority, use a correction/rereview-only topology (``revise ->
    rereview -> {accept, revise}``, with no bootstrap ``design``/``review``
    phase) with ``max_autonomous_hops == 6``, and hold mutation authority
    over exactly the two existing additive Design-Revision artifacts -- never
    the original accepted FTR, its review, the Slice-1/Slice-2
    implementation-review artifacts, the source BUG, the predecessor's own
    governance bundle, production source, ordinary product tests,
    workflow-coordinator-runtime, the evidence repository/run ledger, or
    canonical Project Memory. The task text must pin the predecessor Run,
    H001 design candidate, H002/final HEAD, and stable blocker DR1-IR-001.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_CONTINUATION_001_GOVERNING_PATHS,
    )
    state_root = (
        tmp_path / "external-state" / "correction-convergence-escape-design-revision-001-continuation-001"
    )

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_CONTINUATION_001_GOVERNING_PATHS,
    ))
    texts = result[4]
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    revise = profile.phases["revise"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,
    )

    assert work_item.work_item_id == (
        "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-DESIGN-REVISION-001-CONTINUATION-001"
    )
    # Initial phase is exactly `revise` -- no bootstrap design/review phase
    # is added merely to replay predecessor history.
    assert work_item.initial_phase == "revise"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 6
    assert profile.profile_id == "correction-convergence-escape-design-revision-001-continuation-001"
    assert policy.policy_id == (
        "workflow-coordinator-correction-convergence-escape-design-revision-001-continuation-001"
    )
    # Topology is exactly revise -> rereview -> {accept, revise}; no design
    # or review phase exists in this profile.
    assert tuple(profile.phases) == ("revise", "rereview")

    # Exact two-file WorkItem scope: the same existing Design Revision and
    # its dedicated review artifact the predecessor already used.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("src/workflow_coordinator", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope("workflow-coordinator-run-evidence", work_item.scope)
    assert not _within_scope("project_memory/canonical", work_item.scope)
    assert not _within_scope(
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc", work_item.scope
    )
    assert not _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT[0], work_item.scope
    )
    assert not _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_REVIEW_ARTIFACT, work_item.scope
    )
    assert not _within_scope(
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc", work_item.scope
    )
    for path in CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over production source, ordinary product tests,
    # the predecessor's own governance bundle, or any other governance
    # bundle.
    for withheld in (
        "src/workflow_coordinator/model.py",
        "src/workflow_coordinator/reducer.py",
        "src/workflow_coordinator/persistence.py",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/operator.py",
        "src/workflow_coordinator/serialization.py",
        "tests/architecture/test_self_development_governance.py",
        "tests/unit/test_reducer.py",
        "workflow-coordinator-runtime",
        "workflow-coordinator-run-evidence",
        "governance/work-items/correction-convergence-escape-design-revision-001.yaml",
        "governance/self-development/correction-convergence-escape-design-revision-001-profile.yaml",
        "governance/self-development/correction-convergence-escape-design-revision-001-policy.yaml",
        "governance/work-items/correction-convergence-escape-design-revision-001-task.txt",
        "governance/work-items/correction-convergence-escape-implementation-s2-001.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s2-001-profile.yaml",
        "governance/work-items/correction-convergence-escape-design-001.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # `revise` authorized scope is only the existing Design Revision
    # artifact; `rereview` authorized scope is only the existing review
    # artifact. Neither phase's authorized_scope grants both, and neither
    # ever grants the original accepted FTR.
    assert revise.authorized_scope == CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE
    assert rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE).isdisjoint(
        rereview.authorized_scope
    )
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT
        not in revise.authorized_scope
    )

    # Rereview durable artifact is required and exactly the existing review
    # artifact.
    assert durable_artifacts == {
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,
        rereview.authorized_scope,
    )
    assert rereview.durable_artifact.value == "required"
    assert revise.durable_artifact.value == "none"
    assert rereview.authorized_actions == ("review", "edit", "commit")
    assert revise.authorized_actions == ("edit", "commit")

    # Exact topology: no design/review bootstrap phase, no accept-without-
    # DR1-IR-001-disposition shortcut, no S2-002 commissioning phase.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "revise": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("revise", "goto", "revise"),
        ),
    }
    assert rereview.role == "independent_reviewer"
    assert revise.role == "worker"
    assert rereview.review.finding_authority == ()
    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    # The task text pins the exact predecessor lineage and stable blocker
    # this successor continues.
    task_text = texts["task"]
    assert "RUN-5979c331d8ed4cc0a0ab0166eb47f4f8" in task_text
    assert "14830d3306056bb41f65111e941dc436a7bee5dd" in task_text
    assert "8594a5ef93b173bfc920b08bfb3e672952165547" in task_text
    assert "DR1-IR-001" in task_text

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-run-evidence", policy.work_item_scope_ceiling)
    assert not _within_scope("project_memory/canonical", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    # Admission happened entirely through provider-free preflight: no
    # provider/executable was ever invoked, no Runtime/evidence/state/
    # Project-Memory mutation occurred, and the state root the launch would
    # eventually use was never created by this process.
    assert not state_root.exists()


def test_scope_escalation_continuation_implementation_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Bounded implementation commission for the accepted continuation design.

    ``WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-001`` implements the
    successor-Run semantic continuation mechanism accepted by
    ``docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION.adoc``. It must
    parse, stay within policy/profile authority, use the bounded four-phase
    bootstrap-safe topology (``implement -> review -> {accept, correct} ->
    correct -> rereview -> {accept, correct}``) with ``max_autonomous_hops ==
    6``, and hold authority over exactly the design's named production/test
    paths plus one durable implementation-review artifact -- never the design
    FTR, its own review artifact, this WorkItem's own BUG record, or any
    other governance bundle. Unlike every prior bootstrap-safe profile in
    this module, ``rereview`` here also carries ``durable_artifact:
    required`` with ``edit``/``commit`` authority on that same review
    artifact, so a later fresh rereview leaves its own durable acceptance or
    correction record and trusted commit rather than only observing and
    routing.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "scope-escalation-continuation-implementation"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implement = profile.phases["implement"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT,
    )
    assert len(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE) == 12

    assert work_item.work_item_id == "WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-001"
    assert work_item.initial_phase == "implement"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 6
    assert profile.profile_id == "scope-escalation-continuation-implementation"
    assert policy.policy_id == "workflow-coordinator-scope-escalation-continuation-implementation"
    assert tuple(profile.phases) == ("implement", "review", "correct", "rereview")

    # WorkItem.scope is exactly the accepted design's twelve production/test
    # paths plus the single fresh implementation-review artifact.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("Makefile", work_item.scope)
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope(
        "docs/backlog/BUG-WFC-SCOPE-ESCALATION-CONTINUATION-GAP.adoc", work_item.scope
    )
    for path in SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over the accepted design FTR, its own review
    # history, any other governance bundle, or the frozen predecessor Run.
    for withheld in (
        "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION.adoc",
        "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION_review_001.adoc",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/adapter.py",
        "tests/architecture/test_self_development_governance.py",
        "workflow-coordinator-runtime",
        "governance/work-items/scope-escalation-continuation-design.yaml",
        "governance/self-development/scope-escalation-continuation-design-profile.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # implement/correct share the exact twelve-path implementation scope;
    # review and rereview are independently scoped by the profile to the
    # single durable review artifact alone -- no phase's authorized_scope
    # grants both.
    assert (
        implement.authorized_scope
        == correct.authorized_scope
        == SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT,
    )
    assert set(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE).isdisjoint(review.authorized_scope)
    assert SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT not in implement.authorized_scope
    assert SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT not in correct.authorized_scope

    # The Commission-specific acceptance-recording requirement: both review
    # AND rereview require, and are mapped, the same durable artifact, and
    # both carry edit+commit authority on it -- the mechanical property that
    # forces a trusted commit touching that artifact on every review and
    # rereview disposition, not only the first review.
    assert durable_artifacts == {
        "review": (SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT,),
        "rereview": (SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT,),
    }
    assert _within_scope(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT, review.authorized_scope)
    assert _within_scope(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT, rereview.authorized_scope)
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "required"
    assert implement.durable_artifact.value == correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(review.authorized_actions))
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))

    # Topology: implement -> review -> {accept: complete, correct} ->
    # correct -> rereview -> {accept: complete, correct}. No
    # investigate/planning/supervisor phase was added, and no phase carries
    # implementation authority outside the twelve named production/test
    # paths.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "implement": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert implement.role == correct.role == "worker"
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    # Role/persona separation: distinct fresh independent-review persona,
    # never the implement/correct persona.
    assert work_item.personas[WorkflowRole.WORKER] == (
        "senior software engineer implementing an authority- and trust-sensitive "
        "Run state, persistence, and operator change"
    )
    assert work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER] == (
        "independent adversarial reviewer for authority widening, predecessor "
        "mutation, and salvage trust boundaries"
    )
    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_scope_escalation_continuation_implementation_002_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Successor bounded correction/acceptance commission: preflight-only proof.

    ``WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-002`` continues
    predecessor ``WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-001``
    (attempt ``wfc-scope-escalation-continuation-implementation-20261001-01``,
    Run ``RUN-692338c488044e8e9393a1585d118eb8``), which exhausted its 6/6
    autonomous Hop budget at H006 ``changes_required`` with a clean committed
    final HEAD, ``638d2c60a90831f2291b94fac8b25c76cd3fd929``, and no trusted
    commit beyond it. Unlike the predecessor's four-phase
    ``implement -> review -> {accept, correct} -> correct -> rereview ->
    {accept, correct}`` topology, this successor commission begins directly
    at ``correct`` -- the predecessor already has an implementation candidate
    and an independent H006 ``changes_required`` disposition -- and loops
    ``correct -> rereview -> {accept: complete, correct: correct}`` for up to
    ``max_autonomous_hops: 8`` (four bounded correction/rereview cycles). It
    uses its own dedicated ``-002`` WorkItem/profile/policy/task files -- the
    predecessor's governing files remain untouched frozen historical
    evidence -- and its scope ceiling is exactly the same six production
    paths and six ordinary test paths already authorized to the predecessor,
    plus a NEW durable review artifact,
    ``..._implementation_review_002.adoc`` -- never the predecessor's own
    ``..._implementation_review_001.adoc``.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "scope-escalation-continuation-implementation-002"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_REVIEW_ARTIFACT,
    )
    assert len(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE) == 12

    assert work_item.work_item_id == "WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-002"
    assert work_item.initial_phase == "correct"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 8
    assert profile.profile_id == "scope-escalation-continuation-implementation-002"
    assert policy.policy_id == "workflow-coordinator-scope-escalation-continuation-implementation-002"
    assert tuple(profile.phases) == ("correct", "rereview")

    # WorkItem.scope is exactly the predecessor's twelve production/test
    # paths plus a fresh, dedicated implementation-review artifact.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("Makefile", work_item.scope)
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope(
        "docs/backlog/BUG-WFC-SCOPE-ESCALATION-CONTINUATION-GAP.adoc", work_item.scope
    )
    for path in SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over the accepted design FTR, its own review
    # history, the predecessor's own review artifact, any other governance
    # bundle, or either frozen predecessor Run.
    for withheld in (
        "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION.adoc",
        "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION_review_001.adoc",
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT,
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/adapter.py",
        "tests/architecture/test_self_development_governance.py",
        "workflow-coordinator-runtime",
        "governance/work-items/scope-escalation-continuation-implementation.yaml",
        "governance/self-development/scope-escalation-continuation-implementation-profile.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # correct shares the exact twelve-path implementation scope; rereview is
    # independently scoped by the profile to the single, fresh durable
    # review artifact alone -- no phase's authorized_scope grants both.
    assert correct.authorized_scope == SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE
    assert rereview.authorized_scope == (
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_REVIEW_ARTIFACT,
    )
    assert set(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE).isdisjoint(rereview.authorized_scope)
    assert SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_REVIEW_ARTIFACT not in correct.authorized_scope

    # Only rereview carries the durable-artifact acceptance-recording
    # requirement -- correct has no review authority or artifact in this
    # topology.
    assert durable_artifacts == {
        "rereview": (SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert rereview.durable_artifact.value == "required"
    assert correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))
    assert {"edit", "commit"}.issubset(set(correct.authorized_actions))

    # Topology: correct -> rereview -> {accept: complete, correct}. No
    # implement/review preamble, investigate/planning/supervisor phase was
    # added -- the predecessor already produced an implementation candidate
    # and an independent changes_required disposition, so this commission
    # begins directly at correction.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert rereview.role == "independent_reviewer"
    assert correct.role == "worker"
    assert rereview.review.finding_authority == ()

    # Role/persona separation preserved identically from the predecessor
    # commission: distinct fresh independent-review persona, never the
    # correction persona.
    assert work_item.personas[WorkflowRole.WORKER] == (
        "senior software engineer implementing an authority- and trust-sensitive "
        "Run state, persistence, and operator change"
    )
    assert work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER] == (
        "independent adversarial reviewer for authority widening, predecessor "
        "mutation, and salvage trust boundaries"
    )
    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_scope_escalation_continuation_implementation_003_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Second-successor bounded correction/acceptance commission: preflight-only proof.

    ``WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-003`` continues
    predecessor ``WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-002``
    (Run ``RUN-7cac69cf29eb42439f86c2576083f798``), which exhausted its 8/8
    autonomous Hop budget at H008 ``changes_required`` with a clean committed
    final HEAD, ``8a237efaea1a6d0923721bb0480c802c5ebcec67``, and no trusted
    commit beyond it. H008 confirms the shared-salvage, publication, tamper,
    and separate-checkout counterexamples found through H007 are closed and
    requires no scope expansion; its sole remaining blocking group, H008-R1,
    is that the mandatory acceptance matrix is still materially incomplete.
    This commission reuses the same two-phase
    ``correct -> rereview -> {accept: complete, correct: correct}`` topology
    as its predecessor, bounded this time to ``max_autonomous_hops: 6``
    (three bounded correction/rereview cycles -- fewer than the predecessor's
    four, per the maintainer's explicit new budget). It uses its own
    dedicated ``-003`` WorkItem/profile/policy/task files -- both
    predecessors' governing files remain untouched frozen historical
    evidence -- and its scope ceiling is exactly the same six production
    paths and six ordinary test paths already authorized to both
    predecessors, plus a NEW durable review artifact,
    ``..._implementation_review_003.adoc`` -- never either predecessor's own
    ``..._implementation_review_001.adoc`` or
    ``..._implementation_review_002.adoc``. This commission also withholds
    authority over the unrelated, newly recorded
    ``BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc``, which documents a
    correction-loop convergence pattern across both predecessor Runs but is
    explicitly not this commission's subject.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "scope-escalation-continuation-implementation-003"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_REVIEW_ARTIFACT,
    )
    assert len(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE) == 12

    assert work_item.work_item_id == "WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-003"
    assert work_item.initial_phase == "correct"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 6
    assert profile.profile_id == "scope-escalation-continuation-implementation-003"
    assert policy.policy_id == "workflow-coordinator-scope-escalation-continuation-implementation-003"
    assert tuple(profile.phases) == ("correct", "rereview")

    # WorkItem.scope is exactly both predecessors' twelve production/test
    # paths plus a fresh, dedicated implementation-review artifact.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("Makefile", work_item.scope)
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope(
        "docs/backlog/BUG-WFC-SCOPE-ESCALATION-CONTINUATION-GAP.adoc", work_item.scope
    )
    assert not _within_scope(
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc", work_item.scope
    )
    for path in SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over the accepted design FTR, its own review
    # history, either predecessor's own review artifact, the unrelated
    # carryover BUG, any other governance bundle, or any frozen predecessor
    # Run.
    for withheld in (
        "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION.adoc",
        "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION_review_001.adoc",
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_REVIEW_ARTIFACT,
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/adapter.py",
        "tests/architecture/test_self_development_governance.py",
        "workflow-coordinator-runtime",
        "governance/work-items/scope-escalation-continuation-implementation.yaml",
        "governance/self-development/scope-escalation-continuation-implementation-profile.yaml",
        "governance/work-items/scope-escalation-continuation-implementation-002.yaml",
        "governance/self-development/scope-escalation-continuation-implementation-002-profile.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # correct shares the exact twelve-path implementation scope; rereview is
    # independently scoped by the profile to the single, fresh durable
    # review artifact alone -- no phase's authorized_scope grants both.
    assert correct.authorized_scope == SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE
    assert rereview.authorized_scope == (
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_REVIEW_ARTIFACT,
    )
    assert set(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE).isdisjoint(rereview.authorized_scope)
    assert SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_REVIEW_ARTIFACT not in correct.authorized_scope

    # Only rereview carries the durable-artifact acceptance-recording
    # requirement -- correct has no review authority or artifact in this
    # topology.
    assert durable_artifacts == {
        "rereview": (SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert rereview.durable_artifact.value == "required"
    assert correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))
    assert {"edit", "commit"}.issubset(set(correct.authorized_actions))

    # Topology: correct -> rereview -> {accept: complete, correct}, bounded
    # to six Hops (three correction/rereview cycles) -- fewer than the
    # predecessor commission's eight, per the maintainer's explicit new
    # budget. No implement/review preamble or planning/supervisor phase was
    # added.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert rereview.role == "independent_reviewer"
    assert correct.role == "worker"
    assert rereview.review.finding_authority == ()

    # Role/persona separation preserved identically from both predecessor
    # commissions: distinct fresh independent-review persona, never the
    # correction persona.
    assert work_item.personas[WorkflowRole.WORKER] == (
        "senior software engineer implementing an authority- and trust-sensitive "
        "Run state, persistence, and operator change"
    )
    assert work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER] == (
        "independent adversarial reviewer for authority widening, predecessor "
        "mutation, and salvage trust boundaries"
    )
    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_scope_escalation_continuation_implementation_004_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Third-successor bounded correction/acceptance commission: preflight-only proof.

    ``WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-004`` continues
    predecessor ``WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-003``
    (Run ``RUN-c30fec4573134508975b5342ec5b2c28``), which exhausted its 6/6
    autonomous Hop budget at H006 ``changes_required`` with a clean committed
    final HEAD, ``f189c4c07475d3bcfed938026563acbeabe79005``, and no trusted
    commit beyond it. H006 confirms Run wire compatibility/legacy eligibility,
    the layered-stop regression, salvage containment/rendering, and clean
    successor/independent acceptance are closed and requires no scope
    expansion; its three remaining blocking groups -- H006-R1 (split here into
    the stable identifiers R1A, a false-failure-after-publication defect, and
    R1B, the complete public two-artifact publication matrix), H006-R2 (R2,
    frozen predecessor identity at preparation), and H006-R3 (R3, status/
    launch next-action output) -- are this commission's complete correction
    obligation. This commission reuses the same two-phase
    ``correct -> rereview -> {accept: complete, correct: correct}`` topology
    as all three predecessors, again bounded to ``max_autonomous_hops: 6``
    (three bounded correction/rereview cycles). It uses its own dedicated
    ``-004`` WorkItem/profile/policy/task files -- all three predecessors'
    governing files remain untouched frozen historical evidence -- and its
    scope ceiling is exactly the same six production paths and six ordinary
    test paths already authorized to all three predecessors, plus a NEW
    durable review artifact, ``..._implementation_review_004.adoc`` -- never
    any predecessor's own ``..._implementation_review_001.adoc``,
    ``..._implementation_review_002.adoc``, or
    ``..._implementation_review_003.adoc``. This commission also withholds
    authority over the unrelated ``BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc``,
    which documents a correction-loop convergence pattern across the first two
    predecessor Runs but is explicitly not this commission's subject.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "scope-escalation-continuation-implementation-004"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_REVIEW_ARTIFACT,
    )
    assert len(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE) == 12

    assert work_item.work_item_id == "WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-004"
    assert work_item.initial_phase == "correct"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 6
    assert profile.profile_id == "scope-escalation-continuation-implementation-004"
    assert policy.policy_id == "workflow-coordinator-scope-escalation-continuation-implementation-004"
    assert tuple(profile.phases) == ("correct", "rereview")

    # WorkItem.scope is exactly all three predecessors' twelve production/test
    # paths plus a fresh, dedicated implementation-review artifact.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("Makefile", work_item.scope)
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope(
        "docs/backlog/BUG-WFC-SCOPE-ESCALATION-CONTINUATION-GAP.adoc", work_item.scope
    )
    assert not _within_scope(
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc", work_item.scope
    )
    for path in SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over the accepted design FTR, its own review
    # history, any predecessor's own review artifact, the unrelated
    # carryover BUG, any other governance bundle, or any frozen predecessor
    # Run.
    for withheld in (
        "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION.adoc",
        "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION_review_001.adoc",
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_REVIEW_ARTIFACT,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_REVIEW_ARTIFACT,
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/adapter.py",
        "tests/architecture/test_self_development_governance.py",
        "workflow-coordinator-runtime",
        "governance/work-items/scope-escalation-continuation-implementation.yaml",
        "governance/self-development/scope-escalation-continuation-implementation-profile.yaml",
        "governance/work-items/scope-escalation-continuation-implementation-002.yaml",
        "governance/self-development/scope-escalation-continuation-implementation-002-profile.yaml",
        "governance/work-items/scope-escalation-continuation-implementation-003.yaml",
        "governance/self-development/scope-escalation-continuation-implementation-003-profile.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # correct shares the exact twelve-path implementation scope; rereview is
    # independently scoped by the profile to the single, fresh durable
    # review artifact alone -- no phase's authorized_scope grants both.
    assert correct.authorized_scope == SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE
    assert rereview.authorized_scope == (
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_REVIEW_ARTIFACT,
    )
    assert set(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE).isdisjoint(rereview.authorized_scope)
    assert SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_REVIEW_ARTIFACT not in correct.authorized_scope

    # Only rereview carries the durable-artifact acceptance-recording
    # requirement -- correct has no review authority or artifact in this
    # topology.
    assert durable_artifacts == {
        "rereview": (SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert rereview.durable_artifact.value == "required"
    assert correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))
    assert {"edit", "commit"}.issubset(set(correct.authorized_actions))

    # Topology: correct -> rereview -> {accept: complete, correct}, bounded
    # to six Hops (three correction/rereview cycles), matching the immediate
    # predecessor commission's budget. No implement/review preamble or
    # planning/supervisor phase was added.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert rereview.role == "independent_reviewer"
    assert correct.role == "worker"
    assert rereview.review.finding_authority == ()

    # Role/persona separation preserved identically from all three predecessor
    # commissions: distinct fresh independent-review persona, never the
    # correction persona.
    assert work_item.personas[WorkflowRole.WORKER] == (
        "senior software engineer implementing an authority- and trust-sensitive "
        "Run state, persistence, and operator change"
    )
    assert work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER] == (
        "independent adversarial reviewer for authority widening, predecessor "
        "mutation, and salvage trust boundaries"
    )
    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_correction_convergence_escape_implementation_s1_001_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Slice-1-only implementation commission for the accepted convergence-escape design.

    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-001`` implements
    only Slice 1 ("Structured obligations and wire") of
    ``docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc``, accepted by
    fresh independent rereview with zero remaining findings at H004
    (``4edcea0e341e9c8669378fa299104d66aed2851f``). It must parse, stay within
    policy/profile authority, use the same bounded four-phase bootstrap-safe
    topology as the first scope-escalation-continuation implementation
    commission (``implement -> review -> {accept, correct} -> correct ->
    rereview -> {accept, correct}``) with ``max_autonomous_hops == 6``, and
    hold authority over exactly six production paths and six ordinary test/
    fixture paths plus one dedicated durable implementation-review artifact --
    never the accepted design FTR, its own review artifact, the source BUG,
    ``operator.py``/``vertical.py``/``prompt.py``, or any other governance
    bundle. This commission is deliberately narrower than the accepted
    design's full closed path union (which also permits ``operator.py`` and
    several Slice-2/3/4-only test paths): Slice 1 needs none of those.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-implementation-s1-001"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implement = profile.phases["implement"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT,
    )
    assert len(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_SCOPE) == 12

    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-001"
    assert work_item.initial_phase == "implement"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 6
    assert profile.profile_id == "correction-convergence-escape-implementation-s1-001"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-implementation-s1-001"
    assert tuple(profile.phases) == ("implement", "review", "correct", "rereview")

    # WorkItem.scope is exactly Slice 1's six production paths, six ordinary
    # test/fixture paths, and its single dedicated implementation-review
    # artifact -- never the design FTR, operator.py, vertical.py, prompt.py,
    # or any Slice-2/3/4-only path (e.g. a curriculum fixture).
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("Makefile", work_item.scope)
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope(
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc", work_item.scope
    )
    for path in CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over the accepted design FTR, its own review
    # history, the unrelated source BUG, any other governance bundle, or the
    # files reserved for later slices.
    for withheld in (
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_review_001.adoc",
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/operator.py",
        "tests/unit/test_operator.py",
        "tests/unit/test_slice3_vertical.py",
        "tests/unit/test_slice5b.py",
        "tests/fixtures/convergence_curriculum.yaml",
        "tests/architecture/test_self_development_governance.py",
        "workflow-coordinator-runtime",
        "governance/work-items/correction-convergence-escape-design-001.yaml",
        "governance/self-development/correction-convergence-escape-design-001-profile.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # implement/correct share the exact twelve-path implementation scope;
    # review and rereview are independently scoped by the profile to the
    # single durable review artifact alone -- no phase's authorized_scope
    # grants both.
    assert (
        implement.authorized_scope
        == correct.authorized_scope
        == CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_SCOPE).isdisjoint(review.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT not in implement.authorized_scope
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT not in correct.authorized_scope

    # The Commission-specific acceptance-recording requirement: both review
    # AND rereview require, and are mapped, the same durable artifact, and
    # both carry edit+commit authority on it.
    assert durable_artifacts == {
        "review": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT,),
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT, review.authorized_scope
    )
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "required"
    assert implement.durable_artifact.value == correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(review.authorized_actions))
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))

    # Topology: implement -> review -> {accept: complete, correct} ->
    # correct -> rereview -> {accept: complete, correct}. No
    # diagnosis/reframe/strategy-gate phase was added -- those remain later
    # slices -- and no phase carries implementation authority outside the
    # twelve named production/test paths.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "implement": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert implement.role == correct.role == "worker"
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    # Role/persona separation: distinct fresh independent-review persona,
    # never the implement/correct persona.
    assert work_item.personas[WorkflowRole.WORKER] == (
        "senior workflow-systems/Python architecture engineer extending the Finding "
        "ledger with a structured, wire-versioned correction-obligation and "
        "coverage/assessment extension while preserving exact legacy compatibility"
    )
    assert work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER] == (
        "independent adversarial reviewer for Finding-authority integrity, "
        "untracked-blocker creation, successor-identity correctness, legacy wire/"
        "round-trip compatibility, and domain-neutral scope discipline"
    )
    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_correction_convergence_escape_implementation_s1_002_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Fresh follow-up Slice-1 commission continuing the interrupted S1-001 Run.

    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-002`` is a fresh
    forward-continuation commission from current committed repository state
    (HEAD ``55e4c0b4807b260ceb1cd060f635deefc1e46953``, the maintainer-preserved,
    untrusted WIP candidate from predecessor Run
    ``RUN-a28208a38013403d9b7e0a89ce1e3531`` H001, which reached its configured
    900-second timeout with no trusted commit). It reuses the exact same
    bounded four-phase bootstrap-safe topology, scope, and
    ``max_autonomous_hops == 6`` as predecessor WI-WFC-CORRECTION-CONVERGENCE-
    ESCAPE-IMPLEMENTATION-S1-001, and deliberately reuses that predecessor's
    originally designated (but never written-to) durable review artifact path
    ``docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_implementation_review_001.adoc``
    -- predecessor S1-001 never reached its review phase, so no stale review
    content exists under that name to protect with a separately numbered
    artifact. This is distinguishable from the scope-escalation-continuation
    precedent (WI-...-IMPLEMENTATION-002), which created a fresh
    ``_implementation_review_002.adoc`` specifically because its predecessor's
    ``_implementation_review_001.adoc`` already held real H002/H004/H006
    review history that had to remain frozen and separate.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-implementation-s1-002"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implement = profile.phases["implement"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT,
    )
    assert len(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_SCOPE) == 12

    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-002"
    assert work_item.initial_phase == "implement"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 6
    assert profile.profile_id == "correction-convergence-escape-implementation-s1-002"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-implementation-s1-002"
    assert tuple(profile.phases) == ("implement", "review", "correct", "rereview")

    # Explicit lineage: the authority source names the predecessor WorkItem,
    # the predecessor's failed Run, the original semantic baseline, and the
    # preserved WIP commit, and states the WIP commit confers no trust.
    assert "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-001" in work_item.authority_source
    assert "RUN-a28208a38013403d9b7e0a89ce1e3531" in work_item.authority_source
    assert "272e2b27f4c75f5fb12cf5287d1b1940caeba5dc" in work_item.authority_source
    assert "55e4c0b4807b260ceb1cd060f635deefc1e46953" in work_item.authority_source
    assert "untrusted" in work_item.authority_source

    # WorkItem.scope is exactly the same Slice-1 twelve production/test paths
    # as S1-001, plus the one reused durable implementation-review artifact --
    # never the design FTR, operator.py, vertical.py, prompt.py, or any
    # Slice-2/3/4-only path (e.g. a curriculum fixture).
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("Makefile", work_item.scope)
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope(
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc", work_item.scope
    )
    for path in CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over the accepted design FTR, its own review
    # history, the unrelated source BUG, any other governance bundle, the
    # predecessor's own governing bundle, or the files reserved for later
    # slices.
    for withheld in (
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_review_001.adoc",
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/operator.py",
        "tests/unit/test_operator.py",
        "tests/unit/test_slice3_vertical.py",
        "tests/unit/test_slice5b.py",
        "tests/fixtures/convergence_curriculum.yaml",
        "tests/architecture/test_self_development_governance.py",
        "workflow-coordinator-runtime",
        "governance/work-items/correction-convergence-escape-implementation-s1-001.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s1-001-profile.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s1-001-policy.yaml",
        "governance/work-items/correction-convergence-escape-design-001.yaml",
        "governance/self-development/correction-convergence-escape-design-001-profile.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # implement/correct share the exact twelve-path implementation scope;
    # review and rereview are independently scoped by the profile to the
    # single reused durable review artifact alone -- no phase's
    # authorized_scope grants both.
    assert (
        implement.authorized_scope
        == correct.authorized_scope
        == CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_SCOPE).isdisjoint(review.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT not in implement.authorized_scope
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT not in correct.authorized_scope

    # Load-bearing complete-review-baseline rule: both review and rereview
    # purposes must name the original semantic baseline and explicitly state
    # that the WIP commit confers no trust and is not a sufficient review
    # boundary by itself.
    assert "272e2b27f4c75f5fb12cf5287d1b1940caeba5dc" in review.review.purpose
    assert "55e4c0b4807b260ceb1cd060f635deefc1e46953" in review.review.purpose
    assert "no trust" in review.review.purpose
    assert "272e2b27f4c75f5fb12cf5287d1b1940caeba5dc" in rereview.review.purpose
    assert "55e4c0b4807b260ceb1cd060f635deefc1e46953" in rereview.review.purpose

    # The Commission-specific acceptance-recording requirement: both review
    # AND rereview require, and are mapped, the same reused durable artifact,
    # and both carry edit+commit authority on it.
    assert durable_artifacts == {
        "review": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT,),
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT, review.authorized_scope
    )
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "required"
    assert implement.durable_artifact.value == correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(review.authorized_actions))
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))

    # Topology: implement -> review -> {accept: complete, correct} ->
    # correct -> rereview -> {accept: complete, correct}. No
    # diagnosis/reframe/strategy-gate phase was added -- those remain later
    # slices -- and no phase carries implementation authority outside the
    # twelve named production/test paths.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "implement": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert implement.role == correct.role == "worker"
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_correction_convergence_escape_implementation_s1_003_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Fresh correct-only continuation closing S1-002's sole remaining blocker.

    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-003`` continues
    predecessor ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-002``
    (Run ``RUN-36e0ca5ca7e245c8af90260ec8bd3ba9``), which exhausted its 6/6
    autonomous Hop budget at H006 ``changes_required`` with a clean committed
    final HEAD, ``3aaedaa9a4eb9dd8ebd0f16b44360fc29ee19864``, and exactly one
    remaining open blocking implementation-review identifier, ``S1-IR-002``
    ("New-counterexample evidence must be absent from the correlated
    correction history"); ``S1-IR-001`` is already closed. Unlike S1-002 (a
    four-phase ``implement -> review -> correct -> rereview`` continuation of
    an interrupted, never-reviewed implementation attempt), this commission's
    implementation already exists and has already undergone independent
    review -- it uses the bounded two-phase
    ``correct -> rereview -> {accept: complete, correct: correct}`` topology,
    starting directly at `correct`, bounded to ``max_autonomous_hops: 4`` (at
    most two correction/rereview cycles, not six). Its correction scope
    ceiling is exactly the two files named by S1-IR-002's required
    correction -- ``reducer.py`` and ``test_reducer.py`` -- not the full
    twelve-path Slice-1 implementation scope used by S1-001/S1-002. It reuses
    (and appends to) the SAME durable review artifact as S1-002,
    ``..._implementation_review_001.adoc``, rather than creating a ``_002``
    artifact, because H006 explicitly requires the fresh rereview to dispose
    S1-IR-002 in a separately appended section of the complete durable
    Slice-1 implementation-review history, preserving every prior section.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-implementation-s1-003"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_REVIEW_ARTIFACT,
    )
    assert len(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_SCOPE) == 2

    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-003"
    assert work_item.initial_phase == "correct"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 4
    assert profile.profile_id == "correction-convergence-escape-implementation-s1-003"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-implementation-s1-003"
    assert tuple(profile.phases) == ("correct", "rereview")

    # Explicit frozen lineage: the predecessor WorkItem, its Run, and its
    # final head are captured, S1-IR-002 is named as the complete remaining
    # obligation, S1-IR-001 is recorded as already closed (not reopened), and
    # the original semantic baseline is retained.
    assert "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-002" in work_item.authority_source
    assert "RUN-36e0ca5ca7e245c8af90260ec8bd3ba9" in work_item.authority_source
    assert "3aaedaa9a4eb9dd8ebd0f16b44360fc29ee19864" in work_item.authority_source
    assert "272e2b27f4c75f5fb12cf5287d1b1940caeba5dc" in work_item.authority_source
    assert "S1-IR-002" in work_item.authority_source
    assert "S1-IR-001" in work_item.authority_source
    assert "closed" in work_item.authority_source

    # WorkItem.scope is exactly the two reducer/test paths S1-IR-002's
    # correction is bounded to, plus the reused durable review artifact --
    # never model.py, serialization.py, adapter.py, codex_adapter.py,
    # persistence.py, tests/support.py, vertical.py, prompt.py, operator.py,
    # the design FTR, the source BUG, or any governance path.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    for withheld in (
        "Makefile",
        "src",
        "governance",
        "workflow-coordinator-runtime",
        "src/workflow_coordinator/model.py",
        "src/workflow_coordinator/serialization.py",
        "src/workflow_coordinator/adapter.py",
        "src/workflow_coordinator/codex_adapter.py",
        "src/workflow_coordinator/persistence.py",
        "tests/support.py",
        "tests/unit/test_persistence.py",
        "tests/unit/test_codex_adapter_conformance.py",
        "tests/architecture/test_replays.py",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/operator.py",
        "tests/unit/test_operator.py",
        "tests/unit/test_slice3_vertical.py",
        "tests/unit/test_slice5b.py",
        "tests/fixtures/convergence_curriculum.yaml",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_review_001.adoc",
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "tests/architecture/test_self_development_governance.py",
        "governance/work-items/correction-convergence-escape-implementation-s1-002.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s1-002-profile.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s1-002-policy.yaml",
        "governance/work-items/correction-convergence-escape-implementation-s1-001.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s1-001-profile.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)
    for path in CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_SCOPE:
        assert _within_scope(path, work_item.scope)

    # `correct` is scoped to exactly reducer.py + test_reducer.py; `rereview`
    # is independently scoped by the profile to the single reused durable
    # review artifact alone -- no phase's authorized_scope grants both.
    assert correct.authorized_scope == CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_SCOPE
    assert rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_SCOPE).isdisjoint(rereview.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_REVIEW_ARTIFACT not in correct.authorized_scope

    # Only rereview carries the durable-artifact acceptance-recording
    # requirement; the reused artifact is the SAME path as S1-002's, not a
    # fresh `_002`/`_003`-suffixed review artifact.
    assert durable_artifacts == {
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_REVIEW_ARTIFACT,),
    }
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_REVIEW_ARTIFACT
        == "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_implementation_review_001.adoc"
    )
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert rereview.durable_artifact.value == "required"
    assert correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))
    assert {"edit", "commit"}.issubset(set(correct.authorized_actions))

    # Load-bearing complete-review-baseline rule: rereview's purpose names the
    # original semantic baseline and explicitly excludes narrower deltas
    # (the S1-003 correction delta, the S1-002-final-head-to-candidate delta,
    # and the post-WIP delta) as insufficient review scope.
    assert "272e2b27f4c75f5fb12cf5287d1b1940caeba5dc" in rereview.review.purpose
    assert "S1-IR-002" in rereview.review.purpose
    assert "S1-IR-001" in rereview.review.purpose

    # Topology: correct -> rereview -> {accept: complete, correct}, bounded
    # to four Hops (two correction/rereview cycles). No implement/review
    # preamble, diagnosis/reframe/strategy-gate phase, or Slice-2/3/4/5
    # implementation path is authorized.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert rereview.role == "independent_reviewer"
    assert correct.role == "worker"
    assert rereview.review.finding_authority == ()

    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_correction_convergence_escape_implementation_s2_001_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Slice-2-only implementation commission for the accepted convergence-escape design.

    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S2-001`` implements
    only Slice 2 ("Derived convergence facts") of
    ``docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc``, treating
    Slice 1 ("Structured obligations and wire") as an independently accepted
    prerequisite -- WorkItem
    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-003``, Run
    ``RUN-786b30f27aef437da5c605b71ee7d0fe``, Hop H002, accepted candidate
    ``33d6c0092be988f759bb8dcc8d0e8ee56bcef372``, durable acceptance record at
    commit ``83cccb98bb800479645d87051fb1cd66b3a4ac7f`` with zero blocking
    implementation-review findings remaining. It uses the same bounded
    four-phase bootstrap-safe topology as the first Slice-1 implementation
    commission (``implement -> review -> {accept, correct} -> correct ->
    rereview -> {accept, correct}``), deliberately widened to
    ``max_autonomous_hops: 10`` (several correction/rereview cycles), and
    holds authority over exactly three production paths and four ordinary
    test/fixture paths plus the SAME reused durable implementation-review
    artifact as every Slice-1 commission -- never the accepted design FTR,
    its own review artifact, the source BUG, serialization.py/adapter.py/
    codex_adapter.py/vertical.py/prompt.py/operator.py, any Slice-3/4-only
    fixture, or any other governance bundle. This commission is deliberately
    narrower than the accepted design's full closed path union and than the
    twelve-path Slice-1 scope: Slice 2 is a pure derivation over already-
    persisted Slice-1 wire plus a checkpoint-projection extension, and needs
    neither the Slice-1 wire/adapter paths nor any profile-v3/Slice-4 path.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-implementation-s2-001"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implement = profile.phases["implement"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT,
    )
    assert len(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_SCOPE) == 7

    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S2-001"
    assert work_item.initial_phase == "implement"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 10
    assert profile.profile_id == "correction-convergence-escape-implementation-s2-001"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-implementation-s2-001"
    assert tuple(profile.phases) == ("implement", "review", "correct", "rereview")

    # Explicit frozen Slice-1 prerequisite lineage: the accepted baseline
    # commit, its WorkItem/Run/Hop identity, and the closed zero-blocker
    # outcome are captured; Slice 1 is treated as accepted authority, not
    # reopened.
    assert "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-003" in work_item.authority_source
    assert "RUN-786b30f27aef437da5c605b71ee7d0fe" in work_item.authority_source
    assert "33d6c0092be988f759bb8dcc8d0e8ee56bcef372" in work_item.authority_source
    assert "83cccb98bb800479645d87051fb1cd66b3a4ac7f" in work_item.authority_source
    assert "S1-IR-001" in work_item.authority_source
    assert "S1-IR-002" in work_item.authority_source
    assert "closed" in work_item.authority_source
    assert "prerequisite" in work_item.authority_source

    # WorkItem.scope is exactly Slice 2's three production paths, four
    # ordinary test/fixture paths, and the reused durable implementation-
    # review artifact -- never the design FTR, its own review artifact, the
    # source BUG, serialization.py/adapter.py/codex_adapter.py/vertical.py/
    # prompt.py/operator.py, or any Slice-3/4-only path (e.g. a curriculum
    # fixture or a profile-v3 governance bundle).
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    for withheld in (
        "Makefile",
        "src",
        "governance",
        "workflow-coordinator-runtime",
        "src/workflow_coordinator/serialization.py",
        "src/workflow_coordinator/adapter.py",
        "src/workflow_coordinator/codex_adapter.py",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/operator.py",
        "tests/unit/test_serialization.py",
        "tests/unit/test_codex_adapter_conformance.py",
        "tests/unit/test_operator.py",
        "tests/unit/test_slice3_vertical.py",
        "tests/unit/test_slice5b.py",
        "tests/fixtures/convergence_curriculum.yaml",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_review_001.adoc",
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "tests/architecture/test_self_development_governance.py",
        "governance/work-items/correction-convergence-escape-implementation-s1-003.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s1-003-profile.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s1-003-policy.yaml",
        "governance/work-items/correction-convergence-escape-design-001.yaml",
        "governance/self-development/correction-convergence-escape-design-001-profile.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)
    for path in CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_SCOPE:
        assert _within_scope(path, work_item.scope)

    # implement/correct share the exact seven-path Slice-2 implementation
    # scope; review and rereview are independently scoped by the profile to
    # the single reused durable review artifact alone -- no phase's
    # authorized_scope grants both.
    assert (
        implement.authorized_scope
        == correct.authorized_scope
        == CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_SCOPE).isdisjoint(review.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT not in implement.authorized_scope
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT not in correct.authorized_scope

    # The reused artifact is the SAME path as every Slice-1 commission's, not
    # a fresh Slice-2-suffixed review artifact; both review AND rereview
    # require, and are mapped to, it, with edit+commit authority.
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT
        == "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_implementation_review_001.adoc"
    )
    assert durable_artifacts == {
        "review": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT,),
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT, review.authorized_scope
    )
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "required"
    assert implement.durable_artifact.value == correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(review.authorized_actions))
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))

    # Topology: implement -> review -> {accept: complete, correct} ->
    # correct -> rereview -> {accept: complete, correct}. No
    # diagnosis/reframe/strategy-gate phase was added -- those remain later
    # slices -- and no phase carries implementation authority outside the
    # seven named production/test paths. max_autonomous_hops is 10, not 6,
    # deliberately permitting several correction/rereview cycles.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "implement": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert implement.role == correct.role == "worker"
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

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


def _immutable_finding_echo_fix_bootstrap_profile():
    """Reproduce the commissioned profile's review/rereview shape exactly.

    ``review``/``rereview`` are ``gate``-authority phases with
    ``finding_authority: ()`` (no Finding disposition authority granted at
    all) and routes ``{accept: complete, correct: goto correct}`` -- the
    exact shape recorded in
    ``governance/self-development/immutable-finding-echo-profile.yaml``.
    """

    return profile(
        {
            "review": phase(
                {
                    "accept": route("complete"),
                    "correct": route("goto", "correct"),
                },
                role="independent_reviewer",
                review="gate",
                finding_authority=(),
            ),
            "correct": phase({"rereview": route("goto", "review")}),
        }
    )


def test_bootstrap_changes_required_correct_route_is_legal_with_empty_finding_set():
    """Provider-free proof: this commission's bootstrap choreography does not
    depend on repairing the very defect it exists to repair.

    The Runtime that will execute the commissioned Run is intentionally the
    currently promoted PRE-FIX Runtime, which still carries
    BUG-WFC-IMMUTABLE-FINDING-ECHO (an existing Finding's Coordinator-owned
    ``invariant`` is projected as generative result content and later
    requires exact restatement, so any attempt to create/dispose a
    current-Run Finding to carry an ordinary review-vs-correction cycle
    would risk exercising the broken path). This test exercises the actual,
    unmodified production reducer directly -- no provider, no mocking of
    ``reduce_result`` -- to prove that a gate-authority review Hop may
    legally request ``outcome: changes_required`` / ``route: correct`` with
    an entirely empty ``findings`` tuple, and that the Coordinator accepts
    it and transitions cleanly to ``correct`` without creating, requiring,
    or touching any Finding at all. If this property ever stops holding,
    the commissioned profile's review/rereview loop is unsound and the
    bundle must not be launched.
    """

    workflow = _immutable_finding_echo_fix_bootstrap_profile()
    run, hop_id = charge_hop(
        run_for(workflow, initial_phase="review"), role="independent_reviewer"
    )
    assert run.findings == ()

    reduction = reduce_result(
        run,
        result("correct", outcome="changes_required", findings=()),
        workflow,
        hop_id=hop_id,
    )

    assert reduction.run.phase == "correct"
    assert reduction.run.status is RunStatus.READY
    assert reduction.route_key == "correct"
    assert reduction.autonomous_route is True
    assert reduction.run.findings == ()
    assert reduction.run.stop_reason is None


def test_bootstrap_rereview_changes_required_correct_route_is_also_legal_with_empty_finding_set():
    """The same empty-Finding ``changes_required -> correct`` property holds
    for a ``rereview`` Hop reached after one correction round, not only for
    the initial ``review`` Hop -- the commissioned profile relies on both.
    """

    workflow = profile(
        {
            "correct": phase({"rereview": route("goto", "rereview")}),
            "rereview": phase(
                {
                    "accept": route("complete"),
                    "correct": route("goto", "correct"),
                },
                role="independent_reviewer",
                review="gate",
                finding_authority=(),
            ),
        }
    )
    run, hop_id = charge_hop(
        run_for(workflow, initial_phase="rereview"), role="independent_reviewer"
    )
    assert run.findings == ()

    reduction = reduce_result(
        run,
        result("correct", outcome="changes_required", findings=()),
        workflow,
        hop_id=hop_id,
    )

    assert reduction.run.phase == "correct"
    assert reduction.run.status is RunStatus.READY
    assert reduction.route_key == "correct"
    assert reduction.run.findings == ()

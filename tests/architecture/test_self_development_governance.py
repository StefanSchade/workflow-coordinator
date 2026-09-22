"""Production-preflight proof for current self-development governance."""

from __future__ import annotations

import argparse
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GOVERNING_PATHS = (
    "governance/work-items/developer-ergonomics.yaml",
    "governance/self-development/standalone-development-profile.yaml",
    "governance/self-development/repository-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/developer-ergonomics-task.txt",
    "governance/self-development/prompts/developer.txt",
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
    replacements: dict[str, str] | None = None,
) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--object-format=sha1")
    _git(repository, "config", "user.email", "preflight@example.invalid")
    _git(repository, "config", "user.name", "B2 Preflight")
    for relative in GOVERNING_PATHS:
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        text = (replacements or {}).get(relative, _current_text(relative))
        target.write_text(text, encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "test: exact governing bundle")
    expected_head = _git(repository, "rev-parse", "HEAD")
    assert len(expected_head) == 40
    return repository, expected_head


def _preflight_args(repository: Path, expected_head: str, state_root: Path) -> argparse.Namespace:
    return argparse.Namespace(
        repository=str(repository),
        state_root=str(state_root),
        expected_head=expected_head,
        work_item=GOVERNING_PATHS[0],
        profile=GOVERNING_PATHS[1],
        policy=GOVERNING_PATHS[2],
        components=GOVERNING_PATHS[3],
        task=GOVERNING_PATHS[4],
        durable_artifact=[],
        timeout_seconds=900.0,
        executable="unused-before-dispatch",
    )


def test_current_self_development_bundle_passes_production_preflight(tmp_path: Path):
    repository, expected_head = _materialize_bundle(tmp_path)

    state_root = tmp_path / "external-state" / "new-run"
    args = _preflight_args(repository, expected_head, state_root)

    result = _preflight(args)
    work_item, profile, policy = result[5:8]
    components, durable_artifacts = result[8:10]
    prepared_evidence, environment = result[13:15]
    exact_scope = (*IMPLEMENTATION_PATHS, REVIEW_NAMESPACE)

    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert profile.revision == policy.revision == "2"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 4
    assert tuple(profile.phases) == ("investigate", "review", "correct", "rereview")
    assert durable_artifacts == {"review": (REVIEW_ARTIFACT,)}
    assert tuple(components) == _selected_ids(profile) == ("developer", "reviewer")
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
        "review": (("correct", "goto", "correct"),),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (("accept", "complete", None), ("correct", "goto", "correct")),
    }
    assert policy.governance_paths == ("governance",)
    assert policy.actions == ("edit", "commit", "review")
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(prepared_evidence) == ("pytest",)
    prepared = prepared_evidence["pytest"]
    assert prepared.authored_argv == ("python", "-m", "pytest", "-q")
    assert prepared.admission_resolution.invocation_path == sys.executable
    assert environment.values["PATH"].split(":", 1)[0] == str(Path(sys.executable).parent)
    assert not state_root.exists()


def test_work_item_mapping_cannot_enlarge_review_phase_authority(tmp_path: Path):
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

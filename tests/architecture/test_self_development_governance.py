"""Production-preflight proof for committed self-development governance."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from workflow_coordinator.operator import _preflight, _selected_ids, _within_scope


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
REVIEW_ARTIFACT = "docs/backlog/first_wfc_on_wfc_dogfood_review.adoc"


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _committed_text(path: str) -> str:
    return _git(PROJECT_ROOT, "show", f"HEAD:{path}") + "\n"


def test_committed_first_dogfood_bundle_passes_production_preflight(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--object-format=sha1")
    _git(repository, "config", "user.email", "preflight@example.invalid")
    _git(repository, "config", "user.name", "B2 Preflight")
    for relative in GOVERNING_PATHS:
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_committed_text(relative), encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "test: exact governing bundle")
    expected_head = _git(repository, "rev-parse", "HEAD")
    assert len(expected_head) == 40

    state_root = tmp_path / "external-state" / "new-run"
    args = argparse.Namespace(
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

    result = _preflight(args)
    work_item, profile, policy = result[5:8]
    components, durable_artifacts = result[8:10]
    prepared_evidence, environment = result[13:15]
    exact_scope = (*IMPLEMENTATION_PATHS, REVIEW_ARTIFACT)

    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
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
    assert review.authorized_scope == rereview.authorized_scope == (REVIEW_ARTIFACT,)
    assert review.authorized_actions == ("review", "edit", "commit")
    assert rereview.authorized_actions == ("review",)
    assert investigate.durable_artifact.value == correct.durable_artifact.value == "optional"
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "none"
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

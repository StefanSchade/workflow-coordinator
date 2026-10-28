"""Focused provider-free coverage for the closed ``direct@v1`` renderer."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from workflow_coordinator.commissioning import CommissionError, commission_intent_from_yaml, render_commission, verify_render_manifest


REPO = Path(__file__).resolve().parents[2]


def git(repository: Path, *args: str) -> str:
    return subprocess.run(("git", "-C", str(repository), *args), text=True, capture_output=True, check=True).stdout.strip()


def intent_text(head: str, *, evidence: str = "none", scope: str = "src") -> str:
    return f'''schema_version: 1
commission_id: commission-test
work_item_id: WI-COMMISSION-TEST
authority_source: Maintainer authorizes the stated bounded test only.
program: {{id: direct, revision: v1, source_commit: {head}}}
task:
  title: Commission test
  body: Render the closed program.
  acceptance: The rendered bundle admits without a provider call.
  withheld_authority: Nothing outside the requested scope is authorized.
  escalation_default: Use a typed scope escalation when another path is needed.
scope: [{scope}]
personas:
  worker: Bounded worker persona.
evidence_profile: {evidence}
policy_basis: {{id: repository-standard, revision: v1, source_commit: {head}}}
max_autonomous_hops: 1
durable_artifacts: {{}}
components_ref: {{path: governance/self-development/components.yaml, source_commit: {head}}}
'''


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    clone = tmp_path / "repository"
    subprocess.run(("git", "clone", "--local", "--no-hardlinks", "--quiet", str(REPO), str(clone)), check=True)
    subprocess.run(("git", "-C", str(clone), "config", "user.email", "commission@example.invalid"), check=True)
    subprocess.run(("git", "-C", str(clone), "config", "user.name", "Commission test"), check=True)
    return clone


def materialize(repository: Path, *, evidence: str = "none") -> tuple[str, str]:
    source_head = git(repository, "rev-parse", "HEAD")
    intent_path = "governance/commissions/commission-test/intent.yaml"
    (repository / intent_path).parent.mkdir(parents=True)
    (repository / intent_path).write_text(intent_text(source_head, evidence=evidence), encoding="utf-8")
    rendered = render_commission(commission_intent_from_yaml(intent_text(source_head, evidence=evidence)), repository=repository)
    rendered.write_to(repository)
    git(repository, "add", ".")
    git(repository, "commit", "-m", "test: materialize Commission projection")
    return intent_path, git(repository, "rev-parse", "HEAD")


def test_direct_render_is_deterministic_and_literal_for_both_evidence_profiles(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    none = render_commission(commission_intent_from_yaml(intent_text(head)), repository=repository)
    again = render_commission(commission_intent_from_yaml(intent_text(head)), repository=repository)
    checked = render_commission(commission_intent_from_yaml(intent_text(head, evidence="task-check-v1", scope="tests")), repository=repository)

    assert none.texts == again.texts
    assert "evidence: []" in none.texts[none.paths["profile"]]
    assert yaml.safe_load(none.texts[none.paths["profile"]])["phases"]["verification"]["evidence"] == []
    assert yaml.safe_load(checked.texts[checked.paths["profile"]])["phases"]["verification"]["evidence"] == ["task-check"]
    assert "failed_evidence_route: correction" in checked.texts[checked.paths["profile"]]
    assert "independent_reviewer" not in checked.texts[checked.paths["profile"]]


def test_intent_rejects_forbidden_workflow_semantics_before_render(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    for forbidden in ("routes: {}", "finding_authority: [resolve]", "phase: implement", "model: x"):
        with pytest.raises(CommissionError, match="forbidden"):
            commission_intent_from_yaml(intent_text(head) + forbidden + "\n")
    altered = intent_text(head).replace("evidence_profile: none", "evidence_profile: undefined")
    with pytest.raises(CommissionError, match="undefined"):
        render_commission(commission_intent_from_yaml(altered), repository=repository)
    extra_role = intent_text(head).replace("  worker: Bounded worker persona.", "  worker: Bounded worker persona.\n  independent_reviewer: forbidden")
    with pytest.raises(CommissionError, match="forbidden"):
        commission_intent_from_yaml(extra_role)


def test_equivalence_rerenders_then_delegates_to_real_preflight(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    intent_path, expected_head = materialize(repository, evidence="task-check-v1")
    verify_render_manifest(intent_path, repository=repository, expected_head=expected_head)

    profile = repository / "governance/commissions/commission-test/profile.yaml"
    profile.write_text(profile.read_text(encoding="utf-8") + "# hand edit\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "test: tamper rendered profile")
    with pytest.raises(CommissionError, match="projection equivalence mismatch"):
        verify_render_manifest(intent_path, repository=repository, expected_head=git(repository, "rev-parse", "HEAD"))

    manifest = repository / "governance/commissions/commission-test/render-manifest.json"
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["digests"]["governance/commissions/commission-test/profile.yaml"] = hashlib.sha256(
        profile.read_bytes()
    ).hexdigest()
    manifest.write_text(json.dumps(manifest_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "test: tamper matching manifest digest")
    with pytest.raises(CommissionError, match="projection equivalence mismatch"):
        verify_render_manifest(intent_path, repository=repository, expected_head=git(repository, "rev-parse", "HEAD"))

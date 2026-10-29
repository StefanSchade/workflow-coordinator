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

import workflow_coordinator.commissioning as commissioning
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


def robust_intent_text(
    head: str, *, scope: str = "src, tests, docs",
    broad_artifact: str = "docs/reviews/robust-broad.adoc",
    focused_artifact: str = "docs/reviews/robust-focused.adoc",
) -> str:
    return f'''schema_version: 1
commission_id: robust-commission-test
work_item_id: WI-ROBUST-COMMISSION-TEST
authority_source: Maintainer authorizes the stated bounded robust test only.
program: {{id: robust, revision: v1, source_commit: {head}}}
task:
  title: Robust Commission test
  body: Render the accepted closed robust program.
  acceptance: The rendered bundle admits without a provider call.
  withheld_authority: Nothing outside the requested scope is authorized.
  escalation_default: Use a typed scope escalation when another path is needed.
scope: [{scope}]
personas:
  worker: Bounded worker persona.
  independent_reviewer: Independent adversarial reviewer persona.
evidence_profile: task-check-v1
policy_basis: {{id: repository-standard, revision: v1, source_commit: {head}}}
max_autonomous_hops: 8
durable_artifacts:
  broad_review: [{broad_artifact}]
  focused_review: [{focused_artifact}]
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
    def expected(scope: list[str], evidence: list[str]) -> dict:
        return {
            "implement": {"role": "worker", "components": {"role": ["worker"], "modifiers": [], "repository_policy": []}, "durable_artifact": "none", "authorized_scope": scope, "authorized_actions": ["edit", "commit"], "human_gates": [], "evidence": [], "review": None, "routes": {"verify": {"effect": "goto", "target": "verification"}}},
            "verification": {"role": "worker", "components": {"role": ["worker"], "modifiers": [], "repository_policy": []}, "durable_artifact": "none", "authorized_scope": scope, "authorized_actions": ["review"], "human_gates": [], "evidence": evidence, "review": {"purpose": "deterministic task-check disposition only; no semantic review", "breadth": "verification", "authority": "gate", "context": "fresh", "blocking_policy": "blocking_only", "trigger": "profile_selected", "exit": "declared_route", "finding_authority": [], "failed_evidence_route": "correction"}, "routes": {"accept": {"effect": "complete"}, "correction": {"effect": "goto", "target": "implement"}}},
        }

    assert yaml.safe_load(none.texts[none.paths["profile"]])["phases"] == expected(["src"], [])
    assert yaml.safe_load(checked.texts[checked.paths["profile"]])["phases"] == expected(["tests"], ["task-check"])
    assert "independent_reviewer" not in checked.texts[checked.paths["profile"]]


def test_robust_intent_roles_are_exactly_the_closed_program_roles(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    accepted = commission_intent_from_yaml(robust_intent_text(head))
    assert set(accepted.personas) == {"worker", "independent_reviewer"}

    for altered in (
        robust_intent_text(head).replace("  independent_reviewer: Independent adversarial reviewer persona.\n", ""),
        robust_intent_text(head).replace(
            "  independent_reviewer: Independent adversarial reviewer persona.\n",
            "  independent_reviewer: Independent adversarial reviewer persona.\n  observer: forbidden\n",
        ),
        robust_intent_text(head).replace("  worker: Bounded worker persona.\n", ""),
    ):
        with pytest.raises(CommissionError, match="missing|required|forbidden"):
            commission_intent_from_yaml(altered)


def test_robust_render_is_literal_deterministic_and_admits(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    head = git(repository, "rev-parse", "HEAD")
    intent = commission_intent_from_yaml(robust_intent_text(head))
    rendered = render_commission(intent, repository=repository)
    assert rendered.texts == render_commission(intent, repository=repository).texts

    profile = yaml.safe_load(rendered.texts[rendered.paths["profile"]])["phases"]
    broad_paths = ["docs/reviews/robust-broad.adoc"]
    focused_paths = ["docs/reviews/robust-focused.adoc"]
    assert profile == {
        "implement": {"role": "worker", "components": {"role": ["worker"], "modifiers": [], "repository_policy": []}, "durable_artifact": "none", "authorized_actions": ["edit", "commit"], "human_gates": [], "evidence": [], "review": None, "routes": {"broad_review": {"effect": "goto", "target": "broad_review"}}, "authorized_scope": ["src", "tests", "docs"]},
        "broad_review": {"role": "independent_reviewer", "components": {"role": ["reviewer"], "modifiers": [], "repository_policy": []}, "durable_artifact": "required", "authorized_actions": ["review", "edit", "commit"], "human_gates": [], "evidence": ["task-check"], "review": {"purpose": "independent adversarial full-scope review of the rendered Commission", "breadth": "broad", "authority": "gate", "context": "fresh", "blocking_policy": "blocking_only", "trigger": "profile_selected", "exit": "declared_route", "finding_authority": ["resolve"], "failed_evidence_route": "correction"}, "routes": {"accept": {"effect": "complete"}, "correction": {"effect": "goto", "target": "correct"}}, "authorized_scope": broad_paths},
        "correct": {"role": "worker", "components": {"role": ["worker"], "modifiers": [], "repository_policy": []}, "durable_artifact": "none", "authorized_actions": ["edit", "commit"], "human_gates": [], "evidence": [], "review": None, "routes": {"focused_review": {"effect": "goto", "target": "focused_review"}}, "authorized_scope": ["src", "tests", "docs"]},
        "focused_review": {"role": "independent_reviewer", "components": {"role": ["reviewer"], "modifiers": [], "repository_policy": []}, "durable_artifact": "required", "authorized_actions": ["review", "edit", "commit"], "human_gates": [], "evidence": ["task-check"], "review": {"purpose": "independent focused verification of correction and disposition of the prior broad/focused Findings", "breadth": "focused", "authority": "gate", "context": "fresh", "blocking_policy": "blocking_only", "trigger": "profile_selected", "exit": "declared_route", "finding_authority": ["resolve"], "failed_evidence_route": "correction"}, "routes": {"correction": {"effect": "goto", "target": "correct"}, "renew_broad_review": {"effect": "goto", "target": "broad_review"}}, "authorized_scope": focused_paths},
    }
    item = yaml.safe_load(rendered.texts[rendered.paths["work_item"]])
    assert item["durable_artifacts"] == {"broad_review": broad_paths, "focused_review": focused_paths}
    assert [entry["id"] for entry in rendered.manifest["sources"]["selected_components"]] == ["worker", "reviewer"]

    intent_path = "governance/commissions/robust-commission-test/intent.yaml"
    (repository / intent_path).parent.mkdir(parents=True)
    (repository / intent_path).write_text(robust_intent_text(head), encoding="utf-8")
    rendered.write_to(repository)
    git(repository, "add", ".")
    git(repository, "commit", "-m", "test: materialize robust Commission projection")
    verify_render_manifest(intent_path, repository=repository, expected_head=git(repository, "rev-parse", "HEAD"))


@pytest.mark.parametrize(
    ("scope", "broad_artifact", "focused_artifact"),
    [
        ("src, docs", "docs/reviews/alternate-broad.adoc", "docs/reviews/alternate-focused.adoc"),
        ("tests, docs", "docs/reviews/second-broad.adoc", "docs/reviews/second-focused.adoc"),
    ],
)
def test_robust_slot_bindings_follow_each_distinct_intent(
    repository: Path, scope: str, broad_artifact: str, focused_artifact: str
) -> None:
    head = git(repository, "rev-parse", "HEAD")
    rendered = render_commission(
        commission_intent_from_yaml(
            robust_intent_text(
                head, scope=scope, broad_artifact=broad_artifact, focused_artifact=focused_artifact,
            )
        ),
        repository=repository,
    )
    phases = yaml.safe_load(rendered.texts[rendered.paths["profile"]])["phases"]
    assert phases["implement"]["authorized_scope"] == scope.split(", ")
    assert phases["correct"]["authorized_scope"] == scope.split(", ")
    assert phases["broad_review"]["authorized_scope"] == [broad_artifact]
    assert phases["focused_review"]["authorized_scope"] == [focused_artifact]


def test_robust_renewal_trace_requires_a_new_broad_review(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    rendered = render_commission(commission_intent_from_yaml(robust_intent_text(head)), repository=repository)
    phases = yaml.safe_load(rendered.texts[rendered.paths["profile"]])["phases"]

    def take(phase: str, route: str) -> str | None:
        action = phases[phase]["routes"][route]
        return None if action["effect"] == "complete" else action["target"]

    assert take("implement", "broad_review") == "broad_review"
    assert take("broad_review", "accept") is None
    assert set(phases["focused_review"]["routes"]) == {"correction", "renew_broad_review"}

    phase = "implement"
    for route in (
        "broad_review", "correction", "focused_review", "correction", "focused_review",
        "renew_broad_review", "correction", "focused_review", "renew_broad_review", "accept",
    ):
        phase = take(phase, route)
    assert phase is None, "two correction cycles can complete only through renewed broad_review.accept"


def test_robust_rejects_invalid_artifacts_evidence_and_tampered_completion_before_preflight(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = git(repository, "rev-parse", "HEAD")
    for altered, match in (
        (robust_intent_text(head).replace("evidence_profile: task-check-v1", "evidence_profile: none"), "undefined"),
        (robust_intent_text(head).replace("  focused_review: [docs/reviews/robust-focused.adoc]\n", ""), "durable-artifact"),
        (robust_intent_text(head).replace("docs/reviews/robust-focused.adoc", "outside/focused.adoc"), "contained"),
    ):
        with pytest.raises(CommissionError, match=match):
            render_commission(commission_intent_from_yaml(altered), repository=repository)

    intent_path = "governance/commissions/robust-commission-test/intent.yaml"
    (repository / intent_path).parent.mkdir(parents=True)
    (repository / intent_path).write_text(robust_intent_text(head), encoding="utf-8")
    rendered = render_commission(commission_intent_from_yaml(robust_intent_text(head)), repository=repository)
    rendered.write_to(repository)
    git(repository, "add", ".")
    git(repository, "commit", "-m", "test: materialize robust Commission projection")
    profile_path = repository / rendered.paths["profile"]
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    profile["phases"]["focused_review"]["routes"]["accept"] = {"effect": "complete"}
    profile_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "test: tamper robust focused completion")
    calls = []
    monkeypatch.setattr(commissioning, "_preflight", lambda *_args, **_kwargs: calls.append(True))
    with pytest.raises(CommissionError, match="projection equivalence mismatch"):
        verify_render_manifest(intent_path, repository=repository, expected_head=git(repository, "rev-parse", "HEAD"))
    assert calls == []

    manifest_path = repository / rendered.paths["manifest"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["digests"][rendered.paths["profile"]] = hashlib.sha256(profile_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "test: tamper robust matching manifest digest")
    with pytest.raises(CommissionError, match="projection equivalence mismatch"):
        verify_render_manifest(intent_path, repository=repository, expected_head=git(repository, "rev-parse", "HEAD"))
    assert calls == []


def test_robust_rejects_a_pinned_same_path_catalogue_change(repository: Path) -> None:
    original = git(repository, "rev-parse", "HEAD")
    program_path = repository / "governance/workflow-programs/robust/v1.yaml"
    program_path.write_text(
        program_path.read_text(encoding="utf-8").replace("breadth: focused", "breadth: broad"),
        encoding="utf-8",
    )
    git(repository, "add", str(program_path.relative_to(repository)))
    git(repository, "commit", "-m", "test: mutate same revision robust program")
    changed = git(repository, "rev-parse", "HEAD")
    altered = robust_intent_text(original).replace(f"source_commit: {original}", f"source_commit: {changed}", 1)
    with pytest.raises(CommissionError, match="closed robust@v1 literal record"):
        render_commission(commission_intent_from_yaml(altered), repository=repository)


def test_render_uses_pinned_logical_repository_not_checkout_name(repository: Path, tmp_path: Path) -> None:
    """The same pins must render byte-identically in a differently named clone."""

    head = git(repository, "rev-parse", "HEAD")
    first = render_commission(commission_intent_from_yaml(intent_text(head)), repository=repository)
    renamed = tmp_path / "renamed-checkout"
    subprocess.run(("git", "clone", "--local", "--no-hardlinks", "--quiet", str(repository), str(renamed)), check=True)
    second = render_commission(commission_intent_from_yaml(intent_text(head)), repository=renamed)

    assert first.texts == second.texts
    assert yaml.safe_load(first.texts[first.paths["work_item"]])["repository"] == "workflow-coordinator"


def test_intent_rejects_forbidden_workflow_semantics_before_render(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    for forbidden in (
        "routes: {}", "route_effects: {}", "initial_phase: implement", "phase: implement",
        "actions: [edit]", "review: {}", "finding_authority: [resolve]", "failed_evidence_route: accept",
        "components: {}", "governance_paths: [x]", "model: x", "adapter: x", "evidence_commands: {}",
    ):
        with pytest.raises(CommissionError, match="forbidden"):
            commission_intent_from_yaml(intent_text(head) + forbidden + "\n")
    altered = intent_text(head).replace("evidence_profile: none", "evidence_profile: undefined")
    with pytest.raises(CommissionError, match="undefined"):
        render_commission(commission_intent_from_yaml(altered), repository=repository)
    extra_role = intent_text(head).replace("  worker: Bounded worker persona.", "  worker: Bounded worker persona.\n  independent_reviewer: forbidden")
    with pytest.raises(CommissionError, match="forbidden"):
        commission_intent_from_yaml(extra_role)
    for altered, match in (
        (intent_text(head, scope="governance"), "exceeds"),
        (intent_text(head, scope="outside"), "exceeds"),
        (intent_text(head).replace("durable_artifacts: {}", "durable_artifacts: {implement: [src/x]" + "}"), "forbidden"),
    ):
        with pytest.raises(CommissionError, match=match):
            render_commission(commission_intent_from_yaml(altered), repository=repository)


@pytest.mark.parametrize("malformed", ("src*", "src?", "src[generated]", "src/", "src//nested"))
def test_intent_rejects_non_a1_scope_paths_before_render(repository: Path, malformed: str) -> None:
    """The compiler shares canonical A1 shape validation with admission."""

    head = git(repository, "rev-parse", "HEAD")
    text = intent_text(head).replace("scope: [src]", f'scope: ["{malformed}"]')
    with pytest.raises(CommissionError, match="A1-normalized"):
        commission_intent_from_yaml(text)


@pytest.mark.parametrize("malformed", ("governance/commissions/render*", "governance/commissions/render?", "governance/commissions/render[x]"))
def test_render_rejects_non_a1_target_directory(repository: Path, malformed: str) -> None:
    """A target path cannot bypass the same canonical A1 boundary."""

    head = git(repository, "rev-parse", "HEAD")
    with pytest.raises(CommissionError, match="A1-normalized"):
        render_commission(commission_intent_from_yaml(intent_text(head)), repository=repository, target_dir=malformed)


def test_render_rejects_a_non_a1_default_target_from_commission_id(repository: Path) -> None:
    """The derived target receives the same check when ``--out-dir`` is absent."""

    head = git(repository, "rev-parse", "HEAD")
    malformed = intent_text(head).replace("commission_id: commission-test", "commission_id: commission*")
    with pytest.raises(CommissionError, match="A1-normalized"):
        render_commission(commission_intent_from_yaml(malformed), repository=repository)


def test_full_immutable_policy_record_is_verified_before_render(repository: Path) -> None:
    """A same-id/revision policy with another field changed is not a basis pin."""

    original = git(repository, "rev-parse", "HEAD")
    policy_path = repository / "governance/policy-bases/repository-standard-v1.yaml"
    policy_path.write_text(policy_path.read_text(encoding="utf-8").replace("max_autonomous_hops: 10", "max_autonomous_hops: 9"), encoding="utf-8")
    git(repository, "add", str(policy_path.relative_to(repository)))
    git(repository, "commit", "-m", "test: mutate same revision policy")
    mutated = git(repository, "rev-parse", "HEAD")
    altered = intent_text(original).replace(f"policy_basis: {{id: repository-standard, revision: v1, source_commit: {original}}}", f"policy_basis: {{id: repository-standard, revision: v1, source_commit: {mutated}}}")
    with pytest.raises(CommissionError, match="immutable repository-standard@v1"):
        render_commission(commission_intent_from_yaml(altered), repository=repository)


def test_equivalence_rerenders_then_delegates_to_real_preflight(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    intent_path, expected_head = materialize(repository, evidence="task-check-v1")
    verify_render_manifest(intent_path, repository=repository, expected_head=expected_head)

    calls = []
    monkeypatch.setattr(commissioning, "_preflight", lambda *_args, **_kwargs: calls.append(True))
    profile = repository / "governance/commissions/commission-test/profile.yaml"
    profile.write_text(profile.read_text(encoding="utf-8") + "# hand edit\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "test: tamper rendered profile")
    with pytest.raises(CommissionError, match="projection equivalence mismatch"):
        verify_render_manifest(intent_path, repository=repository, expected_head=git(repository, "rev-parse", "HEAD"))
    assert calls == [], "equivalence failures must occur before preflight"

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
    assert calls == [], "matching a manifest digest cannot bypass rerender comparison"


def test_component_tampering_refuses_before_preflight(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    intent_path, expected_head = materialize(repository)
    worker = repository / "governance/self-development/prompts/worker.txt"
    worker.write_text(worker.read_text(encoding="utf-8") + "\nhand edit\n", encoding="utf-8")
    git(repository, "add", str(worker.relative_to(repository)))
    git(repository, "commit", "-m", "test: tamper component source reuse")
    calls = []
    monkeypatch.setattr(commissioning, "_preflight", lambda *_args, **_kwargs: calls.append(True))
    with pytest.raises(CommissionError, match="reused component differs"):
        verify_render_manifest(intent_path, repository=repository, expected_head=git(repository, "rev-parse", "HEAD"))
    assert calls == []

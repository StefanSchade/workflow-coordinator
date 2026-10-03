import json
from dataclasses import replace

import pytest
import yaml

from workflow_coordinator.model import (
    FindingState,
    DurableArtifact,
    Hop,
    RouteEffect,
    SemanticStop,
    SuccessorProvenance,
    WorkflowRole,
    new_run,
)
from workflow_coordinator.reducer import reduce_result
from workflow_coordinator.serialization import (
    ValidationError,
    repository_policy_from_yaml,
    routing_result_from_data,
    run_execution_configuration_from_yaml,
    run_from_json,
    run_to_json,
    work_item_from_yaml,
    workflow_profile_from_yaml,
)
from workflow_coordinator.persistence import checkpoint_projection
from tests.support import (
    assessment,
    charge_hop,
    coverage,
    existing_delta,
    new_delta,
    phase,
    profile,
    result,
    route,
    run_for,
)

pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")

# Stable pre-change schema-v1 wire fixture: do not infer its absent facts.
PRECHANGE_RUN_JSON = '{"anomalies":[],"baseline_head":"abc","child_run_id":null,"current_head":"abc","findings":[],"hop_limit":1,"hop_used":0,"hops":[],"human_question":"old question","phase":"implement","policy_id":"core-policy","policy_revision":"policy-r1","profile_id":"core","profile_revision":"profile-r1","reconcile_anchor":null,"reconcile_reason":null,"repository":"repo","run_id":"RUN-legacy","schema_version":1,"status":"ready","stop_reason":null,"suspension_head":null,"work_item_id":"WI-1"}\n'


WORK_ITEM_YAML = """
schema_version: 2
work_item_id: WI-1
authority_source: accepted task
profile_ref: workflow.yaml
repository: repo
scope: [scripts]
initial_phase: implement
max_autonomous_hops: 1
personas:
  worker: senior software developer
  independent_reviewer: adversarial code reviewer
profile_labels: [routine]
"""

POLICY_YAML = """
schema_version: 3
policy_id: core-policy
revision: policy-r1
scope: [repo]
governance_paths: [docs/ai_info, docs/governance/agent_operating_contract_v1.txt]
work_item_scope_ceiling: [scripts]
max_autonomous_hops: 1
actions: [edit, commit]
evidence_commands:
  tests:
    argv: [git, --version]
    timeout_seconds: 10
"""

PROFILE_YAML = """
schema_version: 2
profile_id: routine
revision: profile-r1
phases:
  implement:
    role: worker
    components:
      role: [implementer]
      modifiers: []
      repository_policy: [testing]
    durable_artifact: none
    authorized_scope: [scripts]
    authorized_actions: [edit, commit]
    human_gates: [semantic_change]
    evidence: [tests]
    review: null
    routes:
      next: {effect: goto, target: review}
      finish: {effect: complete}
      ask: {effect: await_human, question: Decide semantics.}
      child: {effect: suspend_for_prerequisite}
      halt: {effect: stop}
  review:
    role: independent_reviewer
    components:
      role: [reviewer]
      modifiers: [independent]
      repository_policy: [testing]
    durable_artifact: required
    authorized_scope: [scripts]
    authorized_actions: [review, edit, commit]
    human_gates: []
    evidence: [tests]
    review:
      purpose: falsify implementation
      breadth: implementation
      authority: gate
      context: fresh
      blocking_policy: blocking_only
      trigger: profile_selected
      exit: accepted_or_correction
      finding_authority: [resolve, residual, supersede, reopen]
    routes:
      accept: {effect: complete}
"""


def valid_result_data(**changes):
    data = {
        "schema_version": 1,
        "outcome": "completed",
        "requested_route": "finish",
        "scope_changed": False,
        "requires_human": False,
        "escalation": None,
        "findings": [],
        "claimed_commits": [],
        "commit_intent": None,
        "evidence_refs": ["tests"],
        "summary": "done",
    }
    data.update(changes)
    return data


def valid_result_v2_data(**changes):
    data = valid_result_data(
        schema_version=2,
        coverage_claims=[],
        convergence_assessments=[],
        diagnosis=None,
        strategy_revision=None,
        strategy_assessment=None,
    )
    data.update(changes)
    return data


def v3_run_with_assessment():
    workflow = profile(
        {
            "evaluate": phase(
                {"correct": route("goto", "correct")},
                role="independent_reviewer",
                review="gate",
                finding_authority=("resolve", "residual", "supersede"),
                convergence_role="evaluator",
            ),
            "correct": phase(
                {"evaluate": route("goto", "evaluate")},
                convergence_role="corrector",
            ),
        }
    )
    run, introduction_hop = charge_hop(
        run_for(workflow, initial_phase="evaluate", run_schema_version=3),
        role="independent_reviewer",
    )
    run = reduce_result(
        run,
        result(
            "correct",
            outcome="changes_required",
            schema_version=2,
            findings=(new_delta("open", local_ref="N1"),),
        ),
        workflow,
        hop_id=introduction_hop,
    ).run
    run, correction_hop = charge_hop(run)
    run = reduce_result(
        run,
        result(
            "evaluate",
            schema_version=2,
            coverage_claims=(coverage("F001", "addressed", "worker-evidence"),),
        ),
        workflow,
        hop_id=correction_hop,
    ).run
    run, evaluation_hop = charge_hop(run, role="independent_reviewer")
    run = reduce_result(
        run,
        result(
            "correct",
            outcome="changes_required",
            schema_version=2,
            findings=(existing_delta("F001", "open", evidence="still-unmet"),),
            convergence_assessments=(
                assessment("F001", correction_hop, "carried_unmet", "gate-evidence"),
            ),
        ),
        workflow,
        hop_id=evaluation_hop,
    ).run
    return run


def policy_yaml(**changes):
    data = yaml.safe_load(POLICY_YAML)
    data.update(changes)
    return yaml.safe_dump(data, sort_keys=False)


def test_strict_yaml_loads_work_item_profile_and_policy():
    item = work_item_from_yaml(WORK_ITEM_YAML)
    profile = workflow_profile_from_yaml(PROFILE_YAML)
    policy = repository_policy_from_yaml(POLICY_YAML)

    assert item.max_autonomous_hops == 1
    assert item.personas[WorkflowRole.WORKER] == "senior software developer"
    assert profile.phases["review"].durable_artifact is DurableArtifact.REQUIRED
    assert {route.effect for route in profile.phases["implement"].routes.values()} == set(
        RouteEffect
    )
    assert policy.evidence_commands["tests"].argv == ("git", "--version")
    assert policy.schema_version == 3
    assert policy.governance_paths == (
        "docs/ai_info",
        "docs/governance/agent_operating_contract_v1.txt",
    )
    assert policy.work_item_scope_ceiling == ("scripts",)
    assert policy.max_autonomous_hops == 1


@pytest.mark.parametrize(
    "path",
    [
        "", " docs/ai_info", "docs/ai_info ", "/docs/ai_info", r"docs\ai_info",
        "docs//ai_info", "docs/./ai_info", "docs/../ai_info", ".",
        "docs/ai_info/", "docs/*", "docs/?", "docs/[ai]", "docs/ai]",
    ],
)
@pytest.mark.parametrize("field", ["governance_paths", "work_item_scope_ceiling"])
def test_repository_policy_v3_rejects_malformed_a1_paths(field, path):
    with pytest.raises(ValidationError, match="normalized repository-relative A1 paths"):
        repository_policy_from_yaml(policy_yaml(**{field: [path]}))


@pytest.mark.parametrize("field", ["governance_paths", "work_item_scope_ceiling"])
@pytest.mark.parametrize(
    "paths",
    [
        ["docs/ai_info", "docs/ai_info"],
        ["docs/ai_info", "docs/ai_info/workflows"],
        ["docs/ai_info/workflows", "docs/ai_info"],
    ],
)
def test_repository_policy_v3_rejects_duplicate_or_overlapping_roots(field, paths):
    with pytest.raises(ValidationError, match="unique|must not overlap"):
        repository_policy_from_yaml(policy_yaml(**{field: paths}))


@pytest.mark.parametrize("value", [True, 0, -1, 21])
def test_repository_policy_v3_rejects_invalid_hop_ceiling(value):
    with pytest.raises(ValidationError, match="max_autonomous_hops"):
        repository_policy_from_yaml(policy_yaml(max_autonomous_hops=value))


@pytest.mark.parametrize("value", [1, 5, 6, 20])
def test_repository_policy_v3_accepts_hop_ceiling_up_to_coordinator_hard_maximum(value):
    policy = repository_policy_from_yaml(policy_yaml(max_autonomous_hops=value))
    assert policy.max_autonomous_hops == value


def test_repository_policy_old_versions_and_v3_shape_fail_closed():
    legacy_v1 = {
        "schema_version": 1, "policy_id": "legacy", "revision": "r1",
        "scope": ["repo"], "actions": ["edit"], "evidence_states": ["tests"],
    }
    with pytest.raises(ValidationError, match="schema_version 1.*migration.*3"):
        repository_policy_from_yaml(yaml.safe_dump(legacy_v1, sort_keys=False))
    legacy_v2 = yaml.safe_load(POLICY_YAML)
    legacy_v2["schema_version"] = 2
    with pytest.raises(ValidationError, match="schema_version 2.*migration.*3"):
        repository_policy_from_yaml(yaml.safe_dump(legacy_v2, sort_keys=False))
    missing = yaml.safe_load(POLICY_YAML)
    missing.pop("governance_paths")
    with pytest.raises(ValidationError, match="missing fields: governance_paths"):
        repository_policy_from_yaml(yaml.safe_dump(missing, sort_keys=False))
    with pytest.raises(ValidationError, match="unknown fields: evidence_states"):
        repository_policy_from_yaml(policy_yaml(evidence_states=[]))


@pytest.mark.parametrize(
    "document",
    [
        WORK_ITEM_YAML + "unknown: true\n",
        WORK_ITEM_YAML.replace("max_autonomous_hops: 1", "max_autonomous_hops: 0"),
    ],
)
def test_invalid_work_item_yaml_fails_closed(document):
    with pytest.raises(ValidationError):
        work_item_from_yaml(document)


def test_profile_unknown_field_and_invalid_effect_fail_closed():
    with pytest.raises(ValidationError, match="unknown fields"):
        workflow_profile_from_yaml(
            PROFILE_YAML.replace("role: worker", "role: worker\n    surprise: true", 1)
        )
    with pytest.raises(ValidationError, match="must be one of"):
        workflow_profile_from_yaml(PROFILE_YAML.replace("effect: complete", "effect: teleport", 1))


def test_duplicate_yaml_key_fails_closed():
    with pytest.raises(ValidationError, match="duplicate key"):
        work_item_from_yaml(WORK_ITEM_YAML + "work_item_id: WI-2\n")


def test_profile_unknown_target_and_nonfresh_context_fail_closed():
    with pytest.raises(ValidationError, match="unknown phase"):
        workflow_profile_from_yaml(PROFILE_YAML.replace("target: review", "target: absent"))
    with pytest.raises(ValidationError, match="must be fresh"):
        workflow_profile_from_yaml(PROFILE_YAML.replace("context: fresh", "context: continuing"))


def test_advisory_review_cannot_be_configured_with_disposition_authority():
    document = PROFILE_YAML.replace("authority: gate", "authority: advisory")
    with pytest.raises(ValidationError, match="advisory review"):
        workflow_profile_from_yaml(document)


def test_edit_without_commit_action_is_rejected_as_incoherent():
    document = PROFILE_YAML.replace("authorized_actions: [edit, commit]", "authorized_actions: [edit]")
    with pytest.raises(ValidationError, match="authorizes 'edit' without 'commit'"):
        workflow_profile_from_yaml(document)


def test_commit_without_edit_action_is_rejected_as_incoherent():
    # AAH-R1: ``commit`` authorizes only the Coordinator's mechanical landing; it
    # cannot substitute for the ``edit`` authority that a trusted commit of
    # agent-created worktree mutation also requires.
    document = PROFILE_YAML.replace("authorized_actions: [edit, commit]", "authorized_actions: [commit]")
    with pytest.raises(ValidationError, match="authorizes 'commit' without 'edit'"):
        workflow_profile_from_yaml(document)


def test_required_durable_artifact_without_any_mutation_action_is_rejected():
    # A required-durable phase must both produce and land its artifact, so a phase
    # that declares neither ``edit`` nor ``commit`` is rejected.
    document = PROFILE_YAML.replace(
        "authorized_actions: [review, edit, commit]", "authorized_actions: [review]"
    )
    with pytest.raises(ValidationError, match="requires a durable artifact"):
        workflow_profile_from_yaml(document)


def test_required_durable_artifact_with_only_commit_is_rejected():
    # Producing the required artifact exercises ``edit`` as well as ``commit``.
    document = PROFILE_YAML.replace(
        "authorized_actions: [review, edit, commit]", "authorized_actions: [commit]"
    )
    with pytest.raises(ValidationError, match="authorizes 'commit' without 'edit'"):
        workflow_profile_from_yaml(document)


def test_required_durable_artifact_with_only_edit_is_rejected():
    # Landing the required artifact exercises ``commit`` as well as ``edit``.
    document = PROFILE_YAML.replace(
        "authorized_actions: [review, edit, commit]", "authorized_actions: [edit]"
    )
    with pytest.raises(ValidationError, match="authorizes 'edit' without 'commit'"):
        workflow_profile_from_yaml(document)


def test_required_durable_artifact_with_edit_and_commit_is_accepted():
    document = PROFILE_YAML.replace(
        "authorized_actions: [review, edit, commit]", "authorized_actions: [edit, commit]"
    )
    profile = workflow_profile_from_yaml(document)
    assert profile.phases["review"].durable_artifact is DurableArtifact.REQUIRED
    assert profile.phases["review"].authorized_actions == ("edit", "commit")


def test_non_mutating_observation_phase_needs_no_commit_action():
    document = """
schema_version: 2
profile_id: observe-only
revision: profile-r1
phases:
  observe:
    role: worker
    components:
      role: [observer]
      modifiers: []
      repository_policy: [testing]
    durable_artifact: none
    authorized_scope: [scripts]
    authorized_actions: [review]
    human_gates: []
    evidence: [tests]
    review: null
    routes:
      finish: {effect: complete}
"""
    profile = workflow_profile_from_yaml(document)
    assert profile.phases["observe"].authorized_actions == ("review",)


@pytest.mark.parametrize("role", ["developer", "observer", "prepare", "verify"])
def test_profile_rejects_values_outside_closed_workflow_role_vocabulary(role):
    with pytest.raises(ValidationError, match="must be one of: worker, independent_reviewer"):
        workflow_profile_from_yaml(PROFILE_YAML.replace("role: worker", f"role: {role}", 1))


@pytest.mark.parametrize(
    ("personas_yaml", "message"),
    [
        ("personas: {}", "missing fields|personas"),
        ("personas:\n  architect: expert", "must be one of"),
        ("personas:\n  worker: ''", "non-empty"),
        ("personas:\n  worker: |\n    first\n    second", "single-line"),
        ("personas:\n  worker: '" + "x" * 513 + "'", "512 UTF-8 bytes"),
    ],
)
def test_persona_shape_rejects_unknown_empty_multiline_and_oversized_values(
    personas_yaml, message
):
    document = WORK_ITEM_YAML.replace(
        "personas:\n  worker: senior software developer\n"
        "  independent_reviewer: adversarial code reviewer",
        personas_yaml,
    )
    with pytest.raises(ValidationError, match=message):
        work_item_from_yaml(document)


def test_execution_binding_parser_is_strict_explicit_and_immutable():
    parsed = run_execution_configuration_from_yaml(
        """schema_version: 1
bindings:
  worker: {adapter: codex_cli, model: worker-model, reasoning: medium}
  independent_reviewer: {adapter: codex_cli, model: reviewer-model, reasoning: high}
"""
    )
    assert parsed.bindings[WorkflowRole.WORKER].model == "worker-model"
    assert parsed.bindings[WorkflowRole.INDEPENDENT_REVIEWER].reasoning == "high"
    with pytest.raises(TypeError):
        parsed.bindings[WorkflowRole.WORKER] = parsed.bindings[WorkflowRole.WORKER]


@pytest.mark.parametrize(
    ("binding", "message"),
    [
        ("{adapter: other, model: m, reasoning: high}", "adapter must be codex_cli"),
        ("{adapter: codex_cli, reasoning: high}", "missing fields: model"),
        ("{adapter: codex_cli, model: m}", "missing fields: reasoning"),
        ("{adapter: codex_cli, model: '', reasoning: high}", "non-empty"),
    ],
)
def test_execution_binding_parser_rejects_unsupported_or_incomplete_bindings(
    binding, message
):
    document = f"schema_version: 1\nbindings:\n  worker: {binding}\n"
    with pytest.raises(ValidationError, match=message):
        run_execution_configuration_from_yaml(document)


@pytest.mark.parametrize(
    ("schema_version", "message"),
    [
        ("0", "unsupported version: 0"),
        ("2", "unsupported version: 2"),
        ("99", "unsupported version: 99"),
        ("-1", "unsupported version: -1"),
        ("'1'", "must be an integer"),
        ("1.0", "must be an integer"),
        ("true", "must be an integer"),
    ],
)
def test_execution_binding_parser_admits_only_binding_schema_version_one(
    schema_version, message
):
    document = (
        f"schema_version: {schema_version}\nbindings:\n"
        "  worker: {adapter: codex_cli, model: m, reasoning: medium}\n"
    )
    with pytest.raises(ValidationError, match=message):
        run_execution_configuration_from_yaml(document)


def test_result_validation_rejects_unknown_missing_and_malformed_fields():
    with pytest.raises(ValidationError, match="unknown fields"):
        routing_result_from_data(valid_result_data(extra=True))
    missing = valid_result_data()
    del missing["requested_route"]
    with pytest.raises(ValidationError, match="missing fields"):
        routing_result_from_data(missing)
    with pytest.raises(ValidationError, match="kind is unknown"):
        routing_result_from_data(
            valid_result_data(escalation={"kind": "invented", "question": "What now?"})
        )


def test_existing_finding_delta_has_a_ledger_derived_wire_form():
    parsed = routing_result_from_data(valid_result_data(findings=[{
        "finding_id": "F001", "proposed_state": "resolved", "evidence_refs": ["review"],
    }]))
    delta = parsed.findings[0]
    assert delta.finding_id == "F001"
    assert not hasattr(delta, "invariant") and not hasattr(delta, "blocking")
    for forbidden in ("invariant", "blocking"):
        with pytest.raises(ValidationError, match="unknown fields"):
            routing_result_from_data(valid_result_data(findings=[{
                "finding_id": "F001", "proposed_state": "resolved",
                "evidence_refs": ["review"], forbidden: "replacement",
            }]))


def test_result_validation_preserves_completed_escalation_shape():
    parsed = routing_result_from_data(
        valid_result_data(
            outcome="completed",
            requires_human=True,
            escalation={"kind": "semantic_authority", "question": "Choose authority."},
        )
    )
    assert parsed.outcome.value == "completed"
    assert parsed.requires_human is True
    assert parsed.escalation and parsed.escalation.kind == "semantic_authority"


def test_commit_intent_is_explicit_strict_semantic_data_not_a_commit_claim():
    parsed = routing_result_from_data(
        valid_result_data(commit_intent={"paths": ["scripts/a.py"], "subject": "fix(workflow): WI-1 H001 change"})
    )
    assert parsed.claimed_commits == ()
    assert parsed.commit_intent and parsed.commit_intent.paths == ("scripts/a.py",)
    with pytest.raises(ValidationError, match="unknown fields"):
        routing_result_from_data(valid_result_data(commit_intent={"paths": ["scripts/a.py"], "subject": "x", "argv": ["git"]}))


def test_structured_schema_versions_fail_closed():
    with pytest.raises(ValidationError, match="missing fields"):
        routing_result_from_data(valid_result_data(schema_version=2))
    with pytest.raises(ValidationError, match="unsupported version"):
        routing_result_from_data(valid_result_data(schema_version=3))


def test_result_v1_remains_closed_and_result_v2_requires_its_exact_shape():
    legacy = valid_result_data()
    assert routing_result_from_data(legacy).schema_version == 1
    for field in (
        "coverage_claims",
        "convergence_assessments",
        "diagnosis",
        "strategy_revision",
        "strategy_assessment",
    ):
        with pytest.raises(ValidationError, match="unknown fields"):
            routing_result_from_data({**legacy, field: None if field.endswith("assessment") or field == "diagnosis" or field == "strategy_revision" else []})

    parsed = routing_result_from_data(valid_result_v2_data())
    assert parsed.schema_version == 2
    assert parsed.coverage_claims == parsed.convergence_assessments == ()
    assert parsed.diagnosis is parsed.strategy_revision is parsed.strategy_assessment is None
    for field in ("diagnosis", "strategy_revision", "strategy_assessment"):
        with pytest.raises(ValidationError, match=f"{field} must be null"):
            routing_result_from_data(valid_result_v2_data(**{field: {}}))


def test_result_v2_local_ref_is_new_finding_only_and_v1_rejects_it():
    new_finding = {
        "finding_id": None,
        "invariant": "A bounded obligation.",
        "blocking": True,
        "proposed_state": "open",
        "evidence_refs": ["gate"],
        "local_ref": "N1",
    }
    parsed = routing_result_from_data(valid_result_v2_data(findings=[new_finding]))
    assert parsed.findings[0].local_ref == "N1"
    with pytest.raises(ValidationError, match="unknown fields"):
        routing_result_from_data(valid_result_data(findings=[new_finding]))
    existing = {
        "finding_id": "F001",
        "proposed_state": "open",
        "evidence_refs": [],
        "local_ref": "N1",
    }
    with pytest.raises(ValidationError, match="unknown fields"):
        routing_result_from_data(valid_result_v2_data(findings=[existing]))


def test_profile_v2_shape_stays_closed_and_cannot_select_convergence_role():
    with pytest.raises(ValidationError, match="unknown fields: convergence_role"):
        workflow_profile_from_yaml(
            PROFILE_YAML.replace(
                "    role: worker\n",
                "    role: worker\n    convergence_role: corrector\n",
                1,
            )
        )


def test_run_v3_round_trip_persists_only_concrete_identity_and_new_hop_records():
    run = v3_run_with_assessment()
    encoded = run_to_json(run)
    data = json.loads(encoded)
    assert data["schema_version"] == 3
    assert data["findings"][0]["correction_obligation"] == {
        "owner": "gate",
        "introduced_by_hop": "H001",
    }
    assert data["hops"][1]["coverage_claims"][0]["finding_id"] == "F001"
    assert data["hops"][2]["convergence_assessments"][0]["correction_hop_id"] == "H002"
    assert "local_ref" not in encoded and "local:N1" not in encoded
    assert run_from_json(encoded) == run


def test_run_v3_rejects_untracked_blockers_and_invalid_new_cross_references():
    data = json.loads(run_to_json(v3_run_with_assessment()))
    data["findings"][0]["correction_obligation"] = None
    with pytest.raises(ValidationError, match="lacks correction_obligation"):
        run_from_json(json.dumps(data))

    data = json.loads(run_to_json(v3_run_with_assessment()))
    data["hops"][0]["finding_delta_ids"] = []
    with pytest.raises(ValidationError, match="introduction Hop lacks its delta ID"):
        run_from_json(json.dumps(data))

    data = json.loads(run_to_json(v3_run_with_assessment()))
    data["hops"][1]["coverage_claims"][0]["finding_id"] = "F999"
    with pytest.raises(ValidationError, match="unknown or untracked"):
        run_from_json(json.dumps(data))

    data = json.loads(run_to_json(v3_run_with_assessment()))
    data["hops"][2]["convergence_assessments"][0]["correction_hop_id"] = "H404"
    with pytest.raises(ValidationError, match="absent or non-prior"):
        run_from_json(json.dumps(data))


def test_run_v3_rejects_local_successors_cycles_and_reused_correction_assessments():
    run = v3_run_with_assessment()
    data = json.loads(run_to_json(run))
    data["findings"][0]["state"] = FindingState.SUPERSEDED.value
    data["findings"][0]["successor_ref"] = "local:N1"
    with pytest.raises(ValidationError, match="only a concrete Finding ID"):
        run_from_json(json.dumps(data))

    data = json.loads(run_to_json(run))
    data["findings"][0]["state"] = FindingState.SUPERSEDED.value
    data["findings"][0]["successor_ref"] = "F001"
    with pytest.raises(ValidationError, match="cycle"):
        run_from_json(json.dumps(data))

    data = json.loads(run_to_json(run))
    duplicate = dict(data["hops"][2])
    duplicate["hop_id"] = "H004"
    duplicate["sequence"] = 4
    data["hops"].append(duplicate)
    data["hop_used"] = 4
    with pytest.raises(ValidationError, match="assessed more than once"):
        run_from_json(json.dumps(data))


def test_run_json_round_trip_is_deterministic_and_strict():
    item = work_item_from_yaml(WORK_ITEM_YAML.replace("repository: repo", "repository: repo"))
    profile = workflow_profile_from_yaml(PROFILE_YAML)
    policy = repository_policy_from_yaml(POLICY_YAML)
    run = new_run(item, profile, policy, run_id="RUN-1", baseline_head="abc")

    encoded = run_to_json(run)
    assert run_to_json(run_from_json(encoded)) == encoded
    data = json.loads(encoded)
    data["next_route_keys"] = ["finish"]
    with pytest.raises(ValidationError, match="unknown fields"):
        run_from_json(json.dumps(data))


def test_prechange_v1_fixture_loads_without_fabricated_successor_facts():
    legacy = run_from_json(PRECHANGE_RUN_JSON)
    assert legacy.schema_version == 1
    assert legacy.semantic_stop is None and legacy.successor_provenance is None
    assert run_to_json(legacy) == PRECHANGE_RUN_JSON
    malformed = json.loads(PRECHANGE_RUN_JSON)
    malformed["semantic_stop"] = None
    with pytest.raises(ValidationError, match="unknown fields"):
        run_from_json(json.dumps(malformed))


def test_legacy_v1_read_only_replay_and_inspection_preserve_the_exact_wire_bytes():
    """No read-only projection may back-patch a historical predecessor."""
    original = PRECHANGE_RUN_JSON.encode("utf-8")
    legacy = run_from_json(PRECHANGE_RUN_JSON)
    workflow = profile({"implement": phase({"done": route("complete")})})
    first = checkpoint_projection(legacy, workflow)
    second = checkpoint_projection(run_from_json(PRECHANGE_RUN_JSON), workflow)
    assert first == second
    assert run_to_json(legacy).encode("utf-8") == original


def test_v2_non_null_successor_records_are_closed_and_strict():
    """Malformed provenance cannot be smuggled into a version-2 Run."""
    workflow = profile({"work": phase({"done": route("complete")})})
    run, hop_id = charge_hop(run_for(workflow))
    populated = replace(
        run,
        semantic_stop=SemanticStop("scope", "Grant a new scope.", hop_id),
        successor_provenance=SuccessorProvenance(
            "RUN-predecessor", "WI-predecessor", "a" * 40, "decision", "b" * 64,
            "c" * 64, None,
        ),
    )
    data = json.loads(run_to_json(populated))
    assert run_from_json(json.dumps(data)) == populated
    for field in ("semantic_stop", "successor_provenance"):
        incomplete = json.loads(json.dumps(data))
        incomplete[field] = {}
        with pytest.raises(ValidationError):
            run_from_json(json.dumps(incomplete))
    missing = json.loads(json.dumps(data))
    missing["semantic_stop"].pop("question")
    with pytest.raises(ValidationError):
        run_from_json(json.dumps(missing))
    malformed = json.loads(json.dumps(data))
    malformed["successor_provenance"]["continuation_descriptor_sha256"] = "not-a-digest"
    with pytest.raises(ValidationError):
        run_from_json(json.dumps(malformed))


def test_hop_role_remains_open_trace_data_outside_phase_workflow_roles():
    hop = Hop(
        "H001",
        1,
        "recovery",
        "uncertain_dispatch",
        "fresh",
        "start",
        None,
        "base",
        "base",
        (),
        "uncertain",
        "failed",
        (),
        (),
        None,
        "reconcile",
        "fixture",
        "coordinator",
    )
    assert hop.compact().role == "uncertain_dispatch"


def test_new_run_rejects_unknown_initial_phase():
    item = work_item_from_yaml(
        WORK_ITEM_YAML.replace("initial_phase: implement", "initial_phase: absent")
    )
    with pytest.raises(ValueError, match="unknown initial phase"):
        new_run(
            item,
            workflow_profile_from_yaml(PROFILE_YAML),
            repository_policy_from_yaml(POLICY_YAML),
            run_id="RUN-1",
            baseline_head="abc",
        )

import json
from dataclasses import fields, replace

import pytest
import yaml

from workflow_coordinator.model import (
    ConvergenceRole,
    FindingCitation,
    FindingTransition,
    FindingState,
    DurableArtifact,
    Hop,
    HopSummary,
    Run,
    Finding,
    RouteEffect,
    ResultContractKind,
    SemanticStop,
    SuccessorProvenance,
    UntrustedDiagnosticCapture,
    WorkflowRole,
    SupersedeFinding,
    new_run,
)
from workflow_coordinator.reducer import reduce_result
from workflow_coordinator.serialization import (
    ValidationError,
    execution_evidence_from_json,
    execution_evidence_to_json,
    pre_route_anchor_material,
    repository_policy_from_yaml,
    routing_result_from_data,
    review_verdict_from_data,
    work_result_from_data,
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
    qualification_evidence,
    result,
    route,
    run_for,
    tracked_convergence_run,
    convergence_cycle,
)

pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")


def test_projected_result_families_parse_to_distinct_types_and_reject_cross_family_vocabulary():
    work_data = valid_result_data()
    work_data.pop("outcome")
    work_data["status"] = "completed"
    review_data = valid_result_data()
    review_data.pop("outcome")
    review_data["verdict"] = "changes_required"
    assert type(work_result_from_data(work_data)).__name__ == "WorkResult"
    assert type(review_verdict_from_data(review_data)).__name__ == "ReviewVerdict"
    with pytest.raises(ValidationError, match="unknown fields|missing fields"):
        work_result_from_data({**work_data, "verdict": "changes_required"})

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


def profile_v3_data():
    phase_base = {
        "role": "worker",
        "components": {"role": ["worker"], "modifiers": [], "repository_policy": ["testing"]},
        "durable_artifact": "none",
        "authorized_scope": ["scripts"],
        "authorized_actions": ["edit", "commit"],
        "human_gates": [],
        "evidence": ["tests"],
        "review": None,
    }
    gate = {
        "purpose": "falsify",
        "breadth": "implementation",
        "authority": "gate",
        "context": "fresh",
        "blocking_policy": "blocking_only",
        "trigger": "profile_selected",
        "exit": "declared_route",
        "finding_authority": ["resolve", "residual", "supersede"],
    }
    phases = {
        "correct": {**phase_base, "convergence_role": "corrector", "routes": {"evaluate": {"effect": "goto", "target": "evaluate"}}},
        "evaluate": {
            **phase_base,
            "role": "independent_reviewer",
            "review": gate,
            "convergence_role": "evaluator",
            "normal_correction_route": "correct",
            "escape_route": "diagnose",
            "routes": {
                "accept": {"effect": "complete"},
                "correct": {"effect": "goto", "target": "correct"},
                "diagnose": {"effect": "goto", "target": "diagnose"},
            },
        },
        "diagnose": {**phase_base, "authorized_actions": [], "convergence_role": "diagnosis", "routes": {"reframe": {"effect": "goto", "target": "reframe"}}},
        "reframe": {**phase_base, "convergence_role": "reframe", "routes": {"assess": {"effect": "goto", "target": "strategy"}}},
        "strategy": {
            **phase_base,
            "role": "independent_reviewer",
            "review": {**gate, "finding_authority": []},
            "convergence_role": "strategy_gate",
            "routes": {"correct": {"effect": "goto", "target": "correct"}, "reframe": {"effect": "goto", "target": "reframe"}},
        },
    }
    return {
        "schema_version": 3,
        "profile_id": "convergence",
        "revision": "r1",
        "convergence": {
            "protocol": "repeated_counterexample_v1",
            "qualified_cycle_limit": 2,
        },
        "phases": phases,
    }


def profile_v3_yaml(**changes):
    data = profile_v3_data()
    data.update(changes)
    return yaml.safe_dump(data, sort_keys=False)


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


def valid_work_result_v3_data(**changes):
    data = valid_result_data(schema_version=3)
    data.pop("outcome")
    data["status"] = "completed"
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


def test_profile_v3_loads_closed_convergence_grammar_and_routes():
    parsed = workflow_profile_from_yaml(profile_v3_yaml())

    assert parsed.schema_version == 3
    assert parsed.convergence is not None
    assert parsed.convergence.qualified_cycle_limit == 2
    evaluator = parsed.phases["evaluate"]
    assert evaluator.convergence_role is ConvergenceRole.EVALUATOR
    assert evaluator.normal_correction_route == "correct"
    assert evaluator.escape_route == "diagnose"
    assert parsed.phases["diagnose"].convergence_role is ConvergenceRole.DIAGNOSIS
    assert parsed.phases["reframe"].convergence_role is ConvergenceRole.REFRAME
    assert parsed.phases["strategy"].convergence_role is ConvergenceRole.STRATEGY_GATE


def test_failed_evidence_route_accepts_goto_or_await_human_only():
    data = yaml.safe_load(PROFILE_YAML)
    review = data["phases"]["review"]
    review["review"]["failed_evidence_route"] = "retry"
    review["routes"]["retry"] = {"effect": "goto", "target": "implement"}
    assert workflow_profile_from_yaml(yaml.safe_dump(data, sort_keys=False)).phases["review"].review

    review["review"]["failed_evidence_route"] = "ask"
    review["routes"]["ask"] = {"effect": "await_human", "question": "Choose an evidence disposition."}
    parsed = workflow_profile_from_yaml(yaml.safe_dump(data, sort_keys=False))
    assert parsed.phases["review"].review.failed_evidence_route == "ask"
    assert parsed.phases["review"].routes["ask"].effect is RouteEffect.AWAIT_HUMAN

    review["review"]["failed_evidence_route"] = "halt"
    review["routes"]["halt"] = {"effect": "stop"}
    with pytest.raises(ValidationError, match="goto or await_human"):
        workflow_profile_from_yaml(yaml.safe_dump(data, sort_keys=False))


def test_v3_evaluator_await_human_failed_evidence_route_is_not_a_goto_key():
    data = profile_v3_data()
    evaluator = data["phases"]["evaluate"]
    evaluator["review"]["failed_evidence_route"] = "ask"
    evaluator["routes"]["ask"] = {"effect": "await_human", "question": "Choose next action."}
    parsed = workflow_profile_from_yaml(yaml.safe_dump(data, sort_keys=False))
    assert parsed.phases["evaluate"].review.failed_evidence_route == "ask"


@pytest.mark.parametrize("limit", [True, 0, 1, 21])
def test_profile_v3_rejects_out_of_bounds_convergence_limit(limit):
    data = profile_v3_data()
    data["convergence"]["qualified_cycle_limit"] = limit
    with pytest.raises(ValidationError, match="qualified_cycle_limit"):
        workflow_profile_from_yaml(yaml.safe_dump(data, sort_keys=False))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data["convergence"].update(protocol="custom"), "protocol"),
        (lambda data: data["convergence"].update(expression="count > 1"), "unknown fields"),
        (lambda data: data["phases"]["correct"].update(convergence_role="optimizer"), "convergence_role"),
        (lambda data: data["phases"]["evaluate"].update(extra="value"), "unknown fields"),
        (lambda data: data["phases"]["evaluate"].update(normal_correction_route="absent"), "unknown convergence route"),
        (lambda data: data["phases"]["evaluate"].update(escape_route="absent"), "unknown convergence route"),
        (lambda data: data["phases"]["evaluate"].update(normal_correction_route="accept"), "evaluator goto routes"),
        (lambda data: data["phases"]["evaluate"].update(escape_route="accept"), "evaluator goto routes"),
        (lambda data: data["phases"]["diagnose"]["routes"]["reframe"].update(target="correct"), "diagnosis goto"),
        (lambda data: data["phases"]["reframe"]["routes"]["assess"].update(target="correct"), "reframe goto"),
        (lambda data: data["phases"]["strategy"]["routes"]["correct"].update(target="diagnose"), "strategy_gate goto"),
        (lambda data: data["phases"]["strategy"]["routes"].pop("reframe"), "strategy_gate goto"),
        (lambda data: data["phases"]["evaluate"]["review"].update(finding_authority=["resolve"]), "lacks Finding authority"),
        (lambda data: data["phases"]["evaluate"]["review"].update(failed_evidence_route="diagnose"), "reserved escape_route"),
    ],
)
def test_profile_v3_fail_closed_cases(mutation, message):
    data = profile_v3_data()
    mutation(data)
    with pytest.raises(ValidationError, match=message):
        workflow_profile_from_yaml(yaml.safe_dump(data, sort_keys=False))


def test_profile_versions_reject_mixed_v2_v3_shapes():
    data = profile_v3_data()
    data["schema_version"] = 2
    with pytest.raises(ValidationError, match="unknown fields: convergence"):
        workflow_profile_from_yaml(yaml.safe_dump(data, sort_keys=False))


def test_new_run_selects_run_wire_version_from_pinned_profile():
    item = work_item_from_yaml(WORK_ITEM_YAML.replace("initial_phase: implement", "initial_phase: correct"))
    policy = repository_policy_from_yaml(POLICY_YAML)
    convergence_profile = workflow_profile_from_yaml(profile_v3_yaml())
    v3 = new_run(item, convergence_profile, policy, run_id="RUN-v3", baseline_head="abc")
    assert v3.schema_version == 3

    simple_item = work_item_from_yaml(WORK_ITEM_YAML)
    simple = new_run(
        simple_item,
        workflow_profile_from_yaml(PROFILE_YAML),
        policy,
        run_id="RUN-v2",
        baseline_head="abc",
    )
    assert simple.schema_version == 2

    data = yaml.safe_load(PROFILE_YAML)
    data["schema_version"] = 3
    with pytest.raises(ValidationError, match="missing fields: convergence"):
        workflow_profile_from_yaml(yaml.safe_dump(data, sort_keys=False))


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


@pytest.mark.parametrize("adapter", ("codex_cli", "claude_cli"))
def test_execution_binding_parser_admits_exactly_the_two_supported_adapters(adapter):
    parsed = run_execution_configuration_from_yaml(
        f"""schema_version: 1
bindings:
  worker: {{adapter: {adapter}, model: selected-model, reasoning: high}}
"""
    )
    assert parsed.bindings[WorkflowRole.WORKER].adapter == adapter


@pytest.mark.parametrize(
    ("binding", "message"),
    [
        ("{adapter: other, model: m, reasoning: high}", "adapter must be codex_cli or claude_cli"),
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
        with pytest.raises(ValidationError):
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


def test_work_result_v3_parses_structural_citations_and_transitions_only():
    citation = {"finding_id": "F001", "evidence_refs": ["inspection"]}
    transition = {
        "finding_id": "F002",
        "target": {"state": "superseded", "successor_ref": "F003"},
        "evidence_refs": ["review"],
    }
    parsed = work_result_from_data(
        valid_work_result_v3_data(findings=[citation, transition])
    )
    assert isinstance(parsed.findings[0], FindingCitation)
    assert isinstance(parsed.findings[1], FindingTransition)
    assert isinstance(parsed.findings[1].target, SupersedeFinding)
    assert parsed.findings[1].target.successor_ref == "F003"
    for forbidden in ("proposed_state", "successor_ref", "new_material_evidence"):
        with pytest.raises(ValidationError, match="unknown fields"):
            work_result_from_data(
                valid_work_result_v3_data(findings=[{**citation, forbidden: "x"}])
            )
    with pytest.raises(ValidationError, match="unknown fields"):
        work_result_from_data(
            valid_work_result_v3_data(
                findings=[{
                    "finding_id": "F002",
                    "target": {"state": "resolved", "successor_ref": "F003"},
                    "evidence_refs": ["review"],
                }]
            )
        )


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


def test_schema4_execution_evidence_is_closed_and_anchor_is_route_independent():
    workflow, run = tracked_convergence_run()
    run, _, evaluator_hop = convergence_cycle(
        run, workflow, evidence_prefix="schema4"
    )
    run, _, evidence_json = qualification_evidence(
        run, {evaluator_hop: ("pass",)}
    )
    document = execution_evidence_from_json(evidence_json)
    assert execution_evidence_to_json(document) == evidence_json
    attempt = document.attempts[0]
    material = pre_route_anchor_material(
        document.run,
        hop_id=attempt.hop_id,
        hop_sequence=attempt.hop_sequence,
        invocation_id=attempt.invocation_id,
        phase=attempt.phase,
        workflow_role=attempt.workflow_role,
        outcome=attempt.outcome,
        base_head=attempt.base_head,
        end_head=attempt.end_head,
        actual_commits=attempt.actual_commits,
        candidate_result_sha256=attempt.candidate_result_sha256,
        required_evidence=attempt.required_evidence,
    )
    forbidden = {
        "applied_route",
        "route_reason",
        "stop_reason",
        "run_status",
        "checkpoint",
        "trusted_observations",
    }
    assert forbidden.isdisjoint(material)

    unknown = json.loads(evidence_json)
    unknown["attempts"][0]["qualified"] = True
    with pytest.raises(ValidationError, match="unknown fields"):
        execution_evidence_from_json(json.dumps(unknown))

    duplicate = json.loads(evidence_json)
    duplicate["attempts"][0]["trusted_observations"].append(
        duplicate["attempts"][0]["trusted_observations"][0]
    )
    with pytest.raises(ValidationError, match="duplicate trusted observations"):
        execution_evidence_from_json(json.dumps(duplicate))

    for field, value in (
        ("command_name", "other"),
        ("argv_digest", "0" * 64),
        ("timeout_seconds", 11),
    ):
        mismatched = json.loads(evidence_json)
        mismatched["attempts"][0]["trusted_observations"][0][field] = value
        if field == "command_name":
            mismatched["attempts"][0]["trusted_observations"][0]["provider"] = value
        with pytest.raises(ValidationError, match="identity-mismatched"):
            execution_evidence_from_json(json.dumps(mismatched))

    duplicate_row = json.loads(evidence_json)
    duplicate_row["attempts"].append(duplicate_row["attempts"][0])
    with pytest.raises(ValidationError, match="duplicate Hop rows"):
        execution_evidence_from_json(json.dumps(duplicate_row))

    for field in ("workflow_role", "outcome"):
        malformed = json.loads(evidence_json)
        malformed["attempts"][0][field] = "unsupported-value"
        with pytest.raises(ValidationError, match="must be one of"):
            execution_evidence_from_json(json.dumps(malformed))


def test_schema4_observation_round_trips_untrusted_diagnostic_capture():
    workflow, run = tracked_convergence_run()
    run, _, evaluator_hop = convergence_cycle(
        run, workflow, evidence_prefix="diagnostic"
    )
    run, _, evidence_json = qualification_evidence(
        run, {evaluator_hop: ("fail",)}
    )
    document = execution_evidence_from_json(evidence_json)
    diagnostic = UntrustedDiagnosticCapture(
        stdout_tail_base64="c3Rkb3V0LXRhaWw=",
        stdout_tail_truncated=False,
        stderr_tail_base64="",
        stderr_tail_truncated=True,
    )
    attempt = document.attempts[0]
    observation = replace(attempt.trusted_observations[0], untrusted_diagnostic=diagnostic)
    attempt = replace(attempt, trusted_observations=(observation,))
    document = replace(document, attempts=(attempt,) + document.attempts[1:])

    encoded = execution_evidence_to_json(document)
    assert execution_evidence_from_json(encoded) == document

    original = json.loads(evidence_json)["attempts"][0]["trusted_observations"][0]
    round_tripped = json.loads(encoded)["attempts"][0]["trusted_observations"][0]
    assert round_tripped["untrusted_diagnostic"] == {
        "stdout_tail_base64": "c3Rkb3V0LXRhaWw=",
        "stdout_tail_truncated": False,
        "stderr_tail_base64": "",
        "stderr_tail_truncated": True,
    }
    # Every existing trusted field round-trips identically to the undiagnosed original.
    trusted_keys = set(original) - {"untrusted_diagnostic"}
    assert {key: round_tripped[key] for key in trusted_keys} == {
        key: original[key] for key in trusted_keys
    }


def test_schema4_observation_tolerates_missing_untrusted_diagnostic_key():
    workflow, run = tracked_convergence_run()
    run, _, evaluator_hop = convergence_cycle(
        run, workflow, evidence_prefix="diagnostic-absent"
    )
    run, _, evidence_json = qualification_evidence(
        run, {evaluator_hop: ("pass",)}
    )
    historical = json.loads(evidence_json)
    del historical["attempts"][0]["trusted_observations"][0]["untrusted_diagnostic"]
    document = execution_evidence_from_json(json.dumps(historical))
    assert document.attempts[0].trusted_observations[0].untrusted_diagnostic is None


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda diag: diag.update(stdout_tail_base64="not-base64!!"), "stdout_tail_base64"),
        (lambda diag: diag.update(stdout_tail_truncated="yes"), "stdout_tail_truncated"),
        (lambda diag: diag.pop("stderr_tail_base64"), "missing fields: stderr_tail_base64"),
        (lambda diag: diag.update(extra=True), "unknown fields: extra"),
    ],
)
def test_schema4_observation_rejects_malformed_untrusted_diagnostic(mutate, message):
    workflow, run = tracked_convergence_run()
    run, _, evaluator_hop = convergence_cycle(
        run, workflow, evidence_prefix="diagnostic-malformed"
    )
    run, _, evidence_json = qualification_evidence(
        run, {evaluator_hop: ("fail",)}
    )
    malformed = json.loads(evidence_json)
    malformed["attempts"][0]["trusted_observations"][0]["untrusted_diagnostic"] = {
        "stdout_tail_base64": "",
        "stdout_tail_truncated": False,
        "stderr_tail_base64": "",
        "stderr_tail_truncated": False,
    }
    mutate(malformed["attempts"][0]["trusted_observations"][0]["untrusted_diagnostic"])
    with pytest.raises(ValidationError, match=message):
        execution_evidence_from_json(json.dumps(malformed))


def test_v3_hop_anchor_is_the_only_new_semantic_wire_field():
    run = v3_run_with_assessment()
    run = replace(
        run,
        hops=tuple(
            replace(hop, pre_route_anchor_sha256="a" * 64)
            if hop.hop_id == "H003"
            else hop
            for hop in run.hops
        ),
    )
    data = json.loads(run_to_json(run))
    assert data["hops"][2]["pre_route_anchor_sha256"] == "a" * 64
    semantic_fields = {
        field.name
        for record in (Run, HopSummary, Finding)
        for field in fields(record)
    }
    forbidden = {
        "qualified",
        "required_evidence_passed",
        "observation",
        "observations",
        "invocation_id",
        "qualified_cycle_count",
        "goalpost_reached",
        "route_substitution",
        "convergence_escape",
        "strategy_generation",
    }
    assert semantic_fields & forbidden == set()


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


def test_run_v3_persists_source_phase_and_rejects_unknown_escape_triggers():
    run = v3_run_with_assessment()
    data = json.loads(run_to_json(run))
    assert data["hops"][-1]["phase"] == "evaluate"

    missing_phase = json.loads(run_to_json(run))
    del missing_phase["hops"][-1]["phase"]
    with pytest.raises(ValidationError, match="missing fields: phase"):
        run_from_json(json.dumps(missing_phase))

    unknown_trigger = json.loads(run_to_json(run))
    unknown_trigger["hops"][-1].update(
        requested_route="correct",
        applied_route="diagnose",
        route_reason="convergence_escape:F999",
    )
    with pytest.raises(ValidationError, match="malformed convergence route truth"):
        run_from_json(json.dumps(unknown_trigger))


def test_run_v3_rejects_forged_accepted_assessment_without_a_causal_chain():
    """Persisted acceptance cannot advance a derived generation by itself."""

    data = json.loads(run_to_json(v3_run_with_assessment()))
    escape = data["hops"][-1]
    escape.update(
        requested_route="correct",
        applied_route="diagnose",
        route_reason="convergence_escape:F001",
        strategy_assessment={
            "escape_hop_id": escape["hop_id"],
            "diagnosis_hop_id": "H404",
            "revision_hop_id": "H405",
            "finding_ids": ["F001"],
            "result": "accepted",
            "evidence_refs": [],
        },
    )
    with pytest.raises(ValidationError, match="does not correlate to the current chain"):
        run_from_json(json.dumps(data))


def test_run_v3_rejects_diagnosis_on_its_own_escape_hop():
    """A persisted diagnosis must follow, rather than share, its escape Hop."""

    data = json.loads(run_to_json(v3_run_with_assessment()))
    escape = data["hops"][-1]
    escape.update(
        route_reason="convergence_escape:F001",
        requested_route="correct",
        applied_route="diagnose",
        diagnosis={
            "escape_hop_id": escape["hop_id"],
            "finding_ids": ["F001"],
            "classification": "regression_chain",
            "analysis": "Forged same-Hop diagnosis.",
            "evidence_refs": [],
        },
    )
    revision = dict(escape)
    revision.update(
        hop_id="H004",
        sequence=4,
        route_reason=None,
        requested_route="correct",
        applied_route="strategy",
        diagnosis=None,
        strategy_revision={
            "escape_hop_id": "H003",
            "diagnosis_hop_id": "H003",
            "finding_ids": ["F001"],
            "kind": "address_regression_chain",
            "plan": "Forged follow-up revision.",
            "evidence_refs": [],
        },
    )
    assessment = dict(revision)
    assessment.update(
        hop_id="H005",
        sequence=5,
        applied_route="correct",
        strategy_revision=None,
        strategy_assessment={
            "escape_hop_id": "H003",
            "diagnosis_hop_id": "H003",
            "revision_hop_id": "H004",
            "finding_ids": ["F001"],
            "result": "accepted",
            "evidence_refs": [],
        },
    )
    data["hops"].extend((revision, assessment))
    data["hop_used"] = 5
    data["hop_limit"] = 5

    with pytest.raises(ValidationError, match="diagnosis does not correlate"):
        run_from_json(json.dumps(data))


def test_run_v3_rejects_strategy_chain_for_older_of_two_unconsumed_escapes():
    """Replay cannot use an older escape while a newer one remains active."""

    data = json.loads(run_to_json(v3_run_with_assessment()))
    data["hops"][-1].update(
        route_reason="convergence_escape:F001",
        requested_route="correct",
        applied_route="diagnose",
    )
    escape = dict(data["hops"][-1])
    escape.update(
        hop_id="H004",
        sequence=4,
        coverage_claims=[],
        convergence_assessments=[],
        finding_delta_ids=[],
        route_reason="convergence_escape:F001",
        requested_route="correct",
        applied_route="diagnose",
        diagnosis=None,
        strategy_revision=None,
        strategy_assessment=None,
    )
    diagnosis = dict(escape)
    diagnosis.update(
        hop_id="H005",
        sequence=5,
        route_reason=None,
        requested_route="correct",
        applied_route="reframe",
        diagnosis={
            "escape_hop_id": "H003",
            "finding_ids": ["F001"],
            "classification": "regression_chain",
            "analysis": "Older chain diagnosis.",
            "evidence_refs": [],
        },
    )
    revision = dict(diagnosis)
    revision.update(
        hop_id="H006",
        sequence=6,
        applied_route="strategy",
        diagnosis=None,
        strategy_revision={
            "escape_hop_id": "H003",
            "diagnosis_hop_id": "H005",
            "finding_ids": ["F001"],
            "kind": "address_regression_chain",
            "plan": "Older chain revision.",
            "evidence_refs": [],
        },
    )
    assessment = dict(revision)
    assessment.update(
        hop_id="H007",
        sequence=7,
        applied_route="correct",
        strategy_revision=None,
        strategy_assessment={
            "escape_hop_id": "H003",
            "diagnosis_hop_id": "H005",
            "revision_hop_id": "H006",
            "finding_ids": ["F001"],
            "result": "accepted",
            "evidence_refs": [],
        },
    )
    data["hops"].extend((escape, diagnosis, revision, assessment))
    data["hop_used"] = 7
    data["hop_limit"] = 7

    with pytest.raises(ValidationError, match="more than one unconsumed escape"):
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


def test_hop_result_contract_identity_is_optional_and_round_trips_without_backfill():
    workflow = profile({"implement": phase({"done": route("complete")})})
    run, _ = charge_hop(run_for(workflow))
    historical = json.loads(run_to_json(run))
    assert "result_contract_kind" not in historical["hops"][0]
    persisted = replace(
        run,
        hops=(replace(
            run.hops[0],
            result_contract_kind=ResultContractKind.GENERIC_ROUTING_RESULT,
            result_contract_version=1,
        ),),
    )
    encoded = json.loads(run_to_json(persisted))
    assert encoded["hops"][0]["result_contract_kind"] == "generic-routing-result"
    assert encoded["hops"][0]["result_contract_version"] == 1
    assert run_from_json(json.dumps(encoded)) == persisted


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

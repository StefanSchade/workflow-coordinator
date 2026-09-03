import json

import pytest

from scripts.workflow_coordinator.model import DurableArtifact, RouteEffect, new_run
from scripts.workflow_coordinator.serialization import (
    ValidationError,
    repository_policy_from_yaml,
    routing_result_from_data,
    run_from_json,
    run_to_json,
    work_item_from_yaml,
    workflow_profile_from_yaml,
)

pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")


WORK_ITEM_YAML = """
schema_version: 1
work_item_id: WI-1
authority_source: accepted task
profile_ref: workflow.yaml
repository: repo
scope: [scripts]
initial_phase: implement
max_autonomous_hops: 1
profile_labels: [routine]
"""

POLICY_YAML = """
schema_version: 1
policy_id: core-policy
revision: policy-r1
scope: [repo]
actions: [edit]
evidence_states: [tests]
"""

PROFILE_YAML = """
schema_version: 1
profile_id: routine
revision: profile-r1
phases:
  implement:
    role: implementer
    components:
      role: [implementer]
      modifiers: []
      repository_policy: [testing]
    durable_artifact: none
    authorized_scope: [scripts]
    authorized_actions: [edit]
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
    role: reviewer
    components:
      role: [reviewer]
      modifiers: [independent]
      repository_policy: [testing]
    durable_artifact: required
    authorized_scope: [scripts]
    authorized_actions: [review]
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


def test_strict_yaml_loads_work_item_profile_and_policy():
    item = work_item_from_yaml(WORK_ITEM_YAML)
    profile = workflow_profile_from_yaml(PROFILE_YAML)
    policy = repository_policy_from_yaml(POLICY_YAML)

    assert item.max_autonomous_hops == 1
    assert profile.phases["review"].durable_artifact is DurableArtifact.REQUIRED
    assert {route.effect for route in profile.phases["implement"].routes.values()} == set(
        RouteEffect
    )
    assert policy.evidence_states == ("tests",)


@pytest.mark.parametrize(
    "document",
    [
        WORK_ITEM_YAML + "unknown: true\n",
        WORK_ITEM_YAML.replace("1\nprofile_labels", "0\nprofile_labels"),
    ],
)
def test_invalid_work_item_yaml_fails_closed(document):
    with pytest.raises(ValidationError):
        work_item_from_yaml(document)


def test_profile_unknown_field_and_invalid_effect_fail_closed():
    with pytest.raises(ValidationError, match="unknown fields"):
        workflow_profile_from_yaml(
            PROFILE_YAML.replace("role: implementer", "role: implementer\n    surprise: true", 1)
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
    with pytest.raises(ValidationError, match="unsupported version"):
        routing_result_from_data(valid_result_data(schema_version=2))


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

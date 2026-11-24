"""C(i)/(ii), WI-WFC-RECOVERY-ROUTING-001: every gate-review phase's
assembled prompt lists each permitted route with a generic, profile-derived
explanation, states the forward-route rule, and (when a durable artifact is
required) the re-review artifact obligation. Uses two profiles with
different, arbitrary route/phase names to prove nothing is hard-coded.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from workflow_coordinator.adapter import Invocation
from workflow_coordinator.model import DurableArtifact
from workflow_coordinator.prompt import PromptComponent, assemble_prompt, load_agent_operating_contract
from tests.support import phase, profile, route, run_for


def components(*ids: str) -> dict[str, PromptComponent]:
    return {name: PromptComponent(name, f"{name}.txt", "HEAD", name) for name in ids}


def invocation() -> Invocation:
    return Invocation("RUN-1", "H001", "INV-1")


def _assemble(workflow, phase_key: str) -> str:
    package = assemble_prompt(
        run_for(workflow, initial_phase=phase_key),
        workflow.phases[phase_key],
        components("worker", "independent_reviewer", "testing"),
        invocation=invocation(),
        agent_operating_contract=load_agent_operating_contract(),
        persona="fixture persona", task_payload="task",
        profile=workflow,
    )
    return package.text


def test_requires_profile_for_any_gate_review_phase():
    workflow = profile({
        "scrutinize": phase({"ok": route("complete")}, role="independent_reviewer", review="gate"),
    })
    with pytest.raises(ValueError, match="requires the profile"):
        assemble_prompt(
            run_for(workflow),
            workflow.phases["scrutinize"],
            components("independent_reviewer", "testing"),
            invocation=invocation(),
            agent_operating_contract=load_agent_operating_contract(),
            persona="fixture persona", task_payload="task",
        )


def test_non_review_phase_never_gets_the_route_semantics_block():
    workflow = profile({"build": phase({"done": route("complete")})})
    text = _assemble(workflow, "build")
    assert "[review_route_semantics]" not in text


def test_hands_back_to_worker_route_is_named_and_the_forward_rule_is_stated():
    workflow = profile({
        "scrutinize": replace(
            phase(
                {
                    "ratify": route("complete"),
                    "bounce": route("goto", "redo"),
                },
                role="independent_reviewer", review="gate", finding_authority=("resolve",),
            ),
            durable_artifact=DurableArtifact.REQUIRED,
        ),
        "redo": phase({"scrutinize": route("goto", "scrutinize")}, role="worker"),
    })
    review_phase = workflow.phases["scrutinize"]
    patched = replace(review_phase, review=replace(review_phase.review, failed_evidence_route="bounce"))
    workflow = replace(workflow, phases={**workflow.phases, "scrutinize": patched})

    text = _assemble(workflow, "scrutinize")
    assert "[review_route_semantics]" in text
    block = text.split("[review_route_semantics]", 1)[1].split("[task]", 1)[0]

    # Derived from the profile, not hard-coded: both arbitrary route names
    # and the arbitrary target-phase name appear verbatim.
    assert "ratify: effect=complete" in block
    assert "hands_back_to_worker=false" in block
    assert "bounce: effect=goto; target_phase=redo; target_phase_role=worker; hands_back_to_worker=true" in block
    assert "is_failed_evidence_route=true" in block
    assert "never a route with hands_back_to_worker=true" in block
    assert "update the required durable artifact with this Hop's verdict" in block
    assert "name that artifact's path in the CommitIntent" in block


def test_no_required_durable_artifact_omits_the_artifact_obligation_sentence():
    workflow = profile({
        "scrutinize": phase(
            {"ratify": route("complete")}, role="independent_reviewer", review="gate",
            finding_authority=("resolve",),
        ),
    })
    text = _assemble(workflow, "scrutinize")
    assert "[review_route_semantics]" in text
    assert "update the required durable artifact" not in text


def test_evaluator_escape_route_is_excluded_like_the_header(tmp_path=None):
    workflow = profile({
        "evaluate": replace(
            phase(
                {"accept": route("complete"), "escalate": route("goto", "diagnose", supervisor=True)},
                role="independent_reviewer", review="gate", finding_authority=("resolve",),
                convergence_role="evaluator", escape_route="escalate",
            ),
        ),
        "diagnose": phase({"evaluate": route("goto", "evaluate")}, role="worker"),
    }, qualified_cycle_limit=2)
    text = _assemble(workflow, "evaluate")
    block = text.split("[review_route_semantics]", 1)[1].split("[task]", 1)[0]
    assert "escalate:" not in block
    assert "accept: effect=complete" in block

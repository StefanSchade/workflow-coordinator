"""Focused Slice-3 contracts: prompt bounds, result correlation, and one fake Hop."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.workflow_coordinator.adapter import (
    AgentExecutionOutcome,
    AgentExecutionRequest,
    FakeAdapter,
    Invocation,
    ResultValidationError,
    SubprocessAdapter,
    validated_result,
)
from scripts.workflow_coordinator.model import DurableArtifact
from scripts.workflow_coordinator.prompt import (
    COMPONENT_LIMIT_BYTES, CONTEXT_LIMIT_BYTES, DIFF_FILE_LIMIT, DIFF_LIMIT_BYTES,
    ContextItem, PromptComponent, assemble_prompt,
)
from scripts.workflow_coordinator.vertical import run_one_hop
from tests.utils.workflow_coordinator import phase, profile, route, run_for

pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")


def envelope(run_id="RUN-1", hop_id="H001", invocation_id="INV-1", **result_changes):
    result = {"schema_version": 1, "outcome": "completed", "requested_route": "done",
              "scope_changed": False, "requires_human": False, "escalation": None,
              "findings": [], "claimed_commits": [], "commit_intent": None,
              "evidence_refs": ["git_version"], "summary": "done"}
    result.update(result_changes)
    return json.dumps({"schema_version": 1, "run_id": run_id, "hop_id": hop_id,
                       "invocation_id": invocation_id, "result": result})


def components(*items: tuple[str, int]) -> dict[str, PromptComponent]:
    return {name: PromptComponent(name, f"docs/{name}.txt", "PIN", "x" * size) for name, size in items}


def test_prompt_selects_only_phase_components_in_explicit_order_and_keeps_critical_header():
    workflow = profile({"work": phase({"done": route("complete")}, role="worker")})
    current = workflow.phases["work"]
    selected = replace(current, role_components=("role",), modifier_components=("modifier",), repository_policy_components=("policy",))
    workflow = replace(workflow, phases={"work": selected})
    package = assemble_prompt(run_for(workflow), selected, components(("role", 2), ("modifier", 2), ("policy", 2), ("absent", 2)), task_payload="task")
    assert [item["id"] for item in package.selected_components] == ["role", "modifier", "policy"]
    assert "absent" not in package.text
    assert package.text.index("xx") < package.text.index("[workflow_policy]") < package.text.index("[task]")
    for critical in ("work_item_id=WI-1", "run_id=RUN-1", "scope=repo", "permitted_routes=done", "output_contract=structured_result_v1"):
        assert critical in package.text


@pytest.mark.parametrize("size, omitted", [(COMPONENT_LIMIT_BYTES - 1, False), (COMPONENT_LIMIT_BYTES + 1, True)])
def test_component_bound_is_visible_not_truncated(size, omitted):
    workflow = profile({"work": phase({"done": route("complete")})})
    package = assemble_prompt(run_for(workflow), workflow.phases["work"], components(("worker", size), ("testing", 1)), task_payload="task")
    assert bool(package.omissions) is omitted
    assert ("x" * 30 in package.text) is not omitted
    if omitted:
        assert package.omissions[0]["retrieval"] == "git show PIN:docs/worker.txt"


@pytest.mark.parametrize("size, omitted", [(CONTEXT_LIMIT_BYTES - 2, False), (CONTEXT_LIMIT_BYTES + 1, True)])
def test_total_context_bound_has_a_reference_for_every_excluded_item(size, omitted):
    workflow = profile({"work": phase({"done": route("complete")})})
    package = assemble_prompt(run_for(workflow), workflow.phases["work"], components(("worker", 1), ("testing", 1)), task_payload="task", context=(ContextItem("artifact://large", "z" * size),))
    assert any(item["reference"] == "artifact://large" for item in package.omissions) is omitted
    assert ("z" * 30 in package.text) is not omitted


@pytest.mark.parametrize("size, files, omitted", [(DIFF_LIMIT_BYTES - 1, 1, False), (DIFF_LIMIT_BYTES + 1, 1, True), (1, DIFF_FILE_LIMIT, False), (1, DIFF_FILE_LIMIT + 1, True)])
def test_diff_bounds_are_visible_and_exact(size, files, omitted):
    workflow = profile({"work": phase({"done": route("complete")})})
    package = assemble_prompt(run_for(workflow), workflow.phases["work"], components(("worker", 1), ("testing", 1)), task_payload="task", diff="d" * size, diff_base="base", diff_head="head", changed_paths=tuple(f"src/{number}" for number in range(files)))
    assert (package.inline_diff is None) is omitted
    if omitted:
        row = next(item for item in package.omissions if item["kind"] == "diff")
        assert row["retrieval"] == "git diff base..head" and row["changed_paths"]


def test_result_validation_accepts_only_complete_correlated_structured_envelope():
    expected = type("Expected", (), {"run_id": "RUN-1", "hop_id": "H001", "invocation_id": "INV-1"})()
    assert validated_result(envelope(), expected).requested_route == "done"
    for raw in ("{", envelope(hop_id="H002"), json.dumps({"schema_version": 1})):
        with pytest.raises(ResultValidationError):
            validated_result(raw, expected)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    subprocess.run(("git", "init", str(tmp_path / "repo")), check=True, capture_output=True)
    repo = tmp_path / "repo"
    for key, value in (("user.email", "fixture@example.invalid"), ("user.name", "Fixture")):
        subprocess.run(("git", "-C", str(repo), "config", key, value), check=True)
    (repo / "src").mkdir()
    (repo / "src" / "base").write_text("base\n")
    subprocess.run(("git", "-C", str(repo), "add", "."), check=True)
    subprocess.run(("git", "-C", str(repo), "commit", "-m", "test: baseline"), check=True, capture_output=True)
    return repo


def test_fake_adapter_n1_path_accepts_valid_result_without_live_dependency(repository):
    workflow = profile({"work": replace(phase({"done": route("complete")}), evidence=("git_version",))})
    run = replace(run_for(workflow, budget=1), current_head=subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip())
    completed = run_one_hop(run, workflow, repository, FakeAdapter(envelope()), components(("worker", 1), ("testing", 1)), task_payload="tiny", invocation_id="INV-1")
    assert completed.reduction.run.status.value == "completed"
    assert completed.reduction.run.hops[0].actual_commits == ()
    assert "WI-1/RUN-1 completed H001" in completed.terminal


class CommitAdapter:
    def __init__(self, repository: Path, subject: str, output: str, path: str = "src/change"):
        self.repository = repository
        self.subject = subject
        self.output = output
        self.path = path

    def execute(self, request: AgentExecutionRequest) -> AgentExecutionOutcome:
        target = request.repository / self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("change\n")
        subprocess.run(("git", "-C", str(request.repository), "add", self.path), check=True)
        subprocess.run(
            ("git", "-C", str(request.repository), "commit", "-m", self.subject),
            check=True,
            capture_output=True,
        )
        return AgentExecutionOutcome(self.output, 0, False, "", "")


@pytest.mark.parametrize(
    "subject, accepted",
    [
        ("feat(workflow): WI-1 H001 tiny change", True),
        ("feat(workflow): WI-100 H0012 collision", False),
        ("not conventional: WI-1 H001", False),
        ("feat(workflow): tiny change", False),
    ],
)
def test_git_subject_postcondition_charges_invalid_commits_and_rejects_collisions(
    repository, subject, accepted
):
    workflow = profile({"work": replace(phase({"done": route("complete")}), authorized_scope=("src",), evidence=("git_version",))})
    head = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
    run = replace(run_for(workflow, budget=1), current_head=head)
    adapter = CommitAdapter(repository, subject, envelope())
    if accepted:
        assert run_one_hop(
            run,
            workflow,
            repository,
            adapter,
            components(("worker", 1), ("testing", 1)),
            task_payload="tiny",
            invocation_id="INV-1",
        ).reduction.run.status.value == "completed"
    else:
        stopped = run_one_hop(
            run,
            workflow,
            repository,
            adapter,
            components(("worker", 1), ("testing", 1)),
            task_payload="tiny",
            invocation_id="INV-1",
        )
        assert stopped.reduction.run.status.value == "awaiting_human"
        assert stopped.reduction.run.hop_used == len(stopped.reduction.run.hops) == 1
        assert stopped.reduction.run.hops[0].actual_commits
        assert "WFC-GIT-08" in (stopped.reduction.run.stop_reason or "")
        assert stopped.checkpoint["hops"] and stopped.checkpoint["state"]["stop_reason"]


def test_durable_artifact_required_charges_post_acceptance_failure(repository):
    required = profile({"work": replace(phase({"done": route("complete")}), authorized_scope=("src",), durable_artifact=DurableArtifact.REQUIRED, evidence=("git_version",))})
    head = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
    run = replace(run_for(required, budget=1), current_head=head)
    stopped = run_one_hop(
        run,
        required,
        repository,
        CommitAdapter(repository, "feat(workflow): WI-1 H001 ordinary change", envelope()),
        components(("worker", 1), ("testing", 1)),
        task_payload="tiny",
        invocation_id="INV-1",
    )
    assert stopped.reduction.run.status.value == "awaiting_human"
    assert stopped.reduction.run.hop_used == len(stopped.reduction.run.hops) == 1
    assert stopped.reduction.run.hops[0].actual_commits
    assert "required durable artifact" in (stopped.reduction.run.stop_reason or "")
    assert stopped.checkpoint["hops"] and stopped.checkpoint["state"]["stop_reason"]
    # The separate fake N=1 test proves `none` has no document obligation.


def test_out_of_scope_post_acceptance_anomaly_still_charges_and_checkpoints(repository):
    workflow = profile({"work": replace(phase({"done": route("complete")}), authorized_scope=("src",), evidence=("git_version",))})
    head = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
    run = replace(run_for(workflow, budget=1), current_head=head)
    stopped = run_one_hop(
        run,
        workflow,
        repository,
        CommitAdapter(repository, "feat(workflow): WI-1 H001 leaked path", envelope(), "docs/leak"),
        components(("worker", 1), ("testing", 1)),
        task_payload="tiny",
        invocation_id="INV-1",
    )
    assert stopped.reduction.run.status.value == "awaiting_human"
    assert stopped.reduction.run.hop_used == len(stopped.reduction.run.hops) == 1
    assert "out-of-scope changed path: docs/leak" in (stopped.reduction.run.stop_reason or "")
    assert stopped.checkpoint["hops"] and stopped.checkpoint["state"]["stop_reason"]


def test_durable_artifact_required_accepts_an_in_scope_committed_artifact(repository):
    required = profile({"work": replace(phase({"done": route("complete")}), authorized_scope=("src",), durable_artifact=DurableArtifact.REQUIRED, evidence=("git_version",))})
    head = subprocess.run(("git", "-C", str(repository), "rev-parse", "HEAD"), text=True, capture_output=True, check=True).stdout.strip()
    run = replace(run_for(required, budget=1), current_head=head)
    adapter = CommitAdapter(repository, "docs(workflow): WI-1 H001 artifact", envelope())
    completed = run_one_hop(run, required, repository, adapter, components(("worker", 1), ("testing", 1)), task_payload="tiny", invocation_id="INV-1", durable_artifacts=("src/change",))
    assert completed.reduction.run.status.value == "completed"


def test_subprocess_adapter_is_local_fixture_seam_not_provider(repository):
    adapter = SubprocessAdapter(("/bin/sh", "-c", "printf '{}'"))
    outcome = adapter.execute(AgentExecutionRequest(Invocation("RUN-1", "H001", "INV-1"), "ignored", repository, "test", 0))
    assert outcome.candidate_result == "{}"

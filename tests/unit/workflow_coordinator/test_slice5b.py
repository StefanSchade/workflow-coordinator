"""Focused deterministic acceptance for the bounded Slice-5B contract."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from scripts.workflow_coordinator.adapter import AgentExecutionOutcome
from scripts.workflow_coordinator.model import (
    DurableArtifact,
    FindingAuthority,
    MechanicalFacts,
    Observation,
    RepositoryPolicy,
    RunStatus,
    WorkItem,
    new_run,
)
from scripts.workflow_coordinator.operator import (
    OperatorPreflightError,
    _preflight,
    run_serial_hops,
)
from scripts.workflow_coordinator.prompt import PromptComponent
from scripts.workflow_coordinator.reducer import (
    ReductionError,
    apply_finding_deltas,
    reduce_result,
)
from scripts.workflow_coordinator.serialization import (
    ValidationError,
    workflow_profile_from_yaml,
)
from scripts.workflow_coordinator.slice4 import register_checkout
from scripts.workflow_coordinator.vertical import run_one_hop
from tests.utils.workflow_coordinator import (
    charge_hop,
    delta,
    phase,
    profile,
    result,
    route,
    run_for,
)

pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *args),
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def commit_all(repository: Path, subject: str = "test: fixture") -> str:
    git(repository, "add", ".")
    git(repository, "commit", "-m", subject)
    return git(repository, "rev-parse", "HEAD")


def test_same_state_citations_are_noop_or_evidence_only_and_truthfully_attributed():
    workflow = profile({"work": phase({"again": route("goto", "work")})})
    run, first_hop = charge_hop(run_for(workflow, budget=4))
    run = apply_finding_deltas(
        run, (delta(None, "open", evidence="initial"),),
        hop_id=first_hop, authority=FindingAuthority.AGENT,
    )
    run = replace(run, status=RunStatus.READY)

    charged, hop_id = charge_hop(run)
    cited = reduce_result(
        charged,
        result("again", findings=(delta("F001", "open", evidence="initial"),)),
        workflow,
        hop_id=hop_id,
    ).run
    assert cited.hops[-1].finding_delta_ids == ()
    assert cited.findings[0].changed_by_hop == first_hop

    charged, hop_id = charge_hop(cited)
    enriched = reduce_result(
        charged,
        result("again", findings=(delta("F001", "open", evidence="correction"),)),
        workflow,
        hop_id=hop_id,
    ).run
    assert enriched.hops[-1].finding_delta_ids == ("F001",)
    assert enriched.findings[0].evidence_refs == ("initial", "correction")
    assert enriched.findings[0].changed_by_hop == hop_id

    charged, hop_id = charge_hop(enriched)
    rejected = reduce_result(
        charged,
        result(
            "again",
            findings=(delta("F001", "open", blocking=False, evidence="smuggled"),),
        ),
        workflow,
        hop_id=hop_id,
    ).run
    assert "same-state citation cannot change blocking" in (rejected.stop_reason or "")
    assert rejected.hops[-1].finding_delta_ids == ()
    assert rejected.findings == enriched.findings


def test_superseded_same_state_citation_must_restate_successor_exactly():
    workflow = profile({"gate": phase({"done": route("complete")}, review="gate",
                                      finding_authority=("supersede",))})
    run, hop_id = charge_hop(run_for(workflow))
    superseded = apply_finding_deltas(
        run,
        (delta(None, "superseded", successor_ref="F-next"),),
        hop_id=hop_id,
        authority=FindingAuthority.GATE,
        permitted_gate_operations=("supersede",),
    )
    with pytest.raises(ReductionError, match="restate successor_ref exactly"):
        apply_finding_deltas(
            superseded,
            (delta("F001", "superseded", successor_ref=None),),
            hop_id=hop_id,
            authority=FindingAuthority.GATE,
            permitted_gate_operations=("supersede",),
        )


def test_outcome_and_preemption_semantics_are_distinct_and_atomic():
    workflow = profile({"work": phase({"again": route("goto", "work"),
                                       "done": route("complete")})})

    failed_run, failed_hop = charge_hop(run_for(workflow, budget=3))
    failed = reduce_result(
        failed_run,
        result(
            "invented", outcome="failed", scope_changed=True,
            findings=(delta(None, "open"),),
        ),
        workflow,
        hop_id=failed_hop,
    ).run
    assert failed.stop_reason == "result_failed"
    assert failed.findings == () and failed.hops[-1].finding_delta_ids == ()

    human_run, human_hop = charge_hop(run_for(workflow, budget=3))
    human = reduce_result(
        human_run,
        result("done", scope_changed=True, requires_human=True),
        workflow,
        hop_id=human_hop,
    ).run
    assert human.stop_reason == "mechanical_preemption:human_authority"

    blocked_run, blocked_hop = charge_hop(run_for(workflow, budget=3))
    blocked = reduce_result(
        blocked_run,
        result("again", outcome="blocked", findings=(delta(None, "open"),)),
        workflow,
        hop_id=blocked_hop,
    ).run
    assert blocked.stop_reason == "result_blocked"
    assert blocked.phase == "work"
    assert blocked.hops[-1].finding_delta_ids == ("F001",)

    invalid_run, invalid_hop = charge_hop(run_for(workflow, budget=3))
    invalid = reduce_result(
        invalid_run,
        result(
            "again", outcome="blocked",
            findings=(delta(None, "open"), delta("F404", "open")),
        ),
        workflow,
        hop_id=invalid_hop,
    ).run
    assert invalid.findings == () and invalid.hops[-1].finding_delta_ids == ()


def test_changes_required_and_run_wide_open_blocker_completion_guards():
    review = profile({"review": phase({"accept": route("complete")}, review="gate")})
    charged, hop_id = charge_hop(run_for(review))
    refused = reduce_result(
        charged, result("accept", outcome="changes_required"), review, hop_id=hop_id
    ).run
    assert refused.stop_reason == "changes_required_cannot_complete"

    ordinary = profile({"work": phase({"done": route("complete")})})
    charged, hop_id = charge_hop(run_for(ordinary))
    blocked = reduce_result(
        charged,
        result("done", findings=(delta(None, "open"),)),
        ordinary,
        hop_id=hop_id,
    ).run
    assert blocked.stop_reason == "completion_blocked_by_open_blocking_findings"

    gate = profile({"gate": phase({"done": route("complete")}, review="gate",
                                   finding_authority=("residual",))})
    charged, hop_id = charge_hop(run_for(gate))
    completed = reduce_result(
        charged,
        result(
            "done",
            findings=(delta(None, "residual", blocking=False),),
        ),
        gate,
        hop_id=hop_id,
    ).run
    assert completed.status is RunStatus.COMPLETED


def test_clean_evidence_failure_uses_only_declared_gate_route():
    workflow = profile({
        "review": replace(
            phase({"correct": route("goto", "correct"), "accept": route("complete")},
                  review="gate"),
            review=replace(
                phase({}, review="gate").review,
                failed_evidence_route="correct",
            ),
        ),
        "correct": phase({"done": route("complete")}),
    })
    charged, hop_id = charge_hop(run_for(workflow))
    rerouted = reduce_result(
        charged, result("accept"), workflow, hop_id=hop_id,
        facts=MechanicalFacts(required_evidence_failed=True),
    )
    assert rerouted.route_key == "correct" and rerouted.run.phase == "correct"

    no_route = profile({"work": phase({"again": route("goto", "work")})})
    charged, hop_id = charge_hop(run_for(no_route))
    stopped = reduce_result(
        charged, result("again"), no_route, hop_id=hop_id,
        facts=MechanicalFacts(required_evidence_failed=True),
    ).run
    assert stopped.stop_reason == "required_evidence_failed_without_route"

    charged, hop_id = charge_hop(run_for(workflow))
    errored = reduce_result(
        charged, result("accept"), workflow, hop_id=hop_id,
        facts=MechanicalFacts(
            required_evidence_error="runner error",
            required_evidence_failed=True,
        ),
    ).run
    assert errored.stop_reason == "mechanical_preemption:runner error"

    charged, hop_id = charge_hop(run_for(workflow))
    failed = reduce_result(
        charged, result("accept", outcome="failed"), workflow, hop_id=hop_id,
        facts=MechanicalFacts(required_evidence_failed=True),
    ).run
    assert failed.stop_reason == "result_failed"

    charged, hop_id = charge_hop(run_for(workflow))
    error_over_failed = reduce_result(
        charged, result("accept", outcome="failed"), workflow, hop_id=hop_id,
        facts=MechanicalFacts(required_evidence_error="hard error"),
    ).run
    assert error_over_failed.stop_reason == "mechanical_preemption:hard error"


class FakeEvidenceRunner:
    def __init__(self, supported=("fake_check",), status="pass"):
        self.supported = tuple(supported)
        self.status = status
        self.support_calls: list[str] = []
        self.observe_calls: list[str] = []

    def supports(self, provider: str) -> bool:
        self.support_calls.append(provider)
        return provider in self.supported

    def observe(self, provider: str, repository: Path) -> Observation:
        self.observe_calls.append(provider)
        return Observation(
            provider, self.status, ("fake-evidence", "--fixed"),
            f"fixed {self.status}", None, f"digest-{self.status}",
        )


def fixture_data() -> dict[str, object]:
    return yaml.safe_load(
        Path("tests/data/workflow_coordinator/slice5b_non_dmc.yaml").read_text()
    )


def fixture_repository(tmp_path: Path, *, data: dict[str, object] | None = None) -> Path:
    value = data or fixture_data()
    repository = tmp_path / "repo"
    repository.mkdir(parents=True)
    subprocess.run(("git", "init", str(repository)), check=True, capture_output=True)
    git(repository, "config", "user.email", "fixture@example.invalid")
    git(repository, "config", "user.name", "Fixture")
    for filename, key in (
        ("work.yml", "work_item"),
        ("profile.yml", "profile"),
        ("policy.yml", "repository_policy"),
        ("components.yml", "components"),
    ):
        (repository / filename).write_text(yaml.safe_dump(value[key], sort_keys=False))
    (repository / "task.txt").write_text(str(value["task"]) + "\n")
    (repository / "generic_agent.txt").write_text("Return the declared structured result.\n")
    (repository / "payload.txt").write_text("baseline\n")
    (repository / "review").mkdir()
    return repository


def preflight_args(repository: Path, state_root: Path, *, legacy=()) -> argparse.Namespace:
    return argparse.Namespace(
        repository=str(repository), state_root=str(state_root),
        expected_head=git(repository, "rev-parse", "HEAD"),
        work_item="work.yml", profile="profile.yml", policy="policy.yml",
        components="components.yml", task="task.txt",
        durable_artifact=list(legacy), timeout_seconds=1.0, executable="local",
    )


def test_slice5b_preflight_rejects_artifact_and_evidence_ambiguities(tmp_path: Path):
    repository = fixture_repository(tmp_path)
    commit_all(repository)
    runner = FakeEvidenceRunner()
    with pytest.raises(OperatorPreflightError, match="mutually exclusive"):
        _preflight(
            preflight_args(repository, tmp_path / "state", legacy=("payload.txt",)),
            evidence_runner=runner,
        )

    policy = repository / "policy.yml"
    policy.write_text(policy.read_text().replace("- fake_check\n", "- other\n"))
    commit_all(repository, "test: disallow evidence")
    with pytest.raises(OperatorPreflightError, match="outside repository policy"):
        _preflight(preflight_args(repository, tmp_path / "state-2"), evidence_runner=runner)

    policy.write_text(policy.read_text().replace("- other\n", "- fake_check\n"))
    commit_all(repository, "test: restore evidence")
    with pytest.raises(OperatorPreflightError, match="unresolved evidence"):
        _preflight(
            preflight_args(repository, tmp_path / "state-3"),
            evidence_runner=FakeEvidenceRunner(supported=()),
        )


def test_slice5b_static_profile_and_legacy_preflight_negatives(tmp_path: Path):
    data = fixture_data()
    bad_policy = json.loads(json.dumps(data["profile"]))
    bad_policy["phases"]["review"]["review"]["blocking_policy"] = "all_findings"
    with pytest.raises(ValidationError, match="blocking_only"):
        workflow_profile_from_yaml(yaml.safe_dump(bad_policy))

    bad_route = json.loads(json.dumps(data["profile"]))
    bad_route["phases"]["review"]["review"]["failed_evidence_route"] = "absent"
    with pytest.raises(ValidationError, match="names unknown route"):
        workflow_profile_from_yaml(yaml.safe_dump(bad_route))

    legacy = fixture_data()
    legacy["work_item"].pop("durable_artifacts")
    repository = fixture_repository(tmp_path, data=legacy)
    commit_all(repository)
    with pytest.raises(OperatorPreflightError, match="later required phase review"):
        _preflight(
            preflight_args(repository, tmp_path / "state"),
            evidence_runner=FakeEvidenceRunner(),
        )

    reentry = fixture_data()
    reentry["work_item"].pop("durable_artifacts")
    reentry["work_item"]["initial_phase"] = "review"
    reentry["profile"]["phases"]["review"]["routes"]["again"] = {
        "effect": "goto", "target": "review"
    }
    repository = fixture_repository(tmp_path / "reentry", data=reentry)
    commit_all(repository)
    with pytest.raises(OperatorPreflightError, match="direct initial-phase re-entry"):
        _preflight(
            preflight_args(repository, tmp_path / "state-2", legacy=("review/report.md",)),
            evidence_runner=FakeEvidenceRunner(),
        )


class ScriptedConvergenceAdapter:
    def __init__(self, repository: Path):
        self.repository = repository
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        hop = request.invocation.hop_id
        common = {
            "schema_version": 1, "scope_changed": False,
            "requires_human": False, "escalation": None,
            "claimed_commits": [], "evidence_refs": [f"agent-{hop}"],
        }
        if hop == "H001":
            self.repository.joinpath("payload.txt").write_text("investigated\n")
            result_value = {
                **common, "outcome": "completed", "requested_route": "review",
                "findings": [],
                "commit_intent": {"paths": ["payload.txt"],
                                  "subject": "feat(workflow): WI-GENERIC-5B H001 investigate"},
                "summary": "investigation completed",
            }
        elif hop == "H002":
            self.repository.joinpath("review", "report.md").write_text("blocking issue\n")
            result_value = {
                **common, "outcome": "changes_required", "requested_route": "correct",
                "findings": [{"finding_id": None, "invariant": "Payload must satisfy the generic invariant.",
                              "blocking": True, "proposed_state": "open",
                              "evidence_refs": ["review-observation"]}],
                "commit_intent": {"paths": ["review/report.md"],
                                  "subject": "docs(workflow): WI-GENERIC-5B H002 review"},
                "summary": "independent review found a blocker",
            }
        elif hop == "H003":
            assert "finding=F001|state=open|blocking=true" in request.prompt
            self.repository.joinpath("payload.txt").write_text("corrected\n")
            result_value = {
                **common, "outcome": "completed", "requested_route": "rereview",
                "findings": [{"finding_id": "F001", "invariant": "Payload must satisfy the generic invariant.",
                              "blocking": True, "proposed_state": "open",
                              "evidence_refs": ["correction-evidence"]}],
                "commit_intent": {"paths": ["payload.txt"],
                                  "subject": "fix(workflow): WI-GENERIC-5B H003 correct"},
                "summary": "correction supplied evidence without disposition",
            }
        else:
            assert hop == "H004"
            assert "prior_hop=H003" in request.prompt
            result_value = {
                **common, "outcome": "completed", "requested_route": "accept",
                "findings": [{"finding_id": "F001", "invariant": "Payload must satisfy the generic invariant.",
                              "blocking": True, "proposed_state": "resolved",
                              "evidence_refs": ["fresh-rereview"]}],
                "commit_intent": None,
                "summary": "fresh gate re-review resolved the blocker",
            }
        envelope = {
            "schema_version": 1,
            "run_id": request.invocation.run_id,
            "hop_id": hop,
            "invocation_id": request.invocation.invocation_id,
            "result": result_value,
        }
        return AgentExecutionOutcome(json.dumps(envelope), 0, False, "", "")


def test_integrated_non_dmc_four_hop_convergence_uses_fresh_context_and_truthful_evidence(
    tmp_path: Path, monkeypatch
):
    repository = fixture_repository(tmp_path)
    baseline = commit_all(repository)
    runner = FakeEvidenceRunner()
    state_root = tmp_path / "state"
    (
        observed_repository, observed_state, _paths, texts, item, workflow,
        policy, components, artifact_map, legacy_artifacts, governing, active_runner,
    ) = _preflight(
        preflight_args(repository, state_root), evidence_runner=runner
    )
    assert observed_repository == repository and observed_state == state_root
    assert legacy_artifacts == ()
    assert active_runner is runner
    assert runner.support_calls == ["fake_check"] * 4

    import scripts.workflow_coordinator.operator as operator

    original = operator._governing_inputs_unchanged
    revalidations = []

    def counted(*args, **kwargs):
        revalidations.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(operator, "_governing_inputs_unchanged", counted)
    run = new_run(item, workflow, policy, run_id="RUN-GENERIC-5B", baseline_head=baseline)
    association = register_checkout(state_root, repository)
    adapter = ScriptedConvergenceAdapter(repository)
    final, evidence = run_serial_hops(
        run, workflow, repository, components,
        state_root=state_root, association=association,
        task_payload=texts["task"], governing=governing,
        timeout_seconds=1, executable="local", adapter=adapter,
        durable_artifacts_by_phase=artifact_map,
        evidence_runner=active_runner,
    )

    assert final.status is RunStatus.COMPLETED
    assert final.hop_used == len(final.hops) == len(adapter.requests) == 4
    assert [hop.hop_id for hop in final.hops] == ["H001", "H002", "H003", "H004"]
    assert len({request.invocation.invocation_id for request in adapter.requests}) == 4
    assert all(request.invocation.hop_id in request.prompt for request in adapter.requests)
    assert [hop.finding_delta_ids for hop in final.hops] == [(), ("F001",), ("F001",), ("F001",)]
    assert final.findings[0].state.value == "resolved"
    assert final.findings[0].evidence_refs == (
        "review-observation", "correction-evidence", "fresh-rereview"
    )
    assert len(revalidations) == 3
    assert runner.observe_calls == ["fake_check"] * 4
    assert len(evidence) == 4
    assert all(entry["context"] == "fresh" for entry in evidence)
    assert all(entry["trusted_observations"][0]["provider"] == "fake_check" for entry in evidence)
    assert all(entry["trusted_observations"][0]["status"] == "pass" for entry in evidence)
    assert not (state_root / "checkouts" / association.checkout_id / "dispatch.json").exists()
    assert git(repository, "status", "--porcelain") == ""


def test_five_hop_budget_stops_before_h006_without_reservation_or_retry(tmp_path):
    repository = fixture_repository(tmp_path)
    baseline = commit_all(repository)
    workflow = profile({"loop": replace(
        phase({"again": route("goto", "loop")}),
        authorized_scope=("payload.txt",), evidence=(),
    )})
    item = WorkItem(
        1, "WI-BUDGET-5B", "fixture", "profile.yml", "repo",
        ("payload.txt",), "loop", 5,
    )
    policy = RepositoryPolicy(1, "policy", "1", ("repo",), ("edit", "commit"), ())
    run = new_run(item, workflow, policy, run_id="RUN-BUDGET-5B", baseline_head=baseline)

    class LoopAdapter:
        def __init__(self):
            self.requests = []

        def execute(self, request):
            self.requests.append(request)
            envelope = {
                "schema_version": 1, "run_id": request.invocation.run_id,
                "hop_id": request.invocation.hop_id,
                "invocation_id": request.invocation.invocation_id,
                "result": base_result("again"),
            }
            return AgentExecutionOutcome(json.dumps(envelope), 0, False, "", "")

    adapter = LoopAdapter()
    state = tmp_path / "state"
    association = register_checkout(state, repository)
    final, evidence = run_serial_hops(
        run, workflow, repository,
        {"worker": PromptComponent("worker", "worker", baseline, "worker"),
         "testing": PromptComponent("testing", "testing", baseline, "testing")},
        state_root=state, association=association, task_payload="loop",
        governing={}, timeout_seconds=1, executable="local", adapter=adapter,
        durable_artifacts=("payload.txt",),
    )
    assert final.status is RunStatus.AWAITING_HUMAN_BUDGET
    assert final.hop_used == len(adapter.requests) == len(evidence) == 5
    assert [request.invocation.hop_id for request in adapter.requests] == [
        "H001", "H002", "H003", "H004", "H005"
    ]
    assert "durable_artifacts=payload.txt" in adapter.requests[0].prompt
    assert "durable_artifacts=\n" in adapter.requests[1].prompt
    assert not (state / "checkouts" / association.checkout_id / "dispatch.json").exists()


class ResultAdapter:
    def __init__(self, result_value: dict[str, object], mutate=None):
        self.result_value = result_value
        self.mutate = mutate

    def execute(self, request):
        if self.mutate:
            self.mutate(request.repository)
        envelope = {
            "schema_version": 1, "run_id": request.invocation.run_id,
            "hop_id": request.invocation.hop_id,
            "invocation_id": request.invocation.invocation_id,
            "result": self.result_value,
        }
        return AgentExecutionOutcome(json.dumps(envelope), 0, False, "", "")


def base_result(route_key: str, *, outcome="completed", commit_intent=None, findings=()):
    return {
        "schema_version": 1, "outcome": outcome, "requested_route": route_key,
        "scope_changed": False, "requires_human": False, "escalation": None,
        "findings": list(findings), "claimed_commits": [],
        "commit_intent": commit_intent, "evidence_refs": [], "summary": "fixture",
    }


@pytest.mark.parametrize("outcome, expected", [("blocked", "result_blocked"),
                                                ("failed", "result_failed")])
def test_unsuccessful_outcomes_do_not_require_artifact_or_route(tmp_path, outcome, expected):
    repository = fixture_repository(tmp_path)
    baseline = commit_all(repository)
    workflow = profile({"review": replace(
        phase({"again": route("goto", "review")}, review="gate"),
        authorized_scope=("review/report.md",),
        durable_artifact=DurableArtifact.REQUIRED,
        evidence=(),
    )})
    run = replace(run_for(workflow, budget=2), current_head=baseline)
    vertical = run_one_hop(
        run, workflow, repository,
        ResultAdapter(base_result("again", outcome=outcome)),
        {"worker": PromptComponent("worker", "worker", baseline, "worker"),
         "testing": PromptComponent("testing", "testing", baseline, "testing")},
        task_payload="task", invocation_id="INV-1",
        durable_artifacts=("review/report.md",),
    )
    assert vertical.reduction.run.stop_reason == expected


@pytest.mark.parametrize("outcome", ["blocked", "failed"])
def test_unsuccessful_outcomes_forbid_commit_intent(tmp_path, outcome):
    repository = fixture_repository(tmp_path)
    baseline = commit_all(repository)
    workflow = profile({"review": replace(
        phase({"again": route("goto", "review")}, review="gate"),
        authorized_scope=("review/report.md",), evidence=(),
    )})
    run = replace(run_for(workflow, budget=2), current_head=baseline)
    vertical = run_one_hop(
        run, workflow, repository,
        ResultAdapter(
            base_result(
                "again", outcome=outcome,
                commit_intent={"paths": ["review/report.md"],
                               "subject": "docs(workflow): WI-1 H001 forbidden"},
            ),
            mutate=lambda repo: repo.joinpath("review", "report.md").write_text("report\n"),
        ),
        {"worker": PromptComponent("worker", "worker", baseline, "worker"),
         "testing": PromptComponent("testing", "testing", baseline, "testing")},
        task_payload="task", invocation_id="INV-1",
        durable_artifacts=("review/report.md",),
    )
    assert "commit intent is not permitted" in (vertical.reduction.run.stop_reason or "")


@pytest.mark.parametrize(
    "status, expected",
    [
        ("error", "fixed error"),
        ("not_run", "fixed not_run"),
        ("malformed", "malformed trusted evidence status: malformed"),
    ],
)
def test_required_evidence_error_not_run_and_malformed_hard_stop(
    tmp_path, status, expected
):
    repository = fixture_repository(tmp_path)
    baseline = commit_all(repository)
    workflow = profile({"review": replace(
        phase({"accept": route("complete")}, review="gate"),
        authorized_scope=("payload.txt",), evidence=("fake_check",),
    )})
    run = replace(run_for(workflow, budget=1), current_head=baseline)
    vertical = run_one_hop(
        run, workflow, repository,
        ResultAdapter(base_result("accept")),
        {"worker": PromptComponent("worker", "worker", baseline, "worker"),
         "testing": PromptComponent("testing", "testing", baseline, "testing")},
        task_payload="task", invocation_id="INV-1",
        evidence_runner=FakeEvidenceRunner(status=status),
    )
    assert vertical.reduction.run.status is RunStatus.AWAITING_HUMAN
    assert expected in (vertical.reduction.run.stop_reason or "")


def test_changes_required_commit_is_limited_to_declared_review_artifact(tmp_path):
    repository = fixture_repository(tmp_path)
    baseline = commit_all(repository)
    workflow = profile({
        "review": replace(
            phase({"correct": route("goto", "correct")}, review="gate"),
            authorized_scope=("payload.txt", "review/report.md"),
            durable_artifact=DurableArtifact.REQUIRED,
            evidence=(),
        ),
        "correct": phase({"done": route("complete")}),
    })
    run = replace(run_for(workflow, budget=2), current_head=baseline)
    missing = run_one_hop(
        run, workflow, repository,
        ResultAdapter(base_result("correct", outcome="changes_required")),
        {"worker": PromptComponent("worker", "worker", baseline, "worker"),
         "testing": PromptComponent("testing", "testing", baseline, "testing")},
        task_payload="task", invocation_id="INV-missing",
        durable_artifacts=("review/report.md",),
    )
    assert "required durable artifact" in (missing.reduction.run.stop_reason or "")

    repository = fixture_repository(tmp_path / "accepted-case")
    baseline = commit_all(repository)
    run = replace(run_for(workflow, budget=2), current_head=baseline)
    intent = {"paths": ["review/report.md"],
              "subject": "docs(workflow): WI-1 H001 review artifact"}
    accepted = run_one_hop(
        run, workflow, repository,
        ResultAdapter(
            base_result("correct", outcome="changes_required", commit_intent=intent),
            mutate=lambda repo: repo.joinpath("review", "report.md").write_text("report\n"),
        ),
        {"worker": PromptComponent("worker", "worker", baseline, "worker"),
         "testing": PromptComponent("testing", "testing", baseline, "testing")},
        task_payload="task", invocation_id="INV-1",
        durable_artifacts=("review/report.md",),
    )
    assert accepted.reduction.run.phase == "correct"

    # A fresh repository/run proves that the exception cannot mutate reviewed payload.
    repository = fixture_repository(tmp_path / "payload-case")
    baseline = commit_all(repository)
    run = replace(run_for(workflow, budget=2), current_head=baseline)
    payload_intent = {"paths": ["payload.txt"],
                      "subject": "fix(workflow): WI-1 H001 payload"}
    rejected = run_one_hop(
        run, workflow, repository,
        ResultAdapter(
            base_result("correct", outcome="changes_required", commit_intent=payload_intent),
            mutate=lambda repo: repo.joinpath("payload.txt").write_text("reviewer edit\n"),
        ),
        {"worker": PromptComponent("worker", "worker", baseline, "worker"),
         "testing": PromptComponent("testing", "testing", baseline, "testing")},
        task_payload="task", invocation_id="INV-1",
        durable_artifacts=("review/report.md",),
    )
    assert "commit intent is not permitted" in (rejected.reduction.run.stop_reason or "")

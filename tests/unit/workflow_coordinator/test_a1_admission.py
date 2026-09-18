"""Pure bounded tests for carve-out A1 cross-document admission."""

from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.workflow_coordinator.model import RepositoryPolicy, WorkItem
from scripts.workflow_coordinator.operator import OperatorPreflightError, _admit_a1
from tests.utils.workflow_coordinator import phase, profile, route

pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")


def work_item(scope=("src/pkg/file.py",), *, hops=2):
    return WorkItem(
        1,
        "WI-A1",
        "fixture",
        "docs/ai_info/workflow/profile.yml",
        "repo",
        scope,
        "work",
        hops,
    )


def workflow(scope=("src/pkg/file.py",)):
    selected = replace(
        phase({"done": route("complete")}),
        authorized_scope=scope,
        evidence=(),
    )
    return profile({"work": selected})


def policy(
    *,
    governance=("docs/ai_info",),
    ceiling=("src/pkg",),
    hops=2,
):
    return RepositoryPolicy(
        2,
        "policy-a1",
        "r2",
        ("repo",),
        governance,
        ceiling,
        hops,
        ("edit", "commit"),
        (),
    )


def test_a1_admits_multiple_governing_files_exact_ceilings_and_equal_hops():
    _admit_a1(
        work_item(),
        workflow(),
        policy(ceiling=("src/pkg/file.py",)),
        (
            "docs/ai_info/workflow/work.yml",
            "docs/ai_info/workflow/profile.yml",
            "docs/ai_info/workflow/policy.yml",
            "docs/ai_info/workflow/components.yml",
            "docs/ai_info/workflow/task.txt",
            "docs/ai_info/components/worker.txt",
        ),
    )


@pytest.mark.parametrize(
    ("phase_scope", "work_scope", "ceiling"),
    [
        (("src/pkg/file.py",), ("src/pkg/file.py",), ("src/pkg",)),
        (("docs/ai_info_extra/x",), ("docs/ai_info_extra/x",), ("docs/ai_info_extra",)),
        ((), (), ("src/read-only-boundary",)),
    ],
    ids=["sibling-phase", "sibling-prefix", "read-only-empty-scope"],
)
def test_a1_admits_sibling_and_read_only_topologies(phase_scope, work_scope, ceiling):
    _admit_a1(
        work_item(work_scope),
        workflow(phase_scope),
        policy(ceiling=ceiling),
        ("docs/ai_info/work.yml",),
    )


def test_a1_admits_nonexistent_governance_path_lexically():
    _admit_a1(
        work_item(),
        workflow(),
        policy(governance=("future/governance",)),
        ("future/governance/not-created-yet.txt",),
    )


def test_a1_rejects_governing_input_outside_every_governance_path():
    with pytest.raises(OperatorPreflightError, match="outside repository policy"):
        _admit_a1(work_item(), workflow(), policy(), ("outside/task.txt",))


@pytest.mark.parametrize(
    "phase_scope",
    [
        ("docs/ai_info",),
        ("docs",),
        ("docs/ai_info/workflows",),
    ],
    ids=["equal", "phase-ancestor", "phase-descendant"],
)
def test_a1_rejects_symmetric_phase_governance_overlap(phase_scope):
    with pytest.raises(OperatorPreflightError, match="authorized_scope overlaps"):
        _admit_a1(
            work_item(phase_scope),
            workflow(phase_scope),
            policy(ceiling=("docs",)),
            ("docs/ai_info/work.yml",),
        )


@pytest.mark.parametrize("scope", [("tests/file.py",), ("src",)])
def test_a1_rejects_work_item_outside_or_wider_than_ceiling(scope):
    with pytest.raises(OperatorPreflightError, match="work_item_scope_ceiling"):
        _admit_a1(
            work_item(scope),
            workflow(()),
            policy(ceiling=("src/pkg",)),
            ("docs/ai_info/work.yml",),
        )


def test_a1_rejects_work_item_hops_above_policy_ceiling():
    with pytest.raises(OperatorPreflightError, match="exceeds repository policy maximum"):
        _admit_a1(
            work_item(hops=3),
            workflow(),
            policy(hops=2),
            ("docs/ai_info/work.yml",),
        )


@pytest.mark.parametrize("operand", ["work-item", "phase", "governing"])
def test_a1_rejects_repository_root_for_every_operator_operand(operand):
    item = work_item((".",) if operand == "work-item" else ())
    selected_profile = workflow((".",) if operand == "phase" else ())
    governing = (".",) if operand == "governing" else ("docs/ai_info/work.yml",)
    with pytest.raises(OperatorPreflightError, match="malformed A1 paths"):
        _admit_a1(item, selected_profile, policy(), governing)

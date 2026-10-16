"""Production-preflight proof for current self-development governance."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import workflow_coordinator.operator as operator_module
from workflow_coordinator.operator import (
    OperatorPreflightError,
    _preflight as _production_preflight,
    _selected_ids,
    _within_scope,
)
from workflow_coordinator.model import (
    COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS,
    RunStatus,
    WorkflowRole,
)
from workflow_coordinator.reducer import reduce_result
from tests.support import charge_hop, phase, profile, result, route, run_for

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _preflight(args):
    """Inspect archived profiles without treating them as new-launchable.

    The governance fixtures below are historical standalone profiles.  Their
    worker-owned required evidence is deliberately still readable for archive
    inspection, but the evidence-disposition hardening makes each one invalid
    at a new-launch boundary.  Assert that boundary first, then retain the
    pre-existing structural checks using an inspection-only invocation with
    just that new-launch admission predicate suppressed.
    """
    with pytest.raises(
        OperatorPreflightError,
        match="has required evidence but (is not a gate review|no failed_evidence_route)",
    ):
        _production_preflight(args)

    original_admission = operator_module._admit_evidence_disposition
    try:
        operator_module._admit_evidence_disposition = lambda profile: None
        return _production_preflight(args)
    finally:
        operator_module._admit_evidence_disposition = original_admission


GOVERNING_PATHS = (
    "governance/work-items/developer-ergonomics.yaml",
    "governance/self-development/standalone-development-profile.yaml",
    "governance/self-development/repository-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/developer-ergonomics-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CONTINUATION_GOVERNING_PATHS = (
    "governance/work-items/developer-ergonomics-review.yaml",
    "governance/self-development/standalone-development-profile.yaml",
    "governance/self-development/repository-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/developer-ergonomics-review-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
ATTEMPT_4_GOVERNING_PATHS = (
    "governance/work-items/developer-ergonomics-review-002.yaml",
    "governance/self-development/standalone-development-profile.yaml",
    "governance/self-development/repository-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/developer-ergonomics-review-002-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
ATTEMPT_5_GOVERNING_PATHS = (
    "governance/work-items/developer-ergonomics-review-003.yaml",
    "governance/self-development/standalone-development-profile.yaml",
    "governance/self-development/repository-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/developer-ergonomics-review-003-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
ATTEMPT_6_GOVERNING_PATHS = (
    "governance/work-items/developer-ergonomics-review-004.yaml",
    "governance/self-development/standalone-development-profile.yaml",
    "governance/self-development/repository-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/developer-ergonomics-review-004-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
ATTEMPT_7_GOVERNING_PATHS = (
    "governance/work-items/developer-ergonomics-review-005.yaml",
    "governance/self-development/standalone-development-profile.yaml",
    "governance/self-development/repository-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/developer-ergonomics-review-005-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
GENERATIVE_ECHO_AUDIT_GOVERNING_PATHS = (
    "governance/work-items/generative-echo-audit.yaml",
    "governance/self-development/standalone-development-profile.yaml",
    "governance/self-development/repository-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/generative-echo-audit-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
IMMUTABLE_FINDING_ECHO_FIX_GOVERNING_PATHS = (
    "governance/work-items/immutable-finding-echo-fix.yaml",
    "governance/self-development/immutable-finding-echo-profile.yaml",
    "governance/self-development/immutable-finding-echo-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/immutable-finding-echo-fix-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
IMMUTABLE_FINDING_ECHO_FIX_002_GOVERNING_PATHS = (
    "governance/work-items/immutable-finding-echo-fix-002.yaml",
    "governance/self-development/immutable-finding-echo-profile-002.yaml",
    "governance/self-development/immutable-finding-echo-policy-002.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/immutable-finding-echo-fix-002-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
SCOPE_ESCALATION_CONTINUATION_DESIGN_GOVERNING_PATHS = (
    "governance/work-items/scope-escalation-continuation-design.yaml",
    "governance/self-development/scope-escalation-continuation-design-profile.yaml",
    "governance/self-development/scope-escalation-continuation-design-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/scope-escalation-continuation-design-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
SCOPE_ESCALATION_CONTINUATION_DESIGN_SCOPE = (
    "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION.adoc",
)
SCOPE_ESCALATION_CONTINUATION_DESIGN_REVIEW_ARTIFACT = (
    "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION_review_001.adoc"
)
CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-design-001.yaml",
    "governance/self-development/correction-convergence-escape-design-001-profile.yaml",
    "governance/self-development/correction-convergence-escape-design-001-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-design-001-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_SCOPE = (
    "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc",
)
CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT = (
    "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_review_001.adoc"
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_REVIEW_ARTIFACT = (
    "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_implementation_review_001.adoc"
)
CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-design-revision-001.yaml",
    "governance/self-development/correction-convergence-escape-design-revision-001-profile.yaml",
    "governance/self-development/correction-convergence-escape-design-revision-001-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-design-revision-001-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE = (
    "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001.adoc",
)
CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT = (
    "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001_review_001.adoc"
)
CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_CONTINUATION_001_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-design-revision-001-continuation-001.yaml",
    "governance/self-development/correction-convergence-escape-design-revision-001-continuation-001-profile.yaml",
    "governance/self-development/correction-convergence-escape-design-revision-001-continuation-001-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-design-revision-001-continuation-001-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_GOVERNING_PATHS = (
    "governance/work-items/scope-escalation-continuation-implementation.yaml",
    "governance/self-development/scope-escalation-continuation-implementation-profile.yaml",
    "governance/self-development/scope-escalation-continuation-implementation-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/scope-escalation-continuation-implementation-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE = (
    "src/workflow_coordinator/model.py",
    "src/workflow_coordinator/serialization.py",
    "src/workflow_coordinator/reducer.py",
    "src/workflow_coordinator/persistence.py",
    "src/workflow_coordinator/operator.py",
    "src/workflow_coordinator/git_facts.py",
    "tests/unit/test_reducer.py",
    "tests/unit/test_serialization.py",
    "tests/unit/test_persistence.py",
    "tests/unit/test_operator.py",
    "tests/unit/test_git_facts.py",
    "tests/architecture/test_replays.py",
)
SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT = (
    "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION_implementation_review_001.adoc"
)
SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_GOVERNING_PATHS = (
    "governance/work-items/scope-escalation-continuation-implementation-002.yaml",
    "governance/self-development/scope-escalation-continuation-implementation-002-profile.yaml",
    "governance/self-development/scope-escalation-continuation-implementation-002-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/scope-escalation-continuation-implementation-002-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_REVIEW_ARTIFACT = (
    "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION_implementation_review_002.adoc"
)
SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_GOVERNING_PATHS = (
    "governance/work-items/scope-escalation-continuation-implementation-003.yaml",
    "governance/self-development/scope-escalation-continuation-implementation-003-profile.yaml",
    "governance/self-development/scope-escalation-continuation-implementation-003-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/scope-escalation-continuation-implementation-003-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_REVIEW_ARTIFACT = (
    "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION_implementation_review_003.adoc"
)
SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_GOVERNING_PATHS = (
    "governance/work-items/scope-escalation-continuation-implementation-004.yaml",
    "governance/self-development/scope-escalation-continuation-implementation-004-profile.yaml",
    "governance/self-development/scope-escalation-continuation-implementation-004-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/scope-escalation-continuation-implementation-004-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_REVIEW_ARTIFACT = (
    "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION_implementation_review_004.adoc"
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-implementation-s1-001.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s1-001-profile.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s1-001-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-implementation-s1-001-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_SCOPE = (
    "src/workflow_coordinator/model.py",
    "src/workflow_coordinator/serialization.py",
    "src/workflow_coordinator/adapter.py",
    "src/workflow_coordinator/codex_adapter.py",
    "src/workflow_coordinator/reducer.py",
    "src/workflow_coordinator/persistence.py",
    "tests/support.py",
    "tests/unit/test_serialization.py",
    "tests/unit/test_reducer.py",
    "tests/unit/test_persistence.py",
    "tests/unit/test_codex_adapter_conformance.py",
    "tests/architecture/test_replays.py",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT = (
    "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_implementation_review_001.adoc"
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-implementation-s1-002.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s1-002-profile.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s1-002-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-implementation-s1-002-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_SCOPE = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_SCOPE
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-implementation-s1-003.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s1-003-profile.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s1-003-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-implementation-s1-003-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_SCOPE = (
    "src/workflow_coordinator/reducer.py",
    "tests/unit/test_reducer.py",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_REVIEW_ARTIFACT = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-implementation-s2-001.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s2-001-profile.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s2-001-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-implementation-s2-001-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_SCOPE = (
    "src/workflow_coordinator/model.py",
    "src/workflow_coordinator/reducer.py",
    "src/workflow_coordinator/persistence.py",
    "tests/support.py",
    "tests/unit/test_reducer.py",
    "tests/unit/test_persistence.py",
    "tests/architecture/test_replays.py",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_REVIEW_ARTIFACT
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-implementation-s2-002.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s2-002-profile.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s2-002-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-implementation-s2-002-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_SCOPE = (
    "src/workflow_coordinator/model.py",
    "src/workflow_coordinator/serialization.py",
    "src/workflow_coordinator/reducer.py",
    "src/workflow_coordinator/persistence.py",
    "src/workflow_coordinator/vertical.py",
    "src/workflow_coordinator/slice4.py",
    "src/workflow_coordinator/operator.py",
    "tests/support.py",
    "tests/unit/test_reducer.py",
    "tests/unit/test_persistence.py",
    "tests/unit/test_serialization.py",
    "tests/unit/test_slice4.py",
    "tests/unit/test_operator.py",
    "tests/architecture/test_replays.py",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_REVIEW_ARTIFACT = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_003_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-implementation-s2-003.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s2-003-profile.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s2-003-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-implementation-s2-003-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_003_SCOPE = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_SCOPE
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_003_REVIEW_ARTIFACT = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_REVIEW_ARTIFACT
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-implementation-s3-001.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s3-001-profile.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s3-001-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-implementation-s3-001-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_SCOPE = (
    "src/workflow_coordinator/model.py",
    "src/workflow_coordinator/serialization.py",
    "src/workflow_coordinator/reducer.py",
    "src/workflow_coordinator/persistence.py",
    "src/workflow_coordinator/prompt.py",
    "src/workflow_coordinator/vertical.py",
    "src/workflow_coordinator/operator.py",
    "tests/support.py",
    "tests/unit/test_serialization.py",
    "tests/unit/test_reducer.py",
    "tests/unit/test_persistence.py",
    "tests/unit/test_slice3_vertical.py",
    "tests/unit/test_operator.py",
    "tests/architecture/test_replays.py",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_REVIEW_ARTIFACT = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_REVIEW_ARTIFACT
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_002_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-implementation-s3-002.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s3-002-profile.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s3-002-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-implementation-s3-002-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_002_SCOPE = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_SCOPE
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_002_REVIEW_ARTIFACT = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_REVIEW_ARTIFACT
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_003_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-implementation-s3-003.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s3-003-profile.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s3-003-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-implementation-s3-003-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_003_SCOPE = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_SCOPE
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_003_REVIEW_ARTIFACT = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_REVIEW_ARTIFACT
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-implementation-s4-001.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s4-001-profile.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s4-001-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-implementation-s4-001-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_SCOPE = (
    "src/workflow_coordinator/model.py",
    "src/workflow_coordinator/serialization.py",
    "src/workflow_coordinator/codex_adapter.py",
    "src/workflow_coordinator/reducer.py",
    "src/workflow_coordinator/persistence.py",
    "src/workflow_coordinator/prompt.py",
    "src/workflow_coordinator/vertical.py",
    "src/workflow_coordinator/operator.py",
    "tests/support.py",
    "tests/unit/test_serialization.py",
    "tests/unit/test_reducer.py",
    "tests/unit/test_persistence.py",
    "tests/unit/test_codex_adapter_conformance.py",
    "tests/unit/test_slice3_vertical.py",
    "tests/unit/test_operator.py",
    "tests/architecture/test_replays.py",
    "tests/fixtures/convergence_curriculum.yaml",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_REVIEW_ARTIFACT = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_REVIEW_ARTIFACT
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_002_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-implementation-s4-002.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s4-002-profile.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s4-002-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-implementation-s4-002-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_002_SCOPE = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_SCOPE
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_002_REVIEW_ARTIFACT = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_REVIEW_ARTIFACT
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_003_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-implementation-s4-003.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s4-003-profile.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s4-003-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-implementation-s4-003-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_003_SCOPE = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_002_SCOPE
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_003_REVIEW_ARTIFACT = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_002_REVIEW_ARTIFACT
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_004_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-implementation-s4-004.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s4-004-profile.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s4-004-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-implementation-s4-004-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_004_SCOPE = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_003_SCOPE
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_004_REVIEW_ARTIFACT = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_003_REVIEW_ARTIFACT
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_005_GOVERNING_PATHS = (
    "governance/work-items/correction-convergence-escape-implementation-s4-005.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s4-005-profile.yaml",
    "governance/self-development/correction-convergence-escape-implementation-s4-005-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/correction-convergence-escape-implementation-s4-005-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_005_SCOPE = (
    "src/workflow_coordinator/reducer.py",
    "tests/unit/test_reducer.py",
)
CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_005_REVIEW_ARTIFACT = (
    CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_004_REVIEW_ARTIFACT
)
IMPLEMENTATION_PATHS = (
    "Makefile",
    "README.md",
    "pyproject.toml",
    "scripts/wfc_snapshot.py",
    "tests/unit/test_wfc_snapshot.py",
    "tests/architecture/test_package_boundaries.py",
)
REVIEW_NAMESPACE = "docs/backlog/wfc_reviews"
REVIEW_ARTIFACT = f"{REVIEW_NAMESPACE}/developer_ergonomics_review.adoc"
HISTORICAL_REVIEW_ARTIFACT = "docs/backlog/first_wfc_on_wfc_dogfood_review.adoc"
IMMUTABLE_FINDING_ECHO_FIX_CORRECTION_SCOPE = (
    "src/workflow_coordinator/model.py",
    "src/workflow_coordinator/serialization.py",
    "src/workflow_coordinator/reducer.py",
    "src/workflow_coordinator/codex_adapter.py",
    "tests/unit/test_reducer.py",
    "tests/unit/test_serialization.py",
    "tests/unit/test_codex_adapter_conformance.py",
    "tests/unit/test_slice3_vertical.py",
    "tests/unit/test_slice5b.py",
    "tests/architecture/test_replays.py",
)
IMMUTABLE_FINDING_ECHO_FIX_REVIEW_ARTIFACT = (
    "docs/backlog/BUG-WFC-IMMUTABLE-FINDING-ECHO_review_001.adoc"
)
IMMUTABLE_FINDING_ECHO_FIX_002_CORRECTION_SCOPE = (
    *IMMUTABLE_FINDING_ECHO_FIX_CORRECTION_SCOPE,
    "tests/unit/test_slice4.py",
)
IMMUTABLE_FINDING_ECHO_FIX_002_REVIEW_ARTIFACT = (
    "docs/backlog/BUG-WFC-IMMUTABLE-FINDING-ECHO_review_002.adoc"
)


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _current_text(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def _materialize_bundle(
    tmp_path: Path,
    *,
    governing_paths: tuple[str, ...] = GOVERNING_PATHS,
    replacements: dict[str, str] | None = None,
) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--object-format=sha1")
    _git(repository, "config", "user.email", "preflight@example.invalid")
    _git(repository, "config", "user.name", "B2 Preflight")
    for relative in governing_paths:
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        text = (replacements or {}).get(relative, _current_text(relative))
        target.write_text(text, encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "test: exact governing bundle")
    expected_head = _git(repository, "rev-parse", "HEAD")
    assert len(expected_head) == 40
    return repository, expected_head


def _preflight_args(
    repository: Path,
    expected_head: str,
    state_root: Path,
    *,
    governing_paths: tuple[str, ...] = GOVERNING_PATHS,
) -> argparse.Namespace:
    binding_source = state_root.parent / f"{state_root.name}-bindings.yml"
    binding_source.parent.mkdir(parents=True, exist_ok=True)
    binding_source.write_text(
        "schema_version: 1\nbindings:\n"
        "  worker: {adapter: codex_cli, model: worker-model, reasoning: medium}\n"
        "  independent_reviewer: {adapter: codex_cli, model: reviewer-model, reasoning: high}\n",
        encoding="utf-8",
    )
    return argparse.Namespace(
        repository=str(repository),
        state_root=str(state_root),
        expected_head=expected_head,
        work_item=governing_paths[0],
        profile=governing_paths[1],
        policy=governing_paths[2],
        components=governing_paths[3],
        task=governing_paths[4],
        execution_bindings=str(binding_source),
        durable_artifact=[],
        timeout_seconds=900.0,
        executable="unused-before-dispatch",
    )


@pytest.fixture
def preflight_python_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Expose this test environment's invoked interpreter as policy `python`."""

    monkeypatch.setenv(
        "PATH",
        os.pathsep.join((str(Path(sys.executable).parent), os.environ.get("PATH", ""))),
    )


def test_current_self_development_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    repository, expected_head = _materialize_bundle(tmp_path)

    state_root = tmp_path / "external-state" / "new-run"
    args = _preflight_args(repository, expected_head, state_root)

    assert not state_root.exists()
    result = _preflight(args)
    work_item, profile, policy = result[5:8]
    components, durable_artifacts = result[8:10]
    prepared_evidence, environment = result[13:15]
    exact_scope = (*IMPLEMENTATION_PATHS, REVIEW_NAMESPACE)

    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert profile.revision == "4"
    assert policy.revision == "3"
    assert work_item.max_autonomous_hops == 4
    assert policy.max_autonomous_hops == 5
    assert tuple(profile.phases) == ("investigate", "review", "correct", "rereview")
    assert durable_artifacts == {"review": (REVIEW_ARTIFACT,)}
    assert tuple(components) == _selected_ids(profile) == ("worker", "reviewer")
    investigate = profile.phases["investigate"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]
    assert investigate.authorized_scope == correct.authorized_scope == IMPLEMENTATION_PATHS
    assert investigate.authorized_actions == correct.authorized_actions == ("edit", "commit")
    assert review.authorized_scope == rereview.authorized_scope == (REVIEW_NAMESPACE,)
    assert HISTORICAL_REVIEW_ARTIFACT not in review.authorized_scope
    assert HISTORICAL_REVIEW_ARTIFACT not in rereview.authorized_scope
    assert review.authorized_scope != ("docs/backlog",)
    assert not _within_scope("docs/backlog/unrelated.adoc", review.authorized_scope)
    assert review.authorized_actions == ("review", "edit", "commit")
    assert rereview.authorized_actions == ("review",)
    assert investigate.durable_artifact.value == correct.durable_artifact.value == "optional"
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "none"
    assert _within_scope(REVIEW_ARTIFACT, work_item.scope)
    assert _within_scope(REVIEW_ARTIFACT, review.authorized_scope)
    assert set(IMPLEMENTATION_PATHS).isdisjoint(review.authorized_scope)
    assert REVIEW_NAMESPACE not in investigate.authorized_scope
    assert REVIEW_NAMESPACE not in correct.authorized_scope
    assert {
        phase: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase, value in profile.phases.items()
    } == {
        "investigate": (("review", "goto", "review"),),
        "review": (
            ("rereview", "goto", "rereview"),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (("accept", "complete", None), ("correct", "goto", "correct")),
    }
    assert review.routes["rereview"].target == "rereview"
    assert review.role == rereview.role == "independent_reviewer"
    assert policy.governance_paths == ("governance",)
    assert policy.actions == ("edit", "commit", "review")
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(prepared_evidence) == ("pytest",)
    prepared = prepared_evidence["pytest"]
    assert prepared.authored_argv == ("python", "-m", "pytest", "-q")
    assert prepared.admission_resolution.invocation_path == str(Path(sys.executable).parent / "python")
    assert prepared.admission_resolution.canonical_path == str(Path(sys.executable).resolve())
    assert environment.values["PATH"].split(":", 1)[0] == str(Path(sys.executable).parent)
    assert not state_root.exists()


def test_production_preflight_identifies_different_python_interpreter_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preflight_python_path: None,
):
    alternate_bin = tmp_path / "alternate-bin"
    alternate_bin.mkdir()
    alternate_python = alternate_bin / "python"
    shutil.copy2(sys.executable, alternate_python)

    monkeypatch.setenv(
        "PATH",
        os.pathsep.join((str(alternate_bin), os.environ["PATH"])),
    )
    repository, expected_head = _materialize_bundle(tmp_path)
    state_root = tmp_path / "external-state" / "different-interpreter"

    result = _preflight(_preflight_args(repository, expected_head, state_root))
    prepared = result[13]["pytest"]

    assert prepared.admission_resolution.canonical_path == str(alternate_python.resolve())
    assert prepared.admission_resolution.canonical_path != str(Path(sys.executable).resolve())
    assert not state_root.exists()


def test_review_continuation_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CONTINUATION_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "review-continuation"

    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CONTINUATION_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    review = profile.phases["review"]

    artifact = f"{REVIEW_NAMESPACE}/WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-001/review.adoc"
    assert work_item.work_item_id == "WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-001"
    assert work_item.initial_phase == "review"
    assert work_item.max_autonomous_hops == 3
    assert work_item.scope == policy.work_item_scope_ceiling == (
        *IMPLEMENTATION_PATHS,
        REVIEW_NAMESPACE,
    )
    assert profile.revision == "4"
    assert policy.revision == "3"
    assert durable_artifacts == {"review": (artifact,)}
    assert _within_scope(artifact, work_item.scope)
    assert _within_scope(artifact, review.authorized_scope)
    assert tuple(review.routes) == ("rereview", "correct")
    assert "accept" not in review.routes
    assert tuple(profile.phases["correct"].routes) == ("rereview",)
    assert tuple(profile.phases["rereview"].routes) == ("accept", "correct")
    assert not state_root.exists()


def test_attempt_4_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=ATTEMPT_4_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "attempt-4"

    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=ATTEMPT_4_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    review = profile.phases["review"]

    artifact = f"{REVIEW_NAMESPACE}/WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-002/review.adoc"
    assert work_item.work_item_id == "WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-002"
    assert work_item.initial_phase == "review"
    assert work_item.max_autonomous_hops == 3
    assert profile.revision == "4"
    assert policy.revision == "3"
    assert work_item.scope == policy.work_item_scope_ceiling == (
        *IMPLEMENTATION_PATHS,
        REVIEW_NAMESPACE,
    )
    assert durable_artifacts == {"review": (artifact,)}
    assert _within_scope(artifact, work_item.scope)
    assert _within_scope(artifact, review.authorized_scope)
    assert tuple(review.routes) == ("rereview", "correct")
    assert "accept" not in review.routes
    assert tuple(profile.phases["correct"].routes) == ("rereview",)
    assert tuple(profile.phases["rereview"].routes) == ("accept", "correct")
    assert not state_root.exists()


def test_attempt_5_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=ATTEMPT_5_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "attempt-5"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=ATTEMPT_5_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    review = profile.phases["review"]

    artifact = f"{REVIEW_NAMESPACE}/WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-003/review.adoc"
    assert work_item.work_item_id == "WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-003"
    assert work_item.initial_phase == "review"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 5
    assert COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS == 20
    assert profile.revision == "4"
    assert policy.revision == "3"
    assert work_item.scope == policy.work_item_scope_ceiling == (
        *IMPLEMENTATION_PATHS,
        REVIEW_NAMESPACE,
    )
    assert durable_artifacts == {"review": (artifact,)}
    assert _within_scope(artifact, work_item.scope)
    assert _within_scope(artifact, review.authorized_scope)
    assert tuple(review.routes) == ("rereview", "correct")
    assert "accept" not in review.routes
    assert tuple(profile.phases["correct"].routes) == ("rereview",)
    assert tuple(profile.phases["rereview"].routes) == ("accept", "correct")
    assert not state_root.exists()


def test_attempt_6_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=ATTEMPT_6_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "attempt-6"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=ATTEMPT_6_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]

    assert work_item.work_item_id == "WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-004"
    assert work_item.initial_phase == "rereview"
    assert work_item.max_autonomous_hops == 3
    assert profile.revision == "4"
    assert policy.revision == "3"
    assert policy.max_autonomous_hops == 5
    assert work_item.scope == policy.work_item_scope_ceiling == (
        *IMPLEMENTATION_PATHS,
        REVIEW_NAMESPACE,
    )
    assert durable_artifacts == {
        "review": (
            f"{REVIEW_NAMESPACE}/WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-004/review.adoc",
        ),
    }
    assert tuple(profile.phases["review"].routes) == ("rereview", "correct")
    assert "accept" not in profile.phases["review"].routes
    assert tuple(profile.phases["correct"].routes) == ("rereview",)
    assert tuple(profile.phases["rereview"].routes) == ("accept", "correct")
    assert not state_root.exists()


def test_attempt_7_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=ATTEMPT_7_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "attempt-7"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=ATTEMPT_7_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]

    assert work_item.work_item_id == "WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-005"
    assert work_item.initial_phase == "rereview"
    assert work_item.max_autonomous_hops == 3
    assert profile.revision == "4"
    assert policy.revision == "3"
    assert policy.max_autonomous_hops == 5
    assert work_item.scope == policy.work_item_scope_ceiling == (
        *IMPLEMENTATION_PATHS,
        REVIEW_NAMESPACE,
    )
    assert durable_artifacts == {
        "review": (
            f"{REVIEW_NAMESPACE}/WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-005/review.adoc",
        ),
    }
    assert tuple(profile.phases["review"].routes) == ("rereview", "correct")
    assert "accept" not in profile.phases["review"].routes
    assert tuple(profile.phases["correct"].routes) == ("rereview",)
    assert tuple(profile.phases["rereview"].routes) == ("accept", "correct")
    assert not state_root.exists()


def test_generative_echo_audit_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Bounded architecture-assessment WorkItem: preflight-only proof.

    This governing bundle commissions an investigation, not an
    implementation: it must parse, stay within policy/profile authority,
    start at ``review``, carry a two-Hop-happy-path/three-Hop budget, name
    exactly one durable artifact, and hold no mutation authority over any
    inspected production source. WorkItem.scope carries the same ceiling as
    the developer-ergonomics chain -- the shared profile's ``investigate``/
    ``correct`` phases mechanically require it (their authorized_scope must
    lie within WorkItem.scope even though this WorkItem never reaches those
    phases in practice) -- but the phases this WorkItem actually starts and
    can reach, ``review``/``rereview``, are independently scoped by the
    profile to the review namespace alone, so actual mutation authority is
    narrower than the ceiling in practice.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=GENERATIVE_ECHO_AUDIT_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "generative-echo-audit"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=GENERATIVE_ECHO_AUDIT_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    review = profile.phases["review"]
    rereview = profile.phases["rereview"]

    artifact = f"{REVIEW_NAMESPACE}/WI-WFC-GENERATIVE-ECHO-AUDIT-001/review.adoc"

    assert work_item.work_item_id == "WI-WFC-GENERATIVE-ECHO-AUDIT-001"
    assert work_item.initial_phase == "review"
    assert work_item.max_autonomous_hops == 3
    assert profile.revision == "4"
    assert policy.revision == "3"

    # WorkItem.scope equals the shared policy ceiling: the profile's
    # `investigate`/`correct` phases mechanically require their
    # authorized_scope (IMPLEMENTATION_PATHS) to lie within WorkItem.scope,
    # so a narrower WorkItem.scope is preflight-rejected even though this
    # WorkItem never reaches those phases in practice.
    assert work_item.scope == policy.work_item_scope_ceiling == (
        *IMPLEMENTATION_PATHS,
        REVIEW_NAMESPACE,
    )

    # No mutation authority is granted over any inspected production source:
    # `src/` is not part of the ceiling at all, and this WorkItem's only
    # reachable phases (review/rereview) are independently scoped by the
    # profile to the review namespace alone.
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("src/workflow_coordinator/reducer.py", work_item.scope)
    assert not _within_scope("src/workflow_coordinator/model.py", work_item.scope)
    assert review.authorized_scope == rereview.authorized_scope == (REVIEW_NAMESPACE,)
    assert set(IMPLEMENTATION_PATHS).isdisjoint(review.authorized_scope)

    assert durable_artifacts == {"review": (artifact,)}
    assert _within_scope(artifact, work_item.scope)
    assert _within_scope(artifact, review.authorized_scope)
    assert tuple(review.routes) == ("rereview", "correct")
    assert "accept" not in review.routes
    assert tuple(rereview.routes) == ("accept", "correct")
    assert review.role == rereview.role == "independent_reviewer"
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "none"

    assert not state_root.exists()


def test_immutable_finding_echo_fix_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Bounded self-development correction commission: preflight-only proof.

    This governing bundle commissions the correction implementation
    authorized by ``docs/backlog/BUG-WFC-IMMUTABLE-FINDING-ECHO.adoc``,
    "Authorized correction scope". Unlike every other WorkItem exercised
    above, it deliberately uses its *own* dedicated profile and repository
    policy (not the shared ``standalone-development-profile.yaml`` /
    ``repository-policy.yaml``), because the shared policy's
    ``work_item_scope_ceiling`` is fixed to developer-ergonomics files plus
    the review namespace and structurally excludes ``src`` -- see that BUG
    record's "Why this is not a WFC self-development WorkItem" for why a
    WorkItem naming the correction's source files could never admit under
    the shared ceiling. The dedicated policy narrows admission to exactly
    the bounded correction scope instead of widening the shared one.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=IMMUTABLE_FINDING_ECHO_FIX_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "immutable-finding-echo-fix"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=IMMUTABLE_FINDING_ECHO_FIX_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implement = profile.phases["implement"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (*IMMUTABLE_FINDING_ECHO_FIX_CORRECTION_SCOPE, IMMUTABLE_FINDING_ECHO_FIX_REVIEW_ARTIFACT)

    assert work_item.work_item_id == "WI-WFC-IMMUTABLE-FINDING-ECHO-FIX-001"
    assert work_item.initial_phase == "implement"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 4
    assert profile.profile_id == "immutable-finding-echo-fix"
    assert policy.policy_id == "workflow-coordinator-immutable-finding-echo-fix"
    assert tuple(profile.phases) == ("implement", "review", "correct", "rereview")

    # WorkItem.scope is exactly the bounded correction scope plus the single
    # durable review artifact -- the smallest ceiling this commission needs,
    # not the shared developer-ergonomics ceiling.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("Makefile", work_item.scope)
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    for path in IMMUTABLE_FINDING_ECHO_FIX_CORRECTION_SCOPE:
        assert _within_scope(path, work_item.scope)

    # Non-scope: files the accepted BUG record explicitly withholds from any
    # correction commission remain outside this WorkItem's authority.
    for withheld in (
        "src/workflow_coordinator/adapter.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/resources/agent_operating_contract_v6.txt",
        "tests/architecture/test_self_development_governance.py",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # implement/correct share the exact bounded correction scope; review and
    # rereview are independently scoped by the profile to the single durable
    # review artifact alone -- no phase's authorized_scope grants both.
    assert implement.authorized_scope == correct.authorized_scope == IMMUTABLE_FINDING_ECHO_FIX_CORRECTION_SCOPE
    assert review.authorized_scope == rereview.authorized_scope == (IMMUTABLE_FINDING_ECHO_FIX_REVIEW_ARTIFACT,)
    assert set(IMMUTABLE_FINDING_ECHO_FIX_CORRECTION_SCOPE).isdisjoint(review.authorized_scope)
    assert IMMUTABLE_FINDING_ECHO_FIX_REVIEW_ARTIFACT not in implement.authorized_scope
    assert IMMUTABLE_FINDING_ECHO_FIX_REVIEW_ARTIFACT not in correct.authorized_scope

    assert durable_artifacts == {"review": (IMMUTABLE_FINDING_ECHO_FIX_REVIEW_ARTIFACT,)}
    assert _within_scope(IMMUTABLE_FINDING_ECHO_FIX_REVIEW_ARTIFACT, review.authorized_scope)
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "none"
    assert implement.durable_artifact.value == correct.durable_artifact.value == "none"

    # Topology: implement -> review -> {accept: complete, correct} ->
    # correct -> rereview -> {accept: complete, correct}. No
    # investigate/planning/supervisor phase was added.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "implement": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert implement.role == correct.role == "worker"
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_immutable_finding_echo_fix_002_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Successor bounded self-development correction commission: preflight-only proof.

    ``WI-WFC-IMMUTABLE-FINDING-ECHO-FIX-002`` continues predecessor
    ``WI-WFC-IMMUTABLE-FINDING-ECHO-FIX-001`` (attempt
    ``wfc-immutable-finding-echo-fix-20260930-01``, Run
    ``RUN-76e153b40ef1459db4587446b18e02d5``), which stopped
    ``awaiting_human`` after H001 with no trusted commit. This bundle uses
    its own dedicated ``-002`` WorkItem/profile/policy/task files -- the
    predecessor's governing files remain untouched frozen historical
    evidence -- and its scope ceiling is exactly the predecessor's ten
    correction paths plus the single additional path justified by that
    Run's own legitimate scope escalation, ``tests/unit/test_slice4.py``
    (see ``docs/backlog/BUG-WFC-SCOPE-ESCALATION-CONTINUATION-GAP.adoc``),
    plus a fresh review artifact, ``..._review_002.adoc`` -- never the
    predecessor's ``..._review_001.adoc``.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=IMMUTABLE_FINDING_ECHO_FIX_002_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "immutable-finding-echo-fix-002"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=IMMUTABLE_FINDING_ECHO_FIX_002_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implement = profile.phases["implement"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *IMMUTABLE_FINDING_ECHO_FIX_002_CORRECTION_SCOPE,
        IMMUTABLE_FINDING_ECHO_FIX_002_REVIEW_ARTIFACT,
    )
    assert len(IMMUTABLE_FINDING_ECHO_FIX_002_CORRECTION_SCOPE) == 11

    assert work_item.work_item_id == "WI-WFC-IMMUTABLE-FINDING-ECHO-FIX-002"
    assert work_item.initial_phase == "implement"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 4
    assert profile.profile_id == "immutable-finding-echo-fix-002"
    assert policy.policy_id == "workflow-coordinator-immutable-finding-echo-fix-002"
    assert tuple(profile.phases) == ("implement", "review", "correct", "rereview")

    # WorkItem.scope is exactly the eleven correction paths (the
    # predecessor's ten plus the one newly authorized Slice-4 test) plus the
    # single fresh durable review artifact -- the smallest authority
    # extension this successor commission needs.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("Makefile", work_item.scope)
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    for path in IMMUTABLE_FINDING_ECHO_FIX_002_CORRECTION_SCOPE:
        assert _within_scope(path, work_item.scope)

    # Exact delta versus predecessor authority: precisely one newly
    # authorized product/test path, and nothing withdrawn.
    assert set(IMMUTABLE_FINDING_ECHO_FIX_002_CORRECTION_SCOPE) - set(
        IMMUTABLE_FINDING_ECHO_FIX_CORRECTION_SCOPE
    ) == {"tests/unit/test_slice4.py"}
    assert set(IMMUTABLE_FINDING_ECHO_FIX_CORRECTION_SCOPE) - set(
        IMMUTABLE_FINDING_ECHO_FIX_002_CORRECTION_SCOPE
    ) == set()

    # Non-scope: files the accepted BUG record explicitly withholds from any
    # correction commission remain outside this successor WorkItem's
    # authority, and so does every predecessor governing file and the
    # predecessor's own review artifact -- this successor neither mutates
    # nor reuses them.
    for withheld in (
        "src/workflow_coordinator/adapter.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/resources/agent_operating_contract_v6.txt",
        "tests/architecture/test_self_development_governance.py",
        "governance/work-items/immutable-finding-echo-fix.yaml",
        "governance/work-items/immutable-finding-echo-fix-task.txt",
        "governance/self-development/immutable-finding-echo-profile.yaml",
        "governance/self-development/immutable-finding-echo-policy.yaml",
        IMMUTABLE_FINDING_ECHO_FIX_REVIEW_ARTIFACT,
    ):
        assert not _within_scope(withheld, work_item.scope)
    assert IMMUTABLE_FINDING_ECHO_FIX_REVIEW_ARTIFACT not in work_item.scope

    # implement/correct share the exact eleven-path correction scope; review
    # and rereview are independently scoped by the profile to the single
    # fresh durable review artifact alone -- no phase's authorized_scope
    # grants both.
    assert (
        implement.authorized_scope
        == correct.authorized_scope
        == IMMUTABLE_FINDING_ECHO_FIX_002_CORRECTION_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        IMMUTABLE_FINDING_ECHO_FIX_002_REVIEW_ARTIFACT,
    )
    assert set(IMMUTABLE_FINDING_ECHO_FIX_002_CORRECTION_SCOPE).isdisjoint(review.authorized_scope)
    assert IMMUTABLE_FINDING_ECHO_FIX_002_REVIEW_ARTIFACT not in implement.authorized_scope
    assert IMMUTABLE_FINDING_ECHO_FIX_002_REVIEW_ARTIFACT not in correct.authorized_scope

    assert durable_artifacts == {"review": (IMMUTABLE_FINDING_ECHO_FIX_002_REVIEW_ARTIFACT,)}
    assert _within_scope(IMMUTABLE_FINDING_ECHO_FIX_002_REVIEW_ARTIFACT, review.authorized_scope)
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "none"
    assert implement.durable_artifact.value == correct.durable_artifact.value == "none"

    # Topology is exactly the predecessor's bounded bootstrap-safe topology:
    # implement -> review -> {accept: complete, correct} -> correct ->
    # rereview -> {accept: complete, correct}. No investigate/planning/
    # supervisor phase was added.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "implement": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert implement.role == correct.role == "worker"

    # Bootstrap PRE-FIX Runtime property preserved: review/rereview remain
    # gate reviews with no Finding-disposition authority at all. The
    # generic provider-free proof that a gate-authority Hop may legally
    # request `changes_required` / `route: correct` with an empty findings
    # tuple already covers this exact review/rereview shape -- see
    # `test_bootstrap_changes_required_correct_route_is_legal_with_empty_finding_set`
    # and its rereview counterpart below, which reproduce this shape
    # directly against the unmodified production reducer.
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_scope_escalation_continuation_design_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Bounded design/review-only commission: preflight-only proof.

    This governing bundle commissions an independently reviewed,
    implementation-ready design for a successor-Run semantic continuation
    mechanism (docs/backlog/BUG-WFC-SCOPE-ESCALATION-CONTINUATION-GAP.adoc).
    It must parse, stay within policy/profile authority, use the bounded
    four-phase bootstrap-safe topology (design -> review ->
    {accept, correct} -> correct -> rereview -> {accept, correct}) with
    max_autonomous_hops == 6, and hold no mutation authority over
    src/workflow_coordinator/**, governance/, workflow-coordinator-runtime,
    or any other file: its only authorized scope is the design artifact and
    its single durable review artifact.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_DESIGN_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "scope-escalation-continuation-design"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_DESIGN_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    design = profile.phases["design"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *SCOPE_ESCALATION_CONTINUATION_DESIGN_SCOPE,
        SCOPE_ESCALATION_CONTINUATION_DESIGN_REVIEW_ARTIFACT,
    )

    assert work_item.work_item_id == "WI-WFC-SCOPE-ESCALATION-CONTINUATION-DESIGN-001"
    assert work_item.initial_phase == "design"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 6
    assert profile.profile_id == "scope-escalation-continuation-design"
    assert policy.policy_id == "workflow-coordinator-scope-escalation-continuation-design"
    assert tuple(profile.phases) == ("design", "review", "correct", "rereview")

    # WorkItem.scope is exactly the design artifact plus its single durable
    # review artifact -- the smallest ceiling this commission needs.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("src/workflow_coordinator", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope(
        "docs/backlog/BUG-WFC-SCOPE-ESCALATION-CONTINUATION-GAP.adoc", work_item.scope
    )
    for path in SCOPE_ESCALATION_CONTINUATION_DESIGN_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over production source, ordinary product tests,
    # or any other governance bundle.
    for withheld in (
        "src/workflow_coordinator/model.py",
        "src/workflow_coordinator/reducer.py",
        "src/workflow_coordinator/operator.py",
        "src/workflow_coordinator/serialization.py",
        "src/workflow_coordinator/persistence.py",
        "tests/architecture/test_self_development_governance.py",
        "tests/unit/test_reducer.py",
        "workflow-coordinator-runtime",
        "governance/work-items/immutable-finding-echo-fix-002.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # design/correct share the exact single-path design scope; review and
    # rereview are independently scoped by the profile to the single durable
    # review artifact alone -- no phase's authorized_scope grants both, and
    # neither ever grants the BUG record itself.
    assert (
        design.authorized_scope
        == correct.authorized_scope
        == SCOPE_ESCALATION_CONTINUATION_DESIGN_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        SCOPE_ESCALATION_CONTINUATION_DESIGN_REVIEW_ARTIFACT,
    )
    assert set(SCOPE_ESCALATION_CONTINUATION_DESIGN_SCOPE).isdisjoint(review.authorized_scope)
    assert SCOPE_ESCALATION_CONTINUATION_DESIGN_REVIEW_ARTIFACT not in design.authorized_scope
    assert SCOPE_ESCALATION_CONTINUATION_DESIGN_REVIEW_ARTIFACT not in correct.authorized_scope

    assert durable_artifacts == {"review": (SCOPE_ESCALATION_CONTINUATION_DESIGN_REVIEW_ARTIFACT,)}
    assert _within_scope(SCOPE_ESCALATION_CONTINUATION_DESIGN_REVIEW_ARTIFACT, review.authorized_scope)
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "none"
    assert design.durable_artifact.value == correct.durable_artifact.value == "none"

    # Topology: design -> review -> {accept: complete, correct} -> correct ->
    # rereview -> {accept: complete, correct}. No investigate/planning/
    # supervisor phase was added, and no phase carries implementation
    # authority over product source.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "design": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert design.role == correct.role == "worker"
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    # Role/persona separation: distinct fresh independent-review persona,
    # never the design/correct persona.
    assert work_item.personas[WorkflowRole.WORKER] == "senior software architect"
    assert (
        work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]
        == "independent adversarial architecture reviewer"
    )
    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_correction_convergence_escape_design_001_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Bounded design/review-only commission: preflight-only proof.

    This governing bundle commissions an independently reviewed,
    implementation-ready design for a generic, domain-neutral
    correction-loop convergence-escape mechanism
    (docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc). It must
    parse, stay within policy/profile authority, use the bounded four-phase
    bootstrap-safe topology (design -> review -> {accept, correct} ->
    correct -> rereview -> {accept, correct}) with max_autonomous_hops == 6,
    and hold no mutation authority over src/workflow_coordinator/**,
    governance/, workflow-coordinator-runtime, the
    workflow-coordinator-run-evidence repository, canonical Project Memory,
    the source BUG itself, any historical FTR-WFC-SCOPE-ESCALATION-
    CONTINUATION review artifact, or any other governance bundle: its only
    authorized scope is the new design artifact and its single dedicated
    durable review artifact.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-design-001"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    design = profile.phases["design"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT,
    )

    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-DESIGN-001"
    assert work_item.initial_phase == "design"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 6
    assert profile.profile_id == "correction-convergence-escape-design-001"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-design-001"
    assert tuple(profile.phases) == ("design", "review", "correct", "rereview")

    # WorkItem.scope is exactly the new design artifact plus its single
    # dedicated durable review artifact -- the smallest ceiling this
    # commission needs.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("src/workflow_coordinator", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope("workflow-coordinator-run-evidence", work_item.scope)
    assert not _within_scope("project_memory/canonical", work_item.scope)
    assert not _within_scope(
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc", work_item.scope
    )
    assert not _within_scope(
        "docs/backlog/BUG-WFC-SCOPE-ESCALATION-CONTINUATION-GAP.adoc", work_item.scope
    )
    assert not _within_scope(
        "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION.adoc", work_item.scope
    )
    for historical_review_artifact in (
        SCOPE_ESCALATION_CONTINUATION_DESIGN_REVIEW_ARTIFACT,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_REVIEW_ARTIFACT,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_REVIEW_ARTIFACT,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_REVIEW_ARTIFACT,
    ):
        assert not _within_scope(historical_review_artifact, work_item.scope)
    for path in CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over production source, ordinary product tests,
    # or any other governance bundle (including this BUG's own motivating
    # predecessor commission).
    for withheld in (
        "src/workflow_coordinator/model.py",
        "src/workflow_coordinator/reducer.py",
        "src/workflow_coordinator/operator.py",
        "src/workflow_coordinator/serialization.py",
        "src/workflow_coordinator/persistence.py",
        "tests/architecture/test_self_development_governance.py",
        "tests/unit/test_reducer.py",
        "workflow-coordinator-runtime",
        "workflow-coordinator-run-evidence",
        "governance/work-items/scope-escalation-continuation-design.yaml",
        "governance/self-development/scope-escalation-continuation-design-profile.yaml",
        "governance/work-items/scope-escalation-continuation-implementation-004.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # design/correct share the exact single-path design scope; review and
    # rereview are independently scoped by the profile to the single
    # dedicated durable review artifact alone -- no phase's authorized_scope
    # grants both, and neither ever grants the source BUG itself.
    assert (
        design.authorized_scope
        == correct.authorized_scope
        == CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_SCOPE).isdisjoint(review.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT not in design.authorized_scope
    assert CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT not in correct.authorized_scope

    assert durable_artifacts == {
        "review": (CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT,),
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT, review.authorized_scope
    )
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    # Both reviewer phases durably mutate exactly the dedicated review
    # artifact -- `rereview` preserves and appends to the same durable
    # history `review` started, rather than being unable to record its own
    # fresh disposition.
    assert review.durable_artifact.value == rereview.durable_artifact.value == "required"
    assert design.durable_artifact.value == correct.durable_artifact.value == "none"
    assert review.authorized_actions == rereview.authorized_actions == ("review", "edit", "commit")
    assert design.authorized_actions == correct.authorized_actions == ("edit", "commit")
    # Neither reviewer phase may edit the FTR design artifact itself.
    assert "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc" not in review.authorized_scope
    assert "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc" not in rereview.authorized_scope

    # Topology: design -> review -> {accept: complete, correct} -> correct ->
    # rereview -> {accept: complete, correct}. No investigate/planning/
    # supervisor/diagnosis phase was added, and no phase carries
    # implementation authority over product source.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "design": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert design.role == correct.role == "worker"
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    # Role/persona separation: distinct fresh independent-review persona,
    # never the design/correct persona. The worker persona is
    # domain-neutral wording -- the mechanism being designed must plausibly
    # apply outside software development, so the commissioned role avoids
    # "software architect" in favor of "workflow-systems architect".
    assert work_item.personas[WorkflowRole.WORKER] == "senior workflow-systems architect"
    assert (
        work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]
        == "independent adversarial architecture reviewer"
    )
    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-run-evidence", policy.work_item_scope_ceiling)
    assert not _within_scope("project_memory/canonical", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    # Admission happened entirely through provider-free preflight: no
    # provider/executable was ever invoked (the placeholder `executable`
    # value is never resolved or run by `_preflight`), and the state root
    # the launch would eventually use was never created by this process.
    assert not state_root.exists()


def test_correction_convergence_escape_design_revision_001_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Bounded Design-Revision-only commission: preflight-only proof.

    This governing bundle commissions an independently reviewed,
    implementation-ready Design Revision of the narrow convergence-fact
    derivation authority surface within the accepted convergence-escape
    architecture, prompted by the empirical Slice-2 blocker S2-IR-001. It
    must parse, stay within policy/profile authority, use the bounded
    four-phase topology (design -> review -> {accept, revise} -> revise ->
    rereview -> {accept, revise}) with max_autonomous_hops == 2, and hold no
    mutation authority over src/workflow_coordinator/**, governance/,
    workflow-coordinator-runtime, the workflow-coordinator-run-evidence
    repository, canonical Project Memory, the original accepted FTR and its
    review artifact, the Slice-2 implementation-review artifact, or any
    other governance bundle: its only authorized scope is the new revision
    artifact and its single dedicated durable review artifact.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-design-revision-001"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_GOVERNING_PATHS,
    ))
    texts = result[4]
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    design = profile.phases["design"]
    review = profile.phases["review"]
    revise = profile.phases["revise"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,
    )

    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-DESIGN-REVISION-001"
    assert work_item.initial_phase == "design"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 2
    assert profile.profile_id == "correction-convergence-escape-design-revision-001"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-design-revision-001"
    assert tuple(profile.phases) == ("design", "review", "revise", "rereview")

    # WorkItem.scope is exactly the new revision artifact plus its single
    # dedicated durable review artifact -- the smallest ceiling this
    # commission needs.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("src/workflow_coordinator", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope("workflow-coordinator-run-evidence", work_item.scope)
    assert not _within_scope("project_memory/canonical", work_item.scope)
    assert not _within_scope(
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc", work_item.scope
    )
    assert not _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT[0], work_item.scope
    )
    assert not _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_REVIEW_ARTIFACT, work_item.scope
    )
    assert not _within_scope(
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc", work_item.scope
    )
    for path in CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over production source, ordinary product tests,
    # or any other governance bundle (including the original accepted design
    # and its review/implementation-review history).
    for withheld in (
        "src/workflow_coordinator/model.py",
        "src/workflow_coordinator/reducer.py",
        "src/workflow_coordinator/persistence.py",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/operator.py",
        "src/workflow_coordinator/serialization.py",
        "tests/architecture/test_self_development_governance.py",
        "tests/unit/test_reducer.py",
        "workflow-coordinator-runtime",
        "workflow-coordinator-run-evidence",
        "governance/work-items/correction-convergence-escape-implementation-s2-001.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s2-001-profile.yaml",
        "governance/work-items/correction-convergence-escape-design-001.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # design/revise share the exact single-path revision scope; review and
    # rereview are independently scoped by the profile to the single
    # dedicated durable review artifact alone -- no phase's authorized_scope
    # grants both, and neither ever grants the original accepted FTR.
    assert (
        design.authorized_scope
        == revise.authorized_scope
        == CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE).isdisjoint(
        review.authorized_scope
    )
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT
        not in design.authorized_scope
    )
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT
        not in revise.authorized_scope
    )

    assert durable_artifacts == {
        "review": (CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,),
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,
        review.authorized_scope,
    )
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,
        rereview.authorized_scope,
    )
    assert review.durable_artifact.value == rereview.durable_artifact.value == "required"
    assert design.durable_artifact.value == revise.durable_artifact.value == "none"
    assert review.authorized_actions == rereview.authorized_actions == ("review", "edit", "commit")
    assert design.authorized_actions == revise.authorized_actions == ("edit", "commit")
    # Neither reviewer phase may edit the revision artifact itself.
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE[0]
        not in review.authorized_scope
    )
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE[0]
        not in rereview.authorized_scope
    )

    # Topology: design -> review -> {accept: complete, revise} -> revise ->
    # rereview -> {accept: complete, revise}. No production-implementation
    # authority, escape routing, or S2-002 commissioning phase was added.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "design": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("revise", "goto", "revise"),
        ),
        "revise": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("revise", "goto", "revise"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert design.role == revise.role == "worker"
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    # The task text pins the exact empirical motivation this commission
    # exists for: S2-001 H002 and its blocker S2-IR-001.
    task_text = texts["task"]
    assert "S2-IR-001" in task_text
    assert "H002" in task_text
    assert "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S2-001" in task_text
    assert "830bdf5536e28a52502bb6faf9172e57093150cb" in task_text

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-run-evidence", policy.work_item_scope_ceiling)
    assert not _within_scope("project_memory/canonical", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    # Admission happened entirely through provider-free preflight: no
    # provider/executable was ever invoked, no Runtime/evidence/state/
    # Project-Memory mutation occurred, and the state root the launch would
    # eventually use was never created by this process.
    assert not state_root.exists()


def test_correction_convergence_escape_design_revision_001_continuation_001_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Bounded successor commission: correct and rereview DR1-IR-001 only.

    This governing bundle authorizes a fresh bounded successor Commission,
    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-DESIGN-REVISION-001-CONTINUATION-001``,
    continuing the frozen predecessor Design-Revision Commission
    (``RUN-5979c331d8ed4cc0a0ab0166eb47f4f8``) that stopped
    ``awaiting_human_budget`` after H002 validly requested ``revise`` against
    stable blocker ``DR1-IR-001``. It must parse, stay within policy/profile
    authority, use a correction/rereview-only topology (``revise ->
    rereview -> {accept, revise}``, with no bootstrap ``design``/``review``
    phase) with ``max_autonomous_hops == 6``, and hold mutation authority
    over exactly the two existing additive Design-Revision artifacts -- never
    the original accepted FTR, its review, the Slice-1/Slice-2
    implementation-review artifacts, the source BUG, the predecessor's own
    governance bundle, production source, ordinary product tests,
    workflow-coordinator-runtime, the evidence repository/run ledger, or
    canonical Project Memory. The task text must pin the predecessor Run,
    H001 design candidate, H002/final HEAD, and stable blocker DR1-IR-001.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_CONTINUATION_001_GOVERNING_PATHS,
    )
    state_root = (
        tmp_path / "external-state" / "correction-convergence-escape-design-revision-001-continuation-001"
    )

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_CONTINUATION_001_GOVERNING_PATHS,
    ))
    texts = result[4]
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    revise = profile.phases["revise"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,
    )

    assert work_item.work_item_id == (
        "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-DESIGN-REVISION-001-CONTINUATION-001"
    )
    # Initial phase is exactly `revise` -- no bootstrap design/review phase
    # is added merely to replay predecessor history.
    assert work_item.initial_phase == "revise"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 6
    assert profile.profile_id == "correction-convergence-escape-design-revision-001-continuation-001"
    assert policy.policy_id == (
        "workflow-coordinator-correction-convergence-escape-design-revision-001-continuation-001"
    )
    # Topology is exactly revise -> rereview -> {accept, revise}; no design
    # or review phase exists in this profile.
    assert tuple(profile.phases) == ("revise", "rereview")

    # Exact two-file WorkItem scope: the same existing Design Revision and
    # its dedicated review artifact the predecessor already used.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("src/workflow_coordinator", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope("workflow-coordinator-run-evidence", work_item.scope)
    assert not _within_scope("project_memory/canonical", work_item.scope)
    assert not _within_scope(
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc", work_item.scope
    )
    assert not _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_001_REVIEW_ARTIFACT[0], work_item.scope
    )
    assert not _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_REVIEW_ARTIFACT, work_item.scope
    )
    assert not _within_scope(
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc", work_item.scope
    )
    for path in CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over production source, ordinary product tests,
    # the predecessor's own governance bundle, or any other governance
    # bundle.
    for withheld in (
        "src/workflow_coordinator/model.py",
        "src/workflow_coordinator/reducer.py",
        "src/workflow_coordinator/persistence.py",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/operator.py",
        "src/workflow_coordinator/serialization.py",
        "tests/architecture/test_self_development_governance.py",
        "tests/unit/test_reducer.py",
        "workflow-coordinator-runtime",
        "workflow-coordinator-run-evidence",
        "governance/work-items/correction-convergence-escape-design-revision-001.yaml",
        "governance/self-development/correction-convergence-escape-design-revision-001-profile.yaml",
        "governance/self-development/correction-convergence-escape-design-revision-001-policy.yaml",
        "governance/work-items/correction-convergence-escape-design-revision-001-task.txt",
        "governance/work-items/correction-convergence-escape-implementation-s2-001.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s2-001-profile.yaml",
        "governance/work-items/correction-convergence-escape-design-001.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # `revise` authorized scope is only the existing Design Revision
    # artifact; `rereview` authorized scope is only the existing review
    # artifact. Neither phase's authorized_scope grants both, and neither
    # ever grants the original accepted FTR.
    assert revise.authorized_scope == CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE
    assert rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_SCOPE).isdisjoint(
        rereview.authorized_scope
    )
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT
        not in revise.authorized_scope
    )

    # Rereview durable artifact is required and exactly the existing review
    # artifact.
    assert durable_artifacts == {
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_DESIGN_REVISION_001_REVIEW_ARTIFACT,
        rereview.authorized_scope,
    )
    assert rereview.durable_artifact.value == "required"
    assert revise.durable_artifact.value == "none"
    assert rereview.authorized_actions == ("review", "edit", "commit")
    assert revise.authorized_actions == ("edit", "commit")

    # Exact topology: no design/review bootstrap phase, no accept-without-
    # DR1-IR-001-disposition shortcut, no S2-002 commissioning phase.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "revise": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("revise", "goto", "revise"),
        ),
    }
    assert rereview.role == "independent_reviewer"
    assert revise.role == "worker"
    assert rereview.review.finding_authority == ()
    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    # The task text pins the exact predecessor lineage and stable blocker
    # this successor continues.
    task_text = texts["task"]
    assert "RUN-5979c331d8ed4cc0a0ab0166eb47f4f8" in task_text
    assert "14830d3306056bb41f65111e941dc436a7bee5dd" in task_text
    assert "8594a5ef93b173bfc920b08bfb3e672952165547" in task_text
    assert "DR1-IR-001" in task_text

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-run-evidence", policy.work_item_scope_ceiling)
    assert not _within_scope("project_memory/canonical", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    # Admission happened entirely through provider-free preflight: no
    # provider/executable was ever invoked, no Runtime/evidence/state/
    # Project-Memory mutation occurred, and the state root the launch would
    # eventually use was never created by this process.
    assert not state_root.exists()


def test_scope_escalation_continuation_implementation_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Bounded implementation commission for the accepted continuation design.

    ``WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-001`` implements the
    successor-Run semantic continuation mechanism accepted by
    ``docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION.adoc``. It must
    parse, stay within policy/profile authority, use the bounded four-phase
    bootstrap-safe topology (``implement -> review -> {accept, correct} ->
    correct -> rereview -> {accept, correct}``) with ``max_autonomous_hops ==
    6``, and hold authority over exactly the design's named production/test
    paths plus one durable implementation-review artifact -- never the design
    FTR, its own review artifact, this WorkItem's own BUG record, or any
    other governance bundle. Unlike every prior bootstrap-safe profile in
    this module, ``rereview`` here also carries ``durable_artifact:
    required`` with ``edit``/``commit`` authority on that same review
    artifact, so a later fresh rereview leaves its own durable acceptance or
    correction record and trusted commit rather than only observing and
    routing.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "scope-escalation-continuation-implementation"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implement = profile.phases["implement"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT,
    )
    assert len(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE) == 12

    assert work_item.work_item_id == "WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-001"
    assert work_item.initial_phase == "implement"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 6
    assert profile.profile_id == "scope-escalation-continuation-implementation"
    assert policy.policy_id == "workflow-coordinator-scope-escalation-continuation-implementation"
    assert tuple(profile.phases) == ("implement", "review", "correct", "rereview")

    # WorkItem.scope is exactly the accepted design's twelve production/test
    # paths plus the single fresh implementation-review artifact.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("Makefile", work_item.scope)
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope(
        "docs/backlog/BUG-WFC-SCOPE-ESCALATION-CONTINUATION-GAP.adoc", work_item.scope
    )
    for path in SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over the accepted design FTR, its own review
    # history, any other governance bundle, or the frozen predecessor Run.
    for withheld in (
        "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION.adoc",
        "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION_review_001.adoc",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/adapter.py",
        "tests/architecture/test_self_development_governance.py",
        "workflow-coordinator-runtime",
        "governance/work-items/scope-escalation-continuation-design.yaml",
        "governance/self-development/scope-escalation-continuation-design-profile.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # implement/correct share the exact twelve-path implementation scope;
    # review and rereview are independently scoped by the profile to the
    # single durable review artifact alone -- no phase's authorized_scope
    # grants both.
    assert (
        implement.authorized_scope
        == correct.authorized_scope
        == SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT,
    )
    assert set(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE).isdisjoint(review.authorized_scope)
    assert SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT not in implement.authorized_scope
    assert SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT not in correct.authorized_scope

    # The Commission-specific acceptance-recording requirement: both review
    # AND rereview require, and are mapped, the same durable artifact, and
    # both carry edit+commit authority on it -- the mechanical property that
    # forces a trusted commit touching that artifact on every review and
    # rereview disposition, not only the first review.
    assert durable_artifacts == {
        "review": (SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT,),
        "rereview": (SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT,),
    }
    assert _within_scope(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT, review.authorized_scope)
    assert _within_scope(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT, rereview.authorized_scope)
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "required"
    assert implement.durable_artifact.value == correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(review.authorized_actions))
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))

    # Topology: implement -> review -> {accept: complete, correct} ->
    # correct -> rereview -> {accept: complete, correct}. No
    # investigate/planning/supervisor phase was added, and no phase carries
    # implementation authority outside the twelve named production/test
    # paths.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "implement": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert implement.role == correct.role == "worker"
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    # Role/persona separation: distinct fresh independent-review persona,
    # never the implement/correct persona.
    assert work_item.personas[WorkflowRole.WORKER] == (
        "senior software engineer implementing an authority- and trust-sensitive "
        "Run state, persistence, and operator change"
    )
    assert work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER] == (
        "independent adversarial reviewer for authority widening, predecessor "
        "mutation, and salvage trust boundaries"
    )
    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_scope_escalation_continuation_implementation_002_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Successor bounded correction/acceptance commission: preflight-only proof.

    ``WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-002`` continues
    predecessor ``WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-001``
    (attempt ``wfc-scope-escalation-continuation-implementation-20261001-01``,
    Run ``RUN-692338c488044e8e9393a1585d118eb8``), which exhausted its 6/6
    autonomous Hop budget at H006 ``changes_required`` with a clean committed
    final HEAD, ``638d2c60a90831f2291b94fac8b25c76cd3fd929``, and no trusted
    commit beyond it. Unlike the predecessor's four-phase
    ``implement -> review -> {accept, correct} -> correct -> rereview ->
    {accept, correct}`` topology, this successor commission begins directly
    at ``correct`` -- the predecessor already has an implementation candidate
    and an independent H006 ``changes_required`` disposition -- and loops
    ``correct -> rereview -> {accept: complete, correct: correct}`` for up to
    ``max_autonomous_hops: 8`` (four bounded correction/rereview cycles). It
    uses its own dedicated ``-002`` WorkItem/profile/policy/task files -- the
    predecessor's governing files remain untouched frozen historical
    evidence -- and its scope ceiling is exactly the same six production
    paths and six ordinary test paths already authorized to the predecessor,
    plus a NEW durable review artifact,
    ``..._implementation_review_002.adoc`` -- never the predecessor's own
    ``..._implementation_review_001.adoc``.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "scope-escalation-continuation-implementation-002"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_REVIEW_ARTIFACT,
    )
    assert len(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE) == 12

    assert work_item.work_item_id == "WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-002"
    assert work_item.initial_phase == "correct"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 8
    assert profile.profile_id == "scope-escalation-continuation-implementation-002"
    assert policy.policy_id == "workflow-coordinator-scope-escalation-continuation-implementation-002"
    assert tuple(profile.phases) == ("correct", "rereview")

    # WorkItem.scope is exactly the predecessor's twelve production/test
    # paths plus a fresh, dedicated implementation-review artifact.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("Makefile", work_item.scope)
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope(
        "docs/backlog/BUG-WFC-SCOPE-ESCALATION-CONTINUATION-GAP.adoc", work_item.scope
    )
    for path in SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over the accepted design FTR, its own review
    # history, the predecessor's own review artifact, any other governance
    # bundle, or either frozen predecessor Run.
    for withheld in (
        "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION.adoc",
        "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION_review_001.adoc",
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT,
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/adapter.py",
        "tests/architecture/test_self_development_governance.py",
        "workflow-coordinator-runtime",
        "governance/work-items/scope-escalation-continuation-implementation.yaml",
        "governance/self-development/scope-escalation-continuation-implementation-profile.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # correct shares the exact twelve-path implementation scope; rereview is
    # independently scoped by the profile to the single, fresh durable
    # review artifact alone -- no phase's authorized_scope grants both.
    assert correct.authorized_scope == SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE
    assert rereview.authorized_scope == (
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_REVIEW_ARTIFACT,
    )
    assert set(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE).isdisjoint(rereview.authorized_scope)
    assert SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_REVIEW_ARTIFACT not in correct.authorized_scope

    # Only rereview carries the durable-artifact acceptance-recording
    # requirement -- correct has no review authority or artifact in this
    # topology.
    assert durable_artifacts == {
        "rereview": (SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert rereview.durable_artifact.value == "required"
    assert correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))
    assert {"edit", "commit"}.issubset(set(correct.authorized_actions))

    # Topology: correct -> rereview -> {accept: complete, correct}. No
    # implement/review preamble, investigate/planning/supervisor phase was
    # added -- the predecessor already produced an implementation candidate
    # and an independent changes_required disposition, so this commission
    # begins directly at correction.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert rereview.role == "independent_reviewer"
    assert correct.role == "worker"
    assert rereview.review.finding_authority == ()

    # Role/persona separation preserved identically from the predecessor
    # commission: distinct fresh independent-review persona, never the
    # correction persona.
    assert work_item.personas[WorkflowRole.WORKER] == (
        "senior software engineer implementing an authority- and trust-sensitive "
        "Run state, persistence, and operator change"
    )
    assert work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER] == (
        "independent adversarial reviewer for authority widening, predecessor "
        "mutation, and salvage trust boundaries"
    )
    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_scope_escalation_continuation_implementation_003_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Second-successor bounded correction/acceptance commission: preflight-only proof.

    ``WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-003`` continues
    predecessor ``WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-002``
    (Run ``RUN-7cac69cf29eb42439f86c2576083f798``), which exhausted its 8/8
    autonomous Hop budget at H008 ``changes_required`` with a clean committed
    final HEAD, ``8a237efaea1a6d0923721bb0480c802c5ebcec67``, and no trusted
    commit beyond it. H008 confirms the shared-salvage, publication, tamper,
    and separate-checkout counterexamples found through H007 are closed and
    requires no scope expansion; its sole remaining blocking group, H008-R1,
    is that the mandatory acceptance matrix is still materially incomplete.
    This commission reuses the same two-phase
    ``correct -> rereview -> {accept: complete, correct: correct}`` topology
    as its predecessor, bounded this time to ``max_autonomous_hops: 6``
    (three bounded correction/rereview cycles -- fewer than the predecessor's
    four, per the maintainer's explicit new budget). It uses its own
    dedicated ``-003`` WorkItem/profile/policy/task files -- both
    predecessors' governing files remain untouched frozen historical
    evidence -- and its scope ceiling is exactly the same six production
    paths and six ordinary test paths already authorized to both
    predecessors, plus a NEW durable review artifact,
    ``..._implementation_review_003.adoc`` -- never either predecessor's own
    ``..._implementation_review_001.adoc`` or
    ``..._implementation_review_002.adoc``. This commission also withholds
    authority over the unrelated, newly recorded
    ``BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc``, which documents a
    correction-loop convergence pattern across both predecessor Runs but is
    explicitly not this commission's subject.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "scope-escalation-continuation-implementation-003"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_REVIEW_ARTIFACT,
    )
    assert len(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE) == 12

    assert work_item.work_item_id == "WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-003"
    assert work_item.initial_phase == "correct"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 6
    assert profile.profile_id == "scope-escalation-continuation-implementation-003"
    assert policy.policy_id == "workflow-coordinator-scope-escalation-continuation-implementation-003"
    assert tuple(profile.phases) == ("correct", "rereview")

    # WorkItem.scope is exactly both predecessors' twelve production/test
    # paths plus a fresh, dedicated implementation-review artifact.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("Makefile", work_item.scope)
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope(
        "docs/backlog/BUG-WFC-SCOPE-ESCALATION-CONTINUATION-GAP.adoc", work_item.scope
    )
    assert not _within_scope(
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc", work_item.scope
    )
    for path in SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over the accepted design FTR, its own review
    # history, either predecessor's own review artifact, the unrelated
    # carryover BUG, any other governance bundle, or any frozen predecessor
    # Run.
    for withheld in (
        "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION.adoc",
        "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION_review_001.adoc",
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_REVIEW_ARTIFACT,
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/adapter.py",
        "tests/architecture/test_self_development_governance.py",
        "workflow-coordinator-runtime",
        "governance/work-items/scope-escalation-continuation-implementation.yaml",
        "governance/self-development/scope-escalation-continuation-implementation-profile.yaml",
        "governance/work-items/scope-escalation-continuation-implementation-002.yaml",
        "governance/self-development/scope-escalation-continuation-implementation-002-profile.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # correct shares the exact twelve-path implementation scope; rereview is
    # independently scoped by the profile to the single, fresh durable
    # review artifact alone -- no phase's authorized_scope grants both.
    assert correct.authorized_scope == SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE
    assert rereview.authorized_scope == (
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_REVIEW_ARTIFACT,
    )
    assert set(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE).isdisjoint(rereview.authorized_scope)
    assert SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_REVIEW_ARTIFACT not in correct.authorized_scope

    # Only rereview carries the durable-artifact acceptance-recording
    # requirement -- correct has no review authority or artifact in this
    # topology.
    assert durable_artifacts == {
        "rereview": (SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert rereview.durable_artifact.value == "required"
    assert correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))
    assert {"edit", "commit"}.issubset(set(correct.authorized_actions))

    # Topology: correct -> rereview -> {accept: complete, correct}, bounded
    # to six Hops (three correction/rereview cycles) -- fewer than the
    # predecessor commission's eight, per the maintainer's explicit new
    # budget. No implement/review preamble or planning/supervisor phase was
    # added.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert rereview.role == "independent_reviewer"
    assert correct.role == "worker"
    assert rereview.review.finding_authority == ()

    # Role/persona separation preserved identically from both predecessor
    # commissions: distinct fresh independent-review persona, never the
    # correction persona.
    assert work_item.personas[WorkflowRole.WORKER] == (
        "senior software engineer implementing an authority- and trust-sensitive "
        "Run state, persistence, and operator change"
    )
    assert work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER] == (
        "independent adversarial reviewer for authority widening, predecessor "
        "mutation, and salvage trust boundaries"
    )
    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_scope_escalation_continuation_implementation_004_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Third-successor bounded correction/acceptance commission: preflight-only proof.

    ``WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-004`` continues
    predecessor ``WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-003``
    (Run ``RUN-c30fec4573134508975b5342ec5b2c28``), which exhausted its 6/6
    autonomous Hop budget at H006 ``changes_required`` with a clean committed
    final HEAD, ``f189c4c07475d3bcfed938026563acbeabe79005``, and no trusted
    commit beyond it. H006 confirms Run wire compatibility/legacy eligibility,
    the layered-stop regression, salvage containment/rendering, and clean
    successor/independent acceptance are closed and requires no scope
    expansion; its three remaining blocking groups -- H006-R1 (split here into
    the stable identifiers R1A, a false-failure-after-publication defect, and
    R1B, the complete public two-artifact publication matrix), H006-R2 (R2,
    frozen predecessor identity at preparation), and H006-R3 (R3, status/
    launch next-action output) -- are this commission's complete correction
    obligation. This commission reuses the same two-phase
    ``correct -> rereview -> {accept: complete, correct: correct}`` topology
    as all three predecessors, again bounded to ``max_autonomous_hops: 6``
    (three bounded correction/rereview cycles). It uses its own dedicated
    ``-004`` WorkItem/profile/policy/task files -- all three predecessors'
    governing files remain untouched frozen historical evidence -- and its
    scope ceiling is exactly the same six production paths and six ordinary
    test paths already authorized to all three predecessors, plus a NEW
    durable review artifact, ``..._implementation_review_004.adoc`` -- never
    any predecessor's own ``..._implementation_review_001.adoc``,
    ``..._implementation_review_002.adoc``, or
    ``..._implementation_review_003.adoc``. This commission also withholds
    authority over the unrelated ``BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc``,
    which documents a correction-loop convergence pattern across the first two
    predecessor Runs but is explicitly not this commission's subject.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "scope-escalation-continuation-implementation-004"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_REVIEW_ARTIFACT,
    )
    assert len(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE) == 12

    assert work_item.work_item_id == "WI-WFC-SCOPE-ESCALATION-CONTINUATION-IMPLEMENTATION-004"
    assert work_item.initial_phase == "correct"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 6
    assert profile.profile_id == "scope-escalation-continuation-implementation-004"
    assert policy.policy_id == "workflow-coordinator-scope-escalation-continuation-implementation-004"
    assert tuple(profile.phases) == ("correct", "rereview")

    # WorkItem.scope is exactly all three predecessors' twelve production/test
    # paths plus a fresh, dedicated implementation-review artifact.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("Makefile", work_item.scope)
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope(
        "docs/backlog/BUG-WFC-SCOPE-ESCALATION-CONTINUATION-GAP.adoc", work_item.scope
    )
    assert not _within_scope(
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc", work_item.scope
    )
    for path in SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over the accepted design FTR, its own review
    # history, any predecessor's own review artifact, the unrelated
    # carryover BUG, any other governance bundle, or any frozen predecessor
    # Run.
    for withheld in (
        "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION.adoc",
        "docs/backlog/FTR-WFC-SCOPE-ESCALATION-CONTINUATION_review_001.adoc",
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_REVIEW_ARTIFACT,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_002_REVIEW_ARTIFACT,
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_003_REVIEW_ARTIFACT,
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/adapter.py",
        "tests/architecture/test_self_development_governance.py",
        "workflow-coordinator-runtime",
        "governance/work-items/scope-escalation-continuation-implementation.yaml",
        "governance/self-development/scope-escalation-continuation-implementation-profile.yaml",
        "governance/work-items/scope-escalation-continuation-implementation-002.yaml",
        "governance/self-development/scope-escalation-continuation-implementation-002-profile.yaml",
        "governance/work-items/scope-escalation-continuation-implementation-003.yaml",
        "governance/self-development/scope-escalation-continuation-implementation-003-profile.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # correct shares the exact twelve-path implementation scope; rereview is
    # independently scoped by the profile to the single, fresh durable
    # review artifact alone -- no phase's authorized_scope grants both.
    assert correct.authorized_scope == SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE
    assert rereview.authorized_scope == (
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_REVIEW_ARTIFACT,
    )
    assert set(SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_SCOPE).isdisjoint(rereview.authorized_scope)
    assert SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_REVIEW_ARTIFACT not in correct.authorized_scope

    # Only rereview carries the durable-artifact acceptance-recording
    # requirement -- correct has no review authority or artifact in this
    # topology.
    assert durable_artifacts == {
        "rereview": (SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        SCOPE_ESCALATION_CONTINUATION_IMPLEMENTATION_004_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert rereview.durable_artifact.value == "required"
    assert correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))
    assert {"edit", "commit"}.issubset(set(correct.authorized_actions))

    # Topology: correct -> rereview -> {accept: complete, correct}, bounded
    # to six Hops (three correction/rereview cycles), matching the immediate
    # predecessor commission's budget. No implement/review preamble or
    # planning/supervisor phase was added.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert rereview.role == "independent_reviewer"
    assert correct.role == "worker"
    assert rereview.review.finding_authority == ()

    # Role/persona separation preserved identically from all three predecessor
    # commissions: distinct fresh independent-review persona, never the
    # correction persona.
    assert work_item.personas[WorkflowRole.WORKER] == (
        "senior software engineer implementing an authority- and trust-sensitive "
        "Run state, persistence, and operator change"
    )
    assert work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER] == (
        "independent adversarial reviewer for authority widening, predecessor "
        "mutation, and salvage trust boundaries"
    )
    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_correction_convergence_escape_implementation_s1_001_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Slice-1-only implementation commission for the accepted convergence-escape design.

    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-001`` implements
    only Slice 1 ("Structured obligations and wire") of
    ``docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc``, accepted by
    fresh independent rereview with zero remaining findings at H004
    (``4edcea0e341e9c8669378fa299104d66aed2851f``). It must parse, stay within
    policy/profile authority, use the same bounded four-phase bootstrap-safe
    topology as the first scope-escalation-continuation implementation
    commission (``implement -> review -> {accept, correct} -> correct ->
    rereview -> {accept, correct}``) with ``max_autonomous_hops == 6``, and
    hold authority over exactly six production paths and six ordinary test/
    fixture paths plus one dedicated durable implementation-review artifact --
    never the accepted design FTR, its own review artifact, the source BUG,
    ``operator.py``/``vertical.py``/``prompt.py``, or any other governance
    bundle. This commission is deliberately narrower than the accepted
    design's full closed path union (which also permits ``operator.py`` and
    several Slice-2/3/4-only test paths): Slice 1 needs none of those.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-implementation-s1-001"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implement = profile.phases["implement"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT,
    )
    assert len(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_SCOPE) == 12

    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-001"
    assert work_item.initial_phase == "implement"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 6
    assert profile.profile_id == "correction-convergence-escape-implementation-s1-001"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-implementation-s1-001"
    assert tuple(profile.phases) == ("implement", "review", "correct", "rereview")

    # WorkItem.scope is exactly Slice 1's six production paths, six ordinary
    # test/fixture paths, and its single dedicated implementation-review
    # artifact -- never the design FTR, operator.py, vertical.py, prompt.py,
    # or any Slice-2/3/4-only path (e.g. a curriculum fixture).
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("Makefile", work_item.scope)
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope(
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc", work_item.scope
    )
    for path in CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over the accepted design FTR, its own review
    # history, the unrelated source BUG, any other governance bundle, or the
    # files reserved for later slices.
    for withheld in (
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_review_001.adoc",
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/operator.py",
        "tests/unit/test_operator.py",
        "tests/unit/test_slice3_vertical.py",
        "tests/unit/test_slice5b.py",
        "tests/fixtures/convergence_curriculum.yaml",
        "tests/architecture/test_self_development_governance.py",
        "workflow-coordinator-runtime",
        "governance/work-items/correction-convergence-escape-design-001.yaml",
        "governance/self-development/correction-convergence-escape-design-001-profile.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # implement/correct share the exact twelve-path implementation scope;
    # review and rereview are independently scoped by the profile to the
    # single durable review artifact alone -- no phase's authorized_scope
    # grants both.
    assert (
        implement.authorized_scope
        == correct.authorized_scope
        == CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_SCOPE).isdisjoint(review.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT not in implement.authorized_scope
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT not in correct.authorized_scope

    # The Commission-specific acceptance-recording requirement: both review
    # AND rereview require, and are mapped, the same durable artifact, and
    # both carry edit+commit authority on it.
    assert durable_artifacts == {
        "review": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT,),
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT, review.authorized_scope
    )
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_001_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "required"
    assert implement.durable_artifact.value == correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(review.authorized_actions))
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))

    # Topology: implement -> review -> {accept: complete, correct} ->
    # correct -> rereview -> {accept: complete, correct}. No
    # diagnosis/reframe/strategy-gate phase was added -- those remain later
    # slices -- and no phase carries implementation authority outside the
    # twelve named production/test paths.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "implement": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert implement.role == correct.role == "worker"
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    # Role/persona separation: distinct fresh independent-review persona,
    # never the implement/correct persona.
    assert work_item.personas[WorkflowRole.WORKER] == (
        "senior workflow-systems/Python architecture engineer extending the Finding "
        "ledger with a structured, wire-versioned correction-obligation and "
        "coverage/assessment extension while preserving exact legacy compatibility"
    )
    assert work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER] == (
        "independent adversarial reviewer for Finding-authority integrity, "
        "untracked-blocker creation, successor-identity correctness, legacy wire/"
        "round-trip compatibility, and domain-neutral scope discipline"
    )
    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_correction_convergence_escape_implementation_s1_002_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Fresh follow-up Slice-1 commission continuing the interrupted S1-001 Run.

    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-002`` is a fresh
    forward-continuation commission from current committed repository state
    (HEAD ``55e4c0b4807b260ceb1cd060f635deefc1e46953``, the maintainer-preserved,
    untrusted WIP candidate from predecessor Run
    ``RUN-a28208a38013403d9b7e0a89ce1e3531`` H001, which reached its configured
    900-second timeout with no trusted commit). It reuses the exact same
    bounded four-phase bootstrap-safe topology, scope, and
    ``max_autonomous_hops == 6`` as predecessor WI-WFC-CORRECTION-CONVERGENCE-
    ESCAPE-IMPLEMENTATION-S1-001, and deliberately reuses that predecessor's
    originally designated (but never written-to) durable review artifact path
    ``docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_implementation_review_001.adoc``
    -- predecessor S1-001 never reached its review phase, so no stale review
    content exists under that name to protect with a separately numbered
    artifact. This is distinguishable from the scope-escalation-continuation
    precedent (WI-...-IMPLEMENTATION-002), which created a fresh
    ``_implementation_review_002.adoc`` specifically because its predecessor's
    ``_implementation_review_001.adoc`` already held real H002/H004/H006
    review history that had to remain frozen and separate.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-implementation-s1-002"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implement = profile.phases["implement"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT,
    )
    assert len(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_SCOPE) == 12

    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-002"
    assert work_item.initial_phase == "implement"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 6
    assert profile.profile_id == "correction-convergence-escape-implementation-s1-002"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-implementation-s1-002"
    assert tuple(profile.phases) == ("implement", "review", "correct", "rereview")

    # Explicit lineage: the authority source names the predecessor WorkItem,
    # the predecessor's failed Run, the original semantic baseline, and the
    # preserved WIP commit, and states the WIP commit confers no trust.
    assert "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-001" in work_item.authority_source
    assert "RUN-a28208a38013403d9b7e0a89ce1e3531" in work_item.authority_source
    assert "272e2b27f4c75f5fb12cf5287d1b1940caeba5dc" in work_item.authority_source
    assert "55e4c0b4807b260ceb1cd060f635deefc1e46953" in work_item.authority_source
    assert "untrusted" in work_item.authority_source

    # WorkItem.scope is exactly the same Slice-1 twelve production/test paths
    # as S1-001, plus the one reused durable implementation-review artifact --
    # never the design FTR, operator.py, vertical.py, prompt.py, or any
    # Slice-2/3/4-only path (e.g. a curriculum fixture).
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert not _within_scope("Makefile", work_item.scope)
    assert not _within_scope("src", work_item.scope)
    assert not _within_scope("governance", work_item.scope)
    assert not _within_scope("workflow-coordinator-runtime", work_item.scope)
    assert not _within_scope(
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc", work_item.scope
    )
    for path in CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_SCOPE:
        assert _within_scope(path, work_item.scope)

    # No mutation authority over the accepted design FTR, its own review
    # history, the unrelated source BUG, any other governance bundle, the
    # predecessor's own governing bundle, or the files reserved for later
    # slices.
    for withheld in (
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_review_001.adoc",
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/operator.py",
        "tests/unit/test_operator.py",
        "tests/unit/test_slice3_vertical.py",
        "tests/unit/test_slice5b.py",
        "tests/fixtures/convergence_curriculum.yaml",
        "tests/architecture/test_self_development_governance.py",
        "workflow-coordinator-runtime",
        "governance/work-items/correction-convergence-escape-implementation-s1-001.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s1-001-profile.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s1-001-policy.yaml",
        "governance/work-items/correction-convergence-escape-design-001.yaml",
        "governance/self-development/correction-convergence-escape-design-001-profile.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # implement/correct share the exact twelve-path implementation scope;
    # review and rereview are independently scoped by the profile to the
    # single reused durable review artifact alone -- no phase's
    # authorized_scope grants both.
    assert (
        implement.authorized_scope
        == correct.authorized_scope
        == CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_SCOPE).isdisjoint(review.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT not in implement.authorized_scope
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT not in correct.authorized_scope

    # Load-bearing complete-review-baseline rule: both review and rereview
    # purposes must name the original semantic baseline and explicitly state
    # that the WIP commit confers no trust and is not a sufficient review
    # boundary by itself.
    assert "272e2b27f4c75f5fb12cf5287d1b1940caeba5dc" in review.review.purpose
    assert "55e4c0b4807b260ceb1cd060f635deefc1e46953" in review.review.purpose
    assert "no trust" in review.review.purpose
    assert "272e2b27f4c75f5fb12cf5287d1b1940caeba5dc" in rereview.review.purpose
    assert "55e4c0b4807b260ceb1cd060f635deefc1e46953" in rereview.review.purpose

    # The Commission-specific acceptance-recording requirement: both review
    # AND rereview require, and are mapped, the same reused durable artifact,
    # and both carry edit+commit authority on it.
    assert durable_artifacts == {
        "review": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT,),
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT, review.authorized_scope
    )
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_002_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "required"
    assert implement.durable_artifact.value == correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(review.authorized_actions))
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))

    # Topology: implement -> review -> {accept: complete, correct} ->
    # correct -> rereview -> {accept: complete, correct}. No
    # diagnosis/reframe/strategy-gate phase was added -- those remain later
    # slices -- and no phase carries implementation authority outside the
    # twelve named production/test paths.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "implement": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert implement.role == correct.role == "worker"
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_correction_convergence_escape_implementation_s1_003_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Fresh correct-only continuation closing S1-002's sole remaining blocker.

    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-003`` continues
    predecessor ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-002``
    (Run ``RUN-36e0ca5ca7e245c8af90260ec8bd3ba9``), which exhausted its 6/6
    autonomous Hop budget at H006 ``changes_required`` with a clean committed
    final HEAD, ``3aaedaa9a4eb9dd8ebd0f16b44360fc29ee19864``, and exactly one
    remaining open blocking implementation-review identifier, ``S1-IR-002``
    ("New-counterexample evidence must be absent from the correlated
    correction history"); ``S1-IR-001`` is already closed. Unlike S1-002 (a
    four-phase ``implement -> review -> correct -> rereview`` continuation of
    an interrupted, never-reviewed implementation attempt), this commission's
    implementation already exists and has already undergone independent
    review -- it uses the bounded two-phase
    ``correct -> rereview -> {accept: complete, correct: correct}`` topology,
    starting directly at `correct`, bounded to ``max_autonomous_hops: 4`` (at
    most two correction/rereview cycles, not six). Its correction scope
    ceiling is exactly the two files named by S1-IR-002's required
    correction -- ``reducer.py`` and ``test_reducer.py`` -- not the full
    twelve-path Slice-1 implementation scope used by S1-001/S1-002. It reuses
    (and appends to) the SAME durable review artifact as S1-002,
    ``..._implementation_review_001.adoc``, rather than creating a ``_002``
    artifact, because H006 explicitly requires the fresh rereview to dispose
    S1-IR-002 in a separately appended section of the complete durable
    Slice-1 implementation-review history, preserving every prior section.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-implementation-s1-003"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_REVIEW_ARTIFACT,
    )
    assert len(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_SCOPE) == 2

    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-003"
    assert work_item.initial_phase == "correct"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 4
    assert profile.profile_id == "correction-convergence-escape-implementation-s1-003"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-implementation-s1-003"
    assert tuple(profile.phases) == ("correct", "rereview")

    # Explicit frozen lineage: the predecessor WorkItem, its Run, and its
    # final head are captured, S1-IR-002 is named as the complete remaining
    # obligation, S1-IR-001 is recorded as already closed (not reopened), and
    # the original semantic baseline is retained.
    assert "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-002" in work_item.authority_source
    assert "RUN-36e0ca5ca7e245c8af90260ec8bd3ba9" in work_item.authority_source
    assert "3aaedaa9a4eb9dd8ebd0f16b44360fc29ee19864" in work_item.authority_source
    assert "272e2b27f4c75f5fb12cf5287d1b1940caeba5dc" in work_item.authority_source
    assert "S1-IR-002" in work_item.authority_source
    assert "S1-IR-001" in work_item.authority_source
    assert "closed" in work_item.authority_source

    # WorkItem.scope is exactly the two reducer/test paths S1-IR-002's
    # correction is bounded to, plus the reused durable review artifact --
    # never model.py, serialization.py, adapter.py, codex_adapter.py,
    # persistence.py, tests/support.py, vertical.py, prompt.py, operator.py,
    # the design FTR, the source BUG, or any governance path.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    for withheld in (
        "Makefile",
        "src",
        "governance",
        "workflow-coordinator-runtime",
        "src/workflow_coordinator/model.py",
        "src/workflow_coordinator/serialization.py",
        "src/workflow_coordinator/adapter.py",
        "src/workflow_coordinator/codex_adapter.py",
        "src/workflow_coordinator/persistence.py",
        "tests/support.py",
        "tests/unit/test_persistence.py",
        "tests/unit/test_codex_adapter_conformance.py",
        "tests/architecture/test_replays.py",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/operator.py",
        "tests/unit/test_operator.py",
        "tests/unit/test_slice3_vertical.py",
        "tests/unit/test_slice5b.py",
        "tests/fixtures/convergence_curriculum.yaml",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_review_001.adoc",
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "tests/architecture/test_self_development_governance.py",
        "governance/work-items/correction-convergence-escape-implementation-s1-002.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s1-002-profile.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s1-002-policy.yaml",
        "governance/work-items/correction-convergence-escape-implementation-s1-001.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s1-001-profile.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)
    for path in CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_SCOPE:
        assert _within_scope(path, work_item.scope)

    # `correct` is scoped to exactly reducer.py + test_reducer.py; `rereview`
    # is independently scoped by the profile to the single reused durable
    # review artifact alone -- no phase's authorized_scope grants both.
    assert correct.authorized_scope == CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_SCOPE
    assert rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_SCOPE).isdisjoint(rereview.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_REVIEW_ARTIFACT not in correct.authorized_scope

    # Only rereview carries the durable-artifact acceptance-recording
    # requirement; the reused artifact is the SAME path as S1-002's, not a
    # fresh `_002`/`_003`-suffixed review artifact.
    assert durable_artifacts == {
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_REVIEW_ARTIFACT,),
    }
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_REVIEW_ARTIFACT
        == "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_implementation_review_001.adoc"
    )
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S1_003_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert rereview.durable_artifact.value == "required"
    assert correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))
    assert {"edit", "commit"}.issubset(set(correct.authorized_actions))

    # Load-bearing complete-review-baseline rule: rereview's purpose names the
    # original semantic baseline and explicitly excludes narrower deltas
    # (the S1-003 correction delta, the S1-002-final-head-to-candidate delta,
    # and the post-WIP delta) as insufficient review scope.
    assert "272e2b27f4c75f5fb12cf5287d1b1940caeba5dc" in rereview.review.purpose
    assert "S1-IR-002" in rereview.review.purpose
    assert "S1-IR-001" in rereview.review.purpose

    # Topology: correct -> rereview -> {accept: complete, correct}, bounded
    # to four Hops (two correction/rereview cycles). No implement/review
    # preamble, diagnosis/reframe/strategy-gate phase, or Slice-2/3/4/5
    # implementation path is authorized.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert rereview.role == "independent_reviewer"
    assert correct.role == "worker"
    assert rereview.review.finding_authority == ()

    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_correction_convergence_escape_implementation_s2_001_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Slice-2-only implementation commission for the accepted convergence-escape design.

    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S2-001`` implements
    only Slice 2 ("Derived convergence facts") of
    ``docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc``, treating
    Slice 1 ("Structured obligations and wire") as an independently accepted
    prerequisite -- WorkItem
    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-003``, Run
    ``RUN-786b30f27aef437da5c605b71ee7d0fe``, Hop H002, accepted candidate
    ``33d6c0092be988f759bb8dcc8d0e8ee56bcef372``, durable acceptance record at
    commit ``83cccb98bb800479645d87051fb1cd66b3a4ac7f`` with zero blocking
    implementation-review findings remaining. It uses the same bounded
    four-phase bootstrap-safe topology as the first Slice-1 implementation
    commission (``implement -> review -> {accept, correct} -> correct ->
    rereview -> {accept, correct}``), deliberately widened to
    ``max_autonomous_hops: 10`` (several correction/rereview cycles), and
    holds authority over exactly three production paths and four ordinary
    test/fixture paths plus the SAME reused durable implementation-review
    artifact as every Slice-1 commission -- never the accepted design FTR,
    its own review artifact, the source BUG, serialization.py/adapter.py/
    codex_adapter.py/vertical.py/prompt.py/operator.py, any Slice-3/4-only
    fixture, or any other governance bundle. This commission is deliberately
    narrower than the accepted design's full closed path union and than the
    twelve-path Slice-1 scope: Slice 2 is a pure derivation over already-
    persisted Slice-1 wire plus a checkpoint-projection extension, and needs
    neither the Slice-1 wire/adapter paths nor any profile-v3/Slice-4 path.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-implementation-s2-001"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implement = profile.phases["implement"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT,
    )
    assert len(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_SCOPE) == 7

    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S2-001"
    assert work_item.initial_phase == "implement"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 10
    assert profile.profile_id == "correction-convergence-escape-implementation-s2-001"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-implementation-s2-001"
    assert tuple(profile.phases) == ("implement", "review", "correct", "rereview")

    # Explicit frozen Slice-1 prerequisite lineage: the accepted baseline
    # commit, its WorkItem/Run/Hop identity, and the closed zero-blocker
    # outcome are captured; Slice 1 is treated as accepted authority, not
    # reopened.
    assert "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-003" in work_item.authority_source
    assert "RUN-786b30f27aef437da5c605b71ee7d0fe" in work_item.authority_source
    assert "33d6c0092be988f759bb8dcc8d0e8ee56bcef372" in work_item.authority_source
    assert "83cccb98bb800479645d87051fb1cd66b3a4ac7f" in work_item.authority_source
    assert "S1-IR-001" in work_item.authority_source
    assert "S1-IR-002" in work_item.authority_source
    assert "closed" in work_item.authority_source
    assert "prerequisite" in work_item.authority_source

    # WorkItem.scope is exactly Slice 2's three production paths, four
    # ordinary test/fixture paths, and the reused durable implementation-
    # review artifact -- never the design FTR, its own review artifact, the
    # source BUG, serialization.py/adapter.py/codex_adapter.py/vertical.py/
    # prompt.py/operator.py, or any Slice-3/4-only path (e.g. a curriculum
    # fixture or a profile-v3 governance bundle).
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    for withheld in (
        "Makefile",
        "src",
        "governance",
        "workflow-coordinator-runtime",
        "src/workflow_coordinator/serialization.py",
        "src/workflow_coordinator/adapter.py",
        "src/workflow_coordinator/codex_adapter.py",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/operator.py",
        "tests/unit/test_serialization.py",
        "tests/unit/test_codex_adapter_conformance.py",
        "tests/unit/test_operator.py",
        "tests/unit/test_slice3_vertical.py",
        "tests/unit/test_slice5b.py",
        "tests/fixtures/convergence_curriculum.yaml",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_review_001.adoc",
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "tests/architecture/test_self_development_governance.py",
        "governance/work-items/correction-convergence-escape-implementation-s1-003.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s1-003-profile.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s1-003-policy.yaml",
        "governance/work-items/correction-convergence-escape-design-001.yaml",
        "governance/self-development/correction-convergence-escape-design-001-profile.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)
    for path in CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_SCOPE:
        assert _within_scope(path, work_item.scope)

    # implement/correct share the exact seven-path Slice-2 implementation
    # scope; review and rereview are independently scoped by the profile to
    # the single reused durable review artifact alone -- no phase's
    # authorized_scope grants both.
    assert (
        implement.authorized_scope
        == correct.authorized_scope
        == CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_SCOPE).isdisjoint(review.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT not in implement.authorized_scope
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT not in correct.authorized_scope

    # The reused artifact is the SAME path as every Slice-1 commission's, not
    # a fresh Slice-2-suffixed review artifact; both review AND rereview
    # require, and are mapped to, it, with edit+commit authority.
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT
        == "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_implementation_review_001.adoc"
    )
    assert durable_artifacts == {
        "review": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT,),
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT, review.authorized_scope
    )
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_001_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "required"
    assert implement.durable_artifact.value == correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(review.authorized_actions))
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))

    # Topology: implement -> review -> {accept: complete, correct} ->
    # correct -> rereview -> {accept: complete, correct}. No
    # diagnosis/reframe/strategy-gate phase was added -- those remain later
    # slices -- and no phase carries implementation authority outside the
    # seven named production/test paths. max_autonomous_hops is 10, not 6,
    # deliberately permitting several correction/rereview cycles.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "implement": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert implement.role == correct.role == "worker"
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    assert not state_root.exists()


def test_correction_convergence_escape_implementation_s2_002_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Fresh successor Slice-2 implementation commission under Design Revision 001.

    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S2-002`` is a fresh
    successor to the unaccepted ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S2-001``
    attempt (factual candidate ``830bdf5536e28a52502bb6faf9172e57093150cb``,
    stable blocker ``S2-IR-001``), implementing revised Slice 2 under the
    independently accepted Design Revision 001
    (``docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001.adoc``,
    accepted at commit ``f08c8cb61480ec5b9e2529eebc5ae44a0463bc9e`` with stable
    design blocker ``DR1-IR-001`` closed) rather than S2-001's superseded
    Run-only derivation contract. It uses the same bounded four-phase
    bootstrap-safe topology as every prior Slice-1/Slice-2 implementation
    commission (``implement -> review -> {accept, correct} -> correct ->
    rereview -> {accept, correct}``) with ``max_autonomous_hops: 10``, and
    holds authority over exactly seven production paths and seven ordinary
    test/fixture paths -- wider than S2-001's three production paths because
    Design Revision 001's own review established that live/replay
    equivalence and crash-safe pair publication cross these additional
    seams -- plus the SAME reused durable implementation-review artifact as
    every prior Slice-1/Slice-2 commission. It never holds authority over the
    accepted design FTR, the Design Revision and its own review artifact, the
    source BUG, S2-001's frozen governance bundle, ``evidence.py``/
    ``adapter.py``/``codex_adapter.py``/``prompt.py``, any Slice-3/4-only
    fixture, ``workflow-coordinator-runtime``, the evidence repository, the
    run ledger, or Project Memory. No phase carries push or Runtime-promotion
    authority, and the admission succeeds entirely through provider-free
    preflight: no provider is invoked and no state root is created.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-implementation-s2-002"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_GOVERNING_PATHS,
    ))
    texts = result[4]
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implement = profile.phases["implement"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_REVIEW_ARTIFACT,
    )
    assert len(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_SCOPE) == 14

    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S2-002"
    assert work_item.initial_phase == "implement"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 10
    assert profile.profile_id == "correction-convergence-escape-implementation-s2-002"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-implementation-s2-002"
    assert tuple(profile.phases) == ("implement", "review", "correct", "rereview")

    # Explicit frozen authority lineage: the unaccepted S2-001 factual
    # candidate and its stable blocker, the accepted Design Revision's final
    # acceptance commit and its closed stable blocker, and the Slice-1
    # prerequisite lineage are all pinned -- never silently dropped.
    assert "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S1-003" in work_item.authority_source
    assert "RUN-786b30f27aef437da5c605b71ee7d0fe" in work_item.authority_source
    assert "33d6c0092be988f759bb8dcc8d0e8ee56bcef372" in work_item.authority_source
    assert "83cccb98bb800479645d87051fb1cd66b3a4ac7f" in work_item.authority_source
    assert "S1-IR-001" in work_item.authority_source
    assert "S1-IR-002" in work_item.authority_source
    assert "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S2-001" in work_item.authority_source
    assert "830bdf5536e28a52502bb6faf9172e57093150cb" in work_item.authority_source
    assert "S2-IR-001" in work_item.authority_source
    assert "f08c8cb61480ec5b9e2529eebc5ae44a0463bc9e" in work_item.authority_source
    assert "DR1-IR-001" in work_item.authority_source
    assert "Family B" in work_item.authority_source
    assert "closed" in work_item.authority_source

    # WorkItem.scope is exactly Slice 2's seven production paths, seven
    # ordinary test/fixture paths, and the reused durable implementation-
    # review artifact -- never the design FTR, the Design Revision or its
    # own review artifact, the source BUG, S2-001's frozen governance
    # bundle, evidence.py/adapter.py/codex_adapter.py/prompt.py, or any
    # Slice-3/4-only path.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    for withheld in (
        "Makefile",
        "src",
        "governance",
        "workflow-coordinator-runtime",
        "workflow-coordinator-run-evidence",
        "project_memory/canonical",
        "src/workflow_coordinator/evidence.py",
        "src/workflow_coordinator/adapter.py",
        "src/workflow_coordinator/codex_adapter.py",
        "src/workflow_coordinator/prompt.py",
        "tests/unit/test_a2_evidence_commands.py",
        "tests/unit/test_codex_adapter_conformance.py",
        "tests/unit/test_slice3_vertical.py",
        "tests/unit/test_slice5b.py",
        "tests/fixtures/convergence_curriculum.yaml",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_review_001.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001_review_001.adoc",
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "tests/architecture/test_self_development_governance.py",
        "governance/work-items/correction-convergence-escape-implementation-s2-001.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s2-001-profile.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s2-001-policy.yaml",
        "governance/work-items/correction-convergence-escape-implementation-s2-001-task.txt",
        "governance/work-items/correction-convergence-escape-design-001.yaml",
        "governance/self-development/correction-convergence-escape-design-001-profile.yaml",
        "governance/work-items/correction-convergence-escape-design-revision-001.yaml",
        "governance/self-development/correction-convergence-escape-design-revision-001-profile.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)
    for path in CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_SCOPE:
        assert _within_scope(path, work_item.scope)

    # implement/correct share the exact fourteen-path Slice-2 implementation
    # scope; review and rereview are independently scoped by the profile to
    # the single reused durable review artifact alone -- no phase's
    # authorized_scope grants both.
    assert (
        implement.authorized_scope
        == correct.authorized_scope
        == CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_SCOPE).isdisjoint(review.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_REVIEW_ARTIFACT not in implement.authorized_scope
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_REVIEW_ARTIFACT not in correct.authorized_scope

    # The reused artifact is the SAME path as every Slice-1/S2-001
    # commission's, not a fresh S2-002-suffixed review artifact; both review
    # AND rereview require, and are mapped to, it, with edit+commit
    # authority.
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_REVIEW_ARTIFACT
        == "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_implementation_review_001.adoc"
    )
    assert durable_artifacts == {
        "review": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_REVIEW_ARTIFACT,),
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_REVIEW_ARTIFACT, review.authorized_scope
    )
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_002_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "required"
    assert implement.durable_artifact.value == correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(review.authorized_actions))
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))

    # No Finding authority is granted to either review phase, preserving the
    # established S2 implementation-review convention.
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    # Topology: implement -> review -> {accept: complete, correct} ->
    # correct -> rereview -> {accept: complete, correct}. No
    # diagnosis/reframe/strategy-gate/escape-route phase was added -- those
    # remain later slices -- and no phase carries implementation authority
    # outside the fourteen named production/test paths. max_autonomous_hops
    # is 10, deliberately permitting several correction/rereview cycles.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "implement": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert implement.role == correct.role == "worker"
    assert review.review.context == rereview.review.context == "fresh"

    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    # The task text pins the complete authority lineage this commission
    # exists for: the unaccepted S2-001 factual candidate and its stable
    # blocker, and the accepted Design Revision's closed stable blocker and
    # Family-B contract.
    task_text = texts["task"]
    assert "S2-IR-001" in task_text
    assert "830bdf5536e28a52502bb6faf9172e57093150cb" in task_text
    assert "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S2-001" in task_text
    assert "f08c8cb61480ec5b9e2529eebc5ae44a0463bc9e" in task_text
    assert "DR1-IR-001" in task_text
    assert "pre_route_anchor_sha256" in task_text

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-run-evidence", policy.work_item_scope_ceiling)
    assert not _within_scope("project_memory/canonical", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    # Admission happened entirely through provider-free preflight: no
    # provider/executable was ever invoked, no Runtime/evidence/state/
    # Project-Memory mutation occurred, and the state root the launch would
    # eventually use was never created by this process.
    assert not state_root.exists()


def test_correction_convergence_escape_implementation_s2_003_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Fresh forward-continuation Slice-2 commission atop the S2-002 salvage.

    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S2-003`` is a fresh
    forward-continuation Commission succeeding predecessor
    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S2-002``, whose Run
    ``RUN-51246b2c0cb64da5975c0ecc9c71f7ae`` used exactly one of ten
    authorized Hops, stopped ``reconcile_required`` during an uncertain,
    candidate-less Hop H001, and was subsequently archived mechanically and
    preserved by the maintainer as the non-authoritative Git salvage commit
    ``69e94191df8a390b061eec2ef50d0365d1a08b5e``. This is NOT same-Run
    reconcile/resume, NOT same-Hop retry, NOT provider-session continuation,
    and NOT retrospective acceptance of S2-002 H001. It reuses the identical
    bounded four-phase bootstrap-safe topology (``implement -> review ->
    {accept, correct} -> correct -> rereview -> {accept, correct}``) with
    ``max_autonomous_hops: 10``, the identical fourteen-path Slice-2
    production/test scope, and the SAME reused durable implementation-review
    artifact as every prior Slice-1/Slice-2 commission. The admission
    succeeds entirely through provider-free preflight: no provider is
    invoked and no state root is created.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_003_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-implementation-s2-003"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_003_GOVERNING_PATHS,
    ))
    texts = result[4]
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implement = profile.phases["implement"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_003_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_003_REVIEW_ARTIFACT,
    )
    assert len(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_003_SCOPE) == 14

    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S2-003"
    assert work_item.initial_phase == "implement"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 10
    assert profile.profile_id == "correction-convergence-escape-implementation-s2-003"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-implementation-s2-003"
    assert tuple(profile.phases) == ("implement", "review", "correct", "rereview")

    # Explicit frozen authority lineage: predecessor S2-002 Run/Commission,
    # the salvage commit and its non-authoritative status, the accepted
    # Design Revision's final acceptance commit and its closed stable
    # blocker, and the still-open Slice-2 blocker are all pinned.
    assert "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S2-002" in work_item.authority_source
    assert "RUN-51246b2c0cb64da5975c0ecc9c71f7ae" in work_item.authority_source
    assert "97161b304188af2d3e44e01d2f226220c4d40f0a" in work_item.authority_source
    assert "reconcile_required" in work_item.authority_source
    assert "uncertain_dispatch" in work_item.authority_source
    assert "69e94191df8a390b061eec2ef50d0365d1a08b5e" in work_item.authority_source
    assert "salvage preserves work, not authority" in work_item.authority_source
    assert "83cccb98bb800479645d87051fb1cd66b3a4ac7f" in work_item.authority_source
    assert "4edcea0e341e9c8669378fa299104d66aed2851f" in work_item.authority_source
    assert "f08c8cb61480ec5b9e2529eebc5ae44a0463bc9e" in work_item.authority_source
    assert "S2-IR-001" in work_item.authority_source
    assert "DR1-IR-001" in work_item.authority_source
    assert "keep, alter, remove, or replace" in work_item.authority_source
    assert "55e4c0b4807b260ceb1cd060f635deefc1e46953" in work_item.authority_source

    # WorkItem.scope is exactly Slice 2's seven production paths, seven
    # ordinary test/fixture paths, and the reused durable implementation-
    # review artifact -- never the design FTR, the Design Revision or its
    # own review artifact, the source BUG, S2-001's or S2-002's frozen
    # governance bundles, evidence.py/adapter.py/codex_adapter.py/prompt.py,
    # or any Slice-3/4-only path.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    for withheld in (
        "Makefile",
        "src",
        "governance",
        "workflow-coordinator-runtime",
        "workflow-coordinator-run-evidence",
        "project_memory/canonical",
        "src/workflow_coordinator/evidence.py",
        "src/workflow_coordinator/adapter.py",
        "src/workflow_coordinator/codex_adapter.py",
        "src/workflow_coordinator/prompt.py",
        "tests/unit/test_a2_evidence_commands.py",
        "tests/unit/test_codex_adapter_conformance.py",
        "tests/unit/test_slice3_vertical.py",
        "tests/unit/test_slice5b.py",
        "tests/fixtures/convergence_curriculum.yaml",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_review_001.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001_review_001.adoc",
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "tests/architecture/test_self_development_governance.py",
        "governance/work-items/correction-convergence-escape-implementation-s2-001.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s2-001-profile.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s2-001-policy.yaml",
        "governance/work-items/correction-convergence-escape-implementation-s2-001-task.txt",
        "governance/work-items/correction-convergence-escape-implementation-s2-002.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s2-002-profile.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s2-002-policy.yaml",
        "governance/work-items/correction-convergence-escape-implementation-s2-002-task.txt",
        "governance/work-items/correction-convergence-escape-design-001.yaml",
        "governance/self-development/correction-convergence-escape-design-001-profile.yaml",
        "governance/work-items/correction-convergence-escape-design-revision-001.yaml",
        "governance/self-development/correction-convergence-escape-design-revision-001-profile.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)
    for path in CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_003_SCOPE:
        assert _within_scope(path, work_item.scope)

    # implement/correct share the exact fourteen-path Slice-2 implementation
    # scope; review and rereview are independently scoped by the profile to
    # the single reused durable review artifact alone -- no phase's
    # authorized_scope grants both.
    assert (
        implement.authorized_scope
        == correct.authorized_scope
        == CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_003_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_003_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_003_SCOPE).isdisjoint(review.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_003_REVIEW_ARTIFACT not in implement.authorized_scope
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_003_REVIEW_ARTIFACT not in correct.authorized_scope

    # The reused artifact is the SAME path as every Slice-1/S2-001/S2-002
    # commission's, not a fresh S2-003-suffixed review artifact; both review
    # AND rereview require, and are mapped to, it, with edit+commit
    # authority.
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_003_REVIEW_ARTIFACT
        == "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_implementation_review_001.adoc"
    )
    assert durable_artifacts == {
        "review": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_003_REVIEW_ARTIFACT,),
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_003_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_003_REVIEW_ARTIFACT, review.authorized_scope
    )
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S2_003_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "required"
    assert implement.durable_artifact.value == correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(review.authorized_actions))
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))

    # No Finding authority is granted to either review phase, preserving the
    # established S2 implementation-review convention.
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    # Topology: implement -> review -> {accept: complete, correct} ->
    # correct -> rereview -> {accept: complete, correct}. No
    # diagnosis/reframe/strategy-gate/escape-route phase was added -- those
    # remain later slices -- and no phase carries implementation authority
    # outside the fourteen named production/test paths. max_autonomous_hops
    # is 10, deliberately permitting several correction/rereview cycles.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "implement": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert implement.role == correct.role == "worker"
    assert review.review.context == rereview.review.context == "fresh"

    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    # The task text pins the complete authority lineage this commission
    # exists for: the predecessor Run/Commission, the salvage commit and its
    # non-authoritative status, the accepted Design Revision's closed stable
    # blocker, the still-open Slice-2 blocker, the explicit distinction
    # between execution starting point and assurance baseline, the worker's
    # keep/alter/remove/replace license over salvaged material, and the
    # prohibition on same-Run reconcile/resume.
    task_text = texts["task"]
    assert "RUN-51246b2c0cb64da5975c0ecc9c71f7ae" in task_text
    assert "97161b304188af2d3e44e01d2f226220c4d40f0a" in task_text
    assert "69e94191df8a390b061eec2ef50d0365d1a08b5e" in task_text
    assert "f08c8cb61480ec5b9e2529eebc5ae44a0463bc9e" in task_text
    assert "S2-IR-001" in task_text
    assert "SALVAGE PRESERVES WORK, NOT AUTHORITY" in task_text
    assert "keep, alter, remove, or replace" in task_text
    assert "Execution starting point" in task_text
    assert "Assurance baseline" in task_text
    assert "NO SAME-RUN RECONCILE/RESUME" in task_text
    assert "pre_route_anchor_sha256" in task_text

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-run-evidence", policy.work_item_scope_ceiling)
    assert not _within_scope("project_memory/canonical", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    # Admission happened entirely through provider-free preflight: no
    # provider/executable was ever invoked, no Runtime/evidence/state/
    # Project-Memory mutation occurred, and the state root the launch would
    # eventually use was never created by this process.
    assert not state_root.exists()


def test_correction_convergence_escape_implementation_s3_001_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Fresh Slice-3 ("Profile and routing support") implementation commission.

    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S3-001`` is a
    fresh implementation Commission (no predecessor Run, no salvage) for
    Slice 3 of the accepted correction-loop convergence-escape architecture,
    atop accepted Slice 1 and independently accepted revised Slice 2. It
    reuses the identical bounded four-phase bootstrap-safe topology
    (``implement -> review -> {accept, correct} -> correct -> rereview ->
    {accept, correct}``) with ``max_autonomous_hops: 10`` and the SAME
    reused durable implementation-review artifact as every prior
    Slice-1/Slice-2 commission, scoped to exactly Slice 3's own seven
    production paths and seven ordinary test/fixture paths -- deliberately
    excluding ``adapter.py``/``codex_adapter.py`` (no new agent-facing
    schema), ``tests/unit/test_slice5b.py``, and
    ``tests/fixtures/convergence_curriculum.yaml`` (Slice 4's fixture). The
    source BUG remains open and ungranted. The admission succeeds entirely
    through provider-free preflight: no provider is invoked and no state
    root is created.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-implementation-s3-001"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_GOVERNING_PATHS,
    ))
    texts = result[4]
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implement = profile.phases["implement"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_REVIEW_ARTIFACT,
    )
    assert len(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_SCOPE) == 14

    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S3-001"
    assert work_item.initial_phase == "implement"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 10
    assert profile.profile_id == "correction-convergence-escape-implementation-s3-001"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-implementation-s3-001"
    assert tuple(profile.phases) == ("implement", "review", "correct", "rereview")

    # Explicit frozen authority lineage: accepted original design, accepted
    # Slice-1 record, accepted Design Revision 001's closed stable blocker,
    # the accepted revised Slice-2 Run/candidate/acceptance commit, closed
    # S1/S2 stable identifiers, and the still-open source BUG are all pinned.
    assert "4edcea0e341e9c8669378fa299104d66aed2851f" in work_item.authority_source
    assert "83cccb98bb800479645d87051fb1cd66b3a4ac7f" in work_item.authority_source
    assert "f08c8cb61480ec5b9e2529eebc5ae44a0463bc9e" in work_item.authority_source
    assert "RUN-50c04083b2114b20a313944fffeb9272" in work_item.authority_source
    assert "34587fcdaaf2efe940b7d2d9241fc6e1b64436df" in work_item.authority_source
    assert "01a0dead992e32f6569f9a06324e5fce1b828a2d" in work_item.authority_source
    assert "S1-IR-001" in work_item.authority_source
    assert "S1-IR-002" in work_item.authority_source
    assert "S2-IR-001" in work_item.authority_source
    assert "S2-IR-002" in work_item.authority_source
    assert "DR1-IR-001" in work_item.authority_source
    assert "BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc, remains OPEN" in work_item.authority_source
    assert "does not close it" in work_item.authority_source
    assert "6904ff2ff1ed92516002419134a1a03e615ba8f2" in work_item.authority_source
    assert "Slice 4" in work_item.authority_source
    assert "Slice 5" in work_item.authority_source
    assert "R1A dogfood" in work_item.authority_source

    # WorkItem.scope is exactly Slice 3's seven production paths, seven
    # ordinary test/fixture paths, and the reused durable implementation-
    # review artifact -- never the design FTR, Design Revision, source BUG,
    # any earlier S1/S2 frozen governance bundle, adapter.py/
    # codex_adapter.py/evidence.py, or any Slice-4/5-only path.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    for withheld in (
        "Makefile",
        "src",
        "governance",
        "workflow-coordinator-runtime",
        "workflow-coordinator-run-evidence",
        "project_memory/canonical",
        "src/workflow_coordinator/evidence.py",
        "src/workflow_coordinator/adapter.py",
        "src/workflow_coordinator/codex_adapter.py",
        "src/workflow_coordinator/slice4.py",
        "tests/unit/test_a2_evidence_commands.py",
        "tests/unit/test_codex_adapter_conformance.py",
        "tests/unit/test_slice4.py",
        "tests/unit/test_slice5b.py",
        "tests/fixtures/convergence_curriculum.yaml",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_review_001.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001_review_001.adoc",
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "docs/run-recovery-and-salvage.adoc",
        "tests/architecture/test_self_development_governance.py",
        "governance/work-items/correction-convergence-escape-implementation-s2-003.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s2-003-profile.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s2-003-policy.yaml",
        "governance/work-items/correction-convergence-escape-implementation-s2-003-task.txt",
    ):
        assert not _within_scope(withheld, work_item.scope)
    for path in CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_SCOPE:
        assert _within_scope(path, work_item.scope)

    # implement/correct share the exact fourteen-path Slice-3 implementation
    # scope; review and rereview are independently scoped by the profile to
    # the single reused durable review artifact alone -- no phase's
    # authorized_scope grants both, and the two scopes are disjoint.
    assert (
        implement.authorized_scope
        == correct.authorized_scope
        == CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_SCOPE).isdisjoint(review.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_REVIEW_ARTIFACT not in implement.authorized_scope
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_REVIEW_ARTIFACT not in correct.authorized_scope

    # The reused artifact is the SAME path as every Slice-1/Slice-2
    # commission's, not a fresh S3-001-suffixed review artifact; both review
    # AND rereview require, and are mapped to, it, with edit+commit
    # authority, and neither review phase is granted Finding authority.
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_REVIEW_ARTIFACT
        == "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_implementation_review_001.adoc"
    )
    assert durable_artifacts == {
        "review": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_REVIEW_ARTIFACT,),
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_REVIEW_ARTIFACT, review.authorized_scope
    )
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_001_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "required"
    assert implement.durable_artifact.value == correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(review.authorized_actions))
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    # Topology: implement -> review -> {accept: complete, correct} ->
    # correct -> rereview -> {accept: complete, correct}. No diagnosis/
    # reframe/strategy-gate/escape-route phase was ADDED to this profile --
    # those remain later-slice concerns exercised only inside the FTR under
    # review -- and no phase carries implementation authority outside the
    # fourteen named production/test paths. max_autonomous_hops is 10,
    # deliberately permitting several correction/rereview cycles while
    # remaining a ceiling.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "implement": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert implement.role == correct.role == "worker"
    assert review.review.context == rereview.review.context == "fresh"
    assert review.review.authority.value == "gate"
    assert rereview.review.authority.value == "gate"
    assert review.review.blocking_policy == "blocking_only"
    assert rereview.review.blocking_policy == "blocking_only"

    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    # The task text pins the complete authority lineage and every critical
    # Slice-3 invariant: profile v3, the repeated_counterexample_v1 protocol
    # and its qualified_cycle_limit, the closed convergence_role vocabulary,
    # the evaluator's normal_correction_route/escape_route declarations, the
    # reserved escape route's non-agent-selectability, deterministic route
    # substitution, the trusted Slice-2 qualification index, the requested-
    # versus-applied route distinction, simple-profile non-regression, and
    # the explicit Slice-4 exclusion.
    task_text = texts["task"]
    assert "RUN-50c04083b2114b20a313944fffeb9272" in task_text
    assert "34587fcdaaf2efe940b7d2d9241fc6e1b64436df" in task_text
    assert "01a0dead992e32f6569f9a06324e5fce1b828a2d" in task_text
    assert "83cccb98bb800479645d87051fb1cd66b3a4ac7f" in task_text
    assert "f08c8cb61480ec5b9e2529eebc5ae44a0463bc9e" in task_text
    assert "6904ff2ff1ed92516002419134a1a03e615ba8f2" in task_text
    assert "profile schema version 3" in task_text
    assert "repeated_counterexample_v1" in task_text
    assert "qualified_cycle_limit" in task_text
    assert "convergence_role" in task_text
    assert "normal_correction_route" in task_text
    assert "escape_route" in task_text
    assert "COORDINATOR-RESERVED" in task_text
    assert "must be omitted from the agent's" in task_text
    assert "Deterministic escape substitution" in task_text
    assert "trusted Slice-2 qualification" in task_text
    assert "Requested versus applied routing truth" in task_text
    assert "Simple-profile non-regression" in task_text
    assert "Do not implement any of the following" in task_text
    assert "Slice 4" in task_text
    assert "Slice 5" in task_text
    assert "R1A dogfood" in task_text
    assert "NOT authorized" in task_text

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-run-evidence", policy.work_item_scope_ceiling)
    assert not _within_scope("project_memory/canonical", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    # Admission happened entirely through provider-free preflight: no
    # provider/executable was ever invoked, no Runtime/evidence/state/
    # Project-Memory mutation occurred, and the state root the launch would
    # eventually use was never created by this process.
    assert not state_root.exists()


def test_correction_convergence_escape_implementation_s3_002_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Fresh forward-continuation review-only commission for the already
    trusted Slice-3 candidate.

    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S3-002`` obtains a
    fresh independent review of the already trusted Slice-3 implementation
    candidate ``c34f4806e686e7702cb3e4775d2782341eb0d992`` produced by the
    predecessor ``S3-001`` Run's ``H001``, whose own ``H002`` independent
    review was interrupted (``uncertain_dispatch``, no candidate, no route,
    no review authority) and never resumed. This is a FRESH successor Run,
    not same-Run resume, reconcile, retry, replay, salvage, or
    reimplementation. The profile begins directly in ``review`` -- there is
    no initial ``implement`` phase -- and only permits bounded correction of
    the identical fourteen Slice-3 paths, scoped disjointly from the single
    reused durable review artifact, if and only if review finds a blocking
    defect. The source BUG remains open and ungranted. The admission
    succeeds entirely through provider-free preflight: no provider is
    invoked and no state root is created.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_002_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-implementation-s3-002"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_002_GOVERNING_PATHS,
    ))
    texts = result[4]
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_002_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_002_REVIEW_ARTIFACT,
    )
    assert len(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_002_SCOPE) == 14

    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S3-002"
    assert work_item.initial_phase == "review"
    assert "implement" not in profile.phases
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 10
    assert profile.profile_id == "correction-convergence-escape-implementation-s3-002"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-implementation-s3-002"
    assert tuple(profile.phases) == ("review", "correct", "rereview")

    # Explicit frozen predecessor/authority lineage: the exact predecessor
    # Attempt/Run/WorkItem, the exact trusted H001 candidate SHA, the H002
    # interruption characterization (uncertain dispatch, no candidate, no
    # review authority -- never an accepted review), the complete accepted
    # S1/S2/Design-Revision lineage, the S3-001 commission, and the still-
    # open source BUG are all pinned, together with the explicit non-resume/
    # non-salvage characterization and the Slice-4/5/R1A-dogfood exclusion.
    assert "wfc-correction-convergence-escape-implementation-s3-001-20261004-01" in work_item.authority_source
    assert "RUN-5cef7672fa46484396177757ec9670eb" in work_item.authority_source
    assert "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S3-001" in work_item.authority_source
    assert "c34f4806e686e7702cb3e4775d2782341eb0d992" in work_item.authority_source
    assert "INV-a962023bb2a84936a5234ba91b1b2467" in work_item.authority_source
    assert "uncertain_dispatch" in work_item.authority_source
    assert "candidate_result_present=false" in work_item.authority_source
    assert "NOT independent acceptance" in work_item.authority_source
    assert "NOT same-Run resume" in work_item.authority_source
    assert "NOT reconcile" in work_item.authority_source
    assert "NOT salvage" in work_item.authority_source
    assert "NOT reimplementation from scratch" in work_item.authority_source
    assert "f594eba0a2f76566e1aa5dc9831d6144586a957a" in work_item.authority_source
    assert "4edcea0e341e9c8669378fa299104d66aed2851f" in work_item.authority_source
    assert "83cccb98bb800479645d87051fb1cd66b3a4ac7f" in work_item.authority_source
    assert "f08c8cb61480ec5b9e2529eebc5ae44a0463bc9e" in work_item.authority_source
    assert "34587fcdaaf2efe940b7d2d9241fc6e1b64436df" in work_item.authority_source
    assert "01a0dead992e32f6569f9a06324e5fce1b828a2d" in work_item.authority_source
    assert "6db337ac80a93b0030ef6dc6e55bebafee3667fa" in work_item.authority_source
    assert "BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc, remains OPEN" in work_item.authority_source
    assert "does not close it" in work_item.authority_source
    assert "Slice 4" in work_item.authority_source
    assert "Slice 5" in work_item.authority_source
    assert "R1A dogfood" in work_item.authority_source

    # WorkItem.scope is exactly the same fourteen Slice-3 paths plus the
    # reused durable review artifact -- never the design FTR, Design
    # Revision, source BUG, any S1/S2/S3-001 frozen governance bundle,
    # adapter.py/codex_adapter.py/evidence.py, or any Slice-4/5-only path.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    for withheld in (
        "Makefile",
        "src",
        "governance",
        "workflow-coordinator-runtime",
        "workflow-coordinator-run-evidence",
        "project_memory/canonical",
        "src/workflow_coordinator/evidence.py",
        "src/workflow_coordinator/adapter.py",
        "src/workflow_coordinator/codex_adapter.py",
        "src/workflow_coordinator/slice4.py",
        "tests/unit/test_codex_adapter_conformance.py",
        "tests/unit/test_slice4.py",
        "tests/unit/test_slice5b.py",
        "tests/fixtures/convergence_curriculum.yaml",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_review_001.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001_review_001.adoc",
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "docs/run-recovery-and-salvage.adoc",
        "tests/architecture/test_self_development_governance.py",
        "governance/work-items/correction-convergence-escape-implementation-s3-001.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s3-001-profile.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s3-001-policy.yaml",
        "governance/work-items/correction-convergence-escape-implementation-s3-001-task.txt",
    ):
        assert not _within_scope(withheld, work_item.scope)
    for path in CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_002_SCOPE:
        assert _within_scope(path, work_item.scope)

    # correct carries the exact fourteen-path Slice-3 correction scope;
    # review and rereview are independently scoped by the profile to the
    # single reused durable review artifact alone -- no phase's
    # authorized_scope grants both, and the two scopes are disjoint. There
    # is no implementation phase at all in this profile.
    assert correct.authorized_scope == CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_002_SCOPE
    assert review.authorized_scope == rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_002_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_002_SCOPE).isdisjoint(review.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_002_REVIEW_ARTIFACT not in correct.authorized_scope

    # The reused artifact is the SAME path as every prior Slice-1/2/3-001
    # commission's, not a fresh S3-002-suffixed review artifact; both review
    # AND rereview require, and are mapped to, it, with edit+commit
    # authority, and neither review phase is granted Finding authority.
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_002_REVIEW_ARTIFACT
        == "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_implementation_review_001.adoc"
    )
    assert durable_artifacts == {
        "review": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_002_REVIEW_ARTIFACT,),
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_002_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_002_REVIEW_ARTIFACT, review.authorized_scope
    )
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_002_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "required"
    assert correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(review.authorized_actions))
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))
    assert review.review.finding_authority == ()
    assert rereview.review.finding_authority == ()

    # Topology: review -> {accept: complete, correct} -> correct ->
    # rereview -> {accept: complete, correct}. No implement phase, no
    # diagnosis/reframe/strategy-gate/escape-route phase was added to this
    # profile -- those remain later-slice concerns exercised only inside the
    # FTR under review -- and no phase carries implementation authority
    # outside the fourteen named production/test paths. max_autonomous_hops
    # is 10, deliberately permitting several correction/rereview cycles
    # while remaining a ceiling, and is NOT a continuation of the
    # predecessor's remaining budget.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert correct.role == "worker"
    assert review.review.context == rereview.review.context == "fresh"
    assert review.review.authority.value == "gate"
    assert rereview.review.authority.value == "gate"
    assert review.review.blocking_policy == "blocking_only"
    assert rereview.review.blocking_policy == "blocking_only"

    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    # The task text pins the complete review contract and the explicit
    # fresh-successor/non-resume/non-salvage distinctions: the trusted
    # candidate is not independent acceptance, this is not same-Run resume,
    # the review must be of the complete Slice-3 contract, profile v3, the
    # reserved escape route, deterministic substitution, trusted Slice-2
    # qualification, requested/applied route truth, simple-profile non-
    # regression, and the explicit Slice-4 exclusion.
    task_text = texts["task"]
    assert "trusted implementation candidate" in task_text
    assert "NOT independent acceptance" in task_text
    assert "NOT:" in task_text and "same-Run resume" in task_text
    assert "c34f4806e686e7702cb3e4775d2782341eb0d992" in task_text
    assert "INV-a962023bb2a84936a5234ba91b1b2467" in task_text
    assert "complete Slice-3 implementation" in task_text or "complete accepted Slice-3 contract" in task_text
    assert "profile schema version 3" in task_text or "Profile schema version 3" in task_text
    assert "repeated_counterexample_v1" in task_text
    assert "qualified_cycle_limit" in task_text
    assert "COORDINATOR-RESERVED" in task_text
    assert "Deterministic escape substitution" in task_text
    assert "Trusted Slice-2 qualification" in task_text
    assert "Requested versus applied route truth" in task_text
    assert "Simple-profile compatibility" in task_text
    assert "Slice-4 boundary" in task_text
    assert "Slice 4" in task_text
    assert "Slice 5" in task_text
    assert "R1A dogfood" in task_text
    assert "NOT authorized" in task_text

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-run-evidence", policy.work_item_scope_ceiling)
    assert not _within_scope("project_memory/canonical", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    # Admission happened entirely through provider-free preflight: no
    # provider/executable was ever invoked, no Runtime/evidence/state/
    # Project-Memory mutation occurred, and the state root the launch would
    # eventually use was never created by this process.
    assert not state_root.exists()


def test_correction_convergence_escape_implementation_s3_003_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Fresh successor rereview commission correcting the S3-002 governance gap.

    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S3-003`` obtains a
    fresh independent rereview of the technically-corrected Slice-3 candidate
    ``70c252c5e22f52d7933b9f65cd60118ec83000e0`` (S3-002's H002 correction).
    S3-002's own H003 rereview technically judged that correction sufficient
    but could not resolve its Run-native ``F001``/``F002``/``F003`` because
    its profile granted ``finding_authority: []`` -- see
    ``docs/backlog/BUG-WFC-SELF-DEVELOPMENT-GATE-FINDING-AUTHORITY.adoc``.
    This profile corrects exactly that gap by granting its ``rereview`` gate
    ``finding_authority: [resolve]`` and nothing broader. This is a FRESH
    successor Run: it inherits no predecessor Run-native Finding, mutates no
    S3-002 Run state, and begins directly in ``rereview`` with no
    ``implement``/``review`` phase. The stable cross-Run identifiers
    S3-IR-001/002/003 are durable review-artifact prose, never WFC Finding
    IDs: a rereview that confirms all three corrections and finds no new
    current blocker closes them in the durable artifact and requests
    ``accept`` with an EMPTY S3-003 Findings delta, never a synthetic
    S3-003 Run-native Finding created merely to mirror them.
    ``finding_authority: [resolve]`` exists only defensively, for a genuine
    S3-003-local Finding this Run's own rereview might materialize and a
    later correction fix. The admission succeeds entirely through
    provider-free preflight: no provider is invoked and no state root is
    created.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_003_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-implementation-s3-003"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_003_GOVERNING_PATHS,
    ))
    texts = result[4]
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    rereview = profile.phases["rereview"]
    correct = profile.phases["correct"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_003_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_003_REVIEW_ARTIFACT,
    )
    assert len(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_003_SCOPE) == 14

    # Fresh S3-003 identity, starting directly in rereview, with exactly the
    # two reachable phases and no implement/review phase at all.
    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S3-003"
    assert work_item.initial_phase == "rereview"
    assert "implement" not in profile.phases
    assert "review" not in profile.phases
    assert tuple(profile.phases) == ("rereview", "correct")
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 10
    assert profile.profile_id == "correction-convergence-escape-implementation-s3-003"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-implementation-s3-003"

    # Predecessor S3-002 identity is pinned: Run, Commission, H001/H002/H003
    # facts, final awaiting_human status with F001/F002/F003 still open, and
    # the corrected candidate this Commission actually operates on.
    assert "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S3-002" in work_item.authority_source
    assert "RUN-2bc443f24ec642a3ae1f4d1da462a336" in work_item.authority_source
    assert "7738078f0905af78dfffe56d081062c58409c2d2" in work_item.authority_source
    assert "dc811ae4055801f4afc7181378bd1dbaa6940f2d" in work_item.authority_source
    assert "70c252c5e22f52d7933b9f65cd60118ec83000e0" in work_item.authority_source
    assert "INV-d4ed8e692ea442bf8220916ad36050d6" in work_item.authority_source
    assert "awaiting_human" in work_item.authority_source
    assert "F001" in work_item.authority_source
    assert "F002" in work_item.authority_source
    assert "F003" in work_item.authority_source
    assert "FRESH successor" in work_item.authority_source
    assert "NOT same-Run resume" in work_item.authority_source
    assert "NOT reconcile" in work_item.authority_source
    assert "NOT salvage" in work_item.authority_source
    assert "NOT reimplementation from scratch" in work_item.authority_source

    # Explicit fresh-Run / no-inheritance semantics: S3-002's F001/F002/F003
    # are frozen predecessor Run-local truth, never inherited or mutable by
    # this Commission, and this Commission does not claim authority to
    # transition them -- it is never asserted that S3-003 "may resolve"
    # S3-002's own Finding identities.
    assert "frozen historical Run truth" in work_item.authority_source
    assert "NOT inherited" in work_item.authority_source
    assert "NOT mutable" in work_item.authority_source
    assert "does NOT claim authority over S3-002's F001/F002/F003" in work_item.authority_source
    assert "begins with no inherited predecessor Findings" in work_item.authority_source
    assert "may resolve S3-002" not in work_item.authority_source
    assert "transition S3-002" not in work_item.authority_source

    # Stable cross-Run durable review identifiers carried forward. The
    # persona dispositions them in the durable artifact and separately holds
    # resolve-only authority for genuine S3-003-local Run Findings -- it
    # never claims that S3-IR-001/002/003 are themselves Run-native Findings
    # or that disposition authority is granted "to disposition S3-IR".
    assert "S3-IR-001" in work_item.authority_source
    assert "S3-IR-002" in work_item.authority_source
    assert "S3-IR-003" in work_item.authority_source
    reviewer_persona = work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]
    assert "dispositions S3-IR-001/002/003 in the durable review artifact" in reviewer_persona
    assert "resolve-only Finding authority for any genuine S3-003-local Run Finding" in reviewer_persona
    assert "resolve-only Finding authority to disposition S3-IR" not in reviewer_persona

    # New governance BUG referenced; source correction-carryover BUG remains
    # independently open and unreferenced as shared authority.
    assert "BUG-WFC-SELF-DEVELOPMENT-GATE-FINDING-AUTHORITY.adoc" in work_item.authority_source
    assert "BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc" in work_item.authority_source
    assert "remains independently OPEN" in work_item.authority_source

    # Slice 4/5/R1A withheld; no Runtime/push/evidence authority.
    assert "Slice 4" in work_item.authority_source
    assert "Slice 5" in work_item.authority_source
    assert "R1A dogfood" in work_item.authority_source
    assert "does not promote workflow-coordinator-runtime" in work_item.authority_source
    assert "does not push" in work_item.authority_source
    assert "does not mutate the workflow-coordinator-run-evidence repository" in work_item.authority_source
    assert "does not launch any Run or invoke any provider" in work_item.authority_source

    # WorkItem.scope is exactly the same fourteen Slice-3 paths plus the
    # reused durable review artifact -- disjoint phase scopes, same as S3-002.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    for withheld in (
        "Makefile",
        "src",
        "governance",
        "workflow-coordinator-runtime",
        "workflow-coordinator-run-evidence",
        "project_memory/canonical",
        "src/workflow_coordinator/adapter.py",
        "src/workflow_coordinator/codex_adapter.py",
        "src/workflow_coordinator/slice4.py",
        "tests/unit/test_codex_adapter_conformance.py",
        "tests/unit/test_slice4.py",
        "tests/unit/test_slice5b.py",
        "tests/fixtures/convergence_curriculum.yaml",
        "tests/architecture/test_self_development_governance.py",
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "docs/backlog/BUG-WFC-SELF-DEVELOPMENT-GATE-FINDING-AUTHORITY.adoc",
        "governance/work-items/correction-convergence-escape-implementation-s3-002.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s3-002-profile.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s3-002-policy.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)
    for path in CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_003_SCOPE:
        assert _within_scope(path, work_item.scope)

    # correct carries the exact fourteen-path Slice-3 correction scope;
    # rereview is independently scoped to the single reused durable review
    # artifact alone -- the two scopes are disjoint.
    assert correct.authorized_scope == CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_003_SCOPE
    assert rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_003_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_003_SCOPE).isdisjoint(rereview.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_003_REVIEW_ARTIFACT not in correct.authorized_scope
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_003_REVIEW_ARTIFACT
        == "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_implementation_review_001.adoc"
    )
    assert durable_artifacts == {
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_003_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S3_003_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert rereview.durable_artifact.value == "required"
    assert correct.durable_artifact.value == "none"
    assert {"review", "edit", "commit"}.issubset(set(rereview.authorized_actions))
    assert {"edit", "commit"}.issubset(set(correct.authorized_actions))

    # The core of this Commission: rereview is gate/fresh/blocking-only with
    # resolve-only Finding authority -- no residual, supersede, reopen, or
    # human authority granted.
    assert rereview.role == "independent_reviewer"
    assert correct.role == "worker"
    assert rereview.review.context == "fresh"
    assert rereview.review.authority.value == "gate"
    assert rereview.review.blocking_policy == "blocking_only"
    assert rereview.review.finding_authority == ("resolve",)
    assert "residual" not in rereview.review.finding_authority
    assert "supersede" not in rereview.review.finding_authority
    assert "reopen" not in rereview.review.finding_authority

    # Topology: rereview -> {accept: complete, correct} -> correct ->
    # rereview. No implement/review phase was added, and max_autonomous_hops
    # is a 10-Hop ceiling, not a target, permitting an immediate one-Hop
    # accept as the expected happy path while still allowing several
    # correction/rereview cycles if a genuine blocker remains.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
    }

    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    task_text = texts["task"]
    assert "FRESH successor" in task_text
    assert "NOT:" in task_text and "same-Run resume of S3-002" in task_text
    assert "70c252c5e22f52d7933b9f65cd60118ec83000e0" in task_text
    assert "INV-d4ed8e692ea442bf8220916ad36050d6" in task_text
    assert "CRITICAL SUCCESSOR-IDENTITY RULE" in task_text
    assert "does NOT inherit S3-002's Run-native Findings" in task_text
    assert "S3-IR-001" in task_text and "S3-IR-002" in task_text and "S3-IR-003" in task_text
    assert "finding_authority: [resolve]" in task_text
    assert "not residual, not supersede, not reopen, not human" in task_text

    # Critical Finding-identity distinction: S3-IR-001/002/003 are durable
    # review-artifact prose identifiers dispositioned by writing into that
    # artifact, never WFC Finding-ledger `resolve` operations. An empty
    # Findings delta is explicitly permitted (and expected) on immediate
    # acceptance, and creating a synthetic S3-003 Finding merely to mirror a
    # historical S3-IR identifier is explicitly forbidden. `resolve`
    # authority is reserved for a genuine S3-003-local Finding only.
    assert "durable review-artifact prose identifiers, not WFC" in task_text
    assert "never a WFC Finding-ledger `resolve` operation" in task_text
    assert "EMPTY S3-003 WFC Findings delta" in task_text
    assert "must NOT create a synthetic S3-003" in task_text
    assert "merely to mirror a historical S3-IR identifier" in task_text
    assert "exists purely" in task_text and "defensively" in task_text
    assert "genuine S3-003-local Finding may ever be the" in task_text
    assert "never operates on S3-002's F001/F002/F003" in task_text
    assert "never the mechanism by" in task_text and "an S3-IR prose identifier is dispositioned" in task_text
    assert "Slice 4" in task_text
    assert "Slice 5" in task_text
    assert "R1A dogfood" in task_text
    assert "NOT authorized" in task_text
    assert "BUG-WFC-SELF-DEVELOPMENT-GATE-FINDING-AUTHORITY.adoc" in task_text
    assert "BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc" in task_text
    assert "remains independently OPEN" in task_text

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-run-evidence", policy.work_item_scope_ceiling)
    assert not _within_scope("project_memory/canonical", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    # Admission happened entirely through provider-free preflight: no
    # provider/executable was ever invoked, no Runtime/Evidence/STATE/OPS
    # mutation occurred, and the state root the launch would eventually use
    # was never created by this process.
    assert not state_root.exists()


def test_correction_convergence_escape_implementation_s4_001_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Fresh Slice-4 ("Diagnosis and reframe support") implementation commission.

    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S4-001`` is a fresh
    implementation Commission (no predecessor Run, no salvage) for Slice 4 of
    the accepted correction-loop convergence-escape architecture, atop
    accepted Slice 1, independently accepted revised Slice 2, and
    independently accepted Slice 3. It reuses the identical bounded
    four-phase topology (``implement -> review -> {accept, correct} ->
    correct -> rereview -> {accept, correct}``) with ``max_autonomous_hops:
    10`` and the SAME reused durable implementation-review artifact as every
    prior Slice-1/2/3 commission, scoped to Slice 4's own eight production
    paths and nine ordinary test/fixture paths -- now including
    ``codex_adapter.py``/``tests/unit/test_codex_adapter_conformance.py``
    (Slice 4 activates real non-null agent-facing result content for the
    first time) and ``tests/fixtures/convergence_curriculum.yaml`` (the
    curriculum-domain falsification fixture the accepted FTR assigns to this
    slice), while still deliberately excluding ``adapter.py`` (generic
    provider-neutral delegation, no schema of its own),
    ``src/workflow_coordinator/slice4.py`` (an unrelated EXISTING production
    file for Design-Revision-001's own crash-recovery numbering, not this
    FTR's Slice 4), ``tests/unit/test_slice4.py`` (an unrelated EXISTING
    historical test file for that same crash-recovery surface, excluded for
    semantic-ownership / naming-collision reasons -- NOT because it does not
    exist; new semantics land in the existing test files exactly as every
    prior slice's did), and ``tests/unit/test_slice5b.py`` (unrelated prior
    implementation). The profile applies the self-development
    Finding-authority governance fix from the outset: because initial_phase
    is ``implement``, the normal fresh-Run sequence is H001 = implement,
    H002 = review -- ``review`` is NOT the first Hop, and the H001
    ``implement`` Hop may legitimately create a genuine current-Run OPEN
    Finding without any disposition authority (creating OPEN is not itself a
    disposition operation under the reducer's ``_finding_operation``
    semantics). Both ``review`` and ``rereview`` are therefore granted the
    identical minimal ``finding_authority: [resolve]`` -- mirroring, but not
    identically reproducing, the fix the S3-003 successor Commission already
    applied after ``BUG-WFC-SELF-DEVELOPMENT-GATE-FINDING-AUTHORITY`` was
    discovered (S3-003 began directly at ``rereview`` with no
    ``implement``/``review`` phase, so only its one gate phase needed the
    grant; this Commission's four-phase topology gives both gates a
    preceding worker Hop, so both need it). Both the source BUG and the
    self-development governance BUG remain open and ungranted. The admission
    succeeds entirely through provider-free preflight: no provider is
    invoked and no state root is created.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-implementation-s4-001"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_GOVERNING_PATHS,
    ))
    texts = result[4]
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implement = profile.phases["implement"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_REVIEW_ARTIFACT,
    )
    assert len(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_SCOPE) == 17

    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S4-001"
    assert work_item.initial_phase == "implement"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 10
    assert profile.profile_id == "correction-convergence-escape-implementation-s4-001"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-implementation-s4-001"
    assert tuple(profile.phases) == ("implement", "review", "correct", "rereview")

    # Explicit frozen authority lineage: accepted original design, accepted
    # Design Revision 001, accepted Slice-1/2/3 records, closed stable
    # identifiers through S3-IR-003, the S3-002 Finding-authority governance
    # defect and its S3-003 fix, and both still-open BUGs are all pinned.
    assert "4edcea0e341e9c8669378fa299104d66aed2851f" in work_item.authority_source
    assert "f08c8cb61480ec5b9e2529eebc5ae44a0463bc9e" in work_item.authority_source
    assert "83cccb98bb800479645d87051fb1cd66b3a4ac7f" in work_item.authority_source
    assert "34587fcdaaf2efe940b7d2d9241fc6e1b64436df" in work_item.authority_source
    assert "01a0dead992e32f6569f9a06324e5fce1b828a2d" in work_item.authority_source
    assert "6db337ac80a93b0030ef6dc6e55bebafee3667fa" in work_item.authority_source
    assert "c34f4806e686e7702cb3e4775d2782341eb0d992" in work_item.authority_source
    assert "7738078f0905af78dfffe56d081062c58409c2d2" in work_item.authority_source
    assert "70c252c5e22f52d7933b9f65cd60118ec83000e0" in work_item.authority_source
    assert "343f419a2cf122221dd5b7ec98be92cd61e2ec24" in work_item.authority_source
    assert "a889f9a3b515b4cac600dc9f7366637a388f2e25" in work_item.authority_source
    assert "S3-IR-001" in work_item.authority_source
    assert "S3-IR-002" in work_item.authority_source
    assert "S3-IR-003" in work_item.authority_source
    assert "F001" in work_item.authority_source
    assert "F002" in work_item.authority_source
    assert "F003" in work_item.authority_source
    assert "BUG-WFC-SELF-DEVELOPMENT-GATE-FINDING-AUTHORITY.adoc" in work_item.authority_source
    assert "finding_authority: [resolve]" in work_item.authority_source

    # Corrected identity/authority wording: F001/F002/F003 (Run-native WFC
    # Finding IDs) and S3-IR-001/002/003 (durable review-artifact prose
    # identifiers) are explicitly named as distinct identity surfaces, never
    # aliases; S3-003's own Run-native Finding ledger is stated to have
    # remained empty throughout, not merely "if any"; and the implement-then-
    # review topology (H001 implement may leave a genuine OPEN Finding for
    # H002 review) is stated accurately rather than claiming review is the
    # first Hop with nothing to resolve.
    assert "NOT the same" in work_item.authority_source
    assert "distinct identity surfaces" in work_item.authority_source
    assert "never aliases" in work_item.authority_source
    assert "remained EMPTY throughout" in work_item.authority_source
    assert "Run-native Findings, if any" not in work_item.authority_source
    assert "H001 is implement, not review" in work_item.authority_source
    assert "is NOT itself a disposition operation" in work_item.authority_source
    assert "review is the first Hop" not in work_item.authority_source
    assert "it is the first Hop; nothing" not in work_item.authority_source

    assert "BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc" in work_item.authority_source
    assert "remains OPEN" in work_item.authority_source
    assert "Slice 5" in work_item.authority_source
    assert "R1A dogfood" in work_item.authority_source

    # WorkItem.scope is exactly Slice 4's eight production paths, nine
    # ordinary test/fixture paths, and the reused durable implementation-
    # review artifact -- never the design FTR, Design Revision, either open
    # BUG, any earlier S1/S2/S3 frozen governance bundle, the unrelated
    # adapter.py or slice4.py production files, or any Slice-5-only path.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    for withheld in (
        "Makefile",
        "src",
        "governance",
        "workflow-coordinator-runtime",
        "workflow-coordinator-run-evidence",
        "project_memory/canonical",
        "src/workflow_coordinator/evidence.py",
        "src/workflow_coordinator/adapter.py",
        "src/workflow_coordinator/slice4.py",
        "tests/unit/test_slice4.py",
        "tests/unit/test_slice5b.py",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_review_001.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001_review_001.adoc",
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "docs/backlog/BUG-WFC-SELF-DEVELOPMENT-GATE-FINDING-AUTHORITY.adoc",
        "tests/architecture/test_self_development_governance.py",
        "governance/work-items/correction-convergence-escape-implementation-s3-003.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s3-003-profile.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s3-003-policy.yaml",
        "governance/work-items/correction-convergence-escape-implementation-s3-003-task.txt",
    ):
        assert not _within_scope(withheld, work_item.scope)
    for path in CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_SCOPE:
        assert _within_scope(path, work_item.scope)

    # implement/correct share the exact seventeen-path Slice-4 implementation
    # scope; review and rereview are independently scoped by the profile to
    # the single reused durable review artifact alone -- no phase's
    # authorized_scope grants both, and the two scopes are disjoint.
    assert (
        implement.authorized_scope
        == correct.authorized_scope
        == CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_SCOPE).isdisjoint(review.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_REVIEW_ARTIFACT not in implement.authorized_scope
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_REVIEW_ARTIFACT not in correct.authorized_scope

    # The reused artifact is the SAME path as every Slice-1/2/3 commission's,
    # not a fresh S4-001-suffixed review artifact; both review AND rereview
    # require, and are mapped to, it, with edit+commit authority.
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_REVIEW_ARTIFACT
        == "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_implementation_review_001.adoc"
    )
    assert durable_artifacts == {
        "review": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_REVIEW_ARTIFACT,),
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_REVIEW_ARTIFACT, review.authorized_scope
    )
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_001_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "required"
    assert implement.durable_artifact.value == correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(review.authorized_actions))
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))

    # The core governance-authority invariant of this Commission: review is
    # NOT the first Hop (implement precedes it), so H001 implement may
    # legitimately leave a genuine current-Run OPEN Finding for H002 review
    # to resolve once independently verified -- creating OPEN is not itself
    # a disposition operation, so implement needed no finding_authority to
    # do so, but review's own resolve of it IS a disposition operation and
    # requires the grant. Both review and rereview are therefore granted the
    # identical minimal resolve-only authority, never residual/supersede/
    # reopen/human -- the S3-002 defect (an empty-everywhere disposition-
    # bearing gate) is not repeated.
    assert review.review.finding_authority == ("resolve",)
    assert rereview.review.finding_authority == ("resolve",)
    assert "residual" not in review.review.finding_authority
    assert "supersede" not in review.review.finding_authority
    assert "reopen" not in review.review.finding_authority
    assert "residual" not in rereview.review.finding_authority
    assert "supersede" not in rereview.review.finding_authority
    assert "reopen" not in rereview.review.finding_authority

    # Topology: implement -> review -> {accept: complete, correct} ->
    # correct -> rereview -> {accept: complete, correct}. max_autonomous_hops
    # is 10, deliberately permitting several correction/rereview cycles
    # while remaining a ceiling.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "implement": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert implement.role == correct.role == "worker"
    assert review.review.context == rereview.review.context == "fresh"
    assert review.review.authority.value == "gate"
    assert rereview.review.authority.value == "gate"
    assert review.review.blocking_policy == "blocking_only"
    assert rereview.review.blocking_policy == "blocking_only"

    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    # The task text pins the complete authority lineage, the naming-collision
    # warnings, the exact Slice-4 semantic contract (typed activation, exact
    # escape/diagnosis/revision correlation, exactly-once generation
    # advancement, diagnosis read-only enforcement, strategy-gate authority
    # limits), the Finding-authority rationale, and the explicit Slice-5/R1A
    # exclusion.
    task_text = texts["task"]
    assert "4edcea0e341e9c8669378fa299104d66aed2851f" in task_text
    assert "f08c8cb61480ec5b9e2529eebc5ae44a0463bc9e" in task_text
    assert "343f419a2cf122221dd5b7ec98be92cd61e2ec24" in task_text
    assert "a889f9a3b515b4cac600dc9f7366637a388f2e25" in task_text
    assert "NAMING COLLISIONS" in task_text
    assert "slice4.py" in task_text and "NOT authorized" in task_text
    assert "DiagnosisReport" in task_text
    assert "StrategyRevision" in task_text
    assert "StrategyAssessment" in task_text
    assert "Exact escape/diagnosis/revision correlation" in task_text
    assert "Exactly-once generation advancement" in task_text
    assert "Diagnosis read-only/non-authority enforcement" in task_text
    assert "Strategy-gate authority limits" in task_text
    assert "curriculum" in task_text.lower()
    assert "FINDING AUTHORITY" in task_text
    assert "do not repeat the S3-002 Commission defect" in task_text
    assert "finding_authority: [resolve]" in task_text
    assert "Do not implement any of the following" in task_text
    assert "Slice 5" in task_text
    assert "R1A dogfood" in task_text
    assert "NOT authorized" in task_text

    # Correction A: the task text truthfully states review follows implement
    # (H001 = implement, H002 = review) and explicitly flags the superseded
    # "first Hop / nothing exists yet to resolve" premise as FALSE, rather
    # than silently asserting it; it also states the reducer-level reason
    # creating an OPEN Finding needs no finding_authority.
    assert "NOT this Run's first Hop" in task_text
    assert "is\nFALSE" in task_text
    assert "H001 = implement, H002 = review" in task_text
    assert "H001-created current-Run OPEN Finding" in task_text
    assert "_finding_operation(None, FindingState.OPEN)` returns no operation" in task_text

    # Correction B: tests/unit/test_slice4.py is described truthfully as an
    # EXISTING historical file, excluded for semantic-ownership/naming-
    # collision reasons, never as nonexistent.
    assert "test_slice4.py` IS an EXISTING historical test file" in task_text
    assert "NOT because the file\n  does not exist" in task_text
    assert "test_slice4.py` does NOT exist" not in task_text
    assert "deliberately NOT created" not in task_text

    # Correction C: F001/F002/F003 (Run-native WFC Finding IDs) and
    # S3-IR-001/002/003 (durable review-artifact identifiers) are kept
    # explicitly distinct, never aliased, and S3-003's own Run-native
    # Finding ledger is stated to have remained empty throughout.
    assert "NOT the\n     same identifiers as, and never aliases of" in task_text
    assert "distinct identity surfaces" in task_text
    assert "remained EMPTY throughout" in task_text
    assert "durable identifiers S3-IR-001/002/003" not in task_text

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-run-evidence", policy.work_item_scope_ceiling)
    assert not _within_scope("project_memory/canonical", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    # Admission happened entirely through provider-free preflight: no
    # provider/executable was ever invoked, no Runtime/evidence/state/
    # Project-Memory mutation occurred, and the state root the launch would
    # eventually use was never created by this process.
    assert not state_root.exists()


def test_correction_convergence_escape_implementation_s4_002_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Fresh SUCCESSOR Slice-4 implementation commission after S4-001 salvage.

    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S4-002`` is a fresh
    successor Commission authored after predecessor
    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S4-001`` stopped
    fail-closed at H001 (``outcome=changes_required`` is not a commit-capable
    ordinary implementation disposition) and its exact dirty candidate was
    mechanically preserved by a maintainer-owned salvage commit. The salvage
    commit carries no trusted authority -- it is candidate material only --
    so this successor reuses the identical bounded four-phase topology
    (``implement -> review -> {accept, correct} -> correct -> rereview ->
    {accept, correct}``) with ``max_autonomous_hops: 10``, the SAME reused
    durable implementation-review artifact, and the SAME exact seventeen-path
    Slice-4 scope as S4-001, while hardening its own task text and
    ``WorkItem.personas`` prose against the exact phase-local
    outcome-semantics ambiguity the predecessor exposed. The admission
    succeeds entirely through provider-free preflight: no provider is invoked
    and no state root is created.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_002_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-implementation-s4-002"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_002_GOVERNING_PATHS,
    ))
    texts = result[4]
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implement = profile.phases["implement"]
    review = profile.phases["review"]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_002_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_002_REVIEW_ARTIFACT,
    )
    assert len(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_002_SCOPE) == 17

    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S4-002"
    assert work_item.initial_phase == "implement"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 10
    assert profile.profile_id == "correction-convergence-escape-implementation-s4-002"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-implementation-s4-002"
    assert tuple(profile.phases) == ("implement", "review", "correct", "rereview")

    # Explicit frozen authority lineage: accepted original design, accepted
    # Design Revision 001, accepted Slice-1/2/3 records, and both still-open
    # BUGs are pinned -- exactly the assurance baseline, never the salvage.
    assert "4edcea0e341e9c8669378fa299104d66aed2851f" in work_item.authority_source
    assert "f08c8cb61480ec5b9e2529eebc5ae44a0463bc9e" in work_item.authority_source
    assert "83cccb98bb800479645d87051fb1cd66b3a4ac7f" in work_item.authority_source
    assert "34587fcdaaf2efe940b7d2d9241fc6e1b64436df" in work_item.authority_source
    assert "01a0dead992e32f6569f9a06324e5fce1b828a2d" in work_item.authority_source
    assert "343f419a2cf122221dd5b7ec98be92cd61e2ec24" in work_item.authority_source
    assert "a889f9a3b515b4cac600dc9f7366637a388f2e25" in work_item.authority_source
    assert "S3-IR-001" in work_item.authority_source
    assert "S3-IR-002" in work_item.authority_source
    assert "S3-IR-003" in work_item.authority_source
    assert "ASSURANCE BASELINE" in work_item.authority_source
    assert "BUG-WFC-SELF-DEVELOPMENT-GATE-FINDING-AUTHORITY.adoc" in work_item.authority_source
    assert "BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc" in work_item.authority_source
    assert "remains OPEN" in work_item.authority_source
    assert "finding_authority: [resolve]" in work_item.authority_source
    assert "Slice 5" in work_item.authority_source
    assert "R1A dogfood" in work_item.authority_source

    # Predecessor S4-001 Run identity and frozen historical disposition are
    # correctly recorded: its own Run ID, Hop, provider binding, truthful
    # outcome/requested_route, and the reason the Coordinator refused to
    # commit it -- never reinterpreted as completed, resumed, or retried.
    assert "RUN-3efc7eb2aa374263a2b12954213642f1" in work_item.authority_source
    assert "3f801665bdcac2fa4065a4c165789e88ee712176" in work_item.authority_source
    assert "outcome=changes_required" in work_item.authority_source
    assert "requested_route=review" in work_item.authority_source
    assert "awaiting_human" in work_item.authority_source
    assert "no H002 ever occurred" in work_item.authority_source
    assert "no S4-IR identifier was ever opened" in work_item.authority_source
    assert "exactly eight in-scope paths" in work_item.authority_source
    assert "eight in-scope production paths" not in work_item.authority_source

    # Salvage vs assurance baseline: the salvage commit, its frozen diff
    # hash, and the evidence-repository archive are named as preservation
    # only, explicitly never acceptance, never a new assurance baseline, and
    # never same-Run/same-Hop authority; the execution starting point
    # (salvage ancestry) is explicitly distinguished from the assurance
    # baseline (the accepted Slice-1/2/3 lineage).
    assert "15102e6b17a3180e2650ac1cb2a5e4bffe3822f8" in work_item.authority_source
    assert "a7adb30666cca54891dc61c76c9df5d81bb945eb217c36c546c7e8e92ee8a44f" in work_item.authority_source
    assert "workflow-coordinator-run-evidence@02d04d4a4f81748e48518e33abdbba2ac67047e4" in work_item.authority_source
    assert "Salvage preserves work, not authority" in work_item.authority_source
    assert "UNVERIFIED candidate work" in work_item.authority_source
    assert "NOT a trusted WFC H001 implementation commit" in work_item.authority_source
    assert "NOT independent review" in work_item.authority_source
    assert "NOT Slice-4 implementation acceptance" in work_item.authority_source
    assert "NOT same-Run resume" in work_item.authority_source
    assert "NOT same-Hop retry" in work_item.authority_source
    assert "does NOT treat any S4-001 salvage material as a new assurance baseline" in work_item.authority_source
    assert "execution starting point" in work_item.authority_source
    assert "not the same thing as the" in work_item.authority_source

    # No fake predecessor S4-IR findings are introduced anywhere.
    assert "S4-IR-001" not in work_item.authority_source
    assert "S4-IR-002" not in work_item.authority_source

    # WorkItem.scope is exactly Slice 4's eight production paths, nine
    # ordinary test/fixture paths, and the reused durable implementation-
    # review artifact -- identical to S4-001's, never the design FTR, Design
    # Revision, either open BUG, any earlier frozen governance bundle, the
    # unrelated adapter.py or slice4.py production files, or any
    # Slice-5-only path.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    for withheld in (
        "Makefile",
        "src",
        "governance",
        "workflow-coordinator-runtime",
        "workflow-coordinator-run-evidence",
        "project_memory/canonical",
        "src/workflow_coordinator/evidence.py",
        "src/workflow_coordinator/adapter.py",
        "src/workflow_coordinator/slice4.py",
        "tests/unit/test_slice4.py",
        "tests/unit/test_slice5b.py",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_review_001.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001_review_001.adoc",
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "docs/backlog/BUG-WFC-SELF-DEVELOPMENT-GATE-FINDING-AUTHORITY.adoc",
        "tests/architecture/test_self_development_governance.py",
        "governance/work-items/correction-convergence-escape-implementation-s4-001.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s4-001-profile.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s4-001-policy.yaml",
        "governance/work-items/correction-convergence-escape-implementation-s4-001-task.txt",
    ):
        assert not _within_scope(withheld, work_item.scope)
    for path in CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_002_SCOPE:
        assert _within_scope(path, work_item.scope)

    # implement/correct share the exact seventeen-path Slice-4 implementation
    # scope; review and rereview are independently scoped by the profile to
    # the single reused durable review artifact alone -- no phase's
    # authorized_scope grants both, and the two scopes are disjoint.
    assert (
        implement.authorized_scope
        == correct.authorized_scope
        == CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_002_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_002_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_002_SCOPE).isdisjoint(review.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_002_REVIEW_ARTIFACT not in implement.authorized_scope
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_002_REVIEW_ARTIFACT not in correct.authorized_scope

    # The reused artifact is the SAME path as every Slice-1/2/3/S4-001
    # commission's, not a fresh S4-002-suffixed review artifact; both review
    # AND rereview require, and are mapped to, it, with edit+commit
    # authority.
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_002_REVIEW_ARTIFACT
        == "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_implementation_review_001.adoc"
    )
    assert durable_artifacts == {
        "review": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_002_REVIEW_ARTIFACT,),
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_002_REVIEW_ARTIFACT,),
    }
    assert review.durable_artifact.value == "required"
    assert rereview.durable_artifact.value == "required"
    assert implement.durable_artifact.value == correct.durable_artifact.value == "none"
    assert {"edit", "commit"}.issubset(set(review.authorized_actions))
    assert {"edit", "commit"}.issubset(set(rereview.authorized_actions))

    # Review/rereview are fresh independent gates holding the identical
    # minimal resolve-only Finding authority; worker phases (implement,
    # correct) carry no Finding-disposition authority at all -- there is no
    # `review` sub-object on their profile shape at all, not merely an unused
    # one.
    assert review.review.finding_authority == ("resolve",)
    assert rereview.review.finding_authority == ("resolve",)
    assert "residual" not in review.review.finding_authority
    assert "supersede" not in review.review.finding_authority
    assert "reopen" not in review.review.finding_authority
    assert "residual" not in rereview.review.finding_authority
    assert "supersede" not in rereview.review.finding_authority
    assert "reopen" not in rereview.review.finding_authority
    assert implement.review is None
    assert correct.review is None

    # Topology: implement -> review -> {accept: complete, correct} ->
    # correct -> rereview -> {accept: complete, correct}. max_autonomous_hops
    # is 10, deliberately permitting several correction/rereview cycles while
    # remaining a ceiling.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "implement": (("review", "goto", "review"),),
        "review": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert implement.role == correct.role == "worker"
    assert review.review.context == rereview.review.context == "fresh"
    assert review.review.authority.value == "gate"
    assert rereview.review.authority.value == "gate"
    assert review.review.blocking_policy == "blocking_only"
    assert rereview.review.blocking_policy == "blocking_only"

    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    # Worker prompt/persona phase-local outcome hardening: both the task text
    # and the WorkItem persona prose state that a worker Hop is a
    # phase-completion handoff, that changes_required is reserved for
    # review/rereview verdicts (never worker partial-progress signaling), and
    # that the hop ceiling is a workflow-wide ceiling, not a worker
    # time-slicing allowance.
    worker_persona = work_item.personas[WorkflowRole.WORKER]
    assert "phase-completion handoff" in worker_persona
    assert "outcome=completed" in worker_persona
    assert "changes_required is not a worker outcome" in worker_persona
    assert "not time-slicing" in worker_persona
    assert len(worker_persona.encode("utf-8")) <= 512
    assert worker_persona.splitlines() == [worker_persona]

    reviewer_persona = work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]
    assert "changes_required is this gate's verdict" in reviewer_persona
    assert "genuine S4-002-local Run Findings" in reviewer_persona
    assert "S4-IR identifiers are durable review-artifact prose" in reviewer_persona
    assert "Finding authority disposes S4-IR identifiers" not in reviewer_persona
    assert len(reviewer_persona.encode("utf-8")) <= 512
    assert reviewer_persona.splitlines() == [reviewer_persona]

    task_text = texts["task"]
    assert "4edcea0e341e9c8669378fa299104d66aed2851f" in task_text
    assert "f08c8cb61480ec5b9e2529eebc5ae44a0463bc9e" in task_text
    assert "343f419a2cf122221dd5b7ec98be92cd61e2ec24" in task_text
    assert "a889f9a3b515b4cac600dc9f7366637a388f2e25" in task_text
    assert "NAMING COLLISIONS" in task_text
    assert "slice4.py" in task_text and "NOT authorized" in task_text
    assert "DiagnosisReport" in task_text
    assert "StrategyRevision" in task_text
    assert "StrategyAssessment" in task_text
    assert "Exact escape/diagnosis/revision correlation" in task_text
    assert "Exactly-once generation advancement" in task_text
    assert "Diagnosis read-only/non-authority enforcement" in task_text
    assert "Strategy-gate authority limits" in task_text
    assert "curriculum" in task_text.lower()
    assert "FINDING AUTHORITY" in task_text
    assert "do not repeat the S3-002 Commission defect" in task_text
    assert "finding_authority: [resolve]" in task_text
    assert "Do not implement any of the following" in task_text
    assert "Slice 5" in task_text
    assert "R1A dogfood" in task_text
    assert "NOT authorized" in task_text

    # FRESH SUCCESSOR framing: the task text states this is not a
    # continuation, not a same-Run resume, not a same-Hop retry, and not a
    # salvage act itself; it records the predecessor's frozen Run identity
    # and truthful stop reason, and it states the salvage commit's content is
    # unverified candidate material distinct from the assurance baseline.
    assert "FRESH SUCCESSOR Commission" in task_text
    assert "NOT a continuation" in task_text
    assert "NOT a same-Run resume" in task_text
    assert "NOT a same-Hop retry" in task_text
    assert "RUN-3efc7eb2aa374263a2b12954213642f1" in task_text
    assert "15102e6b17a3180e2650ac1cb2a5e4bffe3822f8" in task_text
    assert "UNVERIFIED candidate work" in task_text
    assert "Salvage preserves work, not authority" in task_text
    assert "ASSURANCE BASELINE vs EXECUTION STARTING POINT" in task_text
    assert "NOT the same thing" in task_text

    # The predecessor S4-001's own historical disposition is stated
    # truthfully (H001 implement, changes_required rejected as non-commit-
    # capable, awaiting_human, hop_used 1 of 10, no H002), never retrofitted
    # into an accepted or resumable state.
    assert "outcome=changes_required" in task_text
    assert "requested_route=review" in task_text
    assert "NOT a commit-capable ordinary implementation" in task_text
    assert "awaiting_human" in task_text
    assert "hop_used is 1 of 10" in task_text
    assert "no H002 ever occurred" in task_text
    assert "Review was never reached" in task_text
    assert "do not invent one" in task_text
    assert "exactly eight in-scope paths" in task_text
    assert "eight in-scope production paths" not in task_text

    # Critical S4-002 prompt/role hardening section is present with the
    # exact required phase-local outcome semantics.
    assert "CRITICAL S4-002 PROMPT / ROLE HARDENING" in task_text
    assert "phase-completion handoff" in task_text
    assert "NOT an incremental time slice" in task_text
    assert "do not stop merely because additional normal in-scope implementation work" in task_text
    assert "do not use another Hop as an ordinary continuation mechanism" in task_text
    assert "outcome=completed, requested_route=review" in task_text
    assert "outcome=completed,\n  requested_route=rereview" in task_text
    assert "is a REVIEW / REREVIEW VERDICT" in task_text
    assert "MUST NOT use `changes_required` to mean" in task_text
    assert "is a CEILING for the whole\nworkflow, not permission to split implementation" in task_text
    assert "must NOT encode such a stop as `changes_required`" in task_text
    assert "Do not weaken fail-closed" in task_text
    assert "does not mechanically change result-v1/result-v2\ngrammar" in task_text

    # No Slice-5/R1A/Runtime-promotion authority appears anywhere in the task
    # text as something this Commission grants.
    assert "Runtime promotion" in task_text
    assert "not launch any Run" in task_text

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-run-evidence", policy.work_item_scope_ceiling)
    assert not _within_scope("project_memory/canonical", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    # Admission happened entirely through provider-free preflight: no
    # provider/executable was ever invoked, no Runtime/evidence/state/
    # Project-Memory mutation occurred, and the state root the launch would
    # eventually use was never created by this process.
    assert not state_root.exists()


def test_correction_convergence_escape_implementation_s4_003_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Fresh successor rereview commission for the S4-002 trusted corrected candidate.

    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S4-003`` obtains a
    fresh independent rereview of the trusted corrected Slice-4 candidate
    ``b5f4aba6f0eb03ca621551e54a413844ddde18e4`` (S4-002's H003 correction).
    S4-002's own H004 rereview performed useful adversarial work -- it
    proposed ``F002 -> resolved`` and reported finding a sharper replay
    counterexample against ``F001`` -- but was mechanically preempted
    (``commit_intent=null``, ``claimed_commits=[]``) because its required
    durable artifact was never produced; see
    ``docs/backlog/BUG-WFC-PHASE-COMPLETION-CONTRACT-OPAQUENESS.adoc``. H004's
    route request and narrative verdict were therefore never admitted. This
    profile reuses the S3-003/S4-002 minimal successor-rereview topology,
    granting its ``rereview`` gate ``finding_authority: [resolve]`` and
    nothing broader. This is a FRESH successor Run: it inherits no
    predecessor Run-native Finding, mutates no S4-002 Run state, and begins
    directly in ``rereview`` with no ``implement``/``review`` phase. The
    stable cross-Run identifiers S4-IR-001/002 are durable review-artifact
    prose, never WFC Finding IDs, and must be independently dispositioned
    rather than merely carried forward from H004's non-admitted narrative.
    The admission succeeds entirely through provider-free preflight: no
    provider is invoked and no state root is created.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_003_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-implementation-s4-003"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_003_GOVERNING_PATHS,
    ))
    texts = result[4]
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    rereview = profile.phases["rereview"]
    correct = profile.phases["correct"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_003_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_003_REVIEW_ARTIFACT,
    )
    assert len(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_003_SCOPE) == 17

    # Fresh S4-003 identity, starting directly in rereview, with exactly the
    # two reachable phases and no implement/review/supervisor phase at all.
    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S4-003"
    assert work_item.initial_phase == "rereview"
    assert "implement" not in profile.phases
    assert "review" not in profile.phases
    assert tuple(profile.phases) == ("rereview", "correct")
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 12
    assert profile.profile_id == "correction-convergence-escape-implementation-s4-003"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-implementation-s4-003"

    # Accepted authority lineage pinned: original design, Design Revision
    # 001, accepted Slice 1/2/3, S4-002 H002 review, and the trusted
    # corrected candidate under rereview.
    assert "4edcea0e341e9c8669378fa299104d66aed2851f" in work_item.authority_source
    assert "f08c8cb61480ec5b9e2529eebc5ae44a0463bc9e" in work_item.authority_source
    assert "83cccb98bb800479645d87051fb1cd66b3a4ac7f" in work_item.authority_source
    assert "01a0dead992e32f6569f9a06324e5fce1b828a2d" in work_item.authority_source
    assert "a889f9a3b515b4cac600dc9f7366637a388f2e25" in work_item.authority_source
    assert "4c3257a2f445f3b17edfcd60552f0b651d225d58" in work_item.authority_source
    assert "b5f4aba6f0eb03ca621551e54a413844ddde18e4" in work_item.authority_source
    assert "trusted corrected" in work_item.authority_source

    # S4-002 H004 is represented only as non-admitted advisory evidence, not
    # accepted disposition authority: its Run/invocation identity, its
    # refused result fields, and the mechanical-preemption reason are all
    # pinned, and it is explicitly never an assurance baseline.
    assert "RUN-198ad6957b1b4dc7a0d2fe63f9e0e391" in work_item.authority_source
    assert "INV-86a73cea69f14e739d5d87ec9079de94" in work_item.authority_source
    assert "commit_intent=null" in work_item.authority_source
    assert "claimed_commits=[]" in work_item.authority_source
    assert "NON-ADMITTED execution evidence" in work_item.authority_source
    assert "never treated as an assurance baseline" in work_item.authority_source
    assert "BUG-WFC-PHASE-COMPLETION-CONTRACT-OPAQUENESS.adoc" in work_item.authority_source
    assert "f7b41445ae8271d9bc6d75659a6b83b137b84772" in work_item.authority_source

    # S4-001 salvage is explicitly never assurance authority either.
    assert "15102e6b17a3180e2650ac1cb2a5e4bffe3822f8" in work_item.authority_source
    assert "S4-001 salvage" in work_item.authority_source
    assert "execution ancestry/candidate provenance only" in work_item.authority_source
    assert "Neither S4-001 salvage nor S4-002 H004 is granted independent acceptance authority" in work_item.authority_source

    # S4-002's own Run-native Findings are explicitly NOT declared inherited
    # state: F001/F002 are named only as frozen predecessor Run-local
    # history, never as something this fresh Run resolves or inherits.
    assert "F001" in work_item.authority_source
    assert "F002" in work_item.authority_source
    assert "does NOT inherit S4-002's Run-native Findings" in work_item.authority_source
    assert "frozen, historical Run-local truth" in work_item.authority_source
    assert "begins with no inherited predecessor Findings at all" in work_item.authority_source

    # Slice 4/5/R1A withheld; no Runtime/push/evidence authority; the new
    # phase-completion-opaqueness BUG is explicitly out of mutation scope and
    # grants no implementation authority here.
    assert "Slice 5" in work_item.authority_source
    assert "R1A dogfood" in work_item.authority_source
    assert "does not promote workflow-coordinator-runtime" in work_item.authority_source
    assert "does not push" in work_item.authority_source
    assert "does not mutate the workflow-coordinator-run-evidence repository" in work_item.authority_source
    assert "does not launch any Run or invoke any provider" in work_item.authority_source
    assert "does not authorize fixing it" in work_item.authority_source

    # WorkItem.scope is exactly the same seventeen Slice-4 paths plus the
    # reused durable review artifact -- disjoint phase scopes, same union as
    # S4-001/S4-002.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    for withheld in (
        "Makefile",
        "src",
        "governance",
        "workflow-coordinator-runtime",
        "workflow-coordinator-run-evidence",
        "project_memory/canonical",
        "src/workflow_coordinator/adapter.py",
        "src/workflow_coordinator/slice4.py",
        "tests/unit/test_slice4.py",
        "tests/unit/test_slice5b.py",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001.adoc",
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "docs/backlog/BUG-WFC-SELF-DEVELOPMENT-GATE-FINDING-AUTHORITY.adoc",
        "docs/backlog/BUG-WFC-PHASE-COMPLETION-CONTRACT-OPAQUENESS.adoc",
        "tests/architecture/test_self_development_governance.py",
        "governance/work-items/correction-convergence-escape-implementation-s4-002.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s4-002-profile.yaml",
        "governance/self-development/correction-convergence-escape-implementation-s4-002-policy.yaml",
        "governance/work-items/correction-convergence-escape-implementation-s4-002-task.txt",
    ):
        assert not _within_scope(withheld, work_item.scope)
    for path in CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_003_SCOPE:
        assert _within_scope(path, work_item.scope)

    # correct carries the exact seventeen-path Slice-4 correction scope;
    # rereview is independently scoped to the single reused durable review
    # artifact alone -- the two scopes are disjoint.
    assert correct.authorized_scope == CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_003_SCOPE
    assert rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_003_REVIEW_ARTIFACT,
    )
    assert set(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_003_SCOPE).isdisjoint(rereview.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_003_REVIEW_ARTIFACT not in correct.authorized_scope
    assert (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_003_REVIEW_ARTIFACT
        == "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_implementation_review_001.adoc"
    )
    assert durable_artifacts == {
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_003_REVIEW_ARTIFACT,),
    }
    assert _within_scope(
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_003_REVIEW_ARTIFACT, rereview.authorized_scope
    )
    assert rereview.durable_artifact.value == "required"
    assert correct.durable_artifact.value == "none"
    assert {"review", "edit", "commit"}.issubset(set(rereview.authorized_actions))
    assert {"edit", "commit"}.issubset(set(correct.authorized_actions))

    # The core of this Commission: rereview is gate/fresh/blocking-only with
    # resolve-only Finding authority -- no residual, supersede, reopen, or
    # human authority granted.
    assert rereview.role == "independent_reviewer"
    assert correct.role == "worker"
    assert rereview.review.context == "fresh"
    assert rereview.review.authority.value == "gate"
    assert rereview.review.blocking_policy == "blocking_only"
    assert rereview.review.finding_authority == ("resolve",)
    assert "residual" not in rereview.review.finding_authority
    assert "supersede" not in rereview.review.finding_authority
    assert "reopen" not in rereview.review.finding_authority

    # Topology: rereview -> {accept: complete, correct} -> correct ->
    # rereview. No implement/review/supervisor phase was added, and
    # max_autonomous_hops is a 12-Hop ceiling, not a target -- deliberately
    # larger headroom than S3-003's 10 for autonomous correction/rereview
    # convergence, never permission to weaken convergence guards.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
        "correct": (("rereview", "goto", "rereview"),),
    }

    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    task_text = texts["task"]
    assert "FRESH" in task_text and "same-Run resume of S4-002" in task_text
    assert "b5f4aba6f0eb03ca621551e54a413844ddde18e4" in task_text
    assert "RUN-198ad6957b1b4dc7a0d2fe63f9e0e391" in task_text
    assert "INV-86a73cea69f14e739d5d87ec9079de94" in task_text
    assert "CRITICAL SUCCESSOR-IDENTITY RULE" in task_text
    assert "does NOT inherit S4-002's Run-native\nFindings" in task_text
    assert "S4-IR-001" in task_text and "S4-IR-002" in task_text
    assert "finding_authority: [resolve]" in task_text
    assert "not residual, not supersede, not reopen, not human" in task_text

    # The mandatory falsification hint: H004's counterexample is presented as
    # a hint to investigate, not a fact to cite, and the reviewer must
    # independently reproduce or falsify it.
    assert "forged persisted Run-v3 history" in task_text
    assert "two simultaneously\n  unconsumed convergence escapes" in task_text
    assert "Independently reproduce or falsify" in task_text
    assert "not a fact to cite" in task_text

    # Critical Finding-identity distinction, carried from S3-003: S4-IR-001/
    # 002 are durable review-artifact prose identifiers dispositioned by
    # writing into that artifact, never WFC Finding-ledger `resolve`
    # operations. An empty Findings delta is explicitly permitted on
    # acceptance, and creating a synthetic S4-003 Finding merely to mirror a
    # historical S4-IR identifier is explicitly forbidden.
    assert "durable review-artifact prose identifiers, not WFC" in task_text
    assert "never a WFC Finding-ledger `resolve` operation" in task_text
    assert "EMPTY S4-003 WFC Findings delta" in task_text
    assert "you must NOT create a\nsynthetic S4-003" in task_text
    assert "merely to mirror a historical S4-IR\nidentifier" in task_text
    assert "exists purely" in task_text and "defensively" in task_text
    assert "genuine S4-003-local Finding\nmay ever be the" in task_text
    assert "never operates on S4-002's F001/F002" in task_text

    # H004 non-admission is explained in the task text with the exact
    # mechanical-preemption reason, never presented as authoritative.
    assert "NON-AUTHORITATIVE advisory evidence" in task_text
    assert "mechanical_preemption:required durable artifact is missing" in task_text
    assert "You must NOT simply adopt H004's narrative verdict as authoritative" in task_text

    # Slice 4/5/R1A withheld; the new phase-completion-opaqueness BUG is read
    # as context only and is never implementation scope.
    assert "Slice 5" in task_text
    assert "R1A dogfood" in task_text
    assert "NOT authorized" in task_text
    assert "BUG-WFC-SELF-DEVELOPMENT-GATE-FINDING-AUTHORITY.adoc" in task_text
    assert "BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc" in task_text
    assert "BUG-WFC-PHASE-COMPLETION-CONTRACT-OPAQUENESS.adoc" in task_text
    assert "is NOT Slice-4 implementation authority" in task_text
    assert "remains\n    independently OPEN" in task_text

    # Phase-completion-contract hardening: an explicit rereview phase
    # contract for accept/changes_required, and the exact prohibition on the
    # S4-002 H004 failure mode.
    assert "ROLE / RESULT SEMANTICS UNDER THE CURRENT MECHANISM" in task_text
    assert "commit_intent=null` is NOT a valid phase-completion\n  handoff for a route-eligible rereview" in task_text
    assert "CommitIntent MUST represent\n  exactly that required durable artifact" in task_text
    assert "do not repeat it" in task_text

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-run-evidence", policy.work_item_scope_ceiling)
    assert not _within_scope("project_memory/canonical", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    # Admission happened entirely through provider-free preflight: no
    # provider/executable was ever invoked, no Runtime/Evidence/STATE/OPS
    # mutation occurred, and the state root the launch would eventually use
    # was never created by this process.
    assert not state_root.exists()


def test_correction_convergence_escape_implementation_s4_004_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Bounded post-acceptance correction commission against later external assurance.

    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S4-004`` corrects
    the accepted Slice-4 implementation against the later independent
    external assurance review (OPUS-S4-001 blocking, OPUS-S4-002 blocking
    assurance defect, OPUS-S4-003 non-blocking), then obtains a fresh
    independent rereview, looping ``correct -> rereview -> {accept,
    correct}``. Unlike the rereview-first S4-003 successor, this Commission
    starts directly in ``correct`` because the external assurance review
    found a concrete correction obligation up front. ``correct`` carries no
    durable artifact and no Finding-disposition authority; ``rereview`` is
    gate/fresh/blocking-only with resolve-only Finding authority, scoped to
    the single shared durable implementation-review artifact. The admission
    succeeds entirely through provider-free preflight: no provider is
    invoked and no state root is created.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_004_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-implementation-s4-004"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_004_GOVERNING_PATHS,
    ))
    texts = result[4]
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_004_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_004_REVIEW_ARTIFACT,
    )
    assert len(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_004_SCOPE) == 17

    # (1)/(2)/(3) exact identity, initial phase, and Hop ceiling.
    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S4-004"
    assert profile.profile_id == "correction-convergence-escape-implementation-s4-004"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-implementation-s4-004"
    assert work_item.initial_phase == "correct"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 16

    # (4) exact phase order / topology: correct and rereview only.
    assert "implement" not in profile.phases
    assert "review" not in profile.phases
    assert "diagnosis" not in profile.phases
    assert "reframe" not in profile.phases
    assert "strategy_gate" not in profile.phases
    assert tuple(profile.phases) == ("correct", "rereview")

    # (5) exact routes: correct -> rereview; rereview accept -> complete;
    # rereview correct -> correct.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }

    # (6) correct phase: worker role; exact seventeen-path scope; edit+commit
    # only; no review descriptor; no Finding disposition authority; no
    # durable artifact.
    assert correct.role == "worker"
    assert correct.authorized_scope == CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_004_SCOPE
    assert set(correct.authorized_actions) == {"edit", "commit"}
    assert correct.review is None
    assert correct.durable_artifact.value == "none"

    # (7) rereview phase: independent_reviewer; fresh context; gate;
    # blocking_only; finding_authority: [resolve]; exact single durable
    # artifact scope; review/edit/commit; no source/test mutation authority.
    assert rereview.role == "independent_reviewer"
    assert rereview.review.context == "fresh"
    assert rereview.review.authority.value == "gate"
    assert rereview.review.blocking_policy == "blocking_only"
    assert rereview.review.finding_authority == ("resolve",)
    assert "residual" not in rereview.review.finding_authority
    assert "supersede" not in rereview.review.finding_authority
    assert "reopen" not in rereview.review.finding_authority
    assert rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_004_REVIEW_ARTIFACT,
    )
    assert {"review", "edit", "commit"}.issubset(set(rereview.authorized_actions))
    assert rereview.durable_artifact.value == "required"
    assert not set(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_004_SCOPE) & set(rereview.authorized_scope)

    # (8) phase scopes are disjoint.
    assert set(correct.authorized_scope).isdisjoint(rereview.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_004_REVIEW_ARTIFACT not in correct.authorized_scope

    # (9) WorkItem / policy scope ceiling is exactly the union of the
    # seventeen correction paths and the one durable review artifact.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert durable_artifacts == {
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_004_REVIEW_ARTIFACT,),
    }
    for path in CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_004_SCOPE:
        assert _within_scope(path, work_item.scope)
    for withheld in (
        "Makefile",
        "src",
        "governance",
        "workflow-coordinator-runtime",
        "workflow-coordinator-run-evidence",
        "project_memory/canonical",
        "src/workflow_coordinator/adapter.py",
        "src/workflow_coordinator/slice4.py",
        "tests/unit/test_slice4.py",
        "tests/unit/test_slice5b.py",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_slice4_post_acceptance_assurance_review_001.adoc",
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "docs/backlog/BUG-WFC-SELF-DEVELOPMENT-GATE-FINDING-AUTHORITY.adoc",
        "docs/backlog/BUG-WFC-PHASE-COMPLETION-CONTRACT-OPAQUENESS.adoc",
        "tests/architecture/test_self_development_governance.py",
        "governance/work-items/correction-convergence-escape-implementation-s4-003.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # (10) authority text pins the required lineage and distinctions.
    assert "4edcea0e341e9c8669378fa299104d66aed2851f" in work_item.authority_source
    assert "f08c8cb61480ec5b9e2529eebc5ae44a0463bc9e" in work_item.authority_source
    assert "83cccb98bb800479645d87051fb1cd66b3a4ac7f" in work_item.authority_source
    assert "01a0dead992e32f6569f9a06324e5fce1b828a2d" in work_item.authority_source
    assert "a889f9a3b515b4cac600dc9f7366637a388f2e25" in work_item.authority_source
    assert "ea45e564dac1fb51624c6ed0aacb8c5dc4e556f1" in work_item.authority_source
    assert "2845573c253e9019db701a8c12ab04c7625f042c" in work_item.authority_source
    assert "37aa97fb66a6703123b2076e22d1bc50c024ad39" in work_item.authority_source
    assert "OPUS-S4-001" in work_item.authority_source
    assert "OPUS-S4-002" in work_item.authority_source
    assert "OPUS-S4-003" in work_item.authority_source
    assert "OPUS-S4-004" in work_item.authority_source
    assert "OPUS-S4-005" in work_item.authority_source
    assert "NOT correction targets" in work_item.authority_source or "OUT OF" in work_item.authority_source
    assert "does NOT authorize projected-schema or Phase-Completion-Contract work" in work_item.authority_source
    assert "Historical acceptance and current assurance sufficiency are distinct" in work_item.authority_source
    assert "H005 historically accepted Slice 4" in work_item.authority_source
    assert "That historical acceptance happened and is NOT rewritten" in work_item.authority_source

    # (11) explicit withheld-authority list.
    assert "does NOT authorize Runtime promotion" in work_item.authority_source
    assert "mutation of" in work_item.authority_source and "workflow-coordinator-runtime" in work_item.authority_source
    assert "Slice 5" in work_item.authority_source
    assert "R1A dogfood" in work_item.authority_source
    assert "foreign-domain falsification" in work_item.authority_source
    assert "push" in work_item.authority_source
    assert "Evidence mutation" in work_item.authority_source
    assert "Project Memory mutation" in work_item.authority_source
    assert "OPS/STATE mutation" in work_item.authority_source
    assert "another nested Run" in work_item.authority_source
    assert "automatic provider fallback" in work_item.authority_source
    assert "Claude adapter work" in work_item.authority_source
    assert "Operator Harness implementation" in work_item.authority_source
    assert "projected phase-specific result contracts" in work_item.authority_source
    assert "Coordinator-owned effect redesign" in work_item.authority_source
    assert "Commission-generation redesign" in work_item.authority_source
    assert "hard-hop-ceiling configuration work" in work_item.authority_source
    assert "correction of OPUS-S4-004" in work_item.authority_source
    assert "redesign motivated by OPUS-S4-005" in work_item.authority_source
    assert "closure of any of the three currently" in work_item.authority_source

    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    task_text = texts["task"]

    # (10)/(11) the same pins and withheld-authority items must also be
    # legible in the operational task text, not only in the dense
    # authority_source narrative.
    assert "4edcea0e341e9c8669378fa299104d66aed2851f" in task_text
    assert "f08c8cb61480ec5b9e2529eebc5ae44a0463bc9e" in task_text
    assert "ea45e564dac1fb51624c6ed0aacb8c5dc4e556f1" in task_text
    assert "2845573c253e9019db701a8c12ab04c7625f042c" in task_text
    assert "37aa97fb66a6703123b2076e22d1bc50c024ad39" in task_text
    assert "Verdict: C -- MATERIAL BLOCKING DEFECT FOUND" in task_text
    assert "historical acceptance happened and MUST NOT be rewritten" in task_text
    assert "a reopening of S4-003 Run-native F001/F002" in task_text
    assert "a claim that S4-003 H005 never accepted Slice 4" in task_text

    # The required negative-regression classes (A-D) for OPUS-S4-001 and the
    # twelve positive-assurance obligations for OPUS-S4-002 are present.
    assert "OPUS-S4-001 (BLOCKING)" in task_text
    assert "Strategy records relabelled onto phases/roles" in task_text
    assert "supervisor_judgment_required" in task_text
    assert "autonomous_hop_budget_exhausted" in task_text
    assert "A triggering Finding persisted outside the required open/blocking/" in task_text
    assert "OPUS-S4-002 (BLOCKING ASSURANCE DEFECT)" in task_text
    assert "tests/support.py:result()" in task_text
    assert "a valid live complete chain" in task_text
    assert "qualified-cycle count resets correctly" in task_text

    # OPUS-S4-003 is bounded and explicitly not promoted to blocking, with an
    # explicit non-silent-disappearance requirement.
    assert "OPUS-S4-003 (NON-BLOCKING)" in task_text
    assert "is not promoted to a blocking acceptance condition merely by\nthis" in task_text
    assert "It must\nnot be silently omitted from the rereview disposition." in task_text

    # OPUS-S4-004/005 are confirmed out of correction scope.
    assert "OUT OF CORRECTION SCOPE" in task_text
    assert "OPUS-S4-004 -- misleading pre-existing" in task_text
    assert "does NOT\nauthorize projected-schema work in this Run" in task_text

    # Historical H005 vs. later-assurance distinction, and the historical
    # S4-IR disposition-under-challenge table, are legible verbatim.
    assert "S4-IR-001 CLOSED,\nS4-IR-002 CLOSED, S4-IR-003 CLOSED, S4-IR-004 CLOSED" in task_text
    assert "historical closure assurance falsified by later evidence" in task_text
    assert "substantive closure survives, but part of the cited closure" in task_text

    # Open-BUG verify-not-close boundary.
    assert "BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc" in task_text
    assert "BUG-WFC-SELF-DEVELOPMENT-GATE-FINDING-AUTHORITY.adoc" in task_text
    assert "BUG-WFC-PHASE-COMPLETION-CONTRACT-OPAQUENESS.adoc" in task_text
    assert "verify status only; close none" in task_text

    # Withheld scope restated operationally in the task text.
    assert "Runtime promotion, Slice 5, R1A\ndogfood" in task_text or "Slice 5" in task_text
    assert "the Operator Harness, projected phase-specific" in task_text
    assert "Commission generation, or Runtime hard-hop-ceiling" in task_text

    # Phase-completion-contract hardening against the exact S4-002 H004
    # failure mode is carried forward unchanged into S4-004.
    assert "ROLE / RESULT SEMANTICS UNDER THE CURRENT MECHANISM" in task_text
    assert "`changes_required` is NOT a valid worker outcome" in task_text
    assert "a route-eligible\nrereview result with no required durable-artifact commit is NOT a valid\nphase-completion handoff" in task_text

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-run-evidence", policy.work_item_scope_ceiling)
    assert not _within_scope("project_memory/canonical", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    # (12) preflight remains entirely provider-free and creates no state
    # root: no provider/executable was invoked, no Runtime/Evidence/STATE/
    # OPS mutation occurred, and the state root a later launch would use was
    # never created by this process.
    assert not state_root.exists()


def test_correction_convergence_escape_implementation_s4_005_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Narrow successor correcting the durable S4-004 H006 replay defect.

    ``WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S4-005`` is a
    fresh successor to the frozen, never-resumed S4-004 Run
    (``RUN-fa85b0a2637147b19784e22ec1c7fd21``, ``awaiting_human``). It acts
    only on S4-004 H006's durable committed review evidence, not on H006's
    rejected structured Finding delta, and inherits no Run-native Finding
    ledger (S4-004 F002 is not an S4-005 Finding identity). Unlike S4-004,
    this Commission's scope is exactly two correction paths
    (``src/workflow_coordinator/reducer.py``,
    ``tests/unit/test_reducer.py``) plus the one shared durable review
    artifact. ``correct`` carries no durable artifact and no
    Finding-disposition authority; ``rereview`` is gate/fresh/blocking-only
    with resolve-only Finding authority. The admission succeeds entirely
    through provider-free preflight: no provider is invoked and no state
    root is created.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_005_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "correction-convergence-escape-implementation-s4-005"

    assert not state_root.exists()
    result = _preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_005_GOVERNING_PATHS,
    ))
    texts = result[4]
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    correct = profile.phases["correct"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_005_SCOPE,
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_005_REVIEW_ARTIFACT,
    )
    assert len(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_005_SCOPE) == 2

    # (1) exact S4-005 WorkItem/profile/policy identities.
    assert work_item.work_item_id == "WI-WFC-CORRECTION-CONVERGENCE-ESCAPE-IMPLEMENTATION-S4-005"
    assert profile.profile_id == "correction-convergence-escape-implementation-s4-005"
    assert policy.policy_id == "workflow-coordinator-correction-convergence-escape-implementation-s4-005"

    # (2)/(3) initial phase and Hop ceiling.
    assert work_item.initial_phase == "correct"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 16

    # (4) exact topology: correct and rereview only, no inherited diagnosis/
    # reframe/strategy-gate workflow phase, no supervisor, no child Run shape.
    assert "implement" not in profile.phases
    assert "review" not in profile.phases
    assert "diagnosis" not in profile.phases
    assert "reframe" not in profile.phases
    assert "strategy_gate" not in profile.phases
    assert tuple(profile.phases) == ("correct", "rereview")

    # (4) exact routes: correct -> rereview; rereview accept -> complete;
    # rereview correct -> correct.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "correct": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correct", "goto", "correct"),
        ),
    }

    # (5) correct phase: worker; exactly two authorized paths; edit+commit
    # only; no review descriptor; no Finding disposition authority; no
    # durable artifact.
    assert correct.role == "worker"
    assert correct.authorized_scope == CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_005_SCOPE
    assert set(correct.authorized_actions) == {"edit", "commit"}
    assert correct.review is None
    assert correct.durable_artifact.value == "none"

    # (6) rereview: independent reviewer; fresh context; gate; blocking_only;
    # resolve-only Finding authority; exact one-path durable artifact scope.
    assert rereview.role == "independent_reviewer"
    assert rereview.review.context == "fresh"
    assert rereview.review.authority.value == "gate"
    assert rereview.review.blocking_policy == "blocking_only"
    assert rereview.review.finding_authority == ("resolve",)
    assert "residual" not in rereview.review.finding_authority
    assert "supersede" not in rereview.review.finding_authority
    assert "reopen" not in rereview.review.finding_authority
    assert rereview.authorized_scope == (
        CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_005_REVIEW_ARTIFACT,
    )
    assert {"review", "edit", "commit"}.issubset(set(rereview.authorized_actions))
    assert rereview.durable_artifact.value == "required"
    assert not set(CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_005_SCOPE) & set(rereview.authorized_scope)

    # (7) scopes are disjoint.
    assert set(correct.authorized_scope).isdisjoint(rereview.authorized_scope)
    assert CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_005_REVIEW_ARTIFACT not in correct.authorized_scope

    # (8) WorkItem/policy scope ceiling is exactly the three-path union.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert durable_artifacts == {
        "rereview": (CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_005_REVIEW_ARTIFACT,),
    }
    for path in CORRECTION_CONVERGENCE_ESCAPE_IMPLEMENTATION_S4_005_SCOPE:
        assert _within_scope(path, work_item.scope)
    for withheld in (
        "Makefile",
        "src",
        "governance",
        "workflow-coordinator-runtime",
        "workflow-coordinator-run-evidence",
        "project_memory/canonical",
        "src/workflow_coordinator/model.py",
        "src/workflow_coordinator/serialization.py",
        "src/workflow_coordinator/persistence.py",
        "src/workflow_coordinator/prompt.py",
        "src/workflow_coordinator/vertical.py",
        "src/workflow_coordinator/operator.py",
        "src/workflow_coordinator/codex_adapter.py",
        "tests/support.py",
        "tests/unit/test_slice4.py",
        "tests/unit/test_slice5b.py",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_design_revision_001.adoc",
        "docs/backlog/FTR-WFC-CORRECTION-CONVERGENCE-ESCAPE_slice4_post_acceptance_assurance_review_001.adoc",
        "docs/backlog/BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc",
        "docs/backlog/BUG-WFC-SELF-DEVELOPMENT-GATE-FINDING-AUTHORITY.adoc",
        "docs/backlog/BUG-WFC-PHASE-COMPLETION-CONTRACT-OPAQUENESS.adoc",
        "tests/architecture/test_self_development_governance.py",
        "governance/work-items/correction-convergence-escape-implementation-s4-004.yaml",
    ):
        assert not _within_scope(withheld, work_item.scope)

    # (9) authority text distinguishes the frozen S4-004 Run, H006 durable
    # evidence, H006's non-admitted result, and the fresh S4-005
    # authorization.
    assert "RUN-fa85b0a2637147b19784e22ec1c7fd21" in work_item.authority_source
    assert "awaiting_human" in work_item.authority_source
    assert "mechanical_preemption:invalid_finding_delta" in work_item.authority_source
    assert "71fa97e5998f1a9f1c6fe6d736785b181a40f5b6" in work_item.authority_source
    assert "H006 DURABLE EVIDENCE VERSUS H006 NON-ADMITTED RESULT" in work_item.authority_source
    assert "was NOT admitted by the trusted reducer" in work_item.authority_source
    assert "THIS COMMISSION is the maintainer authorization" in work_item.authority_source
    assert "hop_used = 8" in work_item.authority_source
    assert "hop_limit = 12" in work_item.authority_source

    # (10) S4-004 F002 is not inherited as an S4-005 Run-native identity.
    assert "NOT inheritance of S4-004 F002 as a Run-native Finding" in work_item.authority_source
    assert "F002 is NOT inherited as" in work_item.authority_source
    assert "no inherited Run-native Finding ledger" in work_item.authority_source

    # (11) OPUS-S4-001 remains the current blocking target while
    # OPUS-S4-002/003 are regression obligations, not reopened work.
    assert "OPUS-S4-001 remains OPEN/BLOCKING" in work_item.authority_source
    assert "OPUS-S4-002 and OPUS-S4-003 are CLOSED/independently reconfirmed" in work_item.authority_source
    assert "regression obligations" in work_item.authority_source

    # (12) projected-contract/Harness/Runtime-promotion/Slice-5 work is
    # explicitly withheld.
    assert "does NOT authorize Runtime promotion" in work_item.authority_source
    assert "Slice 5" in work_item.authority_source
    assert "R1A dogfood" in work_item.authority_source
    assert "Phase-Completion projected" in work_item.authority_source
    assert "Coordinator-owned effect redesign" in work_item.authority_source
    assert "the Operator Harness" in work_item.authority_source
    assert "Commission-generation redesign" in work_item.authority_source
    assert "configurable Runtime hop ceiling" in work_item.authority_source
    assert "correction of OPUS-S4-004" in work_item.authority_source
    assert "redesign motivated by OPUS-S4-005" in work_item.authority_source
    assert "closure of any of the three currently open BUG records" in work_item.authority_source
    assert "push" in work_item.authority_source
    assert "Evidence mutation" in work_item.authority_source
    assert "Project Memory mutation" in work_item.authority_source

    assert work_item.personas[WorkflowRole.WORKER] != work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]

    task_text = texts["task"]

    # The same binding distinctions must be legible in the operational task
    # text, not only in the dense authority_source narrative.
    assert "RUN-fa85b0a2637147b19784e22ec1c7fd21" in task_text
    assert "85760901c93cb65bc2bac538094f79047fa30686" in task_text
    assert "mechanical_preemption:invalid_finding_delta:Finding F002" in task_text
    assert "was NOT admitted by the trusted reducer" in task_text
    assert "H006's `correct` route was therefore never dispatched" in task_text
    assert "F002 is frozen inside the predecessor" in task_text
    assert "hop_used = 8" in task_text and "hop_limit = 12" in task_text

    # Required correction invariant: general mechanical-state equivalence,
    # not a one-counterexample patch, and no prescribed implementation
    # predicate.
    assert "Do NOT commission another literal one-counterexample patch" in task_text
    assert "not itself sufficient authority" in task_text
    assert "do NOT blindly prescribe only `hop_used ==\nhop_limit`" in task_text

    # Mandatory regressions (1)-(5).
    assert "1. VALID FINAL-HOP CASE." in task_text
    assert "2. H006 FORGED PREMATURE CASE." in task_text
    assert "3. GENERAL MECHANICAL-CONSISTENCY CHALLENGE." in task_text
    assert "4. EXISTING REPLAY-AUTHORITY REGRESSIONS." in task_text
    assert "5. EXISTING POSITIVE ASSURANCE." in task_text

    # Exact two-path correction scope / one-path rereview scope.
    assert (
        "  src/workflow_coordinator/reducer.py\n  tests/unit/test_reducer.py\n\n"
        "No other source or test path is authorized."
    ) in task_text
    assert "model.py,\nserialization.py, persistence.py, prompt.py, vertical.py, operator.py,\ncodex_adapter.py, and tests/support.py are NOT authorized" in task_text

    # Acceptance boundary enumerates the ten required establishments.
    for item in (
        "1. The legitimate live-produced final-Hop",
        "2. The H006 forged premature budget-preemption case is rejected",
        "3. The correction establishes the GENERAL live/replay",
        "4. OPUS-S4-001 can now be dispositioned CLOSED",
        "5. OPUS-S4-002 remains CLOSED.",
        "6. OPUS-S4-003 remains CLOSED.",
        "7. Historical surviving S4-IR invariants remain intact.",
        "8. Existing positive strategy-chain assurance remains exercised.",
        "9. Full trusted repository tests pass.",
        "10. No regression occurs in accepted Slices 1-3",
    ):
        assert item in task_text

    # Open-BUG verify-not-close boundary.
    assert "BUG-WFC-CORRECTION-BLOCKING-SET-CARRYOVER.adoc" in task_text
    assert "BUG-WFC-SELF-DEVELOPMENT-GATE-FINDING-AUTHORITY.adoc" in task_text
    assert "BUG-WFC-PHASE-COMPLETION-CONTRACT-OPAQUENESS.adoc" in task_text
    assert "verify status only; close none" in task_text

    # No provider-capable preparation or state allocation is implied by the
    # task text itself (no Hop/launch/OPS/STATE directives in the commission
    # text; this governance test independently re-confirms provider-freedom
    # below via the preflight result).
    assert "launch.sh" not in task_text
    assert "wfc-ops" not in task_text
    assert "wfc-state" not in task_text

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-run-evidence", policy.work_item_scope_ceiling)
    assert not _within_scope("project_memory/canonical", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    # (13) preflight remains entirely provider-free and creates no state
    # root: no provider/executable was invoked, no Runtime/Evidence/STATE/
    # OPS mutation occurred, and the state root a later launch would use was
    # never created by this process.
    assert not state_root.exists()


def test_production_preflight_rejects_consumed_isolated_state_root(
    tmp_path: Path,
    preflight_python_path: None,
):
    repository, expected_head = _materialize_bundle(tmp_path)
    state_root = tmp_path / "external-state" / "consumed-run"
    state_root.mkdir(parents=True)

    with pytest.raises(
        OperatorPreflightError,
        match="state root already exists; one-shot operator roots are never overwritten",
    ):
        _production_preflight(_preflight_args(repository, expected_head, state_root))


def test_work_item_mapping_cannot_enlarge_review_phase_authority(
    tmp_path: Path,
    preflight_python_path: None,
):
    outside_artifact = "docs/backlog/outside_wfc_review.adoc"
    work_item = yaml.safe_load(_current_text(GOVERNING_PATHS[0]))
    work_item["scope"].append(outside_artifact)
    work_item["durable_artifacts"]["review"] = [outside_artifact]
    policy = yaml.safe_load(_current_text(GOVERNING_PATHS[2]))
    policy["work_item_scope_ceiling"].append(outside_artifact)
    replacements = {
        GOVERNING_PATHS[0]: yaml.safe_dump(work_item, sort_keys=False),
        GOVERNING_PATHS[2]: yaml.safe_dump(policy, sort_keys=False),
    }
    repository, expected_head = _materialize_bundle(
        tmp_path,
        replacements=replacements,
    )
    state_root = tmp_path / "external-state" / "rejected-run"

    with pytest.raises(
        OperatorPreflightError,
        match="durable artifact for phase review is outside WorkItem or phase scope",
    ):
        _production_preflight(_preflight_args(repository, expected_head, state_root))

    assert not state_root.exists()


PROJECTED_RESULT_CONTRACTS_P3_002_GOVERNING_PATHS = (
    "governance/work-items/projected-result-contracts-p3-002.yaml",
    "governance/self-development/projected-result-contracts-p3-002-profile.yaml",
    "governance/self-development/projected-result-contracts-p3-002-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/projected-result-contracts-p3-002-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
PROJECTED_RESULT_CONTRACTS_P3_002_IMPLEMENTATION_SCOPE = (
    "src/workflow_coordinator/model.py",
    "src/workflow_coordinator/serialization.py",
    "src/workflow_coordinator/adapter.py",
    "src/workflow_coordinator/codex_adapter.py",
    "src/workflow_coordinator/prompt.py",
    "src/workflow_coordinator/reducer.py",
    "tests/support.py",
    "tests/unit/test_reducer.py",
    "tests/unit/test_serialization.py",
    "tests/unit/test_codex_adapter_conformance.py",
    "tests/unit/test_slice3_vertical.py",
    "tests/unit/test_slice5b.py",
    "tests/unit/test_slice4.py",
    "tests/unit/test_a3_operating_contract.py",
    "tests/architecture/test_replays.py",
)
PROJECTED_RESULT_CONTRACTS_P3_002_REVIEW_ARTIFACT = (
    "docs/backlog/FTR-WFC-PROJECTED-RESULT-CONTRACTS_implementation_review_008.adoc"
)


def test_projected_result_contracts_p3_002_successor_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """The accepted P3 correction has a fresh, narrow, runnable successor.

    The stopped predecessor P3 Run is historical and its preserved H001 ref
    is untrusted salvage.  This test proves only the new bundle's provider-free
    launch admission and exact authority boundary; it neither launches a Run
    nor evaluates the candidate salvage.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=PROJECTED_RESULT_CONTRACTS_P3_002_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "projected-result-contracts-p3-002"

    assert not state_root.exists()
    result = _production_preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=PROJECTED_RESULT_CONTRACTS_P3_002_GOVERNING_PATHS,
    ))
    texts = result[4]
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implement = profile.phases["implement"]
    review = profile.phases["independent_review"]
    correction = profile.phases["correction"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *PROJECTED_RESULT_CONTRACTS_P3_002_IMPLEMENTATION_SCOPE,
        PROJECTED_RESULT_CONTRACTS_P3_002_REVIEW_ARTIFACT,
    )
    assert work_item.work_item_id == "WI-WFC-PROJECTED-RESULT-CONTRACTS-P3-002"
    assert profile.profile_id == "projected-result-contracts-p3-002"
    assert policy.policy_id == "workflow-coordinator-projected-result-contracts-p3-002"
    assert work_item.initial_phase == "implement"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 8
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert tuple(profile.phases) == (
        "implement", "independent_review", "correction", "rereview",
    )
    assert implement.authorized_scope == correction.authorized_scope == (
        PROJECTED_RESULT_CONTRACTS_P3_002_IMPLEMENTATION_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        PROJECTED_RESULT_CONTRACTS_P3_002_REVIEW_ARTIFACT,
    )
    assert durable_artifacts == {
        "independent_review": (PROJECTED_RESULT_CONTRACTS_P3_002_REVIEW_ARTIFACT,),
        "rereview": (PROJECTED_RESULT_CONTRACTS_P3_002_REVIEW_ARTIFACT,),
    }

    # The accepted correction admits only the two P3 result families at v3.
    # The two newly necessary provider-neutral seams and their direct test
    # homes are present; generic routing, route selection, governance tests,
    # and all later roadmap work remain outside the implementation authority.
    for required in (
        "src/workflow_coordinator/adapter.py",
        "src/workflow_coordinator/prompt.py",
        "tests/unit/test_slice4.py",
        "tests/unit/test_a3_operating_contract.py",
    ):
        assert required in PROJECTED_RESULT_CONTRACTS_P3_002_IMPLEMENTATION_SCOPE
    for withheld in (
        "src/workflow_coordinator/vertical.py",
        "tests/architecture/test_self_development_governance.py",
        "governance",
        "workflow-coordinator-runtime",
    ):
        assert not _within_scope(withheld, work_item.scope)

    assert review.review.finding_authority == rereview.review.finding_authority == ("resolve",)
    assert review.review.failed_evidence_route == rereview.review.failed_evidence_route == "correction"
    assert tuple(result[13]) == ("pytest",)

    task_text = texts["task"]
    for required_text in (
        "RUN-8875175a2ea343adbaf47c105699f91e",
        "refs/wfc/candidates/RUN-8875175a2ea343adbaf47c105699f91e/H001",
        "UNTRUSTED SALVAGE",
        "(GENERIC_ROUTING_RESULT, 3)",
        "P3.5",
        "pairless replay regression",
    ):
        assert required_text in task_text
    assert "A -- CORRECTIONS ACCEPTED" in work_item.authority_source
    assert "no authority, acceptance, review" in work_item.authority_source
    assert not state_root.exists()


DOCS_ARCHITECTURE_CONSOLIDATION_DESIGN_001_GOVERNING_PATHS = (
    "governance/work-items/docs-architecture-consolidation-design-001.yaml",
    "governance/self-development/docs-architecture-consolidation-design-001-profile.yaml",
    "governance/self-development/docs-architecture-consolidation-design-001-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/docs-architecture-consolidation-design-001-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
DOCS_ARCHITECTURE_CONSOLIDATION_DESIGN_001_SCOPE = (
    "docs/warm_storage/concepts/workflow_coordinator_documentation_information_architecture_consolidation_2026-10-15.adoc",
)
DOCS_ARCHITECTURE_CONSOLIDATION_DESIGN_001_REVIEW_ARTIFACT = (
    "docs/warm_storage/concepts/workflow_coordinator_documentation_information_architecture_consolidation_2026-10-15_review_001.adoc"
)


def test_docs_architecture_consolidation_design_001_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Run-A governance-authoring-only commission: preflight-only proof.

    This bundle commissions ONE independently reviewed documentation/
    backlog/information-architecture design plus its Run-B migration plan
    for the standalone repository, comparing empirically against the
    read-only reference repository core. It must parse, stay within
    policy/profile authority, use the bootstrap-safe four-phase topology
    (design -> independent_review -> {accept, correction} -> correction ->
    rereview -> {accept, correction}) with max_autonomous_hops == 6, and
    hold no mutation authority over src/workflow_coordinator/**,
    governance/ beyond its own bundle, workflow-coordinator-runtime, the
    workflow-coordinator-run-evidence repository, canonical Project Memory,
    docs/backlog/**, any other docs/*.adoc file, or the core reference
    repository: its only authorized scope is the new design artifact and
    its single dedicated durable review artifact.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=DOCS_ARCHITECTURE_CONSOLIDATION_DESIGN_001_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "docs-architecture-consolidation-design-001"

    assert not state_root.exists()
    result = _production_preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=DOCS_ARCHITECTURE_CONSOLIDATION_DESIGN_001_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    design = profile.phases["design"]
    review = profile.phases["independent_review"]
    correction = profile.phases["correction"]
    rereview = profile.phases["rereview"]

    exact_scope = (
        *DOCS_ARCHITECTURE_CONSOLIDATION_DESIGN_001_SCOPE,
        DOCS_ARCHITECTURE_CONSOLIDATION_DESIGN_001_REVIEW_ARTIFACT,
    )

    assert work_item.work_item_id == "WI-WFC-DOCS-ARCHITECTURE-CONSOLIDATION-DESIGN-001"
    assert work_item.initial_phase == "design"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 6
    assert profile.profile_id == "docs-architecture-consolidation-design-001"
    assert policy.policy_id == "workflow-coordinator-docs-architecture-consolidation-design-001"
    assert tuple(profile.phases) == ("design", "independent_review", "correction", "rereview")

    # WorkItem.scope is exactly the new design artifact plus its single
    # dedicated durable review artifact -- the smallest ceiling Run A needs.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    for withheld in (
        "src",
        "src/workflow_coordinator",
        "src/workflow_coordinator/model.py",
        "governance",
        "workflow-coordinator-runtime",
        "workflow-coordinator-run-evidence",
        "project_memory/canonical",
        "docs/backlog",
        "docs/backlog/README.adoc",
        "docs/self-development.adoc",
        "docs/architecture.md",
        "core",
        "tests/architecture/test_self_development_governance.py",
    ):
        assert not _within_scope(withheld, work_item.scope)
    for path in DOCS_ARCHITECTURE_CONSOLIDATION_DESIGN_001_SCOPE:
        assert _within_scope(path, work_item.scope)

    # design/correction share the exact single-path design scope;
    # independent_review and rereview are independently scoped to the
    # single dedicated durable review artifact alone -- no phase's
    # authorized_scope grants both.
    assert (
        design.authorized_scope
        == correction.authorized_scope
        == DOCS_ARCHITECTURE_CONSOLIDATION_DESIGN_001_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        DOCS_ARCHITECTURE_CONSOLIDATION_DESIGN_001_REVIEW_ARTIFACT,
    )
    assert set(DOCS_ARCHITECTURE_CONSOLIDATION_DESIGN_001_SCOPE).isdisjoint(review.authorized_scope)
    assert DOCS_ARCHITECTURE_CONSOLIDATION_DESIGN_001_REVIEW_ARTIFACT not in design.authorized_scope
    assert DOCS_ARCHITECTURE_CONSOLIDATION_DESIGN_001_REVIEW_ARTIFACT not in correction.authorized_scope

    assert durable_artifacts == {
        "independent_review": (DOCS_ARCHITECTURE_CONSOLIDATION_DESIGN_001_REVIEW_ARTIFACT,),
        "rereview": (DOCS_ARCHITECTURE_CONSOLIDATION_DESIGN_001_REVIEW_ARTIFACT,),
    }
    assert review.durable_artifact.value == rereview.durable_artifact.value == "required"
    assert design.durable_artifact.value == correction.durable_artifact.value == "none"
    assert design.evidence == correction.evidence == ()
    assert review.evidence == rereview.evidence == ("pytest",)
    assert review.authorized_actions == rereview.authorized_actions == ("review", "edit", "commit")
    assert design.authorized_actions == correction.authorized_actions == ("edit", "commit")

    # Topology: design -> independent_review -> {accept: complete,
    # correction} -> correction -> rereview -> {accept: complete,
    # correction}. No implementation phase and no mutation authority over
    # production source anywhere in this bundle.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "design": (("independent_review", "goto", "independent_review"),),
        "independent_review": (
            ("accept", "complete", None),
            ("correction", "goto", "correction"),
        ),
        "correction": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correction", "goto", "correction"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert design.role == correction.role == "worker"
    assert review.review.finding_authority == rereview.review.finding_authority == ("resolve",)
    assert review.review.failed_evidence_route == rereview.review.failed_evidence_route == "correction"

    # Role/persona separation: distinct fresh independent-review persona,
    # never the design/correction persona.
    assert work_item.personas[WorkflowRole.WORKER] != (
        work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]
    )

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-run-evidence", policy.work_item_scope_ceiling)
    assert not _within_scope("project_memory/canonical", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    # Admission happened entirely through provider-free preflight: no
    # provider/executable was ever invoked, and the state root the launch
    # would eventually use was never created by this process.
    assert not state_root.exists()


def _immutable_finding_echo_fix_bootstrap_profile():
    """Reproduce the commissioned profile's review/rereview shape exactly.

    ``review``/``rereview`` are ``gate``-authority phases with
    ``finding_authority: ()`` (no Finding disposition authority granted at
    all) and routes ``{accept: complete, correct: goto correct}`` -- the
    exact shape recorded in
    ``governance/self-development/immutable-finding-echo-profile.yaml``.
    """

    return profile(
        {
            "review": phase(
                {
                    "accept": route("complete"),
                    "correct": route("goto", "correct"),
                },
                role="independent_reviewer",
                review="gate",
                finding_authority=(),
            ),
            "correct": phase({"rereview": route("goto", "review")}),
        }
    )


def test_bootstrap_changes_required_correct_route_is_legal_with_empty_finding_set():
    """Provider-free proof: this commission's bootstrap choreography does not
    depend on repairing the very defect it exists to repair.

    The Runtime that will execute the commissioned Run is intentionally the
    currently promoted PRE-FIX Runtime, which still carries
    BUG-WFC-IMMUTABLE-FINDING-ECHO (an existing Finding's Coordinator-owned
    ``invariant`` is projected as generative result content and later
    requires exact restatement, so any attempt to create/dispose a
    current-Run Finding to carry an ordinary review-vs-correction cycle
    would risk exercising the broken path). This test exercises the actual,
    unmodified production reducer directly -- no provider, no mocking of
    ``reduce_result`` -- to prove that a gate-authority review Hop may
    legally request ``outcome: changes_required`` / ``route: correct`` with
    an entirely empty ``findings`` tuple, and that the Coordinator accepts
    it and transitions cleanly to ``correct`` without creating, requiring,
    or touching any Finding at all. If this property ever stops holding,
    the commissioned profile's review/rereview loop is unsound and the
    bundle must not be launched.
    """

    workflow = _immutable_finding_echo_fix_bootstrap_profile()
    run, hop_id = charge_hop(
        run_for(workflow, initial_phase="review"), role="independent_reviewer"
    )
    assert run.findings == ()

    reduction = reduce_result(
        run,
        result("correct", outcome="changes_required", findings=()),
        workflow,
        hop_id=hop_id,
    )

    assert reduction.run.phase == "correct"
    assert reduction.run.status is RunStatus.READY
    assert reduction.route_key == "correct"
    assert reduction.autonomous_route is True
    assert reduction.run.findings == ()
    assert reduction.run.stop_reason is None


def test_bootstrap_rereview_changes_required_correct_route_is_also_legal_with_empty_finding_set():
    """The same empty-Finding ``changes_required -> correct`` property holds
    for a ``rereview`` Hop reached after one correction round, not only for
    the initial ``review`` Hop -- the commissioned profile relies on both.
    """

    workflow = profile(
        {
            "correct": phase({"rereview": route("goto", "rereview")}),
            "rereview": phase(
                {
                    "accept": route("complete"),
                    "correct": route("goto", "correct"),
                },
                role="independent_reviewer",
                review="gate",
                finding_authority=(),
            ),
        }
    )
    run, hop_id = charge_hop(
        run_for(workflow, initial_phase="rereview"), role="independent_reviewer"
    )
    assert run.findings == ()

    reduction = reduce_result(
        run,
        result("correct", outcome="changes_required", findings=()),
        workflow,
        hop_id=hop_id,
    )

    assert reduction.run.phase == "correct"
    assert reduction.run.status is RunStatus.READY
    assert reduction.route_key == "correct"
    assert reduction.run.findings == ()


DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_GOVERNING_PATHS = (
    "governance/work-items/docs-architecture-consolidation-migration-001.yaml",
    "governance/self-development/docs-architecture-consolidation-migration-001-profile.yaml",
    "governance/self-development/docs-architecture-consolidation-migration-001-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/docs-architecture-consolidation-migration-001-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_SCOPE = (
    "README.md",
    "docs/README.adoc",
    "docs/architecture.md",
    "docs/run-preparation.adoc",
    "docs/self-development.adoc",
    "docs/run-recovery-and-salvage.adoc",
    "docs/wfc_operator_manual.adoc",
    "docs/wfc_projected_result_contracts_concept_2026-10-07.adoc",
    "docs/wfc_projected_result_contracts_concept_2026-10-07_review_001.adoc",
    "docs/wfc_projected_result_contracts_concept_2026-10-07_review_002.adoc",
    "docs/semantic_backtracking_vs_lifecycle_forward_motion_concept_2026-10-02.adoc",
    "docs/spreadsheet_handling_adoption_note_2026-09-25.adoc",
    "docs/product_boundary_and_higher_order_orchestration_concept_2026-09-25.adoc",
    "docs/product_boundary_and_higher_order_orchestration_concept_2026-09-25_review_001.adoc",
    "docs/product_boundary_and_higher_order_orchestration_concept_2026-09-25_review_002.adoc",
    "docs/wfc_evidence_failure_disposition_hardening_design_2026-10-09.adoc",
    "docs/wfc_operator_experience_and_prompt_composition_direction_2026-09-28.adoc",
    "docs/wfc_operator_harness_v0_concept_2026-10-06.adoc",
    "docs/wfc_operator_run_interface_legibility_evidence_2026-10-03.adoc",
    "docs/wfc_p2_stagnation_audit_2026-10-09.adoc",
    "docs/ai_info/_ai_info.adoc",
    "docs/backlog",
    "docs/warm_storage/concepts",
    "docs/cold_storage",
    "project_memory/canonical/decisions.json",
)
DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_REVIEW_ARTIFACT = (
    "docs/cold_storage/concepts/reviews/"
    "workflow_coordinator_docs_architecture_consolidation_migration_implementation_review_001.adoc"
)


def test_docs_architecture_consolidation_migration_001_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Run-B governance-authoring-only commission: preflight-only proof.

    This bundle commissions ONE independently reviewed IMPLEMENTATION of
    the migration plan Run A accepted at design revision f15d65b (closed
    under the independent closure review at 80ac584). It must parse, stay
    within policy/profile authority, use the four-phase topology
    (implementation -> independent_review -> {accept, correction} ->
    correction -> rereview -> {accept, correction}) with
    max_autonomous_hops == 8, and hold write authority over only: the
    explicitly retained current root/reference documents named in the
    accepted manifest; the new docs/ai_info/_ai_info.adoc navigation
    surface; the two source directories the accepted manifest governs in
    their entirety (docs/backlog and docs/warm_storage/concepts); the
    entirely new docs/cold_storage destination tree; and the single
    narrow project_memory/canonical/decisions.json source_path exception.
    It must never hold authority over src/workflow_coordinator/**,
    governance/ beyond its own bundle, Runtime, the Evidence repository,
    canonical Project Memory beyond that one narrow file, or the
    reference repository.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "docs-architecture-consolidation-migration-001"

    assert not state_root.exists()
    result = _production_preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implementation = profile.phases["implementation"]
    review = profile.phases["independent_review"]
    correction = profile.phases["correction"]
    rereview = profile.phases["rereview"]

    exact_scope = DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_SCOPE

    assert work_item.work_item_id == "WI-WFC-DOCS-ARCHITECTURE-CONSOLIDATION-MIGRATION-001"
    assert work_item.initial_phase == "implementation"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 8
    assert work_item.max_autonomous_hops <= COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS
    assert profile.profile_id == "docs-architecture-consolidation-migration-001"
    assert policy.policy_id == "workflow-coordinator-docs-architecture-consolidation-migration-001"
    assert tuple(profile.phases) == (
        "implementation", "independent_review", "correction", "rereview",
    )

    # WorkItem.scope is exactly the accepted manifest's path/family set --
    # explicit root/reference files, the two directories the manifest
    # governs in their entirety, the new navigation surface, the new cold
    # destination tree, and the one narrow Project Memory exception.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    for withheld in (
        "src",
        "src/workflow_coordinator",
        "src/workflow_coordinator/model.py",
        "governance",
        "workflow-coordinator-runtime",
        "workflow-coordinator-run-evidence",
        "core",
        "tests/architecture/test_self_development_governance.py",
        "docs/imported/core-incubator",
        "docs/provenance",
        "docs/warm_storage/runbooks",
        "docs/project_memory.adoc",
        "project_memory/canonical",
        "project_memory/canonical/concerns.json",
        "project_memory/canonical/concern_events.json",
        "project_memory/canonical/concern_event_xrefs.json",
    ):
        assert not _within_scope(withheld, work_item.scope)
        assert not _within_scope(withheld, policy.work_item_scope_ceiling)
    for path in DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_SCOPE:
        assert _within_scope(path, work_item.scope)

    # The narrow Project Memory exception is the exact decisions.json file,
    # never the whole canonical/ table.
    assert "project_memory/canonical/decisions.json" in work_item.scope

    # Both the controlling plan's initial warm path and its terminal cold
    # path fall within the migration's granted scope, as do representative
    # backlog/cold-storage destinations and the dedicated Run-B review
    # artifact -- none of these are listed literally, they are covered by
    # the docs/backlog, docs/warm_storage/concepts, and docs/cold_storage
    # path-family entries.
    for covered in (
        "docs/warm_storage/concepts/workflow_coordinator_documentation_information_architecture_consolidation_2026-10-15.adoc",
        "docs/cold_storage/concepts/history/workflow_coordinator_documentation_information_architecture_consolidation_2026-10-15.adoc",
        "docs/cold_storage/README.adoc",
        "docs/cold_storage/backlog/done/BUG-WFC-CURRENT-HOP-FUTURE-PHASE-AUTHORITY.adoc",
        "docs/backlog/_open.adoc",
        "docs/backlog/BUG-WFC-SELF-DEVELOPMENT-GATE-FINDING-AUTHORITY.adoc",
        "docs/backlog/wfc_reviews/WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-001/review.adoc",
        DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_REVIEW_ARTIFACT,
    ):
        assert _within_scope(covered, work_item.scope)

    # implementation/correction share the exact migration scope;
    # independent_review and rereview are independently scoped to the
    # single dedicated durable review artifact alone -- no phase's
    # authorized_scope grants both.
    assert (
        implementation.authorized_scope
        == correction.authorized_scope
        == DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_REVIEW_ARTIFACT,
    )
    assert set(DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_SCOPE).isdisjoint(review.authorized_scope)
    assert DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_REVIEW_ARTIFACT not in implementation.authorized_scope
    assert DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_REVIEW_ARTIFACT not in correction.authorized_scope

    assert durable_artifacts == {
        "independent_review": (DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_REVIEW_ARTIFACT,),
        "rereview": (DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_REVIEW_ARTIFACT,),
    }
    assert review.durable_artifact.value == rereview.durable_artifact.value == "required"
    assert implementation.durable_artifact.value == correction.durable_artifact.value == "none"
    assert implementation.evidence == correction.evidence == ()
    assert review.evidence == rereview.evidence == ("pytest",)
    assert review.authorized_actions == rereview.authorized_actions == ("review", "edit", "commit")
    assert implementation.authorized_actions == correction.authorized_actions == ("edit", "commit")

    # Topology: implementation -> independent_review -> {accept: complete,
    # correction} -> correction -> rereview -> {accept: complete,
    # correction}. No design phase, no supervisor phase, and no mutation
    # authority over production source anywhere in this bundle.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "implementation": (("independent_review", "goto", "independent_review"),),
        "independent_review": (
            ("accept", "complete", None),
            ("correction", "goto", "correction"),
        ),
        "correction": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correction", "goto", "correction"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert implementation.role == correction.role == "worker"
    assert review.review.finding_authority == rereview.review.finding_authority == ("resolve",)
    assert review.review.failed_evidence_route == rereview.review.failed_evidence_route == "correction"

    # Role/persona separation: distinct fresh independent-review persona,
    # never the implementation/correction persona.
    assert work_item.personas[WorkflowRole.WORKER] != (
        work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]
    )

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-run-evidence", policy.work_item_scope_ceiling)
    assert not _within_scope("project_memory/canonical", policy.work_item_scope_ceiling)
    assert _within_scope("project_memory/canonical/decisions.json", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    # Admission happened entirely through provider-free preflight: no
    # provider/executable was ever invoked, and the state root the launch
    # would eventually use was never created by this process.
    assert not state_root.exists()


DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_002_GOVERNING_PATHS = (
    "governance/work-items/docs-architecture-consolidation-migration-002.yaml",
    "governance/self-development/docs-architecture-consolidation-migration-002-profile.yaml",
    "governance/self-development/docs-architecture-consolidation-migration-002-policy.yaml",
    "governance/self-development/components.yaml",
    "governance/work-items/docs-architecture-consolidation-migration-002-task.txt",
    "governance/self-development/prompts/worker.txt",
    "governance/self-development/prompts/reviewer.txt",
)
DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_002_SCOPE = (
    DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_SCOPE[:-1]
    + (
        "docs/project_memory.adoc",
        "docs/warm_storage/runbooks/successor_continuation_runbook_2026-10-10.adoc",
        DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_SCOPE[-1],
    )
)
DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_002_REVIEW_ARTIFACT = (
    DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_REVIEW_ARTIFACT
)


def test_docs_architecture_consolidation_migration_002_bundle_passes_production_preflight(
    tmp_path: Path,
    preflight_python_path: None,
):
    """Fresh-successor Run-B002 governance-authoring-only commission: preflight-only proof.

    This bundle is the fresh successor to predecessor MIGRATION-001's blocked
    Hop H001: it carries the same accepted Run-A manifest authority (design
    revision f15d65b, closed under 80ac584) and the same four-phase topology
    with max_autonomous_hops == 8, plus exactly two newly granted narrow
    repair paths (docs/project_memory.adoc and the one named runbook file)
    that the predecessor WorkItem correctly did not authorize. It must never
    hold authority over src/workflow_coordinator/**, governance/ beyond its
    own bundle, Runtime, the Evidence repository, canonical Project Memory
    beyond the one narrow decisions.json file, the reference repository, or
    any part of docs/warm_storage/runbooks/** beyond the one named file.
    """

    repository, expected_head = _materialize_bundle(
        tmp_path,
        governing_paths=DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_002_GOVERNING_PATHS,
    )
    state_root = tmp_path / "external-state" / "docs-architecture-consolidation-migration-002"

    assert not state_root.exists()
    result = _production_preflight(_preflight_args(
        repository,
        expected_head,
        state_root,
        governing_paths=DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_002_GOVERNING_PATHS,
    ))
    work_item, profile, policy = result[5:8]
    durable_artifacts = result[9]
    implementation = profile.phases["implementation"]
    review = profile.phases["independent_review"]
    correction = profile.phases["correction"]
    rereview = profile.phases["rereview"]

    exact_scope = DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_002_SCOPE

    assert work_item.work_item_id == "WI-WFC-DOCS-ARCHITECTURE-CONSOLIDATION-MIGRATION-002"
    assert work_item.initial_phase == "implementation"
    assert work_item.max_autonomous_hops == policy.max_autonomous_hops == 8
    assert work_item.max_autonomous_hops <= COORDINATOR_HARD_MAX_AUTONOMOUS_HOPS
    assert profile.profile_id == "docs-architecture-consolidation-migration-002"
    assert policy.policy_id == "workflow-coordinator-docs-architecture-consolidation-migration-002"
    assert tuple(profile.phases) == (
        "implementation", "independent_review", "correction", "rereview",
    )

    # WorkItem.scope is exactly the predecessor's accepted-manifest scope
    # plus the two newly granted repair paths -- all predecessor migration
    # scope remains available, and nothing else is added.
    assert work_item.scope == policy.work_item_scope_ceiling == exact_scope
    assert set(DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_SCOPE) < set(exact_scope)
    assert set(exact_scope) - set(DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_SCOPE) == {
        "docs/project_memory.adoc",
        "docs/warm_storage/runbooks/successor_continuation_runbook_2026-10-10.adoc",
    }
    for withheld in (
        "src",
        "src/workflow_coordinator",
        "src/workflow_coordinator/model.py",
        "governance",
        "workflow-coordinator-runtime",
        "workflow-coordinator-run-evidence",
        "core",
        "tests/architecture/test_self_development_governance.py",
        "docs/imported/core-incubator",
        "docs/provenance",
        "project_memory/canonical",
        "project_memory/canonical/concerns.json",
        "project_memory/canonical/concern_events.json",
        "project_memory/canonical/concern_event_xrefs.json",
    ):
        assert not _within_scope(withheld, work_item.scope)
        assert not _within_scope(withheld, policy.work_item_scope_ceiling)
    for path in DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_002_SCOPE:
        assert _within_scope(path, work_item.scope)

    # The two newly granted paths are present and exact-file-scoped: no
    # broader docs/warm_storage/runbooks/** authority is introduced by
    # granting the single named runbook file.
    assert "docs/project_memory.adoc" in work_item.scope
    assert (
        "docs/warm_storage/runbooks/successor_continuation_runbook_2026-10-10.adoc"
        in work_item.scope
    )
    assert _within_scope("docs/project_memory.adoc", work_item.scope)
    assert _within_scope(
        "docs/warm_storage/runbooks/successor_continuation_runbook_2026-10-10.adoc",
        work_item.scope,
    )
    assert not _within_scope("docs/warm_storage/runbooks", work_item.scope)
    assert not _within_scope(
        "docs/warm_storage/runbooks/some_other_runbook.adoc", work_item.scope
    )

    # The narrow Project Memory exception is still the exact decisions.json
    # file, never the whole canonical/ table, and never docs/project_memory.adoc
    # treated as a semantic-editing grant beyond narrow link retargeting
    # (that boundary is enforced by authority_source/task text, not by scope
    # shape, since scope shape cannot distinguish "retarget a link" from
    # "rewrite the file").
    assert "project_memory/canonical/decisions.json" in work_item.scope

    # Both the controlling plan's possible paths and representative
    # backlog/cold-storage destinations and the dedicated review artifact
    # remain covered, exactly as for the predecessor.
    for covered in (
        "docs/warm_storage/concepts/workflow_coordinator_documentation_information_architecture_consolidation_2026-10-15.adoc",
        "docs/cold_storage/concepts/history/workflow_coordinator_documentation_information_architecture_consolidation_2026-10-15.adoc",
        "docs/cold_storage/README.adoc",
        "docs/cold_storage/backlog/done/BUG-WFC-CURRENT-HOP-FUTURE-PHASE-AUTHORITY.adoc",
        "docs/backlog/_open.adoc",
        "docs/backlog/BUG-WFC-SELF-DEVELOPMENT-GATE-FINDING-AUTHORITY.adoc",
        "docs/backlog/wfc_reviews/WI-WFC-DEVELOPER-ERGONOMICS-REVIEW-001/review.adoc",
        DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_002_REVIEW_ARTIFACT,
    ):
        assert _within_scope(covered, work_item.scope)

    # implementation/correction share the exact migration scope;
    # independent_review and rereview are independently scoped to the single
    # dedicated durable review artifact alone -- the same artifact path the
    # predecessor's bundle named, since predecessor H001 never populated it.
    assert (
        implementation.authorized_scope
        == correction.authorized_scope
        == DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_002_SCOPE
    )
    assert review.authorized_scope == rereview.authorized_scope == (
        DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_002_REVIEW_ARTIFACT,
    )
    assert set(DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_002_SCOPE).isdisjoint(review.authorized_scope)
    assert DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_002_REVIEW_ARTIFACT not in implementation.authorized_scope
    assert DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_002_REVIEW_ARTIFACT not in correction.authorized_scope

    assert durable_artifacts == {
        "independent_review": (DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_002_REVIEW_ARTIFACT,),
        "rereview": (DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_002_REVIEW_ARTIFACT,),
    }
    assert review.durable_artifact.value == rereview.durable_artifact.value == "required"
    assert implementation.durable_artifact.value == correction.durable_artifact.value == "none"
    assert implementation.evidence == correction.evidence == ()
    assert review.evidence == rereview.evidence == ("pytest",)
    assert review.authorized_actions == rereview.authorized_actions == ("review", "edit", "commit")
    assert implementation.authorized_actions == correction.authorized_actions == ("edit", "commit")

    # Topology: implementation -> independent_review -> {accept: complete,
    # correction} -> correction -> rereview -> {accept: complete,
    # correction}. No design phase, no supervisor phase, no mutation
    # authority over production source anywhere in this bundle.
    assert {
        phase_key: tuple((key, route.effect.value, route.target) for key, route in value.routes.items())
        for phase_key, value in profile.phases.items()
    } == {
        "implementation": (("independent_review", "goto", "independent_review"),),
        "independent_review": (
            ("accept", "complete", None),
            ("correction", "goto", "correction"),
        ),
        "correction": (("rereview", "goto", "rereview"),),
        "rereview": (
            ("accept", "complete", None),
            ("correction", "goto", "correction"),
        ),
    }
    assert review.role == rereview.role == "independent_reviewer"
    assert implementation.role == correction.role == "worker"
    assert review.review.finding_authority == rereview.review.finding_authority == ("resolve",)
    assert review.review.failed_evidence_route == rereview.review.failed_evidence_route == "correction"

    # Role/persona separation: distinct fresh independent-review persona,
    # never the implementation/correction persona.
    assert work_item.personas[WorkflowRole.WORKER] != (
        work_item.personas[WorkflowRole.INDEPENDENT_REVIEWER]
    )

    assert policy.governance_paths == ("governance",)
    assert not _within_scope("src", policy.work_item_scope_ceiling)
    assert not _within_scope("governance", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-runtime", policy.work_item_scope_ceiling)
    assert not _within_scope("workflow-coordinator-run-evidence", policy.work_item_scope_ceiling)
    assert not _within_scope("project_memory/canonical", policy.work_item_scope_ceiling)
    assert _within_scope("project_memory/canonical/decisions.json", policy.work_item_scope_ceiling)
    assert tuple(result[13]) == ("pytest",)

    # Admission happened entirely through provider-free preflight: no
    # provider/executable was ever invoked, and the state root the launch
    # would eventually use was never created by this process.
    assert not state_root.exists()

    # This successor bundle is strictly additive over the predecessor's own
    # governing files: the predecessor bundle itself is never mutated.
    for predecessor_path in DOCS_ARCHITECTURE_CONSOLIDATION_MIGRATION_001_GOVERNING_PATHS:
        assert (PROJECT_ROOT / predecessor_path).exists()

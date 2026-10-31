# Implement simple@v1 admission and execution as one bounded vertical slice

Implement, as one bounded vertical slice, the Stage B scope recommended by
docs/warm_storage/concepts/workflow_coordinator_simple_v1_implementation_boundary_handoff_2026-10-31.adoc
section 6 for the accepted
docs/warm_storage/concepts/workflow_coordinator_simple_v1_architecture_design_2026-10-31.adoc.
Treat that accepted design as the implementation contract: do not revisit
its alternatives, reopen its twelve sections, or commission another
architecture-design exercise. Required reading before changing anything:
the accepted design in full; its final broad and focused reviews
(docs/warm_storage/concepts/workflow_coordinator_simple_v1_architecture_design_2026-10-31_broad_review.adoc,
..._focused_review.adoc); the implementation-boundary handoff note above;
docs/backlog/BUG-WFC-PROVIDER-TRANSPORT-CANDIDATE-ACCEPTANCE.adoc;
docs/backlog/BUG-WFC-COMMISSION-SELECTED-COMPONENT-PROVENANCE.adoc; and
docs/backlog/FTR-WFC-ARCHITECTURAL-CORE-AND-CONSOLIDATION-BOUNDARIES.adoc
section 7 of the handoff note, which restates the binding architectural
boundaries for this Commission.

Implement exactly:

1. `simple@v1` recognition and exact closed-template validation in
   `src/workflow_coordinator/commissioning.py`: add `("simple", "v1")` to
   `program_specs` and to `commission_intent_from_yaml`'s
   `required_personas` mapping (worker persona only), add the matching
   `_simple_template()` literal matching
   `governance/workflow-programs/simple/v1.yaml` exactly (excluding its
   `source:` provenance field, as `_direct_template`/`_robust_template`
   already do), and bind it to the existing self-development component
   catalogue (`governance/self-development/components.yaml`).
2. Selected-component provenance traversal completion in
   `render_commission` (`commissioning.py:442-446`): replace the
   role-only comprehension with the deterministic first-reference-ordered
   traversal of `role`, `modifiers`, then `repository_policy` for every
   phase, matching `operator._selected_ids`'s category order exactly, per
   `docs/backlog/BUG-WFC-COMMISSION-SELECTED-COMPONENT-PROVENANCE`. Feed
   the complete ordered set into the existing digest and
   `sources.selected_components` generation unchanged.
3. One shared, narrow closed-terminal-Worker shape predicate (placed in
   `src/workflow_coordinator/model.py`) used by both
   `model.select_result_contract` (to select the existing generic
   `structured_result_v1` routing contract for Simple's outcome-bearing
   result) and `src/workflow_coordinator/operator.py`'s
   `_admit_evidence_disposition` (to admit evidence without a review gate
   for exactly that closed shape: one-phase profile, Worker role, no
   review, exactly `[simple-self-check]` modifiers and `[task-check]`
   evidence, no convergence, exactly one `accept: complete` route). Do
   not introduce a distributed `is_simple`/`skip_review` flag anywhere
   else; this predicate is the single decision point.
4. The early provider-transport acceptance guard in
   `src/workflow_coordinator/vertical.py`'s `run_one_hop`, immediately
   after `adapter.execute` returns and before `outcome.candidate_result`
   is read: if `outcome.returncode` is truthy and nonzero, or
   `outcome.timed_out` is `True`, raise the existing
   `ExecutionResultValidationError(outcome, ...)` unconditionally,
   regardless of whether a candidate is present, per
   `docs/backlog/BUG-WFC-PROVIDER-TRANSPORT-CANDIDATE-ACCEPTANCE`. Reuse
   `slice4.run_real_one_hop`'s existing no-replay `recover_uncertain`
   path unchanged; do not modify `slice4.py`, add a new recovery state,
   or add a retry mechanism. This guard is a shared vertical-Hop
   invariant and must apply unconditionally, not as a Simple-only
   branch.
5. Focused positive and negative tests in the actual existing test
   files `tests/unit/test_commissioning.py`,
   `tests/unit/test_a1_admission.py`, `tests/unit/test_operator.py`,
   `tests/unit/test_slice3_vertical.py`, and `tests/unit/test_slice4.py`
   (do not invent other test file names), covering at minimum, per the
   accepted design's section 10:
   - render/admit Simple with one Worker, one phase, `task-check`
     evidence, no durable artifact, `max_autonomous_hops: 1`; reject
     extra persona/artifact, `none` evidence, a modified
     route/evidence/action/Finding-authority, a missing or altered
     `simple-self-check` component-manifest source or
     selected-component digest, and a nonliteral programme;
   - a pinned component manifest whose `simple-self-check` modifier
     bytes differ between `components_ref.source_commit` and
     `expected_head`: assert Front Door rejects it before operator
     dispatch; assert Direct/Robust render manifests remain
     byte-identical because their `modifiers`/`repository_policy`
     lists stay empty;
   - exactly one agent invocation per Hop, with the mandatory
     Coordinator-owned `task-check` evidence subprocess counted
     separately from that invocation;
   - normal completion (valid Worker result, trusted in-scope commit,
     clean post-evidence repository, passing evidence); Worker-reported
     `outcome: failed` reaching the existing `result_failed` guard
     without a second invocation; `blocked`/escalation remaining
     separately represented; and `changes_required` remaining
     unreachable for a review-less Worker;
   - rejected invalid programme/profile variants: a `goto`, a
     supervisor or human route, an extra route, a missing
     `simple-self-check` modifier, or a non-`accept` terminal shape,
     each preflight-rejected;
   - the modifier-byte provenance mismatch case from the bullet above;
   - a nonzero-`returncode`-with-valid-correlated-candidate case and a
     `timed_out=True`-with-valid-correlated-candidate case, each
     asserting no trusted commit, no evidence subprocess, no
     reduction-to-complete, exactly one charged provider call, and a
     retained dispatch marker/recovered `uncertain_dispatch` Hop;
   - regressions proving `direct@v1`/`robust@v1` render, preflight, and
     execution behavior is byte-for-byte/behavior-for-behavior
     unchanged except for the two explicitly justified shared
     corrections (items 2 and 4 above).

Architectural boundaries (binding, restated from
`FTR-WFC-ARCHITECTURAL-CORE-AND-CONSOLIDATION-BOUNDARIES` and the
handoff note section 7): `reducer.py` stays deterministic and
effect-free -- do not touch it, `serialization.py`, or any evidence
infrastructure or technical schema unless a concrete failing test proves
the accepted design cannot work without that change; if that occurs,
stop and report the blocker instead of expanding this Commission's
authority. No workflow DSL, generic component/capability registry,
adapter framework, service hierarchy, or speculative target
architecture. No scattered `is_simple`/`skip_review` switches. Do not
modify `direct@v1`/`robust@v1`'s programme records or their shared
Worker/Reviewer prompts. Do not implement P3.5, P4, or P5. Do not touch
Claude-adapter or token-accounting work. Do not broaden scope to any
other backlog record.

Both prerequisite BUG records
(`BUG-WFC-PROVIDER-TRANSPORT-CANDIDATE-ACCEPTANCE`,
`BUG-WFC-COMMISSION-SELECTED-COMPONENT-PROVENANCE`) are resolved by items
2 and 4 above and their required negative tests, within this Run; do not
open a separate WorkItem for either.

### Scope note (known, unrepaired limitation)

The rendered Robust-v1 worker-phase `authorized_scope` is mechanically
the complete Commission scope, including both independent-review
artifact paths below, per
`docs/backlog/FTR-WFC-PHASE-WRITE-SCOPE-ARTIFACT-ISOLATION.adoc`. You
(the Worker) are instructed to create or edit only the `src`/`tests`
paths in this Commission's scope. The two review-artifact paths are
reserved for the independent reviewer; do not write to them, and do not
attempt to repair this already-recorded scope-isolation limitation in
this Commission.


## Acceptance

`simple@v1` renders and is admitted from the committed Stage-A
catalogue; a Simple Commission Intent naming `program: {id: simple,
revision: v1}` is accepted by `commission_intent_from_yaml` and
`_load_sources`, and its rendered manifest byte-compares every selected
role/modifier/repository-policy component. One Worker invocation can
complete successfully with passing trusted `task-check` evidence and all
normal Git/scope/commit checks, charging exactly one Hop. Each documented
failure case (Worker `blocked`/`failed`/escalation, evidence
failure/error/not-run, scope/commit/Git fault, nonzero-exit or timed-out
transport with an otherwise-valid candidate, missing candidate) stops or
fails closed without dispatching a second agent. Both BUG fixes have the
specified negative tests and those tests pass. All existing
`direct@v1`/`robust@v1` tests pass unchanged in behavior. No
unauthorized source, governance, Runtime, or Evidence change occurs; `git
diff --check` and the full `task-check` suite pass. The independent
reviewer has read and exercised the actual changed code and tests, not
merely restated this task or the design document, and has recorded
findings against each item above with any Finding disposed using only
this Commission's rendered Robust-v1 profile authority.


## Withheld authority

No edit to any `governance/` path -- the three Stage-A files and every
other programme/policy/component record are read-only governing inputs
for this Commission, not part of its agent-writable scope. No change to
`reducer.py`, `serialization.py`, evidence infrastructure, or any
technical schema unless a concrete failing test proves the accepted
design cannot work without it, in which case stop and report rather than
proceeding. No new programme revision, workflow DSL, capability
registry, adapter framework, or service hierarchy. No change to
`direct@v1`/`robust@v1` records or shared prompts. No P3.5, P4, or P5
implementation. No Claude-adapter or token-accounting work. No Runtime
promotion, Evidence mutation, external push, or nested WFC invocation.
No automatic authorization of a successor Commission or Run.


## Default escalation

Stop and request maintainer authority if: a required change cannot be
made without touching `reducer.py`, `serialization.py`, evidence
infrastructure, a technical schema, or any path outside this Commission's
scope; a pinned governance/component source cannot be verified to match
the committed Stage-A state; the exact one-invocation contract cannot be
implemented without a kernel change broader than task items 1-4 above;
or a reviewer-opened Finding cannot be disposed within this Commission's
own granted Finding authority and ordinary Robust-v1 correction flow.


# Implement the accepted Claude Code CLI adapter design as one vertical slice

Implement the accepted design's single coherent vertical slice adding
the Claude Code CLI as a second real provider behind the existing
`AgentExecutionPort`, alongside the existing Codex CLI provider. Treat
docs/warm_storage/concepts/workflow_coordinator_claude_code_adapter_design_2026-11-01.adoc
as the controlling implementation specification. Its independent
acceptance,
docs/warm_storage/concepts/workflow_coordinator_claude_code_adapter_design_closure_2026-11-01_broad_review.adoc,
and the predecessor review artifacts it closes are historical
provenance, not additional requirements -- do not treat anything in
them as authorizing scope or semantics beyond the accepted design
itself.

### Required reading

Read, in full, before implementing:

- The accepted design document (all 9 sections) and its closure
  review, as grounding fact.
- `src/workflow_coordinator/adapter.py` (`AgentExecutionPort`,
  `AgentExecutionRequest`/`AgentExecutionOutcome`, `validated_result`).
- `src/workflow_coordinator/codex_adapter.py` (argv construction,
  `assert_openai_strict_schema`,
  `structured_result_envelope_openai_schema`, process lifecycle,
  timeout/kill escalation, bounded tail capture, diagnostics).
- `src/workflow_coordinator/serialization.py`'s
  `run_execution_configuration_from_yaml` (currently requires
  `adapter == "codex_cli"` exactly, serialization.py:1011-1012).
- `src/workflow_coordinator/operator.py`'s `run_serial_hops` (currently
  checks `resolved_binding.adapter != "codex_cli"` and directly
  constructs `CodexCliAdapter`, operator.py:859-864) and its executable
  threading from a single scalar `--executable`.
- `scripts/wfc_op.py`'s executable/binding handling
  (`--executable`, `cmd_prepare`, the `ROLE=ADAPTER:MODEL:REASONING`
  binding writer).
- The existing test families: `tests/unit/test_codex_adapter_conformance.py`,
  `tests/unit/test_codex_adapter_smoke.py`, `tests/unit/test_operator.py`,
  `tests/unit/test_serialization.py`, `tests/unit/test_wfc_op.py`, and
  the Front Door family `tests/unit/test_commissioning.py`.

Do not treat any line number above as exact without re-verifying it
against the actual Development HEAD read during this Run; these were
confirmed unchanged between the design's inspected baseline
(`bcbae8ab6873ae4ed7bd414f1cceb3ac983f99d7`) and this Commission's
pinned program/policy/components commit
(`4c16884e5b2e747bd0f90e60660d7f74d02ef943`) at authoring time, but
re-verify before relying on them.

### Scope

Implement exactly:

- New `src/workflow_coordinator/claude_adapter.py`: a frozen
  `ClaudeCliAdapter` implementing the existing `AgentExecutionPort`
  per design section 4.1 -- exact argv, the closed
  `--restricted --safe-mode --strict-mcp-config` profile, the named
  six-tool surface, the generated closed settings JSON (sandbox
  required, no unsandboxed fallback, empty network domains, denied
  `WebFetch`/`WebSearch`), `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`, no
  Codex/OpenAI/plugin/MCP/resume/fallback-model flag, and the wrapper
  decode producing only a candidate result (never repairing
  correlation/kind/version/route fields).
- New `src/workflow_coordinator/structured_result_schema.py`: the
  shared WFC envelope builder extracted from the Codex adapter per
  design section 4.1, taking Invocation/kind/version/permitted routes
  and returning the same closed envelope shape Codex and Claude both
  serialize from.
- Minimal changes to `src/workflow_coordinator/codex_adapter.py`:
  consume the shared builder; preserve `assert_openai_strict_schema`
  and all existing Codex argv/process/diagnostics behavior unchanged.
- `src/workflow_coordinator/serialization.py`: admit exactly the
  closed pair `{codex_cli, claude_cli}` in
  `run_execution_configuration_from_yaml`; preserve every other field,
  role, version, and nonempty check unchanged; reject every other
  literal exactly as today.
- `src/workflow_coordinator/operator.py`: a private two-case
  constructor (not a registry, not dynamic import) selecting
  `CodexCliAdapter` or `ClaudeCliAdapter` from the resolved binding;
  preflight admission that rejects a mixed-provider role set before
  any provider invocation, given the existing single scalar
  `--executable`; `_binding_evidence` continues to persist the actual
  adapter/executable/model/reasoning unchanged in shape.
- `scripts/wfc_op.py`: touch only if its generic binding writer or its
  own tests need the newly admitted `claude_cli` literal to round-trip;
  do not add a role-executable override or any mixed-binding
  interface -- that is explicitly out of scope (design section 4.4).
- Focused tests: new `tests/unit/test_claude_adapter_conformance.py`
  with deterministic fake-executable coverage (see "Required tests"
  below); extensions to `tests/unit/test_codex_adapter_conformance.py`,
  `tests/unit/test_operator.py`, `tests/unit/test_serialization.py`,
  `tests/unit/test_wfc_op.py`, and the Front Door family
  `tests/unit/test_commissioning.py` as needed to prove the above
  without weakening any existing assertion.
- The two durable review artifacts this Commission's rendered
  `robust@v1` profile designates for `broad_review` and
  `focused_review`.

Do not change `model.py`, `reducer.py`, `AgentExecutionPort`'s shape,
result contracts, workflow programmes/profiles, governance policy,
Runtime promotion mechanisms, token accounting, or the per-role
executable interface. Do not touch `slice4.py`, `vertical.py`,
`persistence.py`, `evidence.py`, `git_facts.py`, `prompt.py`, or
`proof_harness.py`.

### Required semantics

Preserve the accepted design without adding a provider framework:

- Explicit `codex_cli` or `claude_cli` selection only; no registry, no
  dynamic routing, no fallback.
- The existing provider-neutral request/outcome port is unchanged.
- Exact `(run_id, hop_id, invocation_id)` correlation and fail-closed
  semantic validation via the existing `validated_result` is unchanged
  and is never weakened or bypassed for either provider.
- Claude-specific argv, JSON-wrapper extraction, settings, permissions,
  and process mechanics stay adapter-private to `claude_adapter.py`.
- Codex retains its existing OpenAI strict-schema behavior after the
  shared-builder extraction; its conformance tests must still pass
  unmodified in intent.
- An observed nonzero process exit and an observed timeout are
  rejected as transport failures before any semantic validation, for
  both providers.
- A zero exit with absent, unsuccessful, malformed, or invalid
  structured output must never be accepted as a candidate, and no
  field is ever repaired or inferred.
- No fabricated provider status and no automatic replay after
  uncertain dispatch.
- The exact closed startup/tool/settings profile from design section
  4.1 is implemented byte-for-byte as specified, not approximated.
- Provider-side sandbox requests (Claude's generated settings, Codex's
  `--sandbox workspace-write`) are never represented as a
  Coordinator-verified OS security boundary.
- Homogeneous Simple, Direct, and Robust provider bindings are
  supported for either provider; a mixed-provider role set is
  rejected in preflight, before any provider invocation, given the
  single scalar executable -- never silently coerced to one provider.
- Existing binding and executable provenance recording
  (`StagedQualificationAttempt`, `_binding_evidence`) is preserved
  unchanged in shape for both providers.

### Required tests and qualification boundary

Add deterministic fake-CLI tests in
`tests/unit/test_claude_adapter_conformance.py` covering, at minimum:
exact argv construction (no Codex/plugin/MCP/resume/fallback/bypass/
ambient-config flag), prompt/cwd, model/effort mapping, the generated
closed-settings JSON content, the scrubbed subprocess environment,
successful wrapper extraction, startup/local pre-`Popen` failure,
nonzero exit with valid-looking JSON, timeout with partial output,
malformed wrapper, zero exit without a successful structured-output
member, malformed extracted envelope, and mismatched
Run/Hop/Invocation correlation. Every negative case must prove no
repair and no acceptance. Also cover the zero-exit
post-sandboxed-command-refusal case: assert only that no unsandboxed
retry is requested and that the adapter does not manufacture a
nonzero transport result.

Extend `test_serialization.py` so exactly `{codex_cli, claude_cli}`
are admitted and every other literal is still rejected. Extend
`test_operator.py` with coverage that patches each adapter and proves
identical binding provenance/reduction for both, plus the
mixed-binding/scalar-executable preflight rejection. Extend
`test_wfc_op.py` only to preserve a `claude_cli` binding
round-trip -- never authenticate or invoke Claude. Extend
`test_commissioning.py` only as needed to keep Front Door Commission
rendering/admission consistent with the newly admitted literal; do
not change its own rendering semantics. Keep all existing Codex
conformance and operator regression tests passing unmodified in
intent.

Run the repository's `task-check` evidence command
(`python -m pytest -q`) as the Coordinator evidence gate.

The accepted design separately requires a real authenticated Claude
CLI smoke suite before release acceptance (design section 7, last
paragraph). That live suite is **not authorized by this Commission**.
Do not invoke an authenticated Claude provider, inspect credentials,
widen sandbox permissions, or make any network-dependent test
mandatory to satisfy this WorkItem. Implementation acceptance here
requires only that the offline implementation and deterministic
evidence are sound; real-provider compatibility and effective sandbox
qualification remain explicit follow-up release gates, never silently
assumed successes.

### Independent review mandate

The independent reviewer must inspect the actual implementation and
tests -- not merely the new Claude adapter file -- for: correctness
against the accepted design; preservation of every security/trust
boundary named above; Codex regression risk after the shared-builder
extraction; and scope discipline against this task's exact file
scope. The reviewer must specifically test and record findings on
whether: any unsupported Claude CLI assumption was implemented as
fact; any correlation or semantic-validation rule was relaxed;
provider-specific logic leaked into `operator.py`/`serialization.py`
beyond the described closed admission and two-case constructor; any
negative transport/timeout/malformed-output case is untested; the
implementation is incompatible with existing Direct, Robust, or
Simple runs; a provider registry, dynamic routing, or role-executable
override was added despite being out of scope; or the fake tests
merely run two unrelated provider-specific paths rather than proving
the shared provider-neutral abstraction. Use normal Robust correction
and rereview routing; disposition any Finding using only the
authority this Commission's rendered profile actually grants. Do not
manufacture additional architectural requirements or expand
implementation scope beyond the accepted design. A successful broad
review may accept directly; focused review is needed only if that
phase is reached.


## Acceptance

`src/workflow_coordinator/claude_adapter.py` and
`src/workflow_coordinator/structured_result_schema.py` exist and
implement design section 4.1 exactly, including the closed
startup/tool/settings profile byte-for-byte. Codex retains equivalent
structured-output schema behavior after the shared-builder
extraction, with its existing conformance tests passing unmodified in
intent. `serialization.py` admits exactly `{codex_cli, claude_cli}`
and rejects every other literal. `operator.py` selects between the
two adapters via an explicit two-case constructor with no registry,
dynamic routing, or fallback, and rejects a mixed-provider role set
in preflight before any provider invocation while a single scalar
executable is used. `scripts/wfc_op.py` is touched only as necessary
to preserve a `claude_cli` binding. New deterministic fake-CLI tests
in `tests/unit/test_claude_adapter_conformance.py` cover every
positive and negative case listed in "Required tests," and existing
Codex conformance, operator, serialization, `wfc_op`, and Front Door
(`test_commissioning.py`) tests all still pass. `task-check`
(`python -m pytest -q`) passes against the full suite. No live
Claude CLI invocation, credential inspection, sandbox-permission
widening, or mandatory network-dependent test was added. The
independent reviewer's broad (and, if reached, focused) review
artifact records a genuine adversarial review against the mandate
above, with no blocking Finding left open at completion.


## Withheld authority

No change to `model.py`, `reducer.py`, `AgentExecutionPort`'s shape,
result contracts, workflow programmes/profiles, governance policy,
Runtime promotion mechanisms, token accounting, or the per-role
executable interface. No provider registry, dynamic routing,
provider fallback, agent hierarchy, generic orchestration layer, or
role-executable/mixed-binding interface -- design section 4.4's
extension is explicitly out of scope and separately authorized only
in the future, if at all. No live, authenticated Claude or Codex CLI
invocation; no credential inspection or exposure; no widening of the
closed sandbox/tool/settings profile beyond design section 4.1; no
network-dependent test made mandatory. No Runtime promotion, Evidence
mutation, nested WFC invocation, or external push. No file outside
this Commission's rendered `authorized_scope` for the active phase
may be edited. The Worker and reviewer must not weaken or bypass
`validated_result`'s exact correlation/semantic checks for either
provider, must not fabricate a provider process outcome, and must
not treat a provider-requested permission or sandbox mode as a
Coordinator-verified OS boundary. Completing this implementation
grants no automatic authorization for the separately authorized
real-Claude-CLI smoke suite or for any future release decision.


## Default escalation

Stop and request maintainer authority if: the accepted design cannot
be implemented within this Commission's exact file scope without a
change to `model.py`, `reducer.py`, the port shape, or governance; a
required Claude CLI behavior assumed by the design is contradicted by
read-only discovery and no fake-testable, design-consistent
alternative can be found within scope; the mixed-provider/
role-executable extension turns out to be required to complete this
slice; a reviewer-opened Finding cannot be disposed within this
Commission's own granted Finding authority and ordinary Robust-v1
correction flow; or completing the slice would require any form of
live, authenticated provider invocation.


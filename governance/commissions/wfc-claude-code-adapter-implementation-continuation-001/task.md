# Complete and independently review the Claude Code adapter implementation from the preserved predecessor candidate

Complete and independently review the single coherent vertical-slice
implementation of the accepted Claude Code CLI adapter design,
reusing the preserved predecessor candidate as a starting point rather
than reimplementing from scratch. Treat
docs/warm_storage/concepts/workflow_coordinator_claude_code_adapter_design_2026-11-01.adoc
as the controlling implementation specification. The predecessor
Commission's own task record,
governance/commissions/wfc-claude-code-adapter-implementation-001/task.md,
and docs/run-recovery-and-salvage.adoc are historical/procedural
context for how this successor came to exist; they are not additional
implementation requirements beyond the accepted design itself.

### Required reading

Read, in full, before implementing:

- The accepted design document (all 9 sections).
- This WorkItem's `authority_source`, which records the verified
  predecessor stop facts, candidate ref/commit, and archive state; do
  not re-derive these independently or re-litigate them.
- The preserved candidate commit
  `3a127f64498b7e277d11f5ea23d8806cb5666bd4` at ref
  `refs/wfc/candidates/RUN-5740ace2e87549228609e6e5cc225996/H001`, and
  its exact diff against baseline
  `bafb70f96d01e459fb8029498f59c775143f79d4` (nine paths, enumerated in
  `authority_source`).
- `src/workflow_coordinator/adapter.py` (`AgentExecutionPort`,
  `AgentExecutionRequest`/`AgentExecutionOutcome`, `validated_result`).
- The pre-candidate `src/workflow_coordinator/codex_adapter.py` at the
  baseline commit, to understand what the candidate's shared-builder
  extraction changed and why.
- `src/workflow_coordinator/serialization.py`'s
  `run_execution_configuration_from_yaml` and
  `src/workflow_coordinator/operator.py`'s `run_serial_hops` at the
  baseline commit, for the same reason.
- The existing test families at baseline:
  `tests/unit/test_codex_adapter_conformance.py`,
  `tests/unit/test_codex_adapter_smoke.py`, `tests/unit/test_operator.py`,
  `tests/unit/test_serialization.py`, `tests/unit/test_wfc_op.py`, and
  the Front Door family `tests/unit/test_commissioning.py`.

Do not expand this required-reading list into historical architecture
archaeology; read only what is necessary to verify and complete this
specific continuation.

### Mandate

1. Inspect the preserved CandidateSnapshot (`3a127f64498b7e277d11f5ea23d8806cb5666bd4`)
   and its exact diff against the original trusted baseline
   (`bafb70f96d01e459fb8029498f59c775143f79d4`) before changing
   anything.
2. Reuse the candidate's nine-file implementation as the starting
   point rather than independently reimplementing the adapter from
   scratch. Every salvaged line is unverified candidate material, free
   to be kept, altered, removed, or replaced within this Commission's
   own scope; the Worker owns every retained line through fresh
   inspection, not through the candidate's provenance.
3. Materialize the candidate's content in this Commission's own
   authorized worktree by extracting each changed path's content from
   the candidate commit (for example `git show
   3a127f64498b7e277d11f5ea23d8806cb5666bd4:<path>`, or an equivalent
   read-only extraction) and writing it into the ordinary tracked
   worktree as part of this Run's own Hop edits. Do not `git
   cherry-pick`, `git merge`, or otherwise import the untrusted
   preservation commit `3a127f64498b7e277d11f5ea23d8806cb5666bd4` itself
   as parent history of any trusted commit this Run produces; do not
   change `main` outside normal Coordinator trusted-commit mechanics;
   do not move, delete, or rewrite the candidate ref.
4. Independently inspect the imported implementation for compliance
   with the accepted Claude adapter design -- do not assume the
   candidate is correct merely because it exists.
5. Complete any missing work and correct defects within the original
   authorized implementation scope (see "Scope" below). The predecessor
   Hop was never reviewed, so treat the entire imported implementation
   as fresh, unverified work requiring full inspection, not merely a
   diff to patch.
6. Run the repository's full `task-check` evidence command
   (`python -m pytest -q`) through the configured Coordinator evidence
   gate -- never a substitute agent-local partial run -- and provide an
   exact `commit_intent` containing only paths with actual worktree
   mutations relative to this Hop's starting tree. The specific
   mechanical mistake that stopped the predecessor's H001 must not
   recur: a file such as `tests/unit/test_codex_adapter_conformance.py`
   must never appear in `commit_intent.paths` merely because it was
   read or exercised by the test run; it belongs in `commit_intent`
   only if this Hop actually wrote to it.

### Scope

Implement, complete, and correct exactly the original implementation
Commission's source/test path scope, reused here verbatim except for
this successor's own newly named durable review artifacts:

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
  design section 4.1.
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
  interface -- explicitly out of scope (design section 4.4).
- Focused tests: `tests/unit/test_claude_adapter_conformance.py` with
  deterministic fake-executable coverage (see "Required tests" below);
  extensions to `tests/unit/test_codex_adapter_conformance.py`,
  `tests/unit/test_operator.py`, `tests/unit/test_serialization.py`,
  `tests/unit/test_wfc_op.py`, and the Front Door family
  `tests/unit/test_commissioning.py` as needed to prove the above
  without weakening any existing assertion.
- The two durable review artifacts this Commission's rendered
  `robust@v1` profile designates for `broad_review` and
  `focused_review`, which are new, uniquely named artifacts distinct
  from any predecessor review artifact.

Do not change `model.py`, `reducer.py`, `AgentExecutionPort`'s shape,
result contracts, workflow programmes/profiles, governance policy,
Runtime promotion mechanisms, token accounting, or the per-role
executable interface. Do not touch `slice4.py`, `vertical.py`,
`persistence.py`, `evidence.py`, `git_facts.py`, `prompt.py`, or
`proof_harness.py`. Do not touch any predecessor review artifact or
predecessor Run evidence.

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
(`python -m pytest -q`) as the Coordinator evidence gate, through the
configured gate itself -- never only an agent-local partial test
invocation, which is exactly the shortfall this successor exists to
close.

The accepted design separately requires a real authenticated Claude
CLI smoke suite before release acceptance (design section 7, last
paragraph). That live suite is **not authorized by this Commission**.
Do not invoke an authenticated Claude provider, inspect credentials,
widen sandbox permissions, or make any network-dependent test
mandatory to satisfy this WorkItem.

### Independent review mandate

A fresh independent reviewer must assess the full materialized and
completed implementation -- every file in scope as it stands after the
Worker's Hop(s), not merely the incremental delta from the preserved
candidate -- for: correctness against the accepted design; faithful
import of the preserved candidate before any intentional correction;
preservation of every security/trust boundary named above; the
Claude adapter's process invocation, settings/environment handling,
sandbox assumptions, wrapper decoding, and failure classification;
Codex regression risk from the shared structured-schema extraction;
exact Run/Hop/Invocation correlation and semantic-validation integrity;
positive and negative fake-CLI cases; the complete suite and trusted
`task-check` evidence; and that no file outside this Commission's
authorized scope was changed. The candidate's historical provenance
provides no correctness credit -- acceptance must rest on this Run's
own actual implementation and evidence. Use normal Robust correction
and rereview routing; disposition any Finding using only the authority
this Commission's rendered profile actually grants. Do not manufacture
additional architectural requirements or expand implementation scope
beyond the accepted design. A successful broad review may accept
directly; focused review is needed only if that phase is reached.


## Acceptance

The preserved candidate was imported faithfully (extracted from
`3a127f64498b7e277d11f5ea23d8806cb5666bd4` without cherry-picking that
commit into trusted history) before any intentional correction.
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
to preserve a `claude_cli` binding. Deterministic fake-CLI tests in
`tests/unit/test_claude_adapter_conformance.py` cover every positive
and negative case listed in "Required tests," and existing Codex
conformance, operator, serialization, `wfc_op`, and Front Door
(`test_commissioning.py`) tests all still pass. The full repository
`task-check` (`python -m pytest -q`) passes through the Coordinator
evidence gate -- not merely an agent-local partial run. No live
Claude CLI invocation, credential inspection, sandbox-permission
widening, or mandatory network-dependent test was added. No file
outside this Commission's authorized scope was changed, and the
candidate ref was not moved, deleted, or mutated. The independent
reviewer's broad (and, if reached, focused) review artifact -- newly
named, distinct from any predecessor review artifact -- records a
genuine adversarial review of the complete materialized
implementation against the mandate above, with no blocking Finding
left open at completion, and explicitly records that candidate
provenance was given no correctness credit.


## Withheld authority

No change to `model.py`, `reducer.py`, `AgentExecutionPort`'s shape,
result contracts, workflow programmes/profiles, governance policy,
Runtime promotion mechanisms, token accounting, or the per-role
executable interface. No provider registry, dynamic routing,
provider fallback, agent hierarchy, generic orchestration layer, or
role-executable/mixed-binding interface -- design section 4.4's
extension remains explicitly out of scope. No live, authenticated
Claude or Codex CLI invocation; no credential inspection or exposure;
no widening of the closed sandbox/tool/settings profile beyond design
section 4.1; no network-dependent test made mandatory. No Runtime
promotion, Evidence mutation, nested WFC invocation, or external
push. No file outside this Commission's rendered `authorized_scope`
for the active phase may be edited, including any predecessor review
artifact or predecessor Run evidence. No mutation, deletion, or
rewrite of the preserved candidate ref
`refs/wfc/candidates/RUN-5740ace2e87549228609e6e5cc225996/H001`, and
no `git cherry-pick`/`merge` of the untrusted preservation commit
`3a127f64498b7e277d11f5ea23d8806cb5666bd4` into this Run's trusted
history. The Worker and reviewer must not weaken or bypass
`validated_result`'s exact correlation/semantic checks for either
provider, must not fabricate a provider process outcome, and must
not treat a provider-requested permission or sandbox mode as a
Coordinator-verified OS boundary. Completing this implementation
grants no automatic authorization for the separately authorized
real-Claude-CLI smoke suite or for any future release decision. The
candidate's historical provenance grants no correctness or acceptance
credit.


## Default escalation

Stop and request maintainer authority if: the accepted design cannot
be implemented within this Commission's exact file scope without a
change to `model.py`, `reducer.py`, the port shape, or governance; a
required Claude CLI behavior assumed by the design is contradicted by
read-only discovery and no fake-testable, design-consistent
alternative can be found within scope; the preserved candidate is
found materially incompatible with the accepted design in a way that
cannot be corrected within this Commission's scope; the
mixed-provider/role-executable extension turns out to be required to
complete this slice; a reviewer-opened Finding cannot be disposed
within this Commission's own granted Finding authority and ordinary
Robust-v1 correction flow; or completing the slice would require any
form of live, authenticated provider invocation.


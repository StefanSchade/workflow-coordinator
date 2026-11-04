# Diagnose and correct the Claude Code adapter's write-permission mismatch for WFC-authorized scope (successor to H001's prerequisite stop)

Diagnose, then correct, the gap recorded in
`docs/backlog/BUG-WFC-CLAUDE-ADAPTER-WRITE-PERMISSION-MISMATCH.adoc`:
a live, noninteractive Claude Code worker process could not perform a
`Write` or a directory-creating `Bash mkdir -p` inside the exact scope
(`docs/developer_guide`) its WFC Commission profile authorized
(`authorized_actions: [edit, commit]`), with both operations denied by
the Claude Code CLI's own permission layer and no approval surface
available to resolve either denial. WFC's own scope/Finding mechanics
are not the defect; `ClaudeCliAdapter`'s effective permission
configuration is.

### Predecessor attempt -- read before anything else

A predecessor Commission for this exact bug,
`wfc-claude-adapter-write-permissions-001`, was already launched once
(`RUN-dcdf990244dd4e9cba19e40dea2d2400`). Its single Hop (`H001`,
`implement`) made no Git change and stopped `awaiting_human` on a
`prerequisite` semantic stop, asking whether a separately scoped,
authenticated real-Claude integration qualification could be
authorized. That mandate explicitly withheld authority for any nested
authenticated `claude` invocation, so the stop was the mandate working
exactly as designed -- it is not evidence of a Coordinator defect, and
it is not grounds to assume a live authenticated smoke is actually
required. Do not re-ask that same question. This Commission instead
grants one additional, narrowly bounded investigation capability not
present in the predecessor (see "Note on CLI introspection" below)
specifically so the mandate can be resolved without it. If, after using
every tool this Commission actually grants, reasonable confidence still
cannot be reached, follow `escalation_default` exactly as written below
-- do not simply restate H001's question.

### Note on provider invocation

This Commission's normal WFC-managed dispatch of the `worker` and
`independent_reviewer` roles as authenticated Codex CLI invocations,
under the maintainer's separately authorized subsequent `prepare`/
`launch`, is expected and is exactly what this Commission exists to
have executed -- it is not prohibited by anything below. What is
prohibited everywhere below is a *nested or independently initiated*
live, authenticated provider invocation that an agent triggers on its
own from inside a Hop -- in particular, opening a real authenticated
`claude` (or any other provider) session to probe permission behavior
against real content or "test" the fix, outside WFC's own dispatch
mechanics. Every later reference in this task to "no authenticated
Claude/Codex CLI invocation" means exactly that nested/independent
case, never the Worker's or reviewer's own ordinary operation as the
WFC-dispatched Codex invocation executing this Commission.

### Note on CLI introspection (new in this successor Commission)

Distinct from the prohibition above, this Commission explicitly
authorizes running the already-installed `claude` executable's own
self-describing, side-effect-free surface as ordinary investigation
evidence -- for example `claude --version` and `claude --help` (and any
subcommand's `--help` text) -- to check the adapter's assumed flag and
environment-variable contract against what the installed CLI itself
documents. This never opens a session, never reads or writes repository
or user content, never authenticates, and never exercises the
permission engine against real content; it is categorically different
from, and does not expand, the still-withheld nested authenticated
invocation above. Network access is not authorized in this Hop's
sandbox; if `--help`/`--version` output and the already-cited accepted
design sources do not suffice to confirm official documented behavior,
say so explicitly in the durable review artifact rather than fetching
external documentation.

### Required reading

Read, in full, before changing anything:

- `docs/backlog/BUG-WFC-CLAUDE-ADAPTER-WRITE-PERMISSION-MISMATCH.adoc`
  in its entirety, including "Suspected mechanisms," "Security
  invariant," "Acceptance criteria," and "Explicit limitation." Do not
  re-derive what it already records as verified fact; do verify its
  two mechanism hypotheses independently rather than assuming either.
- `src/workflow_coordinator/claude_adapter.py` in full -- `argv`
  (`--restricted --safe-mode --strict-mcp-config`, `--permission-mode
  acceptEdits --permission-prompts none`), `_settings`
  (`sandbox.filesystem.allowRead`/`denyRead`, `permissions.deny`,
  `blockReadsOutsideWorkingDirectories`, and the absence of any
  write-scope-equivalent field), and `execute`
  (`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` in the child environment).
- `src/workflow_coordinator/adapter.py` (`AgentExecutionRequest`,
  `AgentExecutionOutcome`, `AgentExecutionPort`) -- read-only context;
  not in this Commission's scope. Note in particular that
  `AgentExecutionRequest` carries only `repository` (the whole
  checkout root), never an explicit per-phase authorized-path list;
  the phase's actual `authorized_scope` lives only in the rendered
  WFC profile, one layer above this adapter, and is never passed into
  the request the adapter receives.
- `tests/unit/test_claude_adapter_conformance.py` in full -- it is
  exclusively a deterministic fake-executable suite; no test in it
  invokes a real `claude` binary or can observe the CLI's own
  permission-engine behavior.
- `governance/commissions/wfc-simple-v1-maintainer-principles-001/profile.yaml`
  and `work-item.yaml`, to see the exact `authorized_scope`/
  `authorized_actions` grant the live attempt's denial contradicted.
- `governance/commissions/wfc-claude-adapter-write-permissions-001/`
  (`intent.yaml`, `work-item.yaml`, `profile.yaml`) -- the predecessor
  Commission's exact mandate and its H001 stop, as summarized above.
- `docs/warm_storage/concepts/workflow_coordinator_claude_code_adapter_design_2026-11-01.adoc`,
  sections 3 ("Permissions and filesystem"), 4.1, 6, and 9 (decision 3:
  widening the provider-side capability request requires maintainer
  approval, "do not solve it with ambient allow rules,
  `excludedCommands`, or an unsandboxed retry") -- the accepted design
  this correction must stay inside.
- `docs/warm_storage/concepts/workflow_coordinator_claude_code_adapter_implementation_continuation_2026-11-01_broad_review.adoc`,
  which independently accepted the current implementation while
  explicitly noting no authenticated provider was invoked and that a
  real Claude smoke test remains a separate, deferred release
  prerequisite -- this Commission does not authorize that smoke test.

### Mandate

1. Establish the actual causal mechanism before writing any fix, using
   every tool this Commission grants: code-level inspection, the
   installed `claude` executable's own `--version`/`--help` introspection
   (see "Note on CLI introspection" above), and deterministic testing.
   Distinguish, with concrete reasoning or a reproducing deterministic
   test: (a) whether `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` causes the CLI
   to fall back to its interactive-default permission mode despite
   `--permission-mode acceptEdits`, as the first qualification attempt's
   own warning text ("Permission mode forced to default —
   CLAUDE_CODE_SUBPROCESS_ENV_SCRUB is set") literally reported, and
   whether `--permission-prompts none` then converts every resulting ask
   into an outright denial for both `Write` and `Bash`; (b) whether the
   generated settings JSON's complete absence of any write-scope/
   `allowWrite`-equivalent field is an independent gap regardless of
   permission mode; and (c) whether `--restricted`'s working-directory
   confinement itself has any distinct interaction with creating a
   not-yet-existing subdirectory. Record which mechanism(s) are
   confirmed, which are ruled out, and the evidence for each -- including
   whether `claude --help`/`--version` documents the exact behavior of
   `--permission-mode`, `--permission-prompts`, `--restricted`, and
   `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` consistently with the bug record's
   hypotheses, or contradicts them. Do not merely restate the bug
   record's hypotheses as a conclusion.
2. Investigate the implication of `AgentExecutionRequest` carrying no
   explicit per-phase authorized-path list. Determine whether a
   correct fix can be made entirely inside `claude_adapter.py`'s own
   `argv`/`_settings`/environment construction -- for example, a
   corrected permission-mode/prompts combination consistent with
   keeping `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`, or an explicit
   write-scope setting derived only from `request.repository` (the
   field the adapter already receives) -- or whether it actually
   requires adding a new phase-scoped authorized-path field to
   `AgentExecutionRequest` and threading it through `operator.py`.
   Strongly prefer the former. If, and only if, investigation
   concludes the latter is genuinely unavoidable, do not implement it:
   stop and escalate per `escalation_default` instead of silently
   expanding the port or operator surface.
3. Implement the narrowest correction the investigation in (1)-(2)
   actually establishes, exclusively within
   `src/workflow_coordinator/claude_adapter.py`. The correction must
   preserve every security invariant
   `BUG-WFC-CLAUDE-ADAPTER-WRITE-PERMISSION-MISMATCH.adoc`'s own
   "Security invariant" section lists verbatim: no
   `--dangerously-skip-permissions`, `--permission-mode
   bypassPermissions`, or other blanket bypass; no widening of
   `sandbox.filesystem.allowRead` beyond the resolved repository root,
   and no `allowWrite`-equivalent broader than the repository root
   (the adapter has no phase-scope information narrower than that to
   grant, per (2)); no removing `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`
   without the investigation in (1) having actually established that
   doing so is the correct and sufficient mechanism, and without
   separately reconfirming the scrubbed-environment authentication
   behavior the accepted design already required (design section
   4.1); and no falling back to Codex's coarser `--sandbox
   workspace-write` model.
4. Do not nest or independently trigger an authenticated `claude` CLI
   process (or any other provider process) from inside a Hop -- for
   example to "test" the fix against a real CLI session, inspect or
   expose credentials, widen sandbox permissions beyond what (3)
   allows, or make any network-dependent step mandatory. This does not
   concern, and does not prohibit, (i) the Worker's or reviewer's own
   ordinary operation as the normal WFC-dispatched Codex invocation
   executing this Commission, or (ii) the side-effect-free
   `claude --version`/`--help` introspection this Commission separately
   authorizes above.
5. Run the repository's full `task-check` evidence command (`python -m
   pytest -q`) through the configured Coordinator evidence gate, and
   provide an exact `commit_intent` containing only paths with actual
   worktree mutation for this Hop.

### Scope

- `src/workflow_coordinator/claude_adapter.py`: the only production
  source file this correction may change. Do not touch
  `codex_adapter.py`, `adapter.py`, `operator.py`, `serialization.py`,
  `model.py`, `reducer.py`, or `scripts/wfc_op.py` -- if the
  investigation concludes any of these must change, stop and escalate
  instead (see `escalation_default`).
- `tests/unit/test_claude_adapter_conformance.py`: add focused,
  deterministic regression tests for the diagnosed mechanism; do not
  weaken or remove any existing assertion.
- The two durable review artifacts this Commission's rendered
  `robust@v1` profile designates for `broad_review` and
  `focused_review`, newly named and distinct from any predecessor
  review artifact.

Do not change any file outside this list. In particular, do not
pre-create `docs/developer_guide/` or otherwise work around the
symptom outside the adapter; do not disable sandboxing globally; do
not permit unsandboxed command execution; do not weaken WFC's own
trusted Git/scope validation; and do not introduce a generic
provider-capability or permissions framework.

### Required semantics

- Every existing Claude structured-output, transport, and
  error-handling behavior (`_candidate_from_wrapper`, timeout
  handling, nonzero-exit handling, diagnostics) is preserved unchanged
  in intent; only the permission-mode/prompts/settings/environment
  construction that causes the diagnosed denial may change.
- `sandbox.enabled`, `failIfUnavailable`, `allowUnsandboxedCommands:
  false`, the empty `network.allowedDomains`, and the `~/`-read deny
  rule are unchanged unless the established mechanism specifically
  requires a narrowly scoped change to one of them -- in which case
  the Worker must explicitly justify that specific change against this
  exact invariant in its `commit_intent` rationale and the durable
  review artifact, not merely make it silently.
- `Invocation`/`AgentExecutionRequest`/`AgentExecutionOutcome` and
  `validated_result`'s exact correlation and semantic-validation
  behavior are unchanged for both providers.
- The Codex adapter and its existing conformance/smoke tests are
  untouched and must still pass unmodified in intent -- this
  correction is Claude-adapter-only.

### Required tests and qualification boundary

Add focused deterministic fake-executable regression tests to
`tests/unit/test_claude_adapter_conformance.py` for exactly the
diagnosed mechanism, at minimum: the resulting `argv` and generated
settings JSON after correction, proving the specific permission-mode/
prompts/settings/environment combination that fixes the denial and
that no unrelated flag, scope, or sandbox setting was widened; and a
negative case proving the corrected configuration still does not grant
`allowRead`/write access, or any flag, beyond the resolved repository
root. Extend, do not replace, the existing positive/negative cases
(successful wrapper extraction, startup/pre-`Popen` failure, nonzero
exit, timeout, malformed wrapper, mismatched correlation, sandbox
refusal) -- every one of them must still pass unmodified in intent.

Per the bug record's own "Explicit limitation" section, these
deterministic fake-executable tests, and this Commission's own
unauthenticated `claude --help`/`--version` introspection, can only
prove what configuration `ClaudeCliAdapter` now generates and what the
installed CLI documents about itself; neither can by itself prove that
a real, authenticated Claude Code CLI process actually grants the
authorized write and directory-creation operations, or that it still
correctly denies operations outside the authorized repository root.
State this boundary explicitly in the durable review artifact,
distinguishing deterministic implementation acceptance (this
Commission's own deliverable) from the separately authorized,
maintainer-run real-Claude smoke that remains the sole conclusive
confirmation and is explicitly **not authorized by this Commission**:
do not claim the live issue is conclusively resolved in the review
artifact's own wording.

### Independent review mandate

A fresh independent reviewer must assess the complete corrected
`claude_adapter.py` and its tests for: whether the Worker actually
established the causal mechanism with concrete evidence (code
inspection, CLI introspection, and/or deterministic tests) rather than
merely asserting a plausible-sounding one; whether the chosen
correction is the narrowest one the established mechanism supports;
whether every named security invariant from the bug record still
holds, including that no scope wider than the resolved repository
root was granted; whether the per-phase authorized-path question in
Mandate step 2 was genuinely investigated and, if a cross-layer change
appeared necessary, correctly escalated rather than silently
implemented; whether the deterministic tests actually exercise the
diagnosed mechanism rather than being tautological; whether any CLI
introspection used stayed strictly within the side-effect-free,
unauthenticated surface this Commission authorizes; and that the
review artifact itself correctly states the fake-executable and
CLI-introspection qualification boundary rather than overclaiming it
as a substitute for a live, authenticated CLI test. Use normal Robust
correction and rereview routing; disposition any Finding using only
the authority this Commission's rendered profile actually grants. A
successful broad review may accept directly; focused review is needed
only if that phase is reached.


## Acceptance

The durable review artifact(s) record a concrete, evidence-based
causal diagnosis of the write-permission denial -- not a restatement
of the bug record's unconfirmed hypotheses as fact, and not a repeat
of predecessor Commission H001's prerequisite stop -- distinguishing
which of the two recorded mechanisms (or another independently found)
is actually responsible, using code inspection, the installed CLI's own
`--help`/`--version` introspection, and/or deterministic tests.
`src/workflow_coordinator/claude_adapter.py` is the only production
source file changed, and the change is the narrowest one the
established diagnosis supports. Every security invariant named in
`BUG-WFC-CLAUDE-ADAPTER-WRITE-PERMISSION-MISMATCH.adoc`'s "Security
invariant" section holds: no blanket permission bypass, no
`allowRead`/write grant wider than the resolved repository root, no
removal of `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` without an established
justification, and no fallback to Codex's coarser sandbox model.
`AgentExecutionRequest`, `operator.py`, `model.py`, `reducer.py`, and
the workflow-program/profile schema are all unchanged -- unless the
Worker instead stopped and escalated per `escalation_default` because
a cross-layer change was genuinely required, in which case this
Commission's acceptance is that clean, well-reasoned escalation, not
an implementation. `tests/unit/test_claude_adapter_conformance.py`
contains new deterministic regression tests that exercise the
diagnosed mechanism and prove no unintended widening, and every
existing test in that file and in the Codex conformance/smoke suites
still passes unmodified in intent. The full repository `task-check`
(`python -m pytest -q`) passes through the Coordinator evidence gate.
No nested or independently initiated authenticated Claude or Codex CLI
invocation was made by either agent, beyond the normal WFC-managed
Codex `worker`/`independent_reviewer` dispatch that runs this
Commission itself and the side-effect-free `claude --help`/`--version`
introspection this Commission separately authorizes; no credential was
inspected or exposed; and the durable review artifact explicitly states
that these deterministic tests and CLI introspection establish
configuration correctness only, with a separately authorized live
authenticated integration test still required before the live issue can
be called conclusively resolved. No file outside this Commission's
authorized scope was changed. The independent reviewer's broad (and, if
reached, focused) review artifact records a genuine adversarial
challenge to the diagnosis and the security implications, with no
blocking Finding left open at completion.


## Withheld authority

No nested or independently initiated live, authenticated Claude or
Codex CLI invocation by either agent -- the normal WFC-managed Codex
`worker`/`independent_reviewer` dispatch that executes this Commission,
under the maintainer's separately authorized subsequent `launch`, is
expected and is not withheld by this clause, and neither is the
side-effect-free `claude --version`/`--help` introspection this
Commission separately authorizes (never a session, never a read/write
against real content, never authentication). No credential inspection
or exposure; no authorization for the separately required
real-authenticated-Claude-CLI integration smoke test this Commission's
task describes but does not grant; no claim in any durable artifact
that the live issue is conclusively resolved absent that smoke. No
change to `src/workflow_coordinator/codex_adapter.py`, `adapter.py`,
`operator.py`, `serialization.py`, `model.py`, `reducer.py`,
`scripts/wfc_op.py`, any workflow program or profile schema, or any
governance file. No new provider-capability registry, generic
permissions/sandbox framework, dynamic routing, or provider fallback.
No global disabling of sandboxing and no unsandboxed command execution
of any kind. No widening of `allowRead`, or any `allowWrite`-equivalent,
beyond the resolved repository root. No removal of
`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` without an evidenced mechanism
justification recorded in the durable review artifact. No weakening of
WFC's own trusted Git/scope validation or of `validated_result`'s exact
correlation/semantic checks for either provider. No file outside this
Commission's rendered `authorized_scope` for the active phase may be
edited, including any predecessor review artifact or predecessor Run
evidence. No relaunch, resume, or rewrite of predecessor Run
`RUN-dcdf990244dd4e9cba19e40dea2d2400` or its recorded `awaiting_human`
state. No Runtime promotion, Evidence mutation, nested WFC invocation,
`prepare`, `launch`, or external push by either agent.


## Default escalation

Stop and request maintainer authority if: a correct fix is found to
require adding a per-phase authorized-path field to
`AgentExecutionRequest` or otherwise touching `operator.py` or the
profile/program schema; a correct fix is found to require any change
outside `src/workflow_coordinator/claude_adapter.py` and its
conformance tests; preserving a named security invariant is found to
be incompatible with any fix that actually resolves the denial; or a
reviewer-opened Finding cannot be disposed within this Commission's own
granted Finding authority and ordinary Robust-v1 correction flow. Do
not stop merely because only a live authenticated Claude Code CLI
invocation could give full certainty of the mechanism -- that
uncertainty is expected and is exactly why `acceptance` above requires
stating the fake-executable/CLI-introspection qualification boundary
explicitly rather than requesting authority for the withheld smoke
test; implement the narrowest correction the strongest evidence
actually available (code inspection, CLI introspection, deterministic
tests) supports, and record the residual uncertainty in the durable
review artifact instead of escalating for it.


# Replace full-environment inheritance in the Claude Code adapter with an explicit allowlist, stop defaulting to environment scrubbing, and add a narrow operator-configurable extension point for additional variable names

Correct `src/workflow_coordinator/claude_adapter.py`'s child-process
environment construction. Read
`docs/backlog/BUG-WFC-CLAUDE-ADAPTER-WRITE-PERMISSION-MISMATCH.adoc` in full
first (its "Suspected mechanisms", "Security invariant", and "Acceptance
criteria" sections in particular) and
`docs/backlog/FTR-WFC-SELF-DEVELOPMENT-EXECUTION-INTEGRATION-DEBT.adoc` §2;
neither is restated here. This Commission's own authority_source above
records the maintainer-run controlled A/B experiment that now supplies the
"evidenced mechanism justification" the bug record's Security invariant
section required before removing `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` --
treat that narrative as established evidence, not as something to
independently reproduce; no nested or independently initiated authenticated
Claude or Codex CLI invocation is authorized inside this Commission's own
Hops.

### Mandate

1. In `ClaudeCliAdapter.execute`, replace `{**os.environ, "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB":
   "1", "TMPDIR": ..., "TMP": ..., "TEMP": ...}` with construction from an
   explicit, minimal allowlist of variable *names* (not a filtered copy of
   `os.environ`), covering exactly what CLI startup, file-based
   authentication, locale, and sandbox initialization need on this
   deployment (at minimum `PATH`, `HOME`, `USER`, `LANG`; include
   `XDG_RUNTIME_DIR` if the sandbox mechanism needs it -- verify by
   inspection/reasoning, do not assume). Do not add any `CLAUDE_CODE_*`
   variable to this allowlist. Preserve the existing short-TMPDIR fix
   (`_SHORT_TMP_ROOT`, `_short_subprocess_tmp_dir`) and the
   `TMPDIR`/`TMP`/`TEMP` triplet unchanged, set per-invocation as today, not
   from the allowlist. Do not set `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` at all
   by default.
2. Add one narrow, explicit extension point letting an operator supply
   additional allowed variable *names* without a new WFC release or any
   governance/profile/policy/schema change -- for example an adapter
   constructor field defaulting to empty, populated from a single simple,
   documented source of the implementer's own choosing (such as one
   environment variable read once at adapter construction, or a small local
   file path). It must only ever add variable *names* to the allowlist
   (values still come from the orchestrating process's own environment at
   launch time); it must never accept raw key=value pairs, must never widen
   `sandbox.filesystem`/`permissions` scope, and must leave today's
   zero-configuration behavior (the minimal allowlist, no scrub) as the
   default with no operator action. Do not build a general
   permissions/environment framework; this is a narrow, single-purpose
   extension point.
3. Add focused, deterministic fake-executable regression tests to
   `tests/unit/test_claude_adapter_conformance.py` proving: the exact
   effective child-environment keys constructed for a representative
   request (no more, no fewer than the allowlist plus the per-invocation
   TMPDIR triplet); that `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` is absent by
   default; that `CLAUDE_CODE_*` variables artificially present in the
   *test process's own* environment (monkeypatched) are never copied into
   the child environment; and that the new extension point adds only the
   names it is given, additively, without otherwise changing the default
   allowlist. Extend, do not replace, existing cases; every one must still
   pass unmodified in intent.
4. Add one new, clearly `WFC_RUN_LIVE_CLAUDE_SMOKE=1`-gated opt-in live
   integration test to `tests/integration/test_claude_adapter_live_smoke.py`
   (same opt-in convention as the existing TMPDIR smoke test in that file;
   do not change that existing test's intent) that runs a worker-authorized
   Bash command analogous to the one the authority_source's A/B experiment
   used, against the corrected adapter, and asserts it actually succeeds end
   to end -- regression coverage for the now-confirmed Bash functionality.
   Like its neighbor, it must stay skipped by default so ordinary
   `python -m pytest -q` never depends on it or on network/authenticated-CLI
   access.
5. Record, in the `broad_review` durable artifact: the diagnosis and its
   evidentiary basis (the A/B experiment plus this record's own code
   inspection), the implemented allowlist and extension-point design and why
   each is the narrowest fit, and explicit residual uncertainty -- in
   particular that this correction does not by itself constitute the
   separate live-provider qualification stage the bug record's "Acceptance
   criteria" section describes, and that a real post-promotion WFC Hop under
   the corrected Runtime remains a distinct, subsequent, maintainer-owned
   integration-proof step outside this Commission.

### Independent review mandate

Assess whether the allowlist is actually explicit and minimal (no silent
`os.environ` passthrough of anything beyond the named allowlist and the
per-invocation TMPDIR triplet); whether no `CLAUDE_CODE_*` variable can reach
the child by any path including the new extension point; whether the
extension point is genuinely a narrow name-only additive mechanism rather
than a general framework or a scope-widening vector; whether the existing
short-TMPDIR fix and every security invariant the bug record lists still
hold unchanged; whether the new tests exercise these guarantees rather than
being tautological; and whether the review artifact honestly states residual
uncertainty, in particular not overclaiming live-provider qualification this
Commission was never authorized to perform. A reviewer who finds the
allowlist non-minimal, a leakage path open, or a security invariant violated
should open a blocking Finding and route to correction. Use normal Robust
correction and re-review routing; disposition any Finding using only the
authority this Commission's rendered profile actually grants. A successful
broad review may accept directly; focused review is needed only if that
phase is reached.


## Acceptance

This Commission is accepted on deterministic implementation grounds alone:
`src/workflow_coordinator/claude_adapter.py` as the only production source
file changed, replacing `**os.environ` inheritance with an explicit minimal
allowlist, no default `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`, the existing
short-TMPDIR fix preserved unchanged, and a narrow name-only operator
extension point that changes nothing by default; new deterministic
regression tests in `tests/unit/test_claude_adapter_conformance.py`
exercising the effective environment shape and non-inheritance of
`CLAUDE_CODE_*` session variables, plus one new opt-in live regression test
in `tests/integration/test_claude_adapter_live_smoke.py`, with every existing
test in both files and the Codex conformance/smoke suites still passing
unmodified in intent; and the broad (and, if reached, focused) review
artifact recording the diagnosis, the correction, and explicit residual
uncertainty, including that live-provider qualification and the subsequent
real WFC Hop remain separate, non-blocking, maintainer-owned next steps not
established by this Commission. The full repository `task-check`
(`python -m pytest -q`) passes through the Coordinator evidence gate. No
file outside this Commission's authorized scope is changed. No nested or
independently initiated authenticated Claude or Codex CLI invocation occurs
beyond normal WFC dispatch.


## Withheld authority

No nested or independently initiated live, authenticated Claude or Codex CLI
invocation by either agent, beyond the normal WFC-managed worker/
independent_reviewer dispatch that executes this Commission. No credential
inspection or exposure. No claim in any durable artifact that live-provider
qualification, or integration proof under a promoted Runtime, is established
by this Commission. No change to any file other than
`src/workflow_coordinator/claude_adapter.py`,
`tests/unit/test_claude_adapter_conformance.py`,
`tests/integration/test_claude_adapter_live_smoke.py`, and this Commission's
durable review artifacts -- in particular no change to `codex_adapter.py`,
`adapter.py` (the generic `AgentExecutionPort`), `operator.py`,
`serialization.py`, `model.py`, `reducer.py`, `scripts/wfc_op.py`, any
workflow-program or profile/policy schema, either backlog record named
above, or any other governance file. No general provider-capability
registry or generic permissions/sandbox framework; the new extension point
must stay a narrow, name-only, additive allowlist mechanism. No
`--dangerously-skip-permissions`, `--permission-mode bypassPermissions`, or
other blanket bypass. No widening of `sandbox.filesystem.allowRead`,
`permissions.allow`, or any write-scope-equivalent beyond what the adapter
already grants today. No change to `max_autonomous_hops`, the Coordinator
Hop ceiling, or any other governance limit. No Runtime promotion, Evidence
mutation, nested WFC invocation, `prepare`, or external push by either
agent.


## Default escalation

Stop and request maintainer authority only if: the diagnosis concludes a
correct fix genuinely requires a change outside the four files this
Commission's scope names; preserving a named security invariant is found
incompatible with any allowlist design the diagnosis supports; or a
reviewer-opened Finding cannot be disposed within this Commission's own
granted authority and ordinary Robust-v1 correction flow. Do not stop merely
because only a live, authenticated, post-promotion WFC Hop could give full
integration certainty -- that is explicitly a separate, later, non-blocking
maintainer step, not a prerequisite for this Commission's own acceptance.


# Implement a bounded, evidence-based correction to the Claude Code adapter's write-permission denial -- acceptance is implementation-based, not proof-based; live-provider qualification is explicitly out of scope

Correct the gap recorded in full in
`docs/backlog/BUG-WFC-CLAUDE-ADAPTER-WRITE-PERMISSION-MISMATCH.adoc`:
read that record in its entirety before changing anything -- it already
carries the verified failure facts, both mechanism hypotheses, the
security invariant your correction must preserve, the predecessor
Attempts' provenance, and its own two-stage "Acceptance criteria" split
between deterministic implementation acceptance and separate
live-provider qualification. Nothing in that record is restated here;
treat it as the authoritative source, not a prompt to summarize back.

### Read this before anything else

Three predecessor Commissions for this exact bug were already launched
and each stopped `awaiting_human` at Hop 1 with zero Git change, every
time on a variant of the same trap: treating the absence of a live,
authenticated Claude CLI invocation -- which no Commission for this bug
has ever granted, including this one -- as an unresolved prerequisite
rather than as a permanent, already-understood boundary of this
Commission's own authority. `docs/wfc_operator_manual.adoc` §§22.5-22.6
and `docs/self-development.adoc`'s Commission-authoring section now
document this generically, citing those three Attempts by name. Read
both before forming your diagnosis.

**This Commission's acceptance bar is implementation-based, not
proof-based.** You are expected to reach a reasoned, best-supported
diagnosis using code inspection, the installed `claude` executable's own
side-effect-free `--version`/`--help` introspection (authorized below),
and deterministic fake-executable tests -- then implement against it.
You are *not* expected, and must not attempt, to obtain or wait for
conclusive proof that your candidate correction actually works against a
real, authenticated Claude Code CLI session. That proof is unobtainable
within this Commission's granted authority by design: only a separately
authorized, maintainer-run live-provider qualification -- not part of
this Commission, not something you can trigger -- can ever supply it,
per the bug record's own "Acceptance criteria." Its unavailability is not
a missing prerequisite, not a withheld-authority conflict, and not
grounds to stop or escalate. If you find yourself about to report
`blocked` or escalate because you cannot be certain your fix actually
works against the real CLI, stop and reread this paragraph: that is
exactly the trap all three predecessor Attempts fell into, and this
sentence exists to tell you not to repeat it. Record your diagnosis and
its residual uncertainty in the durable review artifact instead of
withholding implementation for it.

The resolved-repository-root write boundary that predecessor
`-002` asked about, and that committed maintainer documentation
(`docs/architecture.md`, `docs/self-development.adoc`) already answers,
is settled, authoritative fact, not an open question -- do not re-ask it.

### Authorized CLI introspection

Running the already-installed `claude` executable's own self-describing,
side-effect-free surface -- `claude --version`, `claude --help`, and any
subcommand's `--help` text -- is authorized as investigation evidence.
This never opens a session, authenticates, or touches repository/user
content. No other `claude` or `codex` invocation may be triggered from
inside a Hop, nested or independent of this Commission's own normal
WFC-dispatched worker/reviewer execution (which is itself expected, not
prohibited by this clause).

### Mandate

1. Form a diagnosis of the write-permission denial using code inspection
   of `src/workflow_coordinator/claude_adapter.py` (`argv`, `_settings`,
   `execute`'s environment construction), the authorized CLI
   introspection above, and reasoning about the bug record's two
   recorded hypotheses (or another you independently find). State your
   reasoning, which hypothesis(es) your evidence best supports, and your
   confidence level. You are not required to rule out the alternative
   with certainty before proceeding.
2. Implement the narrowest correction your diagnosis supports, entirely
   within `src/workflow_coordinator/claude_adapter.py`, scoped to
   `request.repository` -- the settled boundary from "Read this before
   anything else" above. Preserve every security invariant the bug
   record's "Security invariant" section lists verbatim (cite it there;
   do not restate it here). If your diagnosis concludes the fix
   genuinely requires touching any other production file, stop and
   escalate per `escalation_default` instead of implementing it.
3. Add focused, deterministic fake-executable regression tests to
   `tests/unit/test_claude_adapter_conformance.py` proving the resulting
   `argv`/settings/environment shape and a negative case proving no
   widening beyond the resolved repository root. Extend, do not replace,
   existing cases; every one must still pass unmodified in intent.
4. Record, in the `broad_review` durable artifact: the diagnosis and its
   evidence, the implemented correction and why it is the narrowest fit,
   and explicit residual uncertainty -- naming exactly what only the
   separately authorized live-provider qualification could further
   confirm. Do not claim the live issue is conclusively resolved.

### Independent review mandate

Assess whether the diagnosis is actually evidence-based rather than
asserted; whether the correction is the narrowest fit, scoped correctly,
and preserves every named security invariant; whether the new tests
exercise the diagnosed mechanism rather than being tautological; and
whether the review artifact honestly states residual uncertainty without
demanding proof neither the Worker nor this Commission was ever
authorized to obtain. A reviewer who finds the diagnosis unsupported or a
security invariant violated should open a blocking Finding and route to
correction, not escalate for live-provider certainty either -- the same
acceptance bar above applies to review. Use normal Robust correction and
rereview routing; disposition any Finding using only the authority this
Commission's rendered profile actually grants. A successful broad review
may accept directly; focused review is needed only if that phase is
reached.


## Acceptance

This Commission targets exactly `docs/backlog/BUG-WFC-CLAUDE-ADAPTER-WRITE-PERMISSION-MISMATCH.adoc`'s
own "Deterministic implementation acceptance" stage, and is accepted on
that stage alone: an evidence-supported diagnosis naming which recorded
mechanism (or another independently found) is most plausible;
`src/workflow_coordinator/claude_adapter.py` as the only production
source file changed, with the narrowest correction the diagnosis
supports, scoped to the resolved repository root; every security
invariant the bug record lists still holding; new deterministic
regression tests in `tests/unit/test_claude_adapter_conformance.py`
exercising the diagnosed mechanism and proving no unintended widening,
with every existing test in that file and the Codex conformance/smoke
suites still passing unmodified in intent; and the broad (and, if
reached, focused) review artifact recording the diagnosis, the
correction, and explicit residual uncertainty without claiming the live
issue is conclusively resolved. The bug record's separate
"Live-provider qualification" stage is explicitly out of scope for this
Commission; its absence does not block, qualify, or diminish this
acceptance. The full repository `task-check` (`python -m pytest -q`)
passes through the Coordinator evidence gate. No file outside this
Commission's authorized scope is changed. No nested or independently
initiated authenticated Claude or Codex CLI invocation occurs beyond
normal WFC dispatch and the authorized side-effect-free introspection.


## Withheld authority

No nested or independently initiated live, authenticated Claude or Codex
CLI invocation by either agent, beyond the normal WFC-managed Codex
worker/independent_reviewer dispatch that executes this Commission and
the side-effect-free `claude --version`/`--help` introspection authorized
above. No credential inspection or exposure. No authorization for, and no
claim in any durable artifact that the live issue is conclusively
resolved absent, the separately required real-authenticated-Claude-CLI
integration smoke. No change to any file other than
`src/workflow_coordinator/claude_adapter.py`,
`tests/unit/test_claude_adapter_conformance.py`, and this Commission's
two durable review artifacts -- in particular no change to
`codex_adapter.py`, `adapter.py`, `operator.py`, `serialization.py`,
`model.py`, `reducer.py`, `scripts/wfc_op.py`, any workflow-program or
profile schema, `docs/backlog/BUG-WFC-CLAUDE-ADAPTER-WRITE-PERMISSION-MISMATCH.adoc`,
or any other governance file. No new provider-capability registry or
generic permissions/sandbox framework. No design or implementation of any
part of a WFC-owned bubblewrap execution-isolation layer. No
`--dangerously-skip-permissions`, `--permission-mode bypassPermissions`,
or other blanket bypass. No widening of `sandbox.filesystem.allowRead`,
or any write-scope-equivalent, beyond the resolved repository root. No
removal of `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` without an evidenced
mechanism justification recorded in the durable review artifact, and
without the implementation preserving the accepted design's scrubbed-
environment authentication behavior. No fallback to Codex's
`--sandbox workspace-write` model. No weakening of WFC's own trusted
Git/scope validation or of `validated_result`'s correlation/semantic
checks for either provider. No relaunch, resume, or rewrite of
predecessor Runs `RUN-dcdf990244dd4e9cba19e40dea2d2400`,
`RUN-878fbc45e9454cef93cccea7423475ee`, or
`RUN-19d5cc40915d466b8790171f3308a4f4`, or their recorded
`awaiting_human` state. No change to `max_autonomous_hops`, the
Coordinator Hop ceiling, or any other governance limit. No Runtime
promotion, Evidence mutation, nested WFC invocation, `prepare`, or
external push by either agent.


## Default escalation

Stop and request maintainer authority only if: the diagnosis concludes a
correct fix genuinely requires a change outside
`src/workflow_coordinator/claude_adapter.py` and its conformance tests;
preserving a named security invariant is found to be incompatible with
any fix the diagnosis supports; or a reviewer-opened Finding cannot be
disposed within this Commission's own granted Finding authority and
ordinary Robust-v1 correction flow. Do not stop merely because only a
live authenticated Claude Code CLI invocation could give full certainty
of the mechanism, and do not re-ask any predecessor Run's
already-resolved question -- implement the narrowest correction the
strongest evidence actually available supports, and record residual
uncertainty in the durable review artifact instead of escalating for it.


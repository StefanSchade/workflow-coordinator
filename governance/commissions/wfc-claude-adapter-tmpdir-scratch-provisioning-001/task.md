# Give the Claude adapter a portable, WFC-owned external scratch target for its short-TMPDIR symlink, with safe concurrent creation and reliable cleanup on success, error, and timeout

Implement the correction recorded in
`docs/backlog/BUG-WFC-CLAUDE-ADAPTER-SHORT-TMPDIR-RESOURCE-EXHAUSTION.adoc`.
Treat that record's "Why it happened", "Required product behavior", and
both "Follow-up live qualification" sections as the controlling
specification and established evidence; this text restates their
requirements but that record remains authoritative if any tension is
found. Do not re-investigate the live-qualification findings (summarized
in this Commission's own `authority_source` above); treat them as given.
Do not attempt to characterize or fix the separate, unrelated `python3 -c`
inline-file-I/O denial observation noted there -- it is explicitly out of
scope.

### Required reading

Read, in full, before implementing:

- `docs/backlog/BUG-WFC-CLAUDE-ADAPTER-SHORT-TMPDIR-RESOURCE-EXHAUSTION.adoc`
  (this Commission's controlling record, in full).
- `src/workflow_coordinator/claude_adapter.py` in full, especially
  `_SHORT_TMP_ROOT`, `_short_subprocess_tmp_dir()`, `_settings()`, and
  `execute()`'s construction of `environment` and its `finally` cleanup.
- `tests/unit/test_claude_adapter_conformance.py` and
  `tests/integration/test_claude_adapter_live_smoke.py` in full, as the
  existing regression-test conventions (fake-executable determinism; the
  `WFC_RUN_LIVE_CLAUDE_SMOKE=1` opt-in live convention) this Commission's
  own new tests must extend, not replace.
- `scripts/wfc_op.py`'s `create_attempt_scratch()` and
  `_scratch_environment()` (read-only; this file is not in this
  Commission's scope and must not be edited), to understand the ambient,
  already-portable, already-generic large-filesystem scratch convention
  named in this Commission's `authority_source` above.

Do not treat any line number anywhere in this task or its authority_source
as exact without re-verifying it against the actual Development HEAD read
during this Run.

### Mandate

Correct `ClaudeCliAdapter` so that:

1. **Portable external-scratch provisioning.** The adapter obtains a
   dedicated, untrusted, large-capacity external scratch subpath without
   assuming any self-development-specific setup and without requiring a
   privileged or administrative host action (no system-wide `/tmp`
   resize, no administrator-installed symlink, no mount performed outside
   WFC's own process tree). It must work, as the ordinary case, at
   minimum on Ubuntu under WSL and in restrictive corporate environments
   where the operator cannot assume elevated host privileges. Investigate
   the existing ambient large-filesystem `TMPDIR`/`TMP`/`TEMP` convention
   named in `authority_source` above as a candidate source for this
   target; a different, technically better concrete design is acceptable
   if it satisfies this same portability requirement and the controlling
   record's "Required product behavior" section, but inventing a new
   sandbox architecture is not authorized.
2. **Short symlink, safe under concurrent invocations.** A short path
   under `/tmp` (or an equivalent short root) is created as a symlink to
   that dedicated external scratch subpath. Creation must be safe when
   multiple Hops/Attempts execute concurrently on the same host: no two
   concurrent invocations may collide on, corrupt, or silently reuse each
   other's target, and a leftover artifact from a prior invocation at the
   same derived path (for example after an unclean prior shutdown) must
   be handled deterministically rather than causing an unhandled
   exception or silently operating on the wrong target.
3. **Correct permissions and scoped sandbox release.** The real external
   scratch target directory is created with restrictive permissions
   (owned by the invoking user, not group/world-accessible), consistent
   with the existing restrictive convention `create_attempt_scratch()`
   already applies to its own scratch directories. `_settings()`'s
   `sandbox.filesystem.allowRead`/`allowWrite` grant names exactly the
   real resolved target directory for this invocation (never the
   symlink, never a broader ancestor, never the Attempt's entire scratch
   root if the adapter's own dedicated subpath is narrower than that).
4. **Reliable cleanup in every outcome.** Both the short symlink and the
   real external target directory (and everything created under it) are
   removed when the invocation completes successfully, when it fails
   locally before or during launch, and when it times out -- the existing
   `finally: shutil.rmtree(tmp_dir, ignore_errors=True)` is a confirmed
   no-op on a symlink today (`claude_adapter.py`'s `finally` block) and
   must be corrected so neither the symlink nor its real target
   accumulates across invocations under any of these three outcomes.
5. **Deterministic regression tests.** See "Required tests" below.

No general logging architecture, no configurable verbosity mechanism, no
new permission profile beyond the scoped `allowRead`/`allowWrite` grant
already described, and no change to workflow/Hop/review semantics,
Result Contracts, or Trusted Commit. No change to `codex_adapter.py`,
`adapter.py`, `operator.py`, `model.py`, `reducer.py`, `vertical.py`,
`git_facts.py`, `commissioning.py`, `scripts/wfc_op.py`, or any file
outside this Commission's `scope` below.

### Required tests

Extend (do not replace) `tests/unit/test_claude_adapter_conformance.py`
with deterministic, fake-executable coverage proving:

- Resource placement: the subprocess's effective `TMPDIR`/`TMP`/`TEMP` is
  a short path; that short path is a symlink; the symlink's real target
  resolves under the dedicated external scratch location the correction
  establishes, not under `/tmp` itself; `sandbox.filesystem.allowRead`/
  `allowWrite` in the generated settings name exactly that real resolved
  target.
- Cleanup: after a successful invocation, after a non-zero-exit/launch
  failure, and after a timeout (mirroring the existing
  `test_subprocess_tmpdir_is_cleaned_up_even_on_timeout` pattern), neither
  the short symlink nor the real external target directory still exists.
- Concurrency/collision safety: a pre-existing leftover at the same
  derived short-symlink path (and/or real-target path) from a simulated
  prior invocation is handled deterministically -- either safely
  superseded or rejected with a clear, actionable error -- never silently
  operating on an unrelated pre-existing non-WFC path.
- Every existing test in this file and in
  `tests/integration/test_claude_adapter_live_smoke.py` still passes
  unmodified in intent (update only what the corrected `TMPDIR`/settings
  shape mechanically requires, such as literal expected-settings
  dictionaries).

Add exactly one new, clearly `WFC_RUN_LIVE_CLAUDE_SMOKE=1`-gated opt-in
live integration test to `tests/integration/test_claude_adapter_live_smoke.py`,
following that file's existing opt-in convention, that runs a real Claude
CLI invocation whose Bash-tool command creates a file and asserts that
file physically exists under the real external scratch target on the host
filesystem (not merely that the adapter reports success) -- regression
coverage for the live-qualified mechanism this Commission implements.
Like its neighbors, it must stay skipped by default so ordinary
`python -m pytest -q` never depends on it or on network/authenticated-CLI
access.

Run the repository's `task-check` evidence command (`python -m pytest
-q`) as the Coordinator evidence gate.

### CommitIntent path granularity

Every `CommitIntent.paths` entry any Hop under this Commission proposes
must name a concrete, individually mutated leaf file path -- never a
directory. See this Commission's `authority_source` above
(`BUG-WFC-COMMIT-INTENT-DIRECTORY-PATH-RECONCILIATION`) for why a
directory-shaped entry deterministically fails trusted-commit
reconciliation and stops the Run before review is ever reached.

### Independent review mandate

The independent reviewer must inspect the actual implementation and tests
-- not merely a diff summary -- for: genuine portability (no hidden
assumption of a privileged host action or of the specific self-development
scratch layout); the symlink mechanism's safety under concurrent/repeated
invocations, including the stale-leftover case; that `allowRead`/
`allowWrite` names exactly the real resolved target and nothing broader;
that cleanup actually removes both the symlink and its real target under
success, failure, and timeout, with no new accumulation path introduced;
that no file outside this Commission's scope was touched and no
workflow/Hop/review/Result-Contract/Trusted-Commit semantics changed; and
that every required test is present and actually exercises the claimed
guarantees rather than being tautological. Use normal Robust correction
and re-review routing; disposition any Finding using only the authority
this Commission's rendered profile actually grants. A successful broad
review may accept directly; focused review is needed only if that phase
is reached.


## Acceptance

`ClaudeCliAdapter` provisions its short-`TMPDIR` symlink's real target
from a dedicated, large-capacity, untrusted external scratch subpath
obtained without any privileged host action and without assuming the
specific self-development scratch layout; symlink creation is safe under
concurrent/repeated invocations, including a pre-existing leftover at the
same derived path; `sandbox.filesystem.allowRead`/`allowWrite` names
exactly the real resolved target; both the symlink and its real target are
reliably removed on success, local failure, and timeout. Every required
test from "Required tests" exists and passes, every pre-existing test in
`tests/unit/test_claude_adapter_conformance.py` and
`tests/integration/test_claude_adapter_live_smoke.py` still passes
unmodified in intent (aside from mechanically necessary literal-value
updates), and `task-check` (`python -m pytest -q`) passes against the full
suite. No file outside this Commission's `scope` is changed; in
particular `codex_adapter.py`, `adapter.py`, `operator.py`, `model.py`,
`reducer.py`, `vertical.py`, `git_facts.py`, `commissioning.py`, and
`scripts/wfc_op.py` are byte-identical to their state before this Attempt.
No workflow/Hop/review semantics, Result Contract, or Trusted Commit
behavior changed. Every `CommitIntent.paths` entry proposed by any Hop
names only concrete leaf files. The independent reviewer's broad (and, if
reached, focused) review artifact records a genuine adversarial review
against the mandate above, with no blocking Finding left open at
completion.


## Withheld authority

No additional provider-credential isolation and no credential broker. No
integration of the whole Claude CLI into an outer Bubblewrap boundary as
part of this Commission, and no nested-Bubblewrap experiments. No change
to internet/network policy (`network.allowedDomains` stays `[]`). No
change to workflow/Hop/review semantics, Result Contracts, or Trusted
Commit. No change to `codex_adapter.py` unless a concrete, evidenced,
necessary shared-interface requirement is found and recorded in the
broad-review artifact -- and even then, no such change is authorized by
this Commission's `scope`, which does not include that file; discovering
such a requirement is an escalation, not an implementation license. No
change to any file other than
`src/workflow_coordinator/claude_adapter.py`,
`tests/unit/test_claude_adapter_conformance.py`,
`tests/integration/test_claude_adapter_live_smoke.py`, and this
Commission's own durable review artifacts -- in particular no change to
`adapter.py`, `operator.py`, `model.py`, `reducer.py`, `vertical.py`,
`git_facts.py`, `commissioning.py`, or `scripts/wfc_op.py`. No widening of
`sandbox.filesystem.allowRead`/`allowWrite` beyond exactly the real
resolved per-invocation external scratch target, no widening of
`permissions.allow`/`deny` or `blockReadsOutsideWorkingDirectories`, no
`--dangerously-skip-permissions`, `--permission-mode bypassPermissions`,
or other blanket bypass. No change to `max_autonomous_hops`, the
Coordinator Hop ceiling, or any other governance limit. No Runtime
promotion, no push, no nested or independently initiated authenticated
Claude or Codex CLI invocation by either agent beyond normal WFC
dispatch. No directory-shaped `CommitIntent.paths` entry (see "CommitIntent
path granularity" above). No characterization or fix of the separate
`python3 -c` inline-file-I/O denial observation.


## Default escalation

Stop and request maintainer authority if: a correct fix genuinely
requires a change outside the three files this Commission's scope names;
satisfying the portability requirement is found incompatible with every
design available without a privileged host action; a reviewer-opened
Finding cannot be disposed within this Commission's own granted authority
and ordinary Robust-v1 correction flow; or the diagnosis concludes a
necessary, evidenced shared-interface change to `codex_adapter.py` is
required. Do not stop merely because only a real post-launch WFC Hop
under this very Commission could give full end-to-end confidence against
a live Claude CLI -- that live qualification is exactly what this
Commission's own authorized Attempt performs; do not treat the maintainer's
already-recorded 2026-11-07/08 live-qualification experiments as leaving
this open, and do not re-run them as a precondition for implementing.


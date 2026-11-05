# Implement FD-08 -- inject a private, Attempt-specific Harness scratch directory into the prepared execution environment

Implement the accepted FD-08 investigation's "Minimal recommended
implementation" (section 5) and its validation/lifecycle requirements
(section 6) exactly, as one bounded vertical slice confined to the
Harness (`scripts/wfc_op.py`) layer. Treat
docs/cold_storage/backlog/pre-0.1.0b1/front-door/workflow_coordinator_fd08_scratch_investigation_2026-10-30.adoc
as the controlling implementation specification for this WorkItem. Do
not treat its own non-claims (section 10) or reserved decisions (section
9) as license to expand scope beyond what section 5/6/7/8 actually
specify.

### Required reading

Read, in full, before implementing:

- The accepted investigation report (all 10 sections), as grounding
  fact and the controlling design.
- The motivating incident report,
  docs/cold_storage/backlog/pre-0.1.0b1/infrastructure-and-incidents/2026-10-28_tmpfs_exhaustion.adoc.
- `scripts/wfc_op.py`: `validate_attempt()`, `ops_root()`,
  `state_root()`, `cmd_prepare()` (around wfc_op.py:719-809),
  `prepare_runtime_environment()`/`prepare_development_environment()`
  (around wfc_op.py:334-406), `run_canonical_preflight_seam()` (around
  wfc_op.py:487-510), `write_launch_sh()` (around wfc_op.py:552-593),
  `write_preparation_pins()` and `write_harness_record()` (around
  wfc_op.py:517-549, :596-626), `cmd_launch()` (around wfc_op.py:812-858),
  `cmd_status()`, and `cmd_archive()`/`_invoke_canonical_archive()`
  (around wfc_op.py:1060-1123).
- `src/workflow_coordinator/git_facts.py`'s `observe_repository()`
  (around git_facts.py:472-495, the incident's exact failure site) and
  `salvage_inventory()`/the omissions salvage helper (around
  git_facts.py:83-122, :247-277), read-only, to confirm (do not change)
  that their default `tempfile.mkstemp()` calls already honor an
  inherited `TMPDIR`/`TMP`/`TEMP` with no source change required there.
- `tests/unit/test_wfc_op.py`'s existing preparation/launch coverage, to
  extend consistently rather than duplicate fixtures.

Do not treat any line number above as exact without re-verifying it
against the actual Development HEAD read during this Run; these were
confirmed unchanged between the investigation's inspected baseline
(`5c558452dd1c2f9811cc2e35ebc7dabda352d860`) and this Commission's
pinned program/policy/components commit
(`8389a7c442cd05e000b5fcbc38b7e660b7f64f45`) at authoring time, but
re-verify before relying on them.

### Scope

Implement exactly, confined to the Harness layer the investigation
identifies as sufficient (section 5: "No Runtime --scratch-root is
necessary for this slice"):

- `scripts/wfc_op.py`:
  - Add one optional `prepare` CLI input, `--scratch-root
    ABSOLUTE_DIRECTORY`, defaulting to `$HOME/wfc-scratch` when absent.
    Do not guess filesystem capacity or alter any `/tmp` mount.
  - A path/ownership/mode/overlap validation helper implementing section
    6's "Recommended validation and failure behavior" exactly: require
    an absolute, expanded root; create the default if absent; reject a
    root or attempt-scratch path that is a symlink or non-directory;
    after creating the per-Attempt directory, verify effective-uid
    ownership and `0700` mode; reject an already-existing attempt-scratch
    directory (never reuse or delete one); reject an effective scratch
    path equal to, inside, or an ancestor of the pinned Development,
    Runtime, or Evidence repository, and require it distinct from OPS
    and STATE; derive the path only from the maintainer's `--scratch-root`
    CLI input and the already-validated attempt id, never from
    agent/policy/evidence-command input; on any create/chmod/verification
    failure, refuse before `PREPARED` with no `/tmp` fallback, no STATE
    allocation, and no provider dispatch.
  - Derive exactly one private per-Attempt directory,
    `<scratch-root>/<validated-attempt-id>`, created in `cmd_prepare()`
    immediately after attempt validation and before repository pinning,
    OPS creation, or any temporary-capable subprocess.
  - Set `TMPDIR`, `TMP`, and `TEMP` all equal to that effective
    per-Attempt path for Harness bootstrap and the canonical preflight
    seam's environment.
  - Add the same three variables as literal, shell-quoted `export`
    lines in the generated `launch.sh`, beside the existing frozen
    `PATH` export, following the exact same literal-value rationale
    `write_launch_sh()`'s own docstring already gives for freezing
    `PATH`.
  - Record the scratch root and the effective per-Attempt path in both
    `preparation-pins.txt` (`write_preparation_pins()`) and
    `harness-prepared.json` (`write_harness_record()`), and project the
    effective path in `cmd_status()`'s human-readable and `--json`
    output.
  - In `cmd_archive()`/`_invoke_canonical_archive()`: recover the
    recorded effective scratch path from preparation facts, cross-check
    agreement between `preparation-pins.txt` and `harness-prepared.json`,
    and revalidate the retained directory (ownership, mode, non-symlink,
    non-overlap) before invoking the canonical Evidence wrapper. Build
    the environment passed to **both** the required dry-run and the real
    `_invoke_canonical_archive()` call as a copy of the archive caller's
    own environment with only `TMPDIR`, `TMP`, and `TEMP` replaced by the
    recorded path -- `subprocess.run()` there currently passes no `env=`
    at all, so this is a new, explicit `env=` argument, not a
    modification of wrapper argv, repository selection, or
    dry-run-before-real sequencing. Refuse archive (no fallback to the
    archive caller's own `/tmp`) on any disagreement between the two
    preparation records or any failed revalidation, before either
    wrapper invocation.
- `tests/unit/test_wfc_op.py`: positive and negative regression coverage
  per "Required tests" below.
- `tests/unit/test_operator.py` and/or
  `tests/unit/test_codex_adapter_conformance.py`: add coverage only if
  proving environment propagation into the existing Coordinator/Codex
  seams requires it -- as read-only-propagation assertions, never as a
  change to `operator.py`, `codex_adapter.py`, or any other
  Runtime-side source file.
- `docs/wfc_operator_manual.adoc`: narrowly correct its existing FD-08
  cross-reference (around docs/wfc_operator_manual.adoc:875-891,
  currently stating FD-08 is "not yet implemented") to reflect this
  implementation and point at the `--scratch-root` flag. No other
  documentation file may be edited.
- The two durable review artifacts this Commission's rendered
  `robust@v1` profile designates for `broad_review` and
  `focused_review`.

Do not change `src/workflow_coordinator/git_facts.py`,
`src/workflow_coordinator/persistence.py`,
`src/workflow_coordinator/operator.py`,
`src/workflow_coordinator/codex_adapter.py`,
`src/workflow_coordinator/claude_adapter.py`,
`src/workflow_coordinator/evidence.py`,
`src/workflow_coordinator/commissioning.py`, `model.py`, `reducer.py`,
`slice4.py`, `vertical.py`, or `proof_harness.py`. The investigation's
own analysis (section 3) establishes that every current
`tempfile.mkstemp()`/`TemporaryDirectory()` call in those files already
honors an inherited `TMPDIR`/`TMP`/`TEMP` with no source change; this
WorkItem's entire mechanism is upstream environment injection in the
Harness, not a change to any temp-file consumer.

### Required semantics

- Lifecycle: create the per-Attempt scratch directory at `PREPARE`;
  retain it unchanged across `LAUNCH` and after completion or failure.
  Implement no recursive auto-delete, no retention policy, and no
  cleanup of historical `/tmp` or `$HOME/wfc-scratch` material -- a
  timed-out provider can have descendants, and deletion adds
  process/lifecycle semantics this slice does not define. Inspection or
  removal of a completed Attempt's scratch directory remains a
  separately authorized maintainer operation, never something this
  WorkItem's code performs automatically.
- Do not implement Runtime `--scratch-root`, a role-executable/
  mixed-adapter interface, provider-independent (e.g. bubblewrap)
  isolation, automatic recovery/retry of an interrupted Run, or any
  general resource-management/quota framework. Each is explicitly out
  of this WorkItem's scope regardless of how natural an extension it
  may seem while implementing.
- Never weaken, remove, or bypass an existing security/admission test
  (including A1 scope-ceiling checks, repository-pin revalidation, or
  launch/execution-bindings digest checks) to reduce temporary storage
  consumption or to make a new test pass more easily.
- Preserve deterministic Harness behavior, evidence provenance, and
  fail-closed semantics throughout: a validation failure refuses before
  `PREPARED` (or before archive, for the archive-time revalidation) with
  no fallback path, exactly as section 6 specifies.

### Required tests

Per investigation section 7, add to `tests/unit/test_wfc_op.py` (and the
optional operator/Codex seams named above only if needed):

- Unit tests for scratch-root resolution: default under a test-isolated
  home seam, explicit absolute root, the validated per-Attempt child
  path, `0700` mode, uid-mismatch refusal, leaf-symlink refusal,
  repository-overlap refusal (Development/Runtime/Evidence/OPS/STATE),
  and pre-existing-attempt-directory refusal. Use test-local directories,
  never live `/tmp` or real mount manipulation.
- Extension proving `cmd_prepare()` records the effective path in both
  `preparation-pins.txt` and `harness-prepared.json`, and that generated
  `launch.sh` exports `TMPDIR`, `TMP`, and `TEMP` (all equal to that
  path) before its `exec`, with frozen `PATH` unchanged.
- A preflight-seam test double proving all three variables (and
  unchanged frozen `PATH`) reach the canonical preflight seam's
  environment.
- A synthetic canonical Evidence archive wrapper recording its own three
  temporary variables, proving both the dry-run and the real
  `_invoke_canonical_archive()` calls receive the recorded path even
  when `wfc-op archive`'s own caller environment supplies different
  values, and proving that preparation-record disagreement or failed
  scratch revalidation refuses before either wrapper invocation.
- An integration-style test preparing then separately launching with a
  different caller environment, proving the generated launch still
  supplies the recorded path (a Python `tempfile` probe landing beneath
  it).
- A scratch-validation-failure test proving refusal with no `/tmp`
  fallback, no STATE allocation, no provider dispatch, and no weakened
  revalidation.
- Do not simulate actual tmpfs exhaustion, alter real mounts, or assert
  Codex-internal or pytest-retention behavior -- those remain outside
  this WorkItem per the investigation's own section 7 closing note.

Run the repository's `task-check` evidence command (`python -m pytest
-q`) as the Coordinator evidence gate.

### Independent review mandate

The independent reviewer must inspect the actual implementation and
tests -- not merely a diff summary -- for: exact conformance to
investigation sections 5 and 6 (including every validation/refusal rule
listed above); that no excluded source file was touched; that no
retention/cleanup/quota/Runtime-`--scratch-root`/mixed-adapter/bubblewrap
mechanism was introduced; that no existing security or admission test
was weakened, skipped, or removed; that the archive-time environment
injection is a true additive `env=` copy (archive caller environment
with only the three variables replaced), not a wholesale environment
replacement that could drop other required variables; and that every
required positive and negative test from "Required tests" above is
present and actually exercises refusal paths, not merely happy-path
coverage. Use normal Robust correction and re-review routing; disposition
any Finding using only the authority this Commission's rendered profile
actually grants. A successful broad review may accept directly; focused
review is needed only if that phase is reached.


## Acceptance

`scripts/wfc_op.py`'s `prepare` subcommand accepts an optional
`--scratch-root ABSOLUTE_DIRECTORY`, defaults to `$HOME/wfc-scratch`,
and creates exactly one private `<scratch-root>/<validated-attempt-id>`
directory in `cmd_prepare()` before any temporary-capable subprocess,
enforcing every validation/refusal rule in investigation section 6.
`TMPDIR`, `TMP`, and `TEMP` are set equal to that path for Harness
bootstrap and the preflight seam, and are exported as literal lines in
generated `launch.sh` beside frozen `PATH`. The effective path is
recorded in `preparation-pins.txt`, `harness-prepared.json`, and
`cmd_status()`'s output. `cmd_archive()`/`_invoke_canonical_archive()`
recover, cross-check, and revalidate that recorded path and inject it
(replacing only `TMPDIR`/`TMP`/`TEMP` in an otherwise-unchanged copy of
the archive caller's environment) into both the dry-run and real
canonical-wrapper invocations, refusing on disagreement or failed
revalidation with no fallback. No file outside this Commission's scope
is changed; in particular `git_facts.py`, `operator.py`,
`codex_adapter.py`, `persistence.py`, `evidence.py`, and
`commissioning.py` are byte-identical to their state before this Attempt.
No recursive deletion, retention policy, Runtime `--scratch-root`,
role-executable/mixed-adapter interface, or provider-independent
isolation mechanism exists anywhere in the diff. All tests listed under
"Required tests" exist and pass, every pre-existing test still passes
unmodified in intent, and `task-check` (`python -m pytest -q`) passes
against the full suite. The independent reviewer's broad (and, if
reached, focused) review artifact records a genuine adversarial review
against the mandate above, with no blocking Finding left open at
completion.


## Withheld authority

No change to `src/workflow_coordinator/git_facts.py`,
`persistence.py`, `operator.py`, `codex_adapter.py`, `claude_adapter.py`,
`evidence.py`, `commissioning.py`, `model.py`, `reducer.py`, `slice4.py`,
`vertical.py`, or `proof_harness.py`. No Runtime `--scratch-root`, no
role-executable or mixed-Claude/Codex-adapter interface (the current
operator explicitly does not support mixed adapters; this WorkItem must
not introduce one), no provider-independent (e.g. bubblewrap) isolation
layer, no automatic recovery or retry of an interrupted Run, and no
general resource-management/quota subsystem -- each is explicitly out of
scope regardless of how the investigation's own non-claims or reserved
decisions (sections 9-10) might seem to invite it. No automatic deletion
of historical `/tmp` or `$HOME/wfc-scratch` material, no retention
policy, and no change to Pytest fixtures, Pytest retention behavior, or
`BUG-WFC-PYTEST-TEMP-RESOURCE-EXHAUSTION`'s own separately-tracked scope.
No weakening, removal, or skip of any existing security or admission
test to reduce temporary storage consumption. No Runtime promotion,
Evidence mutation, nested WFC invocation, or external push. No file
outside this Commission's rendered `authorized_scope` for the active
phase may be edited. The Worker and reviewer must not represent a
provider-requested permission or sandbox mode as a Coordinator-verified
OS boundary, and must not weaken any existing A1 scope-ceiling or
repository-pin revalidation check.


## Default escalation

Stop and request maintainer authority if: the investigation's
recommended design (sections 5-6) cannot be implemented within this
Commission's exact file scope without touching an excluded source file;
a validation rule in section 6 proves ambiguous or internally
inconsistent against the actual current `wfc_op.py` structure in a way
no design-consistent reading resolves; extending test coverage to
`test_operator.py` or `test_codex_adapter_conformance.py` turns out to
require a change to `operator.py` or `codex_adapter.py` itself rather
than a read-only propagation assertion; a reviewer-opened Finding cannot
be disposed within this Commission's own granted Finding authority and
ordinary Robust-v1 correction flow; or completing the slice would
require any decision the investigation itself explicitly reserved to
maintainer authority (section 9) -- including default scratch-root
naming, whether a supplied root must pre-exist, retention/cleanup
policy, whether `wfc` direct or `commission check` need the same
surface, or symlink-race hardening beyond mkdir/lstat.


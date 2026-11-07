# Give a failed trusted A2 evidence observation a bounded, explicitly untrusted diagnostic signal

Implement the correction recorded in
`docs/backlog/BUG-WFC-EVIDENCE-FAILURE-DIAGNOSTICS.adoc`. Treat that
record's "Desired direction (commissioned)" and "Non-scope" sections as
the controlling specification for this task; this text restates their
requirements but that record remains authoritative if any tension is
found.

### Required reading

Read, in full, before implementing:

- `docs/backlog/BUG-WFC-EVIDENCE-FAILURE-DIAGNOSTICS.adoc` (this
  Commission's controlling record).
- `src/workflow_coordinator/evidence.py`: `_stream_facts`,
  `_observation` (around evidence.py:188-233), and
  `ProductionEvidenceRunner.observe` in full (around
  evidence.py:239-326), especially its `"error"` branches (no captured
  bytes available) versus its `"fail"` branch (around
  evidence.py:316-326, real `stdout`/`stderr` bytes already in hand).
- `src/workflow_coordinator/model.py`'s `Observation` dataclass (around
  model.py:757,771-776) -- read-only; its existing fields must not
  change meaning or value.
- `src/workflow_coordinator/persistence.py` (around persistence.py:54,76)
  and `src/workflow_coordinator/serialization.py` (around
  serialization.py:1680,1706), which project and parse `Observation`
  today.
- `docs/wfc_operator_manual.adoc` §16 "Evidence architecture: current
  state vs. future direction" (around :785-803), which already names
  this exact limitation.
- `docs/backlog/BUG-WFC-OPERATOR-DIAGNOSTIC-PROPAGATION.adoc` and
  `docs/backlog/BUG-WFC-EVIDENCE-COMMAND-RESOLUTION-DIAGNOSTICS.adoc`,
  read-only, to confirm (do not duplicate or merge into) their distinct,
  non-overlapping scope.

Do not treat any line number above as exact without re-verifying it
against the actual Development HEAD read during this Run.

### Scope

- `src/workflow_coordinator/evidence.py`: add a bounded (at most 16 KiB
  per stream), explicitly untrusted diagnostic capture of
  `stdout`/`stderr`, populated only in the `"fail"` branch of
  `ProductionEvidenceRunner.observe` (never on `"pass"`, and never
  fabricated for an `"error"` branch that has no real bytes). Record
  whatever truncation metadata is needed beyond the existing
  `stdout_byte_count`/`stderr_byte_count` fields (which already carry
  the untruncated original size).
- `src/workflow_coordinator/model.py`: add whatever new field(s) or
  sibling structure are needed to carry this diagnostic capture.
  `Observation`'s existing `stdout_byte_count`, `stdout_sha256`,
  `stdout_body_omitted`, and their `stderr` counterparts, plus
  `artifact_ref`, must remain byte-for-byte unchanged in meaning and
  value. The new diagnostic capture must not be written into
  `artifact_ref` or any other existing A2-contract field, and must be
  structurally distinguishable by a reader as diagnostic/untrusted
  rather than trusted A2 evidence.
- `src/workflow_coordinator/persistence.py` and
  `src/workflow_coordinator/serialization.py`: project and parse the
  new field(s) consistently with the above, without altering how any
  existing field is projected or parsed.
- `tests/unit/test_a2_evidence_commands.py`,
  `tests/unit/test_persistence.py`, `tests/unit/test_serialization.py`:
  positive and negative regression coverage per "Required tests" below.
- `docs/wfc_operator_manual.adoc` §16 (around :785-803): narrowly
  correct the passage stating that a correction Hop has no way to
  localize a failure beyond re-running the command, to instead describe
  the new bounded diagnostic capture. No other documentation file may
  be edited.
- The two durable review artifacts this Commission's rendered
  `robust@v1` profile designates for `broad_review` and
  `focused_review`.

Do not change any other file, in particular not
`src/workflow_coordinator/operator.py`, `codex_adapter.py`,
`claude_adapter.py`, `proof_harness.py`, `commissioning.py`, `reducer.py`,
`slice4.py`, `vertical.py`, or `git_facts.py`, and not
`scripts/archive_run.py` or any file outside this repository.

### Required semantics

- Diagnostic capture happens only on a failed (`"fail"`-status) trusted
  evidence observation; never on `"pass"`; never fabricated for an
  `"error"` status that has no real output bytes.
- The capture is bounded to at most 16 KiB per stream (tail, i.e. the
  last bytes if the stream exceeds the bound), matching the existing
  `_bounded()` pattern already used elsewhere in this codebase for a
  comparable purpose.
- No existing trusted A2 field (`stdout_byte_count`, `stdout_sha256`,
  `stdout_body_omitted`, their `stderr` counterparts, or `artifact_ref`)
  may change value or meaning for any observation, including the new
  diagnostic capture's own presence or absence.
- The new capture must be clearly identifiable as diagnostic/untrusted,
  never represented as, or merged into, trusted A2 evidence.
- No general logging architecture, framework, or configurable verbosity
  level; no new permission profile; no workflow/Hop/review semantics
  change; no change to `scripts/archive_run.py` or any file in
  `workflow-coordinator-run-evidence`.

### Required tests

- Unit coverage proving: a `"fail"` observation with real stdout/stderr
  produces a bounded diagnostic capture with correct truncation
  behavior at and below the 16 KiB bound; a `"pass"` observation
  produces none; an `"error"` observation (no real bytes available)
  produces none; every existing trusted-field assertion in
  `test_a2_evidence_commands.py` still passes unmodified in intent.
- Persistence/serialization round-trip coverage proving the new
  field(s) project and parse correctly without altering any existing
  field's projection/parsing.
- Do not weaken, skip, or remove any existing test in
  `test_a2_evidence_commands.py`, `test_persistence.py`, or
  `test_serialization.py`.

Run the repository's `task-check` evidence command (`python -m pytest
-q`) as the Coordinator evidence gate.

### Independent review mandate

The independent reviewer must inspect the actual implementation and
tests -- not merely a diff summary -- for: the capture firing only on
`"fail"`; the 16 KiB bound actually enforced per stream; every existing
trusted A2 field provably unchanged in value and meaning (including
`artifact_ref` not being repurposed); no file outside this Commission's
scope touched; no general logging/framework/permission/workflow-
semantics change introduced; and that every required test is present
and actually exercises the `"pass"`/`"fail"`/`"error"` distinction, not
merely happy-path coverage. Use normal Robust correction and re-review
routing; disposition any Finding using only the authority this
Commission's rendered profile actually grants. A successful broad
review may accept directly; focused review is needed only if that phase
is reached.


## Acceptance

A failed (`"fail"`-status) trusted `task-check` (or any other A2)
evidence observation now carries a bounded (at most 16 KiB per stream),
explicitly untrusted diagnostic capture of its stdout/stderr tail,
structurally distinguishable from trusted A2 evidence and never written
into `artifact_ref` or any other existing trusted field. A `"pass"` or
`"error"` observation carries no such capture. Every existing trusted
field (`stdout_byte_count`, `stdout_sha256`, `stdout_body_omitted`,
their `stderr` counterparts, `artifact_ref`) is byte-for-byte unchanged
in value and meaning for every observation. `docs/wfc_operator_manual.adoc`
§16 reflects the new mechanism narrowly, with no other documentation
file touched. No file outside this Commission's scope is changed; in
particular `operator.py`, `codex_adapter.py`, `claude_adapter.py`,
`proof_harness.py`, `commissioning.py`, `reducer.py`, `slice4.py`,
`vertical.py`, and `git_facts.py` are byte-identical to their state
before this Attempt, and no file in `scripts/archive_run.py` or the
`workflow-coordinator-run-evidence` repository is touched. All required
tests exist and pass, every pre-existing test still passes unmodified
in intent, and `task-check` (`python -m pytest -q`) passes against the
full suite. The independent reviewer's broad (and, if reached, focused)
review artifact records a genuine adversarial review against the
mandate above, with no blocking Finding left open at completion.


## Withheld authority

No general logging architecture or framework, no configurable
verbosity/log-level mechanism, no new permission profile, and no
workflow/Hop/review semantics change. No remediation of the distinct
correction-cycling-with-idle-worker observation from
`RUN-b435ab0c7e394eb5912330c38b5eca5b` -- that remains a separate,
not-yet-commissioned concern. No change to `scripts/archive_run.py` or
any other file in the `workflow-coordinator-run-evidence` repository,
which lies outside this repository entirely. No change to any A2
trusted field's value or meaning, and no repurposing of `artifact_ref`
for the new diagnostic capture. No capture on a `"pass"` observation, and
no fabricated capture on an `"error"` observation that has no real
bytes. No Runtime promotion of any kind (already separately decided and
executed by the maintainer, outside this Commission). No file outside
this Commission's rendered `authorized_scope` for the active phase may
be edited. The Worker and reviewer must not cite
`workflow-coordinator-run-evidence`'s `project_memory/run_ledger.adoc`
directly; only the facts already restated in
`docs/backlog/BUG-WFC-EVIDENCE-FAILURE-DIAGNOSTICS.adoc` and this
Commission's own task text are authorized grounding.


## Default escalation

Stop and request maintainer authority if: the 16 KiB bound or the
"fail"-only trigger cannot be implemented without touching an excluded
source file; a reviewer-opened Finding cannot be disposed within this
Commission's own granted Finding authority and ordinary Robust-v1
correction flow; implementing the diagnostic capture appears to require
any change to an existing trusted A2 field's value or meaning; or
satisfying "Required semantics" appears to require a general
logging/configuration mechanism rather than the single bounded,
failure-only capture this Commission authorizes.


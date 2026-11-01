# Correct the unsupported Claude model/effort outcome assumption and close predecessor Findings F001/F002

Apply the one bounded textual correction that predecessor
`WI-WFC-CLAUDE-CODE-ADAPTER-DESIGN-001` left open at its final Hop (H008),
then obtain a fresh independent review that the resulting design
substantively closes both predecessor Findings and still satisfies the
original acceptance mandate.

### Required reading

Read, in full, as grounding fact rather than assumption, before changing
anything:

- `docs/warm_storage/concepts/workflow_coordinator_claude_code_adapter_design_2026-11-01.adoc`
  (the candidate design document, in its current committed state) in
  full, not only the cited lines below.
- `docs/warm_storage/concepts/workflow_coordinator_claude_code_adapter_design_2026-11-01_focused_review.adoc`
  (the predecessor's final, H008, independent focused rereview), in
  particular its section 3.1 (`F002 -- observable provider outcomes`) and
  3.1.3 (`Required correction`), and section 3.2 (`F001 -- no
  regression`).
- `docs/warm_storage/concepts/workflow_coordinator_claude_code_adapter_design_2026-11-01_broad_review.adoc`
  (the predecessor's H002 broad review that opened the Finding later
  resolved as F001), for the original broad-review mandate and disposition
  history.
- `governance/commissions/wfc-claude-code-adapter-design-001/intent.yaml`,
  in particular `task.body`'s "Required design document contents" and
  "Independent review mandate", and `task.acceptance`: these remain the
  governing acceptance mandate for the design document itself. This
  Commission narrows what must still change; it does not relax that
  mandate.
- `docs/warm_storage/runbooks/successor_continuation_runbook_2026-10-10.adoc`
  section 3.1 ("A stopped Run is historical truth") and section 3.4
  (Finding locality / semantic ledger curation), for why this Commission
  treats `F001`/`F002` as predecessor Run-local labels rather than
  identities this Run inherits.

Do not treat any line-number citation in this task body as still exact
without independently re-verifying it against the actual current
committed text: cite only what you have yourself confirmed in the current
design document.

### The one required correction (Worker)

The design document's "Model and effort" row (currently, as last left by
H008, approximately at `design:84`) ends with the unsupported sentence:
"Availability of every model/effort pair is unverified and must surface
as a nonzero transport failure in later smoke." The H008 independent
focused rereview found this to be the one remaining instance, anywhere in
the whole design, of the prohibited pattern it otherwise eliminated: it
predicts a specific process exit status for an unavailable provider
selection, which neither the installed Claude Code CLI help nor the cited
official CLI reference establishes, and which conflicts with the design's
own corrected general classification rule (`design:164-166`: only an
observed nonzero exit or timeout is transport failure; a zero exit
proceeds to wrapper decoding and WFC's existing fail-closed semantic
validation).

Correct that final sentence of the "Model and effort" row so that it
instead states, in your own precise wording consistent with the rest of
the corrected design:

1. an unavailable selected model/effort pair must fail closed, and must
   never trigger `--fallback-model`, automatic replay, or binding
   substitution;
2. the later separately authorized pinned-CLI qualification (section 7)
   records the actual observed process status and wrapper behavior for
   that case rather than requiring or predicting a nonzero exit; and
3. rejection of that case follows the design's own existing general gates
   unchanged: an observed nonzero exit or timeout is a transport failure,
   while a zero exit without a successful, valid structured candidate is
   rejected by wrapper or semantic validation -- no new oracle, inference,
   or fabricated provider-status rule is introduced to decide it.

This is a narrow textual correction to one sentence. Before writing it,
re-read the whole document once more (not only `design:84` and the
classification rule it must agree with) to confirm no other sentence
depends on, repeats, or would be left inconsistent by the old wording;
the H008 review's whole-design regression found none, but you must verify
this yourself rather than trust that finding by citation alone. Do not
add a model router, provider registry, fallback-model behavior, retry,
provider-error oracle, telemetry schema, or any other mechanism. Do not
touch any other section, sentence, or line of the design document for
any other reason: this Commission authorizes exactly this one correction
to the design document, not a general re-edit pass.

### Scope note (known, unrepaired limitation)

You (the Worker) are instructed to correct ONLY the main design document
listed in this Commission's scope, even though the rendered Robust-v1
worker-phase `authorized_scope` is mechanically broader and also contains
both new review-artifact paths below (see
`docs/backlog/FTR-WFC-PHASE-WRITE-SCOPE-ARTIFACT-ISOLATION.adoc`, and the
predecessor's own identical scope note). Reviewer-authored artifacts are
reserved for the independent reviewer. Do not attempt to repair that
separate, already-recorded scope-isolation limitation in this Commission;
it is out of scope here.

### Independent review mandate (Reviewer)

The independent reviewer must, as a fresh review rather than a summary of
H008's conclusion:

- Verify, by reading the corrected design document itself rather than
  trusting this task's description of the correction, that predecessor
  Finding `F002`'s substance (the unsupported nonzero-outcome assertion)
  is actually closed: the corrected sentence must not merely reword the
  assertion while preserving its effect, and must not introduce a new
  unsupported Claude-CLI assumption elsewhere.
  Also verify, by inspection of the corrected design document, that no
  regression has been introduced against predecessor Finding `F001`'s
  substance (the originally resolved blocking defect from the H002 broad
  review -- the ambient-configuration / unsandboxed-startup profile
  issue; see
  `docs/warm_storage/concepts/workflow_coordinator_claude_code_adapter_design_2026-11-01_broad_review.adoc`
  section 3 and the focused review's own section 3.2 "no regression"
  disposition for what must still hold).
- Re-review the whole current design document against the complete
  original independent-review mandate in
  `governance/commissions/wfc-claude-code-adapter-design-001/intent.yaml`
  (unsupported Claude CLI assumptions elsewhere in the document; any
  implied relaxation of structured-result correlation or validation;
  provider-specific logic leaking into the Coordinator; missing
  noninteractive permission/sandbox constraints; missing negative
  transport/timeout tests; incompatibility with Direct/Robust/Simple;
  unnecessary architectural machinery; whether the design actually tests
  the provider-neutral abstraction). Do not limit review to the single
  corrected sentence.
- Dispose any Finding you open using only the authority this Commission's
  own rendered profile actually grants you, under this Run's own
  Run-local Finding numbering. Do not reuse, reference as binding, or
  imply inheritance of the predecessor's `F001`/`F002` labels as if they
  were this Run's own Finding identities; you may cite them only as
  historical predecessor context, exactly as this task's own authority
  source does.
- Accept directly (broad review) if the correction and the whole design
  are sound; a focused review is required only if the broad review
  actually routes to correction and that phase is actually reached. Do
  not manufacture a focused-review phase when broad review can honestly
  accept.


## Acceptance

The design document at
`docs/warm_storage/concepts/workflow_coordinator_claude_code_adapter_design_2026-11-01.adoc`
has its "Model and effort" row's final sentence corrected to require
fail-closed handling of an unavailable selected model/effort pair based
on actually observed process status, wrapper, and candidate validation,
with no fallback-model use, retry, automatic binding substitution, or
fabricated/inferred provider-status rule, consistent with the rest of the
document's own general transport/wrapper classification. No other
sentence, section, or line of that document is changed. A new broad
review artifact exists at this Commission's designated new path and
records a genuine, freshly reasoned independent review disposing the
correction and re-checking the whole document against the original
independent-review mandate; if (and only if) that broad review routes to
correction, a corresponding correction Hop and a new focused review
artifact at this Commission's other designated new path exist and record
a genuine focused rereview. Neither predecessor review artifact is
overwritten, rewritten, or renamed. The final accepted disposition
records, in its own words, that predecessor Findings F001 and F002 are
substantively closed in this design, without asserting that this Run's
own Finding numbering is identical to or continuous with the
predecessor's. No blocking Finding is left open at completion.


## Withheld authority

No production implementation, source-code edit, test, governance change,
kernel change, new schema, Runtime promotion, Evidence mutation, cleanup,
nested WFC invocation, external push, or live provider invocation of any
kind (Codex or Claude). No new programme catalogue entry is created by
this Run; only the design document and the two newly designated review
artifacts below may be written. All code, tests, governance, backlog,
programme definitions, predecessor review artifacts, and other documents
outside this Commission's scope are read-only investigation inputs,
including both predecessor review artifacts, which must not be edited,
overwritten, or renamed. The Worker must touch only the main design
document, and only its single named sentence; the two new review-artifact
paths are reserved for the independent reviewer, notwithstanding the
Robust-v1 worker-phase scope-isolation limitation noted in the task body,
which this Commission does not attempt to repair. No expansion into new
architecture, optional hardening, token accounting, additional providers,
or implementation. No automatic authorization of a subsequent
implementation WorkItem is granted by completing this design closure; the
maintainer separately authorizes preparation and launch of any such
WorkItem.


## Default escalation

Stop and request maintainer authority if: the "Model and effort" row's
unsupported sentence is not found at its expected approximate location,
or the document otherwise no longer matches this task's description of
predecessor H008's remaining defect, such that the described one-sentence
correction cannot be unambiguously identified and applied without a
broader edit; correcting that one sentence is found to require changing
any other sentence, section, or file to remain internally consistent;
a reviewer-opened Finding cannot be disposed within this Commission's own
granted Finding authority and ordinary Robust-v1 correction flow; or the
independent reviewer concludes that predecessor Finding F001 or F002 is
not actually substantively closed by a correction bounded to this
Commission's authorized scope.


# Stops and Evidence

Distils Operator Manual chapter 3, [launch, status and terminal states](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch03_running/02_launch_status.adoc),
chapter 6, [recovery outcomes](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch06_recovery/01_outcomes.adoc),
and chapter 4, [the archive](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch04_evidence/02_archive.adoc),
as released with v0.1.0b1.

## Reading `wfc status`

```shell
wfc status --repository .
```

Strictly read-only; safe from a second shell while a Run is in progress.
Always read the printed `CLASSIFICATION`, never the exit code -- a legitimate
stop such as `awaiting_human` exits non-zero too, exactly like a mechanical
refusal. It also prints each recorded Hop-budget extension and each
`RECOVERY` block for a same-Run recovery of this checkout.

## Every classification, and what it means

What each one *is*; what you may do about it is the next section's table.

* `completed` -- the Run finished.
* `awaiting_human` -- authority or mechanics could not legally continue; not
  a provider or review failure. Inspect the stop reason, whether a human
  question is present, open Findings and the last phase.
* `awaiting_human_budget` -- the Hop budget is exhausted.
* `reconcile_required` -- current reality must be validated before the Run
  continues.
* `refused_not_recoverable` / `refused_budget_extension` -- a continuation
  command re-verified its own predicate against the live Run and checkout and
  it does not hold; the printed reason names the element. It changed nothing
  -- the Run keeps its stop and every charged Hop -- and is a final answer,
  not a defect.
* `stopped` / `interrupted` -- the Run did not reach a clean terminal state.
* `engine_active` -- the engine, or its launcher, still holds the lock.
* `engine_exited_before_state` -- no provider dispatch was recorded.
* `uncertain_launch` -- an unresolved launch; never safe to relaunch.
* `inconsistent` / `launch_failed_no_engine` -- contradictory engine/journal
  facts, or no engine process was ever created.
* `no_lifecycle` / `not_launched` / `lifecycle_active` -- (`wfc status` only)
  nothing recorded yet (not an error), authorized but not launched, or still
  running -- the last means another process holds this checkout's lock.
* `refused_unauthorized` / `refused_drift` / `refused_journal_root` --
  raised *before* authorization is recorded (confirmation refused, a drifted
  Commission, a journal root not disjoint from the repository); this
  checkout's one lifecycle is not consumed.
* `refused_no_lifecycle` / `refused_active_lifecycle` -- nothing was ever
  recorded here, or a lifecycle already is (at most one ever is).
* `refused_prelaunch` / `refused_probe` / `refused_identity_changed` /
  `refused_launch_exhausted` -- raised once a lifecycle already is
  authorized; the printed message names the fact to correct.

## What the mandate already covers, before asking

Delegated task authority persists across a stop: a technical failure does
not by itself end it, and uncertainty about why a Hop or Run stopped is not
itself a reason to ask -- diagnose it, and where the table says the mandate
covers the next step, take it and report what you did.

| Classification(s) | You may, on your own | A human decides |
| --- | --- | --- |
| `completed` | Verify against the declared scope, then integrate. | -- |
| `refused_unauthorized` / `refused_drift` / `refused_journal_root` | Correct the input and `wfc run` again in the same checkout -- the lifecycle was never consumed. | -- |
| `refused_prelaunch` / `refused_probe` / `refused_identity_changed` / `refused_launch_exhausted` | Diagnose the reported fact -- including preparing or correcting the declared evidence command's own environment -- and either `wfc resume` the same checkout or prepare a fresh one and retry, per the printed message; repeating a probe that just failed, once corrected, is ordinary preparation. | Widening scope, authority, program or Hop budget to make the probe pass. |
| `refused_no_lifecycle` / `refused_active_lifecycle` | Prepare a fresh checkout and launch there. | -- |
| `awaiting_human_budget` | Prepare a successor Commission at the same scope, program and an unwidened Hop budget, and report the stop. | Raising the Hop budget -- including the in-place `wfc extend-budget`, which you run only on the maintainer's explicit instruction in the current session and then report. |
| `reconcile_required` (either recoverable class, below) / `awaiting_human` with a `mechanical_preemption:` stop reason | `wfc resume --preview` to see the decision, then `wfc resume` the same checkout; read the new `CLASSIFICATION`. | -- |
| `refused_not_recoverable` / `refused_budget_extension` | Report the printed reason. Where the Run cannot be continued, treat it like the stop it came from. | Whether to salvage, integrate by maintainer decision or discard; any widened authority. The identity invariant is not bypassed. |
| `reconcile_required` / `stopped` / `interrupted` | Investigate the stop; where the preserved candidate is unambiguous and in-scope, prepare a successor Commission carrying the same already-decided scope, program and Hop budget forward. | Judging whether the candidate is worth salvaging, needs only forensic preservation, or should be discarded; any widened authority. |
| `uncertain_launch` | Report the fact. | Any relaunch -- never treated as safe. |
| `awaiting_human` | Report `stop_reason`, `human_question_present`, open Findings and the last recorded phase. | Resolving the stop itself; a Finding is only ever opened or resolved by the independent reviewer. |

## Continuing a stopped Run: the three real channels

**`wfc resume`** covers exactly two recoverable classes, decided from the
Coordinator's own records and never an agent's claim: a mechanical,
post-acceptance durable-artifact anomaly (stop reason beginning with
`mechanical_preemption:`), and a proven-quiescent post-dispatch interruption
(reconcile reason `interruption`: no commit, no HEAD movement, no recorded
anomaly, no staged evidence; the interrupted Hop stays charged and is never
replayed). An interruption that left an uncommitted candidate is refused by
name -- that candidate is salvage material and a maintainer judgement, never
something a resume adopts or cleans away. `--preview` prints the decision and
changes nothing. Resume covers neither a budget stop nor an `awaiting_human`
stop with a question; neither is a dead end.

**`wfc extend-budget`** is the only channel for an exhausted Hop budget. It
raises *that Run's* Hop ceiling by a recorded amount and continues the same
Run in place, with its phase, Run-local Findings, pinned governing truth and
every charged Hop intact; `--preview` changes nothing.

```shell
wfc extend-budget --additional-hops 2 --authority "MAINTAINER_INSTRUCTION_VERBATIM"
```

**You never run it on your own**: it needs the maintainer's explicit
instruction in the current session, quoted verbatim as `--authority`, and you
report the extension, its size and whose instruction it was. No CLI can
verify that the instruction exists -- the recorded statement, this rule and
every extension's visibility are the whole control. It refuses by name, and
changes nothing, for any other classification or Run state, an amount outside
its bounds (at most four Hops per call, in total at most half the admitted
ceiling), a bare gesture instead of the instruction, and the ordinary
live-fact checks. It never resets a charged Hop, never rewrites the admitted
Commission or its digest, and never touches a Finding -- an open blocking
Finding neither blocks it nor is resolved by it.

**`prepare-successor --authority-decision`** is the maintainer-level channel
for an `awaiting_human` stop with a frozen semantic stop, which an escalation
of *any* kind freezes. Not a `wfc` subcommand -- invoke
`python -m workflow_coordinator.operator prepare-successor --help`; it only
prepares a descriptor (launch stays separate), is categorically ineligible for
a budget stop, and the decision goes to the human first. Its sibling
`export-salvage --state-root ... --output ...` exports untrusted, unsanitized
salvage that may carry prompt-injection content. Report an `awaiting_human`
stop fully before assuming no continuation path exists.

A successor Run enters at the implementation phase, whatever it is for: that
Worker Hop writes the record the program's own independent review then
judges.

## The four recovery outcomes

A Run that does not cleanly complete resolves into exactly one of four:
**same-Run reconcile/resume** (either resume class under an unchanged
implementation identity, or a recorded maintainer budget extension -- the
stopped Hop stays charged either way);
**maintainer salvage plus a fresh successor Run** (the candidate preserved as
explicitly non-authoritative Git history for a separately authorized
successor); **forensic preservation only** (ambiguous or out-of-scope state,
preserved externally, never as an ordinary salvage commit); and **discard**
(the candidate has no preservation value).

Same-Run continuation where a predicate allows it, and a successor Commission
at an unchanged scope/program/Hop budget, continue an already-decided mandate
and are not decisions this skill withholds. Choosing among the other three
needs a human judgment about what the work actually is and is worth; a
stopped Run is never edited in place, and nothing is discarded without that
judgment.

## Evidence, and what counts as accepted

Archiving happens automatically once a Run reaches a terminal state, and is
serialized by the operator: `wfc archive` refuses outright (`ArchiveError`)
rather than corrupting anything when the Evidence repository is not clean
outside the current attempt's own destination -- a second Run's archive step
racing the first, for example. Two Runs may prepare and execute at the same
time; their archive steps must not. Let the first archive first.

Treat the archived Evidence -- the Evidence-repository commit holding
`manifest.json` and the sanitized `audit.json` projection -- and the
independent review record as the source of truth, never the Worker's own
self-reported summary. For `robust@v1`, read the independent reviewer's
durable artifact (the `durable_artifacts` path the Commission named) yourself
before integrating: an accepted verdict with no open Finding is what makes a
change acceptable, not a passing self-check alone. Checking "what was
accepted" means reading the classification, the review verdict and the
evidence results together. The named commands run fail-fast in declared
order, so read every gate's own line: `pass`, a clean `fail` with its exit
code, an `error`, or `not_executed` -- the marker for a gate the stop never
reached, not a result. Two alternative forms -- run one of them, never both:

```shell
wfc report EVIDENCE_REPO/archive/unclassified/ATTEMPT_ID/audit.json
wfc report --repository .
```

`SOURCE` names the Evidence/Attempt location directly (an archived Attempt
directory or `audit.json` path), exactly as `wfc run` prints it once a Run
reaches a terminal state; `--repository` resolves that location from the
consumer repository's own `.wfc/config.yaml` and reports the one archived
Attempt found there, refusing by name for none or more than one. Pass exactly
one of the two, never both.

It renders already-archived, already-classified facts, plus whatever
provider-reported token usage was captured -- its `limits` line names
`usage_not_reported`/`usage_partially_reported` when capture was absent or
incomplete, it is never a price, and an unreported figure is never estimated.
A recorded Hop-budget extension gets its own line, and the `next` line names
the continuation channel that actually exists for the reported stop.

## What to bring to the human

The printed `CLASSIFICATION` and `MESSAGE`; for `awaiting_human`, the
`stop_reason` and whether `human_question_present`; any open Finding and
which phase holds authority over it; the Evidence location once archived;
and why, for a stop whose disposition needs judgment -- never widen scope or
a Hop budget, promote a Runtime, publish or discard anything without an
explicit answer. Technical continuation the table already covers is not
itself one of these.

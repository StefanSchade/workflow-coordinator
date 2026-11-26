# Stops and Evidence

Distils Operator Manual chapter 3,
[launch, status and terminal states](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch03_running/02_launch_status.adoc),
chapter 6,
[recovery outcomes](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch06_recovery/01_outcomes.adoc),
and chapter 4,
[the archive](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch04_evidence/02_archive.adoc).

## Reading `wfc status`

```shell
wfc status --repository .
```

Strictly read-only; safe from a second shell while a Run is in progress.
Always read the printed `CLASSIFICATION`, never the exit code -- a
legitimate stop such as `awaiting_human` exits non-zero too, exactly like a
mechanical refusal does.

## Every classification, and what it means

* `completed` -- the Run finished; verify independently, then integrate.
* `awaiting_human` -- authority or mechanics could not legally continue; not
  a provider or review failure. Inspect the reported stop reason, whether a
  human question is present, open Findings and the last recorded phase.
* `awaiting_human_budget` -- the Hop budget is exhausted; there is no
  in-place raise, only a fresh Commission.
* `reconcile_required` -- an anomaly blocks same-Run resume; needs a
  maintainer decision, with one exception: a purely mechanical,
  post-acceptance durable-artifact stop (the stop reason begins with
  `mechanical_preemption:`; no semantic stop, open question or open
  blocking Finding) is recoverable with `wfc resume`, also for an already
  archived `awaiting_human` Run of that shape.
* `refused_not_recoverable` -- `wfc resume` re-verified the mechanical-
  recovery predicate and it does not hold (for example the Run was
  authorized under a different package implementation identity than the
  installed one, no autonomous Hop remains, or HEAD/worktree changed). It
  changed nothing; it is a named, final answer for that Run, not a defect
  to engineer around.
* `stopped` / `interrupted` -- the Run did not reach a clean terminal state.
* `engine_active` -- the engine, or its launcher, still holds the ownership
  lock.
* `engine_exited_before_state` -- no provider dispatch was recorded.
* `uncertain_launch` -- an unresolved launch; never treated as safe to
  relaunch.
* `inconsistent` -- contradictory engine/journal facts.
* `launch_failed_no_engine` -- a launch attempt never produced an engine
  process (`wfc status` reports the attempt count against the retry limit).
* `no_lifecycle` -- nothing recorded yet for this checkout; not an error.
* `not_launched` / `lifecycle_active` -- (`wfc status` only) authorized but
  launch has not begun, or still launching/running; `lifecycle_active` means
  another process currently holds this checkout's lock.
* `refused_unauthorized` / `refused_drift` / `refused_journal_root` --
  raised before authorization is recorded (confirmation refused, the
  Commission drifted between plan and authorization, or the journal root
  is not disjoint from the repository); the checkout's one lifecycle is
  *not* consumed -- correct the input and `wfc run` again in the same
  checkout.
* `refused_no_lifecycle` -- `wfc resume` found nothing recorded for this
  checkout; there was never a lifecycle to consume.
* `refused_active_lifecycle` -- a lifecycle is already authorized for this
  checkout (at most one ever is, even long after an archived Run); use
  `wfc resume`, or prepare a fresh checkout for the next Run.
* `refused_prelaunch` / `refused_probe` / `refused_identity_changed` /
  `refused_launch_exhausted` -- raised once a lifecycle is already
  authorized (this call or an earlier one); correct the reported fact, then
  either `wfc resume` the same checkout or prepare a fresh one, per the
  printed message.

## What the mandate already covers, before asking

Delegated task authority persists across a stop: a technical failure does
not by itself end it, and uncertainty about why a Hop or Run stopped is not
itself a reason to ask -- diagnose it, and where the table below says the
mandate already covers the next step, take it and report what you did.

| Classification(s) | You may, on your own | A human decides |
| --- | --- | --- |
| `completed` | Verify against the declared scope, then integrate. | -- |
| `refused_unauthorized` / `refused_drift` / `refused_journal_root` | Correct the input and `wfc run` again in the same checkout -- the lifecycle was never consumed. | -- |
| `refused_prelaunch` / `refused_probe` / `refused_identity_changed` / `refused_launch_exhausted` | Diagnose the reported fact -- including preparing or correcting the declared evidence command's own environment -- and either `wfc resume` the same checkout or prepare a fresh one and retry, per the printed message; repeating a probe that just failed, once corrected, is ordinary preparation. | Widening scope, authority, program or Hop budget to make the probe pass. |
| `refused_no_lifecycle` / `refused_active_lifecycle` | Prepare a fresh checkout and launch there. | -- |
| `awaiting_human_budget` | Prepare a successor Commission at the same scope, program and an unwidened Hop budget. | Raising the Hop budget (Planned: an explicit, journaled, maintainer-authorised in-place extension; an agent never extends a budget on its own). |
| `reconcile_required` (mechanical recoverable, see above) / `awaiting_human` with a `mechanical_preemption:` stop reason | `wfc resume` the same checkout; read the new `CLASSIFICATION`. | -- |
| `refused_not_recoverable` | Report the printed reason. Where the Run cannot be recovered (identity changed since authorization, no Hop left), treat it like the stop it came from. | Whether to salvage, integrate by maintainer decision or discard; any widened authority. The identity invariant is not bypassed. |
| `reconcile_required` / `stopped` / `interrupted` | Investigate the stop; where the preserved candidate is unambiguous and in-scope, prepare a successor Commission carrying the same already-decided scope, program and Hop budget forward. | Judging whether the candidate is worth salvaging, needs only forensic preservation, or should be discarded; any widened authority. |
| `uncertain_launch` | Report the fact. | Any relaunch -- never treated as safe. |
| `awaiting_human` | Report `stop_reason`, `human_question_present`, open Findings and the last recorded phase. | Resolving the stop itself; a Finding is only ever opened or resolved by the independent reviewer. |

## `wfc resume`'s actual scope, and the `prepare-successor` channel

`wfc resume` covers exactly one recoverable class today: a purely mechanical,
post-acceptance durable-artifact anomaly (`reducer.py:487-500`) -- no open
semantic stop, no open human question, no open blocking Finding. It
correctly refuses (`refused_not_recoverable`) an `awaiting_human` stop that
carries a genuine semantic or operational question; that refusal is not a
dead end. An escalation of *any* kind -- `operational` included -- freezes a
semantic stop on the Run (`reducer.py:1753-1759`), and a frozen semantic stop
on an `awaiting_human` Run is exactly the precondition the maintainer-level
`prepare-successor --authority-decision` operation requires
(`operator.py:324-342`) to validate a successor and emit linkage material; it
is not a `wfc` subcommand -- invoke it as
`python -m workflow_coordinator.operator prepare-successor --help` to see its
full argument set, and bring the decision to the human first: this operation
still only prepares a successor descriptor, launch remains a separate,
explicit step. A sibling operation, `export-salvage --state-root ... --output
...`, exports optional untrusted salvage for inspection first; its bytes may
contain prompt-injection content and are not sanitized. Report an
`awaiting_human` stop fully (as the table above says) before assuming no
productized continuation exists.

## The Evidence-repository concurrency contract

Archiving is serialized by the operator: `wfc archive` refuses outright
(`ArchiveError`) rather than corrupting anything when the Evidence
repository is not clean outside the current attempt's own destination
(`frontdoor/archive.py:805-820`) -- for example a second Run's archive step
racing the first. Two Runs may prepare and execute in parallel, but their
archive steps must not; treat a second Run's authorization as safe to start
only once the first has archived, or serialize the archive calls yourself.

## What counts as accepted

Treat the archived Evidence -- the Evidence-repository commit holding
`manifest.json` and the sanitized `audit.json` projection -- and the
independent review record as the source of truth, never the Worker's own
self-reported summary. Checking "what was accepted" means reading the
classification, the review verdict and the task-check result together.

## Reading `wfc report`

```shell
wfc report <evidence-repo>/archive/unclassified/<attempt-id>/audit.json
```

Renders the already-archived, already-classified facts; `wfc run` itself
prints the exact destination path to use once a Run reaches a terminal
state, and archiving happens automatically. The report also shows whatever
provider-reported token usage was captured, and its `limits` line names
`usage_not_reported`/`usage_partially_reported` when capture was absent or
incomplete; it is never a price, and a figure the provider did not report is
never estimated.

## The review record

For `robust@v1`, read the independent reviewer's durable artifact (the
`durable_artifacts` path the Commission named) yourself before integrating;
an accepted verdict with no open Finding is what makes a change acceptable,
not a passing self-check alone.

## The four recovery outcomes

A Run or Hop that does not cleanly complete resolves into exactly one of
four outcomes. Same-Run resume (where the accepted predicates allow it) and
a successor Commission at an unchanged scope/program/Hop budget are not
decisions this skill withholds -- they continue an already-decided mandate,
per the table above. The four outcomes:

1. **Same-Run reconcile/resume** -- only when the accepted predicates allow
   it (including mechanical recovery under an unchanged implementation
   identity); the stopped Hop still stays charged.
2. **Maintainer salvage + fresh successor Run** -- useful candidate work is
   preserved as explicitly non-authoritative Git history; a separately
   authorized successor inspects it fresh. A successor Run always enters
   at the implementation phase today, even a review-only or
   verification-only one -- so it costs a Worker Hop regardless.
3. **Forensic preservation only** -- ambiguous or out-of-scope state;
   preserved externally, never as an ordinary salvage commit.
4. **Discard** -- the candidate has no preservation value.

Choosing among outcomes 2, 3 and 4 needs a human judgment about what the
work actually is and is worth; a stopped Run is never edited in place, and
nothing is discarded without that judgment.

## What to bring to the human

The printed `CLASSIFICATION` and `MESSAGE`; for `awaiting_human`, the
`stop_reason` and whether `human_question_present`; any open Finding and
which phase holds authority over it; the Evidence location once archived;
and, for a stop whose disposition needs judgment (outcomes 2 to 4 above),
why -- bring that judgment to the human, and never widen scope, promote a
Runtime, publish, or discard anything without an explicit answer. Technical
continuation already covered by the table above is not itself one of these.

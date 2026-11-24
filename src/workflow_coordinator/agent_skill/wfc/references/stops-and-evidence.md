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
  maintainer decision.
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
state, and archiving happens automatically.

## The review record

For `robust@v1`, read the independent reviewer's durable artifact (the
`durable_artifacts` path the Commission named) yourself before integrating;
an accepted verdict with no open Finding is what makes a change acceptable,
not a passing self-check alone.

## The four recovery outcomes

A Run or Hop that does not cleanly complete resolves into exactly one
maintainer-chosen outcome; none is automatic and this skill never chooses
one on its own:

1. **Same-Run reconcile/resume** -- only when the accepted predicates allow
   it; the stopped Hop still stays charged.
2. **Maintainer salvage + fresh successor Run** -- useful candidate work is
   preserved as explicitly non-authoritative Git history; a separately
   authorized successor inspects it fresh.
3. **Forensic preservation only** -- ambiguous or out-of-scope state;
   preserved externally, never as an ordinary salvage commit.
4. **Discard** -- the candidate has no preservation value.

A stopped Run is never edited in place, and nothing is discarded without the
human.

## What to bring to the human

The printed `CLASSIFICATION` and `MESSAGE`; for `awaiting_human`, the
`stop_reason` and whether `human_question_present`; any open Finding and
which phase holds authority over it; the Evidence location once archived;
and, for anything that is not `completed`, which of the four recovery
outcomes applies and why -- never resolve the stop, widen scope, or discard
anything without an explicit answer.

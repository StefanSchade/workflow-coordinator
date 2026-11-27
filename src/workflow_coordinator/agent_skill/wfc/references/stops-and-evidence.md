# Stops and Evidence

Distils Operator Manual chapter 3,
[launch, status and terminal states](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch03_running/02_launch_status.adoc),
chapter 6, [recovery outcomes](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch06_recovery/01_outcomes.adoc),
and chapter 4, [the archive](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch04_evidence/02_archive.adoc).

## Reading `wfc status`

```shell
wfc status --repository .
```

Strictly read-only; safe from a second shell while a Run is in progress.
Always read the printed `CLASSIFICATION`, never the exit code -- a
legitimate stop such as `awaiting_human` exits non-zero too, exactly like a
mechanical refusal. It also prints any recorded Hop-budget extension.

## Every classification, and what it means

* `completed` -- the Run finished; verify independently, then integrate.
* `awaiting_human` -- authority or mechanics could not legally continue; not
  a provider or review failure. Inspect the stop reason, whether a human
  question is present, open Findings and the last phase.
* `awaiting_human_budget` -- the Hop budget is exhausted. Only an explicit
  maintainer instruction continues it in place (see "Continuing a stopped
  Run"); otherwise the answer is a successor Commission.
* `reconcile_required` -- an anomaly blocks same-Run resume; needs a
  maintainer decision, with one exception: a purely mechanical,
  post-acceptance durable-artifact stop (stop reason beginning with
  `mechanical_preemption:`; no semantic stop, open question or open blocking
  Finding) is recoverable with `wfc resume`, also for an already archived
  such `awaiting_human` Run.
* `refused_not_recoverable` / `refused_budget_extension` -- `wfc resume` or
  `wfc extend-budget` re-verified its own predicate and it does not hold (a
  changed implementation identity, no Hop remaining, a changed HEAD or
  worktree, an inadmissible Run state). It changed nothing -- the Run keeps
  its stop and every charged Hop -- and is a final answer, not a defect.
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
  checkout's one lifecycle is not consumed -- correct the input and
  `wfc run` again here.
* `refused_no_lifecycle` / `refused_active_lifecycle` -- nothing was ever
  recorded here, or a lifecycle already is (at most one ever is);
  `wfc resume` it, or prepare a fresh checkout for the next Run.
* `refused_prelaunch` / `refused_probe` / `refused_identity_changed` /
  `refused_launch_exhausted` -- raised once a lifecycle already is
  authorized; correct the reported fact, then `wfc resume` here or prepare a
  fresh checkout, per the printed message.

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
| `reconcile_required` (mechanical recoverable, see above) / `awaiting_human` with a `mechanical_preemption:` stop reason | `wfc resume` the same checkout; read the new `CLASSIFICATION`. | -- |
| `refused_not_recoverable` / `refused_budget_extension` | Report the printed reason. Where the Run cannot be continued, treat it like the stop it came from. | Whether to salvage, integrate by maintainer decision or discard; any widened authority. The identity invariant is not bypassed. |
| `reconcile_required` / `stopped` / `interrupted` | Investigate the stop; where the preserved candidate is unambiguous and in-scope, prepare a successor Commission carrying the same already-decided scope, program and Hop budget forward. | Judging whether the candidate is worth salvaging, needs only forensic preservation, or should be discarded; any widened authority. |
| `uncertain_launch` | Report the fact. | Any relaunch -- never treated as safe. |
| `awaiting_human` | Report `stop_reason`, `human_question_present`, open Findings and the last recorded phase. | Resolving the stop itself; a Finding is only ever opened or resolved by the independent reviewer. |

## Continuing a stopped Run: the three real channels

**`wfc resume`** covers exactly one recoverable class: a purely mechanical,
post-acceptance durable-artifact anomaly (`reducer.py:487-500`) -- no open
semantic stop, open question or open blocking Finding. It correctly refuses
(`refused_not_recoverable`) an `awaiting_human` stop carrying a genuine
question, and does not cover a budget stop at all; neither is a dead end.

**`wfc extend-budget`** is the only channel for an `awaiting_human_budget`
stop. It raises *that Run's* Hop ceiling by a recorded amount and continues
the same Run in place, with its phase, Run-local Findings, pinned governing
truth and every charged Hop intact; `--preview` changes nothing.

```shell
wfc extend-budget --additional-hops 2 --authority "<the maintainer's instruction>"
```

**You never run it on your own**: it needs the maintainer's explicit
instruction in the current session, quoted verbatim as `--authority`, and
you report the extension, its size and whose instruction it was. No CLI can
verify that the instruction exists -- the recorded statement, this rule and
every extension's visibility are the whole control. It refuses by name, and
changes nothing, for any other classification or Run state, an amount
outside its bounds (at most four Hops per call, in total at most half the
admitted ceiling), a bare gesture instead of the instruction, and the
ordinary live-fact checks. It never resets a charged Hop, never rewrites the
admitted Commission or its digest, and never touches a Finding -- an open
blocking Finding does *not* block it.

**`prepare-successor --authority-decision`** is the maintainer-level channel
for an `awaiting_human` stop with a frozen semantic stop, which an
escalation of *any* kind freezes (`reducer.py:1753-1759`,
`operator.py:324-342`). Not a `wfc` subcommand -- invoke
`python -m workflow_coordinator.operator prepare-successor --help`; it only
prepares a descriptor (launch stays separate), is categorically ineligible
for a budget stop, and the decision goes to the human first. Its sibling
`export-salvage --state-root ... --output ...` exports untrusted,
unsanitized salvage that may carry prompt-injection content. Report an
`awaiting_human` stop fully before assuming no continuation path exists.

## The Evidence-repository concurrency contract

Archiving is serialized by the operator: `wfc archive` refuses outright
(`ArchiveError`) rather than corrupting anything when the Evidence
repository is not clean outside the current attempt's own destination
(`frontdoor/archive.py:805-820`) -- a second Run's archive step racing the
first, for example. Two Runs may prepare and execute in parallel, their
archive steps must not; serialize them, or authorize a second Run only once
the first has archived.

## What counts as accepted

Treat the archived Evidence -- the Evidence-repository commit holding
`manifest.json` and the sanitized `audit.json` projection -- and the
independent review record as the source of truth, never the Worker's own
self-reported summary. Checking "what was accepted" means reading the
classification, the review verdict and the evidence results together. A
Commission's named commands run fail-fast in declared order, so read every
gate's own line: `pass`, a clean `fail` with its exit code, an `error`, or
`not_executed` -- the report's own marker for a gate the stop never reached,
never a result.

## Reading `wfc report`

```shell
wfc report <evidence-repo>/archive/unclassified/<attempt-id>/audit.json
wfc report --repository .
```

The first form's `SOURCE` names the Evidence/Attempt location directly (an
archived Attempt directory or `audit.json` path), exactly as `wfc run`
prints it once a Run reaches a terminal state. The second form resolves that
location from the consumer repository's own `.wfc/config.yaml` and reports
the one archived Attempt found there, refusing by name if none or more than
one exists. Pass exactly one of `SOURCE`/`--repository`, never both.

It renders the already-archived, already-classified facts; archiving happens
automatically once a Run reaches a terminal state. It also shows whatever
provider-reported token usage was captured, and its `limits` line names
`usage_not_reported`/`usage_partially_reported` when capture was absent or
incomplete; it is never a price, and an unreported figure is never
estimated. A recorded Hop-budget extension gets its own line.

## The review record

For `robust@v1`, read the independent reviewer's durable artifact (the
`durable_artifacts` path the Commission named) yourself before integrating;
an accepted verdict with no open Finding is what makes a change acceptable,
not a passing self-check alone.

## The four recovery outcomes

A Run that does not cleanly complete resolves into exactly one of four.
Same-Run continuation (where the predicates allow it) and a successor
Commission at an unchanged scope/program/Hop budget continue an
already-decided mandate; they are not decisions this skill withholds.

1. **Same-Run reconcile/resume** -- mechanical recovery under an unchanged
   implementation identity, or a recorded maintainer budget extension; the
   stopped Hop stays charged either way.
2. **Maintainer salvage + fresh successor Run** -- the candidate preserved
   as explicitly non-authoritative Git history for a separately authorized
   successor, which always enters at the implementation phase today.
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
and why, for a stop whose disposition needs judgment (outcomes 2 to 4) --
never widen scope or a Hop budget, promote a Runtime, publish or discard
anything without an explicit answer. Technical continuation the table
already covers is not itself one of these.

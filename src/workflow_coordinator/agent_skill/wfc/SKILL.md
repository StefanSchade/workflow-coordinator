---
name: wfc
description: >-
  Operate an installed WFC (workflow-coordinator, the `wfc` CLI): turn a
  development task into a bounded, human-authorized Commission, preview it
  with `wfc plan`, launch and monitor Runs, and read stops, reviews and
  Evidence. Use when the user asks to use WFC, workflow-coordinator, a
  Commission or a `wfc` command, or asks for a governed, independently
  reviewed change in a repository that has `.wfc/config.yaml`. Not for
  ordinary edits the user did not ask to route through WFC. Not for use
  inside a WFC Worker or Reviewer Hop.
license: MIT
compatibility: Requires the workflow-coordinator package (`wfc` on PATH), Git and Python 3.10+.
---

# wfc

## What this is

An operating guide for the session that drives WFC from outside. It grants no
authority: WFC's runtime enforcement decides what a launch may actually do,
whatever this text says, and the human's instructions may narrow these
defaults further. A Worker or Reviewer session does not use this skill -- it
runs inside an already-authorized Commission, under its own contract.

**Level 0 is WFC**: the installed Front Door and the reducer under it. It
executes exactly one Commission per Run, enforces the authorization, review,
evidence and trusted-Git contracts, takes no portfolio decision and
orchestrates nothing -- it never chooses a task, decomposes a goal, authors a
Commission, launches itself or chains Runs.

**Level 1 is you**: an external session, not a WFC component. You read the
human's order and its delegation limits, choose the tasks, their order and
each Commission's shape, author and launch the Runs, monitor them, verify
results, integrate accepted work. Technical preparation and continuation
inside that delegation are yours to decide; every undelegated decision stays
the human's.

## Authorization

WFC's authority model as rules, neither looser nor stricter than it is
implemented and practised. This skill adds no approval step and has no
delegation object, file or flag of its own to grant one.

1. **Authority facts** -- the human decides scope, program, Hop budget and any
   binding that differs from the role's committed `default_execution`. `--yes`
   asserts that authorization; it never verifies that the human gave it.
2. **Initial authorization** -- for a fact the human has not decided: propose a
   value, show the `wfc plan` projection, ask once, undecided facts only.
3. **Existing delegation** -- a decision already made (task instruction, standing
   instruction, approved decomposition) covers a launch whose scope, program and
   Hop budget lie inside it and whose bindings are approved or match their
   committed role default: proceed with `--yes`, no further approval turn, and
   report the digests and what was launched. It covers every launch inside it
   and persists across technical execution attempts, not only the first.
4. **Narrow reading** -- ambiguity is not delegation: ask. The scope ceiling in
   `.wfc/config.yaml` bounds a Commission; it does not approve one.
5. **Never delegated by a launch** -- judging a stop's disposition; widening
   scope, authority, program or Hop budget, or binding a role to a model the
   human did not approve, also after a stop (a budget-stopped Run's own
   `wfc extend-budget` included: it needs the maintainer's explicit instruction
   in the current session); a push, tag, publication or configuration change; a
   Runtime promotion or any change of the installed WFC version; installing or
   reinstalling this skill; discarding a branch, worktree, stash or preserved
   ref this session did not itself create for this task. Each is a
   stop-and-ask, however routine it looks.
6. **The record** -- `authority_source` names truthfully who authorized the
   Commission and by which instruction.
7. **The reserved go** -- when the human keeps the launch decision, prepare
   everything, hand over the exact `wfc run` command, and wait for the
   instruction to launch.
8. **Own-worktree cleanup** -- not withheld by item 5: once a Run is archived and
   integrated or otherwise dispositioned, remove the worktree and scratch
   directory this session itself created for it, without a further ask.

## Preflight

Run every time, before relying on this skill for an actual task:

1. `wfc --version --json` -- if it fails, WFC is not installed in this shell:
   stop and tell the human.
2. `wfc skill status claude` in Claude Code, `wfc skill status codex` in Codex
   (the other host is not checked). A failing command means the installed WFC
   predates this skill: stop and tell the human.
3. The installation this `SKILL.md` was loaded from must report `current`. In
   any other state read `wfc skill show`, follow it for this session, and tell
   the human to re-run `wfc skill install <host>`; `references/setup.md` has
   the rule for an ambiguous installation.

## When WFC fits, and when it does not

Fits: a bounded, scoped development task in a repository that already has
`.wfc/config.yaml`, where the user wants a governed, independently reviewed
(or self-checked) change.

One Run executes one Commission. That is Level 0's contract, not a limit on
you: several Commissions **in sequence**, each its own Run in its own fresh
checkout, under a delegation that already covers them, are ordinary Level-1
operation -- launch, monitor to a terminal recorded state, verify, integrate,
then the next, reporting each. Where a later package depends on an earlier
one, integrate the earlier Run before the next one's scope assumes it landed.
Two Runs at the same time need a delegation that covers running them in
parallel, and even then: never two lifecycles in one checkout, and never two
archive steps at once (`references/stops-and-evidence.md`).

Does not fit: ordinary edits the user did not ask to route through WFC;
deciding what the larger goal is or how to decompose it -- that stays the
human's and your own job, not WFC's; anything that needs a push, a tag, a
publication or a Runtime promotion -- WFC never does any of those itself.

## The loop

Open a reference only when you reach the step that needs it.

**Consumer operator notes.** A repository may commit its own Level-1
operating notes at `docs/operator-notes.md`. Check once per task, in the
**committed** starting state the Run begins from -- never a working tree,
never a Run's candidate:

```shell
git show <base-ref>:docs/operator-notes.md
```

No such file means nothing is missing; never create one unasked. If it
exists, read it before authoring: advice only, never authority, and never a
reason to skip a rule here (`references/setup.md` holds the convention).

Delegated task authority persists across execution attempts: a technical
failure or a stopped Run does not end it, and uncertainty about *why* an
attempt failed is not, by itself, a reason to ask -- try a plausible,
verifiable, bounded correction before the cause is proven. A new human
decision is needed only for what Authorization's item 5 withholds, never
merely because an earlier attempt failed.

1. Confirm the repository is ready (clean tree, `.wfc/config.yaml`, a
   committed guidance file, every committed command of the evidence sequence)
   and prepare a fresh checkout -- `references/setup.md`.
2. Draft the Commission: task document, scope, program, personas, and the
   ordered evidence sequence, this repository's own deterministic gates,
   cheapest first -- `references/commission.md`.
3. Preview with `wfc plan` (read-only: writes nothing, launches nothing).
4. Run the authorization check above; ask only for what is not yet decided.
5. Launch in that fresh checkout, task document and Consumer Request outside
   the repository: `wfc run --request ... --yes`.
6. Monitor with `wfc status`. Read `CLASSIFICATION`, never the exit code --
   `awaiting_human` and other legitimate stops exit non-zero too.
7. On `completed`, render with `wfc report`, verify independently against the
   declared scope before treating it as accepted, integrate, and remove this
   Run's own worktree and scratch directory (Authorization item 8) without a
   further ask. On anything else -- every classification, and what the
   mandate already covers -- `references/stops-and-evidence.md`: act on your
   own wherever its table says so, and bring the human only a stop that
   genuinely asks for a new authorization decision.

## Never

* Edit an in-scope file directly once the task is routed through WFC.
* Commit inside a Run's checkout.
* Edit `.wfc/` state or Evidence by hand.
* Remove or edit anything under `.git/` by hand, a stale lock included:
  report it and let the human decide.
* Open or resolve a Finding.
* Relaunch `wfc run` in a checkout that already has a lifecycle.
* Run a `wfc` subcommand this skill does not describe. (`prepare-successor`
  and `export-salvage` are the maintainer-level
  `python -m workflow_coordinator.operator` tool, not `wfc` subcommands;
  `references/stops-and-evidence.md` describes them and this rule does not
  withhold them.)
* Install or upgrade WFC, or this skill, unasked.

## Where exact facts come from

The **installed Runtime** is authoritative for what it does: each
subcommand's own `--help` (there is no top-level `wfc --help`; a bare
unrecognized subcommand falls through to an unrelated legacy entry point --
never run one), and the output of `wfc plan`, `wfc status`, `wfc report`.
**This skill** ships with that Runtime and describes that version -- and
nothing about a consumer's own stack, which comes from its configuration, its
preparation hook and its operator notes.

The **Operator Manual** pages linked from here and from the references are
pinned to the release tag this skill shipped with, and are supplementary:
where the installed CLI has a function they do not describe, the CLI and this
skill are right and the page is merely older. Never read a pinned page as
documenting a newer function, and never claim that it does
(`references/setup.md` states how the three sources relate).

The wider pattern, as released with v0.1.0b1:
[the Level-1 operating pattern](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch10_level1_orchestration/01_operating_pattern.adoc).

---
name: wfc
description: >-
  Operate an installed WFC (workflow-coordinator, the `wfc` CLI): turn a
  development task into a bounded, human-authorized Commission, preview it
  with `wfc plan`, launch and monitor one Run, and read stops, reviews and
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

An operating guide for the session that orchestrates WFC from outside --
**Level 1**: deciding what task to run, authoring and launching one bounded
Commission at a time, and reading the result. It grants no authority: WFC's
own runtime enforcement decides what a launch may actually do, whatever this
text says. The human's own instructions may narrow these defaults further;
nothing here widens them. A WFC Worker or Reviewer session does not use this
skill -- it executes inside an already-authorized Commission, under its own
distinct operating contract.

## Authorization

WFC's authority model, stated as rules, neither looser nor stricter than it is
implemented and practised. This skill introduces no approval step of its own and has no
delegation object, file or flag of its own to grant one.

1. **Authority facts** -- the human decides scope, program, Hop budget, and any binding
   in `wfc plan` that differs from the role's committed `default_execution`. `--yes`
   asserts this authorization without verification.
2. **Initial authorization** -- for a fact the human has not yet decided: propose a value,
   show the `wfc plan` projection, ask once, undecided facts only.
3. **Existing delegation** -- a decision already made (task instruction, standing
   instruction, or an approved decomposition) covers a launch with scope, program and Hop
   budget within the delegation and each binding as approved or matching its committed
   role default. Proceed with `--yes` without a further approval turn, and report the
   digests and what was launched.
4. **Narrow reading** -- ambiguity is not delegation: ask. The scope ceiling in
   `.wfc/config.yaml` bounds a Commission; it does not approve one.
5. **Never delegated by a launch** -- judging a stop's disposition; widening scope or
   budget, also after a stop; a push, tag, publication or configuration change; changing
   the installed WFC version; discarding a branch, worktree, stash or preserved ref this
   session did not itself create for this task.
6. **The record** -- `authority_source` names truthfully who authorized the Commission and
   by which instruction.
7. **The reserved go** -- when the human keeps the launch decision, prepare everything and
   hand over the exact `wfc run` command, or wait for the instruction to launch.
8. **Own-worktree cleanup** -- not withheld by item 5: once a Run is archived and
   integrated or otherwise dispositioned, remove the worktree and scratch directory this
   session itself created for it, without a further ask.

Always stop and ask before: a push, tag, publication or configuration change; a Runtime
promotion or a change of installed WFC version; widening scope, authority, program or
Hop budget beyond what the human set; judging a stop's disposition; discarding a branch,
worktree, stash or preserved ref this session did not itself create for this task.

## Preflight

Run every time, before relying on this skill for an actual task:

1. `wfc --version --json`. If it fails, WFC is not installed in this shell:
   stop and tell the human.
2. `wfc skill status claude` in Claude Code, `wfc skill status codex` in
   Codex. If the command fails, the installed WFC predates this skill: stop
   and tell the human. The other host is not checked.
3. Take the installation whose `PATH` is the directory this `SKILL.md` was
   loaded from. `current`: proceed. Any other state: read `wfc skill show`
   and follow that text for this session, and tell the human to re-run
   `wfc skill install <host>` for that scope. If it is unclear which
   installation this session was loaded from, proceed only when every
   reported installation that is not `absent` is `current`; otherwise use
   `wfc skill show`.

## When WFC fits, and when it does not

Fits: a bounded, scoped development task in a repository that already has
`.wfc/config.yaml`, where the user wants a governed, independently reviewed
(or self-checked) change. Does not fit: ordinary edits the user did not ask
to route through WFC; deciding what the larger goal is or how to decompose it
-- that stays the human's and this session's own job, not WFC's; chaining
several Commissions automatically -- WFC executes exactly one bounded
Commission per Run; anything that needs a push, a tag, a publication or a
Runtime promotion -- WFC never does any of those itself.

## The loop

See `references/setup.md` for repository readiness and `wfc init`,
`references/commission.md` for authoring, and `references/stops-and-evidence.md`
for everything about a stop.

Delegated task authority persists across execution attempts: a technical
failure or a stopped Run does not end it, and uncertainty about *why* an
attempt failed is not, by itself, a reason to ask -- try a plausible,
verifiable, bounded correction before the cause is proven. A new human
decision is needed only for what Authorization's item 5 withholds, never
merely because an earlier attempt failed.

1. Confirm the repository is ready (clean tree, `.wfc/config.yaml`, a
   committed guidance file, a committed evidence/task-check command).
2. Draft the Commission: task document, scope, program, personas, evidence.
3. Preview it with `wfc plan` (read-only; writes nothing, launches nothing).
4. Run the authorization check above; ask only for what is not yet decided.
5. In a fresh worktree, with the task document and Consumer Request outside
   the repository, launch with `wfc run --request ... --yes`.
6. Monitor with `wfc status`. Read `CLASSIFICATION`, never the exit code --
   `awaiting_human` and other legitimate stops exit non-zero too.
7. On `CLASSIFICATION: completed`, render with `wfc report` and verify
   independently against the declared scope before treating it as accepted,
   then integrate and remove this Run's own worktree and scratch directory
   (Authorization item 8) without a further ask. On anything else, read
   `references/stops-and-evidence.md`: act on your own where it says this
   mandate already covers the next step (a corrected probe retried in a
   fresh checkout, `wfc resume` where permitted, a successor Commission at
   the same scope/program/Hop budget); bring to the human only a stop that
   genuinely asks for a new authorization decision.

## Never

* Edit an in-scope file directly once the task is routed through WFC.
* Commit inside a Run's checkout.
* Edit `.wfc/` state or Evidence by hand.
* Open or resolve a Finding.
* Relaunch `wfc run` in a checkout that already has a lifecycle.
* Run a `wfc` subcommand this skill does not describe.
* Install or upgrade WFC, or this skill, unasked.

## Where exact facts come from

The `--help` of each subcommand (there is no top-level `wfc --help`; a bare
unrecognized subcommand falls through to an unrelated legacy entry point --
never run one), the `wfc plan` output, `wfc status`, `wfc report`, and the
tag-pinned Operator Manual pages linked from the three reference files:
[consumer quickstart](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch09_consumer_quickstart/01_quickstart.adoc),
[the Level-1 operating pattern](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch10_level1_orchestration/01_operating_pattern.adoc),
[Commission authoring](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch02_commissioning/02_authoring.adoc),
[launch, status and terminal states](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch03_running/02_launch_status.adoc),
[recovery outcomes](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch06_recovery/01_outcomes.adoc) and
[the archive](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch04_evidence/02_archive.adoc).

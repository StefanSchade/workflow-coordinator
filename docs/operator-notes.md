# Operator notes -- workflow-coordinator

Level-1 operating notes for Runs *in this repository*, read by the session
that drives `wfc` from outside. The shipped Agent Skill
(`src/workflow_coordinator/agent_skill/wfc/references/setup.md`, section
*Consumer operator notes*) defines this file's convention: it is read from
committed history, at the base state a Run starts from, only when it exists,
and it carries **no authority whatever** -- no delegation, no scope, no
approval, and no licence to skip a rule of the skill. Where it contradicts
the installed CLI, the CLI is right and the note is stale.

Reading it from a named commit makes *which* notes a Run is operated by
reproducible and out of reach of an uncommitted edit or a Run still in
flight. It does not make the content trustworthy: this file sits under
`docs`, which is inside the committed scope ceiling, so a Commission scoped
to it may change it, and once that change is reviewed and integrated it is
part of a later Run's base. Nothing here is therefore human-authored or
individually checked by the maintainer merely because it is committed. A
change to this file inside a Run's scope should go through that Run's
ordinary independent review; keep the file out of a Worker's scope when no
Worker should touch it.

Not for Workers: a Worker's binding policy document is the Commission's
`guidance.path` (`.wfc/governance/repository-policy.md`), not this file.
Authoritative homes for everything below:
[the developer guide](developer_guide/ch01_development.adoc),
[the Operator Manual](operator_manual/operator_manual.adoc) and the backlog
record a Commission names.

## The gates to declare as evidence

This repository's own deterministic gates, cheapest first, are `make lint`
(`ruff check .`), `make format-check`, `make test` (the pytest suite) and
`make docs-render` (strict AsciiDoc render of `docs/`). `make check`
aggregates the first three and `make check-docs` adds the render; both remain
admitted as a degenerate one-command sequence, but prefer the ordered form:

```text
--evidence-command lint         --evidence-argv make --evidence-argv lint
--evidence-command format-check --evidence-argv make --evidence-argv format-check
--evidence-command task-check   --evidence-argv make --evidence-argv test
--evidence-command docs-render  --evidence-argv make --evidence-argv docs-render
```

Declare `docs-render` whenever the Commission writes `.adoc`. A lint-only
defect (an unused import left by a removal) has survived ten Hops and two
broad reviews under a tests-only evidence command; the cheap gate is what
catches it.

Timing against the 900-second per-command cap: the suite runs in roughly six
minutes with the verified offline input bundle in the environment, and has
reached 897 seconds without it. Treat a Run launched without the bundle as a
Run that will fail its own task-check.

## Preparing a Run checkout

```shell
wfc prepare-checkout --repository . --path ../wfc-SLICE-NNN \
  --branch wfc/SLICE-NNN --hook scripts/wfc_prepare_checkout_env.sh
```

`.wfc/config.yaml` declares no `checkout_preparation_hook`, so the hook is
named explicitly with `--hook`. The hook is committed development tooling,
not product surface: it creates a virtual environment in the *scratch*
directory with an editable install of this repository from the offline
bundle, verifies the bundle's bytes against the profile before installing
anything, and declares `WFC_PREPARE_PATH_PREPEND`, `WFC_TEST_INPUTS` and
`WFC_TEST_PROFILE` on its declaration channel. It fails, by name, when the
bundle is missing; its default location is
`$XDG_DATA_HOME/wfc/offline-test-inputs/PROFILE` (falling back to
`~/.local/share` when `XDG_DATA_HOME` is unset), and the developer guide's
*offline test inputs* section says how to build and verify one.

Then `source SCRATCH/launch-env.sh` and run `wfc plan`/`wfc request`/
`wfc run` from the new checkout, so the baseline probe and every Hop's
task-check inherit that environment. Run the exact declared commands by hand
there first: a refused probe spends the checkout's one lifecycle.

Hazards recorded here:

* Do not set `PYTHONPATH=src` in a checkout that already carries a
  `src/*.egg-info` from the editable install -- it is redundant and can
  shadow the installed metadata.
* Never share one virtual environment between checkouts: an editable install
  pointing at another worktree breaks this checkout's own task-check.
* Keep `TMPDIR` in the scratch directory, never inside the worktree.

## The scope ceiling and its pinning test

The committed ceiling is `src`, `tests`, `scripts`, `docs`, `README.md`,
`pyproject.toml`, `Makefile`, `examples/consumer-demo`, and
`tests/unit/test_self_development_consumer_config.py` asserts that exact
list. A maintainer-granted temporary entry therefore has to land together
with that test, in the same worktree-local base commit the Run starts from;
otherwise the next Run's baseline probe fails before any provider runs. Say
so in the task text when a Commission needs one.

`.wfc/` is outside the ceiling, so no Commission writes the configuration,
the governance directory or this repository's own Commission records.

## Hop budgets observed here

`max_autonomous_hops` is a worst-case ceiling, and roughly twice the expected
count has held in this repository. Measured, not promised: a narrow,
Hop-scoped package written against a controlling record converges in about
2-5 Hops under `robust@v1`; a broad task without one in about 7-12; a
retire-heavy change (removing a mechanism, its tests and every statement
about it) has needed about twice its otherwise expected count. The ten
Beta-2 operator-efficiency slices used 1-10 of their 6-14 Hop ceilings and
none stopped on budget.

An evaluate-first slice may legitimately end with a recorded decision not to
ship; budget it like a design slice rather than an implementation one.

## Incidents worth not repeating

* An empty, read-only `.git/config.lock` from a dead session blocks
  `git config` writes. Check for a live owner (`fuser`, or `pgrep` against a
  running `git`/provider process) before concluding it is stale -- and then
  still report it instead of removing it: nothing under `.git/` is this
  session's to delete.
* A scratch directory removed by name collided with a pre-existing directory
  of the same name and lost its content. Prefix every operator-created
  worktree and scratch path with the slice id, and delete only paths the same
  script created and recorded.
* `wfc plan` and `wfc run` started in one command line left the preview
  unread until the Run had begun. Read the preview, then launch.
* A docs-sync scope that omitted one document family provoked an escalation
  after the work was done. Derive the scope mechanically: search the
  repository for the statement or path the change retires, and list every
  file that holds it.
* A Worker that combined an escalation with a `CommitIntent` stopped the Run
  mechanically before task-check and review; the candidate had to be salvaged
  and re-derived by a successor. The task text should say that a stale or
  out-of-scope statement is reported as a note in a `completed` result.

## This repository as a consumer

WFC developing itself is an ordinary consumer case: the Runtime that executes
a Run is a separately installed, promoted version, never "latest `main`", and
nothing in the product knows that the payload happens to be WFC. Keep the
four directories apart -- the scratch directory, the Run's checkout, the
Evidence repository (`../workflow-coordinator-run-evidence`) and the
installed Runtime.

# Commission authoring

Distils Operator Manual chapter 2,
[Commission authoring](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch02_commissioning/02_authoring.adoc).

## Task document

A task document is `# title` / body / `Acceptance` / `Withheld authority` /
`Default escalation`, written outside the repository (a sibling scratch
directory keeps the working tree clean). `wfc plan` and `wfc run` read the
Consumer Request's `guidance.path` from Git history at a pinned commit, never
from the working tree, so the guidance file must already exist and be
committed before planning.

## `wfc plan` preview

Entirely read-only: no project command, provider, Commission write or Git
mutation.

```shell
wfc plan task.md --repository . \
  --scope src \
  --commission-id example-001 --work-item-id WI-EXAMPLE-001 \
  --authority-source "Repository owner authorizes this bounded task." \
  --program simple@v1 \
  --persona worker="Bounded worker persona." \
  --evidence-argv sh --evidence-argv checks/check.sh \
  --guidance-path .wfc/governance/repository-policy.md
```

`--scope` is repeatable: a bounded, file-exact (or deliberately
directory-scoped) mutation boundary. `--program` is `simple@v1` or
`robust@v1` (`direct@v1` is withheld). `--persona` is repeatable, one per
required program role. `--evidence-argv` is repeatable: one argv token of
the task-check command, also used as the mandatory pre-launch baseline
probe. `--durable-artifact PHASE=PATH` is `robust@v1` only.

## Consumer Request fields

Once the preview looks right, author the equivalent Consumer Request YAML
with the same fields under `task:`, `scope:`, `personas:`, `evidence:` and
`guidance:`, plus `schema_version: 1`, `commission_id`, `work_item_id`,
`authority_source`, `program`, `max_autonomous_hops` and an explicit
`durable_artifacts:` (normally `{}` for `simple@v1`). `wfc plan` does not
write this file; `wfc run` reads it from `--request` by plain filesystem
path, so it need not live inside the repository.

**Current limitation**: nothing emits this file for you -- author it by hand
from the fields `wfc plan` just previewed, and keep it outside the
repository with the task document. `wfc report` likewise cannot resolve the
Evidence location from the repository's own config; pass the archived
Attempt directory or `audit.json` path explicitly (see
`stops-and-evidence.md`). (Planned: a dedicated emission command would
reproduce the previewed Commission digest exactly, and `wfc report` would
resolve the Evidence location itself.)

## Commission-authoring rules

* **Verify, do not inherit.** Before writing a claim about a shipped file
  (what a text says, what a test asserts, what a default is) into a task
  document, check it against the actual file -- a premise inherited from an
  earlier task or a planning document can be stale or simply wrong, and
  writing it into a Commission adopts the error.
* **Check the basis document's baseline.** When a task is commissioned from
  a document that itself names a base commit or a comparison point, check
  that baseline against the current `main` before commissioning, and state
  any drift in the task text; a basis document that is many commits stale
  can carry claims the repository has already outgrown.
* **A temporary scope-ceiling entry and its pinning test move together.**
  If a task needs a maintainer-granted temporary entry in
  `.wfc/config.yaml`'s `work_item_scope_ceiling`, state that the repository's
  own test asserting the exact committed ceiling list (for example
  `tests/unit/test_self_development_consumer_config.py:42-48`) must be
  updated in the same worktree-local base commit, or the next Run's baseline
  probe fails before any provider runs.
* **State the commit mechanics explicitly.** Remind the Worker in the task
  text: no `git add`/`git commit`/other Git mutation -- report a
  `CommitIntent` instead -- and the `CommitIntent.subject` must carry the
  WorkItem id and the current Hop id (for example
  `feat(x): <summary> (WI-..., H00N)`); a subject missing either stops the
  Run mechanically (`WFC-GIT-08`), not through review.

## Execution bindings and `--executable`

`wfc plan`/`wfc run` resolve each role's adapter/model/reasoning from
`.wfc/config.yaml`'s `default_execution`, any `--execution-override
ROLE.FIELD=VALUE`, then `phase_execution`; see `setup.md` for the full
precedence and the YAML shape. All effective bindings in a Run share one
adapter, and `wfc run --executable` must match it (`--executable claude`
for `claude_cli`, `--executable codex`, the default, for `codex_cli`). WFC
refuses an unambiguous cross-adapter mismatch before dispatch -- a bare
`--executable` name resolving to the *other* adapter's own known binary --
but never an explicit path or an unrecognized bare name; get it right at
launch time regardless of this narrow safety net.

## Scope and the scope ceiling

Every scope path must equal, or sit strictly beneath, a path already named
in `.wfc/config.yaml`'s `--scope-ceiling`; a root-level file must be listed
individually, never implied by a sibling directory. A Commission that moves
or deletes many files must list every directory or file holding a link into
the moved paths -- an unlisted consumer stops the Run after the work is
done.

## `simple@v1` versus `robust@v1`

`simple@v1`: a sole Worker self-checks and the Coordinator runs the
`task-check`; no independent review/correction loop. `robust@v1`: fresh
independent review and, when warranted, correction and renewed broad
review. Choose per package: a small, low-risk package is a reasonable
`simple@v1` candidate; anything whose acceptance should rest on adversarial
review belongs on `robust@v1`.

## Hop budget

`max_autonomous_hops` is a bounded *worst-case safety ceiling*, not an
estimate of expected Run length -- the single most commonly misapplied
piece of operator judgment. A too-tight ceiling converts healthy
review/correction convergence into an unnecessary `awaiting_human_budget`
stop; there is no in-place raise once a Run stops on an exhausted budget --
a fresh Commission is required. (Planned: an explicit, journaled,
maintainer-authorised extension of a budget-stopped Run; an agent never
extends a budget on its own, and Finding disposition stays the independent
reviewer's.) Budget using observed convergence, not the
expected count: a narrow, Hop-scoped package written against a controlling
record has converged in roughly 2-5 Hops under `robust@v1`, a broad task
without one in roughly 7-12; a retire-heavy correction (removing a mechanism
and its tests, plus a docs-sync pass) has needed roughly twice the otherwise
expected count, so budget that shape accordingly rather than assuming the
usual multiplier covers it.

## The evidence command and its baseline probe

A Commission declares exactly one trusted `task-check` command (timeout at
most 900 seconds). **The command must run the repository's own
deterministic gates, not only its tests** -- when the repository has a
combined target such as `make check` (lint, format and tests), name that,
or a wrapper that runs them in order; a command that runs tests alone can
pass ten Hops and two Broad Reviews while one lint-only defect (an unused
import left by a removal) goes undetected, because no test node runs the
linter. Reviewer and Worker Hops are not a substitute for a cheap
deterministic check that would have caught the same defect at the first
Hop's task-check. For documentation-only work use the repository's
documentation-inclusive aggregate (`make check-docs`) instead. (This is the
interim rule; an ordered evidence-command list is a separately authorized,
category-B product change this rule does not require.) The identical
command runs first, before any provider starts, as a mandatory pre-launch
baseline probe: it must already be committed, exit `0` on today's clean
tree, and leave the tree and `HEAD` unchanged. A non-zero exit, an
unresolvable executable, a timeout, or any tree mutation refuses the whole
Run before launch (`refused_probe`) and still consumes this checkout's one
lifecycle. A non-zero-exit refusal prints a bounded, explicitly untrusted
tail of the stdout/stderr it already captured, beside the exit code -- still
run it by hand in the fresh checkout, with any required environment already
prepared, before ever launching, rather than relying on that tail alone.
Diagnosing and
correcting a correctable cause -- a missing declared input, a misplaced
scratch directory -- and repeating the probe in a fresh checkout is
ordinary preparation, not a decision that needs asking first; see
`stops-and-evidence.md` for the full table.

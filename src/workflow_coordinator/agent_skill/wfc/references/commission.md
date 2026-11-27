# Commission authoring

Distils Operator Manual chapter 2,
[Commission authoring](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch02_commissioning/02_authoring.adoc),
as released with v0.1.0b1.

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
  --evidence-command lint --evidence-argv LINT_ARGV_TOKEN \
  --evidence-timeout 120 \
  --evidence-command task-check --evidence-argv TASK_CHECK_ARGV_TOKEN \
  --guidance-path .wfc/governance/repository-policy.md
```

`--scope` is repeatable: a bounded, file-exact (or deliberately
directory-scoped) mutation boundary. `--program` is `simple@v1` or
`robust@v1` (`direct@v1` is withheld). `--persona` is repeatable, one per
required program role. `--durable-artifact PHASE=PATH` is `robust@v1` only.
`--execution-override ROLE.FIELD=VALUE` adjusts one resolved binding field;
`setup.md` holds the precedence and the YAML shape, and the same
one-adapter/`--executable` rule applies at launch.

The evidence flags declare an **ordered sequence of named commands**:
`--evidence-command NAME` opens a gate, and the `--evidence-argv TOKEN`
(repeatable, one token per flag) and optional `--evidence-timeout SECONDS`
(default and maximum 900, **per command**) that follow belong to it. Tokens
given before any `--evidence-command` belong to an implicit first gate named
`task-check`, so a single `--evidence-argv` pair alone still means what it
always did. `wfc plan` prints the gates in order with the worst-case total.

## `wfc request`: emitting the Consumer Request

`wfc request` takes exactly the same task document and flags as `wfc plan`,
plus an explicit `--output PATH`, and writes the Consumer Request YAML
`wfc plan` would preview -- byte for byte the same request-construction path,
so the two commands' projected digests are always identical for identical
inputs. The following is a fragment, not a complete command line: repeat
every flag of the `wfc plan` call above, with `request` in place of `plan`,
and add the output path.

```text
wfc request task.md --repository . ... --output ../wfc-scratch/request.yaml
```

Refuses, before any write: an already-existing `--output` file (pass
`--force` to overwrite), and an `--output` path inside this repository's own
worktree (pass `--allow-inside-repository` to allow it) -- an emitted,
uncommitted file inside the tree would dirty exactly the clean tree `wfc
run` requires, so keeping it in a sibling scratch directory (as above) needs
no flag at all. It authorizes nothing: no Git mutation, no provider call, and
its closing line restates that scope, program, personas, Hop budget, the
ordered evidence commands and `authority_source` are this operator's own
judgement, merely carried, never validated by it. `wfc run --request PATH`
re-reads the file from a plain filesystem path -- so it need not live inside
the repository -- re-parses it under the closed schema and re-digests it
exactly as it would a hand-authored one; the emitted file grants no authority
of its own.

## Commission-authoring rules

* **Verify, do not inherit.** Before writing a claim about a shipped file
  (what a text says, what a test asserts, what a default is) into a task
  document, check it against the actual file -- a premise inherited from an
  earlier task or a planning document can be stale or simply wrong, and
  writing it into a Commission adopts the error.
* **Check the basis document's baseline.** When a task is commissioned from
  a document that itself names a base commit or a comparison point, check
  that baseline against the current integration branch before commissioning,
  and state any drift in the task text; a basis document that is many commits
  stale can carry claims the repository has already outgrown.
* **A temporary scope-ceiling entry and its pinning test move together.**
  If a task needs a maintainer-granted temporary entry in
  `.wfc/config.yaml`'s `work_item_scope_ceiling`, and the repository has a
  test asserting the exact committed ceiling list, state in the task that the
  test must be updated in the same worktree-local base commit -- otherwise
  the next Run's baseline probe fails before any provider runs.
* **State the commit mechanics explicitly.** Remind the Worker in the task
  text: no `git add`/`git commit`/other Git mutation -- report a
  `CommitIntent` instead -- and the `CommitIntent.subject` must carry the
  WorkItem id and the current Hop id (for example
  `feat(x): SUMMARY (WI-..., H00N)`); a subject missing either stops the
  Run mechanically (`WFC-GIT-08`), not through review.
* **A note is not an escalation.** A `CommitIntent` is permitted only with a
  clean disposition -- no escalation, no `scope_changed`, no `requires_human`;
  combined with any of them it stops the Run mechanically before task-check
  and review, and the candidate then needs salvage and a successor. Say in
  the task text that a stale or out-of-scope statement the work uncovers is
  reported as a note in a `completed` result.

## Scope and the scope ceiling

Every scope path must equal, or sit strictly beneath, a path already named
in `.wfc/config.yaml`'s `--scope-ceiling`; a root-level file must be listed
individually, never implied by a sibling directory. A Commission that moves
or deletes many files must list every directory or file holding a link into
the moved paths -- an unlisted consumer stops the Run after the work is
done. Derive that list mechanically: search the repository for the statement
or path the work retires, before launching.

## `simple@v1` versus `robust@v1`

`simple@v1`: a sole Worker self-checks and the Coordinator runs the evidence
sequence; no independent review/correction loop. `robust@v1`: fresh
independent review and, when warranted, correction and renewed broad
review. Choose per package: a small, low-risk package is a reasonable
`simple@v1` candidate; anything whose acceptance should rest on adversarial
review belongs on `robust@v1`.

## Hop budget

`max_autonomous_hops` is a bounded *worst-case safety ceiling*, not an
estimate of expected Run length -- the single most commonly misapplied
piece of operator judgment. A too-tight ceiling converts healthy
review/correction convergence into an unnecessary `awaiting_human_budget`
stop. Such a Run can be continued in place with `wfc extend-budget`, but
only on the maintainer's explicit instruction in the current session and
within its bounds (at most four Hops per call, in total at most half the
ceiling this Commission admitted) -- *an agent never extends a budget on its
own* (`stops-and-evidence.md`). Plan the ceiling so you never need it.

Budget from convergence actually observed in this repository -- its own
operator notes are where such figures belong -- not from the expected count,
and widen the ceiling for a shape that has historically needed it (a
retire-heavy change that removes a mechanism, its tests and the statements
about it across documents). A figure measured once in one repository is an
observation, not a rule for the next.

## The evidence commands and their baseline probe

A Commission declares an **ordered sequence of named trusted commands**, each
with its own timeout of at most 900 seconds (a per-command cap: the
sequence's worst case is the sum). **The sequence must be the repository's
own deterministic gates, not only its tests, cheapest first** -- typically a
linter, then a formatting check, then the test suite, plus whatever gate
covers the kind of artifact this Commission writes (a documentation render,
for example). The repository names them; this skill does not. They run in the
declared order and stop at the first failure, which the diagnostic names, so
a lint residue costs one cheap gate instead of a test suite. A sequence that
runs tests alone can pass ten Hops and two Broad Reviews while one lint-only
defect (an unused import left by a removal) goes undetected, because no test
node runs the linter; reviewer and Worker Hops are not a substitute for a
cheap deterministic check. One conflated command (an aggregate target or a
wrapper script) is still admitted as the degenerate one-element sequence, but
it is no longer the rule: it reports one exit code for every gate and cannot
fail fast at the cheap one.

The identical sequence runs first, in the same order, before any provider
starts, as a mandatory pre-launch baseline probe: every command must already
be committed, exit `0` on today's clean tree, and leave the tree and `HEAD`
unchanged. A non-zero exit, an unresolvable executable, a timeout, or any
tree mutation refuses the whole Run before launch (`refused_probe`), naming
the command, and still consumes this checkout's one lifecycle. A
non-zero-exit refusal prints a bounded, explicitly untrusted tail of the
stdout/stderr it already captured -- still run the commands by hand in the
fresh checkout, with any required environment already prepared, before ever
launching, rather than relying on that tail alone. Diagnosing and correcting
a correctable cause -- a missing declared input, a misplaced scratch
directory -- and repeating the probe in a fresh checkout is ordinary
preparation, not a decision that needs asking first; see
`stops-and-evidence.md` for the full table.

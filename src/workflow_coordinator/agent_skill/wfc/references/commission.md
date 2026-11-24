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
a fresh Commission is required.

## The evidence command and its baseline probe

A Commission declares exactly one trusted `task-check` command (timeout at
most 900 seconds). The identical command runs first, before any provider
starts, as a mandatory pre-launch baseline probe: it must already be
committed, exit `0` on today's clean tree, and leave the tree and `HEAD`
unchanged. A non-zero exit, an unresolvable executable, a timeout, or any
tree mutation refuses the whole Run before launch (`refused_probe`) and
still consumes this checkout's one lifecycle.

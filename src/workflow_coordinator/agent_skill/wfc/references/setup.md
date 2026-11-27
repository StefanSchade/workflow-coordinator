# Setup

Distils Operator Manual chapter 9, [consumer quickstart](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch09_consumer_quickstart/01_quickstart.adoc), as released with v0.1.0b1.

## Prerequisites

Nothing here assumes a language, build tool or test runner: WFC runs the
commands the consumer repository declares.

* Git, with a completely clean working tree -- `wfc run` refuses a dirty
  one -- and a `TMPDIR` outside that working tree.
* An already-installed, already-authenticated provider CLI for the adapter
  in use: `codex` for `codex_cli`, `claude` for `claude_cli`.
* The repository's own check commands, already committed, each exiting `0` on
  today's clean tree without changing anything -- `wfc run` executes the
  declared sequence verbatim, in order, as a mandatory pre-launch baseline
  probe and again as the Commission's declared evidence.
* A Git identity in real Git configuration, and no content filter (Git LFS
  included) reachable from the repository -- see *Git identity and filters*
  below; the trusted commits refuse outright otherwise.

## Install and version discovery

WFC itself is a Python package -- a fact about the Runtime, not about any
repository it operates on. Install it in its own virtual environment, outside
every consumer repository's working tree.

```shell
python3 -m pip install 'workflow-coordinator==0.1.0b1'
wfc --version --json
```

The exact pin installs this pre-release deliberately: a plain
`pip install workflow-coordinator` does not. `--version --json` reports the
executing package's implementation identity. If `wfc skill status HOST`
leaves it unclear which installation this skill was loaded from, proceed only
when every reported installation that is not `absent` is `current`; else read
`wfc skill show` and follow it.

Three version-bearing sources, in order of authority: the **installed
Runtime** (each subcommand's `--help`, and `plan`/`status`/`report` output)
says what will actually happen; **this skill** ships with it and describes
that same version; the **Operator Manual** pages linked from these files are
pinned to that version's release tag and are supplementary -- a development
tree runs ahead of them, so a function the CLI has and a pinned page does not
mention is normal, and never cite such a page as its documentation. The
release step rewrites these pins together with the version literals, so every
pinned link names the release it shipped with.

## `wfc init` and configuration

```shell
wfc init --repository . --scope-ceiling src --evidence-repository ../wfc-evidence
```

This writes `.wfc/config.yaml` (never committing it) and creates or adopts
the Evidence repository -- a separate, remote-less Git repository.
`--scope-ceiling` is repeatable and names every path a future Commission's
scope may touch; a root-level file is listed individually, never implied by a
sibling directory. The config then needs a `default_execution` binding per
role and a commit -- the maintainer's own file, never edited on your own
initiative. Its optional `hop_timeout_seconds` key is this repository's
default per-Hop CAP, meant only to end a genuinely hung provider invocation,
never a budget for ordinary work; `wfc run --hop-timeout-seconds` overrides it
for one invocation, and absent both the default is 14400 seconds (4 hours).

## Execution bindings and adapters

```yaml
default_execution:
  worker:
    adapter: codex_cli
    model: YOUR_MODEL
    reasoning: medium
```

Precedence: `default_execution.ROLE`, then any
`--execution-override ROLE.FIELD=VALUE` given to `wfc plan`/`wfc run` (the
value is preserved exactly, surrounding spaces included), then the optional
`phase_execution.PHASE` entry. For `robust@v1`, keep the
`independent_reviewer` role binding and add per-phase models on top:

```yaml
phase_execution:
  broad_review: {model: BROAD_REVIEW_MODEL}
  focused_review: {model: FOCUSED_REVIEW_MODEL, reasoning: medium}
```

Use exact phase names reachable from the chosen program's entry through
`goto` routes. A phase entry supplies at least one of `adapter`, `model` or
`reasoning`; every *effective* binding must end up with all three, nonempty,
single-line and provider-supported. WFC refuses unknown or unreachable phase
keys, extra fields, incomplete bindings and invalid values.

All effective bindings in a Run must use one adapter (`codex_cli` or
`claude_cli`), and `wfc run --executable` must match it: `--executable claude`
for `claude_cli`, `--executable codex` (the default) for `codex_cli`. WFC
refuses an unambiguous cross-adapter mismatch before dispatch -- a bare
(non-path) `--executable` name resolving to the *other* adapter's own known
binary -- but never an explicit path or an unrecognized bare name, so a
legitimate provider wrapper is never blocked. Align them yourself regardless.

Read the effective bindings by phase in `wfc plan` before authorization. An
omitted or empty `phase_execution` keeps the role-keyed envelope and digest;
with phase overrides the digest covers the complete phase-keyed envelope. WFC
pins that envelope through launch and resume and records it in evidence.

A committed `default_execution` role entry the selected program does not use
(an `independent_reviewer` default kept for `robust@v1`, in a repository that
also runs `simple@v1`) is tolerated, not refused: `wfc plan` names every
ignored default in its preview. Only an unused `--execution-override` is
still refused -- a per-Commission intent that cannot take effect, unlike a
standing repository default several programs may share.

## Git identity and filters

The trusted Git operations (the Commission commit, candidate preservation,
`wfc run`'s bounded path commit) strip every inherited `GIT_*` variable
first, so `user.name`/`user.email` must be set in a file Git actually reads
(`~/.gitconfig`, system config, or `$XDG_CONFIG_HOME/git/config` when `HOME`
changes between processes), never only in `GIT_AUTHOR_*`/`GIT_COMMITTER_*`/
`GIT_CONFIG_GLOBAL`. They refuse outright on any visible
`filter.*.clean`/`filter.*.process`, even one the commit would never invoke:

```shell
git config --show-origin --get-regexp '^filter\..*\.(clean|process)$'
```

## `wfc prepare-checkout`: one fresh checkout per Run

```shell
wfc prepare-checkout --repository . --path ../work-run7 --branch work/run7
```

One command prepares what a Run needs and removes nothing. It creates the
worktree at `--path` on the new `--branch` from `--base-ref` (default
`HEAD`), a sibling scratch directory (`PATH-scratch`) with `tmp/` for
`TMPDIR`, runs this repository's declared preparation hook, and writes
`SCRATCH/launch-env.sh`. `source` that file, then run `wfc plan`/`wfc
request`/`wfc run` in the new checkout: the baseline probe and each Hop's
task-check inherit the launching environment. It authorizes nothing and
refuses by name before creating what it can avoid creating: a checkout
identity that already carries a lifecycle (`refused_existing_lifecycle`), an
existing path or branch, a missing parent, a path inside *any* working tree
of the repository, a scratch directory inside the checkout, a dirty base, an
unsafe hook. One Run per checkout is unchanged: the identity is the resolved
top-level *and* the Git common directory, so a rotated checkout needs a new
*path*, not just a fresh `git worktree add`; a spent path is never reusable.

## The preparation hook, and what it is trusted with

Everything stack-specific about preparing a checkout -- an environment for the
declared check commands, inputs they need, caches -- is the consumer's own
*hook*: an executable committed in the repository, declared by the optional
one-line `checkout_preparation_hook:` key of `.wfc/config.yaml` or named with
`--hook REPOSITORY_RELATIVE_PATH` (`--no-hook` skips it). WFC reads the
declaration and the hook **at the base ref**, never from a working tree or a
Run's candidate; gives it the checkout path, the scratch path and the base
commit with `TMPDIR` set; and exports the bounded, never-trusted `NAME=VALUE`
lines it prints on stdout (its only output channel -- diagnostics go to
stderr), composing `PATH` from the hook's `WFC_PREPARE_PATH_PREPEND`. A
failing or hanging hook is a named refusal; what was created is reported and
left in place.

**A committed hook is code the maintainer approved by committing it**: it runs
on the operator's host, as the operator, before any Hop and outside every WFC
boundary, and WFC bounds its own writes, not the hook's. Never write or edit a
hook, or `.wfc/config.yaml` (the maintainer's file), on your own -- ask. Keep
whatever the hook builds in the scratch directory, never in the checkout and
never shared across checkouts: an environment pointing at another checkout
breaks that checkout's own task-check.

## Consumer operator notes: `docs/operator-notes.md`

A repository may commit its own Level-1 operating notes at
`docs/operator-notes.md`: a plain file convention, not a mechanism -- no
config key names it, no command reads it, WFC never projects it anywhere.

* **From a specific committed base state**: at the base ref the Run starts
  from (`git show BASE_REF:docs/operator-notes.md`), never a working tree and
  never a Run's candidate. That fixes *which* notes a Run is operated by --
  reproducibly, and beyond the reach of an uncommitted edit or of a Run still
  in flight -- but it does not make them trustworthy, and it does not mean a
  Run cannot write them: a Commission scoped to the file may change it, and
  once reviewed and integrated that change is part of a later Run's base.
  Keep the file out of Worker scopes if no Worker should touch it.
* **On demand only**: a cheap existence check while authoring a task; for a
  repository without one, nothing is missing and no step is skipped.
* **Level-1 notes only**: this repository's gate names and what each costs,
  what its preparation hook sets up, hazards and incidents observed here, Hop
  convergence measured here.
* **No authority, ever.** Advice to the operator: never a delegation, scope,
  approval or licence for anything Authorization withholds, and never a reason
  to skip a rule of this skill. Where it contradicts the installed CLI, the
  CLI is right and the note is stale.
* **Not the Commission's `guidance.path`**, which is the Worker's committed
  policy document, read at a pinned commit and binding on the Worker. The
  notes bind nobody. A repository declares its own by committing the file:
  never infer from a repository's name that notes should exist, and never
  create or edit them unasked -- propose, and let the human decide.

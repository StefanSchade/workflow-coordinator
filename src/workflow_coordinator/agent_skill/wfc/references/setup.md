# Setup

Distils Operator Manual chapter 9,
[consumer quickstart](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch09_consumer_quickstart/01_quickstart.adoc).

## Prerequisites

* Git, with a completely clean working tree -- `wfc run` refuses a dirty one.
* Python 3.10+ to run `wfc` itself.
* An already-installed, already-authenticated provider CLI for the adapter
  in use: `codex` for `codex_cli`, `claude` for `claude_cli`.
* One or more already-committed check commands, each exiting `0` on today's
  clean tree without changing anything -- `wfc run` executes the declared
  sequence verbatim, in order, as a mandatory pre-launch baseline probe and
  again as the Commission's declared evidence.
* A Git identity (`user.name`/`user.email`) visible in actual Git
  configuration, never only via `GIT_AUTHOR_*`/`GIT_COMMITTER_*`/
  `GIT_CONFIG_GLOBAL` environment variables -- WFC's trusted Git operations
  strip inherited `GIT_*` variables first.
* No configured `filter.*.clean`/`filter.*.process` (including Git LFS)
  reachable from the repository -- WFC's trusted commits refuse outright
  when one is visible.
* `TMPDIR` pointing outside this repository's working tree.

## Install and version discovery

```shell
python3 -m venv ~/.venvs/wfc
source ~/.venvs/wfc/bin/activate
python3 -m pip install 'workflow-coordinator==0.1.0b1'
wfc --version --json
```

The exact version pin installs this pre-release deliberately -- a plain
`pip install workflow-coordinator` does not install a pre-release by
default.

`--version --json` reports the executing package's implementation identity.
There is no top-level `wfc --help`: an unrecognized bare subcommand falls
through to an internal, unrelated legacy entry point -- never run one. Each
subcommand below accepts its own `--help` instead.

## `wfc init` and configuration

From the repository root:

```shell
wfc init --repository . --scope-ceiling src --evidence-repository ../wfc-evidence
```

This writes `.wfc/config.yaml` (never committing it) and creates or adopts
the Evidence repository -- a separate, remote-less Git repository.
`--scope-ceiling` is repeatable and names every path a future Commission's
scope may touch; a root-level file is listed individually, never implied by
a sibling directory. Edit `.wfc/config.yaml` to add a `default_execution`
binding per role, then commit it:

```shell
git add .wfc/config.yaml
git commit -m "chore: add wfc configuration"
```

`.wfc/config.yaml` also accepts an optional `hop_timeout_seconds` key (a
positive number of seconds): this repository's default per-Hop CAP, meant
only to end a genuinely hung provider invocation, never a budget for
ordinary work. `wfc run --hop-timeout-seconds` overrides it for one
invocation; absent both, the built-in default is 14400 seconds (4 hours).

## Execution bindings and adapters

```yaml
default_execution:
  worker:
    adapter: codex_cli
    model: <your model>
    reasoning: medium
```

WFC starts with `default_execution.<role>` and applies
`--execution-override ROLE.FIELD=VALUE` from `wfc plan` or `wfc run`.
Both commands preserve the value exactly, including surrounding spaces.
The optional `phase_execution.<phase>` entry applies last. For `robust@v1`,
keep the role binding for `independent_reviewer` and add the top-level
mapping below with your approved provider model IDs.

```yaml
phase_execution:
  broad_review: {model: <your broad review model>}
  focused_review: {model: <your focused review model>, reasoning: medium}
```

Use exact phase names reachable from the chosen program's entry through
`goto` routes. A phase entry must supply at least one of `adapter`, `model`
or `reasoning`. Every effective binding must have all three fields. WFC
refuses unknown or unreachable phase keys, extra fields, incomplete
bindings and empty or invalid values. `model` and `reasoning` must be
nonempty, single-line strings encodable as UTF-8, with no NUL. Choose models
and reasoning supported by the provider. All effective bindings must use one adapter
(`codex_cli` or `claude_cli`), and `wfc run --executable` must match it -- pass
`--executable claude` for a `claude_cli` binding, `--executable codex` (the
default) for `codex_cli`. WFC refuses an unambiguous cross-adapter mismatch
before dispatch -- a bare (non-path) `--executable` name that resolves to the
*other* adapter's own known binary -- but never an explicit path or an
unrecognized bare name, so a legitimate provider wrapper is never blocked.
Get the executable and adapter aligned regardless; do not rely on this narrow
check to catch every mismatch.

Read the effective bindings by phase in `wfc plan` before authorization.
An omitted or empty `phase_execution` keeps the existing role-keyed
envelope and digest. With phase overrides the digest covers the complete
phase-keyed envelope. WFC pins that envelope through launch and resume,
and records the choices in execution evidence.

## Role defaults a program does not use

A committed `default_execution` role entry that the selected program does
not use (for example an `independent_reviewer` default kept for `robust@v1`
Commissions, in a repository that also runs `simple@v1`) is tolerated, not
refused: `wfc plan` names every ignored default in its preview. Only an
unused `--execution-override` is still refused -- it is a per-Commission
intent that cannot take effect, unlike a standing repository default several
programs may legitimately share.

## A stale lock in `.git/`

An empty, read-only `.git/config.lock` (or a similar lock file) left over
from an earlier session blocks `git config` writes. Before asking the human
to remove it, check that no live process holds it (for example `fuser` or
`pgrep` against a running `git`/provider process); a lock with no owning
process is very likely stale, but removing any file under `.git/` by hand is
outside this skill's own mandate -- report the finding and let the human
decide, rather than removing it yourself.

## Git identity and filters

WFC's trusted Git operations (the Commission commit, candidate preservation,
`wfc run`'s bounded path commit) strip every inherited `GIT_*` environment
variable before running Git. Set `user.name`/`user.email` in a configuration
file Git actually reads (`~/.gitconfig`, system config, or
`$XDG_CONFIG_HOME/git/config` when `HOME` changes between processes). The
same operations refuse outright when any effective `filter.*.clean`/
`filter.*.process` is visible to them, even if the specific commit would
never invoke it:

```shell
git config --show-origin --get-regexp '^filter\..*\.(clean|process)$'
```

## `wfc prepare-checkout`: one fresh checkout per Run

```shell
wfc prepare-checkout --repository . --path ../work-run7 --branch work/run7
```

One command prepares what a Run needs and removes nothing. It creates the
worktree at `--path` on the new `--branch` from `--base-ref`
(default `HEAD`), a sibling scratch directory (`<path>-scratch`) with `tmp/`
for `TMPDIR`, runs this repository's declared preparation hook, and writes
`<scratch>/launch-env.sh`. `source` that file, then run `wfc plan`/`wfc
request`/`wfc run` in the new checkout: the baseline probe and each Hop's
task-check inherit the launching environment. It authorizes nothing and runs
no `plan`/`run`. It refuses by name, before creating what it can avoid
creating: a path whose checkout identity already carries a lifecycle
(`refused_existing_lifecycle`), an existing path, a missing parent, a path
inside *any* working tree of the repository, a scratch directory inside the
checkout, a dirty base, an existing branch, an unsafe hook. One Run per
checkout is unchanged: the identity is the resolved top-level *and* the Git
common directory, so a rotated checkout needs a new *path*, not only a fresh
`git worktree add`. A spent path stays spent -- this makes a fresh one cheap,
never a spent one reusable, and removes no worktree, branch or directory.

## The preparation hook, and what it is trusted with

The consumer-specific part -- a venv with its own editable install, declared
inputs, whatever else the check command needs -- is a *hook*: an executable
committed in the repository, declared by the optional one-line
`checkout_preparation_hook:` key of `.wfc/config.yaml` or named with `--hook
<repository-relative path>` (`--no-hook` skips it). WFC reads the declaration
and the hook **at the base ref**, never from a working tree or a Run's
candidate; gives it the checkout path, the scratch path and the base commit
with `TMPDIR` set; and exports the bounded, never-trusted `NAME=VALUE` lines
it prints on stdout (its only output channel -- diagnostics go to stderr),
composing `PATH` itself from the hook's `WFC_PREPARE_PATH_PREPEND`. A failing
or hanging hook is a named refusal; what was created is reported, left in
place.

**A committed hook is code the maintainer approved by committing it**: it runs
on the operator's host, as the operator, before any Hop and outside every WFC
boundary, and WFC bounds its own writes, not the hook's. Never write or edit a
hook, or `.wfc/config.yaml` (the maintainer's file), on your own -- ask.

Correcting that environment inside an authorized Commission is still ordinary
preparation needing no separate human decision; a probe that failed for a
correctable reason may be retried in a *fresh* checkout. Put the venv in the
scratch directory, never in the checkout, and never share one across
checkouts: an editable install pointing at another worktree breaks that
worktree's task-check. WFC's own hook is `scripts/wfc_prepare_checkout_env.sh`
(offline bundle, `WFC_TEST_INPUTS`/`WFC_TEST_PROFILE`). Where a checkout
already carries a `src/*.egg-info` from an editable install, do not also set
`PYTHONPATH=src` there: it is redundant and can shadow the installed metadata.

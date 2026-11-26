# Setup

Distils Operator Manual chapter 9,
[consumer quickstart](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch09_consumer_quickstart/01_quickstart.adoc).

## Prerequisites

* Git, with a completely clean working tree -- `wfc run` refuses a dirty one.
* Python 3.10+ to run `wfc` itself.
* An already-installed, already-authenticated provider CLI for the adapter
  in use: `codex` for `codex_cli`, `claude` for `claude_cli`.
* A single, already-committed check command that exits `0` on today's clean
  tree without changing anything -- `wfc run` executes it verbatim as a
  mandatory pre-launch baseline probe, and again as the Commission's
  declared `task-check` evidence.
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
default) for `codex_cli`. **Current limitation**: nothing refuses a mismatch
before dispatch -- a `claude_cli` binding launched without `--executable
claude` fails inside the first Hop instead of at prelaunch, costing a whole
checkout. Get this right at launch time; do not rely on a refusal to catch
it. (Planned: a named pre-dispatch refusal for an unambiguous
adapter/executable mismatch retires this paragraph.)

Read the effective bindings by phase in `wfc plan` before authorization.
An omitted or empty `phase_execution` keeps the existing role-keyed
envelope and digest. With phase overrides the digest covers the complete
phase-keyed envelope. WFC pins that envelope through launch and resume,
and records the choices in execution evidence.

## `simple@v1` in a repository with a committed reviewer default

**Current limitation**: `simple@v1` uses no `independent_reviewer` role, so a
committed `.wfc/config.yaml` that still carries an `independent_reviewer`
default (set for `robust@v1` Commissions) makes `wfc plan`/`wfc run` refuse
every `simple@v1` Commission outright ("execution config defaults name roles
the selected program does not use"). Do not edit the committed
`.wfc/config.yaml` to work around this -- a repository's own test may pin its
exact committed defaults, and an edit breaks that pin and fails the baseline
probe, which still consumes the checkout's lifecycle. Instead create a
second, reviewer-less Consumer Config file (for example
`.wfc/config-simple.yaml`, a copy of the committed one with the
`independent_reviewer` default removed), select it with `--config
.wfc/config-simple.yaml` on both `wfc plan` and `wfc run` -- `wfc run`
re-verifies the committed `--config` file from Git history at the authorized
HEAD, and a dirty tree is refused, so this second file must be committed in
this checkout's own local branch, but never merged or pushed into `main`: it
is a workaround for this checkout's Run, not a repository artifact. (Planned:
tolerating an unused *config* role default removes this file and this whole
failure mode.)

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

## Worktree and scratch layout

Prepare one fresh Git worktree per Run -- a checkout that has already
authorized one lifecycle refuses every later `wfc run`, keyed to the
checkout's resolved top-level directory *and* its Git common directory
together. Removing a worktree and recreating one at the *same path* is not
fresh: it hashes to the same checkout identity and `wfc run` answers "a
lifecycle already exists for this checkout; use `wfc resume`". A rotated
checkout needs a new *path*, not only a fresh `git worktree add`. Write the
task document and the Consumer Request *outside* the repository (a sibling
scratch directory): an untracked file anywhere inside the repository counts
as a dirty tree and refuses launch. Keep the scratch directory, the
worktree/checkout, the Evidence repository and the installed Runtime
(changed only by explicit promotion) apart. (Planned: a dedicated
preparation command would make a fresh checkout mechanical and cheap.)

## Preparing the check command's own environment

Preparing, verifying and, where the committed check or baseline probe needs
it, correcting that command's own environment -- declared inputs, scratch
directories, `TMPDIR` placement outside the worktree -- is ordinary
preparation within an already-authorized Commission; it needs no separate
human decision each time, and a probe that fails for a correctable reason
may be retried, once corrected, in a fresh checkout. **Current limitation**:
every fresh checkout needs its own venv with an editable install of the
package under test (`pip install -e <checkout>`) -- a venv shared across
checkouts, whose editable install still points at a different worktree,
breaks *that* worktree's own task-check; do not share one venv across
concurrent checkouts. For WFC's own self-development specifically, the
offline test-input bundle and `WFC_TEST_INPUTS`/`WFC_TEST_PROFILE` this
entails are the same ordinary preparation step, described in Operator Manual
chapter 3's offline-input guidance. Also for self-development: once a
checkout has run `pip install -e .`, it carries a `src/*.egg-info`
directory; do not then set `PYTHONPATH=src` when running the suite in that
same checkout -- with the package already installed editable, `pip` and the
import machinery see it as installed, and adding `src` to `PYTHONPATH` on
top of that is redundant at best and can shadow the installed metadata.
(Planned: a deterministic per-checkout preparation step would make this
recipe mechanical rather than a manual recipe to repeat correctly every
time.)

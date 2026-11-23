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

## Execution bindings and adapters

```yaml
default_execution:
  worker:
    adapter: codex_cli
    model: <your model>
    reasoning: medium
```

`adapter` must be `codex_cli` or `claude_cli`; a Run uses exactly one
adapter throughout, and `wfc run --executable` must match the configured
adapter. `--execution-override ROLE.FIELD=VALUE` overrides one resolved
binding field on `wfc plan`/`wfc run` without editing the committed file.

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
authorized one lifecycle refuses every later `wfc run`. Write the task
document and the Consumer Request *outside* the repository (a sibling
scratch directory): an untracked file anywhere inside the repository counts
as a dirty tree and refuses launch. Keep the scratch directory, the
worktree/checkout, the Evidence repository and the installed Runtime
(changed only by explicit promotion) apart.

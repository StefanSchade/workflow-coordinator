# WFC Consumer Demo Kit

A generator for a disposable WFC demo workspace: a tiny consumer
repository (`greeter/`), its Evidence repository, and three ready-made
Commission requests -- created outside this checkout, with no remote.
Deleting the workspace directory removes every trace.

## Commands

    python3 make_workspace.py WORKSPACE [--wfc PATH]

Creates the workspace at `WORKSPACE`, a path that does not yet exist or is
an empty directory, outside every Git work tree. The `wfc` executable used
is `--wfc`, else the `WFC_BIN` environment variable, else `wfc` on `PATH`.

    python3 rehearse.py WORKSPACE SCENARIO [--wfc PATH]

Runs one complete WFC Run offline and in seconds, in a fresh worktree
(`greeter-run-SCENARIO/`), with a scripted stand-in for the provider instead
of a real one. `SCENARIO` is one of `t1-hello-page`, `t2-farewell`,
`t3-scope-stop` or `t3-probe-refusal` (see `scenarios/*.json`). Every
archived Hop of a rehearsal names the model `rehearsal-no-model`, and a
banner says so before and after WFC's own output -- this shows WFC's
control flow, commits and evidence, never what a real model would do.
Reusing a scenario's worktree is refused; create a new workspace to
rehearse it again.

Once the workspace exists, inspect `requests/` and run any of the three
tasks live, with your own provider credentials, in a fresh worktree of your
own (a sibling of `greeter/`, as the rehearsal worktrees are), using the
installed `wfc plan` (preview, no write) and `wfc run` (a real Run, without
`--yes`, so you answer the authorization prompt yourself) -- see each
`requests/*.request.yaml` file for the exact fields, and the consumer
quickstart below for the full walkthrough.

## Phase-specific models for the live `t2-farewell` task

For `robust@v1`, set role defaults and add `phase_execution` in the live
worktree's `.wfc/config.yaml`. Replace the sample model names with your
approved provider choices and commit the config before launching.

```yaml
default_execution:
  worker: {adapter: codex_cli, model: worker-model, reasoning: medium}
  independent_reviewer: {adapter: codex_cli, model: broad-model, reasoning: high}
phase_execution:
  broad_review: {model: broad-model}
  focused_review: {model: focused-model, reasoning: medium}
```

Both review phases keep the `independent_reviewer` role and use the same
adapter and executable. `wfc plan` shows the binding for each phase and
its envelope digest. Phase entries apply after role defaults and role
CLI overrides. Use this fragment with the live `t2-farewell` request in
a dedicated worktree because its review phases belong to `robust@v1`.

## Further reading

See the chapter overview,
`docs/operator_manual/ch09_consumer_quickstart/00_overview.adoc`, for a map
of the workspace this kit builds, and the three tutorials built on it --
`02_first_run_example.adoc`, `03_tutorial_program_change.adoc` and
`04_tutorial_stops.adoc` in the same directory -- for the full walkthroughs
this kit shortens.

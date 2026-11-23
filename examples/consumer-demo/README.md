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

Once the workspace exists, inspect `requests/` and run any of the three
tasks live, with your own provider credentials, using the installed
`wfc plan` (preview, no write) and `wfc run` (a real Run) -- see the
command printed by `make_workspace.py` and each
`requests/*.request.yaml` file for the exact fields.

An offline, scripted rehearsal (`rehearse.py`) that runs a scenario without
any provider is planned for a later slice of this kit and does not exist
yet.

## Further reading

See the consumer quickstart chapter,
`docs/operator_manual/ch09_consumer_quickstart/_chapter.adoc`, for the full
walkthrough this kit shortens.

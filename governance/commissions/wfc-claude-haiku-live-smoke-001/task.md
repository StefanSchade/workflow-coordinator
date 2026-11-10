# Qualify Claude CLI file and scratch tools

This is a small tool integration smoke test. Work only in
docs/qualification/claude-haiku-20261110/.

First use Read on fixture.txt and verify its exact initial content is:
WFC Claude Haiku qualification fixture
Initial state: pending

Use the Claude Edit tool to change the second line to
"Initial state: verified and edited". Use the Claude Write tool to create
nested/results.txt, where nested/ does not exist at the start. Record
concise factual observations in these qualification files.

Use the Claude Bash tool to inspect TMPDIR, resolve its underlying target,
and establish both the temporary path and its underlying scratch target are
writable, for example by creating and removing a small temporary probe file
through each path. Record the observed paths and factual success or failure
in nested/results.txt. Do not record credentials, tokens, or secrets.

Return the structured WorkResult required by the provided contract. For
CommitIntent.paths list exactly these two leaf files, not their directory:
docs/qualification/claude-haiku-20261110/fixture.txt and
docs/qualification/claude-haiku-20261110/nested/results.txt.
The Coordinator owns the commit. Do not run Git or commit yourself.


## Acceptance

The existing fixture was read and its expected initial content verified,
then edited using Edit. Write created nested/results.txt in the initially
absent nested directory. Bash inspected TMPDIR and verified that both the
temporary path and underlying scratch target are usable and writable.
The qualification files record concise factual results without secrets.
The structured result and exact two leaf-file CommitIntent match the
changes. The Coordinator-owned task-check passes.


## Withheld authority

Do not write outside docs/qualification/claude-haiku-20261110, modify
governance, access other repositories, run Git, or commit. Do not claim
post-invocation cleanup or Coordinator acceptance as observed facts.


## Default escalation

Stop and report blocked or failed if the initial fixture differs, any
required tool or scratch probe fails, or the task requires authority outside
the scope. Do not infer success from an unperformed check.


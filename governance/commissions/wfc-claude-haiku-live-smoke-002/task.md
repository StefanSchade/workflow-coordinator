# Qualify Claude CLI file tools and Bash

This is a small tool integration smoke test. Work only in
docs/qualification/claude-haiku-20261110/.

First use Read on fixture.txt and verify its exact initial content is:
WFC Claude Haiku qualification fixture
Initial state: pending

Use the Claude Edit tool to change the second line to
"Initial state: verified and edited". Use the Claude Write tool to create
nested/results.txt, where nested/ does not exist at the start. Record
concise factual observations in the results file.

Use the Claude Bash tool to execute the simple command `pwd`. Record the
observed result in nested/results.txt. Do not record secrets.

Return the successful structured WorkResult required by the provided
contract. For CommitIntent.paths list exactly these two leaf files:
docs/qualification/claude-haiku-20261110/fixture.txt and
docs/qualification/claude-haiku-20261110/nested/results.txt.
The Coordinator owns the commit. Do not run Git or commit yourself.


## Acceptance

The existing fixture was read and its expected initial content verified,
then edited using Edit. Write created nested/results.txt in the initially
absent nested directory. Bash successfully executed pwd. The qualification
files record concise factual results without secrets. The structured result
and exact two leaf-file CommitIntent match the changes. The Coordinator-owned
task-check passes and the Run completes normally.


## Withheld authority

Do not write outside docs/qualification/claude-haiku-20261110, modify
governance, access other repositories, run Git, or commit. Do not claim
Coordinator acceptance as an observed fact within the worker invocation.


## Default escalation

Stop and report blocked or failed if the initial fixture differs, any
required tool fails, or the task requires authority outside the scope.
Do not infer success from an unperformed check.


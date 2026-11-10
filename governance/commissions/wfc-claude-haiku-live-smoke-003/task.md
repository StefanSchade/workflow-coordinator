# Smoke test the native Claude CLI

Work only in docs/qualification/claude-haiku-20261110/.
Use Read on fixture.txt and verify its initial content is:
WFC Claude Haiku qualification fixture
Initial state: pending

Use Edit to change the second line to "Initial state: verified and edited".
Use Write to create nested/results.txt and record concise factual results.
Use Bash to execute the single command `pwd` and record its result.

When the authorized work is finished, return the required structured
WorkResult with status: completed. The commit_intent.paths must list exactly
these two changed leaf files:
- docs/qualification/claude-haiku-20261110/fixture.txt
- docs/qualification/claude-haiku-20261110/nested/results.txt

The commit_intent.subject must be one line in Conventional Commit syntax
and contain the exact WorkItem ID and Hop ID supplied by WFC as separate
identity tokens. For this Commission and its first Hop, use exactly:
test(smoke): WI-WFC-CLAUDE-HAIKU-LIVE-SMOKE-003 H001 verify native Claude CLI
Confirm those identities against the WFC invocation context. The
Coordinator owns the commit. Do not run Git or commit yourself.


## Acceptance

The fixture was read and edited as requested, nested/results.txt was
created using Write, and the single Bash command pwd succeeded. The
completed structured result proposes precisely the two changed leaf files
and a valid subject containing the exact WorkItem and Hop identities.


## Withheld authority

Do not write outside docs/qualification/claude-haiku-20261110, modify
governance, access other repositories, run Git, or commit. Do not claim
downstream Coordinator evidence or commit acceptance as observed facts.


## Default escalation

Stop and report blocked or failed if the initial fixture differs, any
required tool fails, or the task requires authority outside the scope.
Do not infer success from an unperformed check.


# Add actionable diagnostics to the unresolved evidence-command executable error

Implement the diagnostics improvement described in docs/backlog/BUG-WFC-EVIDENCE-COMMAND-RESOLUTION-DIAGNOSTICS.adoc (read it, its non-scope and the two closed records it cites first). When `evidence.resolve_executable` cannot resolve an authored argv[0] on the snapshotted PATH, the raised error stays fail-closed with the same exception type and the existing message prefix, but additionally names (a) the unresolved authored command and (b) a bounded, read-only list of interpreter-like sibling names actually discoverable on the snapshotted PATH (for example python3), presented as information only, and (c) one sentence on how a conforming environment is supplied (prepending a prepared bin directory to PATH). Never substitute an alias, never change resolution, fingerprinting or canonical-identity behavior, never change `_preflight` refusal behavior. Add focused regression tests in the existing evidence tests: the message carries the new information, the probe is bounded and read-only, resolution results are unchanged for resolvable and unresolvable commands, and an unresolvable command is still refused. Existing tests must pass unchanged. Update the BUG record's status line only if its own upkeep convention allows; otherwise leave it for the maintainer. A Worker never reports any Finding operation (resolve, residual or any other delta); only the independent reviewer opens and resolves Findings. The CommitIntent names every changed leaf individually, never directories; no agent Git writes.

## Acceptance

The unresolved-executable error names the authored command, lists discoverable sibling interpreters from the snapshotted PATH as information and explains how a conforming environment is supplied; resolution, fingerprinting, alias handling and preflight refusal behavior are unchanged; focused regression tests are mutation-sensitive and the existing suite passes; independently accepted with no blocking Findings and Coordinator task-check passed.

## Withheld authority

No automatic alias substitution, no change to resolve_executable's PATH walk, fingerprinting or canonical identity model, no change to preflight refusal semantics, no change outside src/workflow_coordinator/evidence.py, its focused tests and (optionally) the BUG record, no push, merge, rebase, Runtime promotion or tag.

## Default escalation

Escalate only a real scope or authority conflict; incomplete work alone is not blocked.

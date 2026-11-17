# Fix SEC-03 Git scope observation and Coordinator-only commit enforcement

Implement the bounded SEC-03 correction recorded in docs/backlog/BUG-WFC-SEC-03-GIT-SCOPE-COMMIT-AUTHORITY.adoc against actual current code and tests. The accepted technical starting point is the 2026-11-16 independent security assessment SEC-03 follow-up; that full report is currently uncommitted in the security-review worktree and is not on main. The controlling bug record captures the relevant finding and decision. If accessible read the report as read-only context at ../workflow-coordinator-security/docs/warm_storage/concepts/wfc_security_assessment_2026-11-16.adoc; its presence is not a prerequisite and do not copy or adopt it. Work exclusively against current Development baseline; no new general investigation.

Disable Git rename detection explicitly in affected scope-observation range diffs in _observe_hop, salvage inventory diffs, and operator per-Hop and summary reporting diffs, so deleted source and added destination are independently observable regardless of diff.renames. Inspect current code for the actual affected paths. Preserve _staged_paths and trusted-commit behavior: an intent listing both rename leaves must retain the existing staged-path mismatch, and one listing only the destination must retain unexpected-dirty-path refusal. Rename-shaped CommitIntents remain a documented limitation; do not enable them.

Since Slice 4B agents supply CommitIntents and the Coordinator alone creates commits. Reject any agent-authored commit in a Hop without authorized CommitIntent, including implementation and review Hops; use the existing max_commits=0 in the no-intent observation branch if actual contracts and tests confirm suitability. Clean no-commit Hops and valid Coordinator-owned commits must continue working. Do not remove legacy fields or mechanics. No Git authorization redesign, Evidence sandbox, general security hardening or product-goal change.

Add meaningful synthetic-repository regression coverage for out-of-scope rename and delete-plus-similar-add (including source path observed), in-scope rename without scope anomaly, diff.renames true/false/copies, salvage refusal for out-of-scope staged source and complete reporting, agent commits in implementation and review, empty scope and multiple commits, clean no-commit Hops, valid Coordinator commits, unchanged trusted-commit behavior and the existing documented rename-intent limitations. Adapt historical tests only where they explicitly expect newly excluded behavior; preserve other coverage. Run relevant focused tests and the full repository suite, report exact results; Coordinator task-check is separately trusted. Update only necessary Operator Manual and Technical Model contract explanations and the controlling bug record with candidate status and actual checks, stating independent review and Coordinator evidence are pending. Do not claim future acceptance. Keep the separate legacy debt item as authored, with no cleanup implementation.

Use exact changed repository-relative leaf paths in CommitIntent, a Conventional Commit subject including WI-WFC-SEC-03-GIT-SCOPE-001 and actual Hop ID as separate bounded tokens. Agents do not write Git commits. Review artifacts belong to review phases. If a genuine contract conflict prevents this bounded fix, stop for maintainer disposition with concrete evidence; do not invent a new architecture initiative.

Fresh independent broad reviewer inspect actual candidate and full diff, not only worker report. Falsify scope observation, salvage and reporting under each Git configuration, Coordinator-only refusal across phases, clean and trusted commit paths, tests and documented limitations. Verify no inadvertent staged-path or result-contract change and assess actual full-suite results and boundaries. Block material defects, not style; normal robust correction and focused rereview apply, with renewed broad review when configured.

## Acceptance

Both SEC-03 gaps are corrected with unchanged valid trusted-commit behavior, meaningful focused and full-suite regression checks pass, current operator/technical contract reflects actual enforcement, independent fresh review has no blocking Findings, and Coordinator trusted task-check passes. Acceptance and any Runtime promotion remain separate maintainer decisions.

## Withheld authority

No Git authorization redesign, Evidence sandbox, general security hardening, legacy result-field or mechanism removal, rename-shaped CommitIntent support, workflow program or governance changes, product-goal changes, report adoption, agent Git writes, additional Commission, Runtime promotion or push.

## Default escalation

For a concrete contract conflict or required authority outside this bounded mandate, stop with evidence and a precise maintainer decision; do not broaden scope or substitute models.

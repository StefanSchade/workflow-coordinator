# Correct interim Claude Bash curl permission gate

Implement the smallest authorized correction in src/workflow_coordinator/claude_adapter.py: add exactly `Bash(curl:*)` to the existing permissions.allow list. The Maintainer expressly accepts that this interim command permission for ordinary non-sensitive development is broader than read-only HTTPS: curl options may permit uploads, other destinations and protocols subject to remaining effective sandbox rules. This is not public-only OS-enforced containment or live connectivity evidence; WFC-owned containment is not yet integrated.

Inspect the controlling internet FTR, P3-001 qualification and H002/H004/H005 review artifacts, actual Development and accepted Runtime, current Claude adapter and conformance/architecture regressions, and installed /home/stefan/.local/bin/claude version 2.1.322 read-only. The P3 Run RUN-c5971877b9374b04b71066666cfe37d7 completed as partial: agent-reported WebFetch success, Bash curl refused before execution, separate outside-working-directory path restriction; no Bash network reachability observation. Preserve the existing predecessor records. Review installed-version matcher and preceding direct investigation: `curl:*` prefix semantics, deny and ask precedence, command spelling (curl versus absolute executable), compound/quoted/variable commands and safety analysis, and outside-working-directory check that can preempt an allow rule. Confirm support using offline version-specific evidence; do not infer guaranteed effective admission or connectivity. No authenticated Claude invocation or live probe.

Preserve exact existing WebFetch/WebSearch selection and permissions, --restricted/--safe-mode/acceptEdits/no prompts, enabled required sandbox and no unsandboxed fallback, filesystem and blockReadsOutsideWorkingDirectories, scratch/TMPDIR lifecycle, explicit environment and credential handling, model/effort/cwd/input/output/timeout and result-correlation semantics. No generic Bash allowance, auto-allow-all, bypass, new network service, network policy, source beyond the one adapter leaf, provider upgrade, host settings or Runtime change. Exact allowance is the only production behavior change.

Update existing Claude conformance tests to assert exact generated settings including the one rule and unchanged surrounding configuration, and exercise relevant adapter, execution-boundary and architecture regressions offline. Add meaningful deterministic matching/negative checks only if practical without live Claude; fake transport alone does not prove actual provider enforcement. Update only relevant current FTR and _open navigation to record candidate, explicit command scope and exposure, and distinguish pending review/trusted task-check, remaining live qualification, managed policy and path limitations. Preserve H001/H002 dated observations and historical P3 outcome. Render changed AsciiDoc and validate relevant links/diff hygiene. Agent-local tests are not Coordinator evidence.

If rule cannot be supported while preserving safeguards return truthful blocked with precise escalation, no CommitIntent and clean worktree; feasibility first and if necessary restore only your own safely attributable modifications. No agent-owned Git writes. On completion CommitIntent must list every actual changed exact leaf once, Conventional Commit subject includes WI-WFC-CLAUDE-BASH-CURL-PERMISSION-001 and actual Hop ID as separate bounded tokens. Review artifacts reserved to respective review phases.

The fresh independent Terra High broad reviewer must inspect full candidate and installed 2.1.322 version-specific prefix matching and restrictions independently. Assess precise permitted `curl` command, deny/ask precedence, unsupported forms, compound analysis, outside-working-directory checks, intentional upload/private-network exposure and no public-only claim. Verify unchanged web permissions and sandbox/filesystem/scratch/env/credential/result/Git/evidence boundaries and meaningful deterministic tests. No live Claude calls. Block material defects only; normal robust correction/focused and renewed broad-review routing applies.

## Acceptance

Exact Bash(curl:*) grant with preserved existing safeguards and meaningful deterministic tests, independent fresh Terra High review without blocking Findings, and passing Coordinator trusted task-check; no claim of live connectivity.

## Withheld authority

No authenticated Claude invocation or live qualification, other production source/test changes, Runtime promotion, provider upgrade, host/network configuration, broad Bash bypass/auto-allow, disabling sandbox or filesystem restrictions, new network services, policy/workflow changes, agent Git writes, additional Commission or push.

## Default escalation

If version-supported rule cannot be established without violating safeguards, return blocked with exact reason, no CommitIntent and clean worktree; no model substitution.

# Correct BP3 plan, initialization and bounded-commit contract violations

Correct only the three concrete BP3 residuals R1, R4 and R2 in `docs/warm_storage/concepts/wfc_beta1_bp3_init_plan_commit_001_broad_review.adoc` (review `56de52d` of candidate `80f14d0`). The original RUN-7551242df88c4980b731b6d8714fabb3 completed under its configured WFC review; its historical result and review remain unchanged. Its WFC acceptance does not constitute product acceptance of the still-unmet explicit BP3 contracts. Inspect the original Commission task and relevant current code/tests; do not create a new architecture or other BP3 features.

R1: `wfc plan` must not run repository-configured external Git helpers such as `core.fsmonitor`. A helper was actually executed by `observe_repository` and wrote a marker, violating explicit plan no-project-command/no-mutation acceptance. Prevent such invocation on the plan path while preserving HEAD/index/worktree read-only behavior and existing trusted checks. Test a real executable marker helper in a disposable consumer Git repository, configure Git to use it, then invoke plan and assert it did not run and HEAD, raw index and all worktree bytes/modes remain unchanged. Exercise any additional Git commands the plan path uses; no project checks, hooks or provider calls.

R4: `wfc init --config-path` must remain strictly inside the selected consumer repository. Refuse absolute paths, `..` traversal and symlink escapes before *any* mutation or directory creation. Test each with real disposable directories, including a preexisting symlinked parent leading outside; compare in/outside sentinel files, paths and Git state. Preserve valid in-repository init and its no-clobber behavior. No uncontrolled path outside fixture.

R2: Prevent a symlink swap between file type validation and Git staging from becoming an accepted/published unauthorized commit in `bounded_path_commit`. A post-commit error is insufficient once the invalid commit exists. Test the review's concrete race via deterministic injection at the relevant boundary and assert no unacceptable commit is created, HEAD unchanged, and no unauthorized content or path is committed; report any safe staging residue honestly. Preserve exact authorized new-file semantics, authority, hook/filter protections and successful operation. Validate the staged content/modes before publication or an equivalently sound bounded mechanism; no unprovable universal race-freedom guarantee is required. Address the reproduced swap, not speculative architecture.

No changes to compiler, provider, reducer, Runtime, legacy renderer, BP4/5 mechanisms, workflow programs or unrelated BP3 features. Preserve existing test assertions, no silent weakening; add targeted tests that fail on the old behavior and pass on correction. Use verified offline inputs; Coordinator independently runs full task-check. Update only relevant implementation documentation at the actual milestone; do not claim independent acceptance or product closure before they occur. CommitIntent must list exact changed leaves and use a Conventional Commit subject containing WI-WFC-BETA1-BP3-REMEDIATION-001 and the actual Hop ID as separate bounded tokens. No agent Git writes. Review artifacts are reserved to the review phases.

The independent reviewer checks these three concrete original contract violations and direct regressions only: reproduce or validate the marker test and no mutation; in-repository init path, absolute/traversal/symlink refusal before mutation; deterministic pre-publication symlink-swap protection and valid new-leaf success; no hooks/filters or existing BP3 authority weakened. A reproduced violation of an explicit acceptance criterion is a blocking Finding under the newly pinned reviewer contract; do not reclassify it based on low presumed risk, existing behavior or cost. If task/scope/feasible solutions conflict, escalate to the maintainer. Do not invent broad architecture requirements, speculative universal race guarantees or cosmetic blockers. Coordinator task-check remains separate.

## Acceptance

R1 external Git helper cannot run via wfc plan and marker/HEAD/index/worktree test passes; R4 init rejects absolute, traversal and symlink escapes before mutation while valid init works; R2 deterministic pre-staging symlink swap cannot publish an unauthorized commit; existing tests and authority not weakened; independent review without blocking Findings plus full Coordinator task-check pass.

## Withheld authority

No other BP3 features, compiler, providers, reducer, runtime, legacy render/check CLI, workflow program, unrelated code or historical review edits; no BP4/BP5 implementation in this Commission, extra Commission, push, merge, rebase, Runtime promotion or agent Git writes. Review artifacts reserved to review phases.

## Default escalation

Escalate a real conflict between task, scope and feasible solution; no broad unprovable race-freedom requirement.

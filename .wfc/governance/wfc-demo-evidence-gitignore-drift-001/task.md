# Guard the consumer demo Evidence gitignore against drift

Read O-1 in docs/backlog/BUG-WFC-CONSUMER-DEMO-KIT-GIT-IDENTITY.adoc and docs/warm_storage/post-beta-planning/wfc_bug_demo_kit_git_identity_broad_review.adoc. Add a focused regression test in tests/integration/test_consumer_demo_kit.py that compares the demo's actual generated Evidence repository .gitignore byte-for-byte with the Development source's workflow_coordinator.frontdoor.config.evidence_gitignore_block(). Preserve the kit's standard-library-only constraint and avoid product changes or general refactoring. Demonstrate mutation sensitivity by temporarily changing one block in a disposable copy or reversible experiment, running the test and reporting the exact mutation, command and failure; restore and run against unchanged product, reporting the passing result. Do not commit the mutation. Record the work and measured checks in the new backlog work item, leaving independent acceptance and integration for the maintainer. Do not modify the closed BUG or its historical review. Follow repository policy; agents do not perform Git writes. CommitIntent lists each changed leaf separately. Only the independent reviewer opens or resolves Findings.

## Acceptance

The test detects any byte difference between the produced demo block and the Development managed block, passes for the unchanged product, and demonstrably fails on an intentional difference. No existing test is weakened. The work item states the observation, scope, exact red/green evidence and pending acceptance; the Coordinator's full required task-check passes. Independent review evaluates the whole result and records its evidence in the designated review artifact.

## Withheld authority

No changes to product implementation, WFC trusted Git boundary, kit, configuration, runtime, existing closed BUG or its review, other tests, dependency declarations, or existing governance. No push, tag, publication, Runtime promotion or historical worktree cleanup. No weakening or skipping tests. Escalate any necessary scope expansion.

## Default escalation

Escalate scope or authority conflicts, inability to demonstrate mutation sensitivity, or a material disagreement with the recorded observation; report facts rather than asserting acceptance.

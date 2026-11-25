# Correct the token-usage example in the Front Door docs-sync convention

GOAL. Correct one factual inaccuracy in the convention added by Commission wfc-beta2-frontdoor-docs-sync-001 (already in HEAD). Edit only `docs/developer_guide/ch03_engineering_conventions.adoc`.

FINDING (verified by the Level-1 operator). The example sentence in section `frontdoor-docs-sync` says that the Agent Skill and the consumer quickstart currently document that token-usage data is not reliably available (`usage_not_reported`). The shipped Agent Skill (`src/workflow_coordinator/agent_skill/wfc/SKILL.md` and `references/*.md`) contains no statement about token usage at all (grep for usage/token finds nothing relevant); only the consumer quickstart (`docs/operator_manual/ch09_consumer_quickstart/01_quickstart.adoc`, limitations list, "No token usage is reported", and the tutorial pages' `usage_not_reported` listings) says so.

DO. Re-read the skill sources and the quickstart yourself, then rewrite only that example so it is true: the consumer quickstart and tutorial listings document that no token usage is reported (`usage_not_reported`); implementing token accounting would make those statements incomplete and must trigger the check, which should also ask whether the Agent Skill (which today says nothing on usage) now needs a statement about it. Keep the rest of the section unchanged, and do not claim anything you have not verified in the files. Check every other factual sentence of the section against the files while you are at it and fix only what is wrong.

BOUNDARIES. Same as before: documentation only, informative, no gate, hook, script or obligation, no other file. Strict asciidoctor render must print nothing. COMMIT MECHANICS (binding): no git command that writes; `commit_intent.paths` is a JSON array with one plain repository-relative path per element; expected exactly one changed file. A Worker never reports any Finding operation.

## Acceptance

The example in section frontdoor-docs-sync is true against the files (skill silent on usage; quickstart and tutorial listings state usage_not_reported), the rest of the section is unchanged except for verified corrections; exactly one file changed; strict asciidoctor render prints nothing; full required suite and quality gates pass; Coordinator task-check passed.

## Withheld authority

No change to product code, tests, workflow or program records, the Agent Skill text, tutorial or Operator Manual content, .wfc/ or governance/; no new gate, hook, script, CI check or automatic change obligation; no Runtime promotion, installation, push, tag, publication or external repository change; no weakening or skipping of tests; no self-acceptance.

## Default escalation

Escalate a requirement that would need a file outside the scope or would add a gate, hook or mandatory obligation, and missing required evidence. Do not silently substitute models. Incomplete work alone is not blocked.

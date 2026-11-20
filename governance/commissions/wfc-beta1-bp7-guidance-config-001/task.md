# Commit the self-development Consumer Config and the repository-policy guidance document

Implement the remaining part of C1 of docs/backlog/beta1_bp7_cutover_plan.adoc (read sections `config-recommendation`, `sequence` C1 and `open-decisions` first). OPERATOR NOTE: by design of the consumer compiler a Commission scope may not overlap the Consumer Config governance directory, so `.wfc/` cannot be Worker scope. The maintainer-owned inputs are therefore already committed in the baseline: `.wfc/config.yaml` and `.wfc/governance/repository-policy.md` (baseline commit the commit that introduced `.wfc/`, see `git log -- .wfc`; the guidance was adapted from existing documented conventions per the maintainer's decision 2; `default_execution` carries the self-development bindings per decision 3). Do NOT change them; the reviewer verifies them as part of the C1 deliverable.

Deliver exactly:
1. A new focused unit test `tests/unit/test_self_development_consumer_config.py`: the committed `.wfc/config.yaml` parses with `consumer_config_from_yaml`; `resolve_evidence(config, repository_root)` resolves to `repository_root.parent / "workflow-coordinator-run-evidence"` with `raw == "commit"` (pure function, no Evidence filesystem access); `default_execution` carries worker = (`claude_cli`, `claude-sonnet-5`, `high`) and independent_reviewer = (`claude_cli`, `claude-opus-5`, `high`); `repository_name`, `governance_dir`, ceiling and hop limit are the committed values; the guidance file exists and is non-empty; the config text contains no absolute path value and no `/home/`. Mutation-sensitive: each assertion fails if the corresponding committed value changes (demonstrate by reasoning or a local scratch mutation that is not committed). If the committed config or guidance is wrong or inconsistent with the parser, escalate or report in the handoff; do not edit `.wfc/`.
2. A short note in docs/operator_manual/ch05_self_development/01_topology_budget.adoc recording that the self-development Consumer Config `.wfc/config.yaml` and its guidance `.wfc/governance/repository-policy.md` exist, that the Evidence repository stays the sibling repository (`raw: commit`) and that the Harness remains the regular self-development path until BP7 retirement. Do not rewrite other manual text.

Hard rules: one new unit test and one manual note only; no product code change, no `src/` edit, no change to scripts/, governance/self-development, `.wfc/`, existing tests or Commission records; no second archive path; no Run state movement. CommitIntent names every new/changed leaf individually, never directories; subject contains WI-WFC-BETA1-BP7-GUIDANCE-CONFIG-001 and the actual Hop ID. No agent Git writes. A Worker NEVER reports ANY Finding operation (`resolve`, `residual` or any other delta): only the independent reviewer opens and resolves Findings. Deliver ONE complete candidate in the first Worker Hop. Run only the new test file with the offline-input arguments the repository documents for a partial run; no full suite from the Worker (the Coordinator task-check is independent).

Reviewer (fresh, adversarial): verify the committed `.wfc/config.yaml` and guidance (baseline inputs, part of the C1 deliverable) and the Worker's test and note; verify the config against the real parser and `resolve_evidence`, that no value is invented or absolute, that the guidance cites existing sources and invents no new standard, that the test is mutation-sensitive, that no product code or other file changed and that the manual note is accurate. Blocking = a violated explicit requirement, a value the parser rejects, a rule in the guidance without an existing source, or an insensitive test. `finding_authority: [resolve]` does not authorize residual transitions: report non-blocking observations only in the review artifact.


## Acceptance

The committed .wfc/config.yaml parses and resolves as specified with the two default_execution bindings; the guidance document exists, is short and source-grounded; the new test passes and is mutation-sensitive; the manual note is accurate; independently accepted with no blocking Findings and Coordinator task-check passed.

## Withheld authority

No product code, existing test, script, governance/self-development, policy or program change, no change to .wfc, no historical Run or Evidence edit, no extra Commission, Runtime promotion, push, merge, rebase or tag.

## Default escalation

Escalate only a real scope/authority conflict or a value the parser cannot express; incomplete work alone is not blocked.

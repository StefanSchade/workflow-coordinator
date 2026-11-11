# Implement FTR02: hand over Commission design and validation guidance

Implement only FTR02 as one complete Worker candidate. Read fully, as read-only governing task references, the series charter docs/backlog/operator-manual-consolidation/FTR-WFC-OPERATOR-MANUAL-CONSOLIDATION.adoc and FTR02 docs/backlog/operator-manual-consolidation/FTR-WFC-OPERATOR-MANUAL-02-COMMISSIONING.adoc at accepted planning commit e01a4084595ff0923f4a6dbebf056e76267f2d1c, and the accepted FTR01 implementation and its final review at commit 2041918eeae959ed8d1b348bd5f40af3ab4a5aa1. Follow FTR02 and all applicable charter allocation, quality, metadata, invariants, shared-validation and review rules. These references are read-only; inspect with git show. Read current OM and relevant help, code and tests as FTR02 directs.

Create exactly docs/operator_manual/ch02_commissioning/_chapter.adoc, 01_authority.adoc, 02_authoring.adoc and 03_review.adoc in that directory. Modify exactly docs/wfc_operator_manual.adoc and docs/operator_manual/operator_manual.adoc. The separate review-artifact leaves belong only to review phases. Keep all ch01 files byte-identical.

Transfer OM section 4 and all H3s, section 6 including Direct-v1 quickstart, and section 12 according to FTR02 and charter: section 4 input/scope and mechanism admission to 01_authority, section 6 and 12 to 02_authoring, section 4 review breadth to 03_review, without duplicating child content in the parent. Preserve warnings, examples, source-pin semantics and render/check order. Replace assigned old bodies with pointers retaining all original headings and rendered anchors. Add ch02 to entry and route commissioning there; entry includes only existing ch01 and ch02. Keep OM section 22 for trusted acceptance, run-preparation and self-development links as existing current owners; no ch03/ch04 or future-only target. Preserve all unassigned OM bodies and all withheld files.

Preserve directory-shaped authorized scope versus leaf-exact CommitIntent, human mechanism-admission heuristic versus actual loader rules, budget ceiling versus expectation, and distinct render --check versus commission check admission; do not claim rendering authorizes or launches. Inspect scripts/wfc_op.py cmd_commission_render/cmd_commission_check, commissioning.py and tests/unit/test_commissioning.py, compare top-level and relevant help only; do not execute a render into governance or perform operational commands. Record exact duplicate merges and all actual checks and limits in the result.

Apply charter shared validation: predecessor snapshots of headings, anchors and substance, compare each assigned body and warnings/examples, unassigned bodies and withheld files byte-for-byte; render entry and all existing wrappers standalone and assembled and changed/new sections standalone with metadata and heading-level checks; resolve includes and relative links/xrefs including rendered fragments, duplicate IDs and inbound legacy anchors across repository text; inspect relevant help/code; check pointer bodies, no later target, exact dirty leaves, no deletes or renames, and committed-range diff hygiene. Scratch and generated output outside repository. Do not download dependencies, start a Run, invoke providers or issue operational mutations. Local checks do not claim Coordinator evidence or reviewer acceptance.

CommitIntent lists each actual changed normalized repository-relative leaf exactly, no directories or unchanged files; compare the actual dirty set. Subject is one Conventional Commit line with WI-WFC-OPERATOR-MANUAL-COMMISSIONING-001 and actual Hop ID as separate bounded identity tokens. No Git writes by Worker. Incomplete work alone is not blocked; blocked carries no CommitIntent.

Independent reviewer: independently check all FTR02 and charter acceptance criteria, substantive transfer, source pins and authority explanations, preserved ch01 and other unassigned content, old anchors, partial routing, links, rendering, exact scope and committed-range diff hygiene. Only its declared review artifact may change; append rounds for repeated review and propose a leaf-exact CommitIntent with the actual identities. Open blocking Findings for material defects or failed mandatory checks, not editorial preferences. Use robust correction, focused re-review and renewed fresh broad review; final acceptance requires a fresh broad review with no open blocking Findings. Worker and reviewer use the same model in separate fresh invocations.

## Acceptance

1. Exactly the four specified new leaves and two specified modified leaves, no deletions or renames; ch01 remains byte-identical.
2. Every assigned OM section 4, 6 and 12 H2/H3 body appears once in its specified destination with warnings and examples preserved; old bodies are pointers and old anchors resolve.
3. Renderer/check commands, options and source-pin claims match current help and code; rendering alone neither admits nor launches a Run. Preserve scope/CommitIntent, mechanism admission and budget distinctions.
4. Entry includes only existing ch01 and ch02; trusted-evidence ordering reaches existing OM section 22 and preparation/self-development reach existing sources. No dangling or future-only target.
5. All applicable FTR02 and charter shared validation pass: renders, metadata, includes, links/xrefs and fragments, assigned-heading coverage, untouched bodies/files, stubs, exact dirty leaves and committed-range diff hygiene; report actual checks and limitations.
6. Only declared scope changes; Coordinator trusted task-check passes and fresh independent broad review accepts without open blocking Findings.

## Withheld authority

No FTR03 or later, ch01 changes, other implementation leaves, product semantics changes, deletion or rename, source code, scripts, tests, governance, backlog FTRs, project memory, historical records, Runtime or Evidence mutations, agent Git writes, provider calls outside WFC, dependency downloads, push, Runtime promotion, successor or additional Commission. Review artifacts are reserved for their configured reviewer phases.

## Default escalation

Escalate only for a concrete obstacle requiring authority or unresolved normative contradiction not settled by source and code. Incomplete work alone is not a blocker; continue authorized work within the invocation and truthfully report actual operational limits.

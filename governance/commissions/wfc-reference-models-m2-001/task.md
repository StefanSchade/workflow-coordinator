# Modularize Technical Model and add Component and Runtime Views (M2)

Implement only M2 in docs/backlog/FTR-WFC-REFERENCE-MODELS-MODULARIZATION.adoc, especially its Technical Model layout, component-view, runtime-view, M2 work package and applicable acceptance criteria. M1 prerequisite is established: RUN-b7f70ca2be104a1fa62946f544631c3c completed, correction/focused and renewed broad review accepted with no blocking findings at c3d3cbb, Coordinator task-check passed, and Evidence archive commit 378f361. Reuse M1's verified method, but capture the actual current commissioned baseline; planning snapshot counts and old paths are not authoritative.

Before editing render all baseline model entries, inventory current inbound paths and fragments, record HTML IDs and heading sequence. Modularize the Technical Model with stable original entry path, sections, headings, anchors and ordering. Move the two identified seed subsections into the new views, keeping one home and their headings/anchors. Retarget intra-model references for assembled and standalone rendering, preserve working-draft/current standing and visibly resolve the routing-layers standing ambiguity by verifying against actual source. Preserve the SEC-03 Coordinator-only Git contract and source changes already landed in Development. Clearly distinguish Development implementation from separately promoted execution Runtime where relevant; do not imply promotion.

Create current, source-verified Component and Runtime Views per the FTR: complete actual module and relevant script inventory with responsibilities, non-ownership, effects and entry points; dependency direction from real imports with prompt/adapter and operator/adapter exceptions; actors/stores/trust boundaries; Run-level and Hop-level interactions and the specified success, failure and evidence-routing variants, tracing behavior to existing tests when available. Link Operator Manual procedures rather than copying commands or contracts. Record verification methods, exact intentional edits and claim sources; no speculative target machinery described as implemented. Source and tests remain behavior authority.

Check entry and standalone chapter/section renders via asciidoctor --failure-level WARN; compare current baseline and candidate IDs, headings and normalized rendered text for unchanged sections, document moved/new material and new IDs; verify all inbound and new local links/fragments, stable IDs, draft and current authority labels, one-home seeds, full inventory and import graph, and diff hygiene. Run appropriate validation; Coordinator task-check is separate. Update only the controlling FTR with the evidenced M1 completion and a truthful M2 candidate milestone and checks; M2 independent review and Coordinator evidence are pending at implementation. No change to Functional or Semantic model, Python source, tests, Makefile, governance, historical documents, knowledge catalog or provenance pilot. If a substantive contract conflict or missing authority prevents the task, stop with a specific maintainer question rather than guessing or expanding scope.

CommitIntent lists actual changed leaves exactly once; Conventional Commit subject includes WI-WFC-REFERENCE-MODELS-M2-001 and actual Hop ID as separate bounded tokens. Agents do not write Git commits. Review artifacts belong to review phases only.

Fresh independent reviewer compares baseline and candidate rendered output, IDs, headings, links, labels and single-home moved seeds; independently verifies full inventory and actual import direction, and at least fifteen self-selected claims in current source/tests including prompt/adapter, operator imports, SEC-03 observation and Coordinator-only commit and Run/Hop evidence boundaries. Inspect all changes and FTR milestones independently. Report concrete blocking defects; normal robust correction and focused/renewed broad review routes apply.

## Acceptance

Technical Model modularization preserves baseline text, stable links, IDs and standing while adding accurate independently source-verified Component and Runtime Views; fresh independent broad review has no blocking findings and Coordinator task-check passes.

## Withheld authority

No Functional or Semantic changes, source, test, Makefile, governance, historical record, knowledge catalog or provenance work; no invented product mechanics, agent Git writes, Runtime promotion or push.

## Default escalation

Stop for a concrete substantive contract conflict or missing authority; do not broaden scope or substitute models.

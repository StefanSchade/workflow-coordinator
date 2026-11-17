# Modularize Functional and Semantic reference models (M1)

Implement only M1 of docs/backlog/FTR-WFC-REFERENCE-MODELS-MODULARIZATION.adoc, including its M1 work package, proposed layouts, model standing, and applicable acceptance criteria. The current commissioned baseline is the source of truth, not the planning snapshot. Preserve entry paths, all headings, section order, anchors, IDs, GOAL and UC identifiers and existing content and authority. No Technical Model conversion or new views here; M2 is separate.

Before editing regenerate current inbound references and fragment inventory, render all three baseline model entries from the pinned baseline and record IDs and heading order; verify include/leveloffset structure on one chapter in assembled and standalone render before bulk work. Follow the FTR's guarded includes, no added chapter headings that relevel current sections, and reasonable file grouping. Convert Functional and Semantic Models with existing text preserved apart from needed heading-marker depth and retargeting cross-file references. Resolve the specified Semantic standing ambiguity by placing three-verifications and current-boundaries with current content and aligning the standing sentence; preserve draft labels visibly both assembled and standalone. Preserve existing entry links including historical governance, use relative links correctly, do not silently change contracts. If a substantive conflict is discovered stop with a specific maintainer decision.

Check assembled and standalone AsciiDoc with asciidoctor --failure-level WARN, headings and HTML ID sets against baseline, normalized rendered section-text equivalence, identifiers and ordering, actual inbound links and fragments, new relative links and visible standing labels; explicitly list intentional differences and check diff hygiene. Use temporary artifacts outside tracked files. Run appropriate focused validation; Coordinator task-check runs separately. Update only the controlling FTR with a factual M1 candidate milestone and checks, explicitly marking independent review and Coordinator evidence pending. Do not assert later acceptance. No Python, tests, Makefile, governance, Technical Model, historical records, knowledge catalog or provenance pilot changes.

CommitIntent lists actual changed leaves exactly once; subject is Conventional Commit with WI-WFC-REFERENCE-MODELS-M1-001 and the actual Hop ID as separate bounded tokens. No agent Git writes. Review artifacts are for review phases only.

Fresh independent reviewer independently render and compare candidate against the actual baseline, verify IDs, normalized section content, headings, inbound and relative links, section/chapter standalone output and authority labels, inspect exact scope and milestone truth, and challenge the mechanical conversion, not merely worker claims. Report concrete blocking findings only; ordinary robust correction and focused/renewed broad review routes apply.

## Acceptance

M1 preserves current Functional and Semantic content, standing, paths, IDs and links in independently verifiable assembled and standalone modular renders, with the expressly scoped standing clarification; fresh independent review has no blocking findings and Coordinator task-check passes. M2 is a separate next Commission.

## Withheld authority

No Technical Model conversion or new views, runtime/source/test/Makefile/governance changes, historical document edits, knowledge catalog or provenance pilot, new product behavior, agent Git writes, Runtime promotion or push.

## Default escalation

Stop for a concrete substantive contract conflict or authority gap; do not expand into new architecture or substitute models.

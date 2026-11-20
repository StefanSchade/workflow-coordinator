# workflow-coordinator repository policy (guidance for Workers and reviewers)

This guidance restates practice that already exists in this repository. It adds
no new standard; each rule names its source. If a source and this file differ,
the source governs.

## Changes and tests

- Keep the change proportionate and locally understandable; names, errors and
  nearby comments state current behavior, rationale is linked to its owner, not
  copied. (`docs/developer_guide/ch03_engineering_conventions.adoc`)
- Trace the contracts the change affects and add tests that can detect a
  meaningful regression: a behavior change needs a test that fails without it;
  a shared result or persistence seam needs tests on both sides. Never weaken or
  delete a test to make a change pass. (same source)
- Check producer/consumer handoffs (identity, shape, ordering, ownership) across
  the seam. (same source)
- Keep decisions in the deterministic core; filesystem, Git, provider and
  evidence effects stay at explicit boundaries; provider output and diagnostic
  text are never trusted. (same source)

## Scope, authority and Git

- Edit only files inside the WorkItem scope; report a needed change outside it
  instead of making it. (`docs/backlog/README.adoc`, "Upkeep at actual milestones")
- Agents do not write to Git. The Worker reports a CommitIntent naming every
  changed leaf; the Coordinator commits. (Operator Manual, ch02 and ch03)
- Do not invent commit IDs, acceptance or closure; independent acceptance and
  product closure are recorded by the maintainer. (`docs/backlog/README.adoc`)

## Review and findings

- Only an independent reviewer opens or resolves Findings; a Worker never
  reports a Finding operation. Non-blocking observations go in the review
  artifact. (Operator Manual, ch02 and ch06; the reducer's Finding authority)
- A review checks the whole product against its baseline, not only the last
  correction.

## Documentation and artifacts

- Maintain the owning documentation when behavior changes and follow
  `docs/ai_info/doc_conventions.adoc` for placement and standing.
- Committed artifacts contain no secrets and no absolute home-directory paths.
- Historical material under `docs/cold_storage/` is provenance, not a source of
  current requirements. (`CLAUDE.md`)

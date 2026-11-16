# Plan modular reference models, a derived document catalog and operational-provenance retrieval

Produce preferably two, at most three, coherent implementation-ready FTRs in the WFC backlog for an economical first
phase of documentation and knowledge-management improvement. This is a planning task: write the FTRs; do not
implement them. Current Git and repository files are authoritative. Inspect them selectively and sample, rather than
performing exhaustive archaeology. The recent Claude public-internet and permission-qualification work is unrelated:
do not modify or reopen it, and do not reopen or rewrite the completed documentation-curation packages C1-C4
recorded in docs/backlog/FTR-WFC-DOCUMENTATION-CURATION.adoc.

Read first: docs/ai_info/_ai_info.adoc, docs/README.adoc, docs/ai_info/doc_conventions.adoc,
docs/backlog/README.adoc and _open.adoc, docs/backlog/FTR-WFC-DOCUMENTATION-CURATION.adoc (its accepted C1-C4
outcomes and the metadata decision), docs/cold_storage/README.adoc, project_memory/README.adoc and docs/project_memory.adoc,
docs/research/context_sculpting_and_knowledge_lifecycle.adoc, the modular Operator Manual (docs/operator_manual/
entry, chapter directories, _chapter.adoc assemblies) as the structural precedent, and the three current reference
models docs/functional_model/, docs/semantic_model/ and docs/technical_model/. Existing planning Commission
governance/commissions/wfc-documentation-curation-planning-001/ is the shape precedent, not a template to copy.
For theme C sample real examples: a few Commission directories and commit messages carrying WorkItem/Hop identifiers,
several review documents under docs/warm_storage/concepts/ and docs/cold_storage/, and the sibling Run-evidence
repository's existing reviews/findings Project Memory subset, read-only. Do not write outside this repository.

A. Modular reference models. The three models are monolithic. Plan converting each to modular AsciiDoc following the
Operator Manual pattern: a main entry document, chapter directories, _chapter.adoc assembly files, coherent section
files, and standalone plus assembled rendering with the documented :assembled: / leveloffset arrangement. Keep the
Functional, Semantic and Technical Models distinct. Technical Model chapter 1 must contain an architectural
Component View (static responsibilities) and a Runtime View (dynamic interactions); say which current material
seeds each and what must be newly written, verified against source. Preserve existing meaning, anchors, section
numbers, GOAL-WFC IDs, inbound cross-references, current/draft/proposal labelling and authority distinctions. This is
information architecture, not a redesign of WFC. Avoid gratuitous fragmentation: decide per model where sections
genuinely warrant files, and say what stays whole. Inventory inbound links and anchors that a conversion must keep
working, and define how they are verified.

B. Knowledge organization and retrieval. Project Memory is curated, useful memory of the project's evolution, not a
complete audit trail. Concerns, Concern Events and Decisions provide strategic condensation; the missing capability
is access to operational knowledge and its supporting documents. The intended architecture is: current documentation
(distilled present knowledge); Warm Storage (active investigations, alternatives, developing concepts); Cold Storage
(inactive long-term memory: valid earlier knowledge, rationale, rejected alternatives, historical reviews, parked
ideas -- not a graveyard, and its size is a retrieval problem, not a reason for deletion); Project Memory (curated
strategic and operational learnings); and a derived retrieval/index layer. Most of this is already documented:
consolidate or clarify only a concrete gap you can name; do not plan another broad rewrite. Plan a modest first
mechanically generated document catalog, reproducible from repository truth with no manually maintained duplicate
metadata. Evaluate candidate inputs: paths and AsciiDoc titles, a small consistent header for suitable new documents,
existing IDs/dates/types, outgoing references, reliable Git information. The current conventions deliberately
decided against mandatory uniform metadata and a parallel classification index. Reconsider that decision explicitly
for this retrieval use case, state what the new use case changes and what it does not, and either keep, narrow or
revise it with reasoning and the affected convention text named; do not reverse it silently. No bulk migration of
historical documents. Decide where the generated catalog lives, what is committed versus regenerated, how staleness
is detected, and how it is exposed to the router/Project Memory without becoming a second authority or status index.

C. Operational provenance and review findings (later extension). Investigate connecting Git commits, Commission and
WorkItem identities, Runs and Hops, review documents, and Findings with their later interpretation. Determine what
is mechanically extractable from commit messages, Commission artifacts and Run artifacts, and what needs human
judgment. A useful target is evidence-qualified curated disposition of historical review findings: demonstrably
addressed; superseded by later development; not substantiated; consciously not pursued with rationale; still
relevant; unknown disposition. This is neither a complete finding audit trail nor automatic finding closure.
Historical findings are not proven current deficiencies merely because their review documents remain, and later
refactoring does not by itself prove resolution. Original review evidence stays untouched; curated dispositions must
not alter authoritative WFC Run/Review state; they must not require Coordinator semantics changes. Full lifecycle
extraction, knowledge graphs, embeddings and RAG are possible later developments, not first-phase commitments.
Mechanical commit-message construction inside WFC is a separate runtime feature and out of scope. Make the third
FTR only if it is sufficiently concrete and independently valuable; otherwise fold a bounded pilot or its deferral
rationale into the knowledge FTR and say so.

Each FTR must give: problem and practical benefit; existing architecture and sources; in-scope and out-of-scope work;
implementation boundaries and likely work packages; dependencies and sequencing; concrete acceptance criteria; risks
(source authority, metadata drift, knowledge loss, misleading historical findings); mechanical versus human-judgment
work; expected effort and an economical Commission strategy. Prefer a few substantial implementation Commissions
over one per document, chapter or field; roughly ten Hops across planning and implementation is the desired order
of magnitude. Include one short cross-FTR note on sequencing and the total proposed Commission/Hop budget. Do not
plan speculative extensibility frameworks, vector infrastructure, new WFC runtime mechanisms or heavy governance.
Reuse existing conventions and Project Memory structures. Make unresolved Maintainer-owned decisions explicit and
few. Existing FTR documents in docs/backlog/ show the local record style (Type/Status/Authority-boundary header);
follow it. State that the FTRs authorize no execution.

Backlog ownership: docs/backlog/_open.adoc is the sole current priority overview. Add the new FTRs there as a short
clearly labelled documentation/knowledge item with evidence-qualified status (planned, not implemented, not
accepted) and an explicit statement that they are not Beta-1 release blockers; do not restructure _open.adoc, and do
not touch the curation FTR or other backlog records.

Render the new AsciiDoc and check relative links/fragments and diff hygiene; report diagnostic limitations. No Python
or tests change, so do not run the full test suite; the Coordinator task-check is independent and follows
publication. CommitIntent must list every actual changed repository-relative leaf exactly once and carry a Conventional
Commit subject with WI-WFC-DOCUMENTATION-MEMORY-PLANNING-001 and the actual Hop ID as separately bounded tokens;
agents do not write Git commits. Review artifacts are reserved for review phases.

The fresh independent broad reviewer must inspect the entire candidate against actual repository contents, not the
Worker summary, and write the configured durable artifact. Check: the FTRs are grounded in real current sources and
anchors (spot-check claimed paths, anchors and facts); modular-model plan preserves anchors/meaning/authority and avoids
fragmentation; the Technical Model Component/Runtime View is concrete; Cold Storage is treated as inactive long-term
memory, not discardable; the metadata-decision reconsideration is explicit and reasoned; the catalog is reproducible
without duplicate manual metadata; theme-C dispositions are evidence-qualified, never auto-closing, and keep Run/Review
authority intact; acceptance criteria are concrete; Commission/Hop budget is economical and proportionate; backlog
ownership and the non-blocker status are unambiguous; only genuinely Maintainer-owned questions remain open. Identify
concrete blocking Findings and route correction normally; do not spend Hops polishing adequate prose. Focused review
handles corrections, followed by renewed broad review as configured.

## Acceptance

Two or three source-grounded, implementation-ready FTRs with explicit scope, sequencing, acceptance, risks and an economical Commission strategy, covering modular reference models, a derived document catalog pilot with explicit reconsideration of the metadata decision, and the operational-provenance/review-finding retrieval extension (as an FTR or explicitly bounded part of one); _open.adoc navigation stating they are non-Beta-blocking; independently accepted broad review without open blocking Findings and Coordinator trusted task-check; no implementation.

## Withheld authority

No implementation, restructuring, file moves or bulk document edits; no change to docs/ai_info conventions, the models, the Operator Manual, cold or warm storage content, Project Memory canonical data, the curation FTR or C1-C4 records; no Python, tests, scripts, Makefile, Runtime or Workflow Programs, canonical Run evidence, Project History or semantic Run Ledger; no catalog generation, metadata migration, embeddings, RAG or MCP; no WFC runtime change including commit-message construction; no successor or extra Commission, branch merging, push or Runtime promotion; no agent Git writes or outside-WFC provider calls. Review artifacts reserved to review phases.

## Default escalation

Escalate only for a concrete obstacle requiring authority or a genuinely Maintainer-owned decision; incomplete work alone is not blocked.

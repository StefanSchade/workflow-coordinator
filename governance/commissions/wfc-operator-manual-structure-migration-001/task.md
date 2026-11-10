# Establish the modular Operator Manual structure and migrate the existing operator documentation into it

## Objective

Create `docs/operator_manual/` and move the content of the existing
operator-facing documentation into it as ONE modular manual, without losing
operational information. This Commission is structure, navigation and
migration only. Do NOT write new failure-recovery chapters, new
failure-mode analysis, or a new end-to-end walkthrough: those belong to a
later Commission (P2) and must not be started here. Existing recovery,
troubleshooting and worked-example material IS migrated, in its current
substance, to its new home.

This is a documentation task. Doing the work is the default: read the
sources, create the files, and report `completed` with a commit. Work that
you could not finish is incomplete work, not a blocker (see "Result and
commit rules").

## Target structure

```text
docs/operator_manual/
  operator_manual.adoc          assembles chapter wrappers
  ch01_<name>/
    _chapter.adoc               assembles the section files
    01_<name>.adoc
    02_<name>.adoc
  ch02_<name>/ ...
  migration_map.adoc            transitional: old section -> new location
```

Model: the Developer Guide of the sibling Core repository
(`../core/docs/developer_guide/developer_guide.adoc` and any
`ch*/_chapter.adoc`; read-only; `include::...[leveloffset=+1]`). Conventions:
`docs/ai_info/doc_conventions.adoc` (header metadata, chapter mechanics).
If the Core files cannot be read, say so and follow doc_conventions.

Starting outline (adjust from the actual content; do not copy the old
22-section numbering): orientation (purpose, mental model, repositories,
trust boundaries, identity); commissioning (inputs and ownership, authoring
and rendering, source pins, preflight reading, review breadth, Hop budget,
authoring anti-patterns); running a Run (prepare, launch, status, terminal
states); evidence and archive; recovery (stopped Runs, salvage, successor
Runs, the successor-continuation runbook); self-development (topology,
ownership, Runtime promotion); troubleshooting (the existing entries);
reference (quick-reference flow, what Harness v0 does not do, relation to
higher orchestration, the existing worked example kept as-is). Chapters of
2-8 sections each; ASCII lowercase file names, no spaces.

## Sources (live files)

`docs/wfc_operator_manual.adoc` (about 1360 lines), `docs/operator_guide/`
(three files), `docs/run-preparation.adoc`, `docs/self-development.adoc`,
`docs/run-recovery-and-salvage.adoc`,
`docs/successor_continuation_runbook_2026-10-10.adoc`. Orientation only
(do not migrate): `docs/README.adoc`, `docs/ai_info/`, and the inventories
in `docs/warm_storage/concepts/wfc_documentation_consolidation_plan.adoc`
(reuse them; no new planning).

## Deliverables

1. The manual tree above. One canonical home per operational fact: MOVE
   content, do not copy it; where several sources describe the same
   procedure keep the most complete explanation once and have other
   places link to it. Preserve every requirement, warning, example and
   failure-handling procedure. Keep the meaning of normative contracts
   unchanged. Edit for flow and consistency where needed (prose, headings,
   cross-references, duplicate removal), but do not add new operational
   claims. If you find a statement that contradicts the code in
   `src/` or `scripts/wfc_op.py`, fix it only when the code is
   unambiguous, and list it in the migration map under "Corrections".
2. `migration_map.adoc`: a table listing every H2/H3 heading of each source
   document with its new location (`file#anchor`) or an explicit
   "merged into ..." / "dropped as duplicate of ..." disposition. No source
   heading may be unmapped. Label the file transitional (to be retired after
   the follow-up Commission) and keep it the last item in the assembly.
3. Pointer stubs: replace each of these files IN PLACE with a stub of at
   most 15 lines (header metadata, one sentence, link to the new canonical
   location): `docs/wfc_operator_manual.adoc`, `docs/run-preparation.adoc`,
   `docs/self-development.adoc`, `docs/run-recovery-and-salvage.adoc`,
   `docs/successor_continuation_runbook_2026-10-10.adoc`,
   `docs/operator_guide/operator_guide.adoc`,
   `docs/operator_guide/ch02_run_preparation.adoc`,
   `docs/operator_guide/ch08_self_development.adoc`. Scripts, tests and
   immutable governance cite these paths, so they must remain. Do NOT delete
   or `git mv` any tracked file: commit admission compares Git's staged-path
   report with your CommitIntent and Git rename detection reports a deleted
   file plus a similar new file only under the new name, which would fail
   admission. In-place modification does not have this problem.
4. Navigation: update `docs/README.adoc` (index entries and moved-path map),
   `docs/ai_info/_ai_info.adoc` (the operator row and its links), and
   `docs/project_memory.adoc` links; update the status section of
   `docs/warm_storage/concepts/wfc_documentation_consolidation_plan.adoc`
   (what P1 did, that P2 remains); append one concise dated entry to
   `docs/cold_storage/project_history/README.adoc` (append only).
5. Leave docs/backlog, cold-storage records, project-memory JSON,
   `src/`, `scripts/`, `tests/`, `governance/`, and the Runtime and Evidence
   repositories untouched; they keep working through the stubs.

## Validation you perform (report results in your summary)

- `asciidoctor` (available at /usr/bin/asciidoctor): render
  `operator_manual.adoc` and each `_chapter.adoc` standalone with
  `-o /dev/null` (or a temp dir outside the repository), report warnings or
  errors, and fix any that you caused. Write no build output into the repo.
- Every `include::` target exists and every `link:`/`xref:` you wrote or
  touched resolves (a short throwaway script outside the repository is fine).
- Heading coverage: every source heading appears in `migration_map.adoc`.
- Commands and options you migrated or edited match `scripts/wfc_op.py
  --help` output or the source.
- Focused tests are optional; the Coordinator runs `python -m pytest -q`
  itself after your commit, so you need not prove the full suite green
  before reporting `completed`. If a tool is missing or blocked by the
  sandbox, state it and continue.

## Result and commit rules (read exactly)

- Report `status: completed` when you have a coherent committed result.
  If time or size prevents finishing everything, still report `completed`
  for the coherent portion (a valid assembling manual with whatever is
  migrated, stubs only for sources already fully migrated) and list the
  unfinished sources in `summary`; later correction Hops continue it.
  Report `blocked` or escalate only if this Hop genuinely lacks authority,
  scope or a capability it needs; `blocked` carries no CommitIntent, so
  uncommitted work would be stranded. Not having finished is not a reason
  to stop.
- Never run Git write commands (`add`, `commit`, `mv`, `rm`, `stash`,
  `reset`); the Coordinator owns the commit. Read-only git is fine.
- Provide `commit_intent.subject`: one line, Conventional Commit syntax,
  containing the exact WorkItem ID `WI-WFC-OPERATOR-MANUAL-STRUCTURE-MIGRATION-001`
  and the actual current Hop ID from the invocation context as separate
  tokens, for example
  `docs(operator-manual): WI-WFC-OPERATOR-MANUAL-STRUCTURE-MIGRATION-001 H001 migrate operator documentation`
  (use the real Hop ID in later Hops; confirm it from the invocation
  context).
- `commit_intent.paths`: every changed leaf file as an exact, normalized,
  repository-relative path (forward slashes, no `./`, no duplicates), each
  new and modified file individually; never a directory. The set must equal
  the set of files whose worktree state differs from HEAD (new untracked
  files count). Leave no stray or scratch files in the repository.

## Independent reviewer

Review the actual files for technical correctness and information
architecture, and check the acceptance criteria one by one: assembly and
standalone rendering, include/link resolution, migration-map completeness
(spot-check a meaningful sample of source headings against the migrated
content for lost warnings, examples and procedures), one canonical home per
fact, stub correctness and size, scope discipline, and that no new
failure-recovery chapter or walkthrough was introduced. Also judge whether
someone with no WFC history can navigate the manual. Open a blocking
Finding only for a material defect (lost or altered operational content,
broken assembly or links, a competing full source, out-of-scope change,
missing map coverage); editorial preferences are nonblocking and must not
cause correction cycles. Do not make acceptance depend on tests, tools or
evidence outside your authority, or on provider/CLI diagnostics unrelated to
documentation correctness; state what you could not check.
Follow the ordinary Robust correction, focused re-review and renewed
broad-review mechanics; final acceptance must come from a fresh broad review.

Each review Hop must create or update ONLY its own declared durable review
artifact (see `durable_artifacts`) with a section for that round -- on a
repeated review append a new dated round section so the file really
changes -- and propose a CommitIntent whose `subject` follows the rule above
with the actual Hop ID and whose `paths` is exactly that one leaf file. Do
not modify the manual or any other file.


## Acceptance

1. `docs/operator_manual/operator_manual.adoc` assembles `ch*/_chapter.adoc`
   wrappers that assemble section files; the entry document and every
   chapter render standalone with asciidoctor without include errors.
2. Every include target and every touched link resolves.
3. `migration_map.adoc` maps every H2/H3 heading of the migrated source
   documents to a new location or an explicit merge/duplicate disposition;
   none unmapped; any code-verified corrections are listed.
4. Operational content (requirements, warnings, examples, failure-handling
   procedures) of the sources is preserved; each fact has one canonical
   home; no second full manual remains.
5. The eight listed files are pointer stubs of at most 15 lines linking to
   the new canonical location; no tracked file deleted or renamed.
6. `docs/README.adoc`, `docs/ai_info/_ai_info.adoc`, `docs/project_memory.adoc`,
   the consolidation plan status and the project-history entry are updated.
7. No new failure-recovery chapter, failure-mode analysis or new end-to-end
   walkthrough was added.
8. All changes are inside the declared scope; the Coordinator's `task-check`
   (`python -m pytest -q`) passes; the independent broad review accepts with
   no blocking Finding open.


## Withheld authority

No new failure-recovery chapters, new failure-mode analysis or new
end-to-end walkthrough (reserved for a later Commission). No change to
`src/`, `scripts/`, `tests/`, `governance/`, the Runtime or Evidence
repositories, immutable workflow programmes, historical Commissions or
archives (including wfc-operator-manual-consolidation-001 and its Run),
backlog or other historical records (apart from the single appended
project-history entry), or repository policy. No change to the semantics of
a normative contract. No new build framework. No Git write commands by an
agent. No Runtime promotion, push, nested provider invocation outside WFC,
or successor Run.


## Default escalation

Escalate only for a genuine authority problem: a required change outside
the declared scope, a normative contract whose meaning would have to change,
or two authoritative sources that contradict each other where the code
cannot decide (then record the contradiction in the migration map and keep
the code-verified statement). Incomplete work, a missing local tool, a
sandbox-blocked diagnostic or the size of the task are not escalation
reasons: report `completed` with the coherent portion and list what remains.


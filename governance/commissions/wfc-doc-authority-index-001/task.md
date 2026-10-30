# Complete the warm_storage classification in the WFC documentation authority index

docs/README.adoc already indexes docs/wfc_operator_manual.adoc
correctly as current operational authority. Leave that entry,
and every other already-correct entry in the index, unchanged.

The genuine gap is docs/warm_storage/. The index currently
names only one warm_storage file --
warm_storage/concepts/workflow_program_boundary_working_brief_2026-10-26.adoc
-- as DRAFT / NON-AUTHORIZING discussion provenance. The rest
of docs/warm_storage/ (the remaining concepts/ design and
decision records, the concepts/ review artifacts
(*_review*.adoc), the incidents/ record, and the runbooks/
record) is not classified at all.

Extend the index so docs/warm_storage/ as a whole is
classified, distinguishing within it:

- active planning and design evidence (concept/design records
  still under live discussion);
- review evidence (review artifacts produced against those
  records);
- historical provenance (superseded or closed working
  material).

Do not claim any current normative or operational authority
for warm_storage/ material: indexing it must not grant it
execution authority. Existing accepted decisions and
governing inputs (architecture.md, the active-work manifest,
accepted FTRs, and the self-development / run-preparation /
run-recovery contracts) retain their own authority; the index
must not silently override or reinterpret them.

Reuse the existing index's structure and conventions -- the
existing cold_storage/ classification is the closest existing
parallel. Do not reorganize, migrate, or rewrite any existing
document, and do not introduce a new authority system. Do not
invent documentation that does not already exist, and do not
expand classification beyond docs/warm_storage/ and the
already-correct entries that must be preserved.


## Acceptance

docs/README.adoc classifies docs/warm_storage/ as a whole,
distinguishing active planning/design evidence, review
evidence, and historical provenance, consistent with the
existing index's conventions (e.g. the cold_storage/
treatment). No warm_storage document is granted execution
authority by virtue of being indexed, and no existing accepted
decision or governing input is overridden or reinterpreted.
The existing docs/wfc_operator_manual.adoc entry and every
other already-correct entry are preserved unchanged. Links and
AsciiDoc formatting remain valid. No file other than
docs/README.adoc has its substantive content changed.


## Withheld authority

No substantive change outside docs/README.adoc. No change to
the existing docs/wfc_operator_manual.adoc entry or any other
already-correct entry. No reorganization, migration, or
rewrite of any existing document. No new authority system. No
production code, test, governance, or Runtime changes. No Run
launch, provider invocation, or push.


## Default escalation

Stop and request maintainer authority if the warm_storage
classification cannot be completed accurately within
docs/README.adoc alone, or if completing it would require
reinterpreting an existing accepted decision or governing
input.


# Generalize the archive-equivalence comparison to additional retained Runs

Implement C1b of docs/backlog/beta1_bp7_cutover_plan.adoc (read sections `archive-equivalence`, `sequence` C1b and `effort-reasoning` first, then tests/unit/test_archive_equivalence.py, tests/fixtures/bp5/PROVENANCE.txt and src/workflow_coordinator/frontdoor/archive.py).

Goal: the existing BP5 equivalence test runs the frozen, hash-pinned legacy archiver (`tests/fixtures/bp5/legacy/`) and `frontdoor.archive.archive_run` end to end, each against its own disposable copy of one hard-coded retained Run, and asserts the two `manifest.json` files equal once `archived_at` is removed. Generalize it so the same comparison runs over a list of independently committed, hash-pinned fixture Runs, not only the BP5 one, WITHOUT changing the method or the comparison semantics and without changing `archive.py`, `scripts/wfc_op.py` or `tests/fixtures/bp5/`.

Deliver:
1. In tests/unit/test_archive_equivalence.py: parameterize the real-Run equivalence test over fixture descriptors (fixture directory, attempt id, expected provenance digests). The existing BP5 case must keep passing with identical assertions (regression); fixture-specific constants (attempt id, relocation) become descriptor data. Keep the digest self-checks (`setUp`) for every fixture and the legacy archiver. Add a mutation-sensitive check that a tampered fixture byte or a manifest difference makes the comparison fail (a test that proves the comparison can fail).
2. One second, independent fixture copy under tests/fixtures/bp7/ built the same way the BP5 copy was built (see tests/fixtures/bp5/PROVENANCE.txt): verbatim copies of an already archived Run, reconstructing its state-root and OPS layout from the archived copy. Use the archived Attempt `wfc-beta1-bp7-guidance-config-001-20261120-01` (completed, 2 Hops) at ../workflow-coordinator-run-evidence/archive/unclassified/wfc-beta1-bp7-guidance-config-001-20261120-01/{run,ops}/ (Evidence repository commit 3d5092256d09ad09005f27f9ebf365617c9a8e16; read-only, never write there). Write tests/fixtures/bp7/PROVENANCE.txt with the Evidence HEAD, the Attempt and Run ids, the SHA-256 of every copied file matching the archived manifest.json copied_files digests, and the relocation rule. Apply the BP5 collision rules (files that collide with other tests' globs, such as run.json/operator-plan.json/operator-summary.json, are handled exactly as the BP5 provenance describes; do not create rename-shaped delete+add changes). Fixture bytes must not contain secrets; if a file has to be omitted or the archived layout cannot be reconstructed byte-for-byte, record exactly why and escalate rather than inventing content.
3. A short developer note (in docs/developer_guide, in the section that already describes the equivalence test if one exists, otherwise a short new paragraph in the most fitting chapter) on how to add a proof Run's fixture and run the comparison, stating that the comparison reads only committed disposable copies and never the live Evidence repository, and that `scripts/wfc_op.py archive` is not invoked a second time on a real Run (no second archive path).

Hard rules: scope only the test module, tests/fixtures/bp7/, the developer note and the review artifacts; no change to archive.py, scripts/wfc_op.py, tests/fixtures/bp5/, product code or other tests; no weakening of any assertion; the existing suite must keep passing. CommitIntent names every new/changed leaf individually, never directories, avoids rename-shaped changes; subject contains WI-WFC-BETA1-BP7-ARCHIVE-EQUIVALENCE-HARNESS-001 and the actual Hop ID. No agent Git writes. A Worker NEVER reports ANY Finding operation (`resolve`, `residual` or any other delta): only the independent reviewer opens and resolves Findings. Deliver ONE complete candidate in the first Worker Hop. Run only the equivalence test module with the repository's documented offline-input arguments for a partial run; no full suite from the Worker.

Edge-case rule: apply an exotic-input principle once at the boundaries the acceptance names; further exotic instances are non-blocking observations.

Reviewer (fresh, adversarial): verify that the BP5 case is unchanged in method and assertions (diff), that the second fixture really is a verbatim hash-pinned copy of the named archived Run (compare against the Evidence repository read-only), that the comparison can fail (mutation), that nothing outside scope changed, that no secret or absolute home path leaks beyond what BP5's provenance already records, and that the developer note is accurate. Blocking = a weakened/changed BP5 assertion, a fixture not matching its provenance digests, an insensitive comparison or a violated hard rule. `finding_authority: [resolve]` does not authorize residual transitions: report non-blocking observations only in the review artifact.


## Acceptance

The equivalence comparison is parameterized over fixture descriptors; the BP5 case passes unchanged; a second hash-pinned fixture of the named archived Run passes the same comparison; a tamper test proves sensitivity; the developer note is accurate; independently accepted with no blocking Findings and Coordinator task-check passed.

## Withheld authority

No change to archive.py, scripts/wfc_op.py, tests/fixtures/bp5, product code, other tests, policy or program, no write to the Evidence repository or any Run state, no extra Commission, Runtime promotion, push, merge, rebase or tag.

## Default escalation

Escalate only a real scope/authority conflict or an archived layout that cannot be reconstructed faithfully; incomplete work alone is not blocked.

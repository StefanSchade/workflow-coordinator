# E0c: final whole-product review of the E0b index-copy candidate

THIS IS A REVIEW-ONLY CONTINUATION. The product change is already in this checkout: package E0b (WorkItem WI-WFC-POSTBETA-E0B-INDEX-COPY-SITES-001, Run RUN-d91e299ce92847f690424674f25e2e4f) carried the live Git index mtime into all throwaway index copies of src/workflow_coordinator/git_facts.py through the shared helper `_isolated_index_copy(identity, prefix=...)` (shutil.copy2, temp file cleaned on every BaseException), with tests in tests/unit/test_index_copy_mtime.py and tests/unit/test_candidate_preservation.py. That Run stopped at its hop ceiling after its independent focused review H006 accepted, before the final whole-product broad review. Read first: docs/warm_storage/post-beta-planning/wfc_postbeta_e0b_index_copy_sites_broad_review.adoc and ..._focused_review.adoc (all passes), and the E0 record wfc_postbeta_e0_preservation_flake_broad_review.adoc.
DO (Worker, implement hop): do NOT change src/ or tests/. Write one short verification record docs/warm_storage/post-beta-planning/wfc_postbeta_e0c_index_copy_verification.adoc stating, with your own measured numbers: (1) that no `shutil.copyfile` of the index remains in src/ (show the search); (2) per site (`salvage_inventory`, `salvage_omissions`, `observe_repository`, `preserve_candidate`) that the copy carries the live index mtime (an exact attribute check, executed); (3) the new tests and the two affected test files ran at least 40 times with zero failures, and the full `python -m pytest -q` is green (the launching wrapper supplies the offline inputs; report pass/skip counts and name the skips); (4) one paragraph on anything in the E0b review records you consider unresolved or disproportionate. Keep it compact.
The independent reviewer then performs the whole-product broad review of the E0b candidate (implementation range 22e18b5..38106ae) against the E0b acceptance and the recorded review passes; the product code may be corrected only if the reviewer proves a material defect.
A Worker never reports any Finding operation (resolve, residual or any other delta); only the independent reviewer opens and resolves Findings. COMMIT MECHANICS (binding): no git command that writes (no add, mv, rm, reset, stash, commit); the index stays untouched; `commit_intent.paths` is a JSON array with exactly one plain repository-relative path per element (every changed leaf individually, no sentence, count, glob or directory); derive it from `git status --short -uall` and compare counts before you finish; state counts in the summary, never in the array.

## Acceptance

The verification record exists and reports measured results for items (1) to (4); src/ and tests/ are unchanged unless the independent reviewer proved a material defect; the independent whole-product broad review of the E0b candidate accepted with no blocking Findings; the full required suite is green; Coordinator task-check passed.

## Withheld authority

No change outside the verification record, the two E0c review artifacts, and (only after a reviewer-proven material defect) src/workflow_coordinator/git_facts.py, tests/unit/test_candidate_preservation.py and tests/unit/test_index_copy_mtime.py; no weakening of any assertion or refusal; no push, tag, Runtime promotion or external action.

## Default escalation

Escalate evidence that the E0b fix changes an accepted contract or documented guarantee; incomplete work alone is not blocked.

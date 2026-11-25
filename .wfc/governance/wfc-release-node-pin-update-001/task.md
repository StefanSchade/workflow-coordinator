# Move the artifact actions' pins off the Node 20 runtime (maintenance)

GOAL. Maintenance step, not a slice: move the two GitHub artifact actions pinned in `.github/workflows/release.yml` off the deprecated Node 20 runtime, and change nothing else. The maintainer decided on 2026-11-25: "Update the Node-runtime pins after R5; separate, narrowly bounded maintenance step; the new SHAs must demonstrably point to action versions with a suitable Node runtime; no other workflow changes."

VERIFIED FACTS (collected by the Level-1 operator on 2026-11-25 by reading the public upstream repositories over HTTPS: `git ls-remote` for tags and `raw.githubusercontent.com/<repo>/<sha>/action.yml` for `runs.using`; both tags below are lightweight, so the tag SHA is the commit SHA). Re-verify what you can in your sandbox and mark everything you cannot re-verify as taken from this task text; do not invent facts.

| Action | Current pin (runtime) | New pin (runtime) | Comment text |
| `actions/upload-artifact` | `ea165f8d65b6e75b540449e92b4886f43607fa02` (v4.6.2, `node20`) | `b7c566a772e6b6bfb58ed0dc250532a479d7789f` (v6.0.0, `node24`) | `# v6.0.0 (v6)` |
| `actions/download-artifact` | `d3f86a106a0bac45b974a628896c90dbdf5c8093` (v4.3.0, `node20`) | `37930b1c2abaa49bbe596cd826c3c89aef350131` (v7.0.0, `node24`) | `# v7.0.0 (v7)` |

Already on `node24`, therefore NOT changed: `actions/checkout` `d23441a` (v6.1.0), `actions/setup-python` `ece7cb0` (v6.3.0). `pypa/gh-action-pypi-publish` `7245906` (v1.14.3) is a composite action (`runs.using: composite`), not a Node action: NOT changed.

Why these versions: they are the smallest major bumps that remove `node20` (upload-artifact v5 and download-artifact v6 only had preliminary Node 24 support and still ran on Node 20 by default; v6 and v7 are the first to default to `node24`). Newer majors exist (upload-artifact v7.0.2 `cf430e030ddbb5b0abf93d22962f4752f3646cd9`, download-artifact v8.0.2 `9000827ccba6bdab643e8b6fd33ac0654aef8333`) and add inputs and behaviour changes (`archive`, `skip-decompress`, `digest-mismatch`); they are deliberately not adopted here. Inputs this workflow uses and that still exist in the new versions (checked in their `action.yml`): upload `name`, `path`, `if-no-files-found`; download `name`, `path`. Documented breaking changes between the old and new versions concern self-hosted-runner firewall rules, by-artifact-ID extraction and artifacts from `upload-artifact@v3` and below; none applies to this workflow (it uses names, hosted runners and v4-or-newer artifacts). The new versions require Actions runner 2.327.1 or newer; GitHub-hosted `ubuntu-24.04` runners are assumed to satisfy that: an EXTERNAL ASSUMPTION, state it as such. Nothing here was or can be run on GitHub from this environment.

DO.
1. In `.github/workflows/release.yml` change exactly the five `uses:` lines of these two actions (one `upload-artifact`, four `download-artifact`): SHA and trailing comment together, exactly as in the table. No other line of the file changes.
2. In `docs/developer_guide/ch04_release_management.adoc` update only the pin enumeration paragraph (the sentence listing `actions/upload-artifact` `ea165f8` (v4.6.2) and `actions/download-artifact` `d3f86a1` (v4.3.0)): the two new pins, a short statement that the maintainer replaced them on 2026-11-25 to leave the Node 20 runtime, and that the other pins were checked and are already on a suitable runtime. Keep the rest of the paragraph (including the way to change a version) and the whole chapter otherwise unchanged; correct the sentence "so nothing was upgraded" only as far as it is now false.
3. Write a compact `docs/warm_storage/post-beta-planning/wfc_release_node_pin_update_verification.adoc`: the table above with the evidence and its source, what you re-verified yourself and what is taken from this task, the before/after comparison of the workflow (job graph, permissions, environments, triggers, every other `uses:` pin, programmatically: parse both versions with a YAML loader), the external assumption about the runner version, and the checks you ran.
4. Front Door docs-sync convention (`docs/developer_guide/ch03_engineering_conventions.adoc`, `frontdoor-docs-sync`): CI configuration only, no Front Door behavior changes; record the check and its answer.

WITHHELD: no other change in `release.yml`; no change to any other file than the three named; no new action, no permission, no secret, no job or `needs:` change; no `dependabot.yml` change; no product code, test, script, Makefile, pyproject or version change; no `.wfc/` or `governance/` change; no push, tag, release, publication, workflow dispatch, GitHub setting or credential; no Runtime promotion; no weakened or deleted test. A needed file outside the scope is a scope question (stop and name it), not a reason to widen.

ENVIRONMENT: the Coordinator's task-check runs the full required suite (`python -m pytest -q`) after your Hop with verified offline inputs; your sandbox may lack them, so do not claim full-suite counts you did not measure. Strict render `asciidoctor --safe-mode=safe -a assembled -o /dev/null --failure-level=WARN <file>` must print nothing for each changed or new .adoc; `python3 scripts/wfc_doc_catalog.py generate` then `check` must not report more unresolved links or Project Memory misses than before your change (note the baseline first; today 83 and 4). Leave no stray file, generated catalog or `__pycache__` behind.

COMMIT MECHANICS (binding): no git command that writes (no add, mv, rm, reset, stash, commit); the index stays untouched; `commit_intent.paths` is a JSON array with exactly one plain repository-relative path per element (expected: exactly three files); derive it from `git status --short -uall` and compare the counts before you finish. Never put untracked /dev/null-like wrapper entries into the CommitIntent. A Worker never reports any Finding operation.

## Acceptance

Exactly the five uses lines of upload-artifact and download-artifact are changed to the table's SHAs and comments and no other line of release.yml; the chapter-4 pin enumeration names the two new pins and the maintenance step without any other chapter change; the verification record contains the evidence table, what was re-verified versus taken from the task, a programmatic before/after comparison showing the job graph, permissions, environments, triggers and all other pins unchanged, and the runner-version external assumption; exactly three files changed; strict render prints nothing; doc catalog shows no additional unresolved links; the full required suite passes; CommitIntent array has one plain path per element; Coordinator task-check passed.

## Withheld authority

No change to any file other than release.yml (five uses lines), the chapter-4 pin paragraph and the verification record; no job, needs, permission, secret, trigger or other pin change; no product code, test, script, Makefile, pyproject or version change; no .wfc/ or governance/ change; no push, tag, release, publication, workflow dispatch or GitHub setting; no Runtime promotion; no weakening of tests.

## Default escalation

Escalate a needed file outside the scope and any fact in the table that you can show to be wrong. Do not silently substitute models. Incomplete work alone is not blocked.

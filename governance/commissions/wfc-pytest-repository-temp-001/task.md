# Repository-owned pytest temporary storage

Resolve recurring pytest /tmp inode exhaustion at the repository test
infrastructure ownership boundary. Inspect pyproject.toml, Makefile,
fixtures, tests/unit/test_wfc_op.py, Git-heavy architecture tests,
temporary handling, ignore rules, and the controlling BUG including the
November 14 incident and static investigation.

Implement the least complicated viable repository-level mechanism so
ordinary python -m pytest -q uses a suitable pytest temp base outside
default host /tmp without Harness. Avoid developer-specific paths;
preserve explicit pytest temp configuration; prefer suitable supplied
external scratch; provide a repository-owned default writable by a WFC
self-development worker; isolate concurrent processes from destructive
resets; avoid tracked paths/unwanted Git changes and unbounded retention
where practical; fail clearly rather than silently use unsuitable storage;
preserve failure diagnostics and fixtures. Explain the actual filesystem
location. Distinguish pytest-owned temporary paths from direct tempfile
or external tool files. Do not change Runtime or adapters; report any
separately reproducible runtime defect instead.

Correct docs/backlog/BUG-WFC-PYTEST-TEMP-RESOURCE-EXHAUSTION.adoc:
Harness prepares external scratch, Codex inherits Coordinator environment,
and Coordinator-owned evidence inherits its environment; pytest fixtures
and many Git-constructing tests are confirmed. A Codex adapter defect,
TMPDIR propagation failure in the affected process, and the identities
of all processes producing previously retained /tmp files are not
confirmed. Classify demonstrated problem as repository-specific test
infrastructure robustness; retain incident history and set disposition
to match implementation and evidence, not intent or workaround alone.

Update relevant canonical Operator Manual boundaries and self-development
temporary storage guidance, and docs/functional_model/workflow_program_use_cases.adoc:
WFC provides and propagates its documented execution environment;
consumers configure their own tools; arbitrary tools may ignore temp
variables, hard-code paths or alter subprocess environments; WFC cannot
promise placement for all consumer toolchains. This is no exemption from
documented execution or sandbox responsibilities. Self-development makes
the WFC repository a consumer of its Runtime. Keep functional requirements
separate from implementation instructions; Functional Model is a discussion
record, not automatically accepted runtime contract. Avoid redundant
disclaimers or new governance. Update _open.adoc only for material change.

Before test-heavy execution check df -h and df -i on /tmp and chosen
scratch filesystem; never delete unrelated files. Run a small initial
validation before the full suite. Demonstrate ordinary invocation without
Harness avoids /tmp for pytest-owned test repositories, configured suitable
scratch root is honored, overlapping runs do not share destructively reset
bases, actual location is identifiable, and fixture behavior is preserved.
Run Coordinator's existing trusted python -m pytest -q acceptance; report
actual results. Stop safely on concrete resource constraints; do not
disable tests, weaken assertions or bypass evidence. No broad test
refactoring, generic scratch subsystem, provider feature, historical
Commission or Run edits, or unrelated cleanup.

Independent broad reviewer must examine classification, actual pytest
paths, concurrency, cleanup/retention, compatibility with existing scratch
configuration and self-development filesystem restrictions, ordinary
pytest command, documentation claims and BUG status against evidence.
Review artifacts are reserved for their respective review phases. Worker
and reviewer must not perform mutating Git operations: Coordinator owns
trusted publication. CommitIntent lists exactly changed repository-relative
leaves; subject is one Conventional Commit line containing
WI-WFC-PYTEST-REPOSITORY-TEMP-001 and actual Hop ID as separate tokens.
A blocked result carries no CommitIntent. Report actual tests, limitations,
classification and any unresolved evidence.


## Acceptance

Repository-owned pytest configuration meets the task's storage, concurrency, retention, permissions and fixture requirements; targeted evidence and trusted task-check pass; accurate bounded documentation and BUG disposition reflect evidence; independent broad review accepts with no open blocking findings.

## Withheld authority

No runtime or adapter modifications, broad refactoring, generic scratch subsystem, provider features, historical Commissions or Run outcomes, runtime promotion, push, extra Commission or Run, or agent Git writes. Review artifacts are phase-reserved.

## Default escalation

Stop for concrete authority or environment blockers; do not bypass trusted evidence or repeatedly exhaust /tmp. Otherwise complete authorized work.

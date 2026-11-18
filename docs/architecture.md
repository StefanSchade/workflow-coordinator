# Architecture

`workflow_coordinator` is a small `src`-layout Python package. Today its modules
are flat under `src/workflow_coordinator/*.py`; that is observed current state,
not a canonized future package organization. Its operator owns
deterministic admission, serial workflow progression, Git observation and
bounded mechanical commits, evidence execution, persistence, and stop routing.
The provider-neutral execution port separates those semantics from the implemented
`codex_cli` and `claude_cli` adapters, selected through per-role execution
bindings (currently one adapter per Run); consumers own task inputs, repository
policy, workflow profiles, and authorization.

The responsibility boundary that matters is between a deterministic core and an
effectful shell. `model.py`, `serialization.py` and `structured_result_schema.py`
hold frozen values, closed enums and the validation of every governing input and
agent result. `reducer.py` and `prompt.py` make the decisions -- Finding
lifecycle and authority, route selection and refusal, bounded prompt assembly --
as pure functions over admitted values. `operator.py`, `vertical.py`,
`git_facts.py`, `evidence.py`, `persistence.py` and `commissioning.py` perform
every effect, and `git_facts.py:trusted_commit` is the only Git write surface in
the package. A provider result is an untrusted candidate until it is parsed and
correlated to its exact dispatch; Coordinator-run evidence commands and the
Coordinator's own Git observation are separate, trusted facts.

The Front-Door layer in `commissioning.py` deterministically renders a
maintainer-authored Commission Intent into the ordinary WorkItem, Profile,
Policy, and Task files consumed by `prepare` and `launch`. After review and
commit, `commission check` verifies the rendering and asks the canonical,
provider-free operator preflight to admit the bundle; it selects no provider
and admits no Run. The maintainer then prepares, inspects, and authorizes launch.
The current entry point is the repo-local `scripts/wfc_op.py`; a pip-installable,
consumer-repository-neutral Front Door remains planned.

The closed `simple@v1`, `direct@v1`, and `robust@v1` workflow programs are
implemented. `simple@v1` has one bounded Worker invocation with an
in-invocation self-check and no independent reviewer. `direct@v1` follows a
Worker with deterministic verification (including `task-check` when selected)
and a correction loop; its verification gate declares no Finding-disposition
authority. `robust@v1` adds an independent adversarial reviewer with broad and
focused review and correction loops, and places the only completion route on
`broad_review.accept`, so completion always follows a fresh broad review.
`simple@v1` and `robust@v1` have independent acceptance. `direct@v1`'s
implementation acceptance reconciliation remains outstanding: its reported
acceptance Hop was not observed by the Coordinator and that Run stopped
`reconcile_required`. Beta inclusion is a separate decision. Each program's
exact current standing is owned by the
[active-work manifest](backlog/_open.adoc).

The three programs are hand-authored records under
`governance/workflow-programs/`; a Run can never write one, because a phase's
authorized scope may not overlap repository-policy governance paths. A
Commission Intent fills only declared scope, artifact and evidence-profile
slots, so ordinary Commission authoring cannot alter a program's phases,
routes, review descriptors or guarantees.

The package-owned A3 operating contract is loaded through `importlib.resources`
and cannot be selected or replaced by a consumer. Durable consumer evidence and
repository mutations remain governed by the admitted consumer inputs. One
WorkItem mutates one repository.

For self-development, the accepted runtime and development repositories remain
separate:

```text
accepted Runtime WFC N  --operates on-->  Development WFC N+1
accepted Runtime WFC N  --never mutates--> Runtime WFC N
```

The runtime executable, repository, Git state, and execution artifacts are not
development payload. WFC may create only bounded local commits admitted for the
development repository. A maintainer reviews and accepts those commits and
alone owns any push and any later, separate runtime promotion.

WFC's present core responsibility, in general and not only for
self-development, is repository-acceptance integrity: independently observing
Git/filesystem state and applying deterministic scope and acceptance checks
before any Coordinator-owned commit, regardless of what an agent's own
instructions authorized. This is distinct from execution-environment
containment. WFC V1 provides neither hostile-code containment nor host
isolation; restricting agent writes is primarily an operational safeguard
against accidental damage, not a security perimeter. A provider's own
sandbox/permission request (Codex `--sandbox workspace-write`, the Claude
adapter's closed profile) is a transitional operational safeguard -- useful,
not discarded without reason, but provider-owned and not Coordinator-verified
(`DEC-WFC-009`). No current Coordinator-owned or Coordinator-verified
OS/environment containment boundary is claimed, for self-development or any
other consumer repository, and WFC does not require a provider sandbox to
preventively enforce a phase's `authorized_scope`; the Coordinator's own
post-Hop Git/scope observation is the authoritative enforcement of that
scope for candidate acceptance; this does not prevent or undo writes. WFC
ownership of a provider-independent execution-isolation boundary is an accepted
future direction, not current V1 functionality. Its dispatch integration and
effective qualification are post Beta 1; Beta 1 must not claim preventive
Commission-scope isolation. Existing provider safeguards remain in place; see
[FTR-WFC-EXECUTION-TRUST-BOUNDARIES](backlog/FTR-WFC-EXECUTION-TRUST-BOUNDARIES.adoc)
for its status and non-current standing.

This document stays a short overview. The next level of detail lives in three
subject models, which explain rather than authorize:

- [Functional Model](functional_model/workflow_program_use_cases.adoc) -- why WFC
  exists, its goals and non-goals, and its use cases, with present capability
  stated separately from draft product intent.
- [Semantic Model](semantic_model/workflow_execution_semantics.adoc) -- what
  Commission, Program, Run, Hop, Finding, Evidence and Route mean, including
  phase versus route authority and Finding identity versus disposition.
- [Technical Model](technical_model/workflow_program_architecture.adoc) -- how
  admission, projection, reduction, persistence, adapters and evidence are
  wired, including the implemented P1–P3 projected result contracts.

Operating procedures -- commissioning, preparation, launch, evidence, recovery,
promotion -- belong to the [Operator Manual](operator_manual/operator_manual.adoc)
and are not restated here or in the models.

The [standalone authority index](README.adoc) is the authoritative
classification of current authority, provenance, historical material, and
planning evidence. This architecture document does not duplicate that index.
For active unimplemented work, use the
[active-work manifest](backlog/_open.adoc), not historical concepts or reviews.

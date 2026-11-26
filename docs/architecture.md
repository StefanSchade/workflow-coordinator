# Architecture

`workflow_coordinator` is a small `src`-layout Python package. Today its modules
are flat under `src/workflow_coordinator/*.py`; that is observed current state,
not a canonized future package organization. Its operator owns
deterministic admission, serial workflow progression, Git observation and
bounded mechanical commits, evidence execution, persistence, and stop routing.
The provider-neutral execution port separates those semantics from the implemented
`codex_cli` and `claude_cli` adapters, selected through an execution envelope
with role defaults and optional phase overrides (one adapter per Run).
Consumers own task inputs, repository
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

The Front-Door layer (`commissioning.py`'s `compile_commission`, wired into
`frontdoor/`) deterministically compiles a maintainer-authored, consumer-
neutral Consumer Request/Config into the ordinary WorkItem, Profile, Policy,
Components and Task files `wfc run`/`resume` admit and launch. `wfc plan`
previews that same compilation, read-only, before anything is committed;
`wfc run` re-verifies the committed bundle
(`frontdoor.verify.verify_commission`, the canonical, provider-free
preflight) before ever launching, then launches. The installed, pip-
installable `wfc` console command is the regular entry point for every WFC-
operated repository, including WFC self-development itself.

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
(`DEC-WFC-009`). An opt-in, Coordinator-owned Linux filesystem boundary
(`src/workflow_coordinator/execution_boundary.py`) is now wired into both
provider adapters' dispatch through one provider-neutral launcher seam
(`src/workflow_coordinator/execution_launcher.py`); it defaults to off, and
selecting it is a Run-wide configuration choice, never a default. It is
designed so that, for an invocation that selects it and for which it is
successfully established, writes outside the authorized grant and access to
`.git`, the gitdir and the common Git directory are refused at the OS level
rather than merely rejected afterward -- but **no preventive-isolation claim
is made for any invocation yet**: that claim requires both the deterministic
Layer 1 tests and the Layer 2 live qualification with real authenticated
providers to have passed (`FTR-WFC-EXECUTION-BOUNDARY-DISPATCH-INTEGRATION`),
and Layer 2 has not been performed. The boundary shares the provider's
network namespace, hides no repository content from an agent, and claims
nothing about hostile-code or kernel-level containment. Until the
qualification exit criteria are met, and always for an invocation that does
not select the boundary (still every Run by default), the Coordinator's own
post-Hop Git/scope observation remains the sole authoritative enforcement of
a phase's `authorized_scope` for candidate acceptance; this does not prevent
or undo writes. Confidentiality, per-phase visibility profiles, elevated
privilege and network containment remain future directions, not current V1
functionality. See
[FTR-WFC-EXECUTION-TRUST-BOUNDARIES](backlog/FTR-WFC-EXECUTION-TRUST-BOUNDARIES.adoc)
for its status and standing.

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

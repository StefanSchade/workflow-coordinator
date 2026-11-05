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
and a correction loop. `robust@v1` adds an independent adversarial reviewer
with broad and focused review and correction loops. The latter two have
outstanding implementation review work.

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
scope. A WFC-owned, provider-independent execution-isolation layer is a
possible future direction, not current V1 functionality; see
[FTR-WFC-EXECUTION-TRUST-BOUNDARIES](backlog/FTR-WFC-EXECUTION-TRUST-BOUNDARIES.adoc)
for its status and non-current standing.

The [standalone authority index](README.adoc) is the authoritative
classification of current authority, provenance, historical material, and
planning evidence. This architecture document does not duplicate that index.
For active unimplemented work, use the
[active-work manifest](backlog/_open.adoc), not historical concepts or reviews.

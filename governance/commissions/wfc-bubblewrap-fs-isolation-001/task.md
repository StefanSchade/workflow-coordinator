# Implement a first-version WFC-owned, provider-independent bubblewrap filesystem-isolation primitive

Build one small, reusable, provider-neutral execution-boundary module
that uses unprivileged `bwrap` to give an untrusted local subprocess a
private mount namespace: read-only repository visibility with `.git`
effectively hidden, explicit bounded writable subpaths, private writable
temporary storage, and no visibility into unrelated host files, WFC
Coordinator/OPS/STATE state, or Evidence. It must remain fully opt-in:
no existing Codex/Claude adapter, `operator.py`, or ordinary invocation
path is wired to it or changed by it in this WorkItem.

### Required reading

Read, in full, before implementing:

- `docs/backlog/FTR-WFC-EXECUTION-TRUST-BOUNDARIES.adoc` (including its
  2026-11-06 narrow-authorization section) -- the controlling trust-
  dimension framing (visibility / mutation / execution authority are
  distinct), its explicit non-goals, and its "candidate design
  direction" section naming `bwrap` as one candidate mechanism, not a
  required or previously-implemented one.
- `docs/backlog/FTR-WFC-ELEVATED-PRIVILEGE-DEFENSIVE-REVIEW.adoc`, to
  confirm this WorkItem's scope does not itself cross into a surface
  that FTR governs (it does not grant mutation/execution authority over
  any currently-elevated surface; it only builds a boundary primitive
  and tests it against synthetic, harmless local fixtures).
- `docs/self-development.adoc`'s Commission-authoring and Decision
  sections, in particular the `DEC-WFC-009` distinction between a
  provider's own sandbox/permission request (operational,
  provider-owned, not Coordinator-verified) and WFC's own deterministic
  post-Hop Git/scope observation (Coordinator-owned, authoritative).
  This WorkItem's primitive is independent of, and does not change,
  either of those; it must not be represented as superseding or
  replacing the authoritative post-Hop Git/scope check.
- `src/workflow_coordinator/codex_adapter.py` and
  `src/workflow_coordinator/claude_adapter.py`, read-only, for the
  shape of today's provider dispatch (so the new primitive's interface
  is realistic for eventual use) -- without editing either file.
- `src/workflow_coordinator/operator.py`'s `_admit_a1`/`_within_scope`
  (around operator.py:33, :58-123) and
  `governance/policy-bases/repository-standard-v1.yaml`, read-only, to
  understand that this Commission's own file scope is a mutation/write
  boundary, not a general read-authority model -- the same distinction
  this WorkItem's own primitive must express at the OS level for its
  sandboxed subprocess.
- `tests/unit/test_wfc_op.py` and `tests/architecture/` conventions, to
  keep new tests consistent in style with existing ones.

Do not treat this record as asking for, or as itself constituting, a
general read-authority/classification model, a credential broker, a
network-policy layer, or any change to an existing security/admission
check. None of those is in scope.

### Scope

Implement, confined to new files only (no existing source file is
modified):

- `src/workflow_coordinator/execution_boundary.py` (new): a small,
  provider-neutral module providing:
  - A typed, explicit mount-specification representation (host source
    path, sandbox destination path, read-only or writable,
    tmpfs-backed private writable scratch) -- no implicit defaults that
    widen access.
  - A validator that, given a repository root and a mount
    specification, fails closed (raises, never silently narrows or
    widens) on: a missing bwrap binary; a relative or symlinked mount
    source where an absolute, real path is required; a writable grant
    whose destination is `.git` or resolves inside it; overlapping or
    order-dependent mount destinations that bwrap cannot represent
    unambiguously; a missing destination parent directory it is not
    itself asked to create; and any mount-spec shape outside the
    bounded subset this slice supports. A single explicitly-granted
    writable file path must never be silently widened into a writable
    grant over its containing directory.
  - A function that, given a validated mount specification, constructs
    the exact `bwrap` argument vector implementing: a private mount
    namespace; read-only binds for the required system executables/
    libraries (e.g. `/usr`, `/lib*`, `/bin` as available on this
    machine) sufficient to run a local test executable; read-only bind
    of the target repository with `.git` separately bound over
    (`--tmpfs` or a read-only empty overlay) so it is unreadable and
    unwritable inside the sandbox even though the repository parent is
    read-only bound; explicit bind-mounts (writable) for the caller's
    pre-authorized writable subpaths only; a private writable `tmpfs`
    or bound scratch directory distinct from the repository; and
    `--die-with-parent`/equivalent so no sandboxed process outlives the
    caller unexpectedly. No network-namespace flag is set or unset by
    this module either way beyond bwrap's own default; this WorkItem
    introduces no network policy of its own.
  - A runner that invokes `bwrap` as a subprocess wrapping a caller-
    supplied command/argv, raises on a missing or non-executable
    `bwrap`, never falls back to running the command unsandboxed on any
    failure, and returns/propagates the wrapped subprocess's exit
    status, stdout, and stderr faithfully.
- `tests/unit/test_execution_boundary_config.py` (new): configuration/
  mount-specification unit tests that do not require running a real
  `bwrap` subprocess (pure construction/validation-helper tests, using
  temporary directories/files but asserting on the constructed argv or
  raised errors, not on sandboxed subprocess behavior).
- `tests/integration/` (new directory, sibling to the existing
  `tests/unit/` and `tests/architecture/`): real-subprocess filesystem-
  isolation tests, kept in this separate directory specifically so they
  are distinguishable from the configuration/unit tests above.
  `tests/integration/test_execution_boundary_bwrap.py` runs actual
  `bwrap` subprocesses (never a mock or a dry-run flag) against a
  synthetic fixture repository and harmless local test executables
  (e.g. the system `cat`, `ls`, `touch`, or a small fixture script) to
  prove the isolation properties in "Required tests" below.
- The two durable review artifacts this Commission's rendered
  `robust@v1` profile designates for `broad_review` and
  `focused_review`.

Do not edit `src/workflow_coordinator/operator.py`,
`src/workflow_coordinator/codex_adapter.py`,
`src/workflow_coordinator/claude_adapter.py`,
`src/workflow_coordinator/git_facts.py`,
`src/workflow_coordinator/persistence.py`,
`src/workflow_coordinator/evidence.py`,
`src/workflow_coordinator/commissioning.py`,
`src/workflow_coordinator/serialization.py`, `model.py`, `reducer.py`,
`slice4.py`, `vertical.py`, `proof_harness.py`, `scripts/wfc_op.py`,
`pyproject.toml`, or any existing test file. This WorkItem is purely
additive: new files only, nothing wired into any existing dispatch path.
If a pytest marker, config change, or any edit to an existing file ever
appears necessary to make the required tests discoverable or runnable,
treat that as a scope conflict and escalate per "Default escalation"
below rather than editing an out-of-scope file -- rely on
`tests/integration/`'s directory placement (consistent with the
existing `tests/unit/`/`tests/architecture/` separation, both already
discovered via `pyproject.toml`'s unmodified `testpaths = ["tests"]`)
to keep the new tests both discoverable and distinguishable without any
configuration change.

### Required semantics

- The primitive is a library/module only: it is not imported or called
  from `operator.py`, `codex_adapter.py`, `claude_adapter.py`, or
  `scripts/wfc_op.py` in this WorkItem. It must remain possible to run
  every existing test and every existing Codex/Claude Attempt exactly
  as before, byte-for-byte unchanged in behavior.
- Fail closed, always: a missing `bwrap` binary, an invalid or
  unrepresentable mount specification, or any validation failure must
  raise before any subprocess is spawned -- never fall back to running
  the wrapped command unsandboxed, and never silently drop or widen a
  requested restriction.
- Writable authority is file/path-specific and must never implicitly
  become directory-level authority beyond what was explicitly granted.
- `.git` must be verified absent from the resulting sandboxed
  namespace by actual test observation (attempting to read or write it
  from inside the sandbox), not merely inferred from the constructed
  bwrap argument vector.
- Symlink and path-traversal attempts from inside the sandbox (e.g. a
  writable grant containing a symlink pointing outside the granted
  tree, or a `..`-relative escape attempt) must not be able to reach
  forbidden host paths; prove this by actual attempted access from
  inside a real sandboxed subprocess, not by static argv inspection
  alone.
- Never weaken, remove, or bypass any existing security/admission test
  (A1 scope-ceiling checks, repository-pin revalidation, launch/
  execution-bindings digest checks, or any other existing test) to
  make this WorkItem's own tests easier to pass.
- Where a currently-authorized Commission scope pattern (e.g. this very
  Commission's own file-level `scope:` entries) cannot be safely or
  unambiguously translated into a bwrap mount permission, the module
  must reject that shape explicitly (a clear, typed error) rather than
  approximate it; document any such unsupported pattern in the review
  artifact rather than silently handling it.

### Required tests

All in `tests/integration/test_execution_boundary_bwrap.py` unless
noted, using real `bwrap` subprocesses against synthetic fixture
directories (never the real Development/Runtime/Evidence/OPS/STATE
repositories or real host data):

- A permitted read succeeds: a read-only bound file is readable inside
  the sandbox with its real content.
- A permitted write succeeds: a file inside an explicitly writable
  bound subpath can be created/modified inside the sandbox and the
  change is visible on the host afterward at the expected path.
- A forbidden write fails and leaves the host file unchanged: an
  attempted write to a read-only bound path fails inside the sandbox,
  and the host file's content/mtime is verified unchanged afterward.
- An unrelated private host file (outside every granted mount) is
  invisible: the sandboxed process cannot stat, read, or list it or
  its containing directory by the path it has on the host.
- `.git` cannot be read or modified from inside the sandbox even when
  its containing repository is read-only bound for ordinary file
  access.
- Private temporary storage works: a file written to the sandbox's
  private writable scratch is visible inside that same sandboxed
  process/run and is verified host-distinct from the repository's own
  temp handling.
- A symlink- or path-traversal-based escape attempt (a writable grant
  whose leaf is a symlink pointing outside the granted tree, and a
  `../`-relative traversal attempt from inside a writable bound
  directory) cannot reach a forbidden host path; assert the host file
  outside the grant is unchanged.
- Overlapping or order-ambiguous mount specifications are rejected by
  the validator before any subprocess is spawned (unit-level, in
  `tests/unit/test_execution_boundary_config.py`, asserting the raised
  error, not a subprocess run).
- An invalid configuration (e.g. a relative mount source, a writable
  grant resolving inside `.git`, or an unrepresentable overlap) fails
  closed with no subprocess spawned (unit-level, in
  `tests/unit/test_execution_boundary_config.py`).
- An unavailable `bwrap` (simulate via an empty/overridden `PATH` or
  explicit binary-path override, not by uninstalling the real system
  package) fails closed with a clear error and no fallback to
  unsandboxed execution.
- A smoke test proving the full existing repository test suite
  (`python -m pytest -q`) still passes unmodified, demonstrating
  existing execution paths are unchanged by this WorkItem's purely
  additive diff.

A test that only inspects the generated `bwrap` command line without
actually running a sandboxed subprocess and observing its real
filesystem behavior does not satisfy any of the isolation requirements
above.

Run the repository's `task-check` evidence command
(`python -m pytest -q`) as the Coordinator evidence gate.

### Independent review mandate

The independent reviewer must inspect the actual implementation and
tests -- not merely a diff summary -- for: that every required test
above actually exercises a real sandboxed subprocess where specified
(not a command-line-only assertion); that `.git` invisibility and the
symlink/traversal-escape tests genuinely attempt the forbidden access
from inside the sandbox rather than asserting on argv construction;
that no excluded file was touched and the diff is purely additive; that
fail-closed behavior holds for every named invalid-configuration and
missing-bwrap case with no unsandboxed fallback anywhere; that no
existing security/admission test was weakened, skipped, or removed;
that a single file-level writable grant is never representable as
directory-level writable access; and that the full existing test suite
still passes. Use normal Robust correction and re-review routing;
disposition any Finding using only the authority this Commission's
rendered profile actually grants. A successful broad review may accept
directly; focused review is needed only if that phase is reached.


## Acceptance

`src/workflow_coordinator/execution_boundary.py` exists as a new,
standalone, provider-neutral module implementing mount-specification
construction/validation and a real-`bwrap`-backed sandboxed-subprocess
runner exactly per "Scope" and "Required semantics" above. It is not
imported by, and does not change the behavior of, `operator.py`,
`codex_adapter.py`, `claude_adapter.py`, or `scripts/wfc_op.py`.
`tests/unit/test_execution_boundary_config.py` and
`tests/integration/test_execution_boundary_bwrap.py` both exist, the
latter under a new `tests/integration/` directory kept distinguishable
from `tests/unit/`, and together satisfy every item in "Required
tests" above using real `bwrap` subprocesses and actual observed
filesystem behavior for every isolation claim (reads, writes, `.git`
invisibility, unrelated-host-file invisibility, private temp storage,
symlink/traversal-escape resistance, overlap/invalid-config rejection,
missing-bwrap fail-closed behavior). No existing source or test file
outside this Commission's rendered scope is changed; in particular
`operator.py`, `codex_adapter.py`, `claude_adapter.py`,
`scripts/wfc_op.py`, and `pyproject.toml` are byte-identical to their
state before this Attempt. `task-check` (`python -m pytest -q`) passes
against the full suite, including both new test files. The independent
reviewer's broad (and, if reached, focused) review artifact records a
genuine adversarial review against the mandate above, explicitly
confirms every required test actually runs a real sandboxed subprocess
where specified, and leaves no blocking Finding open at completion.


## Withheld authority

No change to `src/workflow_coordinator/operator.py`,
`codex_adapter.py`, `claude_adapter.py`, `git_facts.py`,
`persistence.py`, `evidence.py`, `commissioning.py`,
`serialization.py`, `model.py`, `reducer.py`, `slice4.py`,
`vertical.py`, `proof_harness.py`, `scripts/wfc_op.py`, or
`pyproject.toml`. No wiring of this primitive into any live provider
invocation, Hop dispatch, or ordinary Codex/Claude execution path --
it remains opt-in library code only in this WorkItem. No broader
trust-boundary architecture, read-authority/classification model,
credential broker, network-namespace policy or egress control,
privileged or setuid mechanism, trusted-test-service sidecar, or
automatic recovery/retry mechanism -- each is explicitly out of scope
regardless of how `FTR-WFC-EXECUTION-TRUST-BOUNDARIES`'s own candidate-
direction or non-goals text might seem to invite it. No weakening,
removal, or skip of any existing security or admission test. No
representation of a provider-requested sandbox/permission mode as a
Coordinator-verified OS boundary, and no weakening of any existing A1
scope-ceiling or repository-pin revalidation check. No Runtime
promotion, Evidence mutation, nested WFC invocation, or external push.
No file outside this Commission's rendered `authorized_scope` for the
active phase may be edited.


## Default escalation

Stop and request maintainer authority if: a bounded subset of mount-
specification shapes sufficient for the required tests cannot be
implemented without touching an excluded source file; proving `.git`
invisibility or symlink/traversal resistance under real `bwrap` turns
out to require a kernel/bwrap capability unavailable on this machine
(report the exact `bwrap` error); a reviewer-opened Finding cannot be
disposed within this Commission's own granted Finding authority and
ordinary Robust-v1 correction flow; or completing the slice would
require any decision reserved to maintainer authority by
`FTR-WFC-EXECUTION-TRUST-BOUNDARIES` itself (its independent-elevation,
information-flow/declassification, or indirect-execution sections) --
including any actual wiring into provider dispatch, any network-policy
decision, or any read-authority model broader than "repository-wide
read visibility with `.git` hidden and explicit writable subpaths."


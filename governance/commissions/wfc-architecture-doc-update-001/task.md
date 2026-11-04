# Update docs/architecture.md to describe the current dual-adapter, Front-Door implementation

Correct `docs/architecture.md` so it accurately describes the current
implementation. Keep it Markdown, keep its existing length and register
(a short, dense architecture summary, not a tutorial), and preserve
every existing architecture decision and invariant it already states
unless a specific invariant has itself changed (none has): the A3
operating contract loaded via `importlib.resources` and not consumer-
selectable; one WorkItem mutates one repository; the self-development
Runtime-N/Development-N+1 separation and its never-mutates-itself/
bounded-local-commits-only/maintainer-owns-push-and-promotion
invariants; and the explicit disclaimer that no Coordinator-owned or
Coordinator-verified OS/environment containment boundary is claimed.

Required reading before editing, as grounding fact rather than
assumption:

* `docs/architecture.md` itself, in full -- the file you are correcting.
* `src/workflow_coordinator/operator.py`, enough to confirm both
  `codex_cli` and `claude_cli` are implemented role-binding adapters
  behind the provider-neutral execution port (the current code, not
  this task's own summary of it).
* `src/workflow_coordinator/commissioning.py`'s module docstring and the
  `direct`/`robust`/`simple` entries in its `program_specs` /
  `commission_intent_from_yaml`'s `required_personas`, enough to
  describe accurately, at architecture-summary altitude, what it does:
  it deterministically renders a maintainer-authored Commission Intent
  (one of the closed `direct@v1`, `robust@v1`, or `simple@v1`
  programs) into the ordinary WorkItem/Profile/Policy/Task governing
  files that `prepare`/`launch` already consume, and then (via
  `commission check`) asks the existing canonical, provider-free
  operator preflight to admit the rendered bundle; it makes no
  provider selection and admits no Run by itself.
* `governance/workflow-programs/simple/v1.yaml`,
  `governance/workflow-programs/direct/v1.yaml`, and
  `governance/workflow-programs/robust/v1.yaml`, enough to describe
  each program's shape at one sentence of altitude (e.g. `simple@v1` is
  one bounded Worker invocation with an in-invocation self-check and no
  independent reviewer; `direct@v1` is Worker then deterministic
  task-check verification with a correction loop; `robust@v1` adds an
  independent adversarial reviewer with broad/focused review and
  correction loops) -- do not reproduce their full phase/route
  mechanics, which belong to those files and to
  `docs/wfc_operator_manual.adoc`, not to this architecture summary.
* `docs/wfc_operator_manual.adoc` section 6 ("Normal operator
  workflow" and "Direct-v1 Commission quickstart"), enough to describe
  the Commission-authoring -> render -> commit -> check -> prepare ->
  launch sequence at the same altitude this architecture document
  already uses for its other invariants, without restating that
  section's command-level detail.
* `docs/backlog/_open.adoc`'s current workflow-capability and adapter
  rows, enough to state plainly which of `simple@v1`, `direct@v1`,
  `robust@v1`, the Claude Code adapter, and a pip-installable/
  consumer-neutral Front Door are implemented today versus still
  planned. In particular: do not claim the 0.1.0b1 pip-installable,
  consumer-repository-neutral Operator Harness/Front Door already
  works -- `scripts/wfc_op.py` is repo-local today (not an installed
  console command); that remains a planned target, not current fact.

Make the narrowest edit that fixes these four problems:

1. The adapter description currently singles out "the Codex CLI
   adapter"; correct it to state that the provider-neutral execution
   port has (at least) two implemented adapters, `codex_cli` and
   `claude_cli`, selected per role via execution bindings, without
   claiming either is default or exclusive.
2. Add a brief description of `commissioning.py`'s role (the Front
   Door / Commission-rendering layer) and how it relates to the
   existing `prepare`/`launch` mechanics this document already
   gestures toward -- at the same one-paragraph altitude as this
   document's other responsibilities, not a restatement of operator
   manual section 6 or 7.
3. Mention that the closed `simple@v1`, `direct@v1`, and `robust@v1`
   workflow programs are implemented, at the one-sentence-each altitude
   described above.
4. Make implemented-versus-planned status legible wherever this
   document now touches it (adapters, Front Door, workflow programs),
   so a fresh reader cannot mistake a planned capability (e.g. the
   pip-installable Front Door target) for current fact.

Do not restructure the document's existing section order or prose
style beyond what these four corrections require; do not add new
sections unrelated to them; do not duplicate `docs/backlog/_open.adoc`'s
gap-by-gap acceptance detail (e.g. `direct@v1`'s named implementation
gaps, `robust@v1`'s test-matrix coverage gap) into this architecture
summary -- state only that the programs are implemented, and continue
pointing elsewhere (as the document already does) for active-work
detail; do not change the document's closing pointers to
`README.adoc[the standalone authority index]` or
`backlog/_open.adoc[the active-work manifest]` beyond what is strictly
necessary for them to remain accurate.


## Acceptance

`docs/architecture.md` remains a Markdown file at its current path.
Every existing architecture decision and invariant it previously stated
(A3 contract loading, one-WorkItem-one-repository, the Runtime-N/
Development-N+1 self-development topology and its push/promotion
ownership, the no-containment-boundary disclaimer, its closing pointers
to the authority index and active-work manifest) is still present and
still correct. It no longer describes the execution port as having a
singular "Codex CLI adapter"; it accurately reflects that both
`codex_cli` and `claude_cli` are implemented adapters. It describes
`commissioning.py`'s Front-Door/Commission-rendering role. It mentions
that `simple@v1`, `direct@v1`, and `robust@v1` are implemented workflow
programs. Nothing in the corrected document claims the pip-installable,
consumer-neutral Front Door target, or any other still-planned
capability, is already current fact. No file other than
`docs/architecture.md` is changed. The Coordinator-owned `task-check`
evidence command (`python -m pytest -q`) passes.


## Withheld authority

No edit to any file other than `docs/architecture.md`. No production-
code change of any kind (this Commission corrects documentation only;
it never changes what the code actually does). No architectural
redesign, no new invariant, no retraction of an existing invariant, and
no reinterpretation of self-development topology, admission, or trust
boundaries -- only accurate description of what already exists. No
document-format conversion (the file stays Markdown; do not convert it
to AsciiDoc or restructure it into the `Maturity`/`Authority`/`Owns`
metadata model those apply elsewhere). No unrelated documentation
restructuring, no new document, and no change to this document's
classification in `docs/README.adoc` (which this Commission does not
touch). No claim that any still-planned capability (the pip-installable
Front Door, `direct@v1`'s or `robust@v1`'s remaining acceptance gaps,
any unimplemented adapter) is already delivered. No successor
Commission, WorkItem, Profile, or Policy authored or implied.


## Default escalation

Stop and report `outcome: blocked` or `outcome: failed` (do not guess
or proceed) if: accurately fixing the four named problems would require
editing a file other than `docs/architecture.md`; the current source
(`operator.py`, `commissioning.py`, the three workflow-program YAML
files) does not actually support one of the four corrections as
described; or `docs/backlog/_open.adoc`'s implemented-versus-planned
status for any item this task touches is themselves ambiguous or
internally inconsistent in a way this task cannot resolve by reading
the current source directly.


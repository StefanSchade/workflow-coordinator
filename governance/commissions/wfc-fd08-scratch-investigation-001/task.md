# Investigate WFC temporary/scratch storage handling (FD-08) and propose a bounded improvement

Investigate how the Workflow Coordinator (WFC) uses temporary and
scratch storage, and propose the smallest reliable improvement that
prevents unnecessary exhaustion of the host's default /tmp.

Background: a WFC self-development Run on 2026-10-28 encountered /tmp
tmpfs exhaustion. The incident is already documented in
docs/warm_storage/incidents/2026-10-28_tmpfs_exhaustion.adoc, and the
resulting FD-08 candidate requirement is recorded in
docs/warm_storage/concepts/workflow_coordinator_front_door_priority_and_profile_commission_boundary_plan_2026-10-26.adoc
section 9. Read both, and docs/backlog/BUG-WFC-PYTEST-TEMP-RESOURCE-EXHAUSTION.adoc,
before drawing conclusions.

This investigation must NOT implement FD-08.

Required technical investigation:

1. Trace scripts/wfc_op.py preparation and launch paths through the
   operator, harness, Codex CLI adapter, trusted evidence runner, and
   subprocess invocation.
2. Identify uses of Python's tempfile, explicit temporary-directory
   creation, and relevant filesystem paths.
3. Examine propagation and overrides of TMPDIR, TMP, and TEMP, including
   across subprocesses and prepared virtual environments.
4. Distinguish temporary storage consumed by: the agent/provider CLI;
   coordinator-owned trusted evidence and pytest; dependency/bootstrap
   operations; and persistent Run state, logs, and durable evidence.
5. Identify which processes WFC can reliably control and which may
   ignore or override TMPDIR.
6. Examine path validation, ownership, permissions, symlinks, cleanup,
   persistence, and exhaustion behavior.
7. Evaluate whether a per-Attempt scratch directory outside the
   Development, Runtime, and Evidence repositories is sufficient.
8. Identify how such a directory could be configured and injected with
   minimal changes to existing mechanisms.
9. Preserve A1 scope restrictions, executable fingerprinting, evidence
   provenance, and the boundary between untrusted agent output and
   coordinator-owned trusted evidence.

Avoid inventing a general scratch-management framework.

Produce exactly one AsciiDoc report at
docs/warm_storage/concepts/workflow_coordinator_fd08_scratch_investigation_2026-10-30.adoc
containing: a problem statement and incident references; verified
observations with source file/function references; a map of relevant
process boundaries and environment propagation; what the proposed
solution can and cannot prevent; a minimal recommended implementation
design; a candidate CLI/configuration surface; security and lifecycle
implications; focused regression and integration tests; a proposed
bounded implementation WorkItem (including candidate changed files and
acceptance criteria); and open decisions reserved for maintainer
authority. Clearly separate confirmed facts, hypotheses,
recommendations, and unresolved questions. If necessary information
cannot be established without an experiment or a privileged operation,
record the uncertainty rather than performing an unauthorized action.


## Acceptance

The report at
docs/warm_storage/concepts/workflow_coordinator_fd08_scratch_investigation_2026-10-30.adoc
accurately traces the relevant code and subprocess paths; identifies
relevant temporary-storage consumers and environment boundaries; avoids
unsupported claims about Codex CLI or other external process behavior;
distinguishes scratch from persisted state and trusted evidence;
presents a technically feasible minimal solution; explains safety,
cleanup, and failure behavior; provides a bounded follow-up
implementation proposal; preserves previously accepted trust boundaries;
renders as valid AsciiDoc; and contains no changes outside its
authorized report path. No other file changes.


## Withheld authority

No production-code, test, governance, policy, runtime, or
evidence-repository modifications. No package installation, runtime
configuration changes, privileged filesystem operations, changing /tmp
mounts, cleanup of active scratch/state, nested WFC launches, external
pushes, or implementation of FD-08. No commitment to a new architecture
or new program version. Investigate and recommend only; implementation
requires separate maintainer authorization.


## Default escalation

Stop and request maintainer authority if the required report cannot be
completed within the authorized scope and withheld authority above, or
if required information cannot be established without an experiment or
a privileged operation; record the uncertainty in the report rather than
performing an unauthorized action.


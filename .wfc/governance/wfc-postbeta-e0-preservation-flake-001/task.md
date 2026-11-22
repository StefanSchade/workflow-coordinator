# E0: make the candidate-preservation idempotency test deterministic (root cause)

FIND AND FIX THE ROOT CAUSE of an intermittently failing required-suite test, so the required test suite is deterministic (a release gate must not flake). The test is tests/unit/test_candidate_preservation.py::test_repeat_capture_is_idempotent_no_second_ref; the function under test is workflow_coordinator.git_facts.preserve_candidate (around lines 960-1130 of src/workflow_coordinator/git_facts.py).
OBSERVED (Operator, 2026-11-22): the test fails intermittently (1 of 6 single-file runs; 2 of 40 in the standalone reproduction below). Failure: the SECOND preserve_candidate call returns status "not_needed" (ref None) although the first call returned "preserved" and the working tree is unchanged between the two calls. The fixture commits scope/a.txt = "v1\n" and the test immediately overwrites it with "v2\n" (same size, same second). With a longer replacement ("version-two-longer\n") 0 of 40 runs failed. HYPOTHESIS (NOT verified; verify or refute it): Git's racy-stat handling for a same-size edit made within the index timestamp granularity, interacting with the copied index that preserve_candidate uses for `git status`. The four unexplained red baseline probes seen earlier in this lifecycle are consistent with this flake but unproven; do not claim a link.
STANDALONE REPRODUCTION (run it from the repository root with the project's Python; it creates temporary repositories under /tmp, change the directory if you need to; the argument `same` reproduces, `longer` does not):
import sys, subprocess, tempfile, pathlib, shutil
sys.path.insert(0,'src')
from workflow_coordinator import git_facts as GF
def git(r,*a): return subprocess.run(['git',*a],cwd=r,check=True,capture_output=True,text=True).stdout.strip()
size = sys.argv[1]
bad=0
for i in range(40):
    d=pathlib.Path(tempfile.mkdtemp(dir='/tmp'))
    git(d,'init','-q'); git(d,'config','user.email','a@b.invalid'); git(d,'config','user.name','t')
    (d/'scope').mkdir(); (d/'scope'/'a.txt').write_text('v1\n')
    git(d,'add','.'); git(d,'commit','-q','-m','seed')
    base=git(d,'rev-parse','HEAD')
    (d/'scope'/'a.txt').write_text('v2\n' if size=='same' else 'version-two-longer\n')
    a=GF.preserve_candidate(d,expected_repository=d,base_head=base,allowed_paths=('scope',),run_id='R')
    b=GF.preserve_candidate(d,expected_repository=d,base_head=base,allowed_paths=('scope',),run_id='R')
    if a.status!='preserved' or b.ref!=a.ref: bad+=1; print(i,a.status,b.status)
    shutil.rmtree(d)
print(size,'bad',bad,'of 40')

YOUR TASK, in this order: (1) Reproduce the failure deterministically or with a measured failure rate and record the mechanism you can actually demonstrate (which Git behavior, which call, which state). (2) Decide, with that evidence, whether this is (a) only a racy test fixture or (b) a real defect of preserve_candidate that can report "not_needed" for a real, unpreserved change (a safety-relevant outcome: a candidate worth preserving would be treated as absent). (3) Fix the ROOT CAUSE: for (a) make the fixture or assertion deterministic without weakening what the test proves (do not merely change the content size and hope; remove the timing dependence or make it explicit); for (b) fix the product function minimally, keep every existing behavior and refusal, and add a deterministic regression test that fails before and passes after. Either way the test must not depend on wall-clock timing luck. (4) Show the stability evidence: the single test and the whole file repeated at least 40 times with zero failures after the fix (loop the pytest command; report counts), the reproduction script, and the full required suite `python -m pytest -q` green. Do NOT add retries, sleeps used as a flake bandage, skip markers or xfail. Do not change any other test or product behavior. Heavy-task note: this touches a trust-boundary function if (b) holds; keep the change small and explain it. A Worker never reports any Finding operation (resolve, residual or any other delta); only the independent reviewer opens and resolves Findings. The CommitIntent names every changed leaf individually, never directories, as a JSON array of plain repository-relative paths, one per element; no agent Git writes (no git add, mv, rm, reset, stash, commit); the index stays untouched.

## Acceptance

The mechanism of the intermittent failure is demonstrated and stated; the Worker states whether preserve_candidate itself can mis-report a real change as not_needed; the root cause is fixed (fixture determinism, or a minimal product fix with a deterministic regression test that fails before and passes after); no retry, sleep bandage, skip or xfail was added; the test and the whole test file ran at least 40 times with zero failures after the fix with the counts reported; the full required suite is green; no file other than the named scope changed; independently accepted with no blocking Findings and Coordinator task-check passed.

## Withheld authority

No change to any file outside tests/unit/test_candidate_preservation.py, src/workflow_coordinator/git_facts.py (only if a product defect is proven) and the two review artifacts; no weakening of any assertion or refusal; no other behavior change; no push, tag, Runtime promotion or external action.

## Default escalation

Escalate a defect whose minimal fix would change an accepted contract or a documented guarantee, or evidence that the failure comes from somewhere other than this function; incomplete work alone is not blocked.

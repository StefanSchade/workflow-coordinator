"""Focused tests for the Harness v0 operator tool (`scripts/wfc_op.py`).

These tests invoke `wfc_op.py` the same way a maintainer would: as a
subprocess, with `HOME` redirected into a temporary root so OPS/STATE never
touch the real `$HOME/wfc-ops` / `$HOME/wfc-state`.  Development/Runtime are
disposable local clones of this very repository (`git clone --local`, so no
network and fast via hardlinks); Evidence is a throwaway Git repository.  No
real provider is ever invoked: the `--executable` fixture is a tiny fake
script, exactly like `tests/unit/test_operator.py`'s own `fake()` pattern.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WFC_OP = REPO_ROOT / "scripts" / "wfc_op.py"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(repo), *args), text=True, capture_output=True, check=True,
    )
    return result.stdout.strip()


def git_ok(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(("git", "-C", str(repo), *args), text=True, capture_output=True)


def run_wfc_op(home: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        (sys.executable, str(WFC_OP), *args), text=True, capture_output=True, env=env,
    )


FIXTURE_WORKER_PROFILE = """schema_version: 2
profile_id: fixture
revision: "1"
phases:
  work:
    role: worker
    components:
      role: [worker]
      modifiers: []
      repository_policy: []
    durable_artifact: none
    authorized_scope: [PROOF.md]
    authorized_actions: [edit, commit]
    human_gates: []
    evidence: []
    review: null
    routes:
      done:
        effect: complete
"""

FIXTURE_WORK_ITEM = """schema_version: 2
work_item_id: WI-HARNESS-FIXTURE
authority_source: fixture
profile_ref: profile.yml
repository: repo
scope: [PROOF.md]
initial_phase: work
max_autonomous_hops: 1
personas:
  worker: fixture worker
"""

FIXTURE_POLICY = """schema_version: 3
policy_id: fixture
revision: "1"
scope: [repo]
governance_paths: [work.yml, profile.yml, policy.yml, components.yml, task.txt, worker.txt]
work_item_scope_ceiling: [PROOF.md]
max_autonomous_hops: 5
actions: [edit, commit]
evidence_commands:
  local:
    argv: [git, --version]
    timeout_seconds: 10
"""

FIXTURE_COMPONENTS = """schema_version: 1
components:
  worker:
    path: worker.txt
"""


def _clone_local(source: Path, destination: Path) -> Path:
    subprocess.run(
        ("git", "clone", "--local", "--no-hardlinks", "--quiet", str(source), str(destination)),
        check=True,
    )
    for key, value in (("user.email", "harness-fixture@example.invalid"), ("user.name", "Harness Fixture")):
        subprocess.run(("git", "-C", str(destination), "config", key, value), check=True)
    return destination


def make_development(tmp_path_factory) -> Path:
    """A disposable local clone of this repository, plus one extra commit
    adding a minimal governing-input set (mirrors tests/unit/test_operator.py's
    own `repository` fixture) so the canonical preflight seam has something
    real to admit.
    """

    destination = tmp_path_factory.mktemp("development")
    _clone_local(REPO_ROOT, destination)
    (destination / "PROOF.md").write_text("before\n")
    (destination / "worker.txt").write_text("worker guidance\n")
    (destination / "task.txt").write_text("pinned task\n")
    (destination / "work.yml").write_text(FIXTURE_WORK_ITEM)
    (destination / "policy.yml").write_text(FIXTURE_POLICY)
    (destination / "profile.yml").write_text(FIXTURE_WORKER_PROFILE)
    (destination / "components.yml").write_text(FIXTURE_COMPONENTS)
    git(destination, "add", ".")
    git(destination, "commit", "--quiet", "-m", "test: Harness v0 fixture governing inputs")
    return destination


def _detach_as_runtime(repo: Path) -> None:
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "--quiet", "--detach", head)
    subprocess.run(("git", "-C", str(repo), "config", "remote.origin.pushurl", "DISABLED"), check=True)


def make_runtime(tmp_path_factory) -> Path:
    """A disposable local clone of this repository, detached with push disabled."""

    destination = tmp_path_factory.mktemp("runtime")
    _clone_local(REPO_ROOT, destination)
    _detach_as_runtime(destination)
    return destination


SENTINEL_MESSAGE = "HARNESS-TEST-RUNTIME-SENTINEL: this is the pinned Runtime's own code"


def make_sentinel_runtime(tmp_path_factory) -> Path:
    """A disposable local clone whose own `_preflight()` is patched to refuse
    unconditionally with a distinctive message. Used to prove the compatibility
    seam executes the PINNED RUNTIME's code under its own interpreter, never
    Development's copy or a Harness reimplementation: only this patched copy
    can produce SENTINEL_MESSAGE.
    """

    destination = tmp_path_factory.mktemp("sentinel-runtime")
    _clone_local(REPO_ROOT, destination)
    operator_py = destination / "src" / "workflow_coordinator" / "operator.py"
    original = operator_py.read_text(encoding="utf-8")
    marker = "def _preflight(\n    args: argparse.Namespace,\n    *,\n    evidence_runner: EvidenceRunner = FAIL_CLOSED_EVIDENCE_RUNNER,\n):\n"
    assert marker in original, "operator._preflight() signature moved; update the test sentinel patch"
    patched = original.replace(
        marker,
        marker + f'    raise OperatorPreflightError({SENTINEL_MESSAGE!r})\n',
        1,
    )
    assert patched != original
    operator_py.write_text(patched, encoding="utf-8")
    git(destination, "add", "-A")
    git(destination, "commit", "--quiet", "-m", "test: Harness v0 sentinel-patched _preflight()")
    _detach_as_runtime(destination)
    return destination


def make_evidence(tmp_path_factory) -> Path:
    destination = tmp_path_factory.mktemp("evidence")
    destination.mkdir(exist_ok=True)
    subprocess.run(("git", "init", "--quiet", str(destination)), check=True)
    for key, value in (("user.email", "harness-fixture@example.invalid"), ("user.name", "Harness Fixture")):
        subprocess.run(("git", "-C", str(destination), "config", key, value), check=True)
    (destination / "README.md").write_text("fixture evidence repository\n")
    git(destination, "add", ".")
    git(destination, "commit", "--quiet", "-m", "test: fixture evidence baseline")
    return destination


def fake_executable(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "fake-provider"
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(0o755)
    return path


ONE_HOP_FAKE_BODY = textwrap.dedent('''
    import json, pathlib, sys
    args = sys.argv[1:]
    repo = pathlib.Path(args[args.index("--cd") + 1])
    output = pathlib.Path(args[args.index("--output-last-message") + 1])
    prompt = sys.stdin.read()

    def fact(name):
        return prompt.split(name + "=", 1)[1].split("\\n", 1)[0]

    repo.joinpath("PROOF.md").write_text("after\\n")
    result = {
        "schema_version": 1, "status": "completed", "requested_route": "done",
        "scope_changed": False, "requires_human": False, "escalation": None,
        "findings": [], "claimed_commits": [],
        "commit_intent": {"paths": ["PROOF.md"], "subject": "docs(workflow): WI-HARNESS-FIXTURE H001 fixture"},
        "evidence_refs": [], "summary": "fixture",
    }
    output.write_text(json.dumps({
        "schema_version": 1, "run_id": fact("run_id"), "hop_id": fact("hop_id"),
        "invocation_id": fact("invocation_id"), "result": result,
    }))
''')


def prepare_argv(*, attempt: str, development: Path, runtime: Path, evidence: Path, executable: str) -> list[str]:
    return [
        "prepare",
        "--attempt", attempt,
        "--development", str(development),
        "--runtime", str(runtime),
        "--evidence", str(evidence),
        "--work-item", "work.yml",
        "--profile", "profile.yml",
        "--policy", "policy.yml",
        "--components", "components.yml",
        "--task", "task.txt",
        "--executable", executable,
        "--timeout-seconds", "5",
        "--role-binding", "worker=codex_cli:fixture-worker:medium",
    ]


# --------------------------------------------------------------- unit-level

def test_attempt_id_rejects_implausible_forms():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import wfc_op
        for bad in ("", " x", "x ", ".", "..", "-x", "a/b", "a\\b", "a\tb"):
            with pytest.raises(wfc_op.HarnessRefusal):
                wfc_op.validate_attempt(bad)
        assert wfc_op.validate_attempt("wi-fixture-20261007-01") == "wi-fixture-20261007-01"
    finally:
        sys.path.remove(str(REPO_ROOT / "scripts"))


# -------------------------------------------------------- fixtures (session)

@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return {
        "development": make_development(tmp_path_factory),
        "runtime": make_runtime(tmp_path_factory),
        "sentinel_runtime": make_sentinel_runtime(tmp_path_factory),
        "evidence": make_evidence(tmp_path_factory),
    }


@pytest.fixture(scope="module")
def prepared(world, tmp_path_factory):
    """One full, real `prepare` call (expensive: builds two real venvs and
    installs this package into both). Reused read-only by every LAUNCH test;
    each perturbation is undone by the test that makes it.
    """

    home = tmp_path_factory.mktemp("prepared-home")
    tmp_path = tmp_path_factory.mktemp("prepared-fake")
    executable = fake_executable(tmp_path, ONE_HOP_FAKE_BODY)
    attempt = "wi-harness-fixture-prepared-01"
    result = run_wfc_op(
        home,
        *prepare_argv(
            attempt=attempt, development=world["development"], runtime=world["runtime"],
            evidence=world["evidence"], executable=str(executable),
        ),
    )
    assert result.returncode == 0, result.stderr
    return {
        "home": home,
        "attempt": attempt,
        "ops": home / "wfc-ops" / attempt,
        "state": home / "wfc-state" / attempt,
        "result": result,
        **world,
    }


# ------------------------------------------------------------- PREPARE positive

def test_prepare_positive_produces_complete_envelope_and_leaves_state_absent(prepared):
    ops, state, result = prepared["ops"], prepared["state"], prepared["result"]
    assert "PREPARED / NOT LAUNCHED" in result.stdout
    assert not state.exists(), "STATE must remain absent after prepare"
    assert (ops / "execution-bindings.yaml").is_file()
    assert (ops / "preparation-pins.txt").is_file()
    assert (ops / "launch.sh").is_file()
    assert (ops / "harness-prepared.json").is_file()
    assert (ops / "runtime-env" / "bin" / "wfc").is_file()
    assert (ops / "dev-env" / "bin" / "python3").is_file()

    pins = (ops / "preparation-pins.txt").read_text()
    assert f"attempt_id={prepared['attempt']}" in pins
    assert f"state_root={state}" in pins
    dev_head = git(prepared["development"], "rev-parse", "HEAD")
    runtime_head = git(prepared["runtime"], "rev-parse", "HEAD")
    assert f"development_head={dev_head}" in pins
    assert f"runtime_head={runtime_head}" in pins

    record = json.loads((ops / "harness-prepared.json").read_text())
    assert record["prepared"] is True
    assert record["preflight_seam"] == "ok"
    assert record["development"]["head"] == dev_head
    assert record["runtime"]["head"] == runtime_head

    bindings = (ops / "execution-bindings.yaml").read_text()
    assert "adapter: codex_cli" in bindings and "model: fixture-worker" in bindings

    # The controlled PATH is frozen as a literal value during prepare; launch.sh
    # must never re-expand a future launcher's own $PATH.
    launch_sh_text = (ops / "launch.sh").read_text()
    assert "$PATH" not in launch_sh_text
    assert record["controlled_path"] in launch_sh_text

    # Development/Runtime/Evidence themselves are unchanged.
    assert git(prepared["development"], "status", "--porcelain") == ""
    assert git(prepared["runtime"], "status", "--porcelain") == ""
    assert git(prepared["evidence"], "status", "--porcelain") == ""


def test_prepare_never_creates_a_run_or_checkout(prepared):
    ops = prepared["ops"]
    assert not list(ops.glob("**/run.json"))
    assert not list(ops.glob("**/operator-plan.json"))
    assert not list(ops.glob("**/operator-summary.json"))


# ------------------------------------------------------------- PREPARE refusals

@pytest.fixture
def fresh_attempt_name():
    import uuid
    return f"wi-harness-refusal-{uuid.uuid4().hex[:8]}"


def test_prepare_refuses_dirty_development(world, tmp_path_factory, fresh_attempt_name):
    home = tmp_path_factory.mktemp("home")
    (world["development"] / "untracked-dirt.txt").write_text("dirt\n")
    try:
        result = run_wfc_op(
            home,
            *prepare_argv(
                attempt=fresh_attempt_name, development=world["development"], runtime=world["runtime"],
                evidence=world["evidence"], executable="codex",
            ),
        )
        assert result.returncode == 2
        assert "Development" in result.stderr and "not clean" in result.stderr
        assert not (home / "wfc-ops" / fresh_attempt_name).exists()
    finally:
        (world["development"] / "untracked-dirt.txt").unlink()


def test_prepare_refuses_dirty_runtime(world, tmp_path_factory, fresh_attempt_name):
    home = tmp_path_factory.mktemp("home")
    (world["runtime"] / "untracked-dirt.txt").write_text("dirt\n")
    try:
        result = run_wfc_op(
            home,
            *prepare_argv(
                attempt=fresh_attempt_name, development=world["development"], runtime=world["runtime"],
                evidence=world["evidence"], executable="codex",
            ),
        )
        assert result.returncode == 2
        assert "Runtime" in result.stderr and "not clean" in result.stderr
    finally:
        (world["runtime"] / "untracked-dirt.txt").unlink()


def test_prepare_refuses_attached_runtime(world, tmp_path_factory, fresh_attempt_name):
    home = tmp_path_factory.mktemp("home")
    git(world["runtime"], "checkout", "--quiet", "-B", "not-detached-for-test")
    try:
        result = run_wfc_op(
            home,
            *prepare_argv(
                attempt=fresh_attempt_name, development=world["development"], runtime=world["runtime"],
                evidence=world["evidence"], executable="codex",
            ),
        )
        assert result.returncode == 2
        assert "not detached" in result.stderr
    finally:
        head = git(world["runtime"], "rev-parse", "HEAD")
        git(world["runtime"], "checkout", "--quiet", "--detach", head)
        git(world["runtime"], "branch", "-D", "not-detached-for-test")


def test_prepare_refuses_runtime_with_pushurl_not_disabled(world, tmp_path_factory, fresh_attempt_name):
    home = tmp_path_factory.mktemp("home")
    subprocess.run(("git", "-C", str(world["runtime"]), "config", "remote.origin.pushurl", "https://example.invalid/not-disabled"), check=True)
    try:
        result = run_wfc_op(
            home,
            *prepare_argv(
                attempt=fresh_attempt_name, development=world["development"], runtime=world["runtime"],
                evidence=world["evidence"], executable="codex",
            ),
        )
        assert result.returncode == 2
        assert "pushurl" in result.stderr and "DISABLED" in result.stderr
    finally:
        subprocess.run(("git", "-C", str(world["runtime"]), "config", "remote.origin.pushurl", "DISABLED"), check=True)


def test_prepare_refuses_dirty_evidence(world, tmp_path_factory, fresh_attempt_name):
    home = tmp_path_factory.mktemp("home")
    (world["evidence"] / "untracked-dirt.txt").write_text("dirt\n")
    try:
        result = run_wfc_op(
            home,
            *prepare_argv(
                attempt=fresh_attempt_name, development=world["development"], runtime=world["runtime"],
                evidence=world["evidence"], executable="codex",
            ),
        )
        assert result.returncode == 2
        assert "Evidence" in result.stderr and "not clean" in result.stderr
    finally:
        (world["evidence"] / "untracked-dirt.txt").unlink()


def test_prepare_refuses_existing_ops(world, tmp_path_factory, fresh_attempt_name):
    home = tmp_path_factory.mktemp("home")
    ops = home / "wfc-ops" / fresh_attempt_name
    ops.mkdir(parents=True)
    result = run_wfc_op(
        home,
        *prepare_argv(
            attempt=fresh_attempt_name, development=world["development"], runtime=world["runtime"],
            evidence=world["evidence"], executable="codex",
        ),
    )
    assert result.returncode == 2
    assert "OPS already exists" in result.stderr


def test_prepare_refuses_existing_state(world, tmp_path_factory, fresh_attempt_name):
    home = tmp_path_factory.mktemp("home")
    state = home / "wfc-state" / fresh_attempt_name
    state.mkdir(parents=True)
    result = run_wfc_op(
        home,
        *prepare_argv(
            attempt=fresh_attempt_name, development=world["development"], runtime=world["runtime"],
            evidence=world["evidence"], executable="codex",
        ),
    )
    assert result.returncode == 2
    assert "STATE already exists" in result.stderr
    assert not (home / "wfc-ops" / fresh_attempt_name).exists()


def test_prepare_refuses_missing_governing_input(world, tmp_path_factory, fresh_attempt_name):
    home = tmp_path_factory.mktemp("home")
    argv = prepare_argv(
        attempt=fresh_attempt_name, development=world["development"], runtime=world["runtime"],
        evidence=world["evidence"], executable="codex",
    )
    argv[argv.index("--task") + 1] = "does-not-exist.txt"
    result = run_wfc_op(home, *argv)
    assert result.returncode == 2
    assert "does not exist beneath Development" in result.stderr
    assert not (home / "wfc-ops" / fresh_attempt_name).exists()


# ------------------------------------------------------ FINAL PREPARE REVALIDATION

def test_prepare_final_revalidation_refuses_on_drift_during_preparation(world, tmp_path_factory, fresh_attempt_name, monkeypatch):
    """H0-1 polish: drift injected AFTER the initial pin (development/runtime/
    evidence HEAD captured at the top of `cmd_prepare`) but BEFORE the final
    revalidation this slice adds must still refuse, never report PREPARED.

    Injects real Git drift (an actual commit) via a monkeypatched
    `write_launch_sh` -- one of the last mechanical steps before final
    revalidation -- so the whole real pipeline (venvs, preflight) runs
    exactly as in `prepared`, and only the final check catches the drift.
    """

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        import wfc_op

        home = tmp_path_factory.mktemp("home-drift")
        tmp_path = tmp_path_factory.mktemp("drift-fake")
        executable = fake_executable(tmp_path, ONE_HOP_FAKE_BODY)
        development = world["development"]
        pre_head = git(development, "rev-parse", "HEAD")

        original_write_launch_sh = wfc_op.write_launch_sh

        def drifting_write_launch_sh(*args, **kwargs):
            result = original_write_launch_sh(*args, **kwargs)
            (development / "mid-prepare-drift.txt").write_text("drift\n")
            git(development, "add", "mid-prepare-drift.txt")
            git(development, "commit", "--quiet", "-m", "test: simulate Development drift mid-preparation")
            return result

        monkeypatch.setattr(wfc_op, "write_launch_sh", drifting_write_launch_sh)
        monkeypatch.setenv("HOME", str(home))

        args = wfc_op.build_parser().parse_args(prepare_argv(
            attempt=fresh_attempt_name, development=development, runtime=world["runtime"],
            evidence=world["evidence"], executable=str(executable),
        ))
        try:
            with pytest.raises(wfc_op.HarnessRefusal, match="Development") as excinfo:
                wfc_op.cmd_prepare(args)
            assert "HEAD drift" in str(excinfo.value)
        finally:
            git(development, "reset", "--quiet", "--hard", pre_head)

        state = home / "wfc-state" / fresh_attempt_name
        assert not state.exists(), "no STATE may be created when final revalidation refuses"
    finally:
        sys.path.remove(str(REPO_ROOT / "scripts"))


# ---------------------------------------------------------------- PREFLIGHT SEAM

def test_canonical_preflight_refusal_propagates_and_proves_pinned_runtime_code(world, tmp_path_factory, fresh_attempt_name):
    """The seam must execute the PINNED Runtime's own `_preflight()`, not
    Development's copy and not a Harness reimplementation: only the
    sentinel-patched Runtime clone can produce SENTINEL_MESSAGE. This doubles
    as the "canonical preflight refusal" PREPARE-refusal case.
    """

    home = tmp_path_factory.mktemp("home")
    result = run_wfc_op(
        home,
        *prepare_argv(
            attempt=fresh_attempt_name, development=world["development"], runtime=world["sentinel_runtime"],
            evidence=world["evidence"], executable="codex",
        ),
    )
    assert result.returncode == 2
    assert SENTINEL_MESSAGE in result.stderr
    assert "canonical provider-free preflight refused" in result.stderr

    state = home / "wfc-state" / fresh_attempt_name
    ops = home / "wfc-ops" / fresh_attempt_name
    assert not state.exists(), "no STATE may be created when preflight refuses"
    assert not (ops / "launch.sh").exists(), "no launch artifact when preflight refuses"
    assert not (ops / "harness-prepared.json").exists()
    # No provider-capable side effect: Development/Evidence remain untouched.
    assert git(world["development"], "status", "--porcelain") == ""
    assert git(world["evidence"], "status", "--porcelain") == ""


# ------------------------------------------------------------------------ STATUS
#
# `status` must be run here, between PREPARE and LAUNCH in file order: the
# shared `prepared` fixture's STATE is absent until the LAUNCH section below
# launches it, so this is the only place a "PREPARED / NOT LAUNCHED" status
# read is guaranteed accurate against it.

@pytest.fixture(scope="module")
def completed_run(world, tmp_path_factory):
    """One real prepare+launch, independent of `prepared`, used only by the
    STATUS tests below. Uses its OWN Development clone (module-scoped
    teardown only runs at the very end of the module, long after the LAUNCH
    section has already mutated and reset `world["development"]`, so a real
    Hop commit here must never land on the shared clone those tests depend
    on). Runtime/Evidence are never mutated by an ordinary launch, so they
    are safely shared with `world`.
    """

    development = make_development(tmp_path_factory)
    home = tmp_path_factory.mktemp("completed-home")
    tmp_path = tmp_path_factory.mktemp("completed-fake")
    executable = fake_executable(tmp_path, ONE_HOP_FAKE_BODY)
    attempt = "wi-harness-fixture-completed-01"
    result = run_wfc_op(
        home,
        *prepare_argv(
            attempt=attempt, development=development, runtime=world["runtime"],
            evidence=world["evidence"], executable=str(executable),
        ),
    )
    assert result.returncode == 0, result.stderr
    launch_result = run_wfc_op(home, "launch", attempt)
    assert launch_result.returncode == 0, (launch_result.stdout, launch_result.stderr)
    yield {
        "home": home, "attempt": attempt,
        "ops": home / "wfc-ops" / attempt, "state": home / "wfc-state" / attempt,
        "development": development, "runtime": world["runtime"], "evidence": world["evidence"],
    }


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_status_unknown_attempt(tmp_path_factory, fresh_attempt_name):
    home = tmp_path_factory.mktemp("home")
    result = run_wfc_op(home, "status", fresh_attempt_name)
    assert result.returncode == 0, result.stderr
    assert "UNKNOWN_ATTEMPT" in result.stdout
    assert not (home / "wfc-ops" / fresh_attempt_name).exists()
    assert not (home / "wfc-state" / fresh_attempt_name).exists()


def test_status_prepared_not_launched(prepared):
    assert not prepared["state"].exists(), "must run before the LAUNCH section mutates this fixture"
    result = run_wfc_op(prepared["home"], "status", prepared["attempt"])
    assert result.returncode == 0, result.stderr
    assert "PREPARED_NOT_LAUNCHED" in result.stdout
    assert "state_present: False" in result.stdout
    dev_head = git(prepared["development"], "rev-parse", "HEAD")
    assert f"prepared_development_head: {dev_head}" in result.stdout
    assert "run_id" not in result.stdout
    assert not prepared["state"].exists(), "status must not create STATE"


def test_status_completed_run(completed_run):
    result = run_wfc_op(completed_run["home"], "status", completed_run["attempt"])
    assert result.returncode == 0, result.stderr
    assert "lifecycle: RUN_RECORDED" in result.stdout
    assert "state_present: True" in result.stdout
    summary = json.loads((completed_run["state"] / "operator-summary.json").read_text())
    assert f"run_id: {summary['run_id']}" in result.stdout
    assert f"work_item_id: {summary['work_item_id']}" in result.stdout
    assert "run_status: completed" in result.stdout
    assert f"hop_used: {summary['hop_used']}" in result.stdout
    assert f"hop_limit: {summary['hop_limit']}" in result.stdout
    assert "worktree_clean: True" in result.stdout
    run_json_path = next((completed_run["state"] / "checkouts").glob("*/run.json"))
    run_json = json.loads(run_json_path.read_text())
    assert f"current_phase: {run_json['phase']}" in result.stdout


def test_status_json_mode_projects_existing_facts_only(completed_run):
    result = run_wfc_op(completed_run["home"], "status", completed_run["attempt"], "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    summary = json.loads((completed_run["state"] / "operator-summary.json").read_text())
    assert payload["attempt"] == completed_run["attempt"]
    assert payload["run_id"] == summary["run_id"]
    assert payload["run_status"] == summary["run_status"]
    # --json is a small projection of EXISTING facts, not a new durable file.
    assert not (completed_run["state"] / "status.json").exists()
    assert not (completed_run["ops"] / "status.json").exists()


def test_status_is_read_only(completed_run):
    state, ops = completed_run["state"], completed_run["ops"]
    before_state = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in state.rglob("*.json")}
    tracked_ops_files = [
        ops / "preparation-pins.txt", ops / "harness-prepared.json",
        ops / "launch.sh", ops / "execution-bindings.yaml",
    ]
    before_ops = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in tracked_ops_files}
    dev_status_before = git(completed_run["development"], "status", "--porcelain")

    for args in (("status", completed_run["attempt"]), ("status", completed_run["attempt"], "--json")):
        result = run_wfc_op(completed_run["home"], *args)
        assert result.returncode == 0, result.stderr

    after_state = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in state.rglob("*.json")}
    after_ops = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in tracked_ops_files}
    assert before_state == after_state
    assert before_ops == after_ops
    assert git(completed_run["development"], "status", "--porcelain") == dev_status_before


def test_status_non_completed_persisted_state(tmp_path_factory, fresh_attempt_name):
    """Synthetic STATE fixture (no real Run engineered): proves `status`
    projects a non-completed Run's already-defined `operator._summary()`
    fields verbatim, without reinterpreting them into a new conclusion.
    """

    home = tmp_path_factory.mktemp("home")
    state = home / "wfc-state" / fresh_attempt_name
    state.mkdir(parents=True)
    _write_json(state / "operator-summary.json", {
        "schema_version": 2, "run_id": "RUN-SYNTH-0001", "work_item_id": "WI-HARNESS-FIXTURE",
        "run_status": "awaiting_human", "hop_used": 1, "hop_limit": 5,
        "stop_reason": "human_gate", "human_question": "approve scope widening?",
        "anomalies": ["dirty tracked state"], "final_head": "deadbeefcafef00d",
        "worktree_clean": False, "operator_error": None,
    })
    result = run_wfc_op(home, "status", fresh_attempt_name)
    assert result.returncode == 0, result.stderr
    assert "run_id: RUN-SYNTH-0001" in result.stdout
    assert "run_status: awaiting_human" in result.stdout
    assert "approve scope widening?" in result.stdout
    assert "dirty tracked state" in result.stdout
    assert json.loads((state / "operator-summary.json").read_text())["run_id"] == "RUN-SYNTH-0001"


def test_status_reports_corrupt_json_without_guessing(tmp_path_factory, fresh_attempt_name):
    home = tmp_path_factory.mktemp("home")
    state = home / "wfc-state" / fresh_attempt_name
    state.mkdir(parents=True)
    (state / "operator-summary.json").write_text("{ not valid json", encoding="utf-8")
    result = run_wfc_op(home, "status", fresh_attempt_name)
    assert result.returncode == 0, result.stderr
    assert "corrupt_sources" in result.stdout
    assert "operator-summary.json" in result.stdout
    assert "run_id" not in result.stdout


def test_status_surfaces_disagreeing_sources_without_resolving(tmp_path_factory, fresh_attempt_name):
    home = tmp_path_factory.mktemp("home")
    state = home / "wfc-state" / fresh_attempt_name
    checkout_dir = state / "checkouts" / "c1"
    checkout_dir.mkdir(parents=True)
    _write_json(state / "operator-summary.json", {
        "schema_version": 2, "run_id": "RUN-A", "work_item_id": "WI-HARNESS-FIXTURE",
        "run_status": "running", "hop_used": 1, "hop_limit": 5,
        "stop_reason": None, "human_question": None, "anomalies": [], "operator_error": None,
    })
    _write_json(checkout_dir / "run.json", {
        "schema_version": 2, "run_id": "RUN-B", "work_item_id": "WI-HARNESS-FIXTURE",
        "phase": "work", "status": "running", "hop_limit": 5, "hop_used": 1,
    })
    result = run_wfc_op(home, "status", fresh_attempt_name)
    assert result.returncode == 0, result.stderr
    assert "inconsistencies" in result.stdout
    assert "run_id disagrees" in result.stdout
    assert "RUN-A" in result.stdout and "RUN-B" in result.stdout
    # Never silently resolved: no single `run_id:` line was printed.
    assert "run_id: RUN-A" not in result.stdout and "run_id: RUN-B" not in result.stdout


# ------------------------------------------------------------------ LAUNCH positive

def test_launch_executes_prepared_artifact_exactly_once_with_fake_executable(prepared):
    state = prepared["state"]
    development = prepared["development"]
    assert not state.exists()
    pre_head = git(development, "rev-parse", "HEAD")
    try:
        result = run_wfc_op(prepared["home"], "launch", prepared["attempt"])
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert "LAUNCH RETURN CODE: 0" in result.stderr
        assert state.exists(), "the real pinned Runtime's own wfc creates STATE on launch"
        summary_path = next(state.glob("operator-summary.json"), None)
        assert summary_path is not None
        summary = json.loads(summary_path.read_text())
        assert summary["run_status"] == "completed"

        # Development really was edited (and trusted-committed) by the Hop.
        assert git(development, "rev-parse", "HEAD") != pre_head

        # Launching again must refuse: STATE already exists, and launch never retries.
        second = run_wfc_op(prepared["home"], "launch", prepared["attempt"])
        assert second.returncode == 2
        assert "STATE already exists" in second.stderr
    finally:
        if state.exists():
            shutil.rmtree(state)
        if git(development, "rev-parse", "HEAD") != pre_head:
            git(development, "reset", "--quiet", "--hard", pre_head)


# ------------------------------------------------------------------ LAUNCH drift

def test_launch_refuses_development_pin_drift(prepared):
    development = prepared["development"]
    (development / "drift-marker.txt").write_text("drift\n")
    git(development, "add", "drift-marker.txt")
    git(development, "commit", "--quiet", "-m", "test: simulate Development HEAD drift after prepare")
    try:
        result = run_wfc_op(prepared["home"], "launch", prepared["attempt"])
        assert result.returncode == 2
        assert "Development" in result.stderr and "HEAD drift" in result.stderr
        assert not prepared["state"].exists()
    finally:
        git(development, "reset", "--quiet", "--hard", "HEAD~1")


def test_launch_refuses_runtime_pin_drift(prepared):
    runtime = prepared["runtime"]
    original_head = git(runtime, "rev-parse", "HEAD")
    parent = git(runtime, "rev-parse", "HEAD~1")
    git(runtime, "checkout", "--quiet", "--detach", parent)
    subprocess.run(("git", "-C", str(runtime), "config", "remote.origin.pushurl", "DISABLED"), check=True)
    try:
        result = run_wfc_op(prepared["home"], "launch", prepared["attempt"])
        assert result.returncode == 2
        assert "Runtime" in result.stderr and "HEAD drift" in result.stderr
        assert not prepared["state"].exists()
    finally:
        git(runtime, "checkout", "--quiet", "--detach", original_head)
        subprocess.run(("git", "-C", str(runtime), "config", "remote.origin.pushurl", "DISABLED"), check=True)


def test_launch_refuses_runtime_attached_or_push_enabled(prepared):
    runtime = prepared["runtime"]
    git(runtime, "checkout", "--quiet", "-B", "attached-for-launch-test")
    try:
        result = run_wfc_op(prepared["home"], "launch", prepared["attempt"])
        assert result.returncode == 2
        assert "not detached" in result.stderr
        assert not prepared["state"].exists()
    finally:
        head = git(runtime, "rev-parse", "HEAD")
        git(runtime, "checkout", "--quiet", "--detach", head)
        git(runtime, "branch", "-D", "attached-for-launch-test")

    subprocess.run(("git", "-C", str(runtime), "config", "remote.origin.pushurl", "https://example.invalid/enabled"), check=True)
    try:
        result = run_wfc_op(prepared["home"], "launch", prepared["attempt"])
        assert result.returncode == 2
        assert "pushurl" in result.stderr and "DISABLED" in result.stderr
        assert not prepared["state"].exists()
    finally:
        subprocess.run(("git", "-C", str(runtime), "config", "remote.origin.pushurl", "DISABLED"), check=True)


def test_launch_refuses_evidence_pin_drift(prepared):
    evidence = prepared["evidence"]
    (evidence / "drift-marker.txt").write_text("drift\n")
    git(evidence, "add", "drift-marker.txt")
    git(evidence, "commit", "--quiet", "-m", "test: simulate Evidence HEAD drift after prepare")
    try:
        result = run_wfc_op(prepared["home"], "launch", prepared["attempt"])
        assert result.returncode == 2
        assert "Evidence" in result.stderr and "HEAD drift" in result.stderr
        assert not prepared["state"].exists()
    finally:
        git(evidence, "reset", "--quiet", "--hard", "HEAD~1")


def test_launch_refuses_when_state_already_present(prepared):
    state = prepared["state"]
    state.mkdir(parents=True)
    try:
        result = run_wfc_op(prepared["home"], "launch", prepared["attempt"])
        assert result.returncode == 2
        assert "STATE already exists" in result.stderr
    finally:
        state.rmdir()


def test_launch_refuses_corrupt_prepared_artifact(prepared):
    launch_sh = prepared["ops"] / "launch.sh"
    original = launch_sh.read_text()
    launch_sh.write_text(original + "\necho corrupted-after-prepare\n")
    try:
        result = run_wfc_op(prepared["home"], "launch", prepared["attempt"])
        assert result.returncode == 2
        assert "launch.sh differs from its prepared identity" in result.stderr
        assert not prepared["state"].exists()
    finally:
        launch_sh.write_text(original)


def test_launch_refuses_missing_ops(tmp_path_factory, fresh_attempt_name):
    home = tmp_path_factory.mktemp("home")
    result = run_wfc_op(home, "launch", fresh_attempt_name)
    assert result.returncode == 2
    assert "not prepared" in result.stderr


# ------------------------------------------------------- PATH-freeze regression

def _path_capturing_fake_body(capture: Path) -> str:
    return textwrap.dedent(f'''
        import json, os, pathlib, sys
        args = sys.argv[1:]
        repo = pathlib.Path(args[args.index("--cd") + 1])
        output = pathlib.Path(args[args.index("--output-last-message") + 1])
        prompt = sys.stdin.read()

        def fact(name):
            return prompt.split(name + "=", 1)[1].split("\\n", 1)[0]

        pathlib.Path({str(capture)!r}).write_text(
            "argv0=" + sys.argv[0] + "\\n" + "PATH=" + os.environ.get("PATH", "")
        )
        repo.joinpath("PROOF.md").write_text("after\\n")
        result = {{
            "schema_version": 1, "status": "completed", "requested_route": "done",
            "scope_changed": False, "requires_human": False, "escalation": None,
            "findings": [], "claimed_commits": [],
            "commit_intent": {{"paths": ["PROOF.md"], "subject": "docs(workflow): WI-HARNESS-FIXTURE H001 fixture"}},
            "evidence_refs": [], "summary": "fixture",
        }}
        output.write_text(json.dumps({{
            "schema_version": 1, "run_id": fact("run_id"), "hop_id": fact("hop_id"),
            "invocation_id": fact("invocation_id"), "result": result,
        }}))
    ''')


def test_launch_uses_the_frozen_prepared_path_not_a_later_ambient_path(world, tmp_path_factory, fresh_attempt_name):
    """DEFECT 1 regression: canonical preflight admits a concrete controlled
    PATH during `prepare`; a later `launch` from a shell with a materially
    different ambient PATH must still execute the pinned Runtime (and
    anything it spawns) under the exact PATH preflight admitted, never the
    launch-time ambient one.
    """

    base = tmp_path_factory.mktemp("path-freeze")
    capture = base / "observed.txt"
    executable = fake_executable(base, _path_capturing_fake_body(capture))

    marker_prepare = tmp_path_factory.mktemp("marker-prepare-time")
    marker_launch = tmp_path_factory.mktemp("marker-launch-time")
    ambient_base = os.environ.get("PATH", "")

    home = tmp_path_factory.mktemp("home")
    development = world["development"]
    pre_head = git(development, "rev-parse", "HEAD")
    try:
        prepare_env = {**os.environ, "HOME": str(home), "PATH": f"{marker_prepare}{os.pathsep}{ambient_base}"}
        result = subprocess.run(
            (sys.executable, str(WFC_OP), *prepare_argv(
                attempt=fresh_attempt_name, development=development, runtime=world["runtime"],
                evidence=world["evidence"], executable=str(executable),
            )),
            text=True, capture_output=True, env=prepare_env,
        )
        assert result.returncode == 0, result.stderr
        assert "PREPARED / NOT LAUNCHED" in result.stdout

        ops = home / "wfc-ops" / fresh_attempt_name
        record = json.loads((ops / "harness-prepared.json").read_text())
        assert str(marker_prepare) in record["controlled_path"]
        launch_sh_text = (ops / "launch.sh").read_text()
        assert "$PATH" not in launch_sh_text
        assert str(marker_prepare) in launch_sh_text

        launch_env = {**os.environ, "HOME": str(home), "PATH": f"{marker_launch}{os.pathsep}{ambient_base}"}
        launch_result = subprocess.run(
            (sys.executable, str(WFC_OP), "launch", fresh_attempt_name),
            text=True, capture_output=True, env=launch_env,
        )
        assert launch_result.returncode == 0, (launch_result.stdout, launch_result.stderr)

        observed = capture.read_text()
        assert f"argv0={executable}" in observed
        observed_path = observed.split("PATH=", 1)[1]
        assert str(marker_prepare) in observed_path, observed_path
        assert str(marker_launch) not in observed_path, observed_path
    finally:
        state = home / "wfc-state" / fresh_attempt_name
        if state.exists():
            shutil.rmtree(state)
        if git(development, "rev-parse", "HEAD") != pre_head:
            git(development, "reset", "--quiet", "--hard", pre_head)


# ---------------------------------------------------------- shell-quoting regression

def test_launch_sh_safely_quotes_shell_significant_paths(world, tmp_path_factory, fresh_attempt_name):
    """DEFECT 2 regression: the provider executable path contains shell
    metacharacters (spaces, `$`, backticks, a single quote). Ad-hoc
    double-quote interpolation would either break `launch.sh`'s syntax or let
    the shell reinterpret part of the path; robust quoting must execute the
    exact artifact/argument unchanged.

    HOME/OPS themselves stay on an ordinary path: hosting a venv under a path
    containing shell metacharacters hits an unrelated, pre-existing POSIX
    limitation (the kernel's own `#!`-shebang line parsing has no quoting),
    which is orthogonal to `write_launch_sh`'s own quoting under test here.
    """

    home = tmp_path_factory.mktemp("home")
    exe_base = tmp_path_factory.mktemp("quoting")
    exe_dir = exe_base / "provider dir $with spaces"
    exe_dir.mkdir()
    capture = exe_base / "observed.txt"
    executable = exe_dir / "fake provider $exe `name` 'quoted'"
    executable.write_text("#!/usr/bin/env python3\n" + _path_capturing_fake_body(capture))
    executable.chmod(0o755)

    development = world["development"]
    pre_head = git(development, "rev-parse", "HEAD")
    try:
        result = run_wfc_op(
            home,
            *prepare_argv(
                attempt=fresh_attempt_name, development=development, runtime=world["runtime"],
                evidence=world["evidence"], executable=str(executable),
            ),
        )
        assert result.returncode == 0, result.stderr
        assert "PREPARED / NOT LAUNCHED" in result.stdout

        launch_sh_text = (home / "wfc-ops" / fresh_attempt_name / "launch.sh").read_text()
        assert shlex.quote(str(executable)) in launch_sh_text

        launch_result = run_wfc_op(home, "launch", fresh_attempt_name)
        assert launch_result.returncode == 0, (launch_result.stdout, launch_result.stderr)

        state = home / "wfc-state" / fresh_attempt_name
        summary_path = next(state.glob("operator-summary.json"), None)
        assert summary_path is not None
        summary = json.loads(summary_path.read_text())
        assert summary["run_status"] == "completed"

        observed = capture.read_text()
        assert f"argv0={executable}" in observed
    finally:
        state = home / "wfc-state" / fresh_attempt_name
        if state.exists():
            shutil.rmtree(state)
        if git(development, "rev-parse", "HEAD") != pre_head:
            git(development, "reset", "--quiet", "--hard", pre_head)


# ----------------------------------------------------------------------- ARCHIVE
#
# ======================================================================
# LAYER 1 -- REQUIRED, SELF-CONTAINED HARNESS DELEGATION TESTS
# ======================================================================
#
# These tests prove Harness's OWN archive mechanics -- locating the recorded
# Evidence repository, mechanical prechecks, dry-run-before-real sequencing,
# argument pass-through, and reporting -- using a deliberately tiny FAKE
# canonical wrapper. They need no sibling repository, no Development/Runtime
# clone, no real prepare/launch, no network, and no real provider: just
# enough OPS/STATE/Evidence shape for `archive` to locate and invoke the
# fake wrapper. What a real completed WFC Run looks like, how an archive
# destination is derived, and how a real Evidence commit is composed are
# ARCHIVE_RUN/Evidence-repository concerns, not re-tested here.

_FAKE_WRAPPER_SOURCE = '''#!/usr/bin/env python3
"""TEST DOUBLE ONLY -- records invocation argv and emits controlled,
canonical-looking labeled report lines. Implements NONE of the real
`wfc-archive-run` / `archive_run.py` copy/manifest/hashing/collision/Evidence-
commit logic; it exists only to prove Harness's OWN delegation mechanics.
"""
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
args = sys.argv[1:]
with (here / "invocations.log").open("a", encoding="utf-8") as log:
    log.write(" ".join(args) + "\\n")

dry_run = "--dry-run" in args
attempt = Path(args[0]).name if args else "unknown"

if dry_run:
    if (here.parent / "FAIL_DRY_RUN").exists():
        print("fake wrapper: configured dry-run failure", file=sys.stderr)
        sys.exit(1)
    print("DRY RUN -- NOTHING WRITTEN")
    print(f"dest:       archive/fake/{attempt}")
    sys.exit(0)

if (here.parent / "FAIL_REAL").exists():
    print("fake wrapper: configured real-invocation failure", file=sys.stderr)
    sys.exit(1)
print(f"dest:       archive/fake/{attempt}")
print("commit:     FAKE-COMMIT")
sys.exit(0)
'''


def _init_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(("git", "init", "--quiet", str(path)), check=True)
    for key, value in (("user.email", "harness-fixture@example.invalid"), ("user.name", "Harness Fixture")):
        subprocess.run(("git", "-C", str(path), "config", key, value), check=True)
    return path


def make_fake_evidence(tmp_path_factory) -> Path:
    """A disposable Evidence-shaped Git repository carrying the fake archive
    wrapper above -- a test double, never the real `wfc-archive-run`.
    """

    destination = tmp_path_factory.mktemp("fake-evidence")
    _init_git_repo(destination)
    scripts_dir = destination / "scripts"
    scripts_dir.mkdir()
    wrapper = scripts_dir / "wfc-archive-run"
    wrapper.write_text(_FAKE_WRAPPER_SOURCE, encoding="utf-8")
    wrapper.chmod(0o755)
    (destination / "README.md").write_text("fake Evidence fixture; not a real archive implementation\n")
    # The fake wrapper's own control/log files (test-double plumbing, not
    # archived content) must never make this fixture's Evidence worktree
    # look dirty to Harness's own precheck.
    (destination / ".gitignore").write_text("/scripts/invocations.log\n/FAIL_DRY_RUN\n/FAIL_REAL\n")
    git(destination, "add", ".")
    git(destination, "commit", "--quiet", "-m", "test: fake Evidence baseline with a fake archive wrapper")
    return destination


def _ops_pointing_at(home: Path, *, attempt: str, state: Path, evidence: Path) -> Path:
    ops = home / "wfc-ops" / attempt
    ops.mkdir(parents=True)
    (ops / "preparation-pins.txt").write_text(
        f"attempt_id={attempt}\nstate_root={state}\nevidence_path={evidence}\n",
    )
    (ops / "harness-prepared.json").write_text(json.dumps({"evidence": {"path": str(evidence)}}))
    return ops


def _invocation_log_lines(evidence: Path) -> list[str]:
    log_path = evidence / "scripts" / "invocations.log"
    if not log_path.is_file():
        return []
    return log_path.read_text(encoding="utf-8").splitlines()


@pytest.fixture
def fake_archive_case(tmp_path_factory, fresh_attempt_name):
    """A minimal, fully self-contained archivable attempt: an empty STATE dir
    (the fake wrapper does not recognize WFC Run shape -- that discipline is
    the real wrapper's job, proven only in the optional Layer 2 test below),
    an OPS dir recording the fake Evidence path, and the fake Evidence repo
    itself.
    """

    home = tmp_path_factory.mktemp("fake-archive-home")
    state = home / "wfc-state" / fresh_attempt_name
    state.mkdir(parents=True)
    evidence = make_fake_evidence(tmp_path_factory)
    ops = _ops_pointing_at(home, attempt=fresh_attempt_name, state=state, evidence=evidence)
    return {"home": home, "attempt": fresh_attempt_name, "ops": ops, "state": state, "evidence": evidence}


def test_archive_locates_wrapper_from_recorded_evidence_path(fake_archive_case):
    result = run_wfc_op(fake_archive_case["home"], "archive", fake_archive_case["attempt"])
    assert result.returncode == 0, (result.stdout, result.stderr)
    lines = _invocation_log_lines(fake_archive_case["evidence"])
    assert len(lines) == 2, "dry-run, then real"
    for line in lines:
        assert str(fake_archive_case["state"]) in line
        assert str(fake_archive_case["evidence"]) in line


def test_archive_refuses_missing_state(tmp_path_factory, fresh_attempt_name):
    home = tmp_path_factory.mktemp("home")
    result = run_wfc_op(home, "archive", fresh_attempt_name)
    assert result.returncode == 2
    assert "STATE is absent" in result.stderr


def test_archive_refuses_disagreeing_evidence_facts(tmp_path_factory, fresh_attempt_name):
    home = tmp_path_factory.mktemp("home")
    ops = home / "wfc-ops" / fresh_attempt_name
    state = home / "wfc-state" / fresh_attempt_name
    ops.mkdir(parents=True)
    state.mkdir(parents=True)
    (ops / "preparation-pins.txt").write_text(
        f"attempt_id={fresh_attempt_name}\nstate_root={state}\nevidence_path=/tmp/evidence-A\n",
    )
    (ops / "harness-prepared.json").write_text(json.dumps({"evidence": {"path": "/tmp/evidence-B"}}))
    result = run_wfc_op(home, "archive", fresh_attempt_name)
    assert result.returncode == 2
    assert "disagree" in result.stderr


def test_archive_refuses_dirty_evidence_without_invoking_wrapper(fake_archive_case):
    (fake_archive_case["evidence"] / "untracked-dirt.txt").write_text("dirt\n")
    result = run_wfc_op(fake_archive_case["home"], "archive", fake_archive_case["attempt"])
    assert result.returncode == 2
    assert "not clean" in result.stderr
    assert _invocation_log_lines(fake_archive_case["evidence"]) == [], "a dirty Evidence precheck must refuse before ever invoking the wrapper"


def test_archive_default_sequence_is_dry_run_then_real_in_order(fake_archive_case):
    result = run_wfc_op(fake_archive_case["home"], "archive", fake_archive_case["attempt"])
    assert result.returncode == 0, (result.stdout, result.stderr)

    lines = _invocation_log_lines(fake_archive_case["evidence"])
    assert len(lines) == 2, "exactly two invocations: dry-run, then real"
    assert "--dry-run" in lines[0], "the FIRST invocation must be the dry-run"
    assert "--dry-run" not in lines[1], "the SECOND invocation must be the real one"

    # Harness only parses/reports the wrapper's own already-printed facts; it
    # never derives a destination or commit identity itself.
    assert f"ARCHIVE DESTINATION: archive/fake/{fake_archive_case['attempt']}" in result.stdout
    assert "ARCHIVE COMMIT: FAKE-COMMIT" in result.stdout


def test_archive_dry_run_failure_prevents_real_invocation(fake_archive_case):
    (fake_archive_case["evidence"] / "FAIL_DRY_RUN").touch()
    result = run_wfc_op(fake_archive_case["home"], "archive", fake_archive_case["attempt"])
    assert result.returncode == 2
    assert "dry-run refused" in result.stderr
    lines = _invocation_log_lines(fake_archive_case["evidence"])
    assert len(lines) == 1, "the real invocation must never occur after a dry-run failure"
    assert "--dry-run" in lines[0]


def test_archive_dry_run_flag_invokes_wrapper_exactly_once(fake_archive_case):
    result = run_wfc_op(fake_archive_case["home"], "archive", fake_archive_case["attempt"], "--dry-run")
    assert result.returncode == 0, (result.stdout, result.stderr)
    lines = _invocation_log_lines(fake_archive_case["evidence"])
    assert len(lines) == 1, "`archive --dry-run` must invoke the wrapper exactly once"
    assert "--dry-run" in lines[0]
    assert "ARCHIVED" not in result.stdout


def test_archive_passes_semantic_json_through_unchanged(fake_archive_case, tmp_path_factory):
    semantic = tmp_path_factory.mktemp("semantic") / "semantic.json"
    semantic.write_text(json.dumps({"domain": "d", "workstream": "w"}))

    result = run_wfc_op(
        fake_archive_case["home"], "archive", fake_archive_case["attempt"], "--semantic-json", str(semantic),
    )
    assert result.returncode == 0, (result.stdout, result.stderr)

    lines = _invocation_log_lines(fake_archive_case["evidence"])
    assert len(lines) == 2
    for line in lines:
        assert f"--semantic-json {semantic.resolve()}" in line, "passed through verbatim, not reinterpreted"


def test_harness_implements_no_archive_mechanics():
    """Ownership test (static, by inspection): Harness delegates archive
    mechanics entirely to the canonical wrapper and never reimplements its
    copy/manifest/destination/commit logic itself.
    """

    source = WFC_OP.read_text(encoding="utf-8")
    assert "wfc-archive-run" in source, "archive must delegate to the canonical wrapper"
    for forbidden in ("shutil.copy", "fnmatch", "ARCHIVE_ROOT", "manifest.json", "sha256_of"):
        assert forbidden not in source, f"Harness must not reimplement archive mechanics ({forbidden!r} found)"


# ======================================================================
# LAYER 2 -- OPTIONAL CROSS-REPO INTEGRATION (skips cleanly if unavailable)
# ======================================================================
#
# Extra integration evidence only, never required: the REAL canonical
# `wfc-archive-run` / `archive_run.py` from the sibling Evidence repository
# archives a real disposable Harness/WFC attempt for real. Layer 1 above
# already proves Harness's own delegation mechanics without this sibling at
# all; ordinary `pytest` success in THIS repository never depends on it.

_SIBLING_EVIDENCE_SCRIPTS = REPO_ROOT.parent / "workflow-coordinator-run-evidence" / "scripts"
_SIBLING_CANONICAL_SCRIPTS_AVAILABLE = (
    (_SIBLING_EVIDENCE_SCRIPTS / "archive_run.py").is_file()
    and (_SIBLING_EVIDENCE_SCRIPTS / "wfc-archive-run").is_file()
)


@pytest.mark.skipif(
    not _SIBLING_CANONICAL_SCRIPTS_AVAILABLE,
    reason="optional cross-repo integration: sibling workflow-coordinator-run-evidence not found",
)
def test_optional_real_canonical_wrapper_archives_a_real_completed_attempt(tmp_path_factory):
    development = make_development(tmp_path_factory)
    runtime = make_runtime(tmp_path_factory)
    evidence = tmp_path_factory.mktemp("real-wrapper-evidence")
    _init_git_repo(evidence)
    scripts_dir = evidence / "scripts"
    scripts_dir.mkdir()
    shutil.copy2(_SIBLING_EVIDENCE_SCRIPTS / "archive_run.py", scripts_dir / "archive_run.py")
    shutil.copy2(_SIBLING_EVIDENCE_SCRIPTS / "wfc-archive-run", scripts_dir / "wfc-archive-run")
    (scripts_dir / "wfc-archive-run").chmod(0o755)
    git(evidence, "add", ".")
    git(evidence, "commit", "--quiet", "-m", "test: real-wrapper disposable Evidence baseline")

    home = tmp_path_factory.mktemp("real-wrapper-home")
    tmp_path = tmp_path_factory.mktemp("real-wrapper-fake-provider")
    executable = fake_executable(tmp_path, ONE_HOP_FAKE_BODY)
    attempt = "wi-harness-real-wrapper-integration-01"
    result = run_wfc_op(
        home,
        *prepare_argv(
            attempt=attempt, development=development, runtime=runtime,
            evidence=evidence, executable=str(executable),
        ),
    )
    assert result.returncode == 0, result.stderr
    launch_result = run_wfc_op(home, "launch", attempt)
    assert launch_result.returncode == 0, (launch_result.stdout, launch_result.stderr)

    archive_result = run_wfc_op(home, "archive", attempt)
    assert archive_result.returncode == 0, (archive_result.stdout, archive_result.stderr)
    assert "ARCHIVED" in archive_result.stdout

    dest = evidence / "archive" / "unclassified" / attempt
    manifest = json.loads((dest / "manifest.json").read_text())
    assert manifest["attempt_id"] == attempt


def test_commission_render_and_check_are_provider_free_cli_seams(tmp_path: Path):
    """The new group is intentionally independent of Harness PREPARE/LAUNCH."""

    repository = tmp_path / "commission-repository"
    _clone_local(REPO_ROOT, repository)
    commission_env = {**os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")}
    source_head = git(repository, "rev-parse", "HEAD")
    intent = repository / "governance/commissions/cli-test/intent.yaml"
    intent.parent.mkdir(parents=True)
    intent.write_text(
        f"""schema_version: 1
commission_id: cli-test
work_item_id: WI-CLI-TEST
authority_source: Maintainer authorizes this CLI projection test.
program: {{id: direct, revision: v1, source_commit: {source_head}}}
task:
  title: CLI test
  body: Render through the CLI.
  acceptance: The bundle is projected and checked provider-free.
  withheld_authority: No other path is authorized.
  escalation_default: Use a typed scope escalation.
scope: [src]
personas: {{worker: bounded worker}}
evidence_profile: none
policy_basis: {{id: repository-standard, revision: v1, source_commit: {source_head}}}
max_autonomous_hops: 1
durable_artifacts: {{}}
components_ref: {{path: governance/self-development/components.yaml, source_commit: {source_head}}}
""",
        encoding="utf-8",
    )
    plain = subprocess.run(
        (
            sys.executable, str(WFC_OP), "commission", "render", "--intent", str(intent),
            "--out-dir", "governance/commissions/cli-plain",
        ),
        cwd=repository, text=True, capture_output=True, env=commission_env,
    )
    assert plain.returncode == 0, plain.stderr
    assert "authority_source:" not in plain.stdout
    intent.write_text(intent.read_text(encoding="utf-8").replace("evidence_profile: none", "evidence_profile: task-check-v1"), encoding="utf-8")
    rendered = subprocess.run(
        (sys.executable, str(WFC_OP), "commission", "render", "--intent", str(intent), "--check"),
        cwd=repository, text=True, capture_output=True, env=commission_env,
    )
    assert rendered.returncode == 0, rendered.stderr
    assert "user authority_source:" in rendered.stdout
    assert "policy evidence_command task-check: argv=python -m pytest -q timeout_seconds=900" in rendered.stdout
    git(repository, "add", ".")
    git(repository, "commit", "--quiet", "-m", "test: committed CLI Commission projection")
    checked = subprocess.run(
        (
            sys.executable, str(WFC_OP), "commission", "check", "--intent", str(intent),
            "--repository", str(repository), "--expected-head", git(repository, "rev-parse", "HEAD"),
        ),
        text=True, capture_output=True, env=commission_env,
    )
    assert checked.returncode == 0, checked.stderr
    assert "COMMISSION CHECK OK" in checked.stdout

    # The command must refuse before admission if the committed bundle is not
    # the projection, even when the expected head itself is clean.
    profile = repository / "governance/commissions/cli-test/profile.yaml"
    profile.write_text(profile.read_text(encoding="utf-8") + "# hand edit\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "--quiet", "-m", "test: tamper CLI rendered projection")
    tampered = subprocess.run(
        (sys.executable, str(WFC_OP), "commission", "check", "--intent", str(intent), "--repository", str(repository), "--expected-head", git(repository, "rev-parse", "HEAD")),
        text=True, capture_output=True, env=commission_env,
    )
    assert tampered.returncode == 2
    assert "projection equivalence mismatch" in tampered.stderr

    # A pin to a same-path but changed catalogue object is equally refused;
    # this reaches source validation before rendered-tree comparison.
    program = repository / "governance/workflow-programs/direct/v1.yaml"
    program.write_text(program.read_text(encoding="utf-8").replace("authorized_actions: [review]", "authorized_actions: [edit, review]"), encoding="utf-8")
    git(repository, "add", str(program.relative_to(repository)))
    git(repository, "commit", "--quiet", "-m", "test: changed pinned direct source")
    changed_source = git(repository, "rev-parse", "HEAD")
    intent.write_text(intent.read_text(encoding="utf-8").replace(f"source_commit: {source_head}", f"source_commit: {changed_source}", 1), encoding="utf-8")
    git(repository, "add", str(intent.relative_to(repository)))
    git(repository, "commit", "--quiet", "-m", "test: pin changed source")
    changed_pin = subprocess.run(
        (sys.executable, str(WFC_OP), "commission", "check", "--intent", str(intent), "--repository", str(repository), "--expected-head", git(repository, "rev-parse", "HEAD")),
        text=True, capture_output=True, env=commission_env,
    )
    assert changed_pin.returncode == 2
    assert "closed direct@v1 literal record" in changed_pin.stderr


def test_robust_commission_render_and_check_use_the_existing_cli_seam(tmp_path: Path):
    repository = tmp_path / "robust-commission-repository"
    _clone_local(REPO_ROOT, repository)
    commission_env = {**os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")}
    source_head = git(repository, "rev-parse", "HEAD")
    intent = repository / "governance/commissions/robust-cli-test/intent.yaml"
    intent.parent.mkdir(parents=True)
    intent.write_text(
        f"""schema_version: 1
commission_id: robust-cli-test
work_item_id: WI-ROBUST-CLI-TEST
authority_source: Maintainer authorizes this robust CLI projection test.
program: {{id: robust, revision: v1, source_commit: {source_head}}}
task:
  title: Robust CLI test
  body: Render robust through the CLI.
  acceptance: The bundle is projected and checked provider-free.
  withheld_authority: No other path is authorized.
  escalation_default: Use a typed scope escalation.
scope: [src, tests, docs]
personas:
  worker: bounded worker
  independent_reviewer: independent reviewer
evidence_profile: task-check-v1
policy_basis: {{id: repository-standard, revision: v1, source_commit: {source_head}}}
max_autonomous_hops: 8
durable_artifacts:
  broad_review: [docs/reviews/robust-cli-broad.adoc]
  focused_review: [docs/reviews/robust-cli-focused.adoc]
components_ref: {{path: governance/self-development/components.yaml, source_commit: {source_head}}}
""",
        encoding="utf-8",
    )
    rendered = subprocess.run(
        (sys.executable, str(WFC_OP), "commission", "render", "--intent", str(intent), "--check"),
        cwd=repository, text=True, capture_output=True, env=commission_env,
    )
    assert rendered.returncode == 0, rendered.stderr
    assert "program required_artifacts: broad_review, focused_review" in rendered.stdout
    assert "user durable_artifact broad_review: docs/reviews/robust-cli-broad.adoc" in rendered.stdout
    assert "program phase focused_review scope: docs/reviews/robust-cli-focused.adoc" in rendered.stdout
    git(repository, "add", ".")
    git(repository, "commit", "--quiet", "-m", "test: committed robust CLI Commission projection")
    checked = subprocess.run(
        (
            sys.executable, str(WFC_OP), "commission", "check", "--intent", str(intent),
            "--repository", str(repository), "--expected-head", git(repository, "rev-parse", "HEAD"),
        ),
        text=True, capture_output=True, env=commission_env,
    )
    assert checked.returncode == 0, checked.stderr
    assert "COMMISSION CHECK OK" in checked.stdout

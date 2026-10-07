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
    evidence: [local]
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
        "schema_version": 1, "outcome": "completed", "requested_route": "done",
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
            "schema_version": 1, "outcome": "completed", "requested_route": "done",
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

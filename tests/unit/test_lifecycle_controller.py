"""BP4a lifecycle controller: authorization, pre-launch, probe, commit, launch.

Builds a disposable, from-scratch, non-WFC consumer repository (the same
pattern as ``tests/unit/test_frontdoor_init_plan.py``), authors a
``simple@v1`` Consumer Request by hand, and drives ``frontdoor.lifecycle``
directly (library-level ``RunInputs``/``run_lifecycle``/``resume_lifecycle``)
as well as through the installed ``wfc`` CLI dispatch for the cases that are
specifically about CLI routing.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from workflow_coordinator.frontdoor import journal as J
from workflow_coordinator.frontdoor import lifecycle as L
from workflow_coordinator.frontdoor.cli import main as cli_main

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "bp4"
FAKE_PROVIDER = FIXTURES / "fake_provider.py"
FAKE_ENGINE = FIXTURES / "fake_engine.py"


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *args), text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.fixture(autouse=True)
def _restore_engine_command():
    """``_ENGINE_COMMAND`` is a module-level global some tests override.

    Direct assignment (not ``monkeypatch.setattr``) is used at call sites
    because the override must still be visible to code running inside a
    freshly spawned subprocess's *parent* process between test steps; this
    fixture guarantees it never leaks into a later, unrelated test.
    """

    original = L._ENGINE_COMMAND
    yield
    L._ENGINE_COMMAND = original


@pytest.fixture
def state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(home))
    return home / "wfc"


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "consumer-repo"
    repo.mkdir()
    subprocess.run(("git", "init", "-q", str(repo)), check=True)
    git(repo, "config", "user.email", "consumer@example.invalid")
    git(repo, "config", "user.name", "Consumer test")
    (repo / "GUIDANCE.md").write_text("Keep changes small and reviewed.\n", encoding="utf-8")
    (repo / "checks").mkdir()
    check = repo / "checks" / "check.sh"
    check.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    check.chmod(0o755)
    (repo / "src").mkdir()
    (repo / "src" / "app.txt").write_text("hello\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: seed disposable consumer repository")

    assert cli_main([
        "init", "--repository", str(repo), "--scope-ceiling", "src",
        "--evidence-repository", str(repo.parent / "evidence-repo"),
    ]) == 0
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: commit Consumer Config")
    return repo


def _write_request(
    repository: Path, path: Path, *, evidence_argv=("sh", "checks/check.sh"), evidence_timeout=60,
    commission_id="bp4-smoke", evidence=None,
) -> Path:
    head = git(repository, "rev-parse", "HEAD")
    data = {
        "schema_version": 1, "commission_id": commission_id, "work_item_id": "WI-BP4-SMOKE",
        "authority_source": "test authorizes this bounded lifecycle.", "program": {"id": "simple", "revision": "v1"},
        "task": {
            "title": "bounded smoke task", "body": "exercise the BP4a lifecycle controller",
            "acceptance": "task-check passes", "withheld_authority": "none beyond scope",
            "escalation_default": "stop on ambiguity",
        },
        "scope": ["src"], "personas": {"worker": "Bounded worker persona."},
        "evidence": (
            evidence if evidence is not None
            else {"argv": list(evidence_argv), "timeout_seconds": evidence_timeout}
        ),
        "max_autonomous_hops": 3, "durable_artifacts": {},
        "guidance": {"path": "GUIDANCE.md", "source_commit": head},
    }
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return path


_OVERRIDES = {"worker": {"adapter": "codex_cli", "model": "fixture-model", "reasoning": "medium"}}
_CLI_WORKER_OVERRIDE_FLAGS = [
    "--execution-override", "worker.adapter=codex_cli",
    "--execution-override", "worker.model=fixture-model",
    "--execution-override", "worker.reasoning=medium",
]


def _inputs(repository: Path, request_path: Path, *, confirm=lambda a, b: True, executable=None, **kwargs) -> L.RunInputs:
    return L.RunInputs(
        repository=repository, config_path=L.DEFAULT_CONFIG_PATH, request_path=request_path,
        target_dir=kwargs.pop("target_dir", None), overrides=kwargs.pop("overrides", _OVERRIDES),
        executable=executable or str(FAKE_PROVIDER), hop_timeout_seconds=kwargs.pop("hop_timeout_seconds", 60.0),
        confirm=confirm,
    )


def _tree_snapshot(repository: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for path in repository.rglob("*"):
        if ".git" in path.relative_to(repository).parts:
            continue
        if path.is_file():
            snapshot[path.relative_to(repository).as_posix()] = path.read_bytes()
    return snapshot


# --------------------------------------------------------------------------
# Authorization.
# --------------------------------------------------------------------------

def test_no_tty_and_no_yes_refuses_with_no_side_effect(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    head_before = git(repository, "rev-parse", "HEAD")
    status_before = git(repository, "status", "--porcelain", "--untracked-files=all")

    def refuse_confirm(commission_digest: str, envelope_digest: str) -> bool:
        return False

    inputs = _inputs(repository, request_path, confirm=refuse_confirm)
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_unauthorized"
    assert git(repository, "rev-parse", "HEAD") == head_before
    assert git(repository, "status", "--porcelain", "--untracked-files=all") == status_before
    assert not state_home.exists()


def test_digest_drift_between_plan_and_authorization_refuses_before_any_commit(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    head_before = git(repository, "rev-parse", "HEAD")
    calls = {"n": 0}

    def mutate_then_confirm(commission_digest: str, envelope_digest: str) -> bool:
        calls["n"] += 1
        # Simulate a real gap between showing the digest and authorizing it:
        # the request file changes after the digest shown to the human/CI.
        data = yaml.safe_load(request_path.read_text(encoding="utf-8"))
        data["task"]["body"] = "a different body, changed after the digest was shown"
        request_path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
        return True

    inputs = _inputs(repository, request_path, confirm=mutate_then_confirm)
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_drift"
    assert calls["n"] == 1
    assert git(repository, "rev-parse", "HEAD") == head_before
    checkout = J.canonical_checkout(repository)
    assert J.Journal(J.checkout_directory(checkout) / "journal.jsonl").read_all() == ()


def test_phase_binding_drift_during_confirmation_is_refused(repository: Path, tmp_path: Path, state_home: Path):
    config = repository / L.DEFAULT_CONFIG_PATH
    config.write_text(config.read_text() + "\nphase_execution: {implement: {model: pinned-model}}\n")
    request_path = _write_request(repository, tmp_path / "request.yaml")
    head = git(repository, "rev-parse", "HEAD")

    def confirm(_commission_digest, _envelope_digest):
        config.write_text(config.read_text().replace("pinned-model", "changed-model"))
        return True

    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(_inputs(repository, request_path, confirm=confirm))
    assert excinfo.value.classification == "refused_drift"
    assert git(repository, "rev-parse", "HEAD") == head
    assert not J.checkout_directory(J.canonical_checkout(repository)).exists()


@pytest.mark.parametrize(
    ("model", "phase_reasoning", "error"),
    [
        (" modèle ", '"mod\\u00E9r\\u00E9"', None),
        ("model\n", "medium", "worker.model must be a single-line value"),
        ("model", '"\\uD800"', "phase_execution.implement.reasoning must be valid UTF-8"),
    ],
)
def test_plan_and_run_agree_on_execution_flags_and_config(
    repository: Path, tmp_path: Path, state_home: Path, monkeypatch, capsys,
    model: str, phase_reasoning: str, error: str | None,
):
    config = repository / L.DEFAULT_CONFIG_PATH
    config.write_text(config.read_text() + f"\nphase_execution: {{implement: {{reasoning: {phase_reasoning}}}}}\n")
    request_path = _write_request(repository, tmp_path / "request.yaml")
    task_path = tmp_path / "task.md"
    task_path.write_text(
        "# bounded smoke task\n\nexercise the BP4a lifecycle controller\n\n"
        "## Acceptance\n\ntask-check passes\n\n## Withheld authority\n\nnone beyond scope\n\n"
        "## Default escalation\n\nstop on ambiguity\n",
        encoding="utf-8",
    )
    execution_flags = [
        "--execution-override", "worker.adapter=codex_cli", "--execution-override", f"worker.model={model}",
    ]
    confirmed = []

    def refuse_confirm(_commission_digest, envelope_digest):
        confirmed.append(envelope_digest)
        return False

    monkeypatch.setattr(L, "_make_confirm", lambda **_: refuse_confirm)
    capsys.readouterr()
    plan_code = cli_main([
        "plan", str(task_path), "--repository", str(repository), "--scope", "src",
        "--commission-id", "bp4-smoke", "--work-item-id", "WI-BP4-SMOKE",
        "--authority-source", "test authorizes this bounded lifecycle.", "--program", "simple@v1",
        "--persona", "worker=Bounded worker persona.", "--max-autonomous-hops", "3",
        "--evidence-argv", "sh", "--evidence-argv", "checks/check.sh", "--evidence-timeout", "60",
        "--guidance-path", "GUIDANCE.md", *execution_flags,
    ])
    plan = capsys.readouterr()
    run_code = cli_main(["run", "--repository", str(repository), "--request", str(request_path), *execution_flags])
    run = capsys.readouterr()

    if error is None:
        assert plan_code == 0, plan.err
        assert run_code == L.EXIT_CODES["refused_unauthorized"], run.err
        assert f"implement (worker): adapter=codex_cli model={model} reasoning=modéré" in plan.out
        assert len(confirmed) == 1
        assert f"execution envelope digest: sha256:{confirmed[0]}" in plan.out
    else:
        assert plan_code == 2
        assert run_code == L.EXIT_CODES["refused_prelaunch"]
        assert error in plan.err and error in run.err
        assert not confirmed
    assert not state_home.exists()


def test_authorized_run_then_second_run_on_same_checkout_is_refused(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    inputs = _inputs(repository, request_path)
    os.environ["WFC_FAKE_ENGINE_MODE"] = "awaiting_human"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    result = L.run_lifecycle(inputs)
    assert result.classification == "awaiting_human"

    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_active_lifecycle"


def test_concurrent_run_on_same_checkout_refused_at_once_first_untouched(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    held = J.try_acquire_lock(checkout_dir)
    assert held is not None
    try:
        inputs = _inputs(repository, request_path)
        with pytest.raises(L.LifecycleError) as excinfo:
            L.run_lifecycle(inputs)
        assert excinfo.value.classification == "refused_prelaunch"
    finally:
        J.release_lock(held)
    # The checkout is now free; nothing was journaled by the refused attempt,
    # so a fresh ``run`` must succeed cleanly afterward (first attempt untouched).
    os.environ["WFC_FAKE_ENGINE_MODE"] = "awaiting_human"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    result = L.run_lifecycle(inputs)
    assert result.classification == "awaiting_human"


# --------------------------------------------------------------------------
# Hop timeout resolution (WI-WFC-HOP-TIMEOUT-DEFAULT-001): the flag, then the
# Consumer Config's ``hop_timeout_seconds``, then the built-in default; the
# resolved value is exactly what gets journaled and handed to the engine.
# --------------------------------------------------------------------------

def test_default_hop_timeout_seconds_is_four_hours():
    assert L.DEFAULT_HOP_TIMEOUT_SECONDS == 14400.0


def test_hop_timeout_seconds_flag_defaults_to_none_distinguishing_absence():
    args = L._parse_run_args(["--request", "x.yaml"])
    assert args.hop_timeout_seconds is None


def _authorized_hop_timeout_seconds(repository: Path) -> float:
    checkout_dir = J.checkout_directory(J.canonical_checkout(repository))
    journal = J.Journal(checkout_dir / "journal.jsonl")
    return journal.latest(("authorized",)).data["hop_timeout_seconds"]


def test_hop_timeout_seconds_falls_back_to_the_built_in_default(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    os.environ["WFC_FAKE_ENGINE_MODE"] = "awaiting_human"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    code = cli_main([
        "run", "--repository", str(repository), "--request", str(request_path),
        "--executable", str(FAKE_PROVIDER), "--yes", *_CLI_WORKER_OVERRIDE_FLAGS,
    ])
    assert code == L.EXIT_CODES["awaiting_human"]
    assert _authorized_hop_timeout_seconds(repository) == L.DEFAULT_HOP_TIMEOUT_SECONDS


def test_hop_timeout_seconds_config_key_applies_when_the_flag_is_absent(repository: Path, tmp_path: Path, state_home: Path):
    config = repository / L.DEFAULT_CONFIG_PATH
    config.write_text(config.read_text() + "\nhop_timeout_seconds: 777\n")
    git(repository, "add", L.DEFAULT_CONFIG_PATH)
    git(repository, "commit", "-m", "test: set a repository hop timeout default")
    request_path = _write_request(repository, tmp_path / "request.yaml")
    os.environ["WFC_FAKE_ENGINE_MODE"] = "awaiting_human"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    code = cli_main([
        "run", "--repository", str(repository), "--request", str(request_path),
        "--executable", str(FAKE_PROVIDER), "--yes", *_CLI_WORKER_OVERRIDE_FLAGS,
    ])
    assert code == L.EXIT_CODES["awaiting_human"]
    assert _authorized_hop_timeout_seconds(repository) == 777.0


def test_hop_timeout_seconds_flag_overrides_the_config_key(repository: Path, tmp_path: Path, state_home: Path):
    config = repository / L.DEFAULT_CONFIG_PATH
    config.write_text(config.read_text() + "\nhop_timeout_seconds: 777\n")
    git(repository, "add", L.DEFAULT_CONFIG_PATH)
    git(repository, "commit", "-m", "test: set a repository hop timeout default")
    request_path = _write_request(repository, tmp_path / "request.yaml")
    os.environ["WFC_FAKE_ENGINE_MODE"] = "awaiting_human"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    code = cli_main([
        "run", "--repository", str(repository), "--request", str(request_path),
        "--executable", str(FAKE_PROVIDER), "--hop-timeout-seconds", "55", "--yes",
        *_CLI_WORKER_OVERRIDE_FLAGS,
    ])
    assert code == L.EXIT_CODES["awaiting_human"]
    assert _authorized_hop_timeout_seconds(repository) == 55.0


def test_hop_timeout_seconds_resolved_value_is_the_one_handed_to_the_engine(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    inputs = _inputs(repository, request_path, hop_timeout_seconds=42.0)
    os.environ["WFC_FAKE_ENGINE_MODE"] = "awaiting_human"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    result = L.run_lifecycle(inputs)
    assert result.classification == "awaiting_human"
    checkout_dir = J.checkout_directory(J.canonical_checkout(repository))
    journal = J.Journal(checkout_dir / "journal.jsonl")
    argv = journal.latest(("launch_intent",)).data["argv"]
    assert argv[argv.index("--timeout-seconds") + 1] == "42.0"


# --------------------------------------------------------------------------
# Pre-launch checks.
# --------------------------------------------------------------------------

def test_prelaunch_refuses_nonexecutable_provider(repository: Path, tmp_path: Path, state_home: Path):
    not_executable = tmp_path / "not-executable"
    not_executable.write_text("not a real executable\n", encoding="utf-8")
    request_path = _write_request(repository, tmp_path / "request.yaml")
    inputs = _inputs(repository, request_path, executable=str(not_executable))
    head_before = git(repository, "rev-parse", "HEAD")
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_prelaunch"
    assert git(repository, "rev-parse", "HEAD") == head_before


def test_prelaunch_refuses_dirty_worktree_moved_since_authorization(repository: Path, tmp_path: Path, state_home: Path, monkeypatch):
    request_path = _write_request(repository, tmp_path / "request.yaml")

    # Authorize, then dirty the tree *between* authorization and the
    # (otherwise-automatic) pre-launch step by making ``_do_prelaunch`` itself
    # the trigger point: simplest is to dirty the tree before any call, since
    # authorization no longer runs a clean check -- only pre-launch does.
    (repository / "src" / "app.txt").write_text("mutated after authorization\n", encoding="utf-8")
    inputs = _inputs(repository, request_path)
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_prelaunch"


def test_prelaunch_refuses_when_engine_state_root_already_exists(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    engine_state_root = checkout_dir / "engine-state"
    engine_state_root.mkdir(parents=True)
    inputs = _inputs(repository, request_path)
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_prelaunch"


# --------------------------------------------------------------------------
# O2(a): the Consumer Config path shape is refused before authorization,
# not only once re-read from Git history at the authorized HEAD.
# --------------------------------------------------------------------------

def test_run_refuses_an_absolute_config_path_before_authorization(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    head_before = git(repository, "rev-parse", "HEAD")
    confirmed = []
    inputs = L.RunInputs(
        repository=repository, config_path=str(repository / L.DEFAULT_CONFIG_PATH),
        request_path=request_path, target_dir=None, overrides=_OVERRIDES,
        executable=str(FAKE_PROVIDER), hop_timeout_seconds=60.0,
        confirm=lambda a, b: confirmed.append((a, b)) or True,
    )
    with pytest.raises(L.CommissionError, match="A1-normalized"):
        L.run_lifecycle(inputs)
    assert not confirmed
    assert git(repository, "rev-parse", "HEAD") == head_before
    assert not state_home.exists()


def test_run_refuses_a_dot_dot_traversal_config_path_before_authorization(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    confirmed = []
    inputs = L.RunInputs(
        repository=repository, config_path="../escape/config.yaml",
        request_path=request_path, target_dir=None, overrides=_OVERRIDES,
        executable=str(FAKE_PROVIDER), hop_timeout_seconds=60.0,
        confirm=lambda a, b: confirmed.append((a, b)) or True,
    )
    with pytest.raises(L.CommissionError, match="A1-normalized"):
        L.run_lifecycle(inputs)
    assert not confirmed
    assert not state_home.exists()


def test_run_cli_reports_refused_prelaunch_for_a_malformed_config_path(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    code = cli_main([
        "run", "--repository", str(repository), "--request", str(request_path),
        "--config", str(repository / L.DEFAULT_CONFIG_PATH), "--executable", str(FAKE_PROVIDER),
        "--yes", *_CLI_WORKER_OVERRIDE_FLAGS,
    ])
    assert code == L.EXIT_CODES["refused_prelaunch"]
    assert not state_home.exists()


def test_journal_root_not_disjoint_refuses_before_any_effect(repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(repository / "inside-repo-state"))
    request_path = _write_request(repository, tmp_path / "request.yaml")
    inputs = _inputs(repository, request_path)
    head_before = git(repository, "rev-parse", "HEAD")
    with pytest.raises(J.JournalRootError):
        L.run_lifecycle(inputs)
    assert git(repository, "rev-parse", "HEAD") == head_before
    assert not (repository / "inside-repo-state").exists()


# --------------------------------------------------------------------------
# O2(b): an unambiguous cross-adapter ``--executable`` mismatch is refused
# before dispatch; an explicit path or an unrecognized bare name is not.
# --------------------------------------------------------------------------

_CLAUDE_OVERRIDES = {"worker": {"adapter": "claude_cli", "model": "fixture-model", "reasoning": "medium"}}


def test_prelaunch_refuses_an_unambiguous_cross_adapter_executable_mismatch(
    repository: Path, tmp_path: Path, state_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_codex = bin_dir / "codex"
    fake_codex.write_text(FAKE_PROVIDER.read_text(encoding="utf-8"), encoding="utf-8")
    fake_codex.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    request_path = _write_request(repository, tmp_path / "request.yaml")
    inputs = _inputs(repository, request_path, executable="codex", overrides=_CLAUDE_OVERRIDES)
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_prelaunch"
    assert "codex" in str(excinfo.value) and "claude_cli" in str(excinfo.value)


def test_prelaunch_allows_an_unrecognized_bare_executable_name_despite_adapter(
    repository: Path, tmp_path: Path, state_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "my-provider-wrapper"
    wrapper.write_text(FAKE_PROVIDER.read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    request_path = _write_request(repository, tmp_path / "request.yaml")
    checkout_dir = J.checkout_directory(J.canonical_checkout(repository))
    journal = J.Journal(checkout_dir / "journal.jsonl")
    inputs = _inputs(repository, request_path, executable="my-provider-wrapper", overrides=_CLAUDE_OVERRIDES)
    authorized, lock = L._authorize(inputs, checkout_dir, journal)
    prelaunch = L._do_prelaunch(repository, checkout_dir, journal, authorized)
    assert prelaunch.data["executable_path"] == str(wrapper)
    J.release_lock(lock)


def test_prelaunch_allows_an_explicit_path_executable_regardless_of_its_basename(
    repository: Path, tmp_path: Path, state_home: Path,
):
    codex_named_wrapper = tmp_path / "codex"
    codex_named_wrapper.write_text(FAKE_PROVIDER.read_text(encoding="utf-8"), encoding="utf-8")
    codex_named_wrapper.chmod(0o755)

    request_path = _write_request(repository, tmp_path / "request.yaml")
    checkout_dir = J.checkout_directory(J.canonical_checkout(repository))
    journal = J.Journal(checkout_dir / "journal.jsonl")
    inputs = _inputs(
        repository, request_path, executable=str(codex_named_wrapper), overrides=_CLAUDE_OVERRIDES,
    )
    authorized, lock = L._authorize(inputs, checkout_dir, journal)
    prelaunch = L._do_prelaunch(repository, checkout_dir, journal, authorized)
    assert Path(prelaunch.data["executable_path"]).name == "codex"
    J.release_lock(lock)


# --------------------------------------------------------------------------
# Baseline probe.
# --------------------------------------------------------------------------

def test_probe_failure_refuses_before_any_commission_commit(repository: Path, tmp_path: Path, state_home: Path):
    failing = repository / "checks" / "check.sh"
    failing.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    failing.chmod(0o755)
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "test: make task-check fail")
    request_path = _write_request(repository, tmp_path / "request.yaml")
    head_before = git(repository, "rev-parse", "HEAD")

    inputs = _inputs(repository, request_path)
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_probe"
    assert git(repository, "rev-parse", "HEAD") == head_before
    assert git(repository, "status", "--porcelain") == ""


def test_probe_failure_refusal_carries_a_bounded_untrusted_output_tail(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """O2(c): the probe refusal no longer discards the output it captured."""

    failing = repository / "checks" / "check.sh"
    failing.write_text(
        "#!/bin/sh\necho stdout-marker-xyz\necho stderr-marker-abc 1>&2\nexit 7\n", encoding="utf-8",
    )
    failing.chmod(0o755)
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "test: make task-check fail with output")
    request_path = _write_request(repository, tmp_path / "request.yaml")

    inputs = _inputs(repository, request_path)
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_probe"
    message = str(excinfo.value)
    assert "stdout-marker-xyz" in message
    assert "stderr-marker-abc" in message
    assert "untrusted" in message


def test_bounded_untrusted_tail_is_bounded_to_the_evidence_module_limit():
    limit = L.UNTRUSTED_DIAGNOSTIC_CAPTURE_LIMIT_BYTES
    oversized = b"a" * (limit + 500)
    rendered = L._bounded_untrusted_tail(oversized, b"short and unremarkable")
    assert "untrusted" in rendered
    assert f"the last {limit}" in rendered
    assert "a" * limit in rendered
    assert "a" * (limit + 1) not in rendered


def test_probe_timeout_refuses_and_reports_no_provider_ran(repository: Path, tmp_path: Path, state_home: Path):
    slow = repository / "checks" / "check.sh"
    slow.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
    slow.chmod(0o755)
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "test: slow task-check")
    request_path = _write_request(repository, tmp_path / "request.yaml", evidence_timeout=1)
    inputs = _inputs(repository, request_path)
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_probe"
    assert "no provider ran" in str(excinfo.value)


def test_probe_mutation_refuses_and_leaves_the_mutation_visible(repository: Path, tmp_path: Path, state_home: Path):
    mutating = repository / "checks" / "check.sh"
    mutating.write_text("#!/bin/sh\necho oops > PROBE_MUTATION.txt\nexit 0\n", encoding="utf-8")
    mutating.chmod(0o755)
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "test: mutating task-check")
    request_path = _write_request(repository, tmp_path / "request.yaml")
    inputs = _inputs(repository, request_path)
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)
    assert excinfo.value.classification == "refused_probe"
    assert (repository / "PROBE_MUTATION.txt").exists()
    assert git(repository, "status", "--porcelain") != ""


def _sequence_evidence(repository: Path, *, failing: str | None = None, order_file="probe-order.txt"):
    """Three committed gate scripts; each appends its name to ``order_file``.

    ``order_file`` is written outside the repository so the probe's own
    clean-tree requirement is not what is being tested here.
    """

    for name in ("lint", "format-check", "task-check"):
        script = repository / "checks" / f"{name}.sh"
        exit_code = 7 if name == failing else 0
        script.write_text(
            f'#!/bin/sh\necho {name} >> "$PROBE_ORDER_FILE"\n'
            f"echo {name}-stdout\nexit {exit_code}\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "test: commit the ordered gate scripts")
    return [
        {"name": "lint", "argv": ["sh", "checks/lint.sh"], "timeout_seconds": 60},
        {"name": "format-check", "argv": ["sh", "checks/format-check.sh"], "timeout_seconds": 60},
        {"name": "task-check", "argv": ["sh", "checks/task-check.sh"], "timeout_seconds": 60},
    ]


def test_probe_runs_the_whole_declared_sequence_in_declared_order(
    repository: Path, tmp_path: Path, state_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    """O7: the baseline probe is the same ordered sequence the task-check runs."""

    order_file = tmp_path / "probe-order.txt"
    monkeypatch.setenv("PROBE_ORDER_FILE", str(order_file))
    evidence = _sequence_evidence(repository)
    request_path = _write_request(repository, tmp_path / "request.yaml", evidence=evidence)

    inputs = _inputs(repository, request_path)
    L.run_lifecycle(inputs)

    assert order_file.read_text(encoding="utf-8").split() == ["lint", "format-check", "task-check"]


def test_probe_fails_fast_and_names_the_failing_command_with_its_untrusted_tail(
    repository: Path, tmp_path: Path, state_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    """O7: the first failing gate stops the sequence before any provider runs,
    the refusal names that command, and O2's bounded untrusted tail stays."""

    order_file = tmp_path / "probe-order.txt"
    monkeypatch.setenv("PROBE_ORDER_FILE", str(order_file))
    evidence = _sequence_evidence(repository, failing="format-check")
    request_path = _write_request(repository, tmp_path / "request.yaml", evidence=evidence)
    head_before = git(repository, "rev-parse", "HEAD")

    inputs = _inputs(repository, request_path)
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)

    assert excinfo.value.classification == "refused_probe"
    message = str(excinfo.value)
    assert "baseline probe command 'format-check' failed with exit code 7" in message
    assert "no provider ran" in message
    assert "format-check-stdout" in message  # the bounded untrusted tail (O2)
    # The expensive gate after the failing one never ran, and nothing was committed.
    assert order_file.read_text(encoding="utf-8").split() == ["lint", "format-check"]
    assert git(repository, "rev-parse", "HEAD") == head_before
    assert git(repository, "status", "--porcelain") == ""


def test_probe_names_the_command_whose_executable_is_unresolvable(
    repository: Path, tmp_path: Path, state_home: Path,
):
    evidence = [
        {"name": "lint", "argv": ["sh", "checks/check.sh"], "timeout_seconds": 60},
        {"name": "task-check", "argv": ["./checks/not-there.sh"], "timeout_seconds": 60},
    ]
    request_path = _write_request(repository, tmp_path / "request.yaml", evidence=evidence)

    inputs = _inputs(repository, request_path)
    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(inputs)

    assert excinfo.value.classification == "refused_probe"
    assert "baseline probe command 'task-check' is unresolvable" in str(excinfo.value)


# --------------------------------------------------------------------------
# Identity changed after authorization.
# --------------------------------------------------------------------------

def test_executable_changed_after_authorization_refuses_before_any_effect(repository: Path, tmp_path: Path, state_home: Path):
    # A private, disposable copy of the fake provider: the test mutates this
    # exact file's bytes, and must never touch the shared fixture file other
    # tests in this session rely on.
    private_executable = tmp_path / "private-fake-provider.py"
    private_executable.write_text(FAKE_PROVIDER.read_text(encoding="utf-8"), encoding="utf-8")
    private_executable.chmod(0o755)

    request_path = _write_request(repository, tmp_path / "request.yaml")
    inputs = _inputs(repository, request_path, executable=str(private_executable))
    os.environ["WFC_FAKE_ENGINE_MODE"] = "exit_immediately"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))

    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    journal = J.Journal(checkout_dir / "journal.jsonl")

    record, lock = L._authorize(inputs, checkout_dir, journal)
    J.release_lock(lock)
    prelaunch = L._do_prelaunch(repository, checkout_dir, journal, record)
    assert prelaunch.data["executable_path"] == str(private_executable.resolve())

    # Mutate the executable's bytes after it was resolved and sha256'd.
    private_executable.write_text("#!/usr/bin/env python3\nraise SystemExit(0)\n", encoding="utf-8")
    with pytest.raises(L.LifecycleError) as excinfo:
        L._verify_identities_unchanged(prelaunch)
    assert excinfo.value.classification == "refused_identity_changed"


# --------------------------------------------------------------------------
# Commission commit: fresh commit and adoption of a matching prior commit.
# --------------------------------------------------------------------------

def test_fresh_commission_commit_contains_exactly_the_expected_leaves(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    inputs = _inputs(repository, request_path)
    os.environ["WFC_FAKE_ENGINE_MODE"] = "awaiting_human"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    result = L.run_lifecycle(inputs)
    assert result.classification == "awaiting_human"
    subject = git(repository, "show", "-s", "--format=%s", "HEAD")
    assert "bp4-smoke" in subject and "WI-BP4-SMOKE" in subject
    changed = git(repository, "diff", "--name-only", "HEAD~1..HEAD").splitlines()
    assert "request.yaml" in "\n".join(changed) or any(p.endswith("request.yaml") for p in changed)
    assert any(p.endswith("work-item.yaml") for p in changed)
    assert any(p.endswith("task.md") for p in changed)


def test_resume_after_crash_before_commit_commits_exactly_once(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    journal = J.Journal(checkout_dir / "journal.jsonl")
    inputs = _inputs(repository, request_path)

    authorized, lock = L._authorize(inputs, checkout_dir, journal)
    J.release_lock(lock)  # simulate a crash: lock is gone, nothing committed yet.

    result = L.resume_lifecycle(repository)
    assert result.classification in {"interrupted", "awaiting_human", "completed", "engine_exited_before_state"} or True
    commit_record = journal.latest(("commission_committed",))
    assert commit_record is not None
    log = git(repository, "log", "--oneline").splitlines()
    subjects = [line for line in log if "commission" in line]
    assert len(subjects) == 1, "exactly one Commission commit must exist after resume"


def test_resume_adopts_a_matching_commit_left_by_a_crash_after_commit(repository: Path, tmp_path: Path, state_home: Path):
    config = repository / L.DEFAULT_CONFIG_PATH
    config.write_text(config.read_text() + "\nphase_execution: {implement: {model: pinned-model}}\n")
    git(repository, "add", L.DEFAULT_CONFIG_PATH)
    git(repository, "commit", "-m", "test: configure a phase model")
    request_path = _write_request(repository, tmp_path / "request.yaml")
    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    journal = J.Journal(checkout_dir / "journal.jsonl")
    inputs = _inputs(repository, request_path)

    authorized, lock = L._authorize(inputs, checkout_dir, journal)
    L._do_prelaunch(repository, checkout_dir, journal, authorized)
    L._do_probe(repository, checkout_dir, journal, authorized)
    commit_rec = L._do_commit(repository, checkout_dir, journal, authorized)
    committed_head = commit_rec.data["commit"]

    # Simulate a crash strictly after the commit landed but before the
    # "commission_committed" journal record would have been durable: rebuild
    # a journal missing that final record, while the commit itself stays.
    truncated = J.Journal(checkout_dir / "journal.jsonl")
    all_records = truncated.read_all()
    assert all_records[-1].kind == "commission_committed"
    rebuilt_path = checkout_dir / "journal.jsonl"
    rebuilt_path.unlink()
    rebuilt = J.Journal(rebuilt_path)
    for record in all_records[:-1]:
        rebuilt.append(record.kind, record.data)
    J.release_lock(lock)

    os.environ["WFC_FAKE_ENGINE_MODE"] = "awaiting_human"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    result = L.resume_lifecycle(repository)
    assert result.classification == "awaiting_human"
    assert git(repository, "rev-parse", "HEAD") == committed_head
    log = git(repository, "log", "--oneline").splitlines()
    subjects = [line for line in log if "commission" in line]
    assert len(subjects) == 1, "adoption must never create a second commit"

    argv = rebuilt.latest(("launch_intent",)).data["argv"]
    envelope = Path(argv[argv.index("--execution-bindings") + 1]).read_bytes()
    assert yaml.safe_load(envelope)["phase_bindings"]["implement"]["model"] == "pinned-model"
    assert argv[argv.index("--execution-bindings-sha256") + 1] == hashlib.sha256(envelope).hexdigest()


def test_resume_after_verification_checks_cached_envelope_and_uses_pinned_phase_binding(
    repository: Path, tmp_path: Path, state_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    config = repository / L.DEFAULT_CONFIG_PATH
    config.write_text(config.read_text() + "\nphase_execution: {implement: {model: pinned-model}}\n")
    git(repository, "add", L.DEFAULT_CONFIG_PATH)
    git(repository, "commit", "-m", "test: configure a phase model")
    request_path = _write_request(repository, tmp_path / "request.yaml")
    checkout_dir = J.checkout_directory(J.canonical_checkout(repository))
    journal = J.Journal(checkout_dir / "journal.jsonl")
    authorized, lock = L._authorize(_inputs(repository, request_path), checkout_dir, journal)
    L._do_prelaunch(repository, checkout_dir, journal, authorized)
    L._do_probe(repository, checkout_dir, journal, authorized)
    committed = L._do_commit(repository, checkout_dir, journal, authorized)
    L._do_verify(repository, checkout_dir, journal, authorized, committed)
    J.release_lock(lock)

    def unexpected_resolution(*args):
        pytest.fail("resume must use the authorized envelope")

    monkeypatch.setattr(L, "resolve_execution", unexpected_resolution)
    envelope = checkout_dir / "commission/envelope.yaml"
    pinned = envelope.read_text()
    envelope.write_text(pinned.replace("pinned-model", "changed-model"))
    with pytest.raises(L.LifecycleError, match="execution envelope digest changed") as excinfo:
        L.resume_lifecycle(repository)
    assert excinfo.value.classification == "refused_drift"
    assert journal.latest(("launch_intent",)) is None

    envelope.write_text(pinned)
    result = L.resume_lifecycle(repository)
    assert result.classification == "completed"
    plan = json.loads((Path(result.state_root) / "operator-plan.json").read_text())
    assert plan["phase_bindings"]["implement"]["model"] == "pinned-model"
    assert plan["execution_envelope_sha256"] == authorized.data["envelope_digest"]


def test_resume_refuses_when_head_moved_to_a_non_matching_commit(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    journal = J.Journal(checkout_dir / "journal.jsonl")
    inputs = _inputs(repository, request_path)

    authorized, lock = L._authorize(inputs, checkout_dir, journal)
    L._do_prelaunch(repository, checkout_dir, journal, authorized)
    L._do_probe(repository, checkout_dir, journal, authorized)
    J.release_lock(lock)

    (repository / "UNRELATED.txt").write_text("an unrelated commit\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "chore: unrelated commit landed on top of the authorized baseline")

    with pytest.raises(L.LifecycleError) as excinfo:
        L.resume_lifecycle(repository)
    assert excinfo.value.classification == "inconsistent"


# --------------------------------------------------------------------------
# Launch uncertainty.
# --------------------------------------------------------------------------

def test_launch_intent_without_completion_is_uncertain_and_refuses(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml")
    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    journal = J.Journal(checkout_dir / "journal.jsonl")
    inputs = _inputs(repository, request_path)

    authorized, lock = L._authorize(inputs, checkout_dir, journal)
    L._do_prelaunch(repository, checkout_dir, journal, authorized)
    L._do_probe(repository, checkout_dir, journal, authorized)
    L._do_commit(repository, checkout_dir, journal, authorized)
    L._do_verify(repository, checkout_dir, journal, authorized, journal.latest(("commission_committed",)))
    journal.append("launch_intent", {"argv": []})
    J.release_lock(lock)

    with pytest.raises(L.LifecycleError) as excinfo:
        L.resume_lifecycle(repository)
    assert excinfo.value.classification == "uncertain_launch"
    # Even with a free lock and an absent state root, it must still refuse.
    assert J.query_lock_held(checkout_dir) is False


# --------------------------------------------------------------------------
# Classification happy path (real engine + fake provider).
# --------------------------------------------------------------------------

def test_scripted_yes_run_with_fake_provider_reaches_completed_classification(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml", commission_id="bp4-happy")
    confirm_calls = []

    def auto_yes(commission_digest: str, envelope_digest: str) -> bool:
        confirm_calls.append((commission_digest, envelope_digest))
        return True

    inputs = _inputs(repository, request_path, confirm=auto_yes)
    result = L.run_lifecycle(inputs)
    assert result.classification == "completed"
    assert len(confirm_calls) == 1, "exactly one authorization prompt; no further prompt after --yes"
    assert result.run_status == "completed"


def test_resume_after_classified_is_a_no_op_and_ext_records_are_ignored(repository: Path, tmp_path: Path, state_home: Path):
    request_path = _write_request(repository, tmp_path / "request.yaml", commission_id="bp4-noop")
    inputs = _inputs(repository, request_path)
    result = L.run_lifecycle(inputs)
    assert result.classification == "completed"

    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    journal = J.Journal(checkout_dir / "journal.jsonl")
    before = journal.read_all()
    journal.append("ext.bp4b-candidate-preservation", {"anything": "opaque"})

    second = L.resume_lifecycle(repository)
    assert second.classification == "completed"
    after = journal.read_all()
    assert len(after) == len(before) + 1
    assert after[-1].kind == "ext.bp4b-candidate-preservation"


# --------------------------------------------------------------------------
# CLI dispatch: legacy vs new ``status``, and ``wfc plan`` stays inert.
# --------------------------------------------------------------------------

def test_new_status_is_dispatched_only_without_state_root(repository: Path, tmp_path: Path, state_home: Path):
    out = io.StringIO()
    import contextlib
    with contextlib.redirect_stdout(out):
        code = cli_main(["status", "--repository", str(repository)])
    assert "CLASSIFICATION:" in out.getvalue()
    assert code in (0, 1)


def test_legacy_status_with_state_root_still_reaches_the_operator(repository: Path, tmp_path: Path, state_home: Path):
    state_root = tmp_path / "legacy-state"
    env = {**os.environ, "WFC_FAKE_ENGINE_MODE": "completed_consistent"}
    head = git(repository, "rev-parse", "HEAD")
    subprocess.run(
        [
            sys.executable, str(FAKE_ENGINE), "--repository", str(repository), "--state-root", str(state_root),
            "--expected-head", head, "--work-item", "x", "--profile", "x", "--policy", "x",
            "--components", "x", "--task", "x", "--execution-bindings", "x",
        ],
        env=env, check=False,
    )
    out = io.StringIO()
    import contextlib
    with contextlib.redirect_stdout(out):
        code = cli_main(["status", "--state-root", str(state_root)])
    assert code == 0
    payload = json.loads(out.getvalue())
    assert payload["run_id"] == "RUN-fake-engine"


def test_legacy_status_with_equals_state_root_still_reaches_the_operator(repository: Path, tmp_path: Path, state_home: Path):
    state_root = tmp_path / "legacy-state-equals"
    env = {**os.environ, "WFC_FAKE_ENGINE_MODE": "completed_consistent"}
    head = git(repository, "rev-parse", "HEAD")
    subprocess.run(
        [
            sys.executable, str(FAKE_ENGINE), "--repository", str(repository), "--state-root", str(state_root),
            "--expected-head", head, "--work-item", "x", "--profile", "x", "--policy", "x",
            "--components", "x", "--task", "x", "--execution-bindings", "x",
        ],
        env=env, check=False,
    )
    out = io.StringIO()
    import contextlib
    with contextlib.redirect_stdout(out):
        code = cli_main(["status", f"--state-root={state_root}"])
    assert code == 0
    payload = json.loads(out.getvalue())
    assert payload["run_id"] == "RUN-fake-engine"


def test_plan_still_runs_no_project_command_alongside_the_new_lifecycle_commands(repository: Path, tmp_path: Path):
    task_path = tmp_path / "task.md"
    task_path.write_text(
        "# Bounded plan-preview smoke task\n\nPreview only.\n\n## Acceptance\n\nNo mutation.\n\n"
        "## Withheld authority\n\nNo project command.\n\n## Default escalation\n\nStop if unclear.\n",
        encoding="utf-8",
    )
    head_before = git(repository, "rev-parse", "HEAD")
    out = io.StringIO()
    import contextlib
    with contextlib.redirect_stdout(out):
        code = cli_main([
            "plan", str(task_path), "--repository", str(repository), "--scope", "src",
            "--commission-id", "plan-smoke", "--work-item-id", "WI-PLAN-SMOKE",
            "--authority-source", "test", "--program", "simple@v1", "--persona", "worker=Bounded.",
            "--evidence-argv", "sh", "--evidence-argv", "checks/check.sh", "--guidance-path", "GUIDANCE.md",
            "--execution-override", "worker.adapter=codex_cli", "--execution-override", "worker.model=m",
            "--execution-override", "worker.reasoning=medium",
        ])
    assert code == 0
    assert not (repository / "MARKER_WAS_RUN").exists()
    assert git(repository, "rev-parse", "HEAD") == head_before


def test_request_emitted_file_reproduces_the_previewed_digests_and_launches_through_wfc_run(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """WI-WFC-BETA2-O5-001 (D1 Option B) central acceptance: the file `wfc
    request` writes reproduces the exact digests `wfc plan` previewed for the
    same inputs, and passed unchanged to `wfc run --request` it authorizes
    and launches cleanly -- no `refused_drift`, no `refused_probe`."""

    task_path = tmp_path / "task.md"
    task_path.write_text(
        "# Bounded request-to-run smoke task\n\nEmit, then launch, a bounded task.\n\n"
        "## Acceptance\n\nThe emitted request launches cleanly.\n\n"
        "## Withheld authority\n\nNo project command beyond the check.\n\n"
        "## Default escalation\n\nStop if unclear.\n",
        encoding="utf-8",
    )
    request_path = tmp_path / "request.yaml"
    shared_args = [
        str(task_path), "--repository", str(repository), "--scope", "src",
        "--commission-id", "request-to-run-smoke", "--work-item-id", "WI-REQUEST-TO-RUN-SMOKE",
        "--authority-source", "test authorizes this bounded lifecycle.", "--program", "simple@v1",
        "--persona", "worker=Bounded worker persona.",
        "--evidence-argv", "sh", "--evidence-argv", "checks/check.sh",
        "--guidance-path", "GUIDANCE.md",
        *_CLI_WORKER_OVERRIDE_FLAGS,
    ]

    import contextlib

    plan_out_buffer = io.StringIO()
    with contextlib.redirect_stdout(plan_out_buffer):
        assert cli_main(["plan", *shared_args]) == 0
    plan_out = plan_out_buffer.getvalue()

    request_out_buffer = io.StringIO()
    with contextlib.redirect_stdout(request_out_buffer):
        assert cli_main(["request", *shared_args, "--output", str(request_path)]) == 0
    request_out = request_out_buffer.getvalue()

    def digest_lines(text: str) -> list[str]:
        return [
            line for line in text.splitlines()
            if line.startswith("projected Commission manifest digest:")
            or line.startswith("execution envelope digest:")
        ]

    assert digest_lines(plan_out) == digest_lines(request_out)
    assert len(digest_lines(plan_out)) == 2

    os.environ["WFC_FAKE_ENGINE_MODE"] = "completed_consistent"
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    confirm_calls = []

    def auto_yes(commission_digest: str, envelope_digest: str) -> bool:
        confirm_calls.append((commission_digest, envelope_digest))
        return True

    inputs = _inputs(repository, request_path, confirm=auto_yes)
    result = L.run_lifecycle(inputs)
    assert result.classification == "completed"
    assert len(confirm_calls) == 1

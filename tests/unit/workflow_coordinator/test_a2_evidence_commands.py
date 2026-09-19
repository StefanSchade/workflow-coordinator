"""Bounded acceptance for carve-out A2 evidence command authority."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from scripts.workflow_coordinator import evidence
from scripts.workflow_coordinator.adapter import AgentExecutionOutcome
from scripts.workflow_coordinator.evidence import (
    CWD_POLICY,
    ENVIRONMENT_POLICY,
    PRODUCTION_EVIDENCE_RUNNER,
    EvidencePreparationError,
    authored_argv_digest,
    observation_error,
    prepare_evidence_commands,
    resolve_executable,
    snapshot_environment,
)
from scripts.workflow_coordinator.model import EvidenceCommand, RunStatus
from scripts.workflow_coordinator.operator import (
    OperatorPreflightError,
    _selected_evidence_names,
)
from scripts.workflow_coordinator.persistence import observation_to_data
from scripts.workflow_coordinator.prompt import PromptComponent, load_agent_operating_contract
from scripts.workflow_coordinator.serialization import (
    ValidationError,
    repository_policy_from_yaml,
    workflow_profile_from_yaml,
)
from scripts.workflow_coordinator.vertical import run_one_hop
from tests.utils.workflow_coordinator import (
    phase,
    prepared_evidence,
    profile,
    route,
    run_for,
    simulated_observation,
)

pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")


def policy_data() -> dict[str, object]:
    return {
        "schema_version": 3,
        "policy_id": "a2",
        "revision": "r1",
        "scope": ["repo"],
        "governance_paths": ["governance"],
        "work_item_scope_ceiling": ["payload.txt"],
        "max_autonomous_hops": 1,
        "actions": ["edit", "commit"],
        "evidence_commands": {
            "check": {"argv": ["git", "--version"], "timeout_seconds": 10}
        },
    }


def load_policy(data: dict[str, object]):
    return repository_policy_from_yaml(yaml.safe_dump(data, sort_keys=False))


def test_v3_accepts_valid_and_explicit_empty_command_mapping():
    assert load_policy(policy_data()).evidence_commands["check"] == EvidenceCommand(
        ("git", "--version"), 10
    )
    empty = policy_data()
    empty["evidence_commands"] = {}
    assert load_policy(empty).evidence_commands == {}


@pytest.mark.parametrize("version", [1, 2])
def test_old_policy_versions_require_v3_migration(version):
    data = policy_data()
    data["schema_version"] = version
    with pytest.raises(ValidationError, match=rf"schema_version {version}.*migration.*3"):
        load_policy(data)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: data.pop("evidence_commands"), "missing fields: evidence_commands"),
        (lambda data: data.update(extra=True), "unknown fields: extra"),
        (
            lambda data: data["evidence_commands"]["check"].pop("argv"),
            "missing fields: argv",
        ),
        (
            lambda data: data["evidence_commands"]["check"].update(shell=True),
            "unknown fields: shell",
        ),
    ],
)
def test_policy_and_command_fields_are_exact(mutate, message):
    data = policy_data()
    mutate(data)
    with pytest.raises(ValidationError, match=message):
        load_policy(data)


def test_duplicate_command_key_is_rejected():
    text = yaml.safe_dump(policy_data(), sort_keys=False).replace(
        "  check:\n", "  check:\n", 1
    )
    duplicate = text + "  check:\n    argv: [git]\n    timeout_seconds: 1\n"
    with pytest.raises(ValidationError, match="duplicate key: check"):
        repository_policy_from_yaml(duplicate)


@pytest.mark.parametrize("name", ["", "Upper", "9start", "with-dash", "a" * 65, "ok\nname"])
def test_command_name_requires_full_match(name):
    data = policy_data()
    data["evidence_commands"] = {name: {"argv": ["git"], "timeout_seconds": 1}}
    with pytest.raises(ValidationError, match="command name must match"):
        load_policy(data)


@pytest.mark.parametrize(
    "argv",
    [[], [""], [1], ["git", 1], ["git\0bad"], ["git", "bad\0arg"]],
)
def test_argv_rejects_empty_executable_non_strings_and_nul(argv):
    data = policy_data()
    data["evidence_commands"]["check"]["argv"] = argv
    with pytest.raises(ValidationError, match="argv"):
        load_policy(data)


def test_empty_later_argv_item_is_preserved():
    data = policy_data()
    data["evidence_commands"]["check"]["argv"] = ["git", ""]
    assert load_policy(data).evidence_commands["check"].argv == ("git", "")


@pytest.mark.parametrize("timeout", [True, 1.5, 0, -1, 901])
def test_timeout_is_non_boolean_integer_in_hard_range(timeout):
    data = policy_data()
    data["evidence_commands"]["check"]["timeout_seconds"] = timeout
    with pytest.raises(ValidationError, match="timeout_seconds"):
        load_policy(data)


def test_duplicate_phase_reference_is_rejected_only_by_serialization():
    document = """schema_version: 1
profile_id: duplicate
revision: r1
phases:
  work:
    role: worker
    components: {role: [], modifiers: [], repository_policy: []}
    durable_artifact: none
    authorized_scope: []
    authorized_actions: []
    human_gates: []
    evidence: [check, check]
    review: null
    routes: {done: {effect: complete}}
"""
    with pytest.raises(ValidationError, match="evidence references must be unique"):
        workflow_profile_from_yaml(document)


def test_reference_coherence_unused_definitions_and_reuse_across_phases(tmp_path):
    command = EvidenceCommand(("git", "--version"), 10)
    policy = replace(
        load_policy(policy_data()),
        evidence_commands={
            "check": command,
            "unused": EvidenceCommand(("./definitely-missing",), 10),
        },
    )
    workflow = profile(
        {
            "one": replace(phase({"next": route("goto", "two")}), evidence=("check",)),
            "two": replace(phase({"done": route("complete")}), evidence=("check",)),
        }
    )
    assert _selected_evidence_names(workflow, policy) == ("check",)
    assert prepare_evidence_commands(
        ("check",), policy.evidence_commands, tmp_path
    )[0].command_name == "check"
    unknown = replace(workflow, phases={"one": replace(workflow.phases["one"], evidence=("absent",))})
    with pytest.raises(OperatorPreflightError, match="undefined evidence command"):
        _selected_evidence_names(unknown, policy)


def make_executable(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_resolution_absolute_relative_bare_empty_path_and_symlink(tmp_path):
    target = make_executable(tmp_path / "tool")
    link = tmp_path / "link"
    link.symlink_to(target.name)
    inherited = snapshot_environment({"PATH": str(tmp_path)})
    empty = snapshot_environment({"PATH": ""})

    absolute = resolve_executable(str(target), tmp_path, inherited)
    relative = resolve_executable("./tool", tmp_path, inherited)
    bare = resolve_executable("tool", tmp_path, inherited)
    empty_path = resolve_executable("tool", tmp_path, empty)
    symlink = resolve_executable("./link", tmp_path, inherited)

    assert {absolute.invocation_path, relative.invocation_path, bare.invocation_path, empty_path.invocation_path} == {str(target)}
    assert symlink.invocation_path == str(link)
    assert symlink.canonical_path == str(target)
    assert symlink.stat_fingerprint == absolute.stat_fingerprint
    assert empty.path_source == "inherited"


def test_relative_path_entry_is_rooted_at_repository(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    executable = make_executable(tools / "relative-tool")
    snapshot = snapshot_environment({"PATH": "tools"})
    assert resolve_executable(
        "relative-tool", tmp_path, snapshot
    ).invocation_path == str(executable)


def test_missing_and_non_executable_selected_commands_reject_admission(tmp_path):
    non_executable = tmp_path / "noexec"
    non_executable.write_text("no\n", encoding="utf-8")
    for argv0 in ("./missing", "./noexec"):
        with pytest.raises(EvidencePreparationError, match="executable regular file"):
            prepare_evidence_commands(
                ("check",), {"check": EvidenceCommand((argv0,), 1)}, tmp_path
            )


@pytest.mark.parametrize("timeout", [0, 901, True, 1.5])
def test_in_memory_timeout_must_match_hard_runtime_invariant(tmp_path, timeout):
    with pytest.raises(EvidencePreparationError, match="timeout_seconds"):
        prepare_evidence_commands(
            ("check",), {"check": EvidenceCommand(("git",), timeout)}, tmp_path
        )


def test_environment_snapshot_missing_path_uses_os_defpath_and_is_immutable():
    source = {"A2_VALUE": "before"}
    snapshot = snapshot_environment(source)
    source["A2_VALUE"] = "after"
    assert snapshot.values["PATH"] == os.defpath
    assert snapshot.path_source == "os.defpath"
    assert snapshot.values["A2_VALUE"] == "before"
    with pytest.raises(TypeError):
        snapshot.values["A2_VALUE"] = "mutation"  # type: ignore[index]


def test_execution_is_literal_rooted_devnull_and_preserves_empty_arg(tmp_path):
    literal_args = ["$", ";", "*", "$(touch nope)", ""]
    code = (
        "import os,sys; "
        f"assert os.getcwd() == {str(tmp_path)!r}; "
        "assert sys.stdin.buffer.read() == b''; "
        f"assert sys.argv[1:] == {literal_args!r}; "
        "assert os.environ['A2_VALUE'] == 'snapshotted'; "
        "sys.stdout.buffer.write(b'out'); "
        "sys.stderr.buffer.write(b'err')"
    )
    authored = (sys.executable, "-c", code, *literal_args)
    prepared = prepare_evidence_commands(
        ("literal",), {"literal": EvidenceCommand(authored, 10)}, tmp_path,
        environ={"PATH": os.defpath, "A2_VALUE": "snapshotted"},
    )[0]
    observation = PRODUCTION_EVIDENCE_RUNNER.observe(prepared)
    assert observation.status == "pass" and observation.exit_code == 0
    assert observation.command[1:] == authored[1:]
    assert observation.command[-1] == ""
    assert observation.argv_digest == authored_argv_digest(authored)
    assert observation.cwd_policy == CWD_POLICY
    assert observation.environment_policy == ENVIRONMENT_POLICY
    assert observation.stdout_byte_count == 3 and observation.stdout_body_omitted is True
    assert observation.stderr_byte_count == 3
    assert observation.stderr_sha256 == hashlib.sha256(b"err").hexdigest()
    assert "snapshotted" not in observation.summary
    assert not (tmp_path / "nope").exists()


def test_valid_production_and_simulated_observations_pass_boundary_validation(tmp_path):
    prepared = prepare_evidence_commands(
        ("valid",), {"valid": EvidenceCommand((sys.executable, "-c", "pass"), 10)}, tmp_path
    )[0]
    assert observation_error(
        PRODUCTION_EVIDENCE_RUNNER.observe(prepared), expected=prepared
    ) is None
    assert observation_error(simulated_observation(prepared), expected=prepared) is None


@pytest.mark.parametrize(("exit_code", "status"), [(0, "pass"), (7, "fail")])
def test_exit_taxonomy_and_complete_empty_stream_facts(tmp_path, exit_code, status):
    prepared = prepare_evidence_commands(
        ("exit",),
        {"exit": EvidenceCommand((sys.executable, "-c", f"raise SystemExit({exit_code})"), 10)},
        tmp_path,
    )[0]
    observation = PRODUCTION_EVIDENCE_RUNNER.observe(prepared)
    assert observation.status == status and observation.exit_code == exit_code
    assert observation.stdout_byte_count == observation.stderr_byte_count == 0
    assert observation.stdout_sha256 == hashlib.sha256(b"").hexdigest()
    assert observation.stdout_body_omitted is False


def test_signal_is_error_with_complete_streams(tmp_path):
    prepared = prepare_evidence_commands(
        ("signal",),
        {"signal": EvidenceCommand((sys.executable, "-c", "import os,signal; os.kill(os.getpid(), signal.SIGTERM)"), 10)},
        tmp_path,
    )[0]
    observation = PRODUCTION_EVIDENCE_RUNNER.observe(prepared)
    assert observation.status == "error" and observation.signal == signal.SIGTERM
    assert observation.error_class == "signal" and observation.exit_code is None
    assert observation.stdout_byte_count == 0


def test_runtime_resolution_failure_has_unavailable_streams(tmp_path):
    executable = make_executable(tmp_path / "vanishes")
    prepared = prepare_evidence_commands(
        ("vanishes",), {"vanishes": EvidenceCommand((str(executable),), 10)}, tmp_path
    )[0]
    executable.unlink()
    observation = PRODUCTION_EVIDENCE_RUNNER.observe(prepared)
    assert observation.status == "error"
    assert observation.invocation_path is None
    assert observation.stdout_byte_count is observation.stdout_sha256 is None
    assert observation.stdout_body_omitted is None


def test_spawn_permission_error_is_classified_and_streams_unavailable(tmp_path, monkeypatch):
    prepared = prepare_evidence_commands(
        ("spawn",), {"spawn": EvidenceCommand((sys.executable, "-c", "pass"), 10)}, tmp_path
    )[0]

    def denied(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(evidence.subprocess, "Popen", denied)
    observation = PRODUCTION_EVIDENCE_RUNNER.observe(prepared)
    assert observation.status == "error"
    assert observation.error_class == "spawn:PermissionError"
    assert observation.stdout_byte_count is None


def test_subprocess_communication_failure_is_infrastructure_error(tmp_path, monkeypatch):
    prepared = prepare_evidence_commands(
        ("communication",),
        {"communication": EvidenceCommand((sys.executable, "-c", "pass"), 10)},
        tmp_path,
    )[0]

    class BrokenProcess:
        returncode = 0
        stdout = None
        stderr = None

        def communicate(self, *, timeout):
            raise RuntimeError("broken pipe machinery")

        def poll(self):
            return 0

        def wait(self):
            return 0

    monkeypatch.setattr(evidence.subprocess, "Popen", lambda *args, **kwargs: BrokenProcess())
    observation = PRODUCTION_EVIDENCE_RUNNER.observe(prepared)
    assert observation.status == "error"
    assert observation.error_class == "communication:RuntimeError"
    assert observation.stdout_byte_count is None


def test_timeout_kills_and_reaps_direct_child(tmp_path):
    pid_file = tmp_path / "pid"
    code = f"import os,time,pathlib; pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(30)"
    prepared = prepare_evidence_commands(
        ("timeout",), {"timeout": EvidenceCommand((sys.executable, "-c", code), 1)}, tmp_path
    )[0]
    observation = PRODUCTION_EVIDENCE_RUNNER.observe(prepared)
    pid = int(pid_file.read_text())
    assert observation.status == "error" and observation.error_class == "timeout"
    assert observation.stdout_byte_count is None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_exact_attempt_projection_has_no_output_body_or_combined_digest(tmp_path):
    prepared = prepare_evidence_commands(
        ("audit",), {"audit": EvidenceCommand((sys.executable, "-c", "print('secret-body')"), 10)}, tmp_path
    )[0]
    observation = PRODUCTION_EVIDENCE_RUNNER.observe(prepared)
    data = observation_to_data(observation)
    assert set(data) == {
        "provider", "status", "command_name", "command", "summary", "artifact_ref",
        "argv_digest", "authored_argv0", "timeout_seconds", "cwd_policy",
        "invocation_path", "canonical_path", "stat_fingerprint", "environment_policy",
        "path_source", "path_digest", "exit_code", "signal", "error_class",
        "stdout_byte_count", "stdout_sha256", "stdout_body_omitted",
        "stderr_byte_count", "stderr_sha256", "stderr_body_omitted",
    }
    assert data["provider"] == data["command_name"] == "audit"
    assert "digest" not in data
    assert not any(key in data for key in ("stdout", "stderr", "output", "body"))


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *args), check=True, text=True, capture_output=True
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    git(tmp_path, "init", str(repository))
    git(repository, "config", "user.email", "fixture@example.invalid")
    git(repository, "config", "user.name", "Fixture")
    (repository / "payload.txt").write_text("baseline\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "test: baseline")
    return repository


class CompletedAdapter:
    def execute(self, request):
        result = {
            "schema_version": 1, "outcome": "completed", "requested_route": "done",
            "scope_changed": False, "requires_human": False, "escalation": None,
            "findings": [], "claimed_commits": [], "commit_intent": None,
            "evidence_refs": [], "summary": "done",
        }
        envelope = {
            "schema_version": 1, "run_id": request.invocation.run_id,
            "hop_id": request.invocation.hop_id,
            "invocation_id": request.invocation.invocation_id, "result": result,
        }
        return AgentExecutionOutcome(json.dumps(envelope), 0, False, "", "")


@pytest.mark.parametrize("mutation", ["dirty", "head"])
def test_post_evidence_git_reobservation_preempts_normal_completion(repository, mutation):
    workflow = profile({
        "work": replace(
            phase({"done": route("complete")}),
            authorized_scope=("payload.txt",),
            evidence=("check",),
        )
    })
    head = git(repository, "rev-parse", "HEAD")
    run = replace(run_for(workflow, budget=1), current_head=head, baseline_head=head)
    commands = prepared_evidence(repository, ("check",))

    class MutatingRunner:
        def observe(self, command):
            if mutation == "dirty":
                (repository / "payload.txt").write_text("dirty\n", encoding="utf-8")
            else:
                git(repository, "commit", "--allow-empty", "-m", "test: evidence moved HEAD")
            return simulated_observation(command)

    vertical = run_one_hop(
        run, workflow, repository, CompletedAdapter(),
        {"worker": PromptComponent("worker", "worker", head, "worker"),
         "testing": PromptComponent("testing", "testing", head, "testing")},
        agent_operating_contract=load_agent_operating_contract(), task_payload="task", invocation_id="INV-A2",
        evidence_runner=MutatingRunner(), evidence_commands=commands,
    )
    assert vertical.reduction.run.status is not RunStatus.COMPLETED
    expected = "worktree is not clean" if mutation == "dirty" else "HEAD differs"
    assert expected in (vertical.reduction.run.stop_reason or "")


def test_phase_reference_order_is_execution_order(repository):
    workflow = profile({
        "work": replace(
            phase({"done": route("complete")}),
            authorized_scope=("payload.txt",),
            evidence=("second", "first"),
        )
    })
    head = git(repository, "rev-parse", "HEAD")
    run = replace(run_for(workflow, budget=1), current_head=head, baseline_head=head)
    commands = prepared_evidence(repository, ("first", "second"))

    class OrderedRunner:
        calls: list[str] = []

        def observe(self, command):
            self.calls.append(command.command_name)
            return simulated_observation(command)

    runner = OrderedRunner()
    completed = run_one_hop(
        run, workflow, repository, CompletedAdapter(),
        {"worker": PromptComponent("worker", "worker", head, "worker"),
         "testing": PromptComponent("testing", "testing", head, "testing")},
        agent_operating_contract=load_agent_operating_contract(), task_payload="task", invocation_id="INV-ORDER",
        evidence_runner=runner, evidence_commands=commands,
    )
    assert completed.reduction.run.status is RunStatus.COMPLETED
    assert runner.calls == ["second", "first"]

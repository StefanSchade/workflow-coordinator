"""Focused, fake-process coverage for the declarative N=1 operator."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.workflow_coordinator import operator
from scripts.workflow_coordinator.operator import main
from scripts.workflow_coordinator.operator import _pinned_text
from scripts.workflow_coordinator.serialization import ValidationError, component_manifest_from_yaml


pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")


def git(repository: Path, *args: str) -> str:
    return subprocess.run(("git", "-C", str(repository), *args), text=True, capture_output=True, check=True).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(("git", "init", str(repo)), check=True, capture_output=True)
    for key, value in (("user.email", "fixture@example.invalid"), ("user.name", "Fixture")):
        subprocess.run(("git", "-C", str(repo), "config", key, value), check=True)
    (repo / "PROOF.md").write_text("before\n")
    (repo / "worker.txt").write_text("worker guidance\n")
    (repo / "task.txt").write_text("pinned task\n")
    (repo / "work.yml").write_text("""schema_version: 1
work_item_id: WI-OP
authority_source: fixture
profile_ref: profile.yml
repository: repo
scope: [PROOF.md]
initial_phase: work
max_autonomous_hops: 1
""")
    (repo / "policy.yml").write_text("""schema_version: 1
policy_id: fixture
revision: "1"
scope: [repo]
actions: [edit, commit]
evidence_states: [local]
""")
    (repo / "profile.yml").write_text("""schema_version: 1
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
""")
    (repo / "components.yml").write_text("""schema_version: 1
components:
  worker:
    path: worker.txt
""")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "test: baseline")
    return repo


def args(repository: Path, state: Path, executable: str) -> list[str]:
    return ["--repository", str(repository), "--state-root", str(state), "--expected-head", git(repository, "rev-parse", "HEAD"), "--work-item", "work.yml", "--profile", "profile.yml", "--policy", "policy.yml", "--components", "components.yml", "--task", "task.txt", "--timeout-seconds", "2", "--executable", executable]


def fake(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "fake-codex"
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(0o755)
    return path


def commit(repository: Path, message: str = "test: fixture change") -> str:
    git(repository, "add", ".")
    git(repository, "commit", "-m", message)
    return git(repository, "rev-parse", "HEAD")


def malformed_fake(tmp_path: Path, counter: Path) -> Path:
    return fake(tmp_path, f'''import pathlib, sys
counter=pathlib.Path({str(counter)!r})
counter.write_text(str(int(counter.read_text()) + 1) if counter.exists() else "1")
pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text("{{")
''')


def test_manifest_is_strict_and_repository_relative():
    assert component_manifest_from_yaml("schema_version: 1\ncomponents:\n  x:\n    path: x.txt\n")["x"].path == "x.txt"
    with pytest.raises(ValidationError, match="unknown"):
        component_manifest_from_yaml("schema_version: 1\ncomponents: {}\nextra: true\n")
    with pytest.raises(ValidationError, match="duplicate"):
        component_manifest_from_yaml("schema_version: 1\nschema_version: 1\ncomponents: {}\n")
    with pytest.raises(ValidationError, match="repository-relative"):
        component_manifest_from_yaml("schema_version: 1\ncomponents:\n  x:\n    path: ../x.txt\n")


def test_head_mismatch_and_existing_root_fail_before_fake_execution(repository: Path, tmp_path: Path):
    counter = tmp_path / "counter"
    executable = fake(tmp_path, f"import pathlib\npathlib.Path({str(counter)!r}).write_text('called')\n")
    command = args(repository, tmp_path / "state", str(executable))
    command[command.index("--expected-head") + 1] = "0" * 40
    assert main(command) == 2 and not (tmp_path / "state").exists() and not counter.exists()
    state = tmp_path / "existing"
    state.mkdir()
    assert main(args(repository, state, str(executable))) == 2 and not counter.exists()


def test_fake_success_is_one_hop_and_writes_compact_operator_evidence(repository: Path, tmp_path: Path):
    counter = tmp_path / "counter"
    body = f'''import json, pathlib, sys
args=sys.argv[1:]; repo=pathlib.Path(args[args.index("--cd")+1]); output=pathlib.Path(args[args.index("--output-last-message")+1])
counter=pathlib.Path({str(counter)!r}); counter.write_text(str(int(counter.read_text()) + 1) if counter.exists() else "1")
repo.joinpath("PROOF.md").write_text("after\\n")
prompt=sys.stdin.read()
def fact(name): return prompt.split(name + "=", 1)[1].split("\\n", 1)[0]
output.write_text(json.dumps({{"schema_version":1,"run_id":fact("run_id"),"hop_id":fact("hop_id"),"invocation_id":fact("invocation_id"),"result":{{"schema_version":1,"outcome":"completed","requested_route":"done","scope_changed":False,"requires_human":False,"escalation":None,"findings":[],"claimed_commits":[],"commit_intent":{{"paths":["PROOF.md"],"subject":"docs(workflow): WI-OP H001 fixture"}},"evidence_refs":[],"summary":"done"}}}}))
'''
    executable = fake(tmp_path, body)
    state = tmp_path / "state"
    assert main(args(repository, state, str(executable))) == 0
    plan, summary = json.loads((state / "operator-plan.json").read_text()), json.loads((state / "operator-summary.json").read_text())
    assert counter.read_text() == "1" and plan["max_autonomous_hops"] == 1 and plan["retry"] is False
    assert summary["run_status"] == "completed" and summary["hop_used"] == 1 and summary["worktree_clean"] is True
    assert git(repository, "status", "--porcelain") == ""


def test_n_greater_than_one_rejects_pinned_clean_input_before_execution(repository: Path, tmp_path: Path, capsys):
    counter = tmp_path / "counter"
    executable = malformed_fake(tmp_path, counter)
    work = repository / "work.yml"
    work.write_text(work.read_text().replace("max_autonomous_hops: 1", "max_autonomous_hops: 2"))
    expected_head = commit(repository)
    command = args(repository, tmp_path / "state", str(executable))
    assert command[command.index("--expected-head") + 1] == expected_head
    assert main(args(repository, tmp_path / "state", str(executable))) == 2
    assert "max_autonomous_hops == 1" in capsys.readouterr().err
    assert not counter.exists()


def test_dirty_and_malformed_results_do_not_retry(repository: Path, tmp_path: Path):
    counter = tmp_path / "counter"
    executable = fake(
        tmp_path,
        f'''import pathlib, sys
pathlib.Path({str(counter)!r}).write_text(str(int(pathlib.Path({str(counter)!r}).read_text()) + 1) if pathlib.Path({str(counter)!r}).exists() else "1")
pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text("{{")
''',
    )
    (repository / "PROOF.md").write_text("dirty\n")
    assert main(args(repository, tmp_path / "dirty-state", str(executable))) == 2 and not counter.exists()
    git(repository, "checkout", "--", "PROOF.md")
    state = tmp_path / "malformed-state"
    assert main(args(repository, state, str(executable))) == 1
    summary = json.loads((state / "operator-summary.json").read_text())
    assert counter.read_text() == "1" and summary["run_status"] == "reconcile_required" and summary["hop_used"] == 1


def test_post_provider_summary_failure_is_attempt_failure_with_evidence(repository: Path, tmp_path: Path, monkeypatch):
    counter = tmp_path / "counter"
    executable = fake(
        tmp_path,
        f'''import json, pathlib, sys
args=sys.argv[1:]; output=pathlib.Path(args[args.index("--output-last-message") + 1])
counter=pathlib.Path({str(counter)!r}); counter.write_text(str(int(counter.read_text()) + 1) if counter.exists() else "1")
prompt=sys.stdin.read()
def fact(name): return prompt.split(name + "=", 1)[1].split("\\n", 1)[0]
output.write_text(json.dumps({{"schema_version":1,"run_id":fact("run_id"),"hop_id":fact("hop_id"),"invocation_id":fact("invocation_id"),"result":{{"schema_version":1,"outcome":"completed","requested_route":"done","scope_changed":False,"requires_human":False,"escalation":None,"findings":[],"claimed_commits":[],"commit_intent":None,"evidence_refs":[],"summary":"done"}}}}))
''',
    )
    monkeypatch.setattr(operator, "_summary", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("post-provider observation failed")))
    state = tmp_path / "state"
    assert main(args(repository, state, str(executable))) == 1
    summary = json.loads((state / "operator-summary.json").read_text())
    assert counter.read_text() == "1"
    assert (state / "operator-plan.json").exists()
    assert summary["operator_status"] == "failed" and summary["operator_error"] == "post-provider observation failed"


def test_durable_required_and_optional_preflight_branches(repository: Path, tmp_path: Path):
    counter = tmp_path / "counter"
    executable = malformed_fake(tmp_path, counter)
    profile = repository / "profile.yml"
    profile.write_text(profile.read_text().replace("durable_artifact: none", "durable_artifact: required"))
    commit(repository)
    assert main(args(repository, tmp_path / "required-none", str(executable))) == 2
    assert main([*args(repository, tmp_path / "required-valid", str(executable)), "--durable-artifact", "PROOF.md"]) == 1
    profile.write_text(profile.read_text().replace("durable_artifact: required", "durable_artifact: optional"))
    commit(repository)
    assert main(args(repository, tmp_path / "optional-none", str(executable))) == 1
    assert main([*args(repository, tmp_path / "optional-valid", str(executable)), "--durable-artifact", "PROOF.md"]) == 1
    assert counter.read_text() == "3"


def test_phase_actions_exceeding_repository_policy_reject_before_provider(repository: Path, tmp_path: Path, capsys):
    counter = tmp_path / "counter"
    executable = malformed_fake(tmp_path, counter)
    profile = repository / "profile.yml"
    profile.write_text(
        profile.read_text().replace(
            "authorized_actions: [edit, commit]", "authorized_actions: [edit, commit, publish]"
        )
    )
    commit(repository)
    assert main(args(repository, tmp_path / "state", str(executable))) == 2
    assert "authorizes actions outside repository policy: publish" in capsys.readouterr().err
    assert not counter.exists()


def test_missing_selected_component_rejects_before_provider(repository: Path, tmp_path: Path, capsys):
    counter = tmp_path / "counter"
    executable = malformed_fake(tmp_path, counter)
    (repository / "components.yml").write_text("schema_version: 1\ncomponents: {}\n")
    commit(repository)
    assert main(args(repository, tmp_path / "state", str(executable))) == 2
    assert "selected component ID is absent from manifest: worker" in capsys.readouterr().err
    assert not counter.exists()


def test_profile_ref_mismatch_rejects_pinned_clean_input_before_provider(repository: Path, tmp_path: Path, capsys):
    counter = tmp_path / "counter"
    executable = malformed_fake(tmp_path, counter)
    work = repository / "work.yml"
    work.write_text(work.read_text().replace("profile_ref: profile.yml", "profile_ref: other.yml"))
    commit(repository)
    assert main(args(repository, tmp_path / "state", str(executable))) == 2
    assert "WorkItem.profile_ref does not identify the supplied profile path" in capsys.readouterr().err
    assert not counter.exists()


def test_pinned_text_reads_committed_revision_not_worktree_content(repository: Path):
    baseline = git(repository, "rev-parse", "HEAD")
    (repository / "task.txt").write_text("later committed task\n")
    commit(repository)
    assert _pinned_text(repository, baseline, "task.txt", context="task") == "pinned task\n"
    assert (repository / "task.txt").read_text() == "later committed task\n"

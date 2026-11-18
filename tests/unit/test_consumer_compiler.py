"""Consumer-neutral BP2b compile/verify coverage against a real non-Python repository.

Every fixture repository here is a disposable, from-scratch Git repository
with no WFC governance/self-development files and no Python-project
assumptions -- the whole point of BP2b.  Package-owned resources come from
this installed checkout's own ``resources/roles/*.txt``; nothing here reads
or writes `governance/`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from workflow_coordinator.commissioning import CommissionError, compile_commission
from workflow_coordinator.frontdoor.config import consumer_config_from_yaml, resolve_execution
from workflow_coordinator.frontdoor.request import consumer_request_from_yaml
from workflow_coordinator.frontdoor.verify import verify_commission


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *args), text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """A from-scratch, non-Python, non-WFC repository."""

    repo = tmp_path / "consumer-repo"
    repo.mkdir()
    subprocess.run(("git", "init", "-q", str(repo)), check=True)
    git(repo, "config", "user.email", "consumer@example.invalid")
    git(repo, "config", "user.name", "Consumer test")
    (repo / "GUIDANCE.md").write_text("Keep changes small and reviewed.\n", encoding="utf-8")
    (repo / "checks").mkdir()
    check = repo / "checks/check.sh"
    check.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    check.chmod(0o755)
    (repo / "src").mkdir()
    (repo / "src/app.txt").write_text("hello\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests/app_test.txt").write_text("hello\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs/reviews").mkdir()
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: seed disposable non-Python consumer repository")
    assert not list(repo.rglob("*.py")) and not (repo / "pyproject.toml").exists()
    assert not (repo / "governance").exists()
    return repo


def simple_request_text(head: str, *, scope: str = "src, tests") -> str:
    return f"""schema_version: 1
commission_id: consumer-simple-test
work_item_id: WI-CONSUMER-SIMPLE-TEST
authority_source: Maintainer authorizes the stated bounded consumer test only.
program: {{id: simple, revision: v1}}
task:
  title: Consumer simple test
  body: Compile the consumer-neutral simple program.
  acceptance: The compiled bundle admits without a provider call.
  withheld_authority: Nothing outside the requested scope is authorized.
  escalation_default: Use a typed scope escalation when another path is needed.
scope: [{scope}]
personas:
  worker: Bounded worker persona.
evidence:
  argv: [sh, checks/check.sh]
  timeout_seconds: 10
max_autonomous_hops: 1
durable_artifacts: {{}}
guidance: {{path: GUIDANCE.md, source_commit: {head}}}
"""


def robust_request_text(
    head: str, *, scope: str = "src, tests, docs",
    broad: str = "docs/reviews/broad.md", focused: str = "docs/reviews/focused.md",
) -> str:
    return f"""schema_version: 1
commission_id: consumer-robust-test
work_item_id: WI-CONSUMER-ROBUST-TEST
authority_source: Maintainer authorizes the stated bounded consumer test only.
program: {{id: robust, revision: v1}}
task:
  title: Consumer robust test
  body: Compile the consumer-neutral robust program.
  acceptance: The compiled bundle admits without a provider call.
  withheld_authority: Nothing outside the requested scope is authorized.
  escalation_default: Use a typed scope escalation when another path is needed.
scope: [{scope}]
personas:
  worker: Bounded worker persona.
  independent_reviewer: Independent adversarial reviewer persona.
evidence:
  argv: [sh, checks/check.sh]
  timeout_seconds: 10
max_autonomous_hops: 5
durable_artifacts:
  broad_review: [{broad}]
  focused_review: [{focused}]
guidance: {{path: GUIDANCE.md, source_commit: {head}}}
"""


def config_text(*, scope: str = "src, tests, docs", roles: str = "worker") -> str:
    bindings = "\n".join(
        f"  {role}: {{adapter: codex_cli, model: model-a, reasoning: medium}}"
        for role in roles.split(",")
    )
    return f"""schema_version: 1
repository_name: consumer-repo
governance_dir: .wfc/commissions
work_item_scope_ceiling: [{scope}]
max_autonomous_hops: 5
default_execution:
{bindings}
"""


def commit_compiled(repository: Path, compiled, message: str = "test: materialize compiled Commission") -> str:
    compiled.write_to(repository)
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", message)
    return git(repository, "rev-parse", "HEAD")


# ---------------------------------------------------------------------------
# Parsing-level refusals (ConsumerRequest / ConsumerConfig)
# ---------------------------------------------------------------------------


def test_consumer_request_rejects_forged_binding_key(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    text = simple_request_text(head)
    forged = text.rstrip("\n") + "\nbindings: {worker: {adapter: codex_cli, model: x, reasoning: low}}\n"
    with pytest.raises(CommissionError, match="forbidden"):
        consumer_request_from_yaml(forged)


def test_consumer_request_rejects_execution_fields_nested_in_evidence(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    text = simple_request_text(head).replace(
        "evidence:\n  argv: [sh, checks/check.sh]\n  timeout_seconds: 10\n",
        "evidence:\n  argv: [sh, checks/check.sh]\n  timeout_seconds: 10\n  provider: codex_cli\n",
    )
    with pytest.raises(CommissionError, match="forbidden"):
        consumer_request_from_yaml(text)


def test_consumer_request_rejects_direct_program(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    text = simple_request_text(head).replace("program: {id: simple, revision: v1}", "program: {id: direct, revision: v1}")
    with pytest.raises(CommissionError, match="simple@v1 and robust@v1"):
        consumer_request_from_yaml(text)


def test_consumer_request_rejects_path_traversal_scope(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    text = simple_request_text(head, scope="../etc")
    with pytest.raises(CommissionError, match="A1-normalized"):
        consumer_request_from_yaml(text)


def test_consumer_request_rejects_bad_sha_guidance(repository: Path) -> None:
    text = simple_request_text("not-a-sha")
    with pytest.raises(CommissionError, match="40-character SHA"):
        consumer_request_from_yaml(text)


def test_consumer_config_rejects_unknown_role(repository: Path) -> None:
    text = config_text().replace("worker:", "observer:")
    with pytest.raises(CommissionError, match="unknown role"):
        consumer_config_from_yaml(text)


# ---------------------------------------------------------------------------
# compile_commission: literal field-for-field equivalence to v1 self-dev rendering
# ---------------------------------------------------------------------------


def test_consumer_compile_simple_matches_v1_apart_from_guidance_and_component_paths(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    request = consumer_request_from_yaml(simple_request_text(head))
    config = consumer_config_from_yaml(config_text())
    compiled = compile_commission(request, config, repository=repository)

    again = compile_commission(request, config, repository=repository)
    assert compiled.texts == again.texts

    phases = yaml.safe_load(compiled.texts[compiled.paths["profile"]])["phases"]
    assert phases == {
        "implement": {
            "role": "worker",
            "components": {"role": ["worker"], "modifiers": ["simple-self-check"], "repository_policy": ["repository_policy"]},
            "durable_artifact": "none",
            "authorized_actions": ["edit", "commit"],
            "human_gates": [],
            "evidence": ["task-check"],
            "review": None,
            "routes": {"accept": {"effect": "complete"}},
            "authorized_scope": ["src", "tests"],
        }
    }
    component_ids = [entry["id"] for entry in compiled.manifest["selected_components"]]
    assert sorted(component_ids) == sorted(["worker", "simple-self-check", "repository_policy"])


def test_consumer_compile_robust_matches_v1_apart_from_guidance_and_component_paths(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    request = consumer_request_from_yaml(robust_request_text(head))
    config = consumer_config_from_yaml(config_text(roles="worker,independent_reviewer"))
    compiled = compile_commission(request, config, repository=repository)

    phases = yaml.safe_load(compiled.texts[compiled.paths["profile"]])["phases"]
    scope = ["src", "tests", "docs"]
    broad = ["docs/reviews/broad.md"]
    focused = ["docs/reviews/focused.md"]
    assert phases == {
        "implement": {"role": "worker", "components": {"role": ["worker"], "modifiers": [], "repository_policy": ["repository_policy"]}, "durable_artifact": "none", "authorized_actions": ["edit", "commit"], "human_gates": [], "evidence": [], "review": None, "routes": {"broad_review": {"effect": "goto", "target": "broad_review"}}, "authorized_scope": scope},
        "broad_review": {"role": "independent_reviewer", "components": {"role": ["reviewer"], "modifiers": [], "repository_policy": ["repository_policy"]}, "durable_artifact": "required", "authorized_actions": ["review", "edit", "commit"], "human_gates": [], "evidence": ["task-check"], "review": {"purpose": "independent adversarial full-scope review of the rendered Commission", "breadth": "broad", "authority": "gate", "context": "fresh", "blocking_policy": "blocking_only", "trigger": "profile_selected", "exit": "declared_route", "finding_authority": ["resolve"], "failed_evidence_route": "correction"}, "routes": {"accept": {"effect": "complete"}, "correction": {"effect": "goto", "target": "correct"}}, "authorized_scope": broad},
        "correct": {"role": "worker", "components": {"role": ["worker"], "modifiers": [], "repository_policy": ["repository_policy"]}, "durable_artifact": "none", "authorized_actions": ["edit", "commit"], "human_gates": [], "evidence": [], "review": None, "routes": {"focused_review": {"effect": "goto", "target": "focused_review"}}, "authorized_scope": scope},
        "focused_review": {"role": "independent_reviewer", "components": {"role": ["reviewer"], "modifiers": [], "repository_policy": ["repository_policy"]}, "durable_artifact": "required", "authorized_actions": ["review", "edit", "commit"], "human_gates": [], "evidence": ["task-check"], "review": {"purpose": "independent focused verification of correction and disposition of the prior broad/focused Findings", "breadth": "focused", "authority": "gate", "context": "fresh", "blocking_policy": "blocking_only", "trigger": "profile_selected", "exit": "declared_route", "finding_authority": ["resolve"], "failed_evidence_route": "correction"}, "routes": {"correction": {"effect": "goto", "target": "correct"}, "renew_broad_review": {"effect": "goto", "target": "broad_review"}}, "authorized_scope": focused},
    }


def test_consumer_compile_rejects_scope_overlapping_governance_dir(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    request = consumer_request_from_yaml(simple_request_text(head, scope=".wfc/commissions"))
    config = consumer_config_from_yaml(config_text(scope=".wfc/commissions"))
    with pytest.raises(CommissionError, match="overlaps"):
        compile_commission(request, config, repository=repository)


def test_consumer_compile_rejects_hops_above_config_ceiling(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    text = simple_request_text(head).replace("max_autonomous_hops: 1", "max_autonomous_hops: 99")
    request = consumer_request_from_yaml(text)
    config = consumer_config_from_yaml(config_text())
    with pytest.raises(CommissionError, match="exceeds the Consumer Config ceiling"):
        compile_commission(request, config, repository=repository)


def test_consumer_compile_rejects_tampered_package_resource(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import workflow_coordinator.commissioning as commissioning

    head = git(repository, "rev-parse", "HEAD")
    request = consumer_request_from_yaml(simple_request_text(head))
    config = consumer_config_from_yaml(config_text())
    monkeypatch.setitem(commissioning._PACKAGE_RESOURCE_DIGESTS, "roles/worker.txt", "0" * 64)
    with pytest.raises(CommissionError, match="digest mismatch"):
        compile_commission(request, config, repository=repository)


# ---------------------------------------------------------------------------
# resolve_execution: separate identity, independent of Commission bytes
# ---------------------------------------------------------------------------


def test_resolve_execution_digest_differs_only_with_binding_and_not_with_role_order() -> None:
    config = consumer_config_from_yaml(config_text())
    envelope_a = resolve_execution(config, {}, ("worker",))
    envelope_b = resolve_execution(config, {"worker": {"model": "model-b"}}, ("worker",))
    again_a = resolve_execution(config, {}, ("worker",))
    assert envelope_a.digest == again_a.digest
    assert envelope_a.digest != envelope_b.digest
    assert envelope_a.configuration.bindings != envelope_b.configuration.bindings


def test_resolve_execution_rejects_role_widening_beyond_the_program() -> None:
    config = consumer_config_from_yaml(config_text(roles="worker,independent_reviewer"))
    with pytest.raises(CommissionError, match="roles the selected program does not use"):
        resolve_execution(config, {}, ("worker",))


def test_resolve_execution_rejects_incomplete_binding() -> None:
    config = consumer_config_from_yaml(config_text())
    with pytest.raises(CommissionError, match="missing"):
        resolve_execution(config, {}, ("worker", "independent_reviewer"))


# ---------------------------------------------------------------------------
# Full acceptance: compile, commit by hand, verify+admit, differing bindings
# ---------------------------------------------------------------------------


def test_consumer_simple_end_to_end_admits_with_differing_execution_digests(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    request = consumer_request_from_yaml(simple_request_text(head))
    config = consumer_config_from_yaml(config_text())
    compiled = compile_commission(request, config, repository=repository)
    expected_head = commit_compiled(repository, compiled)

    envelope_a = resolve_execution(config, {}, ("worker",))
    envelope_b = resolve_execution(config, {"worker": {"model": "model-b"}}, ("worker",))
    assert envelope_a.digest != envelope_b.digest

    # Request/Config are supplied as separately committed inputs for `verify_commission`.
    (repository / "consumer-simple-test-request.yaml").write_text(simple_request_text(head), encoding="utf-8")
    (repository / "consumer-simple-test-config.yaml").write_text(config_text(), encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "test: materialize consumer request/config inputs")
    expected_head = git(repository, "rev-parse", "HEAD")

    compiled_a, result_a = verify_commission(
        "consumer-simple-test-request.yaml", "consumer-simple-test-config.yaml",
        repository=repository, expected_head=expected_head, execution_envelope=envelope_a,
    )
    compiled_b, result_b = verify_commission(
        "consumer-simple-test-request.yaml", "consumer-simple-test-config.yaml",
        repository=repository, expected_head=expected_head, execution_envelope=envelope_b,
    )
    assert compiled_a.texts == compiled_b.texts == compiled.texts
    components_a, components_b = result_a[8], result_b[8]
    assert components_a["worker"].content == components_b["worker"].content
    assert components_a["repository_policy"].content == "Keep changes small and reviewed.\n"
    assert result_a[16] != result_b[16]  # execution_configuration differs with the binding


def test_consumer_robust_end_to_end_admits_both_roles(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    request = consumer_request_from_yaml(robust_request_text(head))
    config = consumer_config_from_yaml(config_text(roles="worker,independent_reviewer"))
    compiled = compile_commission(request, config, repository=repository)
    (repository / "docs/reviews/broad.md").write_text("broad review placeholder\n", encoding="utf-8")
    (repository / "docs/reviews/focused.md").write_text("focused review placeholder\n", encoding="utf-8")
    compiled.write_to(repository)
    (repository / "consumer-robust-test-request.yaml").write_text(robust_request_text(head), encoding="utf-8")
    (repository / "consumer-robust-test-config.yaml").write_text(config_text(roles="worker,independent_reviewer"), encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "test: materialize compiled robust Commission")
    expected_head = git(repository, "rev-parse", "HEAD")

    envelope = resolve_execution(config, {}, ("worker", "independent_reviewer"))
    _, result = verify_commission(
        "consumer-robust-test-request.yaml", "consumer-robust-test-config.yaml",
        repository=repository, expected_head=expected_head, execution_envelope=envelope,
    )
    components = result[8]
    assert components["worker"].content and components["reviewer"].content
    assert components["repository_policy"].content == "Keep changes small and reviewed.\n"


def test_consumer_verify_rejects_tampered_compiled_component(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    request = consumer_request_from_yaml(simple_request_text(head))
    config = consumer_config_from_yaml(config_text())
    compiled = compile_commission(request, config, repository=repository)
    commit_compiled(repository, compiled)
    (repository / "consumer-simple-test-request.yaml").write_text(simple_request_text(head), encoding="utf-8")
    (repository / "consumer-simple-test-config.yaml").write_text(config_text(), encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "test: materialize consumer request/config inputs")

    worker_component = repository / f"{compiled.target_dir}/components/worker.txt"
    worker_component.write_text(worker_component.read_text(encoding="utf-8") + "\ntampered\n", encoding="utf-8")
    git(repository, "add", str(worker_component.relative_to(repository)))
    git(repository, "commit", "-q", "-m", "test: tamper compiled worker component")
    tampered_head = git(repository, "rev-parse", "HEAD")

    envelope = resolve_execution(config, {}, ("worker",))
    with pytest.raises(CommissionError, match="compiled projection equivalence mismatch"):
        verify_commission(
            "consumer-simple-test-request.yaml", "consumer-simple-test-config.yaml",
            repository=repository, expected_head=tampered_head, execution_envelope=envelope,
        )


def test_consumer_verify_rejects_tampered_guidance(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    request = consumer_request_from_yaml(simple_request_text(head))
    config = consumer_config_from_yaml(config_text())
    compiled = compile_commission(request, config, repository=repository)
    compiled.write_to(repository)
    (repository / "consumer-simple-test-request.yaml").write_text(simple_request_text(head), encoding="utf-8")
    (repository / "consumer-simple-test-config.yaml").write_text(config_text(), encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "test: materialize consumer request/config inputs")

    (repository / "GUIDANCE.md").write_text("Tampered guidance.\n", encoding="utf-8")
    git(repository, "add", "GUIDANCE.md")
    git(repository, "commit", "-q", "-m", "test: tamper guidance after pin")
    tampered_head = git(repository, "rev-parse", "HEAD")

    envelope = resolve_execution(config, {}, ("worker",))
    with pytest.raises(CommissionError, match="consumer guidance differs from its pinned source object"):
        verify_commission(
            "consumer-simple-test-request.yaml", "consumer-simple-test-config.yaml",
            repository=repository, expected_head=tampered_head, execution_envelope=envelope,
        )


def test_consumer_verify_rejects_dirty_or_mismatched_head(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    request = consumer_request_from_yaml(simple_request_text(head))
    config = consumer_config_from_yaml(config_text())
    compiled = compile_commission(request, config, repository=repository)
    expected_head = commit_compiled(repository, compiled)
    (repository / "consumer-simple-test-request.yaml").write_text(simple_request_text(head), encoding="utf-8")
    (repository / "consumer-simple-test-config.yaml").write_text(config_text(), encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "test: materialize consumer request/config inputs")
    expected_head = git(repository, "rev-parse", "HEAD")

    envelope = resolve_execution(config, {}, ("worker",))
    with pytest.raises(CommissionError, match="current HEAD does not equal expected HEAD"):
        verify_commission(
            "consumer-simple-test-request.yaml", "consumer-simple-test-config.yaml",
            repository=repository, expected_head="0" * 40, execution_envelope=envelope,
        )

    (repository / "src/app.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(CommissionError, match="worktree preconditions fail"):
        verify_commission(
            "consumer-simple-test-request.yaml", "consumer-simple-test-config.yaml",
            repository=repository, expected_head=expected_head, execution_envelope=envelope,
        )


def test_consumer_verify_rejects_binding_role_mismatch(repository: Path) -> None:
    head = git(repository, "rev-parse", "HEAD")
    request = consumer_request_from_yaml(robust_request_text(head))
    config = consumer_config_from_yaml(config_text(roles="worker,independent_reviewer"))
    compiled = compile_commission(request, config, repository=repository)
    (repository / "docs/reviews/broad.md").write_text("broad review placeholder\n", encoding="utf-8")
    (repository / "docs/reviews/focused.md").write_text("focused review placeholder\n", encoding="utf-8")
    compiled.write_to(repository)
    (repository / "consumer-robust-test-request.yaml").write_text(robust_request_text(head), encoding="utf-8")
    (repository / "consumer-robust-test-config.yaml").write_text(config_text(roles="worker,independent_reviewer"), encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "test: materialize compiled robust Commission")
    expected_head = git(repository, "rev-parse", "HEAD")

    worker_only_config = consumer_config_from_yaml(config_text())
    envelope = resolve_execution(worker_only_config, {}, ("worker",))
    with pytest.raises(CommissionError, match="operator admission refused"):
        verify_commission(
            "consumer-robust-test-request.yaml", "consumer-robust-test-config.yaml",
            repository=repository, expected_head=expected_head, execution_envelope=envelope,
        )

"""BP7 C1: the committed self-development Consumer Config and its guidance.

``.wfc/config.yaml`` and ``.wfc/governance/repository-policy.md`` are
maintainer-owned baseline inputs (not Worker scope); this module only verifies
them against the real parser, per the BP7 cutover plan's C1 acceptance
(``docs/backlog/beta1_bp7_cutover_plan.adoc``, <<config-recommendation>> and
<<sequence>> C1). Every assertion below names an exact committed value so a
change to that value -- not just a missing block -- fails the test.
"""

from __future__ import annotations

from pathlib import Path

from workflow_coordinator.frontdoor.config import (
    EvidenceSettings,
    ExecutionBindingValue,
    consumer_config_from_yaml,
    resolve_evidence,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _REPO_ROOT / ".wfc" / "config.yaml"
_GUIDANCE_PATH = _REPO_ROOT / ".wfc" / "governance" / "repository-policy.md"


def _load_config():
    return consumer_config_from_yaml(_CONFIG_PATH.read_text(encoding="utf-8"))


def test_committed_config_parses_without_error():
    config = _load_config()
    assert config.schema_version == 1


def test_committed_config_repository_name_and_governance_dir():
    config = _load_config()
    assert config.repository_name == "workflow-coordinator"
    assert config.governance_dir == ".wfc/governance"


def test_committed_config_scope_ceiling_and_hop_limit():
    config = _load_config()
    assert config.work_item_scope_ceiling == (
        "src", "tests", "scripts", "docs", "README.md", "pyproject.toml", "Makefile",
        "examples/consumer-demo",
    )
    assert config.max_autonomous_hops == 32


def test_committed_config_default_execution_bindings():
    config = _load_config()
    assert config.default_execution == {
        "worker": ExecutionBindingValue(
            adapter="claude_cli", model="claude-sonnet-5", reasoning="high"
        ),
        "independent_reviewer": ExecutionBindingValue(
            adapter="claude_cli", model="claude-opus-5", reasoning="high"
        ),
    }


def test_resolve_evidence_resolves_to_the_sibling_evidence_repository_with_commit_raw():
    config = _load_config()
    repository_root = Path("/payload/workflow-coordinator")
    # Pure function: an absent HOME/XDG_DATA_HOME must not matter, since the
    # committed config always names an explicit, non-default evidence block.
    settings = resolve_evidence(config, repository_root, {})
    assert settings == EvidenceSettings(
        path=repository_root.parent / "workflow-coordinator-run-evidence",
        raw="commit",
    )


def test_committed_config_text_contains_no_absolute_or_home_path():
    text = _CONFIG_PATH.read_text(encoding="utf-8")
    assert "/home/" not in text
    for line in text.splitlines():
        value = line.split(":", 1)[-1].strip().strip("'\"")
        assert not value.startswith("/"), f"absolute path value in committed config: {line!r}"


def test_committed_guidance_document_exists_and_is_non_empty():
    assert _GUIDANCE_PATH.is_file()
    assert _GUIDANCE_PATH.read_text(encoding="utf-8").strip() != ""

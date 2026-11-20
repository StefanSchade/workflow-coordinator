"""BP6b: the optional ``evidence`` block of a Consumer Config.

Every case here is mutation-sensitive: a test that only exercises the happy
path without asserting an exact value would survive a wrong-field or
wrong-default mutation.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from workflow_coordinator.commissioning import CommissionError
from workflow_coordinator.frontdoor.config import (
    EvidenceConfigValue,
    EvidenceSettings,
    consumer_config_from_yaml,
    resolve_evidence,
)


def _base_config_text(*, evidence_block: str = "") -> str:
    return f"""schema_version: 1
repository_name: consumer-repo
governance_dir: .wfc/commissions
work_item_scope_ceiling: [src, tests]
max_autonomous_hops: 5
default_execution:
  worker: {{adapter: codex_cli, model: model-a, reasoning: medium}}
{evidence_block}"""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_config_without_evidence_block_parses_exactly_as_before_and_defaults_to_local():
    text = _base_config_text()
    config = consumer_config_from_yaml(text)
    assert config.evidence is None
    assert config.raw_text == text


def test_config_without_evidence_block_digests_identically_to_the_pre_bp6b_shape():
    """A config authored before BP6b existed must parse and digest unchanged."""

    text = _base_config_text()
    config = consumer_config_from_yaml(text)
    assert hashlib.sha256(config.raw_text.encode("utf-8")).hexdigest() == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert config.schema_version == 1
    assert config.repository_name == "consumer-repo"
    assert config.work_item_scope_ceiling == ("src", "tests")


def test_config_accepts_a_full_evidence_block():
    text = _base_config_text(evidence_block="evidence:\n  repository: ../consumer-evidence\n  raw: commit\n")
    config = consumer_config_from_yaml(text)
    assert config.evidence == EvidenceConfigValue(repository="../consumer-evidence", raw="commit")


def test_config_evidence_block_defaults_raw_to_local_when_omitted():
    text = _base_config_text(evidence_block="evidence:\n  repository: ../consumer-evidence\n")
    config = consumer_config_from_yaml(text)
    assert config.evidence == EvidenceConfigValue(repository="../consumer-evidence", raw="local")


def test_config_evidence_block_defaults_repository_to_none_when_omitted():
    text = _base_config_text(evidence_block="evidence:\n  raw: discard\n")
    config = consumer_config_from_yaml(text)
    assert config.evidence == EvidenceConfigValue(repository=None, raw="discard")


def test_config_empty_evidence_block_is_accepted_and_fully_defaulted():
    text = _base_config_text(evidence_block="evidence: {}\n")
    config = consumer_config_from_yaml(text)
    assert config.evidence == EvidenceConfigValue(repository=None, raw="local")


@pytest.mark.parametrize("raw_value", ["LOCAL", "committed", "discarded", "", "none"])
def test_config_rejects_invalid_raw_value(raw_value):
    text = _base_config_text(evidence_block=f"evidence:\n  raw: {raw_value!r}\n")
    with pytest.raises(CommissionError, match="evidence.raw must be one of"):
        consumer_config_from_yaml(text)


def test_config_rejects_unknown_evidence_key():
    text = _base_config_text(evidence_block="evidence:\n  raw: local\n  retention_days: 30\n")
    with pytest.raises(CommissionError, match="forbidden field"):
        consumer_config_from_yaml(text)


def test_config_rejects_empty_repository_string():
    text = _base_config_text(evidence_block="evidence:\n  repository: ''\n")
    with pytest.raises(CommissionError, match="non-empty"):
        consumer_config_from_yaml(text)


def test_config_rejects_multiline_repository_string():
    text = _base_config_text(evidence_block="evidence:\n  repository: |\n    line-one\n    line-two\n")
    with pytest.raises(CommissionError, match="single-line"):
        consumer_config_from_yaml(text)


def test_config_rejects_non_mapping_evidence_block():
    text = _base_config_text(evidence_block="evidence: [not, a, mapping]\n")
    with pytest.raises(CommissionError, match="must be a mapping"):
        consumer_config_from_yaml(text)


# ---------------------------------------------------------------------------
# resolve_evidence
# ---------------------------------------------------------------------------


def test_resolve_evidence_default_uses_xdg_data_home_and_repository_name():
    config = consumer_config_from_yaml(_base_config_text())
    settings = resolve_evidence(config, Path("/payload/repo"), {"XDG_DATA_HOME": "/xdg-data"})
    assert settings == EvidenceSettings(path=Path("/xdg-data/wfc/evidence/consumer-repo"), raw="local")


def test_resolve_evidence_default_falls_back_to_home_local_share_when_xdg_data_home_absent():
    config = consumer_config_from_yaml(_base_config_text())
    settings = resolve_evidence(config, Path("/payload/repo"), {"HOME": "/home/fixture-user"})
    assert settings == EvidenceSettings(
        path=Path("/home/fixture-user/.local/share/wfc/evidence/consumer-repo"), raw="local",
    )


def test_resolve_evidence_refuses_when_neither_xdg_data_home_nor_home_is_set():
    config = consumer_config_from_yaml(_base_config_text())
    with pytest.raises(CommissionError, match="HOME is not set"):
        resolve_evidence(config, Path("/payload/repo"), {})


def test_resolve_evidence_resolves_relative_repository_against_the_payload_root():
    text = _base_config_text(evidence_block="evidence:\n  repository: ../consumer-evidence\n  raw: commit\n")
    config = consumer_config_from_yaml(text)
    settings = resolve_evidence(config, Path("/payload/repo"), {"HOME": "/home/fixture-user"})
    assert settings == EvidenceSettings(path=Path("/payload/consumer-evidence"), raw="commit")


def test_resolve_evidence_uses_absolute_repository_as_given():
    text = _base_config_text(evidence_block="evidence:\n  repository: /explicit/evidence-repo\n")
    config = consumer_config_from_yaml(text)
    settings = resolve_evidence(config, Path("/payload/repo"), {"HOME": "/home/fixture-user"})
    assert settings == EvidenceSettings(path=Path("/explicit/evidence-repo"), raw="local")


def test_resolve_evidence_explicit_raw_without_repository_still_uses_the_default_location():
    text = _base_config_text(evidence_block="evidence:\n  raw: discard\n")
    config = consumer_config_from_yaml(text)
    settings = resolve_evidence(config, Path("/payload/repo"), {"XDG_DATA_HOME": "/xdg-data"})
    assert settings == EvidenceSettings(path=Path("/xdg-data/wfc/evidence/consumer-repo"), raw="discard")


def test_resolve_evidence_defaults_to_real_os_environ_when_none_supplied(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "/real-xdg-data")
    config = consumer_config_from_yaml(_base_config_text())
    settings = resolve_evidence(config, Path("/payload/repo"))
    assert settings.path == Path("/real-xdg-data/wfc/evidence/consumer-repo")

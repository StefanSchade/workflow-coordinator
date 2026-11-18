"""Syntax smoke check for the optional historical governance archive."""
from pathlib import Path

import pytest
import yaml


ARCHIVE = Path(__file__).resolve().parents[2] / "governance" / "self-development"


def _parse_archive(directory):
    if not directory.exists() and not directory.is_symlink():
        pytest.skip("historical self-development governance directory is absent")
    assert directory.is_dir(), f"historical governance path is not a directory: {directory}"
    files = sorted(directory.rglob("*.yaml")) + sorted(directory.rglob("*.yml"))
    assert files, f"historical governance directory contains no YAML: {directory}"
    for path in files:
        with path.open(encoding="utf-8") as stream:
            list(yaml.safe_load_all(stream))


def test_historical_governance_yaml_parse_smoke():
    _parse_archive(ARCHIVE)


def test_parse_smoke_only_skips_absent_archive(tmp_path):
    archive = tmp_path / "archive"
    with pytest.raises(pytest.skip.Exception):
        _parse_archive(archive)
    archive.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    with pytest.raises(AssertionError, match="not a directory"):
        _parse_archive(archive)
    archive.unlink()
    archive.mkdir()
    with pytest.raises(AssertionError, match="contains no YAML"):
        _parse_archive(archive)
    (archive / "fixture.yaml").write_text("invalid: [\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        _parse_archive(archive)
    (archive / "fixture.yaml").write_text("valid: true\n", encoding="utf-8")
    _parse_archive(archive)

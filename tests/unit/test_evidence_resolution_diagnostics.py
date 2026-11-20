"""Focused regression tests for BUG-WFC-EVIDENCE-COMMAND-RESOLUTION-DIAGNOSTICS.

Covers only the diagnostics enrichment of the unresolved-executable error:
resolution, fingerprinting, alias handling and preflight refusal semantics are
exercised elsewhere (``tests/unit/test_a2_evidence_commands.py``) and are not
re-asserted here beyond confirming they are unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workflow_coordinator import evidence
from workflow_coordinator.evidence import (
    EvidencePreparationError,
    ExecutableResolutionError,
    prepare_evidence_commands,
    resolve_executable,
    snapshot_environment,
)
from workflow_coordinator.model import EvidenceCommand

pytestmark = pytest.mark.ftr("FTR-AGENT-WORKFLOW-COORDINATOR-P5")


def make_executable(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_unresolved_bare_command_names_command_lists_sibling_and_explains_path(tmp_path):
    make_executable(tmp_path / "python3")
    snapshot = snapshot_environment({"PATH": str(tmp_path)})
    with pytest.raises(ExecutableResolutionError) as excinfo:
        resolve_executable("python", tmp_path, snapshot)
    message = str(excinfo.value)
    assert message.startswith(
        "executable does not resolve to an executable regular file: python"
    )
    assert "python3" in message
    assert "informational only" in message
    assert "prepending" in message and "bin/" in message and "PATH" in message


def test_message_reports_no_siblings_when_none_are_discoverable(tmp_path):
    snapshot = snapshot_environment({"PATH": str(tmp_path)})
    with pytest.raises(ExecutableResolutionError) as excinfo:
        resolve_executable("python", tmp_path, snapshot)
    assert "no discoverable PATH siblings" in str(excinfo.value)


def test_evidence_preparation_error_wraps_enriched_message(tmp_path):
    make_executable(tmp_path / "python3")
    with pytest.raises(EvidencePreparationError) as excinfo:
        prepare_evidence_commands(
            ("task-check",),
            {"task-check": EvidenceCommand(("python", "-m", "pytest"), 10)},
            tmp_path,
            environ={"PATH": str(tmp_path)},
        )
    message = str(excinfo.value)
    assert message.startswith(
        "evidence command task-check: executable does not resolve to an "
        "executable regular file: python"
    )
    assert "python3" in message


def test_sibling_probe_excludes_exact_match_and_unrelated_names(tmp_path):
    make_executable(tmp_path / "python3")
    make_executable(tmp_path / "python")
    make_executable(tmp_path / "ruby")
    (tmp_path / "python-notes.txt").write_text("notes\n", encoding="utf-8")
    snapshot = snapshot_environment({"PATH": str(tmp_path)})
    siblings = evidence._discoverable_path_siblings("python", tmp_path, snapshot)
    assert siblings == ("python3",)


def test_sibling_probe_requires_executable_regular_files(tmp_path):
    (tmp_path / "python3").write_text("not executable\n", encoding="utf-8")
    (tmp_path / "python3x").mkdir()
    snapshot = snapshot_environment({"PATH": str(tmp_path)})
    assert evidence._discoverable_path_siblings("python", tmp_path, snapshot) == ()


def test_sibling_probe_is_bounded(tmp_path):
    limit = evidence.DISCOVERABLE_SIBLING_PROBE_LIMIT
    for index in range(limit + 5):
        make_executable(tmp_path / f"python{index}")
    snapshot = snapshot_environment({"PATH": str(tmp_path)})
    siblings = evidence._discoverable_path_siblings("python", tmp_path, snapshot)
    assert len(siblings) == limit


def test_sibling_probe_is_read_only(tmp_path):
    marker = tmp_path / "marker"
    make_executable(
        tmp_path / "python3", body=f"#!/bin/sh\ntouch {marker}\nexit 0\n"
    )
    snapshot = snapshot_environment({"PATH": str(tmp_path)})
    siblings = evidence._discoverable_path_siblings("python", tmp_path, snapshot)
    assert siblings == ("python3",)
    assert not marker.exists()
    with pytest.raises(ExecutableResolutionError):
        resolve_executable("python", tmp_path, snapshot)
    assert not marker.exists()


@pytest.mark.parametrize("authored_argv0", ["./python", "/usr/bin/python"])
def test_sibling_probe_is_empty_for_path_like_authored_commands(tmp_path, authored_argv0):
    make_executable(tmp_path / "python3")
    snapshot = snapshot_environment({"PATH": str(tmp_path)})
    assert evidence._discoverable_path_siblings(authored_argv0, tmp_path, snapshot) == ()


def test_sibling_probe_symlink_loop_does_not_change_refusal_exception_type(tmp_path):
    loop = tmp_path / "python3"
    loop.symlink_to(loop)
    snapshot = snapshot_environment({"PATH": str(tmp_path)})

    assert evidence._discoverable_path_siblings("python", tmp_path, snapshot) == ()
    with pytest.raises(ExecutableResolutionError):
        resolve_executable("python", tmp_path, snapshot)
    with pytest.raises(EvidencePreparationError):
        prepare_evidence_commands(
            ("task-check",),
            {"task-check": EvidenceCommand(("python", "-m", "pytest"), 10)},
            tmp_path,
            environ={"PATH": str(tmp_path)},
        )


def test_resolution_is_unchanged_for_resolvable_commands_and_no_alias_substitution(tmp_path):
    python3 = make_executable(tmp_path / "python3")
    snapshot = snapshot_environment({"PATH": str(tmp_path)})

    resolution = resolve_executable("python3", tmp_path, snapshot)
    assert resolution.invocation_path == str(python3)
    assert resolution.canonical_path == str(python3)

    # A discoverable sibling (python3) must never substitute for the authored
    # command (python): the unresolvable command is still refused.
    with pytest.raises(ExecutableResolutionError):
        resolve_executable("python", tmp_path, snapshot)

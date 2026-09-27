"""Regression tests for the Project Memory proposal/approval protocol.

The protocol separates three meanings -- transformation invariant, proposal
preview, and operator approval -- and these tests pin the properties that keep
them separate: a reviewed delta is promotable, and an unreviewed one is not.

`sheets-run` is maintainer-only tooling that this suite must not require, so the
importer is replaced through the protocol's documented seam.  What is under test
is the protocol, not the ODS transformation.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY_ROOT / "scripts" / "memory_proposal.py"
MAKEFILE = REPOSITORY_ROOT / "Makefile"
TABLES = (
    "concern_event_xrefs.json",
    "concern_events.json",
    "concerns.json",
    "decisions.json",
)
RETIRED_TARGETS = (
    "memory-import",
    "memory-diff-reimport",
    "memory-check",
    "memory-promote-reimport-checked",
)
PROTOCOL_TARGETS = ("memory-roundtrip-check", "memory-preview", "memory-promote")
needs_make = pytest.mark.skipif(shutil.which("make") is None, reason="GNU Make is not installed")

# Stands in for `sheets-run --config ods_to_json.yaml --out-path <destination>`:
# reads the workbook from the repository it runs in and writes the imported
# tables into the destination directory passed as its final argument.  The
# workbook is a JSON sidecar here; the ODS body itself is not under test.
FAKE_IMPORTER = """\
import json
import sys
from pathlib import Path

destination = Path(sys.argv[-1])
workbook = json.loads(Path("project_memory.ods").read_text(encoding="utf-8"))
mode = workbook["mode"]
if mode == "fail":
    sys.exit(3)
tables = dict(workbook["tables"])
if mode == "partial":
    tables = {name: tables[name] for name in sorted(tables)[:2]}
destination.mkdir(parents=True, exist_ok=True)
for name, rows in tables.items():
    text = "{ not json" if mode == "invalid" else json.dumps(rows, indent=2) + "\\n"
    (destination / name).write_text(text, encoding="utf-8")
(destination / "_meta.yaml").write_text("columns: {}\\n", encoding="utf-8")
"""


def _rows(table: str, suffix: str = "") -> list[dict[str, str]]:
    return [{"id": f"{table}-1", "note": f"{table} row{suffix}"}]


def _serialise(rows: list[dict[str, str]]) -> str:
    return json.dumps(rows, indent=2) + "\n"


class MemoryRepository:
    """A minimal repository carrying the canonical tables and the protocol."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "scripts").mkdir(parents=True)
        shutil.copy(SCRIPT, root / "scripts" / SCRIPT.name)
        shutil.copy(MAKEFILE, root / "Makefile")
        importer = root / "fake_sheets_run.py"
        importer.write_text(FAKE_IMPORTER, encoding="utf-8")
        self.environment = {
            **os.environ,
            "WFC_MEMORY_IMPORT_COMMAND": (
                f"{shlex.quote(sys.executable)} {shlex.quote(str(importer))}"
            ),
        }
        (root / "project_memory" / "canonical").mkdir(parents=True)
        (root / "project_memory" / "staging").mkdir()
        (root / "project_memory" / "staging" / ".gitignore").write_text(
            "*\n!.gitignore\n", encoding="utf-8"
        )
        for name in TABLES:
            self.canonical_table(name).write_text(_serialise(_rows(name)), encoding="utf-8")
        self.write_workbook({name: _rows(name) for name in TABLES})

    def canonical_table(self, name: str) -> Path:
        return self.root / "project_memory" / "canonical" / name

    def staging_table(self, name: str) -> Path:
        return self.root / "project_memory" / "staging" / name

    @property
    def marker(self) -> Path:
        return self.root / "project_memory" / ".proposal" / "proposal.json"

    def write_workbook(self, tables: dict[str, list[dict[str, str]]], mode: str = "ok") -> None:
        (self.root / "project_memory.ods").write_text(
            json.dumps({"mode": mode, "tables": tables}, indent=2), encoding="utf-8"
        )

    def edit_workbook(self, suffix: str = " EDITED", mode: str = "ok") -> None:
        tables = {name: _rows(name) for name in TABLES}
        tables["concerns.json"] = _rows("concerns.json", suffix)
        self.write_workbook(tables, mode=mode)

    def canonical_state(self) -> dict[str, str]:
        return {name: self.canonical_table(name).read_text(encoding="utf-8") for name in TABLES}

    def staging_state(self) -> dict[str, str]:
        return {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted((self.root / "project_memory" / "staging").glob("*.json"))
        }

    def proposal_state(self) -> str | None:
        return self.marker.read_text(encoding="utf-8") if self.marker.is_file() else None

    def run(self, operation: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.root / "scripts" / SCRIPT.name), operation],
            cwd=self.root,
            env=self.environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def make(self, *targets: str, jobs: int = 4) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["make", f"-j{jobs}", *targets, f"PYTHON={sys.executable}"],
            cwd=self.root,
            env=self.environment,
            check=False,
            capture_output=True,
            text=True,
        )


@pytest.fixture
def repository(tmp_path: Path) -> MemoryRepository:
    return MemoryRepository(tmp_path / "repository")


def _preview_edited(repository: MemoryRepository, suffix: str = " EDITED") -> str:
    repository.edit_workbook(suffix)
    completed = repository.run("preview")
    assert completed.returncode == 0, completed.stderr
    return json.loads(repository.marker.read_text(encoding="utf-8"))["staged_proposal"][
        "fingerprint"
    ]


def test_roundtrip_invariant_succeeds_for_an_unedited_workbook(
    repository: MemoryRepository,
) -> None:
    completed = repository.run("roundtrip-check")

    assert completed.returncode == 0, completed.stderr
    assert "Round trip lossless" in completed.stdout
    assert repository.staging_state() == {}
    assert repository.proposal_state() is None


def test_roundtrip_invariant_fails_for_an_edited_workbook_without_touching_staging(
    repository: MemoryRepository,
) -> None:
    repository.edit_workbook()

    completed = repository.run("roundtrip-check")

    assert completed.returncode == 1
    assert "EDITED" in completed.stdout
    assert "Round-trip invariant violated" in completed.stderr
    assert "memory-preview" in completed.stderr
    assert repository.staging_state() == {}
    assert repository.proposal_state() is None


def test_preview_shows_a_non_empty_delta_and_exits_zero(repository: MemoryRepository) -> None:
    repository.edit_workbook()
    baseline = repository.canonical_state()

    completed = repository.run("preview")

    assert completed.returncode == 0, completed.stderr
    assert "Proposed canonical change" in completed.stdout
    assert "+" in completed.stdout and "EDITED" in completed.stdout
    assert repository.canonical_state() == baseline
    assert "EDITED" in repository.staging_state()["concerns.json"]
    marker = json.loads(repository.marker.read_text(encoding="utf-8"))
    assert marker["delta_empty"] is False
    assert marker["expected_tables"] == sorted(TABLES)
    assert set(marker["canonical_baseline"]["tables"]) == set(TABLES)
    assert set(marker["staged_proposal"]["tables"]) == set(TABLES)
    assert marker["canonical_baseline"]["fingerprint"] in completed.stdout
    assert marker["staged_proposal"]["fingerprint"] in completed.stdout


def test_preview_reports_an_empty_delta_for_an_unedited_workbook(
    repository: MemoryRepository,
) -> None:
    completed = repository.run("preview")

    assert completed.returncode == 0, completed.stderr
    assert "no canonical change" in completed.stdout
    marker = json.loads(repository.marker.read_text(encoding="utf-8"))
    assert marker["delta_empty"] is True


def test_promote_without_preview_refuses(repository: MemoryRepository) -> None:
    repository.edit_workbook()
    baseline = repository.canonical_state()

    completed = repository.run("promote")

    assert completed.returncode == 1
    assert "No previewed proposal" in completed.stderr
    assert repository.canonical_state() == baseline


def test_promote_after_preview_applies_the_reviewed_proposal_and_consumes_it(
    repository: MemoryRepository,
) -> None:
    fingerprint = _preview_edited(repository)

    completed = repository.run("promote")

    assert completed.returncode == 0, completed.stderr
    assert fingerprint in completed.stdout
    assert "concerns.json" in completed.stdout
    assert "EDITED" in repository.canonical_state()["concerns.json"]
    assert repository.canonical_state() == repository.staging_state()
    assert repository.proposal_state() is None

    repeated = repository.run("promote")

    assert repeated.returncode == 1
    assert "No previewed proposal" in repeated.stderr


def test_promote_refuses_after_staging_is_mutated(repository: MemoryRepository) -> None:
    _preview_edited(repository)
    baseline = repository.canonical_state()
    proposal = repository.proposal_state()
    repository.staging_table("decisions.json").write_text(
        _serialise(_rows("decisions.json", " TAMPERED")), encoding="utf-8"
    )

    completed = repository.run("promote")

    assert completed.returncode == 1
    assert "Staging no longer holds the proposal that was previewed" in completed.stderr
    assert "memory-preview" in completed.stderr
    assert repository.canonical_state() == baseline
    assert repository.proposal_state() == proposal


def test_promote_refuses_after_canonical_is_mutated(repository: MemoryRepository) -> None:
    _preview_edited(repository)
    repository.canonical_table("decisions.json").write_text(
        _serialise(_rows("decisions.json", " MOVED ON")), encoding="utf-8"
    )
    baseline = repository.canonical_state()

    completed = repository.run("promote")

    assert completed.returncode == 1
    assert "Canonical Project Memory changed after the proposal was previewed" in completed.stderr
    assert repository.canonical_state() == baseline
    assert "EDITED" not in baseline["concerns.json"]


def test_promote_refuses_partial_staging(repository: MemoryRepository) -> None:
    _preview_edited(repository)
    baseline = repository.canonical_state()
    repository.staging_table("decisions.json").unlink()

    completed = repository.run("promote")

    assert completed.returncode == 1
    assert "not the complete Project Memory table set" in completed.stderr
    assert "missing decisions.json" in completed.stderr
    assert repository.canonical_state() == baseline


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("fail", "Workbook import failed"),
        ("partial", "not the complete Project Memory table set"),
        ("invalid", "does not parse as JSON"),
    ],
)
def test_failed_import_preserves_the_previous_valid_proposal(
    repository: MemoryRepository, mode: str, reason: str
) -> None:
    _preview_edited(repository)
    staged = repository.staging_state()
    proposal = repository.proposal_state()
    repository.edit_workbook(" LATER EDIT", mode=mode)

    failed = repository.run("preview")

    assert failed.returncode == 1
    assert reason in failed.stderr
    assert repository.staging_state() == staged
    assert repository.proposal_state() == proposal

    promoted = repository.run("promote")

    assert promoted.returncode == 0, promoted.stderr
    assert "EDITED" in repository.canonical_state()["concerns.json"]
    assert "LATER" not in repository.canonical_state()["concerns.json"]


def test_promotion_applies_the_reviewed_proposal_not_a_later_workbook_edit(
    repository: MemoryRepository,
) -> None:
    _preview_edited(repository)
    repository.edit_workbook(" LATER EDIT")

    completed = repository.run("promote")

    assert completed.returncode == 0, completed.stderr
    assert "EDITED" in repository.canonical_state()["concerns.json"]
    assert "LATER" not in repository.canonical_state()["concerns.json"]


@needs_make
def test_parallel_make_cannot_promote_without_a_preview(repository: MemoryRepository) -> None:
    repository.edit_workbook()
    baseline = repository.canonical_state()

    completed = repository.make("memory-promote")

    assert completed.returncode != 0
    assert "No previewed proposal" in completed.stderr
    assert repository.canonical_state() == baseline


@needs_make
def test_parallel_make_roundtrip_invariant_fails_for_an_edited_workbook(
    repository: MemoryRepository,
) -> None:
    repository.edit_workbook()
    baseline = repository.canonical_state()

    completed = repository.make("memory-roundtrip-check")

    assert completed.returncode != 0
    assert "Round-trip invariant violated" in completed.stderr
    assert repository.canonical_state() == baseline
    assert repository.proposal_state() is None


@needs_make
def test_parallel_make_cannot_bypass_the_preview_to_promotion_binding(
    repository: MemoryRepository,
) -> None:
    repository.edit_workbook()
    baseline = repository.canonical_state()

    previewed = repository.make("memory-preview")

    assert previewed.returncode == 0, previewed.stderr
    assert "EDITED" in previewed.stdout
    assert repository.canonical_state() == baseline

    repository.staging_table("decisions.json").write_text(
        _serialise(_rows("decisions.json", " TAMPERED")), encoding="utf-8"
    )
    tampered = repository.make("memory-promote")

    assert tampered.returncode != 0
    assert "Staging no longer holds the proposal that was previewed" in tampered.stderr
    assert repository.canonical_state() == baseline

    assert repository.make("memory-preview").returncode == 0
    approved = repository.make("memory-promote")

    assert approved.returncode == 0, approved.stderr
    assert "EDITED" in repository.canonical_state()["concerns.json"]

    replayed = repository.make("memory-promote")

    assert replayed.returncode != 0
    assert "No previewed proposal" in replayed.stderr


@needs_make
def test_parallel_make_cannot_promote_a_proposal_that_was_never_displayed(
    repository: MemoryRepository,
) -> None:
    """Asking one parallel invocation for both steps still cannot bypass the display."""
    repository.edit_workbook()
    baseline = repository.canonical_state()

    completed = repository.make("memory-preview", "memory-promote")

    promoted = repository.canonical_state()
    if promoted != baseline:
        # Promotion happened, so exactly the delta it applied must have been shown.
        assert "EDITED" in completed.stdout
        assert promoted["concerns.json"] == repository.staging_state()["concerns.json"]
    else:
        assert completed.returncode != 0


def test_makefile_exposes_the_three_protocol_targets_and_no_retired_name() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")

    for target in PROTOCOL_TARGETS:
        assert f"\n{target}:\n" in text
    for retired in RETIRED_TARGETS:
        assert retired not in text


def test_documentation_describes_the_protocol_targets() -> None:
    for document in (REPOSITORY_ROOT / "README.md", REPOSITORY_ROOT / "project_memory/README.adoc"):
        text = document.read_text(encoding="utf-8")
        for target in PROTOCOL_TARGETS:
            assert target in text, document
        for retired in RETIRED_TARGETS:
            assert retired not in text, document

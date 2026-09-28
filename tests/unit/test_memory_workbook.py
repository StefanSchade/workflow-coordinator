"""Regression tests for the Project Memory spreadsheet round trip.

The contract is deliberately small: canonical JSON and the ODS workbook are the
only persistent representations, and Git is the review boundary.  These tests
pin the properties that keep an import safe -- complete validation before any
canonical write, and refusal to overwrite canonical edits or to write outside
the repository -- plus the structural absence of the retired staged-proposal
protocol.

`sheets-run` is maintainer-only tooling this suite must not require, so the
runner is replaced through the `--sheets-run` seam.  What is under test is the
round trip, not the ODS transformation.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY_ROOT / "scripts" / "memory_workbook.py"
MAKEFILE = REPOSITORY_ROOT / "Makefile"
TABLES = (
    "concern_event_xrefs.json",
    "concern_events.json",
    "concerns.json",
    "decisions.json",
)
MEMORY_TARGETS = ("memory-export", "memory-import", "memory-roundtrip-check")
RETIRED_TARGETS = (
    "memory-preview",
    "memory-promote",
    "memory-diff-reimport",
    "memory-check",
    "memory-promote-reimport-checked",
    "check-sheets-run",
)
# Tokens that could only come from the retired persistent staged-proposal
# protocol: its directories, its approval marker, and its fingerprint algorithm.
RETIRED_MACHINERY = (
    "project_memory/staging",
    ".proposal",
    "proposal.json",
    "staged_proposal",
    "canonical_baseline",
    "sha256",
)
needs_make = pytest.mark.skipif(shutil.which("make") is None, reason="GNU Make is not installed")
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="Git is not installed")

# Stands in for `sheets-run`, dispatching on the pipeline it is given.  The
# workbook is a JSON sidecar here; the ODS body itself is not under test.  Its
# `mode` drives the failure shapes an import must survive without touching
# canonical, and `lossy` breaks the export half of the round trip.
FAKE_SHEETS_RUN = """\
#!/usr/bin/env python3
import json
import sys
from pathlib import Path

arguments = sys.argv[1:]
options = dict(zip(arguments[::2], arguments[1::2]))
destination = Path(options["--out-path"])

if options["--config"].endswith("json_to_ods.yaml"):
    tables = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(Path("project_memory/canonical").glob("*.json"))
    }
    if Path("lossy").exists():
        tables["concerns.json"] = [{"id": row["id"]} for row in tables["concerns.json"]]
    destination.write_text(json.dumps({"mode": "ok", "tables": tables}), encoding="utf-8")
    sys.exit(0)

workbook = json.loads(Path(options.get("--in-path", "project_memory.ods")).read_text("utf-8"))
mode = workbook["mode"]
if mode == "fail":
    sys.exit(3)
tables = dict(workbook["tables"])
if mode == "partial":
    tables = {name: tables[name] for name in sorted(tables)[:2]}
if mode == "extra":
    tables["releases.json"] = []
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
    """A minimal Git repository carrying the canonical tables and the round trip."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "scripts").mkdir(parents=True)
        shutil.copy(SCRIPT, root / "scripts" / SCRIPT.name)
        shutil.copy(MAKEFILE, root / "Makefile")
        self.runner = root / "fake-sheets-run"
        self.runner.write_text(FAKE_SHEETS_RUN, encoding="utf-8")
        self.runner.chmod(0o755)
        (root / "project_memory" / "canonical").mkdir(parents=True)
        for name in TABLES:
            self.canonical_table(name).write_text(_serialise(_rows(name)), encoding="utf-8")
        self.write_workbook({name: _rows(name) for name in TABLES})
        self.git("init", "-q")
        self.git("add", "-A")
        self.git(
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "canonical baseline",
        )

    def canonical_table(self, name: str) -> Path:
        return self.root / "project_memory" / "canonical" / name

    @property
    def workbook(self) -> Path:
        return self.root / "project_memory.ods"

    def git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments], cwd=self.root, check=True, capture_output=True, text=True
        )

    def write_workbook(self, tables: dict[str, list[dict[str, str]]], mode: str = "ok") -> None:
        self.workbook.write_text(json.dumps({"mode": mode, "tables": tables}), encoding="utf-8")

    def edit_workbook(self, suffix: str = " EDITED", mode: str = "ok") -> None:
        tables = {name: _rows(name) for name in TABLES}
        tables["concerns.json"] = _rows("concerns.json", suffix)
        self.write_workbook(tables, mode=mode)

    def canonical_state(self) -> dict[str, str]:
        return {name: self.canonical_table(name).read_text(encoding="utf-8") for name in TABLES}

    def run(self, operation: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(self.root / "scripts" / SCRIPT.name),
                operation,
                "--sheets-run",
                str(self.runner),
            ],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )

    def make(self, *targets: str, jobs: int = 4) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "make",
                f"-j{jobs}",
                *targets,
                f"PYTHON={sys.executable}",
                f"SHEETS_RUN={self.runner}",
            ],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )


@pytest.fixture
def repository(tmp_path: Path) -> MemoryRepository:
    return MemoryRepository(tmp_path / "repository")


# --- export and the transformation invariant ---------------------------------


def test_export_renders_canonical_into_the_workbook_without_changing_it(
    repository: MemoryRepository,
) -> None:
    baseline = repository.canonical_state()
    repository.workbook.unlink()

    completed = repository.run("export")

    assert completed.returncode == 0, completed.stderr
    assert repository.canonical_state() == baseline
    exported = json.loads(repository.workbook.read_text(encoding="utf-8"))["tables"]
    assert set(exported) == set(TABLES)
    assert exported["concerns.json"] == _rows("concerns.json")


def test_roundtrip_check_passes_without_touching_canonical_or_the_workbook(
    repository: MemoryRepository,
) -> None:
    baseline = repository.canonical_state()
    # An edited workbook is irrelevant: the invariant is about the transformation.
    repository.edit_workbook()
    workbook = repository.workbook.read_text(encoding="utf-8")

    completed = repository.run("roundtrip-check")

    assert completed.returncode == 0, completed.stderr
    assert "Round trip lossless" in completed.stdout
    assert repository.canonical_state() == baseline
    assert repository.workbook.read_text(encoding="utf-8") == workbook


def test_roundtrip_check_refuses_a_lossy_transformation(repository: MemoryRepository) -> None:
    baseline = repository.canonical_state()
    (repository.root / "lossy").touch()

    completed = repository.run("roundtrip-check")

    assert completed.returncode == 1
    assert "Round-trip invariant violated" in completed.stderr
    assert "concerns.json" in completed.stdout
    assert repository.canonical_state() == baseline


# --- import ------------------------------------------------------------------


@needs_git
def test_import_writes_an_edited_workbook_into_canonical_as_a_git_change(
    repository: MemoryRepository,
) -> None:
    repository.edit_workbook()
    modes = {name: repository.canonical_table(name).stat().st_mode for name in TABLES}

    completed = repository.run("import")

    assert completed.returncode == 0, completed.stderr
    assert "concerns.json" in completed.stdout
    assert {n: repository.canonical_table(n).stat().st_mode for n in TABLES} == modes
    assert "git diff" in completed.stdout
    assert "EDITED" in repository.canonical_state()["concerns.json"]
    status = repository.git("status", "--porcelain").stdout
    assert " M project_memory/canonical/concerns.json" in status
    assert "EDITED" in repository.git("diff", "--", "project_memory/canonical").stdout

    repository.git("restore", "--", "project_memory/canonical")

    assert "EDITED" not in repository.canonical_state()["concerns.json"]


@needs_git
def test_import_refuses_when_canonical_already_has_working_tree_changes(
    repository: MemoryRepository,
) -> None:
    repository.canonical_table("decisions.json").write_text(
        _serialise(_rows("decisions.json", " HAND EDIT")), encoding="utf-8"
    )
    baseline = repository.canonical_state()
    repository.edit_workbook()

    completed = repository.run("import")

    assert completed.returncode == 1
    assert "already has uncommitted changes" in completed.stderr
    assert repository.canonical_state() == baseline


@needs_git
@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("fail", "Workbook conversion failed"),
        ("partial", "not the complete Project Memory table set"),
        ("extra", "unexpected releases.json"),
        ("invalid", "does not parse as JSON"),
    ],
)
def test_a_failed_or_incomplete_conversion_leaves_canonical_unchanged(
    repository: MemoryRepository, mode: str, reason: str
) -> None:
    baseline = repository.canonical_state()
    repository.edit_workbook(mode=mode)

    completed = repository.run("import")

    assert completed.returncode == 1
    assert reason in completed.stderr
    assert repository.canonical_state() == baseline
    assert repository.git("status", "--porcelain", "--", "project_memory/canonical").stdout == ""


@needs_git
def test_import_refuses_a_canonical_directory_redirected_outside_the_repository(
    repository: MemoryRepository, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    shutil.copytree(repository.root / "project_memory" / "canonical", outside)
    untouched = {path.name: path.read_text(encoding="utf-8") for path in outside.glob("*.json")}
    shutil.rmtree(repository.root / "project_memory" / "canonical")
    (repository.root / "project_memory" / "canonical").symlink_to(outside)
    repository.edit_workbook()

    completed = repository.run("import")

    assert completed.returncode == 1
    assert "outside the repository-owned location" in completed.stderr
    assert {path.name: path.read_text(encoding="utf-8") for path in outside.glob("*.json")} == (
        untouched
    )


@needs_git
@needs_make
def test_make_import_is_one_self_contained_target(repository: MemoryRepository) -> None:
    repository.edit_workbook()

    completed = repository.make("memory-import")

    assert completed.returncode == 0, completed.stderr
    assert "EDITED" in repository.canonical_state()["concerns.json"]


# --- the retired staged-proposal protocol ------------------------------------


def test_no_staged_proposal_machinery_remains() -> None:
    assert not (REPOSITORY_ROOT / "scripts" / "memory_proposal.py").exists()
    assert not (REPOSITORY_ROOT / "project_memory" / "staging").exists()
    assert not (REPOSITORY_ROOT / "project_memory" / ".proposal").exists()
    for path in (
        MAKEFILE,
        SCRIPT,
        REPOSITORY_ROOT / ".gitignore",
        REPOSITORY_ROOT / "scripts" / "wfc_snapshot.py",
        REPOSITORY_ROOT / "project_memory" / "pipelines" / "memory" / "ods_to_json.yaml",
        REPOSITORY_ROOT / "project_memory" / "pipelines" / "memory" / "json_to_ods.yaml",
    ):
        text = path.read_text(encoding="utf-8")
        for retired in (*RETIRED_TARGETS, *RETIRED_MACHINERY):
            assert retired not in text, (path, retired)


def test_makefile_exposes_the_memory_targets_and_no_retired_name() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")

    for target in MEMORY_TARGETS:
        assert f"\n{target}:\n" in text
    for retired in RETIRED_TARGETS:
        assert retired not in text


def test_documentation_describes_the_git_backed_round_trip() -> None:
    for document in (REPOSITORY_ROOT / "README.md", REPOSITORY_ROOT / "project_memory/README.adoc"):
        text = document.read_text(encoding="utf-8")
        for target in MEMORY_TARGETS:
            assert target in text, document
        for retired in RETIRED_TARGETS:
            assert retired not in text, document
        assert "git diff" in text, document

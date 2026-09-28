"""Tests for the standalone WFC repository snapshot tool."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_snapshot_tool_writes_grouped_line_numbered_files_with_toc_and_tree(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    (repository / "src" / "workflow_coordinator" / "resources").mkdir(parents=True)
    (repository / "tests" / "unit").mkdir(parents=True)
    (repository / "docs").mkdir()
    (repository / "governance").mkdir()
    (repository / "project_memory" / "canonical").mkdir(parents=True)
    (repository / "project_memory" / "README.adoc").write_text("= Memory\n", encoding="utf-8")
    (repository / "project_memory" / "canonical" / "concerns.json").write_text(
        '[{"id": "CONC-EXAMPLE"}]\n', encoding="utf-8"
    )
    (repository / "project_memory.ods").write_text("workbook\n", encoding="utf-8")
    # An unrelated `_meta.yaml` must survive: the exclusion is repository-relative,
    # not a bare filename.
    (repository / "src" / "workflow_coordinator" / "resources" / "_meta.yaml").write_text(
        "packaged: true\n", encoding="utf-8"
    )
    (repository / "src" / "workflow_coordinator" / "module.py").write_text(
        "value = 1\n", encoding="utf-8"
    )
    (repository / "tests" / "unit" / "test_module.py").write_text(
        "def test_value(): pass\n", encoding="utf-8"
    )
    (repository / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (repository / "governance" / "policy.yaml").write_text("version: 1\n", encoding="utf-8")
    (repository / "README.md").write_text("# Example\n", encoding="utf-8")
    (repository / ".git").mkdir()
    (repository / ".git" / "config").write_text("ignored\n", encoding="utf-8")
    output = tmp_path / "snapshots"
    script = Path(__file__).resolve().parents[2] / "scripts" / "wfc_snapshot.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--repository",
            str(repository),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert {path.name for path in output.iterdir()} == {
        "wfc_docs_and_governance.txt",
        "wfc_project_memory.txt",
        "wfc_repo_infrastructure.txt",
        "wfc_source_and_tests.txt",
        "wfc_repository_tree.txt",
    }
    for path in output.iterdir():
        lines = path.read_text(encoding="utf-8").splitlines()
        assert all(line.startswith(f"{number:06d} | ") for number, line in enumerate(lines, 1))
        toc_heading = next(
            number for number, line in enumerate(lines) if line.endswith("Table of Contents")
        )
        toc_entries = lines[toc_heading + 3 :]
        for line in toc_entries:
            _, entry = line.split(" | ", 1)
            if not entry:
                break
            target, label = entry.split(" | ", 1)
            target_number = int(target)
            assert lines[target_number - 1].endswith(f"==== File: {label} ====")
    sources = (output / "wfc_source_and_tests.txt").read_text(encoding="utf-8")
    assert "src/workflow_coordinator/module.py" in sources
    assert "src/workflow_coordinator/resources/_meta.yaml" in sources
    assert "packaged: true" in sources
    assert "docs/guide.md" in (output / "wfc_docs_and_governance.txt").read_text(encoding="utf-8")
    memory = (output / "wfc_project_memory.txt").read_text(encoding="utf-8")
    assert "project_memory/README.adoc" in memory
    assert "project_memory/canonical/concerns.json" in memory
    assert "CONC-EXAMPLE" in memory
    tree = (output / "wfc_repository_tree.txt").read_text(encoding="utf-8")
    assert "repository/" in tree
    assert ".git" not in tree
    assert "canonical/" in tree
    assert "project_memory.ods" not in tree


def test_snapshot_tool_defaults_output_to_build_snapshots(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "README.md").write_text("# Example\n", encoding="utf-8")
    script = Path(__file__).resolve().parents[2] / "scripts" / "wfc_snapshot.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--repository", str(repository)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert (repository / "build" / "snapshots" / "wfc_repository_tree.txt").is_file()
    assert not (repository / "wfc_snapshots").exists()

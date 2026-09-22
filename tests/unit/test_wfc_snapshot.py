"""Tests for the standalone WFC repository snapshot tool."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_snapshot_tool_writes_grouped_line_numbered_files_with_toc_and_tree(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    (repository / "src" / "workflow_coordinator").mkdir(parents=True)
    (repository / "tests" / "unit").mkdir(parents=True)
    (repository / "docs").mkdir()
    (repository / "governance").mkdir()
    (repository / "src" / "workflow_coordinator" / "module.py").write_text(
        "value = 1\n", encoding="utf-8"
    )
    (repository / "tests" / "unit" / "test_module.py").write_text(
        "def test_value(): pass\n", encoding="utf-8"
    )
    (repository / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (repository / "governance" / "policy.yaml").write_text(
        "version: 1\n", encoding="utf-8"
    )
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
        "wfc_repo_infrastructure.txt",
        "wfc_source_and_tests.txt",
        "wfc_repository_tree.txt",
    }
    for path in output.iterdir():
        lines = path.read_text(encoding="utf-8").splitlines()
        assert all(
            line.startswith(f"{number:06d} | ") for number, line in enumerate(lines, 1)
        )
        toc_heading = next(
            number
            for number, line in enumerate(lines)
            if line.endswith("Table of Contents")
        )
        toc_entries = lines[toc_heading + 3 :]
        for line in toc_entries:
            _, entry = line.split(" | ", 1)
            if not entry:
                break
            target, label = entry.split(" | ", 1)
            target_number = int(target)
            assert lines[target_number - 1].endswith(f"==== File: {label} ====")
    assert "src/workflow_coordinator/module.py" in (
        output / "wfc_source_and_tests.txt"
    ).read_text(encoding="utf-8")
    assert "docs/guide.md" in (output / "wfc_docs_and_governance.txt").read_text(
        encoding="utf-8"
    )
    tree = (output / "wfc_repository_tree.txt").read_text(encoding="utf-8")
    assert "repository/" in tree
    assert ".git" not in tree

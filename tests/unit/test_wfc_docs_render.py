"""Guard tests for the `docs-render` Make target (BUG-WFC-DOCS-RENDER-GATE-COVERAGE).

Each test builds a throwaway checkout with only `Makefile` and
`scripts/wfc_docs_render.py`, then runs `make docs-render DOCS_ROOT=<tmp tree>`
against a documentation tree built for the test. A malformed table row is the
friction-entry-9 defect: an incomplete row dropped at the end of a table.
Simulating a missing `asciidoctor` through an empty `PATH` (rather than a
mock) exercises the gate's own real check, not a stand-in for it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPOSITORY_ROOT / "Makefile"
SCRIPT = REPOSITORY_ROOT / "scripts" / "wfc_docs_render.py"
MAKE = shutil.which("make")

needs_make = pytest.mark.skipif(MAKE is None, reason="GNU Make is not installed")
needs_asciidoctor = pytest.mark.skipif(
    shutil.which("asciidoctor") is None, reason="asciidoctor is not installed"
)

GOOD = "= Title\n\nordinary content.\n"
MALFORMED_TABLE = '= Title\n\n[cols="2,1"]\n|===\n|a |b\n|c\n|===\n'


def _checkout(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(MAKEFILE, root / "Makefile")
    shutil.copy(SCRIPT, root / "scripts" / SCRIPT.name)
    return root


def _make_docs_render(root: Path, docs_root: Path, env: dict[str, str] | None = None):
    return subprocess.run(
        [MAKE, "docs-render", f"PYTHON={sys.executable}", f"DOCS_ROOT={docs_root}"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


@needs_make
@needs_asciidoctor
def test_passing_tree_renders_clean(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "good.adoc").write_text(GOOD, encoding="utf-8")

    completed = _make_docs_render(root, docs)

    assert completed.returncode == 0, completed.stderr


@needs_make
@needs_asciidoctor
@pytest.mark.parametrize("subdirectory", ["backlog", "warm_storage"])
def test_malformed_table_fails_and_names_the_file(tmp_path: Path, subdirectory: str) -> None:
    root = _checkout(tmp_path)
    docs = tmp_path / "docs"
    (docs / subdirectory).mkdir(parents=True)
    (docs / "good.adoc").write_text(GOOD, encoding="utf-8")
    malformed = docs / subdirectory / "bad.adoc"
    malformed.write_text(MALFORMED_TABLE, encoding="utf-8")

    completed = _make_docs_render(root, docs)

    assert completed.returncode != 0
    assert str(malformed) in completed.stdout + completed.stderr


@needs_make
def test_missing_asciidoctor_fails_rather_than_skips(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "good.adoc").write_text(GOOD, encoding="utf-8")
    empty_path_dir = tmp_path / "empty-path"
    empty_path_dir.mkdir()
    environment = {**os.environ, "PATH": str(empty_path_dir)}

    completed = _make_docs_render(root, docs, env=environment)

    assert completed.returncode != 0
    assert "asciidoctor" in (completed.stdout + completed.stderr).lower()

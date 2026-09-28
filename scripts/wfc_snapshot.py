#!/usr/bin/env python3
"""Create compact, line-numbered repository snapshots for WFC development."""

from __future__ import annotations

import argparse
from pathlib import Path


EXCLUDED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
}
TEXT_SUFFIXES = {".adoc", ".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"}
ROOT_TEXT_FILES = {"LICENSE", "Makefile", "README.md"}
# The generated Project Memory workbook is transient, so it stays out of
# snapshots.  The rule is repository-relative: a bare-filename rule would
# silently hide an unrelated file elsewhere in the repository from snapshot
# contents and from the tree.  The workbook is the only generated round-trip
# artifact inside the repository -- import converts into a temporary directory
# outside it -- so no other exclusion is needed.
EXCLUDED_RELATIVE_FILES = {("project_memory.ods",)}
GROUPS = {
    "wfc_docs_and_governance.txt": ("docs", "governance"),
    "wfc_project_memory.txt": ("project_memory",),
    "wfc_repo_infrastructure.txt": (),
    "wfc_source_and_tests.txt": ("src", "tests"),
}


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository to snapshot (default: this repository)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="directory for WFC snapshot files (default: <repository>/wfc_snapshots)",
    )
    return parser.parse_args()


def _is_snapshot_file(path: Path) -> bool:
    return path.suffix in TEXT_SUFFIXES or path.name in ROOT_TEXT_FILES


def _is_excluded_path(relative: Path) -> bool:
    return relative.parts in EXCLUDED_RELATIVE_FILES


def _files(repository: Path, output: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(repository.rglob("*")):
        if not path.is_file() or output in path.parents or not _is_snapshot_file(path):
            continue
        relative = path.relative_to(repository)
        if any(
            part in EXCLUDED_DIRECTORIES or part.endswith(".egg-info") for part in relative.parts
        ):
            continue
        if _is_excluded_path(relative):
            continue
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        files.append(path)
    return files


def _group_name(relative: Path) -> str:
    for name, prefixes in GROUPS.items():
        if prefixes and relative.parts[0] in prefixes:
            return name
    return "wfc_repo_infrastructure.txt"


def _tree(repository: Path, output: Path) -> str:
    lines = [f"{repository.name}/"]

    def visit(directory: Path, prefix: str, depth: int) -> None:
        entries = []
        for path in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name)):
            if (
                path == output
                or path.name in EXCLUDED_DIRECTORIES
                or path.name.endswith(".egg-info")
                or _is_excluded_path(path.relative_to(repository))
            ):
                continue
            entries.append(path)
        for index, path in enumerate(entries):
            last = index == len(entries) - 1
            marker = "└── " if last else "├── "
            lines.append(f"{prefix}{marker}{path.name}{'/' if path.is_dir() else ''}")
            if path.is_dir() and depth < 3:
                visit(path, prefix + ("    " if last else "│   "), depth + 1)

    visit(repository, "", 0)
    return "\n".join(lines)


def _render_snapshot(title: str, sections: list[tuple[str, str]]) -> str:
    toc_placeholders = [f"TOC-{index:04d}" for index in range(len(sections))]
    lines = [title, "=" * len(title), "", "Table of Contents", "-" * 17, ""]
    lines.extend(toc_placeholders)
    lines.append("")
    for label, content in sections:
        lines.extend((f"==== File: {label} ====", ""))
        lines.extend(content.rstrip().splitlines())
        lines.append("")

    for index, (label, _) in enumerate(sections):
        heading = f"==== File: {label} ===="
        target = next(number for number, line in enumerate(lines, start=1) if line == heading)
        lines[6 + index] = f"{target:06d} | {label}"
    numbered = (f"{number:06d} | {line}" for number, line in enumerate(lines, start=1))
    return "\n".join(numbered) + "\n"


def create_snapshots(repository: Path, output: Path) -> list[Path]:
    repository = repository.resolve()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[tuple[str, str]]] = {name: [] for name in GROUPS}
    for path in _files(repository, output):
        relative = path.relative_to(repository).as_posix()
        grouped[_group_name(path.relative_to(repository))].append(
            (relative, path.read_text(encoding="utf-8"))
        )

    written = []
    for name, sections in grouped.items():
        if not sections:
            continue
        destination = output / name
        destination.write_text(
            _render_snapshot(name.removesuffix(".txt"), sections), encoding="utf-8"
        )
        written.append(destination)
    tree_destination = output / "wfc_repository_tree.txt"
    tree_destination.write_text(
        _render_snapshot("wfc_repository_tree", [("<repository tree>", _tree(repository, output))]),
        encoding="utf-8",
    )
    written.append(tree_destination)
    return written


def main() -> int:
    arguments = _parse_arguments()
    repository = arguments.repository.resolve()
    if not repository.is_dir():
        raise SystemExit(f"repository does not exist: {repository}")
    output = arguments.output or repository / "wfc_snapshots"
    for path in create_snapshots(repository, output):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

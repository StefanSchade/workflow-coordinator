#!/usr/bin/env python3
"""Project Memory spreadsheet round trip with Git as the review boundary.

Exactly two persistent representations exist:

``project_memory/canonical/*.json``
    authoritative Project Memory.
``project_memory.ods``
    an alternate, editable representation of exactly those tables.

``export``
    renders canonical JSON as the workbook.
``import``
    converts an edited workbook into a throwaway temporary directory,
    validates the complete result, and only then replaces the canonical JSON
    files as ordinary working-tree changes.
``roundtrip-check``
    verifies the transformation invariant -- canonical JSON -> ODS -> JSON must
    reproduce canonical exactly -- without writing canonical or the
    maintainer's workbook.

There is no proposal, staging area, approval marker, or promotion step.  The
imported change is reviewed with ``git diff`` and accepted with an ordinary
commit or discarded with ``git restore``.  The temporary import directory is
defensive implementation machinery only: it carries no identity, cannot be
previewed now and applied later, and never outlives the process.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


CANONICAL_TABLES = (
    "concern_event_xrefs.json",
    "concern_events.json",
    "concerns.json",
    "decisions.json",
)
MEMORY_DIRECTORY = "project_memory"
CANONICAL_DIRECTORY = "canonical"
WORKBOOK = "project_memory.ods"
EXPORT_PIPELINE = ("pipelines", "memory", "json_to_ods.yaml")
IMPORT_PIPELINE = ("pipelines", "memory", "ods_to_json.yaml")
EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_RUNNER_MISSING = 127


class Refusal(Exception):
    """A deterministic refusal carrying an operator-facing reason and exit code."""

    def __init__(self, reason: str, code: int = EXIT_REFUSED) -> None:
        super().__init__(reason)
        self.code = code


@dataclass(frozen=True)
class Layout:
    """Repository-relative locations the round trip operates on."""

    repository: Path

    @property
    def memory(self) -> Path:
        return self.repository / MEMORY_DIRECTORY

    @property
    def canonical(self) -> Path:
        return self.memory / CANONICAL_DIRECTORY

    @property
    def workbook(self) -> Path:
        return self.repository / WORKBOOK

    def pipeline(self, parts: tuple[str, ...]) -> Path:
        return self.memory.joinpath(*parts)


def _canonical_directory(layout: Layout) -> Path:
    """Return the canonical directory, refusing one that redirects out of the repository.

    Individual table files need no separate rule: replacement renames onto the
    canonical name itself, so it can never write through a symbolic link.
    """
    canonical = layout.canonical
    if not canonical.is_dir():
        raise Refusal(
            f"Missing {MEMORY_DIRECTORY}/{CANONICAL_DIRECTORY}/ under {layout.repository}."
        )
    # ``layout.repository`` is already resolved, so a differing resolution means one of the
    # two remaining components is a symbolic link pointing somewhere else.
    if canonical.resolve() != canonical:
        raise Refusal(
            f"{MEMORY_DIRECTORY}/{CANONICAL_DIRECTORY} resolves to {canonical.resolve()}, "
            f"outside the repository-owned location {canonical}."
        )
    return canonical


def _read_table_set(directory: Path, label: str) -> dict[str, bytes]:
    """Return exactly the expected canonical table set, refusing anything else."""
    expected = sorted(CANONICAL_TABLES)
    present = sorted(path.name for path in directory.glob("*.json")) if directory.is_dir() else []
    if present != expected:
        missing = [name for name in expected if name not in present]
        unexpected = [name for name in present if name not in expected]
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected {', '.join(unexpected)}")
        raise Refusal(
            f"{label} is not the complete Project Memory table set "
            f"({'; '.join(details) or 'no tables found'}) at {directory}."
        )
    tables: dict[str, bytes] = {}
    for name in expected:
        content = (directory / name).read_bytes()
        try:
            json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise Refusal(f"{label} table {name} does not parse as JSON: {error}") from error
        tables[name] = content
    return tables


def _render_delta(canonical: dict[str, bytes], reimported: dict[str, bytes]) -> list[str]:
    lines: list[str] = []
    for name in sorted(CANONICAL_TABLES):
        lines.extend(
            difflib.unified_diff(
                canonical[name].decode("utf-8").splitlines(),
                reimported[name].decode("utf-8").splitlines(),
                fromfile=f"{MEMORY_DIRECTORY}/{CANONICAL_DIRECTORY}/{name}",
                tofile=f"round-trip/{name}",
                lineterm="",
            )
        )
    return lines


def _run_pipeline(
    layout: Layout,
    sheets_run: str,
    config: Path,
    destination: Path,
    source: Path | None = None,
) -> None:
    """Run one spreadsheet-handling conversion; the destination is always explicit."""
    command = [sheets_run, "--config", str(config.relative_to(layout.repository))]
    if source is not None:
        command += ["--in-path", str(source)]
    command += ["--out-path", str(destination)]
    if shutil.which(command[0]) is None and not Path(command[0]).is_file():
        raise Refusal(
            f"{command[0]} not found. Run 'make memory-setup', or pass SHEETS_RUN=<path>.",
            EXIT_RUNNER_MISSING,
        )
    completed = subprocess.run(command, cwd=layout.repository, check=False)
    if completed.returncode != 0:
        raise Refusal(f"Workbook conversion failed (exit {completed.returncode}) in {config.name}.")


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    """Replace ``path`` by rename, keeping the permissions it already has."""
    mode = path.stat().st_mode & 0o777 if path.is_file() else 0o644
    handle = tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tmp-", delete=False)
    try:
        with handle:
            handle.write(content)
        os.chmod(handle.name, mode)
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def _require_unmodified_canonical(layout: Layout) -> None:
    """Refuse to overwrite canonical JSON that already carries working-tree edits."""
    relative = f"{MEMORY_DIRECTORY}/{CANONICAL_DIRECTORY}"
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--", relative],
            cwd=layout.repository,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise Refusal(
            f"Git is required to import the workbook, but could not run: {error}"
        ) from error
    if completed.returncode != 0:
        raise Refusal(
            f"Cannot read Git status for {relative} "
            f"({completed.stderr.strip() or f'git exited {completed.returncode}'}). "
            "Import writes canonical Project Memory as reviewable working-tree changes, "
            "so it refuses without Git."
        )
    modified = completed.stdout.strip()
    if modified:
        raise Refusal(
            f"{relative} already has uncommitted changes:\n{modified}\n"
            "Commit or discard them first; import must not overwrite an existing canonical edit."
        )


def _export(layout: Layout, arguments: argparse.Namespace) -> int:
    _read_table_set(_canonical_directory(layout), "canonical Project Memory")
    _run_pipeline(layout, arguments.sheets_run, layout.pipeline(EXPORT_PIPELINE), layout.workbook)
    print(f"Exported canonical Project Memory to {WORKBOOK}.")
    print("Edit the workbook, then run 'make memory-import'.")
    return EXIT_OK


def _import(layout: Layout, arguments: argparse.Namespace) -> int:
    canonical = _canonical_directory(layout)
    baseline = _read_table_set(canonical, "canonical Project Memory")
    if not layout.workbook.is_file():
        raise Refusal(f"Missing {WORKBOOK}. Run 'make memory-export' first.")
    _require_unmodified_canonical(layout)
    scratch = Path(tempfile.mkdtemp(prefix="wfc-memory-import-"))
    try:
        # Convert into a throwaway directory and validate the complete result before
        # any canonical file is touched: a failed, partial, or malformed conversion
        # therefore leaves canonical Project Memory exactly as Git recorded it.
        _run_pipeline(
            layout, arguments.sheets_run, layout.pipeline(IMPORT_PIPELINE), scratch / "tables"
        )
        imported = _read_table_set(scratch / "tables", "imported workbook")
        changed = [name for name in sorted(imported) if imported[name] != baseline[name]]
        for name in sorted(imported):
            _atomic_write_bytes(canonical / name, imported[name])
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if changed:
        print(f"Imported {WORKBOOK} into {MEMORY_DIRECTORY}/{CANONICAL_DIRECTORY}: ", end="")
        print(", ".join(changed) + ".")
    else:
        print(f"Imported {WORKBOOK}; canonical Project Memory is byte-identical.")
    print("Review it with 'git diff', then commit it or discard it with 'git restore'.")
    return EXIT_OK


def _roundtrip_check(layout: Layout, arguments: argparse.Namespace) -> int:
    baseline = _read_table_set(_canonical_directory(layout), "canonical Project Memory")
    scratch = Path(tempfile.mkdtemp(prefix="wfc-memory-roundtrip-"))
    try:
        workbook = scratch / WORKBOOK
        _run_pipeline(layout, arguments.sheets_run, layout.pipeline(EXPORT_PIPELINE), workbook)
        _run_pipeline(
            layout,
            arguments.sheets_run,
            layout.pipeline(IMPORT_PIPELINE),
            scratch / "tables",
            source=workbook,
        )
        reimported = _read_table_set(scratch / "tables", "reimported Project Memory")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    delta = _render_delta(baseline, reimported)
    if delta:
        print("\n".join(delta))
        raise Refusal(
            "Round-trip invariant violated: canonical JSON -> ODS -> JSON did not reproduce "
            "canonical Project Memory. That is a transformation defect, not an edit."
        )
    print(
        "Round trip lossless: "
        f"{len(CANONICAL_TABLES)} tables reimported identical to canonical Project Memory."
    )
    return EXIT_OK


OPERATIONS = {
    "export": _export,
    "import": _import,
    "roundtrip-check": _roundtrip_check,
}


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Project Memory spreadsheet round trip.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("operation", choices=sorted(OPERATIONS))
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository to operate on (default: this repository)",
    )
    parser.add_argument(
        "--sheets-run",
        default=os.environ.get("SHEETS_RUN", "sheets-run"),
        help="spreadsheet-handling runner used to convert between JSON and ODS",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    layout = Layout(arguments.repository.resolve())
    try:
        return OPERATIONS[arguments.operation](layout, arguments)
    except Refusal as refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return refusal.code


if __name__ == "__main__":
    raise SystemExit(main())

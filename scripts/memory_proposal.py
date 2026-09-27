#!/usr/bin/env python3
"""Project Memory proposal protocol: round-trip invariant, preview, promotion.

Three deliberately distinct meanings, one per operation:

``roundtrip-check``
    *Transformation invariant.*  Imports the workbook into a throwaway
    directory and requires the result to equal canonical JSON exactly.  A
    non-empty delta is a failure.  Never reads or writes the staging proposal,
    so it is not, and cannot be used as, the approval gate for an intentional
    content edit.

``preview``
    *Proposal preview.*  Imports the workbook into a throwaway directory,
    asserts the expected canonical table set and that every imported table
    parses, and only then replaces the active staging proposal.  Renders the
    canonical-vs-staging delta as review information and exits 0 even when the
    delta is non-empty.  Records a proposal marker binding the canonical
    baseline that was compared to the staged proposal that was displayed.

``promote``
    *Operator approval.*  The distinct action meaning "I approve exactly the
    proposal I just reviewed".  Promotes the previewed staging snapshot into
    canonical only while both recorded fingerprints still hold, then removes
    the marker so the same approval cannot be replayed.

The marker is not authority and is not approval: it only identifies which
proposal was previewed.  Authority stays with the maintainer running
``promote``, and canonical JSON stays the single source of truth.

Each operation is one process that sequences import, comparison, and mutation
internally, so no GNU Make invocation mode -- ``-j`` included -- can reorder
them, diff a state that is then replaced, or promote a state that was never
displayed.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


CANONICAL_TABLES = (
    "concern_event_xrefs.json",
    "concern_events.json",
    "concerns.json",
    "decisions.json",
)
MEMORY_DIRECTORY = "project_memory"
CANONICAL_DIRECTORY = "canonical"
STAGING_DIRECTORY = "staging"
PROPOSAL_DIRECTORY = ".proposal"
PROPOSAL_MARKER = "proposal.json"
WORKBOOK = "project_memory.ods"
IMPORT_PIPELINE = ("pipelines", "memory", "ods_to_json.yaml")
STAGING_GITIGNORE = "*\n!.gitignore\n"
FINGERPRINT_ALGORITHM = "sha256-table-set-v1"
MARKER_VERSION = 1
IMPORT_COMMAND_ENVIRONMENT = "WFC_MEMORY_IMPORT_COMMAND"
EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_IMPORTER_MISSING = 127


class Refusal(Exception):
    """A deterministic refusal carrying an operator-facing reason and exit code."""

    def __init__(self, reason: str, code: int = EXIT_REFUSED) -> None:
        super().__init__(reason)
        self.code = code


@dataclass(frozen=True)
class Layout:
    """Repository-relative locations the protocol operates on."""

    repository: Path

    @property
    def memory(self) -> Path:
        return self.repository / MEMORY_DIRECTORY

    @property
    def canonical(self) -> Path:
        return self.memory / CANONICAL_DIRECTORY

    @property
    def staging(self) -> Path:
        return self.memory / STAGING_DIRECTORY

    @property
    def proposal(self) -> Path:
        return self.memory / PROPOSAL_DIRECTORY

    @property
    def marker(self) -> Path:
        return self.proposal / PROPOSAL_MARKER

    @property
    def workbook(self) -> Path:
        return self.repository / WORKBOOK

    @property
    def import_pipeline(self) -> Path:
        return self.memory.joinpath(*IMPORT_PIPELINE)


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


def _digests(tables: dict[str, bytes]) -> dict[str, str]:
    return {name: hashlib.sha256(content).hexdigest() for name, content in tables.items()}


def _fingerprint(digests: dict[str, str]) -> str:
    """Fold per-table digests into one order-independent table-set fingerprint."""
    accumulator = hashlib.sha256(f"{FINGERPRINT_ALGORITHM}\n".encode("utf-8"))
    for name in sorted(digests):
        accumulator.update(f"{name}\0{digests[name]}\n".encode("utf-8"))
    return accumulator.hexdigest()


def _render_delta(baseline: dict[str, bytes], proposal: dict[str, bytes]) -> list[str]:
    lines: list[str] = []
    for name in sorted(CANONICAL_TABLES):
        lines.extend(
            difflib.unified_diff(
                baseline[name].decode("utf-8").splitlines(),
                proposal[name].decode("utf-8").splitlines(),
                fromfile=f"{MEMORY_DIRECTORY}/{CANONICAL_DIRECTORY}/{name}",
                tofile=f"{MEMORY_DIRECTORY}/{STAGING_DIRECTORY}/{name}",
                lineterm="",
            )
        )
    return lines


def _import_command(layout: Layout, sheets_run: str, override: str, destination: Path) -> list[str]:
    """Build the importer invocation; the destination is always its final argument."""
    if override:
        return [*shlex.split(override), str(destination)]
    return [
        sheets_run,
        "--config",
        str(layout.import_pipeline.relative_to(layout.repository)),
        "--out-path",
        str(destination),
    ]


def _import_workbook(
    layout: Layout, sheets_run: str, override: str, destination: Path
) -> dict[str, bytes]:
    """Import the workbook into ``destination`` and return its validated table set."""
    if not layout.workbook.is_file():
        raise Refusal(f"Missing {WORKBOOK}. Run 'make memory-export' first.")
    command = _import_command(layout, sheets_run, override, destination)
    if shutil.which(command[0]) is None and not Path(command[0]).is_file():
        raise Refusal(
            f"{command[0]} not found. Run 'make memory-setup', or pass SHEETS_RUN=<path>.",
            EXIT_IMPORTER_MISSING,
        )
    destination.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(command, cwd=layout.repository, check=False)
    if completed.returncode != 0:
        raise Refusal(
            f"Workbook import failed (exit {completed.returncode}); "
            "the previous proposal is untouched."
        )
    return _read_table_set(destination, "imported workbook")


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tmp-", delete=False)
    try:
        with handle:
            handle.write(content)
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def _replace_staging(layout: Layout, tables: dict[str, bytes]) -> None:
    """Swap the active staging proposal for ``tables`` by directory rename."""
    layout.proposal.mkdir(parents=True, exist_ok=True)
    incoming = layout.proposal / "staging.incoming"
    retired = layout.proposal / "staging.retired"
    for path in (incoming, retired):
        shutil.rmtree(path, ignore_errors=True)
    incoming.mkdir()
    for name, content in tables.items():
        (incoming / name).write_bytes(content)
    tracked_marker = layout.staging / ".gitignore"
    (incoming / ".gitignore").write_text(
        tracked_marker.read_text(encoding="utf-8")
        if tracked_marker.is_file()
        else STAGING_GITIGNORE,
        encoding="utf-8",
    )
    if layout.staging.exists():
        os.replace(layout.staging, retired)
    os.replace(incoming, layout.staging)
    shutil.rmtree(retired, ignore_errors=True)


def _write_marker(
    layout: Layout,
    baseline: dict[str, str],
    proposal: dict[str, str],
    delta_empty: bool,
) -> tuple[str, str]:
    baseline_fingerprint = _fingerprint(baseline)
    proposal_fingerprint = _fingerprint(proposal)
    workbook = layout.workbook.read_bytes()
    marker = {
        "version": MARKER_VERSION,
        "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
        "expected_tables": sorted(CANONICAL_TABLES),
        "previewed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "canonical_baseline": {"fingerprint": baseline_fingerprint, "tables": baseline},
        "staged_proposal": {"fingerprint": proposal_fingerprint, "tables": proposal},
        "delta_empty": delta_empty,
        # Informational only: the workbook stays an editing source and may change
        # after the preview without invalidating the reviewed proposal.
        "workbook": {"sha256": hashlib.sha256(workbook).hexdigest(), "bytes": len(workbook)},
    }
    _atomic_write_bytes(
        layout.marker, (json.dumps(marker, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )
    return baseline_fingerprint, proposal_fingerprint


def _load_marker(layout: Layout) -> dict:
    if not layout.marker.is_file():
        raise Refusal(
            "No previewed proposal to approve. Run 'make memory-preview', review the "
            "rendered delta, and then promote it."
        )
    try:
        marker = json.loads(layout.marker.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Refusal(
            f"Proposal marker {layout.marker} is unreadable ({error}); "
            "run 'make memory-preview' again."
        ) from error
    if not isinstance(marker, dict) or marker.get("version") != MARKER_VERSION:
        raise Refusal(
            f"Proposal marker {layout.marker} is not protocol version {MARKER_VERSION}; "
            "run 'make memory-preview' again."
        )
    if marker.get("fingerprint_algorithm") != FINGERPRINT_ALGORITHM:
        raise Refusal(
            f"Proposal marker {layout.marker} was fingerprinted by a different algorithm; "
            "run 'make memory-preview' again."
        )
    return marker


def _roundtrip_check(layout: Layout, arguments: argparse.Namespace) -> int:
    baseline = _read_table_set(layout.canonical, "canonical Project Memory")
    scratch = Path(tempfile.mkdtemp(dir=_scratch_parent(layout), prefix="roundtrip-"))
    try:
        imported = _import_workbook(
            layout, arguments.sheets_run, arguments.import_command, scratch / "tables"
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    delta = _render_delta(baseline, imported)
    if delta:
        print("\n".join(delta))
        raise Refusal(
            "Round-trip invariant violated: an unedited workbook must reimport to canonical "
            "JSON unchanged. If this delta is an intentional edit, review it with "
            "'make memory-preview' instead -- this target is not the approval gate."
        )
    print(
        "Round trip lossless: "
        f"{len(CANONICAL_TABLES)} tables reimported identical to canonical Project Memory."
    )
    return EXIT_OK


def _preview(layout: Layout, arguments: argparse.Namespace) -> int:
    # The baseline is read before the import and is the exact byte content both
    # rendered in the delta below and recorded in the marker.
    baseline = _read_table_set(layout.canonical, "canonical Project Memory")
    scratch = Path(tempfile.mkdtemp(dir=_scratch_parent(layout), prefix="import-"))
    try:
        imported = _import_workbook(
            layout, arguments.sheets_run, arguments.import_command, scratch / "tables"
        )
        # Reached only after a complete, parseable import: a failed or partial
        # import leaves the previous staging proposal and its marker intact.
        _replace_staging(layout, imported)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    staged = _read_table_set(layout.staging, "staging proposal")
    if staged != imported:
        raise Refusal(f"Staging at {layout.staging} does not hold the imported proposal.")
    delta = _render_delta(baseline, staged)
    print(f"Proposed canonical change ({MEMORY_DIRECTORY}/{CANONICAL_DIRECTORY} -> workbook edit):")
    print("\n".join(delta) if delta else "    (none -- the workbook proposes no canonical change)")
    baseline_fingerprint, proposal_fingerprint = _write_marker(
        layout, _digests(baseline), _digests(staged), not delta
    )
    print("")
    print(f"reviewed canonical baseline : {baseline_fingerprint}")
    print(f"reviewed staged proposal    : {proposal_fingerprint}")
    print(
        "Approve exactly this proposal with 'make memory-promote'. "
        "Any change to canonical or to staging requires a fresh preview."
    )
    return EXIT_OK


def _promote(layout: Layout, arguments: argparse.Namespace) -> int:
    marker = _load_marker(layout)
    staged = _read_table_set(layout.staging, "staging proposal")
    canonical = _read_table_set(layout.canonical, "canonical Project Memory")
    reviewed_proposal = marker["staged_proposal"]["fingerprint"]
    reviewed_baseline = marker["canonical_baseline"]["fingerprint"]
    current_proposal = _fingerprint(_digests(staged))
    current_baseline = _fingerprint(_digests(canonical))
    if current_proposal != reviewed_proposal:
        raise Refusal(
            "Staging no longer holds the proposal that was previewed "
            f"(reviewed {reviewed_proposal}, current {current_proposal}). "
            "Run 'make memory-preview' again and review the delta before promoting."
        )
    if current_baseline != reviewed_baseline:
        raise Refusal(
            "Canonical Project Memory changed after the proposal was previewed "
            f"(reviewed {reviewed_baseline}, current {current_baseline}). "
            "Run 'make memory-preview' again and review the delta before promoting."
        )
    changed = [name for name in sorted(staged) if staged[name] != canonical[name]]
    for name in sorted(staged):
        _atomic_write_bytes(layout.canonical / name, staged[name])
    # Invalidate the approval so a stale promotion cannot be repeated.
    layout.marker.unlink(missing_ok=True)
    print(
        f"Promoted reviewed proposal {reviewed_proposal} onto baseline {reviewed_baseline}: "
        f"{', '.join(changed) if changed else 'no table content changed'}."
    )
    print("Proposal consumed. Promoting again requires a fresh 'make memory-preview'.")
    return EXIT_OK


def _scratch_parent(layout: Layout) -> Path:
    """Keep scratch imports beside staging so directory renames stay atomic."""
    layout.proposal.mkdir(parents=True, exist_ok=True)
    return layout.proposal


OPERATIONS = {
    "roundtrip-check": _roundtrip_check,
    "preview": _preview,
    "promote": _promote,
}


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Project Memory proposal protocol.",
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
        help="spreadsheet-handling runner used to import the workbook",
    )
    parser.add_argument(
        "--import-command",
        default=os.environ.get(IMPORT_COMMAND_ENVIRONMENT, ""),
        help=(
            "override the workbook importer; the destination directory is appended as its "
            f"final argument (also read from ${IMPORT_COMMAND_ENVIRONMENT})"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    layout = Layout(arguments.repository.resolve())
    try:
        if not layout.memory.is_dir():
            raise Refusal(f"Missing {MEMORY_DIRECTORY}/ under {layout.repository}.")
        return OPERATIONS[arguments.operation](layout, arguments)
    except Refusal as refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return refusal.code


if __name__ == "__main__":
    raise SystemExit(main())

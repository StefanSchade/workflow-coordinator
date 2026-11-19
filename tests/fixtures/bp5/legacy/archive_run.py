#!/usr/bin/env python3
"""Deterministic archiver for WFC run/ops evidence.

Copies a bounded, explicit set of small evidence files from a source Run
directory (and optionally a matching OPS directory) into a destination
directory inside this evidence repository, and writes a manifest.json next
to the copy with mechanically-extracted facts.

This script never decides semantic significance. Curated interpretation
(domain, classification, historical significance, related commits, ...) is
supplied by the caller via --semantic-json and stored verbatim under the
manifest's "semantic" key.

It never modifies, deletes, or moves anything under the source directories.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Filenames (glob patterns) copied from a Run directory's top level.
RUN_ROOT_ALLOW = [
    "operator-plan.json",
    "operator-summary.json",
    "launch-plan.json",
    "checkout-association.json",
    "candidate-artifact.adoc",
    "candidate-artifact.sha256",
    "launcher-source.py",
    "task-payload.txt",
]

# Filenames (glob patterns) copied from each checkouts/<uuid>/ directory.
CHECKOUT_ALLOW = [
    "checkpoint.json",
    "result-message.json",
    "run.json",
    "dispatch.json",
    "structured-result.schema.json",
    "execution-evidence.json",
]

# Filenames (glob patterns) copied from an OPS directory's top level.
OPS_ROOT_ALLOW = [
    "execution-bindings.yaml",
    "launch.sh",
    "launch-command.sh",
    "launch-command.txt",
    "run.log",
    "run-analysis-*.txt",
    "preparation.log",
    "preparation-continue.log",
    "pinned-revisions.txt",
    "preparation-pins.txt",
    "H*-worker-dirty.diff",
    "H*-worker-dirty.patch",
    "H*-worker-status.txt",
    "H*-check-*.log",
    "H*-check-*.exitcode",
    "baseline-*.log",
    "baseline-*.exitcode",
]

# Subdirectories copied wholesale (small, curated evidence bundles) from OPS.
OPS_SUBDIR_ALLOW = [
    "predecessor-disposition",
    "failed-h*-artifacts",
]

# Directories that are always bulk/reconstructable and must never be copied,
# even if a future evolution nests them somewhere unexpected.
BULK_DIR_DENY = {
    "runtime-env", "runtime-export",
    "development-env", "development-export",
    "core-evidence-env", "core-export",
    ".venv", "venv", "__pycache__", "node_modules", ".git",
}


REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_ROOT = REPO_ROOT / "archive"


def assert_source_isolation(dest: Path, *sources: Path | None) -> None:
    """Refuse a destination that is, or is nested under/over, any source."""
    for src in sources:
        if src is None:
            continue
        if dest == src:
            raise SystemExit(f"refusing: destination equals source: {dest}")
        if src in dest.parents:
            raise SystemExit(f"refusing: destination {dest} is nested inside source {src}")
        if dest in src.parents:
            raise SystemExit(f"refusing: source {src} is nested inside destination {dest}")


def assert_dest_under_archive_root(dest: Path) -> None:
    try:
        dest.relative_to(ARCHIVE_ROOT)
    except ValueError:
        raise SystemExit(
            f"refusing: destination {dest} is not under this repository's archive root {ARCHIVE_ROOT}"
        )


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def matches_any(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def copy_one(src: Path, dest_root: Path, rel: Path, copied: list[dict]) -> None:
    dest = dest_root / rel
    if dest.exists():
        raise SystemExit(f"refusing to overwrite existing destination file: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    before = sha256_of(src)
    shutil.copy2(src, dest)
    after = sha256_of(dest)
    if before != after:
        raise SystemExit(f"copy verification failed for {src} -> {dest}")
    copied.append({
        "path": str(rel).replace("\\", "/"),
        "source": str(src),
        "sha256": after,
        "bytes": dest.stat().st_size,
    })


def collect_run_files(run_dir: Path, dest_root: Path, copied: list[dict]) -> None:
    for pattern in RUN_ROOT_ALLOW:
        for src in sorted(run_dir.glob(pattern)):
            if src.is_file():
                copy_one(src, dest_root, Path("run") / src.name, copied)

    checkouts = run_dir / "checkouts"
    if checkouts.is_dir():
        for checkout_dir in sorted(p for p in checkouts.iterdir() if p.is_dir()):
            for pattern in CHECKOUT_ALLOW:
                for src in sorted(checkout_dir.glob(pattern)):
                    if src.is_file():
                        rel = Path("run") / "checkouts" / checkout_dir.name / src.name
                        copy_one(src, dest_root, rel, copied)


def collect_ops_files(ops_dir: Path, dest_root: Path, copied: list[dict]) -> None:
    for pattern in OPS_ROOT_ALLOW:
        for src in sorted(ops_dir.glob(pattern)):
            if src.is_file():
                copy_one(src, dest_root, Path("ops") / src.name, copied)

    for pattern in OPS_SUBDIR_ALLOW:
        for subdir in sorted(ops_dir.glob(pattern)):
            if subdir.is_dir() and subdir.name not in BULK_DIR_DENY:
                for src in sorted(p for p in subdir.rglob("*") if p.is_file()):
                    rel = Path("ops") / subdir.name / src.relative_to(subdir)
                    copy_one(src, dest_root, rel, copied)


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


# Scalar Run-identity fields considered for the conservative merge below.
# Note "status" (canonical run.json) and "run_status" (operator-summary.json)
# are kept as two distinct fields, not merged into one another, even though
# they are usually the same underlying value spelled differently by the two
# artifact types -- conflating them would blur which artifact said what.
RUN_IDENTITY_FIELDS = (
    "run_id", "work_item_id", "policy_id", "policy_revision",
    "profile_id", "profile_revision", "hop_limit", "hop_used",
    "baseline_head", "current_head", "final_head", "stop_reason",
    "status", "run_status",
)

PIN_FILES_IN_PRIORITY = ("pinned-revisions.txt", "preparation-pins.txt")


def parse_kv_file(path: Path) -> dict[str, str]:
    """Parse a flat `key=value` pin file. No inference: a line is only kept
    when it is exactly `<identifier>=<rest>`; everything else is ignored."""
    result: dict[str, str] = {}
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.isidentifier():
            result[key] = value.strip()
    return result


def _fill_from(source: dict, source_label: str, facts: dict, field_sources: dict) -> None:
    """Set facts[key] from source[key] only for keys not already established."""
    for key in RUN_IDENTITY_FIELDS:
        if facts.get(key) is not None:
            continue
        value = source.get(key)
        if value is not None:
            facts[key] = value
            field_sources[key] = source_label


def extract_facts(run_dir: Path, ops_dir: Path | None) -> dict:
    facts: dict = {key: None for key in RUN_IDENTITY_FIELDS}
    facts.update({
        "hops": [],
        "adapters": [], "models": [], "reasoning_levels": [], "workflow_roles": [],
        "evidence_commands": [],
        "checkouts": [],
    })
    field_sources: dict[str, str] = {}

    # Pass 1 (highest authority): the aggregate operator-summary.json.
    op_summary = load_json(run_dir / "operator-summary.json")
    if op_summary:
        _fill_from(op_summary, "operator-summary.json", facts, field_sources)
        for hop in op_summary.get("hops", []) or []:
            facts["hops"].append({
                "hop_id": hop.get("hop_id"),
                "role": hop.get("role"),
                "outcome": hop.get("outcome"),
                "summary": hop.get("summary"),
            })
        for ev in op_summary.get("execution_evidence", []) or []:
            for k, bucket in (("adapter", "adapters"), ("model", "models"),
                              ("reasoning", "reasoning_levels"),
                              ("workflow_role", "workflow_roles")):
                v = ev.get(k)
                if v and v not in facts[bucket]:
                    facts[bucket].append(v)

    # Pass 2 (fills gaps only): canonical per-checkout run.json, in
    # deterministic sorted-checkout-directory order. A field already
    # established in pass 1 (or by an earlier checkout in this pass) is
    # never overwritten -- first-established wins, so a later checkout's
    # run.json cannot silently override an earlier, conflicting value.
    checkouts_dir = run_dir / "checkouts"
    if checkouts_dir.is_dir():
        for checkout_dir in sorted(p for p in checkouts_dir.iterdir() if p.is_dir()):
            facts["checkouts"].append(checkout_dir.name)
            cp = load_json(checkout_dir / "checkpoint.json")
            if cp:
                for ev in cp.get("evidence", []) or []:
                    name = ev.get("command_name") or ev.get("provider")
                    if name and name not in facts["evidence_commands"]:
                        facts["evidence_commands"].append(name)
            rj = load_json(checkout_dir / "run.json")
            if rj:
                _fill_from(rj, f"checkouts/{checkout_dir.name}/run.json", facts, field_sources)
                if not facts["hops"]:
                    for hop in rj.get("hops", []) or []:
                        facts["hops"].append({
                            "hop_id": hop.get("hop_id"),
                            "role": hop.get("role"),
                            "outcome": hop.get("outcome"),
                            "summary": hop.get("summary"),
                        })

    facts["field_sources"] = field_sources
    facts["ops_source"] = str(ops_dir) if ops_dir else None

    facts["repository_identities"] = None
    if ops_dir:
        prep = ops_dir / "preparation.log"
        if prep.is_file():
            ids: dict[str, str] = {}
            for line in prep.read_text(errors="replace").splitlines()[:10]:
                for label in ("Core", "WFC", "Runtime", "Codex"):
                    prefix = f"{label}:"
                    if line.strip().startswith(prefix):
                        ids[label] = line.split(":", 1)[1].strip()
            if ids:
                facts["repository_identities"] = ids

    # Pin files (pinned-revisions.txt / preparation-pins.txt): explicit
    # key=value format, parsed verbatim -- no cherry-picking of known keys,
    # since the format itself is the "explicit known key/value format" and
    # the pin files' own key names (e.g. "*_head" vs "*_expected_head")
    # already distinguish observed-at-prep-time from expected/pinned values.
    facts["pinned_revisions"] = None
    if ops_dir:
        merged: dict[str, str] = {}
        used_files: list[str] = []
        for fname in PIN_FILES_IN_PRIORITY:
            p = ops_dir / fname
            if p.is_file():
                used_files.append(fname)
                for k, v in parse_kv_file(p).items():
                    merged.setdefault(k, v)
        if merged:
            facts["pinned_revisions"] = {"values": merged, "source_files": used_files}

    return facts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path,
                     help="Explicit source Run directory (from ~/wfc-runs or ~/wfc-state).")
    ap.add_argument("--ops-dir", type=Path, default=None,
                     help="Explicit matching OPS directory (from ~/wfc-ops), optional.")
    ap.add_argument("--dest", required=True, type=Path,
                     help="Destination attempt directory inside this evidence repo.")
    ap.add_argument("--semantic-json", type=Path, default=None,
                     help="Path to a JSON file of curated semantic annotations, merged verbatim.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    run_dir = args.run_dir.expanduser().resolve()
    ops_dir = args.ops_dir.expanduser().resolve() if args.ops_dir else None
    dest = args.dest.expanduser().resolve()

    if not run_dir.is_dir():
        raise SystemExit(f"missing --run-dir: {run_dir}")
    if ops_dir is not None and not ops_dir.is_dir():
        raise SystemExit(f"missing --ops-dir: {ops_dir}")

    assert_source_isolation(dest, run_dir, ops_dir)
    assert_dest_under_archive_root(dest)

    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"refusing to archive into existing non-empty destination: {dest}")

    if args.dry_run:
        preview: list[str] = []
        for pattern in RUN_ROOT_ALLOW:
            preview += [str(p) for p in run_dir.glob(pattern)]
        for checkout_dir in sorted((run_dir / "checkouts").glob("*")) if (run_dir / "checkouts").is_dir() else []:
            for pattern in CHECKOUT_ALLOW:
                preview += [str(p) for p in checkout_dir.glob(pattern)]
        if ops_dir:
            for pattern in OPS_ROOT_ALLOW:
                preview += [str(p) for p in ops_dir.glob(pattern)]
            for pattern in OPS_SUBDIR_ALLOW:
                for subdir in ops_dir.glob(pattern):
                    preview += [str(p) for p in subdir.rglob("*") if p.is_file()]
        print(json.dumps({"would_copy": sorted(preview)}, indent=2))
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    copied: list[dict] = []
    collect_run_files(run_dir, dest, copied)
    if ops_dir:
        collect_ops_files(ops_dir, dest, copied)

    facts = extract_facts(run_dir, ops_dir)

    semantic = None
    if args.semantic_json:
        semantic = json.loads(args.semantic_json.read_text())

    manifest = {
        "attempt_id": dest.name,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "sources": {"run_dir": str(run_dir), "ops_dir": str(ops_dir) if ops_dir else None},
        "copied_files": copied,
        "facts": facts,
        "semantic": semantic,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n")

    print(json.dumps({"dest": str(dest), "files_copied": len(copied)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

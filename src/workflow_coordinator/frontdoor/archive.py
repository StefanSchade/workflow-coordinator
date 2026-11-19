"""Installed ``wfc archive`` -- the canonical Evidence archiver, ported.

This ports the Evidence repository's own ``scripts/archive_run.py`` (the
archiver: bounded allow-list, source isolation, digest-verified copy,
mechanical fact extraction) and ``scripts/wfc-archive-run`` (the wrapper:
Run-identity derivation and cross-source agreement, OPS-directory discovery,
semantic-classification destination, dry-run preview, and the scoped
commit) into one installed, parameterized module, per
``wfc_beta1_product_boundary_review_second_pass.adoc``'s BP5 reuse-and-
sequencing steps. The allow-lists, confinement guards, fact-merge priority
and cross-source agreement checks are semantically unchanged from the
ported scripts; ``archived_at`` aside, a freshly written manifest is
byte-compatible with the one the old scripts would have produced for the
same inputs.

Two things are deliberately new, not present in the ported scripts:

* *Staging and atomic placement.* The copy lands in a staging directory
  first and is renamed into the final destination only after the manifest
  itself is verified; a partial copy is never observable at the
  destination.
* *Idempotent retry.* A destination already fully placed but not yet
  committed resumes directly at the commit step; a destination already
  committed with matching content is a no-op; only a destination that
  exists with genuinely different content is refused.

The commit itself is the one Coordinator-owned write surface
(``git_facts.bounded_path_commit``), not a raw ``git add``/``git commit``
pair -- the same primitive the Commission commit uses. Because that
primitive commits a bare subject with no body, the rich identity/file-count
narrative the old wrapper wrote into the commit *body* is reported on
stdout instead; the commit subject alone (``evidence: archive <attempt>``)
is unchanged.

Destination-boundary parameterization is limited to accepting the evidence
repository as a caller-supplied root (rather than deriving it from
``__file__`` as the original script did); this port does not implement a
repository-local destination, which remains explicitly withheld pending
DG-2 follow-up.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..git_facts import (
    GitPreflightError,
    NewPathCommitIntent,
    TrustedCommitError,
    bounded_path_commit,
    repository_head,
    repository_identity,
)

# --------------------------------------------------------------------------
# Ported allow-lists (verbatim from the Evidence repository's archive_run.py)
# --------------------------------------------------------------------------

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

CHECKOUT_ALLOW = [
    "checkpoint.json",
    "result-message.json",
    "run.json",
    "dispatch.json",
    "structured-result.schema.json",
    "execution-evidence.json",
]

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

OPS_SUBDIR_ALLOW = [
    "predecessor-disposition",
    "failed-h*-artifacts",
]

BULK_DIR_DENY = {
    "runtime-env", "runtime-export",
    "development-env", "development-export",
    "core-evidence-env", "core-export",
    ".venv", "venv", "__pycache__", "node_modules", ".git",
}

RUN_IDENTITY_FIELDS = (
    "run_id", "work_item_id", "policy_id", "policy_revision",
    "profile_id", "profile_revision", "hop_limit", "hop_used",
    "baseline_head", "current_head", "final_head", "stop_reason",
    "status", "run_status",
)

PIN_FILES_IN_PRIORITY = ("pinned-revisions.txt", "preparation-pins.txt")


class ArchiveError(RuntimeError):
    """A refusal in the ported archiver/wrapper; never a partial mutation."""


def assert_source_isolation(dest: Path, *sources: Path | None) -> None:
    """Refuse a destination that is, or is nested under/over, any source."""

    for src in sources:
        if src is None:
            continue
        if dest == src:
            raise ArchiveError(f"refusing: destination equals source: {dest}")
        if src in dest.parents:
            raise ArchiveError(f"refusing: destination {dest} is nested inside source {src}")
        if dest in src.parents:
            raise ArchiveError(f"refusing: source {src} is nested inside destination {dest}")


def assert_dest_under_archive_root(dest: Path, archive_root: Path) -> None:
    try:
        dest.relative_to(archive_root)
    except ValueError:
        raise ArchiveError(
            f"refusing: destination {dest} is not under this repository's archive root {archive_root}"
        ) from None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def matches_any(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def _copy_one(src: Path, dest_root: Path, rel: Path, copied: list[dict]) -> None:
    dest = dest_root / rel
    if dest.exists():
        raise ArchiveError(f"refusing to overwrite existing destination file: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    before = sha256_of(src)
    shutil.copy2(src, dest)
    after = sha256_of(dest)
    if before != after:
        raise ArchiveError(f"copy verification failed for {src} -> {dest}")
    copied.append({
        "path": str(rel).replace("\\", "/"),
        "source": str(src),
        "sha256": after,
        "bytes": dest.stat().st_size,
    })


def _plan_run_files(run_dir: Path) -> list[Path]:
    planned: list[Path] = []
    for pattern in RUN_ROOT_ALLOW:
        planned += [p for p in sorted(run_dir.glob(pattern)) if p.is_file()]
    checkouts = run_dir / "checkouts"
    if checkouts.is_dir():
        for checkout_dir in sorted(p for p in checkouts.iterdir() if p.is_dir()):
            for pattern in CHECKOUT_ALLOW:
                planned += [p for p in sorted(checkout_dir.glob(pattern)) if p.is_file()]
    return planned


def _plan_ops_files(ops_dir: Path) -> list[Path]:
    planned: list[Path] = []
    for pattern in OPS_ROOT_ALLOW:
        planned += [p for p in sorted(ops_dir.glob(pattern)) if p.is_file()]
    for pattern in OPS_SUBDIR_ALLOW:
        for subdir in sorted(ops_dir.glob(pattern)):
            if subdir.is_dir() and subdir.name not in BULK_DIR_DENY:
                planned += [p for p in sorted(subdir.rglob("*")) if p.is_file()]
    return planned


def collect_run_files(run_dir: Path, dest_root: Path, copied: list[dict]) -> None:
    for pattern in RUN_ROOT_ALLOW:
        for src in sorted(run_dir.glob(pattern)):
            if src.is_file():
                _copy_one(src, dest_root, Path("run") / src.name, copied)

    checkouts = run_dir / "checkouts"
    if checkouts.is_dir():
        for checkout_dir in sorted(p for p in checkouts.iterdir() if p.is_dir()):
            for pattern in CHECKOUT_ALLOW:
                for src in sorted(checkout_dir.glob(pattern)):
                    if src.is_file():
                        rel = Path("run") / "checkouts" / checkout_dir.name / src.name
                        _copy_one(src, dest_root, rel, copied)


def collect_ops_files(ops_dir: Path, dest_root: Path, copied: list[dict]) -> None:
    for pattern in OPS_ROOT_ALLOW:
        for src in sorted(ops_dir.glob(pattern)):
            if src.is_file():
                _copy_one(src, dest_root, Path("ops") / src.name, copied)

    for pattern in OPS_SUBDIR_ALLOW:
        for subdir in sorted(ops_dir.glob(pattern)):
            if subdir.is_dir() and subdir.name not in BULK_DIR_DENY:
                for src in sorted(p for p in subdir.rglob("*") if p.is_file()):
                    rel = Path("ops") / subdir.name / src.relative_to(subdir)
                    _copy_one(src, dest_root, rel, copied)


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def parse_kv_file(path: Path) -> dict[str, str]:
    """Parse a flat ``key=value`` pin file; no inference, as the original."""

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


# --------------------------------------------------------------------------
# Ported orchestration (from the Evidence repository's wfc-archive-run)
# --------------------------------------------------------------------------

def _reject_control_characters(value: str, *, label: str) -> None:
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ArchiveError(f"refusing {label}: contains a control character")


def derive_attempt_id(state_root: Path) -> str:
    attempt_id = state_root.name
    if not attempt_id:
        raise ArchiveError(f"cannot derive an attempt id from state root: {state_root}")
    if attempt_id in (".", "..") or attempt_id.startswith("-"):
        raise ArchiveError(f"refusing implausible attempt id derived from state root: {attempt_id}")
    _reject_control_characters(attempt_id, label="attempt id derived from state root")
    return attempt_id


def _pick(key: str, *sources: dict) -> str | None:
    for src in sources:
        value = src.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _agree(key: str, *labeled_sources: tuple[str, dict]) -> str | None:
    """First non-empty value for ``key`` across ``labeled_sources``, in order.

    Refuses when two sources that each carry their own non-empty value for
    ``key`` disagree, instead of silently preferring whichever is checked
    first.
    """

    found = [
        (label, source[key].strip())
        for label, source in labeled_sources
        if isinstance(source.get(key), str) and source.get(key).strip()
    ]
    distinct = sorted({value for _, value in found})
    if len(distinct) > 1:
        detail = ", ".join(f"{label}={value!r}" for label, value in found)
        raise ArchiveError(
            f"{key} disagrees between this Run's own authoritative state ({detail}); "
            "refusing to archive a Run whose own state disagrees with itself"
        )
    return found[0][1] if found else None


@dataclass(frozen=True)
class RunIdentity:
    run_id: str
    work_item_id: str | None
    run_status: str | None
    checkout_id: str | None
    repository: str | None


def run_identity(state_root: Path) -> RunIdentity:
    summary = load_json(state_root / "operator-summary.json") or {}
    plan = load_json(state_root / "operator-plan.json") or {}
    assoc = load_json(state_root / "checkout-association.json") or {}
    run: dict = {}
    checkouts = state_root / "checkouts"
    if checkouts.is_dir():
        for directory in sorted(p for p in checkouts.iterdir() if p.is_dir()):
            candidate = load_json(directory / "run.json")
            if candidate:
                run = candidate
                break

    plan_state_root = _pick("state_root", plan)
    if plan_state_root and Path(plan_state_root).resolve() != state_root.resolve():
        raise ArchiveError(
            f"state root {state_root} disagrees with the Run's own operator-plan.json "
            f"state_root ({plan_state_root}); refusing to archive a mismatched state root"
        )

    run_id = _agree("run_id", ("operator-summary.json", summary), ("operator-plan.json", plan), ("checkouts/*/run.json", run))
    work_item_id = _agree("work_item_id", ("operator-summary.json", summary), ("operator-plan.json", plan), ("checkouts/*/run.json", run))
    run_status = _pick("run_status", summary) or _pick("status", run)
    checkout_id = _agree("checkout_id", ("operator-summary.json", summary), ("checkout-association.json", assoc))

    repository = _pick("repository", plan)
    canonical_path = _pick("canonical_path", assoc)
    if repository and canonical_path and repository != canonical_path:
        raise ArchiveError(
            f"repository disagrees between operator-plan.json ({repository!r}) and "
            f"checkout-association.json canonical_path ({canonical_path!r}); refusing "
            "to archive a Run whose own state disagrees with itself"
        )
    repository = repository or canonical_path

    if not run_id:
        raise ArchiveError(
            f"no Run id found under {state_root}: not a WFC Run state root (expected "
            "operator-summary.json, operator-plan.json or checkouts/*/run.json)"
        )
    return RunIdentity(run_id=run_id, work_item_id=work_item_id, run_status=run_status,
                        checkout_id=checkout_id, repository=repository)


def resolve_ops_dir(state_root: Path, ops_candidate: Path, attempt_id: str) -> Path | None:
    plan = load_json(state_root / "operator-plan.json") or {}
    bindings = _pick("execution_bindings_source", plan)
    recorded = Path(bindings).parent if bindings else None

    if not ops_candidate.is_dir():
        if recorded is not None and recorded.is_dir():
            raise ArchiveError(
                f"default OPS directory {ops_candidate} is absent, but this Run's preparation "
                f"records OPS evidence at {recorded}; refusing to archive without it"
            )
        return None

    if recorded is not None and recorded.resolve() != ops_candidate.resolve():
        raise ArchiveError(
            f"OPS directory {ops_candidate} is not the one recorded in this Run's preparation "
            f"({recorded}); refusing to archive incoherent OPS evidence"
        )

    pins = ops_candidate / "preparation-pins.txt"
    if pins.is_file():
        values = parse_kv_file(pins)
        pinned_attempt = values.get("attempt_id")
        if pinned_attempt and pinned_attempt != attempt_id:
            raise ArchiveError(
                f"OPS directory {ops_candidate} was prepared for attempt {pinned_attempt}, "
                f"not {attempt_id}; refusing to archive incoherent OPS evidence"
            )
        pinned_state = values.get("state_root")
        if pinned_state and Path(pinned_state).resolve() != state_root.resolve():
            raise ArchiveError(
                f"OPS directory {ops_candidate} was prepared for state root {pinned_state}, "
                f"not {state_root}; refusing to archive incoherent OPS evidence"
            )
    return ops_candidate


def semantic_annotation(semantic_path: Path | None) -> tuple[dict | None, str | None, str | None]:
    """Return ``(payload, domain, workstream)``; ``payload`` is stored verbatim."""

    if semantic_path is None:
        return None, None, None
    try:
        payload = json.loads(semantic_path.read_text())
    except Exception as exc:
        raise ArchiveError(f"--semantic-json is not valid JSON: {semantic_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ArchiveError(f"--semantic-json must contain a JSON object: {semantic_path}")

    domain, workstream = payload.get("domain"), payload.get("workstream")
    present = [n for n, v in (("domain", domain), ("workstream", workstream)) if v is not None]
    if not present:
        return payload, None, None
    if len(present) != 2:
        raise ArchiveError(
            f'--semantic-json supplies "{present[0]}" without its counterpart; supply both '
            '"domain" and "workstream" to file the Run under an existing taxonomy entry, '
            "or neither to file it under archive/unclassified/"
        )

    def segment(name: str, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ArchiveError(f'--semantic-json "{name}" must be a non-empty string')
        value = value.strip()
        if value in (".", "..") or not all(c.isalnum() or c in "._-" for c in value):
            raise ArchiveError(f'--semantic-json "{name}" is not usable as one path segment: {value!r}')
        return value

    return payload, segment("domain", domain), segment("workstream", workstream)


def destination_for(evidence_repo: Path, domain: str | None, workstream: str | None, attempt_id: str) -> Path:
    if domain:
        return evidence_repo / "archive" / domain / workstream / attempt_id
    return evidence_repo / "archive" / "unclassified" / attempt_id


def _status_lines(repository: Path) -> list[str]:
    completed = subprocess.run(
        ("git", "-C", str(repository), "status", "--porcelain=v1", "--untracked-files=all"),
        check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise ArchiveError(completed.stderr.strip() or "git status failed")
    return [line for line in completed.stdout.splitlines() if line]


def _status_path(line: str) -> str:
    rest = line[3:]
    if " -> " in rest:
        rest = rest.split(" -> ", 1)[1]
    return rest


def _assert_narrow_cleanliness(repository: Path, *, allowed_prefixes: tuple[str, ...]) -> None:
    """The narrowed cleanliness rule: nothing staged, nothing dirty outside
    the attempt's own destination and staging directory."""

    def allowed(path: str) -> bool:
        return any(path == prefix or path.startswith(f"{prefix}/") for prefix in allowed_prefixes)

    for line in _status_lines(repository):
        index_status, worktree_status = line[0], line[1]
        if index_status not in (" ", "?"):
            raise ArchiveError(f"evidence repository has pre-existing staged state: {_status_path(line)}")
        if worktree_status != " " and not allowed(_status_path(line)):
            raise ArchiveError(
                "evidence repository is not clean outside the archive destination; "
                f"commit or stash local changes first: {_status_path(line)}"
            )


def _path_is_tracked_clean(repository: Path, path_rel: str) -> bool:
    """True when nothing under ``path_rel`` is staged or dirty (it is either
    absent or fully committed)."""

    for line in _status_lines(repository):
        if _status_path(line) == path_rel or _status_path(line).startswith(f"{path_rel}/"):
            return False
    return True


@dataclass(frozen=True)
class ArchiveReport:
    outcome: str  # "dry_run" | "archived" | "resumed_commit" | "no_op"
    attempt_id: str
    run_id: str | None
    work_item_id: str | None
    run_status: str | None
    checkout_id: str | None
    repository: str | None
    state_root: Path
    ops_dir: Path | None
    ops_candidate: Path
    destination_relative: str
    semantic_path: Path | None
    files: int
    commit: str | None


def _manifest_candidate(
    *, attempt_id: str, state_root: Path, ops_dir: Path | None, semantic_payload: dict | None,
    staging: Path,
) -> tuple[dict, list[dict]]:
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    copied: list[dict] = []
    collect_run_files(state_root, staging, copied)
    if ops_dir:
        collect_ops_files(ops_dir, staging, copied)
    facts = extract_facts(state_root, ops_dir)
    manifest = {
        "attempt_id": attempt_id,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "sources": {"run_dir": str(state_root), "ops_dir": str(ops_dir) if ops_dir else None},
        "copied_files": copied,
        "facts": facts,
        "semantic": semantic_payload,
    }
    return manifest, copied


def _manifests_match_ignoring_archived_at(a: dict, b: dict) -> bool:
    def strip(manifest: dict) -> dict:
        return {k: v for k, v in manifest.items() if k != "archived_at"}

    def copied_key(entries: list[dict]) -> set[tuple[object, object, object]]:
        return {(e.get("path"), e.get("sha256"), e.get("bytes")) for e in entries}

    a2, b2 = strip(a), strip(b)
    if copied_key(a2.pop("copied_files", [])) != copied_key(b2.pop("copied_files", [])):
        return False
    return a2 == b2


def _load_existing_manifest(dest: Path) -> dict:
    manifest_path = dest / "manifest.json"
    if not manifest_path.is_file():
        raise ArchiveError(f"archive destination exists but has no manifest.json: {dest}")
    try:
        manifest = json.loads(manifest_path.read_text())
    except Exception as exc:
        raise ArchiveError(f"existing archived manifest does not parse: {manifest_path}: {exc}") from exc
    copied = manifest.get("copied_files")
    if not isinstance(copied, list) or not copied:
        raise ArchiveError(f"existing archived manifest lists no copied files: {manifest_path}")
    for entry in copied:
        rel = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(rel, str) or not (dest / rel).is_file():
            raise ArchiveError(f"existing archived manifest lists a file that is not present: {rel!r}")
    return manifest


def archive_run(
    *, state_root: Path, evidence_repo: Path, semantic_path: Path | None = None, dry_run: bool = False,
    home: Path | None = None,
) -> ArchiveReport:
    """Archive ``state_root`` (and its matching OPS directory, if any) into
    ``evidence_repo``, making exactly one scoped commit.

    ``home`` overrides ``$HOME`` for deriving the default OPS directory
    (``<home>/wfc-ops/<attempt-id>``); it defaults to the real environment,
    exactly as the ported wrapper derived it.
    """

    state_root = state_root.expanduser().resolve()
    if not state_root.is_dir():
        raise ArchiveError(f"missing state root: {state_root}")
    _reject_control_characters(str(state_root), label="state root")
    attempt_id = derive_attempt_id(state_root)

    evidence_repo = evidence_repo.expanduser().resolve()
    if not evidence_repo.is_dir():
        raise ArchiveError(f"missing evidence repository: {evidence_repo}")
    identity = repository_identity(evidence_repo)
    if identity != evidence_repo:
        raise ArchiveError(f"evidence repository path is not the repository root (root is {identity}): {evidence_repo}")
    if not (identity / "archive").is_dir():
        raise ArchiveError(f"not a WFC evidence repository (no archive/ directory): {identity}")
    base_head = repository_head(identity)

    resolved_semantic = semantic_path.expanduser().resolve() if semantic_path else None
    if resolved_semantic is not None:
        if not resolved_semantic.is_file():
            raise ArchiveError(f"--semantic-json is not a readable file: {semantic_path}")
        _reject_control_characters(str(resolved_semantic), label="--semantic-json path")

    identity_facts = run_identity(state_root)

    home_dir = (home or Path(os.environ.get("HOME", ""))).expanduser()
    if not str(home_dir):
        raise ArchiveError("HOME is not set")
    ops_candidate = home_dir / "wfc-ops" / attempt_id
    ops_dir = resolve_ops_dir(state_root, ops_candidate, attempt_id)
    # The old wrapper reports this unresolved candidate verbatim, but
    # archive_run.py itself always resolves its --ops-dir argument before
    # using it for anything else (copying, facts, the manifest); match that
    # split exactly so a symlinked HOME/scratch root cannot produce a
    # manifest that differs from the old implementation's.
    ops_dir_resolved = ops_dir.resolve() if ops_dir else None

    semantic_payload, domain, workstream = semantic_annotation(resolved_semantic)
    dest = destination_for(identity, domain, workstream, attempt_id)
    dest_rel = dest.relative_to(identity).as_posix()
    staging = identity / ".wfc-archive-staging" / attempt_id
    staging_rel = staging.relative_to(identity).as_posix()

    assert_source_isolation(dest, state_root, ops_dir_resolved)
    assert_source_isolation(staging, state_root, ops_dir_resolved)
    assert_dest_under_archive_root(dest, identity / "archive")

    if dry_run:
        planned = _plan_run_files(state_root) + (_plan_ops_files(ops_dir_resolved) if ops_dir_resolved else [])
        return ArchiveReport(
            outcome="dry_run", attempt_id=attempt_id, run_id=identity_facts.run_id,
            work_item_id=identity_facts.work_item_id, run_status=identity_facts.run_status,
            checkout_id=identity_facts.checkout_id, repository=identity_facts.repository,
            state_root=state_root, ops_dir=ops_dir, ops_candidate=ops_candidate,
            destination_relative=dest_rel, semantic_path=resolved_semantic, files=len(planned), commit=None,
        )

    _assert_narrow_cleanliness(identity, allowed_prefixes=(dest_rel, staging_rel))

    final_run_id, final_work_item_id, final_run_status = (
        identity_facts.run_id, identity_facts.work_item_id, identity_facts.run_status,
    )

    if dest.exists():
        manifest = _load_existing_manifest(dest)
        if _path_is_tracked_clean(identity, dest_rel):
            # Already committed: this must be exactly the identical repeat, never
            # a silent overwrite of a same-named, differently-sourced archive.
            candidate_manifest, _ = _manifest_candidate(
                attempt_id=attempt_id, state_root=state_root, ops_dir=ops_dir_resolved,
                semantic_payload=semantic_payload, staging=staging,
            )
            shutil.rmtree(staging, ignore_errors=True)
            if not _manifests_match_ignoring_archived_at(manifest, candidate_manifest):
                raise ArchiveError(
                    f"archive destination already exists with different content, refusing to overwrite: {dest_rel}"
                )
            facts = manifest.get("facts") or {}
            final_run_id = facts.get("run_id")
            final_work_item_id = facts.get("work_item_id")
            final_run_status = facts.get("run_status") or facts.get("status")
            commit = _run_git(identity, "log", "-1", "--format=%H", "--", dest_rel).strip()
            return ArchiveReport(
                outcome="no_op", attempt_id=attempt_id, run_id=final_run_id, work_item_id=final_work_item_id,
                run_status=final_run_status, checkout_id=identity_facts.checkout_id,
                repository=identity_facts.repository, state_root=state_root, ops_dir=ops_dir,
                ops_candidate=ops_candidate, destination_relative=dest_rel, semantic_path=resolved_semantic,
                files=len(manifest["copied_files"]), commit=commit or None,
            )
        # Placed but never committed (killed between copy and commit): resume.
        copied = manifest["copied_files"]
    else:
        try:
            manifest, copied = _manifest_candidate(
                attempt_id=attempt_id, state_root=state_root, ops_dir=ops_dir_resolved,
                semantic_payload=semantic_payload, staging=staging,
            )
            (staging / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n")
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.rename(staging, dest)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        facts = manifest.get("facts") or {}
        final_run_id = facts.get("run_id")
        final_work_item_id = facts.get("work_item_id")
        final_run_status = facts.get("run_status") or facts.get("status")

    paths = tuple(sorted(f"{dest_rel}/{entry['path']}" for entry in copied)) + (f"{dest_rel}/manifest.json",)
    intent = NewPathCommitIntent(paths=paths, subject=f"evidence: archive {attempt_id}")
    try:
        committed = bounded_path_commit(
            identity, expected_repository=identity, base_head=base_head, allowed_paths=(dest_rel,),
            intent=intent, work_item_id=attempt_id, hop_id=attempt_id,
        )
    except TrustedCommitError as error:
        raise ArchiveError(f"commit failed: {error}") from error

    return ArchiveReport(
        outcome="archived", attempt_id=attempt_id, run_id=final_run_id, work_item_id=final_work_item_id,
        run_status=final_run_status, checkout_id=identity_facts.checkout_id, repository=identity_facts.repository,
        state_root=state_root, ops_dir=ops_dir, ops_candidate=ops_candidate, destination_relative=dest_rel,
        semantic_path=resolved_semantic, files=len(copied), commit=committed.commit,
    )


def _run_git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *args), check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return completed.stdout if completed.returncode == 0 else ""


def render_report(report: ArchiveReport) -> str:
    header = {
        "dry_run": "DRY RUN -- NOTHING WRITTEN",
        "archived": "ARCHIVED WFC RUN",
        "resumed_commit": "ARCHIVED WFC RUN",
        "no_op": "ARCHIVE ALREADY UP TO DATE",
    }[report.outcome]
    ops_display = str(report.ops_dir) if report.ops_dir else f"none (absent: {report.ops_candidate})"
    semantic_display = str(report.semantic_path) if report.semantic_path else "none supplied"

    lines = [header, ""]
    lines.append(f"attempt:    {report.attempt_id}")
    lines.append(f"run:        {report.run_id or ''}")
    if report.work_item_id:
        lines.append(f"work item:  {report.work_item_id}")
    if report.run_status:
        lines.append(f"run status: {report.run_status}")
    if report.repository:
        lines.append(f"repository: {report.repository}")
    lines.append(f"source:     {report.state_root}")
    lines.append(f"ops:        {ops_display}")
    lines.append(f"dest:       {report.destination_relative}")
    lines.append(f"semantic:   {semantic_display}")

    if report.outcome == "dry_run":
        lines.append(f"files:      {report.files} planned")
        lines.append("commit:     not performed")
        lines.append("push:       not performed")
    else:
        lines.append(f"files:      {report.files}")
        lines.append("manifest:   valid")
        lines.append(f"commit:     {report.commit}")
        lines.append("push:       not performed")
    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wfc archive",
        description=(
            "Archive a completed WFC Run's evidence into the Evidence repository and "
            "make one local commit. Derives everything but the two positional "
            "arguments from authoritative Run state; never pushes."
        ),
    )
    parser.add_argument("state_root", metavar="state-root", help="WFC Run state directory, e.g. ~/wfc-state/<attempt-id>")
    parser.add_argument("evidence_repo", metavar="evidence-repo", help="a checkout of the Evidence repository")
    parser.add_argument(
        "--semantic-json", default=None, metavar="PATH",
        help='curated semantic annotation, stored verbatim; files under archive/<domain>/<workstream>/<attempt> when it carries both',
    )
    parser.add_argument("--dry-run", action="store_true", help="plan only: write nothing, commit nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        report = archive_run(
            state_root=Path(args.state_root), evidence_repo=Path(args.evidence_repo),
            semantic_path=Path(args.semantic_json) if args.semantic_json else None,
            dry_run=args.dry_run,
        )
    except (ArchiveError, GitPreflightError) as error:
        print(f"wfc archive: {error}", file=sys.stderr)
        return 1
    print(render_report(report), end="")
    return 0

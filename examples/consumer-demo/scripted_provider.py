#!/usr/bin/env python3
"""A scripted, offline stand-in for the Codex CLI, used only by ``rehearse.py``.

Transport contract: the Codex adapter invokes this executable with its usual
argument list; only ``--cd DIR`` (the repository) and ``--output-last-message
FILE`` (where the answer goes) matter here, plus the prompt on standard
input -- every other argument is ignored. The environment variable
``WFC_DEMO_SCRIPT`` names a JSON scenario file with a ``steps`` list; a
cursor file beside it (``<script>.cursor``) tracks which step answers the
next invocation. See ``docs/backlog/FTR-WFC-CONSUMER-DEMO-WORKSPACE.adoc``,
section "Scripted provider", for the full specification this implements.

This is an independent copy derived from the contract of the permanent test
fixture ``tests/fixtures/bp9/scripted_provider.py``; that fixture and its
test are unchanged by this kit. Standard library only. Makes no commit,
starts no other process, opens no network connection, and writes only the
paths its script step names, each checked to resolve inside ``--cd``.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_HEADER_LINE_RE = re.compile(r"^(\w+)=(.*)$")
_OPEN_BLOCKING_FINDING_RE = re.compile(r"^finding=([^|]+)\|state=open\|blocking=true\|", re.MULTILINE)


def _header_fields(prompt: str) -> dict[str, str]:
    header = prompt.split("\n\n", 1)[0]
    fields: dict[str, str] = {}
    for line in header.splitlines():
        match = _HEADER_LINE_RE.match(line)
        if match:
            fields[match.group(1)] = match.group(2)
    return fields


def _latest_open_blocking_finding_id(prompt: str) -> str | None:
    ids = _OPEN_BLOCKING_FINDING_RE.findall(prompt)
    return ids[-1] if ids else None


def _disposition_field(output_contract: str) -> str:
    if output_contract.startswith("work-result"):
        return "status"
    if output_contract.startswith("review-verdict"):
        return "verdict"
    if output_contract.startswith("structured_result_v"):
        return "outcome"
    raise SystemExit(f"scripted provider: unsupported output_contract: {output_contract!r}")


def _next_step(script_path: Path) -> dict:
    steps = json.loads(script_path.read_text(encoding="utf-8"))["steps"]
    cursor_path = script_path.with_name(script_path.name + ".cursor")
    index = int(cursor_path.read_text(encoding="utf-8")) if cursor_path.is_file() else 0
    if index >= len(steps):
        raise SystemExit(f"scripted provider: no step left at index {index} in {script_path}")
    cursor_path.write_text(str(index + 1), encoding="utf-8")
    return steps[index]


def _safe_target(repository: Path, relative: str) -> Path:
    if os.path.isabs(relative):
        raise SystemExit(f"scripted provider: refusing an absolute write path: {relative}")
    if ".." in Path(relative).parts:
        raise SystemExit(f"scripted provider: refusing a write path containing '..': {relative}")
    repository_real = repository.resolve()
    target = (repository_real / relative).resolve()
    if target != repository_real and repository_real not in target.parents:
        raise SystemExit(f"scripted provider: refusing a write path resolving outside --cd: {relative}")
    return target


def _apply_filesystem(repository: Path, step: dict) -> None:
    for relative, content in (step.get("write") or {}).items():
        target = _safe_target(repository, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _resolved_findings(raw_findings: list[dict], prompt: str) -> list[dict]:
    resolved = []
    for finding in raw_findings:
        finding = dict(finding)
        if finding.get("finding_id") == "__OPEN__":
            found = _latest_open_blocking_finding_id(prompt)
            if found is None:
                raise SystemExit("scripted provider: no open blocking Finding to cite for __OPEN__")
            finding["finding_id"] = found
        resolved.append(finding)
    return resolved


def _build_commit_intent(raw: dict | None, *, work_item_id: str, hop_id: str) -> dict | None:
    if raw is None:
        return None
    subject = f"{raw['type']}: {work_item_id} {hop_id} {raw['description']}"
    return {"paths": list(raw["paths"]), "subject": subject}


def main() -> int:
    args = sys.argv[1:]
    output = Path(args[args.index("--output-last-message") + 1])
    repository = Path(args[args.index("--cd") + 1])
    prompt = sys.stdin.read()
    header = _header_fields(prompt)

    script_path = Path(os.environ["WFC_DEMO_SCRIPT"])
    step = _next_step(script_path)

    expected_phase = step.get("expect_phase")
    if expected_phase is not None and header.get("phase") != expected_phase:
        raise SystemExit(
            f"scripted provider: expected phase {expected_phase!r} at this step, got {header.get('phase')!r}"
        )

    _apply_filesystem(repository, step)

    disposition_field = _disposition_field(header["output_contract"])
    raw_result = step["result"]
    result = {key: value for key, value in raw_result.items() if key not in ("disposition", "commit_intent")}
    result[disposition_field] = raw_result["disposition"]
    result["findings"] = _resolved_findings(raw_result.get("findings", []), prompt)
    result["commit_intent"] = _build_commit_intent(
        raw_result.get("commit_intent"), work_item_id=header["work_item_id"], hop_id=header["hop_id"],
    )

    payload = {
        "schema_version": 1,
        "run_id": header["run_id"],
        "hop_id": header["hop_id"],
        "invocation_id": header["invocation_id"],
        "result": result,
    }
    output.write_text(json.dumps(payload), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

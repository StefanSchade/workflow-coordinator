#!/usr/bin/env python3
"""A scriptable codex-CLI-protocol-compliant fake provider for BP9's
permanent fake-provider end-to-end acceptance test.

Transport contract identical to ``tests/fixtures/bp4/fake_provider.py``
(``--cd <repo> --output-schema <path> --output-last-message <path> -``,
prompt on stdin, one JSON envelope written to ``--output-last-message``).
Unlike that fixture, which always answers the single ``simple@v1`` implement
Hop the same way, this provider is driven by an ordered JSON "script" (its
path given by the ``WFC_BP9_SCRIPT`` environment variable) so each real
engine Hop -- across several separate process launches -- can return a
different worker status or review verdict, Finding delta, file edit and
CommitIntent. A small on-disk cursor file next to the script tracks which
step runs next.

Each script step is a JSON object:
  ``expect_phase``   (optional) asserted against this Hop's ``phase=`` header.
  ``write``          (optional) ``{relative_path: content}`` written into the
                      repository before the result is produced.
  ``delete``         (optional) list of relative paths removed first.
  ``malformed``      (optional) ``"not_json"`` or ``"schema_violation"``:
                      write deliberately bad output instead of a real result.
  ``result``         the structured-result fields, using the family-neutral
                      key ``"disposition"`` in place of ``status``/``verdict``
                      (this provider renames it to whichever the real
                      ``output_contract`` header actually demands, so one
                      script does not need to know which phase uses which
                      contract family). A ``findings`` entry's
                      ``finding_id`` may be the placeholder ``"__OPEN__"``,
                      resolved here to the most recently reported open
                      blocking Finding id found in the prompt's own
                      ``[context]`` block. ``commit_intent`` (optional) is
                      ``{"paths": [...], "type": "feat", "description": "..."}``;
                      its Conventional-Commit subject is assembled here from
                      the Hop's own real ``work_item_id``/``hop_id``, so it
                      always satisfies the Coordinator's commit-subject
                      identity check without the script hardcoding a Hop id.
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
    steps = json.loads(script_path.read_text(encoding="utf-8"))
    cursor_path = script_path.with_name(script_path.name + ".cursor")
    index = int(cursor_path.read_text(encoding="utf-8")) if cursor_path.is_file() else 0
    if index >= len(steps):
        raise SystemExit(f"scripted provider: no step left at index {index} in {script_path}")
    cursor_path.write_text(str(index + 1), encoding="utf-8")
    return steps[index]


def _apply_filesystem(repository: Path, step: dict) -> None:
    for relative, content in (step.get("write") or {}).items():
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    for relative in step.get("delete") or ():
        (repository / relative).unlink(missing_ok=True)


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

    script_path = Path(os.environ["WFC_BP9_SCRIPT"])
    step = _next_step(script_path)

    expected_phase = step.get("expect_phase")
    if expected_phase is not None and header.get("phase") != expected_phase:
        raise SystemExit(
            f"scripted provider: expected phase {expected_phase!r} at this step, got {header.get('phase')!r}"
        )

    malformed = step.get("malformed")
    if malformed == "not_json":
        output.write_text("scripted provider: deliberately not JSON {{{\n", encoding="utf-8")
        return 0
    if malformed == "schema_violation":
        output.write_text(
            json.dumps({"schema_version": 1, "run_id": header.get("run_id", "")}), encoding="utf-8",
        )
        return 0

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
    # A fixed, deterministic token-usage event on stdout, matching the real
    # `codex exec --json` wire contract's own `turn.completed` event (WI-WFC-
    # TOKEN-USAGE-S1-001): every BP9 Hop this provider answers this way now
    # carries real, non-zero usage end to end, from capture through archive
    # to `wfc report`.
    print(json.dumps({
        "type": "turn.completed",
        "usage": {
            "input_tokens": 42, "cached_input_tokens": 5, "cache_write_input_tokens": 0,
            "output_tokens": 7, "reasoning_output_tokens": 1,
        },
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

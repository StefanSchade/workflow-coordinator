#!/usr/bin/env python3
"""A minimal codex-CLI-protocol-compliant fake provider for BP4a lifecycle tests.

Reads the real Codex CLI invocation shape (``--cd <repo> --output-schema
<path> --output-last-message <path> -``, prompt on stdin) and writes a single
``accept``-routed ``work result`` envelope for the closed ``simple@v1``
program's one-Hop ``implement`` phase. It makes no repository mutation and
no commit, since ``simple@v1``'s ``implement`` phase does not require one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _fact(prompt: str, name: str) -> str:
    return prompt.split(f"{name}=", 1)[1].split("\n", 1)[0]


def main() -> int:
    args = sys.argv[1:]
    output = Path(args[args.index("--output-last-message") + 1])
    prompt = sys.stdin.read()
    result = {
        "schema_version": 1,
        "outcome": "completed",
        "requested_route": "accept",
        "scope_changed": False,
        "requires_human": False,
        "escalation": None,
        "findings": [],
        "claimed_commits": [],
        "commit_intent": None,
        "evidence_refs": [],
        "summary": "fake provider: nothing to change; task-check governs completion.",
    }
    payload = {
        "schema_version": 1,
        "run_id": _fact(prompt, "run_id"),
        "hop_id": _fact(prompt, "hop_id"),
        "invocation_id": _fact(prompt, "invocation_id"),
        "result": result,
    }
    output.write_text(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

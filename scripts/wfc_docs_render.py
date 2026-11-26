#!/usr/bin/env python3
"""Strictly render every `.adoc` file under a documentation root.

Runs `asciidoctor --safe-mode=safe -a assembled -o /dev/null --failure-level=WARN`
on each file found recursively under the given root, continuing past a failing
file so every failure is reported in one pass. Writes nothing into the
repository; `-o /dev/null` discards each rendered document. A missing
`asciidoctor` fails the gate -- it never causes a silent skip.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ASCIIDOCTOR_ARGS = (
    "--safe-mode=safe",
    "-a",
    "assembled",
    "-o",
    "/dev/null",
    "--failure-level=WARN",
)


def render_all(root: Path) -> tuple[list[Path], list[Path]]:
    files = sorted(root.rglob("*.adoc"))
    failed = []
    for path in files:
        result = subprocess.run(
            ["asciidoctor", *ASCIIDOCTOR_ARGS, str(path)], capture_output=True, text=True
        )
        if result.returncode != 0:
            failed.append(path)
            sys.stderr.write(result.stderr)
    return files, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="documentation root to render recursively")
    arguments = parser.parse_args()

    if shutil.which("asciidoctor") is None:
        print("docs-render: FAIL: asciidoctor is not installed", file=sys.stderr)
        return 1

    files, failed = render_all(arguments.root)
    if failed:
        print("docs-render: FAIL: strict render failed for:", file=sys.stderr)
        for path in failed:
            print(f"  - {path}", file=sys.stderr)
        return 1
    print(f"docs-render: {len(files)} file(s) rendered strictly, all passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

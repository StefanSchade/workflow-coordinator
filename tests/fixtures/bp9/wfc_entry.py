#!/usr/bin/env python3
"""The real installed ``wfc`` console-script body, runnable without a pip
install so BP9's hermetic consumer-repository scenario can invoke the exact
same dispatcher (``workflow_coordinator.frontdoor.cli:main``) that
``pyproject.toml``'s ``[project.scripts]`` points at, as a real subprocess --
both as the outer ``wfc run``/``wfc resume`` entry point and (via
``frontdoor.lifecycle``'s own self-re-entry for its legacy bare-flag engine
launch) as the inner engine process. No product behavior is added here.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from workflow_coordinator.frontdoor.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

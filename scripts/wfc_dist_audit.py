#!/usr/bin/env python3
"""Audit built distribution archives against ch04_release_management.adoc's C2 allow-list.

Reads the wheel and sdist archives given on the command line and fails on any
entry outside the exact allow-list, and on a wheel missing any of the four
reviewed resource texts or the four agent-skill files; never writes to or
modifies an artifact. One rule, one implementation: this module is both the
Make target's CLI and the distribution test's caller.
"""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path

WHEEL_RESOURCE_FILES = frozenset(
    {
        "workflow_coordinator/resources/agent_operating_contract_v6.txt",
        "workflow_coordinator/resources/roles/reviewer.txt",
        "workflow_coordinator/resources/roles/simple-self-check.txt",
        "workflow_coordinator/resources/roles/worker.txt",
    }
)
WHEEL_SKILL_FILES = frozenset(
    {
        "workflow_coordinator/agent_skill/wfc/SKILL.md",
        "workflow_coordinator/agent_skill/wfc/references/setup.md",
        "workflow_coordinator/agent_skill/wfc/references/commission.md",
        "workflow_coordinator/agent_skill/wfc/references/stops-and-evidence.md",
    }
)
SDIST_TOP_LEVEL_FILES = frozenset(
    {"pyproject.toml", "setup.cfg", "PKG-INFO", "README.md", "LICENSE"}
)
FORBIDDEN_NAMES = (
    "docs/",
    "tests/",
    "governance/",
    "project_memory/",
    ".wfc/",
    "scripts/",
    "requirements/",
    "tmp/",
    "build/",
    "*.ods",
    "any Evidence or Run artifact",
)


def _wheel_entry_allowed(name: str) -> bool:
    is_directory = name.endswith("/")
    normalized = name.rstrip("/")
    if not normalized:
        return True
    if normalized in WHEEL_RESOURCE_FILES or normalized in WHEEL_SKILL_FILES:
        return True
    top_level = normalized.split("/", 1)[0]
    if top_level.endswith(".dist-info"):
        return True
    if top_level != "workflow_coordinator":
        return False
    if is_directory:
        return True
    return normalized.endswith(".py")


def _sdist_entry_allowed(relative: str, is_directory: bool) -> bool:
    if not relative:
        return True
    if is_directory:
        return relative == "src" or relative.startswith("src/")
    if relative in SDIST_TOP_LEVEL_FILES:
        return True
    return relative.startswith("src/")


def _violation(path: Path, entry: str) -> str:
    return (
        f"{path.name}: unlisted entry '{entry}' is not in the C2 allow-list "
        f"(forbidden by name: {', '.join(FORBIDDEN_NAMES)}, or any other unlisted path)"
    )


def audit_wheel(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    violations = [_violation(path, name) for name in names if not _wheel_entry_allowed(name)]
    for missing in sorted(WHEEL_RESOURCE_FILES - set(names)):
        violations.append(f"{path.name}: missing required resource file '{missing}'")
    for missing in sorted(WHEEL_SKILL_FILES - set(names)):
        violations.append(f"{path.name}: missing required skill file '{missing}'")
    return violations


def _sdist_root(path: Path) -> str:
    name = path.name
    return name[: -len(".tar.gz")] if name.endswith(".tar.gz") else name


def audit_sdist(path: Path) -> list[str]:
    violations = []
    root = _sdist_root(path)
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if member.name == root:
                continue
            if not member.name.startswith(f"{root}/"):
                violations.append(_violation(path, member.name))
                continue
            relative = member.name[len(root) + 1 :]
            if not _sdist_entry_allowed(relative, member.isdir()):
                violations.append(_violation(path, member.name))
    return violations


def audit(path: Path) -> list[str]:
    if path.name.endswith(".whl"):
        return audit_wheel(path)
    if path.name.endswith(".tar.gz"):
        return audit_sdist(path)
    raise ValueError(f"unrecognized archive type (expected .whl or .tar.gz): {path}")


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "archives", nargs="+", type=Path, help="wheel (.whl) and sdist (.tar.gz) paths to audit"
    )
    return parser.parse_args()


def main() -> int:
    arguments = _parse_arguments()
    violations: list[str] = []
    for path in arguments.archives:
        if not path.is_file():
            violations.append(f"{path}: not a file")
            continue
        violations.extend(audit(path))
    for violation in violations:
        print(f"FAIL: {violation}")
    if violations:
        return 1
    print(f"dist-audit: all checks passed ({len(arguments.archives)} archive(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Write the version across every enumerated site in one explicit invocation.

The only writing release tool: `scripts/wfc_release_check.py` and
`scripts/wfc_dist_audit.py` stay read-only and network-free. This script
reads the current version from `pyproject.toml`, validates the new version
and the whole enumerated surface first, and writes nothing unless every site
validates -- a partial write is never left behind. It never runs Git and
never starts a subprocess.

The surface list below is the single source of truth for which files carry
the version and how; `tests/unit/test_release_tooling.py` exercises it on
synthetic trees and `tests/unit/test_release_version_surface.py` guards this
checkout's own two highest-risk sites against drift.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
RELEASE_NOTES = Path("docs/release_notes/release_notes.adoc")

# The canonical PEP 440 version-matching pattern (PEP 440 Appendix B).
_PEP440_VERSION_RE = re.compile(
    r"""
    ^\s*
    v?
    (?:(?:[0-9]+)!)?                          # epoch
    [0-9]+(?:\.[0-9]+)*                       # release segment
    (?:[-_.]?(?:a|b|c|rc|alpha|beta|pre|preview)[-_.]?[0-9]*)?   # pre-release
    (?:
        (?:-[0-9]+)
        |
        (?:[-_.]?(?:post|rev|r)[-_.]?[0-9]*)
    )?                                        # post-release
    (?:[-_.]?dev[-_.]?[0-9]*)?                 # dev-release
    (?:\+[a-z0-9]+(?:[-_.][a-z0-9]+)*)?        # local version
    \s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)

_NOTES_HEADING_RE = re.compile(r"^==\s+(\S+)\s+--\s+(.+?)\s*$", re.MULTILINE)


class Site(NamedTuple):
    """One rewrite rule applied inside one file.

    ``pattern`` and ``replacement`` are already built for the concrete
    ``old``/``new`` version pair (see `surface`). ``exact_one`` marks a
    `version_literal` site, whose pattern must match exactly once.
    """

    path: str
    rule: str
    pattern: "re.Pattern[str]"
    replacement: str
    exact_one: bool = False


def _version_literal(path: str, pattern: str, replacement: str, *, flags: int = 0) -> Site:
    return Site(path, "version_literal", re.compile(pattern, flags), replacement, exact_one=True)


def _tag_pinned_url(path: str, old: str, new: str) -> Site:
    return Site(path, "tag_pinned_url", re.compile(rf"/blob/v{re.escape(old)}/"), f"/blob/v{new}/")


def _install_pin(path: str, pattern: str, replacement: str) -> Site:
    return Site(path, "install_pin", re.compile(pattern), replacement)


def surface(old: str, new: str) -> tuple[Site, ...]:
    """The enumerated version-bearing surface this slice owns.

    Ten files: the three `version_literal` sites (D1 keeps the deliberate
    test literal), the Agent Skill's tag-pinned URLs and its one install
    pin, README.md's tag-pinned URLs and two install/version mentions, and
    the chapter 9 quickstart's four install/version mentions. The release
    notes heading is handled separately in `_prepare_release_notes`. The
    chapter 9 "What was verified" tutorial lines are deliberately excluded
    (maintainer decision D4): they are records, not pins.
    """
    old_re = re.escape(old)
    skill = "src/workflow_coordinator/agent_skill/wfc"
    quickstart = "docs/operator_manual/ch09_consumer_quickstart/01_quickstart.adoc"
    return (
        _version_literal(
            "pyproject.toml", rf'^version = "{old_re}"$', f'version = "{new}"', flags=re.MULTILINE
        ),
        _version_literal(
            "src/workflow_coordinator/implementation_identity.py",
            rf'^VERSION = "{old_re}"$',
            f'VERSION = "{new}"',
            flags=re.MULTILINE,
        ),
        _version_literal(
            "tests/architecture/test_bp1_identity.py",
            rf"first\['version'\] == '{old_re}'",
            f"first['version'] == '{new}'",
        ),
        _tag_pinned_url("README.md", old, new),
        _install_pin("README.md", rf"\bOnce {old_re} is\b", f"Once {new} is"),
        _install_pin(
            "README.md", rf"workflow-coordinator=={old_re}\b", f"workflow-coordinator=={new}"
        ),
        _tag_pinned_url(f"{skill}/SKILL.md", old, new),
        _tag_pinned_url(f"{skill}/references/commission.md", old, new),
        _tag_pinned_url(f"{skill}/references/setup.md", old, new),
        _install_pin(
            f"{skill}/references/setup.md",
            rf"workflow-coordinator=={old_re}\b",
            f"workflow-coordinator=={new}",
        ),
        _tag_pinned_url(f"{skill}/references/stops-and-evidence.md", old, new),
        _install_pin(quickstart, rf"\bOnce {old_re} is\b", f"Once {new} is"),
        _install_pin(
            quickstart, rf"workflow-coordinator=={old_re}\b", f"workflow-coordinator=={new}"
        ),
        _install_pin(quickstart, rf"`=={old_re}` pin installs", f"`=={new}` pin installs"),
        _install_pin(quickstart, rf'"version": "{old_re}"', f'"version": "{new}"'),
    )


def _read_current_version(repo: Path) -> tuple[str | None, str | None]:
    path = repo / "pyproject.toml"
    if not path.is_file():
        return None, "pyproject.toml not found"
    match = re.search(r'^version = "([^"]+)"$', path.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        return None, 'pyproject.toml has no `version = "..."` line'
    return match.group(1), None


def _prepare_release_notes(repo: Path, new: str) -> tuple[str | None, str | None]:
    path = repo / RELEASE_NOTES
    if not path.is_file():
        return None, f"release notes file not found: {RELEASE_NOTES}"
    text = path.read_text(encoding="utf-8")
    status = None
    for match in _NOTES_HEADING_RE.finditer(text):
        if match.group(1) == new:
            status = match.group(2).strip()
            break
    if status is None:
        return None, f"release notes has no section for version {new}"
    if status.lower() == "(released)":
        return None, f"release notes section for {new} is already '(released)'"
    if not status.lower().startswith("planned"):
        return None, f"release notes section for {new} has an unexpected status: {status!r}"
    old_heading = f"== {new} -- {status}"
    new_heading = f"== {new} -- (released)"
    return text.replace(old_heading, new_heading, 1), None


def prepare(repo: Path, new_version: str) -> list[str]:
    """Validate the whole surface, then write it. Returns a list of refusal
    reasons; an empty list means every enumerated file was rewritten."""
    if not _PEP440_VERSION_RE.match(new_version):
        return [f"version {new_version!r} is not a valid PEP 440 version"]

    old_version, error = _read_current_version(repo)
    if error:
        return [error]

    errors: list[str] = []
    rewritten: dict[Path, str] = {}
    for site in surface(old_version, new_version):
        path = repo / site.path
        if path not in rewritten:
            if not path.is_file():
                errors.append(f"site file not found: {site.path}")
                continue
            rewritten[path] = path.read_text(encoding="utf-8")
        text = rewritten[path]
        count = len(site.pattern.findall(text))
        if site.exact_one and count != 1:
            errors.append(
                f"{site.path}: expected exactly one occurrence of the version literal "
                f"'{old_version}', found {count}"
            )
            continue
        if count == 0:
            errors.append(f"{site.path}: expected {site.rule} pattern not found")
            continue
        rewritten[path] = site.pattern.sub(site.replacement, text)

    notes_text, notes_error = _prepare_release_notes(repo, new_version)
    if notes_error:
        errors.append(notes_error)

    if errors:
        return errors

    for path, text in rewritten.items():
        path.write_text(text, encoding="utf-8")
    if notes_text is not None:
        (repo / RELEASE_NOTES).write_text(notes_text, encoding="utf-8")
    return []


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="the version to release, e.g. 0.1.0b2")
    parser.add_argument(
        "--repo", type=Path, default=ROOT, help="repository to prepare (default: this repository)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    errors = prepare(arguments.repo.resolve(), arguments.version)
    for error in errors:
        print(f"FAIL: {error}")
    if errors:
        return 1
    print(f"release-prepare: version set to {arguments.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

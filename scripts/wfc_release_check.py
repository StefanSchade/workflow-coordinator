#!/usr/bin/env python3
"""Read-only release preflight, implementing ch04_release_management.adoc's contract.

Writes nothing and never touches the network. The checkout mode is read from
``git symbolic-ref -q HEAD``: attached HEAD is branch mode (pre-tag, on
``main``); detached HEAD is tag mode (the tagged commit, checked out for a
release run). Both modes assert tag-equals-version, frozen release notes and
absolute README documentation links; branch mode additionally asserts branch
identity, a clean tree and sync with ``origin/main``; tag mode instead asserts
that the checked-out commit is reachable from ``origin/main``.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE_NOTES = Path("docs/release_notes/release_notes.adoc")
README = Path("README.md")
NOTES_HEADING = re.compile(r"^==\s+(\S+)\s+--\s+(.+?)\s*$")
README_LINK = re.compile(r"\[[^\]\n]*\]\(([^\s)]+)\)")


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )


def _detect_mode(repo: Path) -> tuple[str, str | None]:
    result = _run_git(repo, "symbolic-ref", "-q", "--short", "HEAD")
    if result.returncode == 0:
        return "branch", result.stdout.strip()
    return "tag", None


def _read_version(repo: Path) -> str:
    with (repo / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    return data["project"]["version"]


def _resolve_tag(
    repo: Path, mode: str, explicit_tag: str | None, version: str
) -> tuple[str | None, str | None]:
    if explicit_tag:
        return explicit_tag, None
    if mode == "branch":
        return f"v{version}", None
    described = _run_git(repo, "describe", "--tags", "--exact-match", "HEAD")
    if described.returncode != 0:
        return None, "HEAD is detached but not checked out at an exact tag; pass --tag explicitly"
    return described.stdout.strip(), None


def check_on_main(branch: str | None) -> str | None:
    if branch != "main":
        return f"HEAD is on branch '{branch}', not 'main'; release tags must come from main"
    return None


def check_clean_tree(repo: Path) -> str | None:
    result = _run_git(repo, "status", "--porcelain")
    if result.stdout.strip():
        return "working tree is not clean; commit or stash before tagging"
    return None


def _remote_main_sha(repo: Path) -> str | None:
    result = _run_git(repo, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main")
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def check_local_main_sync(repo: Path) -> str | None:
    remote_sha = _remote_main_sha(repo)
    if remote_sha is None:
        return "origin/main is not known locally; fetch origin before running this check"
    local = _run_git(repo, "rev-parse", "--verify", "--quiet", "refs/heads/main")
    if local.returncode != 0:
        return "local branch 'main' does not exist"
    local_sha = local.stdout.strip()
    if local_sha != remote_sha:
        return f"local main ({local_sha[:12]}) is out of sync with origin/main ({remote_sha[:12]})"
    return None


def check_head_reachable_from_origin_main(repo: Path) -> str | None:
    remote_sha = _remote_main_sha(repo)
    if remote_sha is None:
        return "origin/main is not known locally; fetch origin before running this check"
    result = _run_git(repo, "merge-base", "--is-ancestor", "HEAD", remote_sha)
    if result.returncode != 0:
        return "HEAD is not reachable from origin/main"
    return None


def check_tag_matches_version(version: str, tag: str) -> str | None:
    expected = f"v{version}"
    if tag != expected:
        return f"tag '{tag}' does not match the version-derived tag '{expected}'"
    return None


def check_release_notes(repo: Path, version: str) -> str | None:
    path = repo / RELEASE_NOTES
    if not path.is_file():
        return f"release notes file not found: {RELEASE_NOTES}"
    for line in path.read_text(encoding="utf-8").splitlines():
        match = NOTES_HEADING.match(line)
        if match and match[1] == version:
            if match[2].strip().lower().startswith("planned"):
                return f"release notes section for {version} is still marked 'planned'"
            return None
    return f"release notes has no section for version {version}"


def check_readme_links(repo: Path) -> str | None:
    path = repo / README
    if not path.is_file():
        return f"README.md not found: {README}"
    offenders = [
        target
        for target in README_LINK.findall(path.read_text(encoding="utf-8"))
        if not target.startswith(("http://", "https://", "#", "mailto:"))
    ]
    if offenders:
        return "README.md has repository-relative documentation link(s): " + ", ".join(offenders)
    return None


def run_checks(repo: Path, explicit_tag: str | None) -> tuple[str, list[str]]:
    mode, branch = _detect_mode(repo)
    version = _read_version(repo)
    tag, tag_error = _resolve_tag(repo, mode, explicit_tag, version)

    checks: list[str | None] = []
    if mode == "branch":
        checks += [check_on_main(branch), check_clean_tree(repo), check_local_main_sync(repo)]
    checks.append(check_head_reachable_from_origin_main(repo))
    if tag_error:
        checks.append(tag_error)
    else:
        checks.append(check_tag_matches_version(version, tag))
    checks += [check_release_notes(repo, version), check_readme_links(repo)]
    return mode, [check for check in checks if check]


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", type=Path, default=ROOT, help="repository to check (default: this repository)"
    )
    parser.add_argument(
        "--tag",
        default=os.environ.get("TAG"),
        help="the tag to validate (default: v<version> in branch mode, "
        "the checked-out tag in tag mode)",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _parse_arguments()
    mode, failures = run_checks(arguments.repo.resolve(), arguments.tag)
    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        return 1
    print(f"release-check: all checks passed ({mode} mode)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

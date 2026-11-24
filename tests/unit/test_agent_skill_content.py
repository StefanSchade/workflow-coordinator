"""Content lint and drift guards for the packaged wfc Agent Skill (S1).

Every check here is static: it reads the four packaged Markdown files and
compares them against this tree's own real CLI, EXIT_CODES and VERSION --
never against memory or a separately maintained copy.
"""

from __future__ import annotations

import contextlib
import io
import re
import shlex
import stat
from pathlib import Path

import pytest
import yaml

from workflow_coordinator.frontdoor.cli import main as cli_main
from workflow_coordinator.frontdoor.lifecycle import EXIT_CODES
from workflow_coordinator.implementation_identity import VERSION

ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = ROOT / "src/workflow_coordinator/agent_skill/wfc"
SKILL_FILE = SKILL_ROOT / "SKILL.md"
REFERENCE_FILES = sorted((SKILL_ROOT / "references").glob("*.md"))
ALL_SKILL_FILES = (SKILL_FILE, *REFERENCE_FILES)

PORTABLE_FRONTMATTER_KEYS = {"name", "description", "license", "compatibility", "metadata"}
WORKER_REVIEWER_EXCLUSION = "Not for use inside a WFC Worker or Reviewer Hop."
FORBIDDEN_SNIPPETS = ("$ARGUMENTS", "${CLAUDE_SKILL_DIR}", "allowed-tools")
TOP_LEVEL_FLAG_COMBOS = ({"--version"}, {"--version", "--json"})

# One subcommand invoker per real CLI dispatch branch (``frontdoor/cli.py``),
# so the drift guard runs each subcommand's own real argparse parser.
SUBCOMMAND_MAIN = {
    "init": lambda argv: cli_main(["init", *argv]),
    "plan": lambda argv: cli_main(["plan", *argv]),
    "archive": lambda argv: cli_main(["archive", *argv]),
    "audit": lambda argv: cli_main(["audit", *argv]),
    "report": lambda argv: cli_main(["report", *argv]),
    "run": lambda argv: cli_main(["run", *argv]),
    "resume": lambda argv: cli_main(["resume", *argv]),
    "status": lambda argv: cli_main(["status", *argv]),
    "skill": lambda argv: cli_main(["skill", *argv]),
}

GITHUB_BLOB_RE = re.compile(
    r"^https://github\.com/StefanSchade/workflow-coordinator/blob/v([^/]+)/([^#]+)(?:#.*)?$"
)
MARKDOWN_LINK_RE = re.compile(r"\]\((https://[^)\s]+)\)")


def _parse_frontmatter(text: str) -> dict:
    assert text.startswith("---\n"), "SKILL.md must open with a YAML frontmatter block"
    end = text.index("\n---", 4)
    return yaml.safe_load(text[4:end])


def _skill_frontmatter() -> dict:
    return _parse_frontmatter(SKILL_FILE.read_text(encoding="utf-8"))


def _help_text(subcommand: str) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            SUBCOMMAND_MAIN[subcommand](["--help"])
        except SystemExit:
            pass
    return buffer.getvalue()


def _accepted_flags(subcommand: str) -> set[str]:
    return set(re.findall(r"--[a-zA-Z][a-zA-Z0-9-]*", _help_text(subcommand)))


def _fenced_shell_lines(text: str):
    in_fence = False
    lang = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                in_fence = True
                lang = stripped[3:].strip().lower()
            else:
                in_fence = False
                lang = None
            continue
        if in_fence:
            yield lang, line


def _logical_commands(text: str):
    """Join backslash-continued lines inside fenced shell/bash/sh/text blocks."""
    buffer: list[str] = []
    for lang, line in _fenced_shell_lines(text):
        if lang not in {"shell", "bash", "sh", "text"}:
            continue
        rstripped = line.rstrip()
        if rstripped.endswith("\\"):
            buffer.append(rstripped[:-1])
            continue
        buffer.append(rstripped)
        yield " ".join(buffer)
        buffer = []
    if buffer:
        yield " ".join(buffer)


def test_exactly_four_skill_files_and_none_executable():
    found = sorted(p for p in SKILL_ROOT.rglob("*") if p.is_file())
    assert found == sorted(ALL_SKILL_FILES)
    for path in found:
        mode = path.stat().st_mode
        assert not mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH), path


def test_no_forbidden_directories_or_files():
    names = {p.name for p in SKILL_ROOT.iterdir()}
    assert names == {"SKILL.md", "references"}
    for forbidden in ("scripts", "assets", "agents"):
        assert not (SKILL_ROOT / forbidden).exists()


@pytest.mark.parametrize("path", ALL_SKILL_FILES, ids=lambda p: p.name)
def test_no_forbidden_syntax(path):
    text = path.read_text(encoding="utf-8")
    for snippet in FORBIDDEN_SNIPPETS:
        assert snippet not in text, f"{path.name} contains forbidden syntax {snippet!r}"
    for lang, line in _fenced_shell_lines(text):
        assert not line.lstrip().startswith("!"), (
            f"{path.name} has an injection-style '!'-prefixed line in a fenced block"
        )


def test_frontmatter_parses_with_portable_keys_only():
    frontmatter = _skill_frontmatter()
    assert set(frontmatter) <= PORTABLE_FRONTMATTER_KEYS
    assert frontmatter["name"] == "wfc"
    assert frontmatter["name"] == SKILL_FILE.parent.name
    assert frontmatter["license"] == "MIT"
    assert "compatibility" in frontmatter


def test_description_length_and_worker_reviewer_exclusion():
    description = _skill_frontmatter()["description"]
    assert len(description) <= 1024
    assert WORKER_REVIEWER_EXCLUSION in description


def test_skill_md_body_order_and_line_limits():
    lines = SKILL_FILE.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 300
    headings = [(number, line) for number, line in enumerate(lines, start=1) if line.startswith("## ")]
    expected = [
        "## What this is",
        "## Authorization",
        "## Preflight",
        "## When WFC fits, and when it does not",
        "## The loop",
        "## Never",
        "## Where exact facts come from",
    ]
    assert [line for _, line in headings] == expected
    # The first three sections ("What this is", "Authorization", "Preflight")
    # must lie entirely inside the first 80 lines: bounded by where the
    # fourth heading ("When WFC fits...") begins.
    fourth_heading_line_number = headings[3][0]
    assert fourth_heading_line_number - 1 <= 80


def test_reference_files_are_linked_one_level_deep_and_line_bounded():
    skill_text = SKILL_FILE.read_text(encoding="utf-8")
    for reference in REFERENCE_FILES:
        assert f"references/{reference.name}" in skill_text
        lines = reference.read_text(encoding="utf-8").splitlines()
        assert len(lines) <= 200
        opening = "\n".join(lines[:6])
        assert "Operator Manual" in opening, f"{reference.name} must open citing the Operator Manual"


def test_name_matches_skill_directory_name():
    assert SKILL_FILE.parent.name == "wfc"
    for reference in REFERENCE_FILES:
        assert reference.parent.name == "references"
        assert reference.parent.parent.name == "wfc"


def test_classifications_named_in_stops_and_evidence_exist_in_exit_codes():
    stops_file = next(f for f in REFERENCE_FILES if f.name == "stops-and-evidence.md")
    lines = stops_file.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## Every classification"))
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines)
    )
    section = "\n".join(lines[start:end])
    named = re.findall(r"`([a-z][a-z_]*)`", section)
    assert named, "expected at least one classification named in the classification section"
    for classification in named:
        assert classification in EXIT_CODES, (
            f"{classification!r} is named in stops-and-evidence.md but is not a key of EXIT_CODES"
        )


def test_documentation_urls_pin_version_and_resolve_in_tree():
    found_any = False
    for path in ALL_SKILL_FILES:
        text = path.read_text(encoding="utf-8")
        for url in MARKDOWN_LINK_RE.findall(text):
            if "github.com/StefanSchade/workflow-coordinator/blob/" not in url:
                continue
            found_any = True
            match = GITHUB_BLOB_RE.match(url)
            assert match, f"{path.name}: documentation URL not tag-pinned as expected: {url}"
            version, file_fragment = match.groups()
            assert version == VERSION, f"{path.name}: {url} is pinned to {version!r}, not {VERSION!r}"
            assert (ROOT / file_fragment).is_file(), f"{path.name}: {url} does not resolve to a file in the tree"
    assert found_any, "expected at least one tag-pinned documentation URL across the skill files"


def test_drift_guard_wfc_commands_match_real_cli():
    checked_any = False
    for path in ALL_SKILL_FILES:
        text = path.read_text(encoding="utf-8")
        for command in _logical_commands(text):
            tokens = shlex.split(command)
            if not tokens or tokens[0] != "wfc" or len(tokens) < 2:
                continue
            checked_any = True
            second = tokens[1]
            if second.startswith("--"):
                flags = {token for token in tokens[1:] if token.startswith("--")}
                assert flags in TOP_LEVEL_FLAG_COMBOS, (
                    f"{path.name}: unrecognized top-level invocation {command!r}"
                )
                continue
            subcommand = second
            assert subcommand in SUBCOMMAND_MAIN, (
                f"{path.name}: {command!r} names a subcommand this CLI does not accept"
            )
            accepted = _accepted_flags(subcommand) | {"--help"}
            used = {token for token in tokens[2:] if token.startswith("--")}
            unknown = used - accepted
            assert not unknown, (
                f"{path.name}: {command!r} uses flag(s) {unknown} not accepted by "
                f"'wfc {subcommand} --help'"
            )
    assert checked_any, "expected at least one 'wfc ...' line in a fenced shell block"

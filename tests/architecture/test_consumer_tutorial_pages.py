"""Wording and structure checks for `docs/operator_manual/ch09_consumer_quickstart/`
(`docs/backlog/FTR-WFC-CONSUMER-TUTORIALS.adoc`): SI-1/SI-2 (AC-8), the
seven-section tutorial page contract (AC-7, as far as it is mechanical), and
the anchor inventory that must survive the change (AC-9).
"""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent.parent
CHAPTER = REPOSITORY / "docs" / "operator_manual" / "ch09_consumer_quickstart"

_SHELL_BLOCK_RE = re.compile(r"\[source,shell\]\n-{4,}\n(.*?)\n-{4,}", re.DOTALL)
_HEADING_RE = re.compile(r"^(=+)\s+(.+?)\s*$")
_ANCHOR_BLOCK_RE = re.compile(r"\[\[([A-Za-z0-9_\-]+)(?:,[^\]]*)?\]\]")

TUTORIAL_FILES = (
    "02_first_run_example.adoc",
    "03_tutorial_program_change.adoc",
    "04_tutorial_stops.adoc",
)
# SI-1/SI-2 (AC-8) apply to the overview and the three tutorials this
# Commission authors. `01_quickstart.adoc` keeps its own pre-existing,
# unrelated "Unattended `wfc run --yes`" example for non-interactive/CI use
# (out of scope here; see WITHHELD AUTHORITY), which is not a tutorial live
# Run pattern and is not a rehearsal.
WORDING_CHECKED_FILES = ("00_overview.adoc",) + TUTORIAL_FILES
PAGE_CONTRACT_HEADINGS = (
    "Goal",
    "The task",
    "Rehearse it",
    "What to look at",
    "Run it live",
    "What was verified",
    "Next",
)

# AC-9: every anchor present in these two files before this change (from
# `git show HEAD~N:<file>` at the commit preceding this Commission) must
# still exist somewhere in the chapter afterward.
PRE_EXISTING_QUICKSTART_ANCHORS = (
    "consumer-quickstart",
    "quickstart-prerequisites",
    "quickstart-git-identity-and-filters",
    "quickstart-bwrap-and-platform",
    "quickstart-install",
    "quickstart-skill",
    "quickstart-init",
    "quickstart-request",
    "quickstart-plan",
    "quickstart-run",
    "quickstart-status",
    "quickstart-evidence-location",
    "quickstart-next",
    "beta-limitations",
)
PRE_EXISTING_FIRST_RUN_ANCHORS = (
    "consumer-quickstart-first-run-example",
    "first-run-verified",
    "first-run-layout",
    "first-run-repository",
    "first-run-init",
    "first-run-task",
    "first-run-plan",
    "first-run-request",
    "first-run-run",
    "first-run-status",
    "first-run-concepts",
    "first-run-why-before-and-after",
    "first-run-next",
)


def _chapter_sources() -> list[Path]:
    return sorted(CHAPTER.glob("*.adoc"))


def _chapter_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in _chapter_sources())


def _shell_blocks(text: str) -> list[str]:
    return _SHELL_BLOCK_RE.findall(text)


# ==========================================================================
# SI-1 / SI-2 (AC-8).
# ==========================================================================

def test_si1_no_shell_block_in_the_overview_or_a_tutorial_passes_dash_dash_yes():
    for name in WORDING_CHECKED_FILES:
        for block in _shell_blocks((CHAPTER / name).read_text(encoding="utf-8")):
            assert "--yes" not in block, f"{name}: a shell block contains --yes"


def _headings_with_blocks(text: str) -> list[tuple[str, str]]:
    """Return (enclosing_heading_text, block_text) for every delimited
    listing block (any `[source,...]` or bare) in document order."""

    lines = text.splitlines()
    current_heading = ""
    results = []
    index = 0
    while index < len(lines):
        heading = _HEADING_RE.match(lines[index])
        if heading is not None:
            current_heading = heading.group(2)
            index += 1
            continue
        if re.match(r"^-{4,}$", lines[index].strip()):
            start = index + 1
            scan = start
            while scan < len(lines) and not re.match(r"^-{4,}$", lines[scan].strip()):
                scan += 1
            results.append((current_heading, "\n".join(lines[start:scan])))
            index = scan + 1
            continue
        index += 1
    return results


def test_si2_every_rehearsal_banner_block_sits_under_a_rehearse_heading():
    failures = []
    for name in WORDING_CHECKED_FILES:
        text = (CHAPTER / name).read_text(encoding="utf-8")
        for heading, block in _headings_with_blocks(text):
            if "REHEARSAL" in block and "Rehearse" not in heading:
                failures.append(f"{name}: a REHEARSAL block sits under heading {heading!r}")
    assert not failures, "\n".join(failures)


def test_guard_self_test_si2_catches_a_misplaced_rehearsal_block():
    text = "\n".join(
        [
            "=== Some other heading",
            "",
            "----",
            "REHEARSAL -- no model was called.",
            "----",
            "",
        ]
    )
    results = [(heading, block) for heading, block in _headings_with_blocks(text) if "REHEARSAL" in block]
    assert results == [("Some other heading", "REHEARSAL -- no model was called.")]


# ==========================================================================
# AC-7: the seven-section page contract, as far as it is mechanical.
# ==========================================================================

def _top_level_section_headings(text: str) -> list[str]:
    headings = []
    for line in text.splitlines():
        match = re.match(r"^===\s+(.+?)\s*$", line)
        if match is not None:
            headings.append(match.group(1))
    return headings


def test_every_tutorial_has_the_seven_contract_headings_in_order():
    for name in TUTORIAL_FILES:
        text = (CHAPTER / name).read_text(encoding="utf-8")
        headings = _top_level_section_headings(text)
        positions = []
        for required in PAGE_CONTRACT_HEADINGS:
            matches = [index for index, heading in enumerate(headings) if heading.startswith(required)]
            assert matches, f"{name}: missing required section heading {required!r}"
            positions.append(matches[0])
        assert positions == sorted(positions), f"{name}: contract headings out of order: {headings}"


def test_every_tutorial_names_the_wfc_version_in_what_was_verified_and_disclaims_live():
    for name in TUTORIAL_FILES:
        text = (CHAPTER / name).read_text(encoding="utf-8")
        match = re.search(r"=== What was verified\n(.*?)(?=\n=== |\Z)", text, re.DOTALL)
        assert match is not None, f"{name}: no 'What was verified' section"
        section = match.group(1)
        assert "0.1.0b1" in section, f"{name}: 'What was verified' does not name the WFC version"
        disclaims_live = bool(
            re.search(r"\bnot\b[^.]*\bexecuted\b", section)
            or "no live run was performed" in section.lower()
        )
        assert disclaims_live, f"{name}: 'What was verified' does not distinguish what was not executed/performed"


# ==========================================================================
# AC-9: every anchor that existed before this change still exists.
# ==========================================================================

def test_ac9_every_pre_existing_quickstart_anchor_still_exists():
    text = (CHAPTER / "01_quickstart.adoc").read_text(encoding="utf-8")
    anchors = set(_ANCHOR_BLOCK_RE.findall(text))
    missing = [anchor for anchor in PRE_EXISTING_QUICKSTART_ANCHORS if anchor not in anchors]
    assert not missing, f"01_quickstart.adoc lost anchors: {missing}"


def test_ac9_every_pre_existing_first_run_example_anchor_still_exists():
    text = (CHAPTER / "02_first_run_example.adoc").read_text(encoding="utf-8")
    anchors = set(_ANCHOR_BLOCK_RE.findall(text))
    missing = [anchor for anchor in PRE_EXISTING_FIRST_RUN_ANCHORS if anchor not in anchors]
    assert not missing, f"02_first_run_example.adoc lost anchors: {missing}"

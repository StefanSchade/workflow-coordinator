"""The consumer operator-notes convention (WI-WFC-BETA2-SK-001, slice SK).

The shipped Agent Skill is stack-agnostic; consumer-specific operating
knowledge lives in a file the consumer repository commits at
``docs/operator-notes.md``. There is deliberately no runtime mechanism: no
configuration key, no CLI, no projection into a Commission. What has to stay
true is therefore documentary, and it is what these guards check -- that the
convention is stated where a consumer will find it (the Operator Manual's
consumer chapter) and where the operating agent will (the skill), that its
two boundaries are stated with it, and that this repository's own notes file
exists as the convention's first user and carries the repository-specific
facts the generic skill no longer does.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parent.parent.parent
NOTES = REPOSITORY / "docs/operator-notes.md"
QUICKSTART = REPOSITORY / "docs/operator_manual/ch09_consumer_quickstart/01_quickstart.adoc"
LEVEL1 = REPOSITORY / "docs/operator_manual/ch10_level1_orchestration/01_operating_pattern.adoc"
SKILL_ROOT = REPOSITORY / "src/workflow_coordinator/agent_skill/wfc"

NOTES_PATH = "docs/operator-notes.md"
ANCHOR = "quickstart-operator-notes"

# The repository-specific facts slice SK moved out of the generic skill
# files. Each one has to still exist somewhere -- in this repository's own
# operator notes, which is the point of the convention.
MOVED_FACTS = (
    "WFC_TEST_INPUTS",
    "WFC_TEST_PROFILE",
    "scripts/wfc_prepare_checkout_env.sh",
    "tests/unit/test_self_development_consumer_config.py",
    "egg-info",
    "PYTHONPATH",
    "make lint",
    "make test",
    "make docs-render",
)

ABSOLUTE_HOME = re.compile(r"(?:^|\s|\()/(?:home|Users|root)/")
MARKDOWN_LINK = re.compile(r"\]\(([^)\s]+)\)")


def _notes_text() -> str:
    return NOTES.read_text(encoding="utf-8")


def test_this_repository_commits_its_own_operator_notes():
    assert NOTES.is_file(), f"{NOTES_PATH} is the convention's first user and must exist"
    text = _notes_text()
    assert text.startswith("# "), "operator notes are a plain Markdown document"
    assert len(text.split()) > 200, "a notes file with nothing in it is not a first user"


@pytest.mark.parametrize("fact", MOVED_FACTS)
def test_repository_specific_facts_have_a_home_in_the_notes(fact):
    assert fact in _notes_text(), (
        f"{fact!r} left the generic skill in slice SK and must be stated in {NOTES_PATH}"
    )


def test_notes_state_their_own_standing_and_are_not_the_guidance_file():
    normalized = re.sub(r"\s+", " ", _notes_text())
    assert "no authority" in normalized.lower()
    assert "guidance.path" in normalized, (
        "the notes must say they are not the Commission's guidance file"
    )
    assert "committed history" in normalized.lower()


def test_notes_carry_no_absolute_home_directory_path():
    offenders = [line for line in _notes_text().splitlines() if ABSOLUTE_HOME.search(line)]
    assert not offenders, f"committed artifacts carry no absolute home paths: {offenders}"


def test_every_relative_link_in_the_notes_resolves():
    missing = []
    for target in MARKDOWN_LINK.findall(_notes_text()):
        if "://" in target or target.startswith("#"):
            continue
        path = (NOTES.parent / target.split("#", 1)[0]).resolve()
        if not path.exists():
            missing.append(target)
    assert not missing, f"{NOTES_PATH} links nothing: {missing}"


def test_consumer_chapter_documents_the_convention_with_both_boundaries():
    text = QUICKSTART.read_text(encoding="utf-8")
    assert f"[[{ANCHOR}]]" in text, "the consumer chapter needs its own anchor for the convention"
    assert NOTES_PATH in text
    normalized = re.sub(r"\s+", " ", text)
    assert "no authority" in normalized.lower(), "the no-authority boundary must be stated"
    assert "guidance.path" in normalized, "the guidance-file distinction must be stated"
    assert "committed" in normalized


def test_level1_chapter_points_at_the_convention():
    text = LEVEL1.read_text(encoding="utf-8")
    assert NOTES_PATH in text
    assert ANCHOR in text, "ch10 should link the convention rather than restate it"


def test_the_shipped_skill_tells_the_operator_when_to_read_the_notes():
    entry = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    setup = (SKILL_ROOT / "references/setup.md").read_text(encoding="utf-8")
    assert NOTES_PATH in entry and NOTES_PATH in setup
    # WI-WFC-BETA2-SKC2-001: UPPER_CASE placeholder, so the fenced command is
    # executable rather than two shell redirections.
    assert f"git show BASE_REF:{NOTES_PATH}" in entry

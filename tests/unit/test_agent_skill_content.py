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

# Level-1 autonomy contract phrases (WI-WFC-LEVEL1-AUTONOMY-002): the
# delegated-mandate principle and the preserved boundaries must both survive
# in the skill text, not just in the Operator Manual it distils.
DELEGATED_AUTHORITY_PHRASE = "Delegated task authority persists across execution attempts"
DIAGNOSTIC_UNCERTAINTY_PHRASE = "is not, by itself, a reason to ask"
PRESERVED_BOUNDARY_PHRASES = (
    "a push, tag, publication or configuration",
    "Runtime promotion",
    "widening scope, authority, program or Hop budget",
    "discarding a branch, worktree, stash or preserved ref",
)
STOP_TABLE_HEADER = "| Classification(s) | You may, on your own | A human decides |"

# One subcommand invoker per real CLI dispatch branch (``frontdoor/cli.py``),
# so the drift guard runs each subcommand's own real argparse parser.
SUBCOMMAND_MAIN = {
    "init": lambda argv: cli_main(["init", *argv]),
    "plan": lambda argv: cli_main(["plan", *argv]),
    "prepare-checkout": lambda argv: cli_main(["prepare-checkout", *argv]),
    "request": lambda argv: cli_main(["request", *argv]),
    "archive": lambda argv: cli_main(["archive", *argv]),
    "audit": lambda argv: cli_main(["audit", *argv]),
    "report": lambda argv: cli_main(["report", *argv]),
    "run": lambda argv: cli_main(["run", *argv]),
    "resume": lambda argv: cli_main(["resume", *argv]),
    # O8, WI-WFC-BETA2-O8-001: the new `frontdoor/cli.py` dispatch branch, so
    # the drift guard below keeps covering every real subcommand the skill
    # may name -- and checks this one's own flags against its real argparse.
    "extend-budget": lambda argv: cli_main(["extend-budget", *argv]),
    "status": lambda argv: cli_main(["status", *argv]),
    "skill": lambda argv: cli_main(["skill", *argv]),
}

GITHUB_BLOB_RE = re.compile(
    r"^https://github\.com/StefanSchade/workflow-coordinator/blob/v([^/]+)/([^#]+)(?:#.*)?$"
)
MARKDOWN_LINK_RE = re.compile(r"\]\((https://[^)\s]+)\)")


def _normalized(text: str) -> str:
    """Collapse whitespace so a phrase can be matched across a line wrap."""
    return re.sub(r"\s+", " ", text)


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
    # The guard's real intent is that the authority rules are reached before
    # anything operational: "## Authorization" must begin early, and the
    # first three sections must still end well inside the file.
    # WI-WFC-BETA2-SK-001 raised the second bound from 80 to 95 lines,
    # deliberately: section 1 now carries the Level-0/Level-1 separation,
    # about twelve lines of load-bearing text it did not have before.
    # Compressing the Authorization rules to stay under 80 would have been
    # the wrong trade.
    assert headings[1][0] <= 45
    fourth_heading_line_number = headings[3][0]
    assert fourth_heading_line_number - 1 <= 95


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


def _shell_fence_commands(text: str):
    """Logical command lines from the ``shell``/``bash``/``sh`` fences only.

    ``text`` fences are excluded on purpose: that is how the skill marks a
    fragment, which may legitimately not run as written.
    """
    buffer: list[str] = []
    for lang, line in _fenced_shell_lines(text):
        if lang not in {"shell", "bash", "sh"}:
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


@pytest.mark.parametrize("path", ALL_SKILL_FILES, ids=lambda p: p.name)
def test_no_shell_fence_carries_a_placeholder_the_shell_would_redirect(path):
    """WI-WFC-BETA2-SKC2-001: a block the skill presents as runnable must run
    as written once its placeholders are filled in -- a stronger property than
    being valid shell, which `<angle-bracket>` placeholders also are (`bash -n`
    exits 0 for them) while the shell reads them as redirections. The
    convention is an UPPER_CASE placeholder word, matching the installed CLI's
    own metavariables. `tests/architecture/test_runnable_shell_examples.py`
    holds the canonical checker and its self-tests, and covers the Operator
    Manual too; this guard keeps the shipped skill honest on its own.
    """
    offenders = []
    for command in _shell_fence_commands(path.read_text(encoding="utf-8")):
        if not command.strip():
            continue
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        try:
            tokens = list(lexer)
        except ValueError:
            offenders.append(f"unbalanced quoting: {command!r}")
            continue
        for index in range(len(tokens) - 2):
            if tokens[index] == "<" and tokens[index + 2] == ">":
                offenders.append(f"<{tokens[index + 1]}> in {command!r}")
    assert not offenders, (
        f"{path.name}: use an UPPER_CASE placeholder, or mark the block as a "
        f"fragment with a 'text' fence: {offenders}"
    )


def test_skill_states_its_placeholder_convention_once():
    """The mechanical guard above enforces a convention; the convention has to
    be written where the operator reads it, and only once."""
    entry = _normalized(SKILL_FILE.read_text(encoding="utf-8"))
    assert "UPPER_CASE" in entry
    assert "run as written" in entry, (
        "the convention must say a runnable block is more than valid shell"
    )


def test_delegated_authority_persists_phrase_present():
    """WI-WFC-LEVEL1-AUTONOMY-002: a technical failure or stopped attempt does
    not end Level 1's delegated task authority; this must survive verbatim in
    the skill, not only in the Operator Manual it distils."""
    text = SKILL_FILE.read_text(encoding="utf-8")
    assert DELEGATED_AUTHORITY_PHRASE in text


def test_diagnostic_uncertainty_not_reason_to_ask_phrase_present():
    text = SKILL_FILE.read_text(encoding="utf-8")
    assert DIAGNOSTIC_UNCERTAINTY_PHRASE in text


def test_skill_preserves_every_always_ask_boundary():
    """Every boundary the delegated mandate never covers must still be
    stated and binding after the autonomy rewrite, not merely implied."""
    normalized = _normalized(SKILL_FILE.read_text(encoding="utf-8"))
    for phrase in PRESERVED_BOUNDARY_PHRASES:
        assert phrase in normalized, f"SKILL.md no longer states the boundary: {phrase!r}"


def test_skill_qualifies_discard_boundary_and_states_own_worktree_cleanup_freedom():
    """WI-WFC-LEVEL1-AUTONOMY-002 F001: the worktree/branch/stash/ref discard
    boundary must be qualified to the operator's own, not-yet-dispositioned
    work (never a blanket 'always ask'), and the skill must say, as plainly
    as Operator Manual ch10 does, that removing its own already-integrated-
    or-dispositioned worktree and scratch needs no further human decision."""
    normalized = _normalized(SKILL_FILE.read_text(encoding="utf-8"))
    assert (
        "discarding a branch, worktree, stash or preserved ref this session "
        "did not itself create for this task" in normalized
    )
    assert "Own-worktree cleanup" in normalized
    assert (
        "remove the worktree and scratch directory this session itself "
        "created for it" in normalized
    )


def test_skill_never_section_still_withholds_finding_and_review_authority():
    text = SKILL_FILE.read_text(encoding="utf-8")
    assert "Open or resolve a Finding." in text
    assert "Commit inside a Run's checkout." in text
    assert "Edit `.wfc/` state or Evidence by hand." in text
    assert "Relaunch `wfc run` in a checkout that already has a lifecycle." in text


def test_stops_and_evidence_has_stop_classification_table():
    """The distinction between technical continuation (inside the delegated
    mandate) and a stop that needs a new human decision must be explicit and
    operational, not just asserted in prose."""
    text = (SKILL_ROOT / "references/stops-and-evidence.md").read_text(encoding="utf-8")
    assert STOP_TABLE_HEADER in text
    for classification in (
        "refused_probe",
        "awaiting_human_budget",
        "reconcile_required",
        "uncertain_launch",
        "awaiting_human",
    ):
        assert f"`{classification}`" in text, f"stop table missing a row for {classification!r}"
    normalized = _normalized(text)
    assert "only the independent reviewer" in normalized.lower() or (
        "resolved by" in normalized and "independent reviewer" in normalized
    ), "the Finding boundary must survive next to the autonomy table"


def test_stops_and_evidence_keeps_salvage_and_discard_as_human_judgment():
    normalized = _normalized(
        (SKILL_ROOT / "references/stops-and-evidence.md").read_text(encoding="utf-8")
    )
    assert "human judgment" in normalized
    assert "nothing is discarded without that judgment" in normalized


# ==========================================================================
# WI-WFC-BETA2-SK-001 (slice SK): the shipped skill is the *generic* operator
# guide. Consumer-specific operating knowledge lives in the consumer's own
# committed operator notes, through the documented file convention. These
# guards pin structure and location, never a particular wording.
# ==========================================================================

OPERATOR_NOTES_PATH = "docs/operator-notes.md"

# Tokens that only ever describe one consumer's stack or one repository's
# layout. Each one lived in a generic skill file before slice SK and now
# belongs in that repository's own operator notes. `python3`/`pip` stay
# admitted: the Runtime itself is a Python package, which is a fact about
# WFC, not about any repository it operates on.
CONSUMER_SPECIFIC_TOKENS = (
    "WFC_TEST_INPUTS",
    "WFC_TEST_PROFILE",
    "egg-info",
    "PYTHONPATH",
    "wfc_prepare_checkout_env",
    "test_self_development_consumer_config",
    "Makefile",
    "pytest",
    "ruff",
    "make lint",
    "make test",
    "make check",
    "make format-check",
    "make docs-render",
    "make check-docs",
)

# Every authority rule the skill must keep stating, each as the smallest set
# of substrings that carries its *meaning*. They are matched against the
# whole skill (entry file plus references) so that moving a rule between
# files is free and losing it is not.
AUTHORITY_RULES = {
    "--yes asserts, never verifies": ("`--yes`", "asserts that authorization"),
    "delegation covers later launches": (
        "Existing delegation",
        "covers every launch inside it",
        "across technical execution attempts",
    ),
    "no self-initiated widening": (
        "widening scope, authority, program or Hop budget",
        "binding a role to a model the human did not approve",
    ),
    "no self-initiated budget extension": (
        "an agent never extends a budget on its own",
        "maintainer's explicit instruction in the current session",
    ),
    "no promotion, publication or reinstallation": (
        "a push, tag, publication or configuration change",
        "Runtime promotion or any change of the installed WFC version",
        "installing or reinstalling this skill",
    ),
    "stop disposition stays with the human": (
        "judging a stop's disposition",
        "nothing is discarded without that judgment",
    ),
    "Finding authority is the reviewer's alone": (
        "Open or resolve a Finding.",
        "only ever opened or resolved by the independent reviewer",
    ),
    "the ceiling bounds but does not approve": (
        "bounds a Commission; it does not approve one",
    ),
    "nothing under .git/ is edited by hand": (
        "Remove or edit anything under `.git/` by hand",
    ),
    "operator notes confer no authority": (
        "advice only, never authority",
        "No authority, ever.",
    ),
}


def _all_skill_text() -> str:
    return _normalized(
        "\n".join(path.read_text(encoding="utf-8") for path in ALL_SKILL_FILES)
    )


def _skill_section(heading: str) -> str:
    lines = SKILL_FILE.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith(heading))
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


@pytest.mark.parametrize("path", ALL_SKILL_FILES, ids=lambda p: p.name)
def test_generic_skill_names_no_consumer_stack_or_repository_layout(path):
    text = path.read_text(encoding="utf-8")
    present = [token for token in CONSUMER_SPECIFIC_TOKENS if token in text]
    assert not present, (
        f"{path.name} names consumer-specific facts {present}; they belong in that "
        f"repository's own {OPERATOR_NOTES_PATH}"
    )


@pytest.mark.parametrize("rule", sorted(AUTHORITY_RULES), ids=lambda name: name)
def test_every_authority_rule_is_still_stated_somewhere_in_the_skill(rule):
    combined = _all_skill_text()
    missing = [part for part in AUTHORITY_RULES[rule] if part not in combined]
    assert not missing, f"authority rule {rule!r} lost its statement(s): {missing}"


def test_operator_notes_convention_is_documented_with_its_boundaries():
    """The convention is a contract between the skill and a consumer
    repository: the path, where it is read from, that it is optional, that it
    grants nothing, and that it is not the Worker's guidance file."""
    entry = SKILL_FILE.read_text(encoding="utf-8")
    setup = (SKILL_ROOT / "references/setup.md").read_text(encoding="utf-8")
    assert OPERATOR_NOTES_PATH in entry
    assert OPERATOR_NOTES_PATH in setup
    # Read from committed history, never from a working tree or a candidate.
    # WI-WFC-BETA2-SKC2-001: the placeholder is an UPPER_CASE word, because
    # `git show <base-ref>:...` inside a shell fence is read by the shell as
    # two redirections (see tests/architecture/test_runnable_shell_examples.py).
    assert f"git show BASE_REF:{OPERATOR_NOTES_PATH}" in entry
    assert f"git show BASE_REF:{OPERATOR_NOTES_PATH}" in setup
    normalized_setup = _normalized(setup)
    assert "never a working tree and never a Run's candidate" in normalized_setup
    # Optional, no authority, and distinct from the Commission's guidance file.
    assert "nothing is missing" in _normalized(entry)
    assert "No authority, ever." in setup
    assert "guidance.path" in setup


def test_skill_states_level_0_and_level_1_and_sequential_multi_run_operation():
    """Level 0 executes one Commission per Run; sequencing several Runs is
    Level 1's own ordinary work. The old text called chaining Commissions a
    case where WFC "does not fit", which is a Level-0 fact stated as a
    Level-1 limit."""
    what_this_is = _normalized(_skill_section("## What this is"))
    assert "Level 0 is WFC" in what_this_is
    assert "exactly one Commission per Run" in what_this_is
    assert "Level 1 is you" in what_this_is
    fit = _normalized(_skill_section("## When WFC fits"))
    assert "in sequence" in fit
    assert "chaining several Commissions" not in fit
    # The authority boundary around parallel Runs is not widened by any of it.
    assert "need a delegation that covers running them in parallel" in fit


def test_loop_section_says_which_reference_and_the_notes_check_belong_where():
    """Progressive disclosure: the references are links an agent follows, so
    the entry file must say at which step to open each one."""
    loop = _skill_section("## The loop")
    for reference in REFERENCE_FILES:
        assert f"references/{reference.name}" in loop, (
            f"the loop never says when to open references/{reference.name}"
        )
    assert OPERATOR_NOTES_PATH in loop


def test_every_tag_pinned_skill_file_is_covered_by_the_release_bump_surface():
    """A skill file carrying a tag-pinned manual link must be named in
    `scripts/wfc_release_prepare.py`'s enumerated surface, or the release
    step would leave it pinned to the previous version."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "wfc_release_prepare_for_skill_test", ROOT / "scripts/wfc_release_prepare.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    covered = {
        site.path
        for site in module.surface(VERSION, "9.9.9")
        if site.rule == "tag_pinned_url"
    }
    for path in ALL_SKILL_FILES:
        text = path.read_text(encoding="utf-8")
        if "github.com/StefanSchade/workflow-coordinator/blob/" not in text:
            continue
        relative = path.relative_to(ROOT).as_posix()
        assert relative in covered, (
            f"{relative} carries a tag-pinned link but is not in the release "
            f"bump surface"
        )

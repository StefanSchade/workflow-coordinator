"""Shell examples presented as runnable must not redirect (slice SKC2).

The defect this guards against is specific, and is *not* a syntax error. A
placeholder written in angle brackets inside a block a reader is meant to
paste -- ``git show <base-ref>:docs/operator-notes.md`` -- is perfectly valid
shell: ``bash -n`` exits ``0`` for it. What the shell actually does with it is
read ``<base-ref`` as an input redirection and ``>:docs/...`` as an output
redirection, so a literal execution fails with "No such file" or silently
creates a stray file. A syntax check alone therefore cannot see it; the check
has to look at the *tokens*.

So the primary guard lexes every runnable block with ``shlex`` in its
punctuation-aware mode, which splits an unquoted ``<`` or ``>`` off as its own
token while leaving a quoted string a single token, and rejects the
``<`` WORD ``>`` triple that is the angle-bracket placeholder's signature.
``bash -n`` runs as a second, deliberately weaker line: it cannot catch this
defect class, but it catches an unbalanced quote or heredoc.

Being quote-aware is what makes the guard usable rather than merely strict.
``printf 'TMPDIR=%s\\n' "${TMPDIR:-<unset>}"`` is correct, executable shell in
which ``<unset>`` is a literal fallback *value*, not a placeholder; a
text-level search for ``<...>`` would reject it. The convention the shipped
skill states (``SKILL.md``, *Where exact facts come from*) is an ``UPPER_CASE``
placeholder word, quoted where the real value contains spaces -- which is also
the installed CLI's own metavariable convention (``--output PATH``,
``--evidence-argv TOKEN``). Nothing here pins any particular wording.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parent.parent.parent
SKILL_ROOT = REPOSITORY / "src/workflow_coordinator/agent_skill/wfc"
OPERATOR_NOTES = REPOSITORY / "docs/operator-notes.md"
OPERATOR_MANUAL = REPOSITORY / "docs/operator_manual"

# Languages that present a block as something to run. A ``text`` fence is
# deliberately excluded: that is how the skill marks a fragment or a usage
# synopsis, which is an admitted way to carry a placeholder these rules
# cannot express.
RUNNABLE_LANGUAGES = ("shell", "bash", "sh")

_ADOC_BLOCK_RE = re.compile(
    r"^\[source, ?(shell|bash|sh)\]\n-{4,}\n(.*?)\n-{4,}$",
    re.DOTALL | re.MULTILINE,
)


def _markdown_blocks(text: str):
    """Yield ``(line_number, body)`` for each runnable fenced block."""
    language = None
    start = 0
    body: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if language is None:
                language = stripped[3:].strip().lower()
                start = number
                body = []
            else:
                if language in RUNNABLE_LANGUAGES:
                    yield start, "\n".join(body)
                language = None
            continue
        if language is not None:
            body.append(line)


def _asciidoc_blocks(text: str):
    for match in _ADOC_BLOCK_RE.finditer(text):
        line_number = text[: match.start()].count("\n") + 1
        yield line_number, match.group(2)


def _runnable_blocks(path: Path):
    text = path.read_text(encoding="utf-8")
    reader = _asciidoc_blocks if path.suffix == ".adoc" else _markdown_blocks
    yield from reader(text)


def _logical_commands(body: str):
    """Join backslash-continued lines into one logical command line each."""
    buffer: list[str] = []
    for raw in body.splitlines():
        line = raw.rstrip()
        if line.endswith("\\"):
            buffer.append(line[:-1])
            continue
        buffer.append(line)
        yield " ".join(buffer)
        buffer = []
    if buffer:
        yield " ".join(buffer)


def _substitution_bodies(command: str):
    """Yield the inside of each ``$( ... )``, which the shell parses as its own
    command list -- so an unquoted ``<`` in there redirects even when the whole
    substitution sits inside double quotes, which is how the one instance of
    this defect in ``ch05_self_development`` was written. ``shlex`` treats the
    quoted substitution as a single opaque token, so it has to be looked into
    separately. ``$(( ... ))`` is arithmetic, not a command list, and is
    skipped."""
    index = 0
    while True:
        start = command.find("$(", index)
        if start == -1:
            return
        if command.startswith("$((", start):
            index = start + 3
            continue
        depth = 0
        for position in range(start + 1, len(command)):
            character = command[position]
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    yield command[start + 2 : position]
                    index = position + 1
                    break
        else:
            return


def redirection_placeholders(body: str) -> list[str]:
    """Angle-bracket placeholders a shell would read as redirections.

    Quoted text is one token and is never reported; a real redirection
    (``> file``, ``2>&1``, ``<<'EOF'``) does not produce the reported triple.
    """
    found: list[str] = []
    pending = [command for command in _logical_commands(body) if command.strip()]
    while pending:
        command = pending.pop()
        pending.extend(_substitution_bodies(command))
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        try:
            tokens = list(lexer)
        except ValueError:
            # An unbalanced quote is the ``bash -n`` guard's business.
            continue
        for index in range(len(tokens) - 2):
            if tokens[index] == "<" and tokens[index + 2] == ">":
                found.append(f"<{tokens[index + 1]}>")
    return found


def _documents() -> list[Path]:
    documents = sorted(SKILL_ROOT.rglob("*.md"))
    documents.append(OPERATOR_NOTES)
    documents.extend(sorted(OPERATOR_MANUAL.rglob("*.adoc")))
    return documents


def _identifier(path: Path) -> str:
    return path.relative_to(REPOSITORY).as_posix()


DOCUMENTS = _documents()


@pytest.mark.parametrize("path", DOCUMENTS, ids=_identifier)
def test_no_runnable_block_carries_an_angle_bracket_placeholder(path):
    offenders = [
        (line_number, placeholders)
        for line_number, body in _runnable_blocks(path)
        if (placeholders := redirection_placeholders(body))
    ]
    assert not offenders, (
        f"{_identifier(path)}: a block presented as runnable carries a placeholder "
        f"the shell reads as a redirection {offenders}. Use an UPPER_CASE "
        f"placeholder word, or mark the block as a fragment with a 'text' fence."
    )


@pytest.mark.parametrize("path", DOCUMENTS, ids=_identifier)
def test_every_runnable_block_is_valid_shell_syntax(path):
    """The weaker second line: a syntax check cannot see the defect above, but
    it does catch an unbalanced quote or an unterminated heredoc."""
    if shutil.which("bash") is None:  # pragma: no cover - environment-dependent
        pytest.skip("bash is not available in this environment")
    failures = []
    for line_number, body in _runnable_blocks(path):
        result = subprocess.run(
            ["bash", "-n"], input=body, text=True, capture_output=True, timeout=30
        )
        if result.returncode != 0:
            failures.append((line_number, result.stderr.strip()))
    assert not failures, f"{_identifier(path)}: invalid shell syntax {failures}"


def test_the_shipped_skill_states_the_placeholder_convention():
    """A mechanical guard is worth little if the convention it enforces is not
    written down where the operator reads it."""
    entry = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    normalized = re.sub(r"\s+", " ", entry)
    assert "UPPER_CASE" in normalized, "the placeholder convention must name its form"
    assert "run as written" in normalized, (
        "the convention must distinguish a runnable block from merely valid shell"
    )


def test_at_least_one_runnable_block_was_actually_found_in_each_surface():
    """Guards against a silent pass if a fence style or block syntax changes."""
    for root in (SKILL_ROOT / "SKILL.md", OPERATOR_NOTES, OPERATOR_MANUAL):
        paths = [root] if root.is_file() else sorted(root.rglob("*.adoc"))
        assert any(list(_runnable_blocks(path)) for path in paths), (
            f"no runnable block found under {root}; the block parser may be stale"
        )


# --------------------------------------------------------------------------
# Guard self-tests: the checker has to fail on the real historical defect and
# pass the correct forms, including the ones a text-level search gets wrong.
# --------------------------------------------------------------------------

KNOWN_BAD = (
    "git show <base-ref>:docs/operator-notes.md",
    "wfc report <evidence-repo>/archive/unclassified/<attempt-id>/audit.json",
    "wfc plan task.md --evidence-argv <lint-argv-token>",
    'M="$(ls "$EVIDENCE"/archive/*/<attempt>/manifest.json)"',
)

KNOWN_GOOD = (
    "git show BASE_REF:docs/operator-notes.md",
    "wfc report EVIDENCE_REPO/archive/unclassified/ATTEMPT_ID/audit.json",
    # A quoted placeholder is not a redirection, whatever it contains.
    'wfc extend-budget --authority "<the maintainer\'s instruction>"',
    # A literal fallback value, not a placeholder: correct, executable shell.
    "printf 'TMPDIR=%s\\n' \"${TMPDIR:-<unset>}\"",
    # Real redirections must not be mistaken for placeholders.
    "nohup wfc run --yes > ../scratch/run.log 2>&1 &",
    "cat > .wfc/governance/repository-policy.md <<'EOF'",
)


@pytest.mark.parametrize("command", KNOWN_BAD)
def test_guard_self_test_rejects_the_historical_defect(command):
    assert redirection_placeholders(command), f"guard no longer catches {command!r}"


@pytest.mark.parametrize("command", KNOWN_GOOD)
def test_guard_self_test_accepts_the_corrected_and_the_legitimate_forms(command):
    assert not redirection_placeholders(command), (
        f"guard falsely rejects the correct form {command!r}"
    )


def test_guard_self_test_reads_a_fenced_block_and_a_source_block():
    markdown = "text\n\n```shell\ngit show <base-ref>:file\n```\n"
    assert [body for _, body in _markdown_blocks(markdown)] == [
        "git show <base-ref>:file"
    ]
    asciidoc = "text\n\n[source,shell]\n----\ngit show <base-ref>:file\n----\n"
    assert [body for _, body in _asciidoc_blocks(asciidoc)] == [
        "git show <base-ref>:file"
    ]
    # A ``text`` fence is how a fragment is marked, and is not checked.
    assert not list(_markdown_blocks("```text\nwfc request task.md ... --output P\n```\n"))

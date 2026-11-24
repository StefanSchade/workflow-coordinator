"""The quote guard for `docs/operator_manual/ch09_consumer_quickstart/`
(`docs/backlog/FTR-WFC-CONSUMER-TUTORIALS.adoc`, "Quote guard"): a page quotes
a kit file only in a listing block immediately preceded by a `// kit: <path>`
comment line, and that block's content must equal the named kit file byte for
byte, apart from one trailing newline.
"""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent.parent
CHAPTER = REPOSITORY / "docs" / "operator_manual" / "ch09_consumer_quickstart"
KIT_DIR = REPOSITORY / "examples" / "consumer-demo"

_KIT_COMMENT_RE = re.compile(r"^// kit: (\S+)\s*$")
_DELIMITER_RE = re.compile(r"^-{4,}$")


def _chapter_sources() -> list[Path]:
    return sorted(CHAPTER.glob("*.adoc"))


def find_kit_quotes(text: str) -> list[tuple[int, str, str]]:
    """Return (comment_line_number, kit_path, block_content) for every
    `// kit: <path>` comment followed by the next delimited listing block."""

    lines = text.splitlines()
    quotes = []
    index = 0
    while index < len(lines):
        match = _KIT_COMMENT_RE.match(lines[index])
        if match is None:
            index += 1
            continue
        comment_line_number = index + 1
        kit_path = match.group(1)
        scan = index + 1
        while scan < len(lines) and not _DELIMITER_RE.match(lines[scan].strip()):
            scan += 1
        if scan >= len(lines):
            raise AssertionError(f"line {comment_line_number}: `// kit: {kit_path}` has no following listing block")
        scan += 1
        content_start = scan
        while scan < len(lines) and not _DELIMITER_RE.match(lines[scan].strip()):
            scan += 1
        if scan >= len(lines):
            raise AssertionError(f"line {comment_line_number}: `// kit: {kit_path}` listing block is never closed")
        content = "\n".join(lines[content_start:scan])
        quotes.append((comment_line_number, kit_path, content))
        index = scan + 1
    return quotes


def _normalize(text: str) -> str:
    return text[:-1] if text.endswith("\n") else text


def test_at_least_six_kit_quotes_covering_three_tasks_and_three_requests():
    task_docs = set()
    requests = set()
    total = 0
    for source in _chapter_sources():
        for _, kit_path, _ in find_kit_quotes(source.read_text(encoding="utf-8")):
            total += 1
            if kit_path.endswith(".task.md"):
                task_docs.add(kit_path)
            elif kit_path.endswith(".request.yaml"):
                requests.add(kit_path)
    assert total >= 6, f"expected at least six kit quotes, found {total}"
    assert len(task_docs) >= 3, f"expected at least three quoted task documents, found {task_docs}"
    assert len(requests) >= 3, f"expected at least three quoted request templates, found {requests}"


def test_every_kit_quote_matches_the_named_kit_file_byte_for_byte():
    failures = []
    for source in _chapter_sources():
        text = source.read_text(encoding="utf-8")
        for line_number, kit_path, block_content in find_kit_quotes(text):
            kit_file = KIT_DIR / kit_path
            if not kit_file.is_file():
                failures.append(
                    f"{source.relative_to(REPOSITORY)}:{line_number}: "
                    f"kit file does not exist: {kit_path}"
                )
                continue
            expected = _normalize(kit_file.read_text(encoding="utf-8"))
            actual = _normalize(block_content)
            if actual != expected:
                failures.append(
                    f"{source.relative_to(REPOSITORY)}:{line_number}: "
                    f"quoted block for {kit_path} does not match the kit file byte for byte"
                )
    assert not failures, "\n".join(failures)


def test_guard_self_test_fails_on_a_missing_kit_file(tmp_path):
    leaf = tmp_path / "leaf_with_a_missing_kit_file.adoc"
    leaf.write_text(
        "\n".join(
            [
                "// kit: requests/does-not-exist.task.md",
                "[source,markdown]",
                "----",
                "anything",
                "----",
                "",
            ]
        ),
        encoding="utf-8",
    )
    quotes = find_kit_quotes(leaf.read_text(encoding="utf-8"))
    assert quotes == [(1, "requests/does-not-exist.task.md", "anything")]
    assert not (KIT_DIR / "requests" / "does-not-exist.task.md").exists()


def test_guard_self_test_fails_on_a_diverging_block(tmp_path):
    real_task = KIT_DIR / "requests" / "t1-hello-page.task.md"
    leaf = tmp_path / "leaf_with_a_diverging_block.adoc"
    leaf.write_text(
        "\n".join(
            [
                "// kit: requests/t1-hello-page.task.md",
                "[source,markdown]",
                "----",
                "this does not match the kit file",
                "----",
                "",
            ]
        ),
        encoding="utf-8",
    )
    ((_, kit_path, block_content),) = find_kit_quotes(leaf.read_text(encoding="utf-8"))
    assert kit_path == "requests/t1-hello-page.task.md"
    assert _normalize(block_content) != _normalize(real_task.read_text(encoding="utf-8"))

"""Every `<<target>>` cross-reference in a standalone-read Operator Manual leaf
must resolve to an anchor defined in that same file; a reference to another
file must use `link:`, which works both standalone and in the assembly
(docs/ai_info/doc_conventions.adoc, "AsciiDoc mechanics").
"""

import re
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent.parent
OPERATOR_MANUAL = REPOSITORY / "docs" / "operator_manual"
ASSEMBLED_MANUAL = OPERATOR_MANUAL / "operator_manual.adoc"

_ANCHOR_BLOCK = re.compile(r"\[\[([A-Za-z0-9_\-]+)(?:,[^\]]*)?\]\]")
_ANCHOR_SHORTHAND = re.compile(r"\[#([A-Za-z0-9_\-]+)(?=[.,\]])")
_XREF = re.compile(r"<<([^,>]+)(?:,[^>]*)?>>")
_FENCE_DASH = re.compile(r"^-{4,}$")
_FENCE_DOT = re.compile(r"^\.{4,}$")


def _unfenced_lines(text):
    """Yield (line_number, line) for lines outside delimited ``----``/``....`` blocks."""
    fence = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if fence is None:
            if _FENCE_DASH.match(stripped):
                fence = "-"
                continue
            if _FENCE_DOT.match(stripped):
                fence = "."
                continue
            yield line_number, line
        else:
            if fence == "-" and _FENCE_DASH.match(stripped):
                fence = None
            elif fence == "." and _FENCE_DOT.match(stripped):
                fence = None
            continue


def find_unresolved_cross_refs(text):
    """Return a list of (line_number, target) for `<<target>>` refs with no
    same-file `[[target]]`/`[#target]` anchor, outside delimited blocks."""
    lines = list(_unfenced_lines(text))
    anchors = set()
    for _, line in lines:
        anchors.update(_ANCHOR_BLOCK.findall(line))
        anchors.update(_ANCHOR_SHORTHAND.findall(line))
    unresolved = []
    for line_number, line in lines:
        for match in _XREF.finditer(line):
            target = match.group(1)
            if target not in anchors:
                unresolved.append((line_number, target))
    return unresolved


def _operator_manual_sources():
    return sorted(
        path
        for path in OPERATOR_MANUAL.rglob("*.adoc")
        if path != ASSEMBLED_MANUAL
    )


def test_operator_manual_sources_cover_the_known_leaves():
    sources = _operator_manual_sources()
    assert sources, "no Operator Manual sources found"
    assert ASSEMBLED_MANUAL not in sources


def test_every_operator_manual_leaf_resolves_its_cross_references():
    failures = []
    for path in _operator_manual_sources():
        text = path.read_text(encoding="utf-8")
        for line_number, target in find_unresolved_cross_refs(text):
            failures.append(
                f"{path.relative_to(REPOSITORY)}:{line_number}: "
                f"<<{target}>> is not a same-file anchor"
            )
    assert not failures, "\n".join(failures)


def test_guard_self_test_fails_on_a_cross_file_reference(tmp_path):
    leaf = tmp_path / "leaf_with_cross_file_reference.adoc"
    leaf.write_text(
        "\n".join(
            [
                "= Synthetic leaf",
                "",
                "[[local-anchor]]",
                "== Local section",
                "",
                "This refers to <<local-anchor>>, which is fine, and to",
                "<<some-other-file-anchor>>, which is not defined here.",
                "",
                "----",
                "<<ignored-inside-a-code-block>>",
                "----",
                "",
            ]
        ),
        encoding="utf-8",
    )
    unresolved = find_unresolved_cross_refs(leaf.read_text(encoding="utf-8"))
    assert unresolved == [(7, "some-other-file-anchor")]

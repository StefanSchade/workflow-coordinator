import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

spec = importlib.util.spec_from_file_location(
    "wfc_doc_catalog", Path(__file__).resolve().parents[2] / "scripts/wfc_doc_catalog.py"
)
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    (tmp_path / "docs/sub").mkdir(parents=True)
    (tmp_path / "docs/sub/WI-EXAMPLE-123.adoc").write_text(
        "ifndef::assembled[]\n= Sample WI-TITLE-1\nendif::[]\n"
        ":base: ..\n\nWorkItem:: WI-HEADER-2\nHop:: H002\nCandidate:: 0123456789abcdef0123456789abcdef01234567\nStatus:: old claim\n\n"
        "Body mentions WI-BODY-9. link:{base}/other.md[label] "
        "xref:{missing}/bad.adoc[x] link:missing.adoc[x]\n\n== First section\n",
        encoding="utf-8",
    )
    (tmp_path / "docs/other.md").write_text(
        "# Markdown\n\n## Details\n[back](sub/WI-EXAMPLE-123.adoc#x)\n", encoding="utf-8"
    )
    (tmp_path / "docs/empty.adoc").write_text("plain text\n", encoding="utf-8")
    monkeypatch.setattr(catalog, "ROOT", tmp_path)
    return tmp_path


def test_projection_boundaries_and_links(tree):
    records, unresolved = catalog.project(tree)
    assert [r["path"] for r in records] == sorted(r["path"] for r in records)
    doc = records[2]
    assert doc["title"] == "Sample WI-TITLE-1"
    assert doc["section_title"] == "First section"
    assert doc["ids"] == [
        "0123456789abcdef0123456789abcdef01234567",
        "H002",
        "WI-EXAMPLE-123",
        "WI-HEADER-2",
        "WI-TITLE-1",
    ]
    assert doc["source_declarations"] == {
        "WorkItem": ["WI-HEADER-2"],
        "Hop": ["H002"],
        "Candidate": ["0123456789abcdef0123456789abcdef01234567"],
        "Status": ["old claim"],
    }
    assert doc["links"] == ["docs/other.md", "docs/sub/missing.adoc"]
    assert len(unresolved) == 2
    assert records[0]["title"] is None and records[0]["section_title"] is None
    assert records[1]["section_title"] == "Details"
    assert catalog.encode(records) == catalog.encode(catalog.project(tree)[0])


def test_unsupported_definition_labels_end_supported_declarations(tree, capsys):
    doc = tree / "docs/review.adoc"
    doc.write_text(
        "= Review\n\n"
        "WorkItem:: WI-SUPPORTED-1\n"
        "  continued WI-WRAPPED-2\n"
        "Run / Hop:: RUN-UNSUPPORTED-3 / H999\n"
        "Reviewed candidate:: 83ec2b10966392af281dcd22b2cc8a25109d4880\n"
        "Reviewed implementation commits::\n"
        "WI-UNSUPPORTED-4\n"
        "Status:: old\n"
        "  continued value\n"
        "Review phase:: WI-UNSUPPORTED-5\n\n"
        "Body mentions WI-BODY-6.\n",
        encoding="utf-8",
    )
    records, _ = catalog.project(tree)
    review = next(record for record in records if record["path"] == "docs/review.adoc")
    assert review["source_declarations"] == {
        "WorkItem": ["WI-SUPPORTED-1\n  continued WI-WRAPPED-2"],
        "Status": ["old\n  continued value"],
    }
    assert review["ids"] == ["WI-SUPPORTED-1", "WI-WRAPPED-2"]
    for identifier in (
        "RUN-UNSUPPORTED-3",
        "H999",
        "83ec2b10966392af281dcd22b2cc8a25109d4880",
        "WI-UNSUPPORTED-4",
        "WI-UNSUPPORTED-5",
        "WI-BODY-6",
    ):
        assert catalog.main(["query", "--live", "--id", identifier]) == 0
        assert capsys.readouterr().out == ""


def test_atomic_noop_and_failure(tree):
    output = tree / "build/catalog.jsonl"
    assert catalog.generate(output, b"old")
    stat = output.stat()
    assert not catalog.generate(output, b"old")
    assert (output.stat().st_ino, output.stat().st_mtime_ns) == (stat.st_ino, stat.st_mtime_ns)
    with patch.object(catalog.os, "replace", side_effect=OSError("simulated")):
        with pytest.raises(OSError):
            catalog.generate(output, b"new")
    assert output.read_bytes() == b"old"
    assert list(output.parent.iterdir()) == [output]
    assert catalog.generate(output, b"new")
    assert output.read_bytes() == b"new"
    with pytest.raises(ValueError):
        catalog.encode([{"x": "a" * catalog.LIMIT}])
    assert output.read_bytes() == b"new"


def test_missing_stale_live_and_check(tree, capsys):
    assert catalog.main(["query", "--id", "WI-HEADER-2"]) == 2
    assert "generate" in capsys.readouterr().err
    assert catalog.main(["generate"]) == 0
    output = tree / catalog.OUTPUT
    old = output.read_bytes()
    (tree / "docs/other.md").write_text(
        "# New title\n\n## New section\n[link](empty.adoc)\n", encoding="utf-8"
    )
    assert catalog.main(["query", "--title", "New title"]) == 0
    assert "New title" not in capsys.readouterr().out
    assert catalog.main(["query", "--live", "--title", "New title"]) == 0
    assert "New title" in capsys.readouterr().out
    assert output.read_bytes() == old
    assert catalog.main(["check"]) == 1
    assert "changed: docs/other.md" in capsys.readouterr().out
    (tree / "docs/other.md").rename(tree / "docs/renamed.md")
    assert catalog.main(["check"]) == 1
    out = capsys.readouterr().out
    assert "added: docs/renamed.md" in out and "removed: docs/other.md" in out
    assert output.read_bytes() == old


def test_duplicate_links_malformed_and_memory_report(tree, capsys):
    doc = tree / "docs/empty.adoc"
    doc.write_text(
        "= Title\n\n== Section\nlink:other.md[x] link:other.md[y] "
        "[bad](http://[broken) link:{unknown}/x[x]\n",
        encoding="utf-8",
    )
    memory = tree / "project_memory/canonical"
    memory.mkdir(parents=True)
    (memory / "records.json").write_text(
        '[{"source_path":"docs/other.md"},{"source_path":"src/code.py"}]', encoding="utf-8"
    )
    records, unresolved = catalog.project(tree)
    assert records[0]["links"] == ["docs/other.md"]
    assert {x[2] for x in unresolved} >= {"malformed target", "unresolved attribute"}
    catalog.diagnostics(tree, records, unresolved)
    out = capsys.readouterr().out
    assert "Project Memory source_path not in catalog: src/code.py" in out
    assert "Project Memory source_path not in catalog: docs/other.md" not in out

import importlib.util
import json
import os
import subprocess
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


# ---------------------------------------------------------------------------
# provenance: synthetic Git fixtures; no test reads the real repository history
# ---------------------------------------------------------------------------

RUN = "RUN-" + "ab" * 16
WI_ONE = "WI-FIX-ONE-001"


def run_git(repo, *args, when=None):
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }
    if when:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
    done = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    )
    return done.stdout.strip()


def make_commit(repo, subject, files, day):
    for rel, text in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", subject, when=f"2026-01-{day:02d}T12:00:00+00:00")
    return run_git(repo, "rev-parse", "HEAD")


WORK_ITEM_YAML = (
    "schema_version: 2\nwork_item_id: WI-FIX-ONE-001\nscope:\n- src/x.py\n"
    "- docs/backlog/FTR-FIX.adoc\n- docs/backlog/_open.adoc\n"
    "profile_labels:\n- program_id=robust\n- program_revision=v1\n"
    "durable_artifacts:\n  broad_review:\n  - docs/reviews/one_review.adoc\n"
    "  focused_review:\n  - docs/reviews/gone_review.adoc\n"
)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    run_git(tmp_path, "init", "-q")
    shas = {}
    shas["seed"] = make_commit(
        tmp_path, "docs: seed", {"docs/plain.adoc": "plain text\n", "docs/FTR-FIX_x.adoc": "= x\n"}, 1
    )
    base = "governance/commissions/c-one/"
    shas["commission"] = make_commit(
        tmp_path,
        "chore: commission fixture",
        {
            base + "work-item.yaml": WORK_ITEM_YAML,
            base + "intent.yaml": "commission_id: c-one\n",
            "docs/backlog/FTR-FIX.adoc": "= FTR\n",
            "docs/backlog/_open.adoc": "= open\n",
        },
        2,
    )
    shas["h001"] = make_commit(tmp_path, f"feat: add thing {WI_ONE} H001", {"src/x.py": "1\n"}, 3)
    review = (
        f"= One review\n\nWorkItem:: {WI_ONE}\nRun:: {RUN}\nHop:: H002\n"
        f"Candidate:: {shas['h001']}\nVerdict:: accept\n\n== Finding F001\nbody\n"
    )
    shas["h002"] = make_commit(
        tmp_path, f"docs: {WI_ONE} H002 record review", {"docs/reviews/one_review.adoc": review}, 4
    )
    shas["h003"] = make_commit(tmp_path, f"fix: change [{WI_ONE} H003]", {"src/x.py": "2\n"}, 5)
    shas["h004"] = make_commit(tmp_path, f"fix({WI_ONE}/H004): scoped", {"src/x.py": "3\n"}, 6)
    shas["h005"] = make_commit(
        tmp_path, f"docs({WI_ONE}): record rereview H005", {"src/x.py": "4\n"}, 7
    )
    shas["only"] = make_commit(tmp_path, f"docs: mention {WI_ONE}", {"docs/note.adoc": "n\n"}, 8)
    shas["accept"] = make_commit(
        tmp_path,
        "docs: record fixture acceptance",
        {"docs/reviews/one_review.adoc": review + "\nAccepted.\n"},
        9,
    )
    shas["other"] = make_commit(
        tmp_path,
        "fix: WI-FIX-TWO-001 H001 touches review",
        {"docs/reviews/one_review.adoc": review + "\nX\n"},
        10,
    )
    shas["backlog"] = make_commit(
        tmp_path, "docs: record fixture promotion", {"docs/backlog/FTR-FIX.adoc": "= FTR\nupdated\n"}, 11
    )
    empty = tmp_path / "governance/commissions/c-empty"
    empty.mkdir(parents=True)
    (empty / "work-item.yaml").write_text(
        "schema_version: 2\nwork_item_id: WI-FIX-EMPTY-001\n", encoding="utf-8"
    )
    monkeypatch.setattr(catalog, "ROOT", tmp_path)
    return tmp_path, shas


def test_subject_forms_pinned_and_extra_forms_named():
    tokens = catalog.subject_tokens
    assert tokens("fix: allow curl WI-A-1 H001") == [
        {"work_item": "WI-A-1", "hop": "H001", "form": "trailing"}
    ]
    assert tokens("docs: WI-A-1 H002 record broad review")[0]["form"] == "mid_subject"
    assert tokens("docs(review): accept shim [WI-A-1 H002]") == [
        {"work_item": "WI-A-1", "hop": "H002", "form": "bracketed"}
    ]
    assert tokens("docs(WI-A-1/H004): x")[0]["form"] == "scoped"
    assert tokens("docs(WI-A-1,H004): x")[0]["form"] == "scoped"
    assert tokens("docs(WI-A-1): record rereview H005") == [
        {"work_item": "WI-A-1", "hop": "H005", "form": "split"}
    ]
    assert tokens("docs: commission WI-A-1") == [
        {"work_item": "WI-A-1", "hop": None, "form": "workitem_only"}
    ]
    assert tokens("docs: accept K1") == []
    # an identifier that merely extends another one is a different identifier
    assert [t["work_item"] for t in tokens("fix: WI-A-1-EXTRA H001 and WI-B-2 H002")] == [
        "WI-A-1-EXTRA",
        "WI-B-2",
    ]
    assert tokens("fix: xWI-A-1 H001") == []
    assert tokens("fix: WI-A-1 H0011") == [{"work_item": "WI-A-1", "hop": None, "form": "workitem_only"}]


def test_work_item_report_joins_and_unlinked(repo):
    root, shas = repo
    report = catalog.work_item_report(root, WI_ONE)
    assert sorted((c["form"], c["hop"]) for c in report["commits"]) == sorted(
        [
            ("workitem_only", None),
            ("split", "H005"),
            ("scoped", "H004"),
            ("bracketed", "H003"),
            ("mid_subject", "H002"),
            ("trailing", "H001"),
        ]
    )
    (commission,) = report["commission"]
    assert commission["commissioning_commit"]["sha"] == shas["commission"]
    assert commission["program"] == "robust"
    assert commission["scope"][0] == "src/x.py"
    # identity header and declared artifact both reach the review; the missing focused
    # review is reported, not invented
    (doc,) = report["documents"]
    assert doc["path"] == "docs/reviews/one_review.adoc"
    assert doc["relation"] == "identity (K1 ids) + declared durable artifact"
    assert report["declared_missing"] == [
        {"declared": "docs/reviews/gone_review.adoc", "same_filename_now_at": []}
    ]
    assert report["runs"] == {RUN: ["docs/reviews/one_review.adoc"]}
    candidate = next(a for a in report["associations"] if a["kind"] == "candidate")
    assert candidate["status"] == "found" and candidate["sha"] == shas["h001"]
    assert candidate["note"] == "subject carries this WorkItem"
    # untokened commits touching own paths are unlinked, other-WorkItem commits are not
    assert [c["sha"] for c in report["unlinked_touching"]] == [shas["accept"]]
    assert report["touching_other_workitem"] == 1
    assert [c["sha"] for c in report["shared_untokened_after_commissioning"]] == [shas["backlog"]]
    assert report["shared_records"] == ["docs/backlog/FTR-FIX.adoc"]
    text = catalog.render_work_item(report)
    for needle in ("trailing", "bracketed", "mid_subject", "Unlinked", shas["accept"][:10]):
        assert needle in text


def test_body_only_mentions_are_separate_from_subject_tokens(repo):
    root, shas = repo
    parsed = catalog.parse_commits(
        "a" * 40 + "\x1f2026-01-01T00:00:00+00:00\x1fchore: commission older\x1f"
        + f"{RUN} ({WI_ONE}) was commissioned.\n\x1e"
        + "b" * 40 + "\x1f2026-01-02T00:00:00+00:00\x1fdocs: subject " + f"{WI_ONE} H001\x1f\x1e"
    )
    found = catalog.body_mentions(parsed, WI_ONE)
    assert [(c["sha"], c["runs"]) for c in found] == [("a" * 40, [RUN])]
    assert catalog.body_mentions(parsed, "WI-FIX-ONE-0") == []
    # real git bodies are read too
    make_commit(root, "chore: older convention", {"docs/body.adoc": "b\n"}, 12)
    run_git(root, "commit", "-q", "--allow-empty", "-m", f"docs: no token\n\nBody names {WI_ONE}.",
            when="2026-01-13T12:00:00+00:00")
    report = catalog.work_item_report(root, WI_ONE)
    assert [c["subject"] for c in report["body_commits"]] == ["docs: no token"]
    assert "docs: no token" not in [c["subject"] for c in report["unlinked_touching"]]
    assert len(report["commits"]) == 6


def test_commission_without_commits_and_unknown_identifier(repo):
    root, _ = repo
    empty = catalog.work_item_report(root, "WI-FIX-EMPTY-001")
    (commission,) = empty["commission"]
    assert commission["dir"].endswith("c-empty") and commission["commissioning_commit"] is None
    assert empty["commits"] == [] and empty["documents"] == []
    assert "commissioning commit: not found" in catalog.render_work_item(empty)
    nothing = catalog.work_item_report(root, "WI-NOT-THERE-1")
    assert nothing["commission"] == [] and nothing["commits"] == []
    assert "no Commission directory carries this identifier" in catalog.render_work_item(nothing)


def test_candidate_resolution_states(repo):
    root, shas = repo
    commits = catalog.history(root)
    assert catalog.resolve_sha(shas["h001"][:9], commits)["status"] == "found"
    assert catalog.resolve_sha("f" * 40, commits)["status"] == "not_in_history"
    forged = [
        {"sha": "abc1234" + "0" * 33, "subject": "a"},
        {"sha": "abc1234" + "1" * 33, "subject": "b"},
    ]
    assert catalog.resolve_sha("abc1234", forged) == {"status": "ambiguous", "matches": 2}


def test_coverage_counts_and_limits(repo):
    root, _ = repo
    cov = catalog.coverage_report(root)
    assert cov["commission_dirs"] == 2 and cov["commits"] == 11
    assert cov["commits_with_token"] == 7
    assert cov["forms"]["trailing"] == 1 and cov["forms"]["bracketed"] == 1
    assert cov["carveout"] is None
    per = {p["work_item"]: p for p in cov["per_commission"]}
    assert per[WI_ONE]["commissioning_commit"] and not per[WI_ONE]["commissioning_token"]
    assert per[WI_ONE]["token_commits"] == 6 and per[WI_ONE]["review_docs"] == 1
    assert per[WI_ONE]["runs"] == 1 and per[WI_ONE]["candidates_resolved"] == 1
    assert not per["WI-FIX-EMPTY-001"]["commissioning_commit"]
    assert per["WI-FIX-EMPTY-001"]["token_commits"] == 0
    assert cov["subject_work_items_without_directory"] == 1  # WI-FIX-TWO-001
    text = catalog.render_coverage(cov)
    assert "not a present process defect" in text and "No global linkage" in text
    assert "Candidate commit:" in text and "filter-repo" in text


def snapshot(root):
    return {
        p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts
    }


def test_provenance_cli_is_read_only_and_json(repo, capsys):
    root, _ = repo
    before = (run_git(root, "status", "--porcelain"), run_git(root, "rev-parse", "HEAD"))
    files = snapshot(root)
    index = (root / ".git/index").read_bytes()
    assert catalog.main(["provenance", "--work-item", WI_ONE]) == 0
    assert "commissioning commit" in capsys.readouterr().out
    assert catalog.main(["provenance", "--coverage"]) == 0
    assert "Coverage" in capsys.readouterr().out
    assert catalog.main(["provenance", "--work-item", WI_ONE, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["work_item"] == WI_ONE
    assert catalog.main(["provenance", "--review", "docs/reviews/one_review.adoc"]) == 0
    assert "Derived leads only" in capsys.readouterr().out
    assert (run_git(root, "status", "--porcelain"), run_git(root, "rev-parse", "HEAD")) == before
    assert snapshot(root) == files
    assert (root / ".git/index").read_bytes() == index
    assert not (root / catalog.OUTPUT).exists()
    assert catalog.main(["provenance", "--review", "docs/missing.adoc"]) == 2
    assert "no such document" in capsys.readouterr().err


def test_review_leads_cited_paths_and_inbound_links(repo):
    root, shas = repo
    (root / "docs/reviews/other_review.adoc").write_text(
        "= Other\n\nSee `src/x.py:3`, `src/old/moved.py` and link:one_review.adoc[x].\n",
        encoding="utf-8",
    )
    leads = catalog.review_leads(root, "docs/reviews/one_review.adoc")
    assert leads["added_by"]["sha"] == shas["h002"]
    assert leads["linked_from"] == ["docs/reviews/other_review.adoc"]
    other = catalog.review_leads(root, "docs/reviews/other_review.adoc")
    assert other["added_by"] is None  # uncommitted: no add commit is invented
    assert other["cited_paths_present"] == ["src/x.py"]
    assert other["cited_paths_absent"] == ["src/old/moved.py"]
    assert other["later_commits"]["src/x.py"]["count"] == 4  # no add commit: all history
    assert "Derived leads only" in catalog.render_leads(other)


def test_generate_and_query_stay_git_independent(tree):
    with patch.object(catalog.subprocess, "run", side_effect=AssertionError("git must not run")):
        assert catalog.main(["generate"]) == 0
        assert catalog.main(["query", "--id", "WI-HEADER-2"]) == 0
        assert catalog.main(["query", "--live", "--id", "WI-HEADER-2"]) == 0
        assert catalog.main(["check"]) == 0


def test_git_failure_is_reported_not_guessed(tmp_path, monkeypatch, capsys):
    (tmp_path / "docs").mkdir()
    monkeypatch.setattr(catalog, "ROOT", tmp_path)
    assert catalog.main(["provenance", "--coverage"]) == 2
    assert "catalog: git" in capsys.readouterr().err


def test_review_record_shapes_and_unsupported_material():
    modern = catalog.review_record(
        f"= R\n\nWorkItem:: WI-A-1\nRun:: `{RUN}`, Hop `H002`\nCandidate:: `{'1' * 40}`\n"
        f"Reviewed range:: `{'2' * 40}..{'3' * 40}`\nVerdict:: changes_required\nVerdict:: accept\n"
        "Invocation:: INV-x\n"
    )
    assert modern["shape"] == "modern"
    assert modern["runs"] == [RUN] and modern["hops"] == ["H002"]
    assert modern["candidates"] == ["1" * 40] and modern["ranges"] == [f"{'2' * 40}..{'3' * 40}"]
    assert [v["verdict"] for v in modern["verdicts"]] == ["changes_required", "accept"]
    assert modern["unsupported_labels"] == {"Invocation": 1}

    outcome = catalog.review_record("= R\n\nOutcome:: `changes_required`.\nDisposition:: *OPEN.*\n")
    assert outcome["shape"] == "outcome-header"
    assert [v["verdict"] for v in outcome["verdicts"]] == ["changes_required"]
    assert outcome["unsupported_labels"] == {"Disposition (finding-level or free text)": 1}

    grade = catalog.review_record("= R\n\nVerdict:: *B -- PLAN ACCEPTED AFTER BOUNDED CORRECTION*\n")
    assert grade["shape"] == "grade" and grade["verdicts"][0]["verdict"] is None
    assert "not mapped" in grade["verdicts"][0]["note"]

    section = catalog.review_record(
        f"= R\n\nReview Hop identity:: Run `{RUN}`, Hop\n`H002`, invocation `INV-1`\n"
        f"Candidate received at entry:: `{'4' * 40}`\nCandidate range::\n`{'5' * 40}..{'6' * 40}`\n\n"
        "== Verdict\n\n*CHANGES REQUIRED; route `correct`.*\n\n"
        "=== B1 -- First\n=== B2 -- Second\n== Rereview H004 -- x\n=== Rereview verdict\n\n*ACCEPT.*\n"
    )
    assert section["shape"] == "verdict-section"
    assert section["runs"] == [RUN] and section["hops"] == ["H002"]
    assert section["candidates"] == ["4" * 40] and len(section["ranges"]) == 1
    assert [v["verdict"] for v in section["verdicts"]] == ["changes_required", "accept"]
    assert section["finding_labels"] == ["B1", "B2"]

    prose = catalog.review_record(
        f"= R\n\nWorkItem `WI-A-1`, Run\n`{RUN}`, Hop `H002`.\nCandidate `{'7' * 40}`, reviewed against\n"
        f"`{'8' * 40}`. Disposition: *accept*, no blocking findings.\n"
    )
    assert prose["shape"] == "prose-preamble" and prose["runs"] == [RUN]
    assert prose["verdicts"][0]["verdict"] == "accept"
    assert prose["ranges"] == [f"{'8' * 40}..{'7' * 40}"]

    nothing = catalog.review_record("plain text\n")
    assert nothing["shape"] == "none" and nothing["runs"] == [] and nothing["verdicts"] == []
    # a finding-level disposition heading is never a review verdict
    finding_level = catalog.review_record("= R\n\n=== Disposition\n\n*Closed.*\n")
    assert finding_level["verdicts"] == []


def test_finding_identities_only_from_headings_and_duplicates_flagged():
    record = catalog.review_record(
        "= R\n\n== Finding F001\n=== H006-R1 -- Long title\n=== FIND-01\n=== C1: x\n=== B1 -- a\n"
        "=== B1 -- again\n== Section 2\n== Checks and limits\n| C2 | in a table |\n"
    )
    assert record["finding_labels"] == ["F001", "H006-R1", "FIND-01", "C1", "B1", "B1"]
    assert record["duplicate_finding_labels"] == ["B1"]


def test_k1_identity_query_semantics_unchanged(tree, capsys):
    # provenance-only labels never become K1 identity ids
    (tree / "docs/rv.adoc").write_text(
        f"= R\n\nWorkItem:: WI-K1-1\nReviewed candidate:: {'a' * 40}\nVerdict:: accept\n"
        f"Review Hop identity:: Run RUN-{'c' * 32}\n",
        encoding="utf-8",
    )
    records, _ = catalog.project(tree)
    rv = next(r for r in records if r["path"] == "docs/rv.adoc")
    assert rv["ids"] == ["WI-K1-1"] and "Verdict" not in rv["source_declarations"]
    assert catalog.main(["query", "--live", "--id", f"RUN-{'c' * 32}"]) == 0
    assert capsys.readouterr().out == ""

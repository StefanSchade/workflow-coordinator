"""``wfc report --repository`` (WI-WFC-BETA2-O5-001, D1 Option B's companion
report-side change): resolve one archived Attempt from the CONSUMER
repository's own Consumer Config via ``frontdoor.config.resolve_evidence``,
leaving the existing positional ``source`` form unchanged.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.fixtures.bp6 import archive_builder as ab
from workflow_coordinator.frontdoor import audit as au
from workflow_coordinator.frontdoor.cli import main as cli_main


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *args), text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "consumer-repo"
    repo.mkdir()
    subprocess.run(("git", "init", "-q", str(repo)), check=True)
    git(repo, "config", "user.email", "consumer@example.invalid")
    git(repo, "config", "user.name", "Consumer test")
    (repo / "src").mkdir()
    (repo / "src" / "app.txt").write_text("hello\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: seed disposable consumer repository")
    return repo


@pytest.fixture
def evidence_dir(tmp_path: Path) -> Path:
    return tmp_path / "evidence-repo"


def _init(repository: Path, evidence_dir: Path) -> None:
    code = cli_main([
        "init", "--repository", str(repository), "--scope-ceiling", "src",
        "--evidence-repository", str(evidence_dir),
    ])
    assert code == 0


def _archive_one_attempt(evidence_dir: Path, *, attempt_id: str) -> Path:
    attempt_dir = ab.build_attempt(evidence_dir / "archive" / "unclassified", attempt_id=attempt_id)
    au.write_audit(attempt_dir)
    return attempt_dir / "audit.json"


def _run(args: list[str]) -> tuple[int, str, str]:
    import io
    import contextlib

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli_main(args)
    return code, out.getvalue(), err.getvalue()


class TestPositionalFormUnchanged:
    def test_positional_source_still_reads_an_attempt_directory_directly(self, tmp_path: Path):
        attempt_dir = ab.build_attempt(tmp_path)
        code, out, err = _run(["report", str(attempt_dir)])
        assert code == 0, err
        assert "WFC RUN" in out

    def test_positional_and_repository_together_are_refused_as_ambiguous(
        self, repository: Path, evidence_dir: Path, tmp_path: Path,
    ):
        _init(repository, evidence_dir)
        attempt_audit = _archive_one_attempt(evidence_dir, attempt_id="attempt-both")
        code, out, err = _run(["report", str(attempt_audit), "--repository", str(repository)])
        assert code != 0
        assert out == ""
        assert "ambiguous" in err


class TestRepositoryFormResolvesTheArchivedAttempt:
    def test_repository_resolves_the_sole_archived_attempt(self, repository: Path, evidence_dir: Path):
        _init(repository, evidence_dir)
        _archive_one_attempt(evidence_dir, attempt_id="attempt-sole")

        code, out, err = _run(["report", "--repository", str(repository)])
        assert code == 0, err
        assert "WFC RUN" in out

    def test_repository_resolution_matches_resolving_the_same_audit_json_directly(
        self, repository: Path, evidence_dir: Path,
    ):
        _init(repository, evidence_dir)
        attempt_audit = _archive_one_attempt(evidence_dir, attempt_id="attempt-match")

        via_repository = _run(["report", "--repository", str(repository), "--json"])
        via_source = _run(["report", str(attempt_audit), "--json"])
        assert via_repository[0] == via_source[0] == 0
        assert via_repository[1] == via_source[1]

    def test_repository_names_the_consumer_repository_not_the_evidence_repository(
        self, repository: Path, evidence_dir: Path,
    ):
        """Passing the Evidence repository itself as ``--repository`` must
        not accidentally "work": it has no Consumer Config of its own."""

        _init(repository, evidence_dir)
        _archive_one_attempt(evidence_dir, attempt_id="attempt-wrong-repo")

        code, out, err = _run(["report", "--repository", str(evidence_dir)])
        assert code != 0
        assert out == ""
        assert "wfc report" in err


class TestRepositoryFormRefusals:
    def test_unresolvable_evidence_location_when_nothing_has_been_archived_yet(
        self, repository: Path, evidence_dir: Path,
    ):
        _init(repository, evidence_dir)
        code, out, err = _run(["report", "--repository", str(repository)])
        assert code != 0
        assert out == ""
        assert "unresolvable Evidence location" in err

    def test_unresolvable_evidence_location_when_no_evidence_repository_exists(self, repository: Path):
        code = cli_main([
            "init", "--repository", str(repository), "--scope-ceiling", "src", "--no-evidence-repository",
        ])
        assert code == 0
        code, out, err = _run(["report", "--repository", str(repository)])
        assert code != 0
        assert out == ""
        assert "unresolvable Evidence location" in err

    def test_ambiguous_input_when_more_than_one_attempt_is_archived(self, repository: Path, evidence_dir: Path):
        _init(repository, evidence_dir)
        _archive_one_attempt(evidence_dir, attempt_id="attempt-one")
        _archive_one_attempt(evidence_dir, attempt_id="attempt-two")

        code, out, err = _run(["report", "--repository", str(repository)])
        assert code != 0
        assert out == ""
        assert "ambiguous" in err
        assert "attempt-one" in err
        assert "attempt-two" in err

    def test_neither_source_nor_repository_is_refused(self, repository: Path):
        code, out, err = _run(["report"])
        assert code != 0
        assert out == ""
        assert "SOURCE or --repository is required" in err

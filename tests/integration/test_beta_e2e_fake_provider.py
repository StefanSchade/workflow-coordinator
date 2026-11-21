"""BP9: permanent fake-provider end-to-end Beta acceptance test.

This is the automated, fake-provider part of BP9 only. It is NOT live Beta
provider qualification: every provider call here is answered by the
scripted, protocol-compliant fixture at ``tests/fixtures/bp9/
scripted_provider.py`` (same wire contract as ``tests/fixtures/bp4/
fake_provider.py``, extended to answer a different, pre-scripted structured
result on each of several real engine Hops). No conclusion about any real
supported adapter's behavior may be drawn from a pass here; that is the
maintainer-owned installed-wheel/live-adapter qualification and the Claude
record reconciliation named by the BP9 backlog entry, which this module does
not attempt.

The whole scenario runs on a disposable, from-scratch, non-Python consumer
repository (no ``pyproject.toml``, no ``.py`` file, a shell evidence command
at ``checks/check.sh``) built fresh under ``tmp_path``, with its own
disposable Evidence repository, a throwaway ``HOME``/``XDG_STATE_HOME``, and
no network use.

What is real and what is a seam, by scenario group:
  (a) happy path -- the real ``wfc`` dispatcher
      (``workflow_coordinator.frontdoor.cli:main``) is driven as an actual
      *subprocess*, twice over: once as the outer CLI (``init``/``plan``/
      ``run --yes``/``status``/``audit`` (called twice -- a bare call that
      is refused fail-closed, then a ``--lifecycle-json`` call that
      reproduces the already-archived ``audit.json`` byte for byte) /
      ``report``) and, transparently, again as ``frontdoor.lifecycle``'s
      own inner bare-flag engine re-entry -- both via the in-repository
      entry-point shim ``tests/fixtures/bp9/wfc_entry.py`` (the same
      ``cli:main`` body ``pyproject.toml``'s installed console script
      points at, runnable without a pip install). This is the one scenario
      that exercises the real, unattended ``--yes`` path end-to-end as a
      black box.
  (b)-(e) correction/review, stops, archive retry, deliberate failures --
      driven in-process through ``frontdoor.lifecycle.run_lifecycle``/
      ``resume_lifecycle`` (as ``tests/unit/test_postrun_lifecycle.py``
      does), which still launches the real engine as a real subprocess for
      every Hop; only the *outer* CLI argument-parsing layer is bypassed
      where it adds no further assurance over what (a) already covers.

Every scenario drives the real reducer/engine/archive code; nothing here
adds or touches any product mechanism.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from workflow_coordinator.frontdoor import archive as A
from workflow_coordinator.frontdoor import lifecycle as L
from workflow_coordinator.frontdoor import postrun as PR
from workflow_coordinator.frontdoor.cli import main as cli_main

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "bp9"
WFC_ENTRY = FIXTURES / "wfc_entry.py"
SCRIPTED_PROVIDER = FIXTURES / "scripted_provider.py"

_BOTH_ROLE_OVERRIDES = {
    "worker": {"adapter": "codex_cli", "model": "fixture-model", "reasoning": "medium"},
    "independent_reviewer": {"adapter": "codex_cli", "model": "fixture-model", "reasoning": "medium"},
}
_WORKER_ONLY_OVERRIDES = {"worker": _BOTH_ROLE_OVERRIDES["worker"]}


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *args), text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.fixture(autouse=True)
def _restore_engine_command():
    original = L._ENGINE_COMMAND
    yield
    L._ENGINE_COMMAND = original


@pytest.fixture(autouse=True)
def _in_process_engine():
    """Every in-process scenario re-enters the real engine via the
    in-repository shim rather than requiring a pip install."""

    L._ENGINE_COMMAND = (sys.executable, str(WFC_ENTRY))


@pytest.fixture
def throwaway_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    return home


def _wfc_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Put a literal ``wfc`` on ``PATH`` wrapping the in-repository entry shim.

    ``frontdoor.lifecycle``'s legacy bare-flag engine re-entry resolves the
    real installed ``wfc`` console script by name (``shutil.which("wfc")``)
    *inside whatever process launched it* -- including a genuinely separate
    ``wfc run`` subprocess, which cannot see this test's own in-process
    ``L._ENGINE_COMMAND`` override. A tiny wrapper script is the one way to
    make that resolution succeed without an actual pip install.
    """

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    wrapper = bin_dir / "wfc"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable!r} {str(WFC_ENTRY)!r} \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def _seed_repository(tmp_path: Path, *, name: str = "consumer-repo") -> Path:
    """A fresh, disposable, non-Python Git repository: no pyproject.toml, no
    ``.py`` file anywhere, a plain shell evidence command."""

    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(("git", "init", "-q", str(repo)), check=True)
    git(repo, "config", "user.email", "consumer@example.invalid")
    git(repo, "config", "user.name", "Consumer test")
    (repo / "GUIDANCE.md").write_text("Keep changes small and reviewed.\n", encoding="utf-8")
    (repo / "checks").mkdir()
    check = repo / "checks" / "check.sh"
    check.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    check.chmod(0o755)
    (repo / "src").mkdir()
    (repo / "src" / "app.txt").write_text("hello\n", encoding="utf-8")
    (repo / "docs" / "reviews").mkdir(parents=True)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: seed disposable non-Python consumer repository")
    assert not list(repo.rglob("*.py")) and not (repo / "pyproject.toml").exists()
    return repo


def _init_and_commit_config(repo: Path, evidence_path: Path, *, scopes: tuple[str, ...] = ("src", "docs")) -> None:
    args = ["init", "--repository", str(repo), "--evidence-repository", str(evidence_path)]
    for scope in scopes:
        args += ["--scope-ceiling", scope]
    assert cli_main(args) == 0
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: commit Consumer Config")


def _simple_request(repo: Path, path: Path, *, commission_id: str, max_autonomous_hops: int = 1) -> Path:
    data = {
        "schema_version": 1, "commission_id": commission_id, "work_item_id": f"WI-{commission_id.upper()}",
        "authority_source": "test authorizes this bounded lifecycle.",
        "program": {"id": "simple", "revision": "v1"},
        "task": {
            "title": "bp9 fake-provider smoke", "body": "exercise the BP9 end-to-end acceptance scenario",
            "acceptance": "task-check passes", "withheld_authority": "none beyond scope",
            "escalation_default": "stop on ambiguity",
        },
        "scope": ["src"], "personas": {"worker": "Bounded worker persona."},
        "evidence": {"argv": ["sh", "checks/check.sh"], "timeout_seconds": 60},
        "max_autonomous_hops": max_autonomous_hops, "durable_artifacts": {},
        "guidance": {"path": "GUIDANCE.md", "source_commit": git(repo, "rev-parse", "HEAD")},
    }
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return path


def _robust_request(repo: Path, path: Path, *, commission_id: str, max_autonomous_hops: int = 5) -> Path:
    data = {
        "schema_version": 1, "commission_id": commission_id, "work_item_id": f"WI-{commission_id.upper()}",
        "authority_source": "test authorizes this bounded lifecycle.",
        "program": {"id": "robust", "revision": "v1"},
        "task": {
            "title": "bp9 robust correction/review smoke", "body": "exercise the robust@v1 correction/review cycle",
            "acceptance": "task-check passes", "withheld_authority": "none beyond scope",
            "escalation_default": "stop on ambiguity",
        },
        "scope": ["src", "docs"],
        "personas": {"worker": "Bounded worker persona.", "independent_reviewer": "Independent reviewer persona."},
        "evidence": {"argv": ["sh", "checks/check.sh"], "timeout_seconds": 60},
        "max_autonomous_hops": max_autonomous_hops,
        "durable_artifacts": {"broad_review": ["docs/reviews/broad.md"], "focused_review": ["docs/reviews/focused.md"]},
        "guidance": {"path": "GUIDANCE.md", "source_commit": git(repo, "rev-parse", "HEAD")},
    }
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return path


def _write_script(tmp_path: Path, steps: list[dict], *, name: str = "script.json") -> Path:
    script_path = tmp_path / name
    script_path.write_text(json.dumps(steps), encoding="utf-8")
    cursor_path = script_path.with_name(script_path.name + ".cursor")
    cursor_path.unlink(missing_ok=True)
    return script_path


def _inputs(repo: Path, request_path: Path, *, overrides: dict, executable: Path = SCRIPTED_PROVIDER) -> L.RunInputs:
    return L.RunInputs(
        repository=repo, config_path=L.DEFAULT_CONFIG_PATH, request_path=request_path,
        target_dir=None, overrides=overrides, executable=str(executable),
        hop_timeout_seconds=60.0, confirm=lambda a, b: True,
    )


def _attempt_dir(evidence_path: Path, repo: Path, run_id: str, *, repository_name: str = "consumer-repo") -> Path:
    identity = PR._attempt_identity(repository_name, run_id)
    return evidence_path / "archive" / "unclassified" / identity


# ==========================================================================
# (a) Happy path: the real ``wfc`` dispatcher, driven as a subprocess, with
# ``init``/``plan``/``run --yes``/``status``/``audit``/``report`` and the
# automatic archive into a disposable Evidence repository.
# ==========================================================================

def test_happy_path_cli_init_plan_run_yes_status_audit_report_archives_automatically(
    tmp_path: Path, throwaway_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    # Kills a mutation that skips the automatic postrun archive on a
    # completed Run, or that reports any non-"completed" status as success
    # anywhere along the init/plan/run/status/audit/report chain, and also
    # a mutation that makes `wfc audit`'s create-only write silently
    # overwrite a differing existing audit.json instead of refusing (the
    # bare-call assertion below would stop failing to reproduce bytes).
    _wfc_on_path(tmp_path, monkeypatch)
    repo = _seed_repository(tmp_path)
    evidence_path = tmp_path / "evidence-repo"

    init = subprocess.run(
        [sys.executable, str(WFC_ENTRY), "init", "--repository", str(repo),
         "--scope-ceiling", "src", "--evidence-repository", str(evidence_path)],
        text=True, capture_output=True,
    )
    assert init.returncode == 0, init.stderr
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: commit Consumer Config")

    task_doc = tmp_path / "task.md"
    task_doc.write_text(
        "# BP9 happy path\n\n"
        "Exercise the fake-provider end-to-end acceptance scenario.\n\n"
        "## Acceptance\n\ntask-check passes\n\n"
        "## Withheld authority\n\nnone beyond scope\n\n"
        "## Default escalation\n\nstop on ambiguity\n",
        encoding="utf-8",
    )
    plan = subprocess.run(
        [sys.executable, str(WFC_ENTRY), "plan", str(task_doc), "--repository", str(repo),
         "--scope", "src", "--commission-id", "bp9-happy", "--work-item-id", "WI-BP9-HAPPY",
         "--authority-source", "test authorizes this bounded lifecycle.", "--program", "simple@v1",
         "--persona", "worker=Bounded worker persona.",
         "--evidence-argv", "sh", "--evidence-argv", "checks/check.sh", "--guidance-path", "GUIDANCE.md",
         "--execution-override", "worker.adapter=codex_cli", "--execution-override", "worker.model=fixture-model",
         "--execution-override", "worker.reasoning=medium"],
        text=True, capture_output=True,
    )
    assert plan.returncode == 0, plan.stderr
    assert "nothing was run or written by this preview" in plan.stdout
    assert not (repo / ".wfc" / "commissions").exists()  # preview never writes a Commission.

    request_path = _simple_request(repo, tmp_path / "request.yaml", commission_id="bp9-happy")
    script = [
        {"expect_phase": "implement", "write": {"src/app.txt": "hello from the happy path\n"},
         "result": {"schema_version": 1, "disposition": "completed", "requested_route": "accept",
                    "scope_changed": False, "requires_human": False, "escalation": None, "findings": [],
                    "claimed_commits": [], "evidence_refs": [], "summary": "implemented the happy path",
                    "commit_intent": {"paths": ["src/app.txt"], "type": "feat", "description": "greet happily"}}},
    ]
    script_path = _write_script(tmp_path, script)
    env = {**os.environ, "WFC_BP9_SCRIPT": str(script_path)}

    run = subprocess.run(
        [sys.executable, str(WFC_ENTRY), "run", "--repository", str(repo), "--request", str(request_path),
         "--executable", str(SCRIPTED_PROVIDER), "--execution-override", "worker.adapter=codex_cli",
         "--execution-override", "worker.model=fixture-model", "--execution-override", "worker.reasoning=medium",
         "--yes"],
        text=True, capture_output=True, env=env,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "CLASSIFICATION: completed" in run.stdout
    assert "WFC RUN" in run.stdout  # the consolidated postrun report was printed.
    assert "ARCHIVE FAILED" not in run.stdout and "ARCHIVE SKIPPED" not in run.stdout
    run_id = re.search(r"RUN ID: (\S+)", run.stdout).group(1)

    status = subprocess.run(
        [sys.executable, str(WFC_ENTRY), "status", "--repository", str(repo)],
        text=True, capture_output=True, env=env,
    )
    assert status.returncode == 0, status.stderr
    assert "CLASSIFICATION: completed" in status.stdout
    assert "ARCHIVE: result" in status.stdout

    attempt_dir = _attempt_dir(evidence_path, repo, run_id)
    audit_path = attempt_dir / "audit.json"
    assert audit_path.is_file()  # `wfc run`'s automatic postrun step already projected and wrote it.
    archived_audit_bytes = audit_path.read_bytes()

    # A bare `wfc audit` on the already-archived attempt is fail-closed: it
    # recomputes without the lifecycle facts `wfc run` had and so refuses to
    # overwrite the differing audit.json already there, rather than silently
    # replacing it.
    bare_audit = subprocess.run(
        [sys.executable, str(WFC_ENTRY), "audit", str(attempt_dir)], text=True, capture_output=True,
    )
    assert bare_audit.returncode == 2
    assert "refusing to overwrite existing audit.json with different content" in bare_audit.stderr
    assert audit_path.read_bytes() == archived_audit_bytes  # the refusal touched nothing.

    # Fed the same lifecycle facts `wfc run` used (read back from the
    # archived audit.json itself), `wfc audit` reproduces that document byte
    # for byte: the real `audit` command, not just the automatic projection.
    lifecycle_json_path = tmp_path / "lifecycle.json"
    lifecycle_json_path.write_text(
        json.dumps(json.loads(archived_audit_bytes)["lifecycle"]), encoding="utf-8",
    )
    audit = subprocess.run(
        [sys.executable, str(WFC_ENTRY), "audit", str(attempt_dir), "--lifecycle-json", str(lifecycle_json_path)],
        text=True, capture_output=True,
    )
    assert audit.returncode == 0, audit.stderr
    assert audit.stdout.strip() == f"AUDIT: {audit_path}"
    assert audit_path.read_bytes() == archived_audit_bytes  # byte-identical reproduction.

    # `wfc report` inspects that already-archived audit.json directly (the
    # realistic "inspect" path: nothing here recomputes or rewrites it).
    report = subprocess.run(
        [sys.executable, str(WFC_ENTRY), "report", str(audit_path)], text=True, capture_output=True,
    )
    assert report.returncode == 0, report.stderr
    assert "outcome     completed" in report.stdout
    assert "commits     " in report.stdout and "none" not in report.stdout.splitlines()[
        next(i for i, line in enumerate(report.stdout.splitlines()) if line.startswith("commits"))
    ]
    assert git(evidence_path, "status", "--porcelain=v1") == ""  # the archive commit left it clean.


# ==========================================================================
# (b) Correction/review cycle under robust@v1: a blocking review Finding,
# a correction Hop, a focused review that resolves it, and a renewed,
# independent broad review that actually completes -- Coordinator-owned
# commits throughout (the trusted-commit boundary, never the provider).
# ==========================================================================

def test_robust_v1_correction_and_review_cycle_resolves_finding_and_completes(
    tmp_path: Path, throwaway_home: Path,
):
    # Kills a mutation that breaks the Finding resolve operation (for
    # example miscategorizing the open-to-resolved state transition): the
    # Finding would stay open, and the Run would stop for human review one
    # Hop early instead of resolving it and reaching a genuine completion
    # (empirically confirmed: mutating reducer._finding_operation's
    # (OPEN, RESOLVED) transition value turns this test's "completed" into
    # "awaiting_human" at H004, never silently staying "completed").
    repo = _seed_repository(tmp_path)
    evidence_path = tmp_path / "evidence-repo"
    _init_and_commit_config(repo, evidence_path, scopes=("src", "docs"))
    request_path = _robust_request(repo, tmp_path / "request.yaml", commission_id="bp9-robust")

    script = [
        {"expect_phase": "implement", "write": {"src/app.txt": "hello v2\n"},
         "result": {"schema_version": 1, "disposition": "completed", "requested_route": "broad_review",
                    "scope_changed": False, "requires_human": False, "escalation": None, "findings": [],
                    "claimed_commits": [], "evidence_refs": [], "summary": "initial change",
                    "commit_intent": {"paths": ["src/app.txt"], "type": "feat", "description": "update app"}}},
        {"expect_phase": "broad_review", "write": {"docs/reviews/broad.md": "Found a problem.\n"},
         "result": {"schema_version": 1, "disposition": "changes_required", "requested_route": "correction",
                    "scope_changed": False, "requires_human": False, "escalation": None,
                    "findings": [{"finding_id": None, "invariant": "app.txt must greet properly",
                                   "blocking": True, "proposed_state": "open",
                                   "evidence_refs": ["docs/reviews/broad.md"],
                                   "successor_ref": None, "new_material_evidence": True}],
                    "claimed_commits": [], "evidence_refs": [], "summary": "found a blocking issue",
                    "commit_intent": {"paths": ["docs/reviews/broad.md"], "type": "docs", "description": "record broad review"}}},
        {"expect_phase": "correct", "write": {"src/app.txt": "hello v3 fixed\n"},
         "result": {"schema_version": 1, "disposition": "completed", "requested_route": "focused_review",
                    "scope_changed": False, "requires_human": False, "escalation": None, "findings": [],
                    "claimed_commits": [], "evidence_refs": [], "summary": "corrected the issue",
                    "commit_intent": {"paths": ["src/app.txt"], "type": "fix", "description": "address review finding"}}},
        {"expect_phase": "focused_review", "write": {"docs/reviews/focused.md": "Correction verified.\n"},
         "result": {"schema_version": 1, "disposition": "accept", "requested_route": "renew_broad_review",
                    "scope_changed": False, "requires_human": False, "escalation": None,
                    "findings": [{"finding_id": "__OPEN__", "proposed_state": "resolved",
                                   "evidence_refs": ["docs/reviews/focused.md"],
                                   "successor_ref": None, "new_material_evidence": False}],
                    "claimed_commits": [], "evidence_refs": [], "summary": "correction verified; renewing broad review",
                    "commit_intent": {"paths": ["docs/reviews/focused.md"], "type": "docs", "description": "record focused review"}}},
        {"expect_phase": "broad_review", "write": {"docs/reviews/broad.md": "Found a problem.\nRe-reviewed: accepted.\n"},
         "result": {"schema_version": 1, "disposition": "accept", "requested_route": "accept",
                    "scope_changed": False, "requires_human": False, "escalation": None, "findings": [],
                    "claimed_commits": [], "evidence_refs": [], "summary": "fresh review accepts",
                    "commit_intent": {"paths": ["docs/reviews/broad.md"], "type": "docs", "description": "record renewed broad review"}}},
    ]
    _write_script(tmp_path, script)
    os.environ["WFC_BP9_SCRIPT"] = str(tmp_path / "script.json")
    try:
        result = L.run_lifecycle(_inputs(repo, request_path, overrides=_BOTH_ROLE_OVERRIDES))
    finally:
        os.environ.pop("WFC_BP9_SCRIPT", None)

    assert result.classification == "completed"
    assert result.run_status == "completed"

    run_root = Path(result.checkout_state_root)
    run_doc = json.loads((run_root / "run.json").read_text(encoding="utf-8"))
    assert run_doc["hop_used"] == 5
    assert run_doc["anomalies"] == []
    findings = run_doc["findings"]
    assert len(findings) == 1
    assert findings[0]["state"] == "resolved"
    assert findings[0]["introduced_by_hop"] == "H002"
    assert findings[0]["changed_by_hop"] == "H004"  # resolved at focused_review, not at the renewed broad_review.

    attempt_dir = _attempt_dir(evidence_path, repo, result.run_id)
    audit = json.loads((attempt_dir / "audit.json").read_text(encoding="utf-8"))
    assert audit["findings"] == [{
        "finding_id": findings[0]["finding_id"], "state": "resolved", "blocking": True,
        "disposition": findings[0]["disposition"], "introduced_by_hop": "H002", "changed_by_hop": "H004",
    }]


# ==========================================================================
# (c) Stops: a Hop-ceiling stop and a human-required stop, each classified
# fail-closed and reported honestly -- never as completed.
# ==========================================================================

def test_hop_ceiling_stop_is_classified_awaiting_human_budget_never_completed(
    tmp_path: Path, throwaway_home: Path,
):
    # Kills a mutation that lets hop_used reach or exceed hop_limit on a
    # GOTO route without stopping for explicit human budget authorization
    # (e.g. one that silently raises the ceiling or ignores it).
    repo = _seed_repository(tmp_path)
    evidence_path = tmp_path / "evidence-repo"
    _init_and_commit_config(repo, evidence_path, scopes=("src", "docs"))
    request_path = _robust_request(repo, tmp_path / "request.yaml", commission_id="bp9-hopceiling", max_autonomous_hops=1)

    script = [
        {"expect_phase": "implement", "write": {"src/app.txt": "hello v2\n"},
         "result": {"schema_version": 1, "disposition": "completed", "requested_route": "broad_review",
                    "scope_changed": False, "requires_human": False, "escalation": None, "findings": [],
                    "claimed_commits": [], "evidence_refs": [], "summary": "wants to continue past the ceiling",
                    "commit_intent": {"paths": ["src/app.txt"], "type": "feat", "description": "update app"}}},
    ]
    _write_script(tmp_path, script)
    os.environ["WFC_BP9_SCRIPT"] = str(tmp_path / "script.json")
    try:
        result = L.run_lifecycle(_inputs(repo, request_path, overrides=_BOTH_ROLE_OVERRIDES))
    finally:
        os.environ.pop("WFC_BP9_SCRIPT", None)

    assert result.classification == "awaiting_human_budget"
    assert result.exit_code == L.EXIT_CODES["awaiting_human_budget"] == 11
    assert result.classification != "completed"


def test_human_required_stop_is_classified_awaiting_human_never_completed(
    tmp_path: Path, throwaway_home: Path,
):
    # Kills a mutation that ignores a worker's requires_human/escalation
    # fields and lets the Run proceed autonomously instead of stopping for
    # explicit human authority.
    repo = _seed_repository(tmp_path)
    evidence_path = tmp_path / "evidence-repo"
    _init_and_commit_config(repo, evidence_path, scopes=("src",))
    request_path = _simple_request(repo, tmp_path / "request.yaml", commission_id="bp9-human", max_autonomous_hops=5)

    script = [
        {"expect_phase": "implement",
         "result": {"schema_version": 1, "disposition": "blocked", "requested_route": "accept",
                    "scope_changed": False, "requires_human": True,
                    "escalation": {"kind": "scope", "question": "need a wider scope than authorized"},
                    "findings": [], "claimed_commits": [], "evidence_refs": [], "summary": "cannot proceed",
                    "commit_intent": None}},
    ]
    _write_script(tmp_path, script)
    os.environ["WFC_BP9_SCRIPT"] = str(tmp_path / "script.json")
    try:
        result = L.run_lifecycle(_inputs(repo, request_path, overrides=_WORKER_ONLY_OVERRIDES))
    finally:
        os.environ.pop("WFC_BP9_SCRIPT", None)

    assert result.classification == "awaiting_human"
    assert result.exit_code == L.EXIT_CODES["awaiting_human"] == 10
    assert result.classification != "completed"
    run_root = Path(result.checkout_state_root)
    run_doc = json.loads((run_root / "run.json").read_text(encoding="utf-8"))
    assert run_doc["semantic_stop"]["kind"] == "scope"


# ==========================================================================
# (d) Archive retry after an interrupted archive: identical bytes, no
# duplicate commit.
# ==========================================================================

def test_archive_retry_after_interrupted_archive_is_idempotent_no_duplicate_commit(
    tmp_path: Path, throwaway_home: Path,
):
    # Kills a mutation that re-archives (and re-commits) on retry instead of
    # recognizing byte-identical already-committed payload as a no-op.
    repo = _seed_repository(tmp_path)
    evidence_path = tmp_path / "evidence-repo"
    _init_and_commit_config(repo, evidence_path, scopes=("src",))
    request_path = _simple_request(repo, tmp_path / "request.yaml", commission_id="bp9-archive-retry")

    script = [
        {"expect_phase": "implement", "write": {"src/app.txt": "hello, archived\n"},
         "result": {"schema_version": 1, "disposition": "completed", "requested_route": "accept",
                    "scope_changed": False, "requires_human": False, "escalation": None, "findings": [],
                    "claimed_commits": [], "evidence_refs": [], "summary": "will be archived twice",
                    "commit_intent": {"paths": ["src/app.txt"], "type": "feat", "description": "archive retry smoke"}}},
    ]
    _write_script(tmp_path, script)

    checkout, checkout_dir, journal = L._prepare_checkout(repo)

    def authorize():
        return L._authorize(_inputs(repo, request_path, overrides=_WORKER_ONLY_OVERRIDES), checkout_dir, journal)

    os.environ["WFC_BP9_SCRIPT"] = str(tmp_path / "script.json")
    try:
        result = L._advance(repo, checkout_dir, journal, authorize=authorize)
    finally:
        os.environ.pop("WFC_BP9_SCRIPT", None)
    assert result.classification == "completed"

    classified = journal.latest(("classified",))
    config = PR._cached_config(checkout_dir)
    identity = PR._attempt_identity(config.repository_name, classified.data["run_id"])
    # Simulate the exact state an interruption right after the archive
    # commit (before the ``ext.archive.result`` record) would leave: commit
    # the archive directly, bypassing ``run_pending_postrun`` entirely.
    archived = A.archive_run(
        state_root=Path(classified.data["state_root"]), evidence_repo=evidence_path,
        raw_retention="local", lifecycle={"classification": "completed", "preservation": None},
        attempt_identity=identity,
    )
    assert archived.outcome == "archived"
    attempt_dir = evidence_path / "archive" / "unclassified" / identity
    audit_before = (attempt_dir / "audit.json").read_bytes()
    manifest_before = (attempt_dir / "manifest.json").read_bytes()
    commit_count_before = int(git(evidence_path, "rev-list", "--count", "HEAD"))
    assert journal.latest(("ext.archive.result", "ext.archive.refused", "ext.archive.skipped")) is None

    resumed = L.resume_lifecycle(repo)
    assert resumed.classification == "completed"
    assert resumed.exit_code == L.EXIT_CODES["completed"]

    commit_count_after = int(git(evidence_path, "rev-list", "--count", "HEAD"))
    assert commit_count_after == commit_count_before  # no duplicate commit.
    assert (attempt_dir / "audit.json").read_bytes() == audit_before  # identical bytes.
    assert (attempt_dir / "manifest.json").read_bytes() == manifest_before

    fact = journal.latest(("ext.archive.result",))
    assert fact is not None
    assert fact.data["outcome"] == "no_op"
    assert journal.count("ext.archive.result") == 1


# ==========================================================================
# (e) Deliberate failures: a failing evidence command and a provider that
# returns malformed output -- both refused or stopped, never accepted.
# ==========================================================================

def test_failing_evidence_command_refuses_before_any_provider_or_commit(
    tmp_path: Path, throwaway_home: Path,
):
    # Kills a mutation that launches the provider/engine (or commits the
    # Commission) despite the baseline evidence probe failing.
    repo = _seed_repository(tmp_path)
    (repo / "checks" / "check.sh").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: make the evidence command fail")
    evidence_path = tmp_path / "evidence-repo"
    _init_and_commit_config(repo, evidence_path, scopes=("src",))
    request_path = _simple_request(repo, tmp_path / "request.yaml", commission_id="bp9-evidence-fail")
    head_before = git(repo, "rev-parse", "HEAD")

    with pytest.raises(L.LifecycleError) as excinfo:
        L.run_lifecycle(_inputs(repo, request_path, overrides=_WORKER_ONLY_OVERRIDES))
    assert excinfo.value.classification == "refused_probe"
    assert L.EXIT_CODES["refused_probe"] == 23

    assert git(repo, "rev-parse", "HEAD") == head_before  # no Commission commit was ever made.
    assert not (evidence_path / "archive" / "unclassified").exists()  # no provider ran; nothing was archived.
    assert int(git(evidence_path, "rev-list", "--count", "HEAD")) == 1  # only `wfc init`'s own commit.


@pytest.mark.parametrize("malformed", ["not_json", "schema_violation"])
def test_malformed_provider_output_is_never_accepted(
    tmp_path: Path, throwaway_home: Path, malformed: str,
):
    # Kills a mutation that accepts an unvalidated or schema-invalid
    # candidate structured result as a completed Hop instead of refusing it
    # and leaving the Run in an uncertain, fail-closed, resumable state.
    repo = _seed_repository(tmp_path)
    evidence_path = tmp_path / "evidence-repo"
    _init_and_commit_config(repo, evidence_path, scopes=("src",))
    request_path = _simple_request(repo, tmp_path / "request.yaml", commission_id=f"bp9-malformed-{malformed}")

    script = [{"expect_phase": "implement", "malformed": malformed}]
    _write_script(tmp_path, script)
    os.environ["WFC_BP9_SCRIPT"] = str(tmp_path / "script.json")
    try:
        result = L.run_lifecycle(_inputs(repo, request_path, overrides=_WORKER_ONLY_OVERRIDES))
    finally:
        os.environ.pop("WFC_BP9_SCRIPT", None)

    assert result.classification != "completed"
    assert result.classification == "reconcile_required"
    assert result.exit_code == L.EXIT_CODES["reconcile_required"] == 12

    run_root = Path(result.checkout_state_root)
    run_doc = json.loads((run_root / "run.json").read_text(encoding="utf-8"))
    assert run_doc["hops"][0]["outcome"] == "uncertain"
    assert run_doc["hops"][0]["actual_commits"] == []  # nothing was trusted-committed.

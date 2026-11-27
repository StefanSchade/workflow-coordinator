"""O8, WI-WFC-BETA2-O8-001: `wfc extend-budget` -- the explicit, recorded
maintainer extension of a budget-stopped Run.

Reproduces, structurally, the incident shape the slice fixes (friction
entries 15 and 19): a Run that spent its whole authorized ceiling and
stopped `awaiting_human_budget` one review short, with an open blocking
Finding whose content is a single unused import. It is continued in place on
a recorded maintainer authority, with every used Hop still charged, its
Findings, phase and pinned governing truth intact and the admitted
Commission digest still verifiable. Every refusal is exercised one at a
time and asserted to change nothing, the visibility surfaces are checked,
and `wfc resume`'s own mechanical-recovery path is asserted unchanged. Uses
the same fake-engine fixture pattern as
``test_mechanical_recovery_lifecycle.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from workflow_coordinator.frontdoor import journal as J
from workflow_coordinator.frontdoor import lifecycle as L
from workflow_coordinator.frontdoor.cli import main as cli_main
from workflow_coordinator.model import Finding, FindingState, SemanticStop
from workflow_coordinator.persistence import write_run_snapshot
from workflow_coordinator.serialization import load_run

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "bp4"
FAKE_PROVIDER = FIXTURES / "fake_provider.py"
FAKE_ENGINE = FIXTURES / "fake_engine.py"

# The admitted ceiling of the fixture Commission, the number of Hops the
# fixture Run actually charges before it stops, and the Consumer Config
# ceiling the compiled policy carries. Chosen so that every bound can be
# made to bite end-to-end: the total bound is 4 (half of 8, rounded up), the
# per-call bound is 4, and the admitted policy ceiling is 10.
ADMITTED_HOPS = 8
CONFIG_CEILING = 10

AUTHORITY = (
    "Maintainer instruction in this session on 2026-11-27: add one correction "
    "cycle so the open broad review can finish; do not widen scope."
)


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
def _clear_fake_engine_env():
    yield
    for name in (
        "WFC_FAKE_ENGINE_MODE", "WFC_FAKE_ENGINE_CONTINUE_MODE",
        "WFC_FAKE_ENGINE_RUN_ID", "WFC_FAKE_ENGINE_BUDGET_HOPS",
    ):
        os.environ.pop(name, None)


@pytest.fixture
def state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(home))
    return home / "wfc"


@pytest.fixture
def evidence_path(tmp_path: Path) -> Path:
    return tmp_path / "evidence"


@pytest.fixture
def repository(tmp_path: Path, evidence_path: Path) -> Path:
    repo = tmp_path / "consumer-repo"
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
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: seed disposable consumer repository")

    assert cli_main([
        "init", "--repository", str(repo), "--scope-ceiling", "src",
        "--evidence-repository", str(evidence_path),
        "--max-autonomous-hops", str(CONFIG_CEILING),
    ]) == 0
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: commit Consumer Config")
    return repo


def _write_request(repository: Path, path: Path) -> Path:
    head = git(repository, "rev-parse", "HEAD")
    data = {
        "schema_version": 1, "commission_id": "wfc-budget", "work_item_id": "WI-BUDGET",
        "authority_source": "test authorizes this bounded lifecycle.",
        "program": {"id": "simple", "revision": "v1"},
        "task": {
            "title": "budget task", "body": "exercise the recorded budget extension",
            "acceptance": "task-check passes", "withheld_authority": "none beyond scope",
            "escalation_default": "stop on ambiguity",
        },
        "scope": ["src"], "personas": {"worker": "Bounded worker persona."},
        "evidence": {"argv": ["sh", "checks/check.sh"], "timeout_seconds": 60},
        "max_autonomous_hops": ADMITTED_HOPS, "durable_artifacts": {},
        "guidance": {"path": "GUIDANCE.md", "source_commit": head},
    }
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return path


_OVERRIDES = {"worker": {"adapter": "codex_cli", "model": "fixture-model", "reasoning": "medium"}}


def _inputs(repository: Path, request_path: Path) -> L.RunInputs:
    return L.RunInputs(
        repository=repository, config_path=L.DEFAULT_CONFIG_PATH, request_path=request_path,
        target_dir=None, overrides=_OVERRIDES, executable=str(FAKE_PROVIDER),
        hop_timeout_seconds=60.0, confirm=lambda a, b: True,
    )


def _run_with_fake_engine(repository: Path, tmp_path: Path, mode: str) -> L.LifecycleResult:
    os.environ["WFC_FAKE_ENGINE_MODE"] = mode
    os.environ["WFC_FAKE_ENGINE_BUDGET_HOPS"] = str(ADMITTED_HOPS)
    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    request_path = _write_request(repository, tmp_path / "request.yaml")
    return L.run_lifecycle(_inputs(repository, request_path))


def _budget_stopped(repository: Path, tmp_path: Path) -> L.LifecycleResult:
    result = _run_with_fake_engine(repository, tmp_path, "budget_exhausted")
    assert result.classification == "awaiting_human_budget", result.message
    run = load_run(_run_json_path(result))
    assert run.hop_used == run.hop_limit == ADMITTED_HOPS
    assert run.stop_reason == "autonomous_hop_budget_exhausted"
    assert run.semantic_stop is None
    return result


def _checkout_dir_and_journal(repository: Path) -> tuple[Path, J.Journal]:
    checkout = J.canonical_checkout(repository)
    checkout_dir = J.checkout_directory(checkout)
    return checkout_dir, J.Journal(checkout_dir / "journal.jsonl")


def _run_json_path(result: L.LifecycleResult) -> Path:
    return Path(result.checkout_state_root) / "run.json"


def _extend(repository: Path, *, hops: int = 2, authority: str = AUTHORITY, preview: bool = False):
    return L.extend_budget_lifecycle(
        repository, additional_hops=hops, authority=authority, preview=preview,
    )


def _open_blocking_finding(run_path: Path) -> Finding:
    """The incident's own shape: one open blocking Finding whose whole
    content is a single unused import."""

    run = load_run(run_path)
    finding = Finding(
        finding_id="F001", invariant="an unused import remains after the removal",
        blocking=True, state=FindingState.OPEN,
        introduced_by_hop=run.hops[-1].hop_id, changed_by_hop=run.hops[-1].hop_id,
        evidence_refs=("task-check",), disposition="open",
    )
    write_run_snapshot(run_path, replace(run, findings=(finding,)))
    return finding


# --------------------------------------------------------------------------
# The accepted path.
# --------------------------------------------------------------------------

def test_budget_stopped_run_continues_in_place_after_a_recorded_extension(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _budget_stopped(repository, tmp_path)
    finding = _open_blocking_finding(_run_json_path(result))

    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    extended = _extend(repository)
    assert extended.classification == "completed", extended.message
    assert extended.run_id == result.run_id  # the identical Run, never a new one.
    assert "Hop budget extended by 2 to 10" in extended.message

    run = load_run(_run_json_path(result))
    assert run.hop_limit == ADMITTED_HOPS + 2
    assert run.admitted_hop_limit == ADMITTED_HOPS
    assert run.findings == (finding,)  # Finding disposition is the reviewer's alone.
    assert [grant.additional_hops for grant in run.budget_extensions] == [2]


def test_used_hops_are_never_reset_or_refunded_by_an_extension(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """`WFC-AUTO-05`: the extension raises the ceiling and nothing else. Every
    Hop charged before the stop stays charged, with its identity, and the
    continuation charges on top of them."""

    result = _budget_stopped(repository, tmp_path)
    before = load_run(_run_json_path(result))

    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    assert _extend(repository).classification == "completed"

    after = load_run(_run_json_path(result))
    assert after.hop_used == before.hop_used + 1 == ADMITTED_HOPS + 1
    assert [hop.hop_id for hop in after.hops[:ADMITTED_HOPS]] == [
        hop.hop_id for hop in before.hops
    ]
    assert after.budget_extensions[0].hop_used_at_grant == ADMITTED_HOPS


def test_the_admitted_commission_and_its_digest_stay_verifiable(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _budget_stopped(repository, tmp_path)
    checkout_dir, journal = _checkout_dir_and_journal(repository)
    authorized = journal.latest(("authorized",))
    digest_before = authorized.data["commission_digest"]
    work_item_path = repository / ".wfc" / "governance" / "wfc-budget" / "work-item.yaml"
    admitted_before = yaml.safe_load(work_item_path.read_text(encoding="utf-8"))

    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    assert _extend(repository).classification == "completed"

    assert journal.latest(("authorized",)).data["commission_digest"] == digest_before
    assert L._recovery_commission_identity_ok(repository, checkout_dir, authorized) is True
    # The admitted governing truth itself is never rewritten: the WorkItem
    # still declares the ceiling admission judged, while the Run's own
    # effective ceiling rose by the recorded grant.
    assert yaml.safe_load(work_item_path.read_text(encoding="utf-8")) == admitted_before
    assert admitted_before["max_autonomous_hops"] == ADMITTED_HOPS
    assert load_run(_run_json_path(result)).hop_limit == ADMITTED_HOPS + 2


def test_the_extension_is_journaled_with_its_own_record_vocabulary(
    repository: Path, tmp_path: Path, state_home: Path,
):
    _budget_stopped(repository, tmp_path)
    _, journal = _checkout_dir_and_journal(repository)
    before = len(journal.read_all())

    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    assert _extend(repository).classification == "completed"

    kinds = [record.kind for record in journal.read_all()[before:]]
    assert kinds[:4] == [
        "budget_extension_intent", "budget_extension_granted",
        "budget_extension_launch_intent", "budget_extension_launch_spawned",
    ]
    assert "classified" in kinds
    # The mechanical-recovery vocabulary is never used by this path.
    assert not any(kind.startswith("mechanical_recovery") for kind in kinds)

    grant = journal.latest(("budget_extension_granted",)).data
    assert grant["additional_hops"] == 2
    assert grant["hop_limit_before"] == ADMITTED_HOPS
    assert grant["hop_limit_after"] == ADMITTED_HOPS + 2
    assert grant["hop_used_at_grant"] == ADMITTED_HOPS
    assert grant["run_status_at_grant"] == "awaiting_human_budget"
    assert grant["stop_reason_at_grant"] == "autonomous_hop_budget_exhausted"
    assert grant["authority"] == AUTHORITY
    assert grant["granted_at"].endswith("Z")


def test_a_second_extension_is_accepted_within_the_total_bound(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _budget_stopped(repository, tmp_path)

    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "budget_exhausted_again"
    first = _extend(repository, hops=1)
    assert first.classification == "awaiting_human_budget", first.message

    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    second = _extend(repository, hops=1)
    assert second.classification == "completed", second.message

    run = load_run(_run_json_path(result))
    assert [grant.sequence for grant in run.budget_extensions] == [1, 2]
    assert run.hop_limit == ADMITTED_HOPS + 2
    assert run.admitted_hop_limit == ADMITTED_HOPS


# --------------------------------------------------------------------------
# Every refusal, one at a time: named, and nothing changed.
# --------------------------------------------------------------------------

def test_an_extension_on_a_non_budget_stop_is_refused(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """The explicitly required negative case: only a budget stop may be
    extended. An ordinary semantic `awaiting_human` stop is refused by name
    and keeps every byte of its record."""

    result = _run_with_fake_engine(repository, tmp_path, "awaiting_human")
    assert result.classification == "awaiting_human"
    run_path = _run_json_path(result)
    before_bytes = run_path.read_bytes()
    _, journal = _checkout_dir_and_journal(repository)
    before_records = len(journal.read_all())

    refused = _extend(repository)
    assert refused.classification == "refused_budget_extension"
    assert "not awaiting_human_budget: awaiting_human" in refused.message
    assert run_path.read_bytes() == before_bytes
    assert len(journal.read_all()) == before_records


@pytest.mark.parametrize("mode, classification", [
    ("reconcile_required", "reconcile_required"),
    ("stopped", "stopped"),
    ("completed_consistent", "completed"),
])
def test_every_other_classification_is_refused_by_name(
    repository: Path, tmp_path: Path, state_home: Path, mode, classification,
):
    result = _run_with_fake_engine(repository, tmp_path, mode)
    assert result.classification == classification
    run_path = _run_json_path(result)
    before_bytes = run_path.read_bytes()

    refused = _extend(repository)
    assert refused.classification == "refused_budget_extension"
    assert f"not awaiting_human_budget: {classification}" in refused.message
    assert run_path.read_bytes() == before_bytes


def test_a_run_that_is_not_actually_out_of_budget_is_refused(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _budget_stopped(repository, tmp_path)
    run_path = _run_json_path(result)
    write_run_snapshot(run_path, replace(load_run(run_path), hop_limit=ADMITTED_HOPS + 2))
    before_bytes = run_path.read_bytes()

    refused = _extend(repository)
    assert refused.classification == "refused_budget_extension"
    assert "not actually out of autonomous Hop budget" in refused.message
    assert run_path.read_bytes() == before_bytes


def test_a_frozen_semantic_stop_is_refused(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """A Run carrying a frozen semantic stop needs the human's answer and,
    where eligible, the `prepare-successor` channel -- not more budget."""

    result = _budget_stopped(repository, tmp_path)
    run_path = _run_json_path(result)
    run = load_run(run_path)
    write_run_snapshot(run_path, replace(run, semantic_stop=SemanticStop(
        kind="scope", question="May I touch a file outside scope?",
        source_hop_id=run.hops[-1].hop_id,
    )))
    before_bytes = run_path.read_bytes()

    refused = _extend(repository)
    assert refused.classification == "refused_budget_extension"
    assert "frozen semantic stop" in refused.message
    assert run_path.read_bytes() == before_bytes


def test_an_amount_above_the_per_call_bound_is_refused(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _budget_stopped(repository, tmp_path)
    before_bytes = _run_json_path(result).read_bytes()

    refused = _extend(repository, hops=L.BUDGET_EXTENSION_MAX_PER_CALL + 1)
    assert refused.classification == "refused_budget_extension"
    assert f"exceeds the per-call bound of {L.BUDGET_EXTENSION_MAX_PER_CALL} Hops" in refused.message
    assert _run_json_path(result).read_bytes() == before_bytes


def test_an_amount_above_the_admitted_policy_ceiling_is_refused(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """The ceiling is re-derived from this checkout's cached, digest-verified
    Commission -- never from the live Consumer Config, which the invoking
    agent can edit."""

    result = _budget_stopped(repository, tmp_path)
    before_bytes = _run_json_path(result).read_bytes()

    refused = _extend(repository, hops=4)  # 8 + 4 = 12 > the admitted ceiling of 10.
    assert refused.classification == "refused_budget_extension"
    assert f"admitted repository policy ceiling of {CONFIG_CEILING} Hops" in refused.message
    assert _run_json_path(result).read_bytes() == before_bytes


def test_an_amount_above_the_total_bound_is_refused_after_an_earlier_grant(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _budget_stopped(repository, tmp_path)

    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "budget_exhausted_again"
    assert _extend(repository, hops=2).classification == "awaiting_human_budget"
    before_bytes = _run_json_path(result).read_bytes()

    refused = _extend(repository, hops=3)
    assert refused.classification == "refused_budget_extension"
    assert "exceeds this Run's total extension bound of 4 Hops" in refused.message
    assert _run_json_path(result).read_bytes() == before_bytes


@pytest.mark.parametrize("statement, reason", [
    ("", "authority statement is empty"),
    ("   ", "authority statement is empty"),
    ("yes", "bare authorisation token"),
    ("Yes. Approved! Granted.", "bare authorisation token"),
    ("maintainer approved it", "bare authorisation token"),
    ("the maintainer said so", "too short to name the maintainer's instruction"),
])
def test_a_degenerate_authority_statement_is_refused_by_name(
    repository: Path, tmp_path: Path, state_home: Path, statement, reason,
):
    """No flag at this CLI can prove a human instructed the extension (the
    record says so plainly); what it can and does refuse is an
    authorisation *gesture* standing in for a statement of the
    instruction."""

    result = _budget_stopped(repository, tmp_path)
    before_bytes = _run_json_path(result).read_bytes()

    refused = _extend(repository, authority=statement)
    assert refused.classification == "refused_budget_extension"
    assert reason in refused.message
    assert _run_json_path(result).read_bytes() == before_bytes


def test_a_moved_head_is_refused(repository: Path, tmp_path: Path, state_home: Path):
    result = _budget_stopped(repository, tmp_path)
    (repository / "src" / "extra.txt").write_text("more\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "chore: move HEAD after the budget stop")
    before_bytes = _run_json_path(result).read_bytes()

    refused = _extend(repository)
    assert refused.classification == "refused_budget_extension"
    assert "HEAD" in refused.message
    assert _run_json_path(result).read_bytes() == before_bytes


def test_a_dirty_worktree_is_refused(repository: Path, tmp_path: Path, state_home: Path):
    result = _budget_stopped(repository, tmp_path)
    (repository / "src" / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")
    before_bytes = _run_json_path(result).read_bytes()

    refused = _extend(repository)
    assert refused.classification == "refused_budget_extension"
    assert "not clean" in refused.message
    assert _run_json_path(result).read_bytes() == before_bytes


def test_a_changed_commission_identity_is_refused(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _budget_stopped(repository, tmp_path)
    checkout_dir, _ = _checkout_dir_and_journal(repository)
    request_path = checkout_dir / "commission" / "request.yaml"
    document = yaml.safe_load(request_path.read_text(encoding="utf-8"))
    document["task"]["title"] += " (tampered since authorization)"
    request_path.write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8")
    before_bytes = _run_json_path(result).read_bytes()

    refused = _extend(repository)
    assert refused.classification == "refused_budget_extension"
    assert "Commission or execution envelope identity changed since authorization" in refused.message
    assert _run_json_path(result).read_bytes() == before_bytes


def test_an_active_lifecycle_lock_is_refused_before_any_mutation(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _budget_stopped(repository, tmp_path)
    run_path = _run_json_path(result)
    before_bytes = run_path.read_bytes()
    checkout_dir, journal = _checkout_dir_and_journal(repository)
    before_records = len(journal.read_all())

    lock = J.try_acquire_lock(checkout_dir)
    assert lock is not None
    try:
        refused = _extend(repository)
        assert refused.classification == "refused_budget_extension"
        assert "active lifecycle already running for this checkout" in refused.message
        assert run_path.read_bytes() == before_bytes
        assert len(journal.read_all()) == before_records
    finally:
        J.release_lock(lock)

    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    assert _extend(repository).classification == "completed"


def test_a_missing_engine_executable_is_refused_and_a_later_call_still_extends(
    repository: Path, tmp_path: Path, state_home: Path,
):
    result = _budget_stopped(repository, tmp_path)
    run_path = _run_json_path(result)
    before_bytes = run_path.read_bytes()
    _, journal = _checkout_dir_and_journal(repository)
    before_records = len(journal.read_all())

    L._ENGINE_COMMAND = (str(tmp_path / "no-such-engine-binary"),)
    refused = _extend(repository)
    assert refused.classification == "refused_budget_extension"
    assert "no engine executable is available for a continuation" in refused.message
    assert run_path.read_bytes() == before_bytes
    assert len(journal.read_all()) == before_records

    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    assert _extend(repository).classification == "completed"


def test_a_launch_failure_rolls_the_extension_back_and_a_later_call_still_extends(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """The continuation engine can pass both pre-mutation checks and still
    fail to spawn (a stale console script with a dangling shebang). The Run
    and checkpoint must be rolled back to their pre-extension state, with
    the grant *and* its rollback both kept in the append-only journal, so a
    rolled-back grant is an auditable attempt that consumes no bound."""

    result = _budget_stopped(repository, tmp_path)
    run_path = _run_json_path(result)
    before_bytes = run_path.read_bytes()
    _, journal = _checkout_dir_and_journal(repository)
    before_records = journal.read_all()

    broken_engine = tmp_path / "broken-wfc-engine"
    broken_engine.write_text("#!/nonexistent/interpreter-xyz\n", encoding="utf-8")
    broken_engine.chmod(0o755)
    L._ENGINE_COMMAND = (str(broken_engine),)

    with pytest.raises(L.LifecycleError) as excinfo:
        _extend(repository)
    assert excinfo.value.classification == "refused_budget_extension"
    assert "budget extension continuation engine failed to launch" in str(excinfo.value)

    assert run_path.read_bytes() == before_bytes
    assert [record.kind for record in journal.read_all()[len(before_records):]] == [
        "budget_extension_intent", "budget_extension_granted",
        "budget_extension_launch_intent", "budget_extension_launch_failed",
        "budget_extension_rolled_back",
    ]
    rolled_back = load_run(run_path)
    assert rolled_back.status.value == "awaiting_human_budget"
    assert rolled_back.stop_reason == "autonomous_hop_budget_exhausted"
    assert rolled_back.budget_extensions == ()
    # A rolled-back grant is an attempt, not a grant: it is not reported as
    # one, and it consumed none of the total bound.
    assert L.status_lifecycle(repository).budget_extensions == ()

    L._ENGINE_COMMAND = (sys.executable, str(FAKE_ENGINE))
    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    assert _extend(repository, hops=4).classification == "refused_budget_extension"
    assert _extend(repository, hops=2).classification == "completed"


def test_a_checkout_with_no_lifecycle_is_refused_without_a_classification(
    repository: Path, tmp_path: Path, state_home: Path,
):
    with pytest.raises(L.LifecycleError) as excinfo:
        _extend(repository)
    assert excinfo.value.classification == "refused_no_lifecycle"
    assert "nothing to extend" in str(excinfo.value)


# --------------------------------------------------------------------------
# The preview: the confirmation block, and nothing else.
# --------------------------------------------------------------------------

def test_preview_shows_the_confirmation_and_changes_nothing(
    repository: Path, tmp_path: Path, state_home: Path, capsys,
):
    result = _budget_stopped(repository, tmp_path)
    run_path = _run_json_path(result)
    before_bytes = run_path.read_bytes()
    _, journal = _checkout_dir_and_journal(repository)
    before_records = len(journal.read_all())
    capsys.readouterr()

    code = cli_main([
        "extend-budget", "--repository", str(repository), "--additional-hops", "2",
        "--authority", AUTHORITY, "--preview",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "BUDGET EXTENSION (recorded maintainer authority)" in out
    assert f"run id            {result.run_id}" in out
    assert f"hops used         {ADMITTED_HOPS} of {ADMITTED_HOPS} (charged; never refunded)" in out
    assert f"new ceiling       {ADMITTED_HOPS + 2}" in out
    assert "total bound       4" in out
    assert AUTHORITY in out
    assert run_path.read_bytes() == before_bytes
    assert len(journal.read_all()) == before_records


def test_the_cli_requires_both_an_amount_and_an_authority_statement(
    repository: Path, tmp_path: Path, state_home: Path,
):
    _budget_stopped(repository, tmp_path)
    for argv in (
        ["extend-budget", "--repository", str(repository), "--additional-hops", "2"],
        ["extend-budget", "--repository", str(repository), "--authority", AUTHORITY],
    ):
        with pytest.raises(SystemExit) as excinfo:
            cli_main(argv)
        assert excinfo.value.code == 2


# --------------------------------------------------------------------------
# Visibility: status, report, audit and the archive.
# --------------------------------------------------------------------------

def test_status_shows_every_recorded_extension(
    repository: Path, tmp_path: Path, state_home: Path, capsys,
):
    _budget_stopped(repository, tmp_path)
    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    assert _extend(repository).classification == "completed"

    status = L.status_lifecycle(repository)
    assert [item["additional_hops"] for item in status.budget_extensions] == [2]

    capsys.readouterr()
    assert cli_main(["status", "--repository", str(repository)]) == 0
    out = capsys.readouterr().out
    assert "BUDGET EXTENSION 1:" in out
    assert f"additional hops: 2 ({ADMITTED_HOPS} -> {ADMITTED_HOPS + 2})" in out
    assert f"hops used at grant: {ADMITTED_HOPS} (charged; never refunded)" in out
    assert f"authority: {AUTHORITY}" in out


def test_the_archived_audit_and_report_show_the_extension_structurally(
    repository: Path, tmp_path: Path, state_home: Path, evidence_path: Path,
):
    _budget_stopped(repository, tmp_path)
    os.environ["WFC_FAKE_ENGINE_CONTINUE_MODE"] = "completed_consistent"
    extended = _extend(repository)
    assert extended.classification == "completed"

    _, journal = _checkout_dir_and_journal(repository)
    archived = journal.latest(("ext.archive.result",))
    assert archived is not None
    destination = evidence_path / archived.data["destination_relative"]
    audit_doc = json.loads((destination / "audit.json").read_text(encoding="utf-8"))

    grants = audit_doc["run"]["budget_extensions"]
    assert len(grants) == 1
    assert grants[0]["additional_hops"] == 2
    assert grants[0]["hop_limit_before"] == ADMITTED_HOPS
    assert grants[0]["hop_used_at_grant"] == ADMITTED_HOPS
    assert grants[0]["run_status_at_grant"] == "awaiting_human_budget"
    # The free-text statement is deliberately absent from the sanitized
    # projection and represented by its digest; the verbatim text is in the
    # archived raw Run record, which the digest ties it to.
    assert "authority" not in grants[0]
    assert len(grants[0]["authority_sha256"]) == 64

    archived_runs = list((destination / "run" / "checkouts").glob("*/run.json"))
    assert len(archived_runs) == 1
    archived_run = json.loads(archived_runs[0].read_text(encoding="utf-8"))
    assert archived_run["budget_extensions"][0]["authority"] == AUTHORITY
    assert archived_run["budget_extensions"][0]["authority_sha256"] == grants[0]["authority_sha256"]

    assert extended.postrun_report_text is not None
    assert "budget ext  +2 hops" in extended.postrun_report_text


def test_the_report_next_action_for_a_budget_stop_names_the_real_command(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """Before this slice the fixed line named `wfc resume`, which never
    answered a budget stop at all."""

    stopped = _budget_stopped(repository, tmp_path)

    assert stopped.postrun_report_text is not None
    assert "wfc extend-budget" in stopped.postrun_report_text
    assert "wfc resume" not in stopped.postrun_report_text.split("next action")[-1]


# --------------------------------------------------------------------------
# Out of scope entirely: no other stop class or path changes.
# --------------------------------------------------------------------------

def test_resume_on_a_budget_stop_keeps_its_unchanged_no_op_behaviour(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """`wfc resume` gains no budget eligibility: a budget stop still falls
    through to the pre-existing no-op, and mechanical recovery is never
    attempted for it."""

    result = _budget_stopped(repository, tmp_path)
    run_path = _run_json_path(result)
    before_bytes = run_path.read_bytes()

    resumed = L.resume_lifecycle(repository)
    assert resumed.classification == "awaiting_human_budget"
    assert resumed.message == "lifecycle already classified (no-op)"
    assert run_path.read_bytes() == before_bytes
    _, journal = _checkout_dir_and_journal(repository)
    assert journal.latest(("mechanical_recovery_intent",)) is None
    assert journal.latest(("budget_extension_intent",)) is None


def test_the_mechanical_recovery_path_still_refuses_an_exhausted_budget(
    repository: Path, tmp_path: Path, state_home: Path,
):
    """The two paths stay separate: `wfc resume` keeps refusing a
    mechanically recoverable stop with no Hop left by name, and
    `extend-budget` keeps requiring a budget stop. Combining them is O10's."""

    result = _run_with_fake_engine(repository, tmp_path, "mechanical_awaiting_human")
    run_path = _run_json_path(result)
    write_run_snapshot(run_path, replace(load_run(run_path), hop_limit=load_run(run_path).hop_used))
    before_bytes = run_path.read_bytes()

    recovered = L.resume_lifecycle(repository)
    assert recovered.classification == "refused_not_recoverable"
    assert "no autonomous Hop budget remains" in recovered.message

    refused = _extend(repository)
    assert refused.classification == "refused_budget_extension"
    assert run_path.read_bytes() == before_bytes

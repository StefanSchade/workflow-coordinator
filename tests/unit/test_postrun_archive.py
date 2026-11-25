"""BP6c: ``frontdoor.archive.archive_run``'s new ``lifecycle`` and
``attempt_identity`` parameters.

``archive_run`` already threads an optional ``lifecycle`` mapping (the BP4a
classification and the latest BP4b preservation fact) into the one atomic
``audit.json`` it projects and commits, for every retention mode -- this is
the piece ``frontdoor.postrun`` needs so the lifecycle facts land in the
*same* commit as the raw files, never a second write. ``attempt_identity``
is the maintainer-decided, Run-derived Attempt identity (never the engine
state root's own directory basename) the product lifecycle passes in so two
Runs of the same consumer never collide on one destination. These tests call
``archive_run`` directly (programmatically; the ``wfc archive`` CLI itself
gains no new flag), reusing the same synthetic-tree construction
``test_archive_retention.py`` already established as the specification.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pytest

from workflow_coordinator.frontdoor import archive as ar
from workflow_coordinator.frontdoor.init import ensure_evidence_repository

RUN_ID = "RUN-" + "p" * 32
WORK_ITEM = "WI-FIXTURE-POSTRUN-001"
CHECKOUT = "33333333-4444-5555-6666-777777777777"
TARGET_REPO = "/nowhere/fixture-product-repo"

REAL_GIT = shutil.which("git")


def git(repository: Path, *args: str) -> str:
    proc = subprocess.run([REAL_GIT, "-C", str(repository), *args], capture_output=True, text=True, check=True)
    return proc.stdout


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def build(base: Path, *, attempt: str, run_id: str = RUN_ID) -> SimpleNamespace:
    home = base / "home"
    state = home / "wfc-state" / attempt
    (state / "checkouts" / CHECKOUT).mkdir(parents=True)

    write_json(state / "operator-summary.json", {
        "schema_version": 1, "run_id": run_id, "work_item_id": WORK_ITEM,
        "run_status": "completed", "checkout_id": CHECKOUT,
        "hops": [{"hop_id": "H001", "outcome": "completed"}],
        "execution_evidence": [{"adapter": "codex_cli", "model": "fixture-model"}],
    })
    write_json(state / "operator-plan.json", {
        "schema_version": 2, "run_id": run_id, "repository": TARGET_REPO, "state_root": str(state),
    })
    write_json(state / "checkout-association.json",
               {"schema_version": 1, "checkout_id": CHECKOUT, "canonical_path": TARGET_REPO})
    write_json(state / "checkouts" / CHECKOUT / "run.json",
               {"run_id": run_id, "work_item_id": WORK_ITEM, "status": "completed", "policy_id": "fixture-policy"})

    repo = base / attempt / "evidence"
    assert ensure_evidence_repository(repo) == "created"
    git(repo, "config", "user.name", "Fixture Operator")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")

    return SimpleNamespace(base=base, home=home, state=state, repo=repo, attempt=attempt, run_id=run_id)


_LIFECYCLE_STOPPED = {
    "classification": "stopped",
    "preservation": {"outcome": "result", "ref": "refs/wfc/preserved/RUN-x/1", "commit": "a" * 40},
}


class PostrunArchiveLifecycleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name) / "sandbox"
        self.base.mkdir(parents=True)
        self._home_before = os.environ.get("HOME")
        self.addCleanup(self._restore_home)

    def _restore_home(self):
        if self._home_before is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._home_before

    def build(self, attempt: str, **kwargs) -> SimpleNamespace:
        fx = build(self.base, attempt=attempt, **kwargs)
        os.environ["HOME"] = str(fx.home)
        return fx

    def dest(self, fx, *, attempt_id: str | None = None) -> Path:
        return fx.repo / "archive" / "unclassified" / (attempt_id or fx.attempt)

    # ----------------------------------------------------------------------
    # Acceptance 7: the lifecycle facts reach audit.json, for every mode.
    # ----------------------------------------------------------------------

    def test_lifecycle_facts_land_in_the_same_commit_for_every_retention_mode(self):
        for raw in ("local", "commit", "discard"):
            with self.subTest(raw=raw):
                fx = self.build(f"wfc-fixture-postrun-{raw}-20261120-01")
                before = git(fx.repo, "rev-parse", "HEAD").strip()
                report = ar.archive_run(
                    state_root=fx.state, evidence_repo=fx.repo, raw_retention=raw, lifecycle=_LIFECYCLE_STOPPED,
                )
                self.assertEqual(report.outcome, "archived")
                # Exactly one new commit holds both the retention payload and audit.json.
                self.assertEqual(git(fx.repo, "rev-list", "--count", f"{before}..HEAD").strip(), "1")

                audit = json.loads((self.dest(fx) / "audit.json").read_text())
                self.assertEqual(audit["lifecycle"]["classification"], "stopped")
                self.assertEqual(audit["lifecycle"]["preservation"]["outcome"], "result")
                self.assertEqual(audit["lifecycle"]["preservation"]["ref"], "refs/wfc/preserved/RUN-x/1")
                self.assertNotIn("preservation_not_recorded", audit["limits"])

    def test_no_lifecycle_supplied_is_unchanged_from_bp6b(self):
        fx = self.build("wfc-fixture-postrun-no-lifecycle-20261120-01")
        ar.archive_run(state_root=fx.state, evidence_repo=fx.repo, raw_retention="local")
        audit = json.loads((self.dest(fx) / "audit.json").read_text())
        self.assertIsNone(audit["lifecycle"])
        self.assertIn("no_lifecycle_facts", audit["limits"])

    # ----------------------------------------------------------------------
    # Identity decision: an explicit attempt_identity replaces the legacy
    # state-root-basename derivation entirely, for every raw_retention value.
    # ----------------------------------------------------------------------

    def test_explicit_attempt_identity_replaces_state_root_basename(self):
        fx = self.build("engine-state")  # the BP4a basename every checkout actually uses.
        report = ar.archive_run(
            state_root=fx.state, evidence_repo=fx.repo, raw_retention="local",
            attempt_identity="consumer-repo-run-aaa",
        )
        self.assertEqual(report.attempt_id, "consumer-repo-run-aaa")
        self.assertTrue((fx.repo / "archive" / "unclassified" / "consumer-repo-run-aaa" / "audit.json").is_file())
        self.assertFalse((fx.repo / "archive" / "unclassified" / "engine-state").exists())

    def test_explicit_attempt_identity_applies_even_without_raw_retention(self):
        # A bare, pre-BP6b-style Evidence repository (no managed .gitignore
        # block) -- the legacy raw_retention=None path's own documented
        # compatibility boundary (see the BP6b limitations record).
        fx = build(self.base, attempt="engine-state")
        os.environ["HOME"] = str(fx.home)
        bare_repo = self.base / "bare-evidence"
        (bare_repo / "archive").mkdir(parents=True)
        (bare_repo / "archive" / ".gitkeep").write_text("")
        git(bare_repo, "init", "-q", "-b", "main")
        git(bare_repo, "config", "user.name", "Fixture Operator")
        git(bare_repo, "config", "user.email", "fixture@example.invalid")
        git(bare_repo, "config", "commit.gpgsign", "false")
        git(bare_repo, "add", "-A")
        git(bare_repo, "commit", "-q", "-m", "fixture baseline")

        report = ar.archive_run(
            state_root=fx.state, evidence_repo=bare_repo, attempt_identity="consumer-repo-run-bbb",
        )
        self.assertEqual(report.attempt_id, "consumer-repo-run-bbb")
        dest = bare_repo / "archive" / "unclassified" / "consumer-repo-run-bbb"
        self.assertTrue((dest / "manifest.json").is_file())
        self.assertFalse((dest / "audit.json").exists())  # raw_retention=None: no audit.json, BP5 unchanged.

    def test_implausible_explicit_attempt_identity_is_refused(self):
        fx = self.build("wfc-fixture-postrun-bad-identity-20261120-01")
        for bad in ("", "-leading-dash", "Has/Slash", "../escape", "UPPER"):
            with self.subTest(bad=bad):
                with self.assertRaises(ar.ArchiveError):
                    ar.archive_run(
                        state_root=fx.state, evidence_repo=fx.repo, raw_retention="local",
                        attempt_identity=bad,
                    )

    # ----------------------------------------------------------------------
    # Acceptance 13a: two Runs of the same consumer archive without collision.
    # ----------------------------------------------------------------------

    def test_two_runs_of_the_same_consumer_never_collide(self):
        fx_a = self.build("engine-state", run_id="RUN-" + "a" * 32)
        shared_repo = fx_a.repo
        report_a = ar.archive_run(
            state_root=fx_a.state, evidence_repo=shared_repo, raw_retention="local",
            attempt_identity="consumer-repo-run-" + "a" * 32,
        )
        self.assertEqual(report_a.outcome, "archived")

        # A second, distinct Run of the same consumer, same shared Evidence
        # repository, own state root -- but a different Run id.
        fx_b = build(self.base, attempt="engine-state-2", run_id="RUN-" + "b" * 32)
        os.environ["HOME"] = str(fx_b.home)
        report_b = ar.archive_run(
            state_root=fx_b.state, evidence_repo=shared_repo, raw_retention="local",
            attempt_identity="consumer-repo-run-" + "b" * 32,
        )
        self.assertEqual(report_b.outcome, "archived")
        self.assertNotEqual(report_a.destination_relative, report_b.destination_relative)

        dest_a = shared_repo / "archive" / "unclassified" / ("consumer-repo-run-" + "a" * 32)
        dest_b = shared_repo / "archive" / "unclassified" / ("consumer-repo-run-" + "b" * 32)
        self.assertTrue((dest_a / "audit.json").is_file())
        self.assertTrue((dest_b / "audit.json").is_file())
        audit_a = json.loads((dest_a / "audit.json").read_text())
        audit_b = json.loads((dest_b / "audit.json").read_text())
        self.assertEqual(audit_a["run"]["run_id"], "RUN-" + "a" * 32)
        self.assertEqual(audit_b["run"]["run_id"], "RUN-" + "b" * 32)

        # Repeating either is an idempotent no-op; no second commit for that attempt.
        before_a = git(shared_repo, "rev-parse", "HEAD").strip()
        again_a = ar.archive_run(
            state_root=fx_a.state, evidence_repo=shared_repo, raw_retention="local",
            attempt_identity="consumer-repo-run-" + "a" * 32,
        )
        self.assertEqual(again_a.outcome, "no_op")
        self.assertEqual(git(shared_repo, "rev-parse", "HEAD").strip(), before_a)

    # ----------------------------------------------------------------------
    # Idempotency: a resume/no-op re-check recomputes the same audit.json
    # (including the lifecycle facts) and never a second commit.
    # ----------------------------------------------------------------------

    def test_repeat_call_with_the_same_lifecycle_is_a_no_op(self):
        fx = self.build("wfc-fixture-postrun-repeat-20261120-01")
        first = ar.archive_run(
            state_root=fx.state, evidence_repo=fx.repo, raw_retention="local", lifecycle=_LIFECYCLE_STOPPED,
        )
        self.assertEqual(first.outcome, "archived")
        before = git(fx.repo, "rev-parse", "HEAD").strip()

        second = ar.archive_run(
            state_root=fx.state, evidence_repo=fx.repo, raw_retention="local", lifecycle=_LIFECYCLE_STOPPED,
        )
        self.assertEqual(second.outcome, "no_op")
        self.assertEqual(git(fx.repo, "rev-parse", "HEAD").strip(), before)

    def test_repeat_call_with_a_different_lifecycle_is_refused_not_silently_overwritten(self):
        fx = self.build("wfc-fixture-postrun-mismatch-20261120-01")
        ar.archive_run(
            state_root=fx.state, evidence_repo=fx.repo, raw_retention="local", lifecycle=_LIFECYCLE_STOPPED,
        )
        different = {"classification": "completed", "preservation": None}
        with self.assertRaises(ar.ArchiveError):
            ar.archive_run(
                state_root=fx.state, evidence_repo=fx.repo, raw_retention="local", lifecycle=different,
            )


class UsageAdditiveArchivingTests(unittest.TestCase):
    """WI-WFC-TOKEN-USAGE-S1-001: ``usage.json`` is archived additively, and
    an attempt built the pre-this-change way (no ``usage.json`` ever
    written) reprojects its ``audit.json`` exactly as before -- no new key,
    no changed limit for it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name) / "sandbox"
        self.base.mkdir(parents=True)
        self._home_before = os.environ.get("HOME")
        self.addCleanup(self._restore_home)

    def _restore_home(self):
        if self._home_before is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._home_before

    def build(self, attempt: str, **kwargs) -> SimpleNamespace:
        fx = build(self.base, attempt=attempt, **kwargs)
        os.environ["HOME"] = str(fx.home)
        return fx

    def dest(self, fx) -> Path:
        return fx.repo / "archive" / "unclassified" / fx.attempt

    def test_old_style_attempt_with_no_usage_json_reprojects_without_the_usage_key(self):
        fx = self.build("wfc-fixture-usage-absent-20261125-01")
        report = ar.archive_run(state_root=fx.state, evidence_repo=fx.repo, raw_retention="local")
        self.assertEqual(report.outcome, "archived")
        audit = json.loads((self.dest(fx) / "audit.json").read_text())
        self.assertNotIn("usage", audit)
        self.assertIn("usage_not_reported", audit["limits"])
        self.assertNotIn("usage_partially_reported", audit["limits"])
        manifest = json.loads((self.dest(fx) / "manifest.json").read_text())
        copied_paths = {entry["path"] for entry in manifest["copied_files"]}
        self.assertNotIn(f"run/checkouts/{CHECKOUT}/usage.json", copied_paths)

    def test_a_present_usage_json_is_archived_additively(self):
        fx = self.build("wfc-fixture-usage-present-20261125-01")
        write_json(fx.state / "checkouts" / CHECKOUT / "usage.json", {
            "schema_version": 1, "run_id": fx.run_id,
            "records": {"H001": {
                "invocation_id": "INV-H001", "role": "worker", "adapter": "claude_cli",
                "model": "claude-haiku-5-5", "coverage": "complete", "input_tokens": 2,
                "output_tokens": 4, "cache_read_tokens": 9074, "cache_creation_tokens": 12736,
                "reasoning_output_tokens": 0, "input_includes_cache_read": False, "per_model": [],
            }},
        })
        report = ar.archive_run(state_root=fx.state, evidence_repo=fx.repo, raw_retention="local")
        self.assertEqual(report.outcome, "archived")
        manifest = json.loads((self.dest(fx) / "manifest.json").read_text())
        copied_paths = {entry["path"] for entry in manifest["copied_files"]}
        self.assertIn(f"run/checkouts/{CHECKOUT}/usage.json", copied_paths)
        self.assertTrue((self.dest(fx) / "run" / "checkouts" / CHECKOUT / "usage.json").is_file())


class ResolveOpsDirProductShapeTests(unittest.TestCase):
    """``frontdoor.archive.resolve_ops_dir`` must tell apart the product
    lifecycle's own per-checkout cached envelope path (never OPS evidence)
    from self-development's own ``execution-bindings.yaml`` (always OPS
    evidence when present): both are recorded, verbatim, as
    ``execution_bindings_source`` in ``operator-plan.json`` by the one real
    engine, so the distinction can only be the basename check itself."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def _state_root(self, *, execution_bindings_source: str) -> Path:
        state_root = self.base / "engine-state"
        state_root.mkdir()
        write_json(state_root / "operator-plan.json", {
            "schema_version": 2, "state_root": str(state_root),
            "execution_bindings_source": execution_bindings_source,
        })
        return state_root

    def test_product_lifecycle_envelope_cache_is_never_mistaken_for_recorded_ops_evidence(self):
        # The product lifecycle's own per-checkout commission directory
        # really exists on disk (it holds the cached envelope used as this
        # Run's ``--execution-bindings`` argv) -- but its basename is
        # ``envelope.yaml``, never ``execution-bindings.yaml``.
        checkout_commission = self.base / "checkouts" / "cid" / "commission"
        checkout_commission.mkdir(parents=True)
        (checkout_commission / "envelope.yaml").write_text("schema_version: 1\n", encoding="utf-8")
        state_root = self._state_root(execution_bindings_source=str(checkout_commission / "envelope.yaml"))
        missing_ops_candidate = self.base / "home" / "wfc-ops" / "consumer-repo-run-x"  # never created.

        self.assertIsNone(ar.resolve_ops_dir(state_root, missing_ops_candidate, "consumer-repo-run-x"))

    def test_self_development_execution_bindings_yaml_recorded_but_missing_is_still_refused(self):
        # The self-development shape both versions of this function must
        # still refuse: ``execution-bindings.yaml`` really was prepared (it
        # is on disk), but the default OPS candidate this call resolves is a
        # different, missing directory.
        prepared_ops = self.base / "home" / "wfc-ops" / "wfc-fixture-attempt"
        prepared_ops.mkdir(parents=True)
        (prepared_ops / "execution-bindings.yaml").write_text("schema_version: 1\n", encoding="utf-8")
        state_root = self._state_root(execution_bindings_source=str(prepared_ops / "execution-bindings.yaml"))
        missing_ops_candidate = self.base / "home" / "wfc-ops" / "other-attempt"

        with self.assertRaises(ar.ArchiveError):
            ar.resolve_ops_dir(state_root, missing_ops_candidate, "other-attempt")


class ValidateExplicitAttemptIdentityTests(unittest.TestCase):
    def test_accepts_lower_case_safe_identity(self):
        self.assertEqual(ar.validate_explicit_attempt_identity("consumer-repo-run-abc123"), "consumer-repo-run-abc123")

    def test_rejects_empty_and_implausible_values(self):
        for bad in ("", "-abc", "a/b", "a\\b", "..", "UPPER", "a b"):
            with self.subTest(bad=bad):
                with pytest.raises(ar.ArchiveError):
                    ar.validate_explicit_attempt_identity(bad)

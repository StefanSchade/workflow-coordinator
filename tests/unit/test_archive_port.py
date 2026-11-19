"""Ported wrapper-orchestration coverage for the installed ``wfc archive``.

Semantic port of the Evidence repository's ``tests/test_wfc_archive_run.py``
(source commit ``bc65ff777bb75602792e416519a7a3025d723c67``, staged read-only
at ``build/wfc/bp5-evidence-inputs/`` for this port). Every invocation here
goes through ``workflow_coordinator.frontdoor.cli.main`` -- the real
installed CLI path -- exactly as ``tests/unit/test_frontdoor_init_plan.py``
drives ``init``/``plan``.

Documented porting adaptations (the "unavoidable... case" the task asks to
record, mirrored here for the wrapper half of the port):

* ``test_wrapper_is_executable`` has no port: there is no longer a
  standalone executable script, only the installed ``wfc archive``
  subcommand dispatched by ``frontdoor.cli``.
* ``test_evidence_repository_without_the_archiver_is_rejected`` is ported as
  ``test_evidence_repository_without_an_archive_directory_is_rejected``: the
  "is this a WFC evidence repository" marker is no longer the presence of an
  external ``scripts/archive_run.py`` (there is none any more -- the
  archiver is in-process), but the repository's own ``archive/`` directory.
* ``test_wrong_argument_count_is_rejected`` is ported against argparse's own
  required-positional refusal (``SystemExit(2)``), the same convention
  ``test_frontdoor_init_plan.py`` uses for a missing required CLI argument,
  rather than a hand-written ``die()`` message.
* The three ``ArchiverFailurePropagationTests`` do not port: they exercised
  a separate archiver *subprocess* misbehaving between a dry-run plan and
  the real copy, a seam this port collapses into one in-process call (per
  the second-pass review's step 2+3: "move archive_run.py... wrapper logic
  in Python"). The safety property they guarded -- a failing precondition
  leaves no destination, no staging and no commit -- is covered instead by
  ``RefusalTests`` and ``StagingAndRetryTests`` below.
* The commit itself is made with ``git_facts.bounded_path_commit``, which
  commits a bare one-line subject with no body (the same primitive the
  Commission commit uses). The old wrapper's rich identity narrative lived
  in the commit *body*; here it is reported on stdout instead, so ported
  assertions that read the commit body for run/work-item/repository/file
  counts are adapted to read stdout, with the subject-only assertion
  unchanged. ``checkout_id`` is the one identity field from that old
  commit-body narrative that is not ported into the stdout report at all --
  ``archive.render_report`` never emits it, so there is no "checkout:" text
  to assert on in either direction. Its forgery-resistance property is still
  fully preserved, just moved upstream from display-time scrubbing to
  ``archive.run_identity``'s own fail-closed ``checkout_id`` agreement check
  (``RefusalTests.test_disagreeing_checkout_id_across_sources_is_rejected``
  below), which is strictly stronger: a disagreeing/forged checkout_id now
  refuses the whole archive rather than merely being omitted from the report.

``StagingAndRetryTests`` below is new: it proves the BP5 additions the old
scripts never had (staging, atomic placement, idempotent retry) and the
narrowed cleanliness rule from
``wfc_beta1_product_boundary_review_second_pass.adoc``'s commit-ownership
section ("nothing staged, and nothing dirty outside the attempt's own
destination and staging directory").
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from workflow_coordinator.frontdoor import archive as ar
from workflow_coordinator.frontdoor.cli import main as cli_main

ATTEMPT = "wfc-fixture-audit-20260929-01"
RUN_ID = "RUN-f1x7ure00000000000000000000000a"
WORK_ITEM = "WI-FIXTURE-AUDIT-001"
CHECKOUT = "11111111-2222-3333-4444-555555555555"
TARGET_REPO = "/nowhere/fixture-product-repo"

REAL_GIT = shutil.which("git")


def git(repository: Path, *args: str) -> str:
    proc = subprocess.run([REAL_GIT, "-C", str(repository), *args],
                          capture_output=True, text=True, check=True)
    return proc.stdout


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def build(base: Path, *, attempt: str = ATTEMPT, with_ops: bool = True,
          plan_state_root: str | None = None, pinned_attempt: str | None = None,
          with_remote: bool = False) -> SimpleNamespace:
    """A synthetic Run state root (and matching OPS root) plus a disposable
    git Evidence repository, exactly like the ported wrapper test's fixture
    (minus the now-unnecessary copy of ``archive_run.py`` into the repo)."""

    home = base / "home"
    state = home / "wfc-state" / attempt
    ops = home / "wfc-ops" / attempt
    (state / "checkouts" / CHECKOUT).mkdir(parents=True)

    if with_ops:
        ops.mkdir(parents=True)
        (ops / "execution-bindings.yaml").write_text(
            "schema_version: 1\nbindings:\n  worker:\n    adapter: codex_cli\n")
        (ops / "preparation-pins.txt").write_text(
            f"attempt_id={pinned_attempt or attempt}\nstate_root={state}\n")

    write_json(state / "operator-summary.json", {
        "schema_version": 1, "run_id": RUN_ID, "work_item_id": WORK_ITEM,
        "run_status": "completed", "checkout_id": CHECKOUT,
        "hops": [{"hop_id": "H001", "outcome": "completed"}],
        "execution_evidence": [{"adapter": "codex_cli", "model": "fixture-model"}],
    })
    write_json(state / "operator-plan.json", {
        "schema_version": 2, "run_id": RUN_ID, "repository": TARGET_REPO,
        "state_root": plan_state_root if plan_state_root is not None else str(state),
        "execution_bindings_source": str(ops / "execution-bindings.yaml"),
    })
    write_json(state / "checkout-association.json",
               {"schema_version": 1, "checkout_id": CHECKOUT, "canonical_path": TARGET_REPO})
    write_json(state / "checkouts" / CHECKOUT / "run.json",
               {"run_id": RUN_ID, "status": "completed", "policy_id": "fixture-policy"})
    write_json(state / "checkouts" / CHECKOUT / "checkpoint.json", {"evidence": []})

    repo = base / "evidence"
    (repo / "archive").mkdir(parents=True)
    (repo / "archive" / ".gitkeep").write_text("")
    (repo / ".gitignore").write_text("stray/\n__pycache__/\n")
    (repo / "stray").mkdir()
    (repo / "stray" / "junk.txt").write_text("not evidence\n")

    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Fixture Operator")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fixture baseline")

    remote = None
    if with_remote:
        remote = base / "remote.git"
        subprocess.run([REAL_GIT, "init", "-q", "--bare", str(remote)], check=True)
        git(repo, "remote", "add", "origin", str(remote))

    return SimpleNamespace(base=base, home=home, state=state, ops=ops, repo=repo,
                           attempt=attempt, remote=remote)


class WrapperTestCase(unittest.TestCase):
    """Shared fixture plumbing. Every invocation runs with ``HOME`` pointed
    at the fixture (so the default OPS-directory derivation is exercised)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # A space in the path: every derived path inherits it.
        self.base = Path(self._tmp.name) / "wfc evidence sandbox"
        self.base.mkdir(parents=True)
        self._home_before = os.environ.get("HOME")
        self.addCleanup(self._restore_home)

    def _restore_home(self):
        if self._home_before is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._home_before

    def build(self, **kwargs):
        return build(self.base, **kwargs)

    def run_wrapper(self, fx, *args: str) -> tuple[int, str, str]:
        os.environ["HOME"] = str(fx.home)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli_main(["archive", str(fx.state), str(fx.repo), *args])
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    def archive_normally(self, fx, *extra: str):
        code, out, err = self.run_wrapper(fx, *extra)
        self.assertEqual(code, 0, f"stdout:\n{out}\nstderr:\n{err}")
        return SimpleNamespace(returncode=code, stdout=out, stderr=err)

    def manifest(self, dest: Path) -> dict:
        return json.loads((dest / "manifest.json").read_text())

    def head(self, repo: Path) -> str:
        return git(repo, "rev-parse", "HEAD").strip()

    def committed_paths(self, repo: Path) -> list[str]:
        out = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
        return sorted(p for p in out.splitlines() if p)


class NormalCaseTests(WrapperTestCase):
    def test_two_positional_arguments_are_sufficient(self):
        fx = self.build()
        before = self.head(fx.repo)
        proc = self.archive_normally(fx)

        dest = fx.repo / "archive" / "unclassified" / fx.attempt
        self.assertTrue((dest / "manifest.json").is_file())
        self.assertIn("ARCHIVED WFC RUN", proc.stdout)
        self.assertIn(RUN_ID, proc.stdout)
        self.assertIn("manifest:   valid", proc.stdout)
        self.assertIn("push:       not performed", proc.stdout)
        self.assertNotEqual(self.head(fx.repo), before)
        self.assertEqual(git(fx.repo, "rev-list", "--count", f"{before}..HEAD").strip(), "1")
        self.assertEqual(git(fx.repo, "status", "--porcelain"), "")

    def test_operator_does_not_have_to_read_json_to_see_the_outcome(self):
        fx = self.build()
        proc = self.archive_normally(fx)
        manifest = self.manifest(fx.repo / "archive" / "unclassified" / fx.attempt)
        self.assertIn(f"files:      {len(manifest['copied_files'])}", proc.stdout)
        self.assertIn(WORK_ITEM, proc.stdout)
        self.assertIn(TARGET_REPO, proc.stdout)

    def test_attempt_id_is_derived_from_the_state_root_basename(self):
        fx = self.build(attempt="wfc-derived-attempt-name-20261231-07")
        self.archive_normally(fx)
        dest = fx.repo / "archive" / "unclassified" / "wfc-derived-attempt-name-20261231-07"
        self.assertTrue(dest.is_dir())
        self.assertEqual(self.manifest(dest)["attempt_id"],
                         "wfc-derived-attempt-name-20261231-07")

    def test_run_id_comes_from_run_state_not_from_the_path(self):
        fx = self.build()
        self.archive_normally(fx)
        manifest = self.manifest(fx.repo / "archive" / "unclassified" / fx.attempt)
        self.assertEqual(manifest["facts"]["run_id"], RUN_ID)

    def test_paths_containing_spaces_are_handled(self):
        fx = self.build(attempt="wfc fixture spaced attempt 01")
        self.archive_normally(fx)
        dest = fx.repo / "archive" / "unclassified" / "wfc fixture spaced attempt 01"
        self.assertTrue((dest / "manifest.json").is_file())
        # The manifest records the resolved (symlink-free) OPS path, matching
        # archive_run.py's own internal resolve of its --ops-dir argument.
        self.assertEqual(self.manifest(dest)["sources"]["ops_dir"], str(fx.ops.resolve()))
        self.assertTrue(all(p.startswith("archive/unclassified/wfc fixture spaced attempt 01/")
                            for p in self.committed_paths(fx.repo)))


class OpsDerivationTests(WrapperTestCase):
    def test_matching_ops_root_is_derived_from_home(self):
        fx = self.build()
        proc = self.archive_normally(fx)
        dest = fx.repo / "archive" / "unclassified" / fx.attempt
        self.assertEqual(self.manifest(dest)["sources"]["ops_dir"], str(fx.ops.resolve()))
        self.assertTrue((dest / "ops" / "execution-bindings.yaml").is_file())
        self.assertIn(f"ops:        {fx.ops}", proc.stdout)

    def test_absent_ops_directory_is_optional(self):
        fx = self.build(with_ops=False)
        proc = self.archive_normally(fx)
        dest = fx.repo / "archive" / "unclassified" / fx.attempt
        self.assertIsNone(self.manifest(dest)["sources"]["ops_dir"])
        self.assertFalse((dest / "ops").exists())
        self.assertIn("ops:        none", proc.stdout)

    def test_ops_root_prepared_for_another_attempt_is_rejected(self):
        fx = self.build(pinned_attempt="wfc-some-other-attempt-20260101-01")
        code, out, err = self.run_wrapper(fx)
        self.assertNotEqual(code, 0)
        self.assertIn("incoherent OPS evidence", err)
        self.assertFalse((fx.repo / "archive" / "unclassified" / fx.attempt).exists())


class IdentityConsistencyTests(WrapperTestCase):
    """The report must never assert a Run identity the archived manifest
    cannot itself corroborate. Ported with the old commit-body assertions
    reading stdout instead (see module docstring)."""

    def test_reported_run_id_matches_the_archived_manifest_even_when_only_plan_supplies_one(self):
        fx = self.build()
        summary_path = fx.state / "operator-summary.json"
        summary = json.loads(summary_path.read_text())
        del summary["run_id"]
        summary_path.write_text(json.dumps(summary))
        run_json_path = fx.state / "checkouts" / CHECKOUT / "run.json"
        run_json = json.loads(run_json_path.read_text())
        del run_json["run_id"]
        run_json_path.write_text(json.dumps(run_json))
        proc = self.archive_normally(fx)
        dest = fx.repo / "archive" / "unclassified" / fx.attempt
        manifest_run_id = self.manifest(dest)["facts"].get("run_id")
        self.assertIsNone(manifest_run_id)
        self.assertNotIn(RUN_ID, proc.stdout)

    def test_reported_run_id_matches_the_archived_manifest_in_the_ordinary_case(self):
        fx = self.build()
        proc = self.archive_normally(fx)
        dest = fx.repo / "archive" / "unclassified" / fx.attempt
        manifest_run_id = self.manifest(dest)["facts"]["run_id"]
        self.assertEqual(manifest_run_id, RUN_ID)
        self.assertIn(f"run:        {manifest_run_id}", proc.stdout)

    def test_manifest_identity_cannot_forge_report_fields(self):
        fx = self.build(with_ops=False)
        forged_run_id = ("RUN-HONEST-0001\nfiles\t99999\n"
                         "ops_dir\t/attacker/claimed/ops\n"
                         "repository\t/attacker/claimed/repository\n"
                         "checkout_id\t00000000-dead-beef-dead-beefdeadbeef")
        summary_path = fx.state / "operator-summary.json"
        summary = json.loads(summary_path.read_text())
        summary["run_id"] = forged_run_id
        summary_path.write_text(json.dumps(summary))
        for path in (fx.state / "operator-plan.json",
                     fx.state / "checkouts" / CHECKOUT / "run.json"):
            payload = json.loads(path.read_text())
            del payload["run_id"]
            path.write_text(json.dumps(payload))

        proc = self.archive_normally(fx)
        dest = fx.repo / "archive" / "unclassified" / fx.attempt
        manifest = self.manifest(dest)

        self.assertEqual(manifest["facts"]["run_id"], forged_run_id)
        # checkout_id is not part of this assertion: the port never reports it
        # (see module docstring); its own forgery protection is the fail-closed
        # RefusalTests.test_disagreeing_checkout_id_across_sources_is_rejected.
        self.assertIn(f"repository: {TARGET_REPO}", proc.stdout)
        self.assertIn(f"files:      {len(manifest['copied_files'])}", proc.stdout)
        self.assertNotIn("files:      99999", proc.stdout)
        self.assertNotIn("repository: /attacker/claimed/repository", proc.stdout)
        self.assertNotIn("ops:        /attacker/claimed/ops", proc.stdout)


class SemanticJsonTests(WrapperTestCase):
    def _semantic(self, payload: dict) -> Path:
        path = self.base / "curated semantic.json"
        path.write_text(json.dumps(payload, indent=2) + "\n")
        return path

    def test_semantic_json_is_passed_through_verbatim(self):
        fx = self.build()
        payload = {"domain": "self-development", "workstream": "fixture-workstream",
                   "purpose": "curated by a maintainer", "learnings": "kept verbatim"}
        self.archive_normally(fx, "--semantic-json", str(self._semantic(payload)))
        dest = fx.repo / "archive" / "self-development" / "fixture-workstream" / fx.attempt
        self.assertEqual(self.manifest(dest)["semantic"], payload)

    def test_without_semantic_json_no_significance_is_fabricated(self):
        fx = self.build()
        self.archive_normally(fx)
        dest = fx.repo / "archive" / "unclassified" / fx.attempt
        self.assertIsNone(self.manifest(dest)["semantic"])

    def test_partial_classification_is_refused_rather_than_guessed(self):
        fx = self.build()
        semantic = self._semantic({"domain": "self-development", "purpose": "no workstream"})
        code, out, err = self.run_wrapper(fx, "--semantic-json", str(semantic))
        self.assertNotEqual(code, 0)
        self.assertIn("without its counterpart", err)

    def test_classification_may_not_escape_the_archive_tree(self):
        fx = self.build()
        semantic = self._semantic({"domain": "../../..", "workstream": "escape"})
        code, out, err = self.run_wrapper(fx, "--semantic-json", str(semantic))
        self.assertNotEqual(code, 0)
        self.assertIn("one path segment", err)


class DryRunTests(WrapperTestCase):
    def test_dry_run_writes_nothing_and_commits_nothing(self):
        fx = self.build()
        before = self.head(fx.repo)
        proc = self.archive_normally(fx, "--dry-run")
        self.assertIn("DRY RUN", proc.stdout)
        self.assertIn("commit:     not performed", proc.stdout)
        self.assertIn("push:       not performed", proc.stdout)
        self.assertFalse((fx.repo / "archive" / "unclassified").exists())
        self.assertEqual(self.head(fx.repo), before)
        self.assertEqual(git(fx.repo, "status", "--porcelain"), "")

    def test_dry_run_reports_the_planned_file_count(self):
        fx = self.build()
        planned = self.archive_normally(fx, "--dry-run").stdout
        copied = self.archive_normally(fx).stdout
        for line in planned.splitlines():
            if line.startswith("files:"):
                count = line.split()[1]
                break
        else:
            self.fail(f"no files: line in dry-run output:\n{planned}")
        self.assertIn(f"files:      {count}\n", copied)


class RefusalTests(WrapperTestCase):
    def test_dirty_evidence_repository_is_rejected(self):
        fx = self.build()
        (fx.repo / "untracked-note.txt").write_text("work in progress\n")
        code, out, err = self.run_wrapper(fx)
        self.assertNotEqual(code, 0)
        self.assertIn("not clean", err)
        self.assertFalse((fx.repo / "archive" / "unclassified").exists())

    def test_existing_destination_with_different_content_is_rejected(self):
        fx = self.build()
        dest = fx.repo / "archive" / "unclassified" / fx.attempt
        dest.mkdir(parents=True)
        (dest / "manifest.json").write_text(json.dumps({
            "attempt_id": "pre-existing", "copied_files": [{"path": "run/x.json"}],
        }))
        (dest / "run").mkdir()
        (dest / "run" / "x.json").write_text("{}")
        git(fx.repo, "add", "-A")
        git(fx.repo, "commit", "-q", "-m", "pre-existing archive")
        before = self.head(fx.repo)

        code, out, err = self.run_wrapper(fx)
        self.assertNotEqual(code, 0)
        self.assertIn("different content", err)
        self.assertEqual(self.manifest(dest)["attempt_id"], "pre-existing")
        self.assertEqual(self.head(fx.repo), before)

    def test_missing_state_root_is_rejected(self):
        fx = self.build()
        code, out, err = self.run_wrapper(fx)
        os.environ["HOME"] = str(fx.home)
        out2, err2 = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out2), contextlib.redirect_stderr(err2):
            try:
                code = cli_main(["archive", str(fx.base / "no such state root"), str(fx.repo)])
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
        self.assertNotEqual(code, 0)
        self.assertIn("missing state root", err2.getvalue())

    def test_state_root_without_run_state_is_rejected(self):
        fx = self.build()
        empty = fx.home / "wfc-state" / "wfc-empty-20260929-01"
        empty.mkdir(parents=True)
        os.environ["HOME"] = str(fx.home)
        out2, err2 = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out2), contextlib.redirect_stderr(err2):
            code = cli_main(["archive", str(empty), str(fx.repo)])
        self.assertNotEqual(code, 0)
        self.assertIn("no Run id", err2.getvalue())

    def test_state_root_contradicting_its_own_run_plan_is_rejected(self):
        fx = self.build(plan_state_root="/somewhere/else/entirely")
        code, out, err = self.run_wrapper(fx)
        self.assertNotEqual(code, 0)
        self.assertIn("mismatched state root", err)

    def test_disagreeing_run_id_across_sources_is_rejected(self):
        fx = self.build()
        plan_path = fx.state / "operator-plan.json"
        plan = json.loads(plan_path.read_text())
        plan["run_id"] = "RUN-DIFFERENT-FROM-SUMMARY-999"
        plan_path.write_text(json.dumps(plan))
        code, out, err = self.run_wrapper(fx)
        self.assertNotEqual(code, 0)
        self.assertIn("run_id disagrees", err)
        self.assertFalse((fx.repo / "archive" / "unclassified" / fx.attempt).exists())

    def test_disagreeing_work_item_id_across_sources_is_rejected(self):
        fx = self.build()
        plan_path = fx.state / "operator-plan.json"
        plan = json.loads(plan_path.read_text())
        plan["work_item_id"] = "WI-CONFLICTING-999"
        plan_path.write_text(json.dumps(plan))
        code, out, err = self.run_wrapper(fx)
        self.assertNotEqual(code, 0)
        self.assertIn("work_item_id disagrees", err)

    def test_disagreeing_checkout_id_across_sources_is_rejected(self):
        fx = self.build()
        assoc_path = fx.state / "checkout-association.json"
        assoc = json.loads(assoc_path.read_text())
        assoc["checkout_id"] = "99999999-8888-7777-6666-555555555555"
        assoc_path.write_text(json.dumps(assoc))
        code, out, err = self.run_wrapper(fx)
        self.assertNotEqual(code, 0)
        self.assertIn("checkout_id disagrees", err)

    def test_disagreeing_repository_across_sources_is_rejected(self):
        fx = self.build()
        assoc_path = fx.state / "checkout-association.json"
        assoc = json.loads(assoc_path.read_text())
        assoc["canonical_path"] = "/totally/different/repo"
        assoc_path.write_text(json.dumps(assoc))
        code, out, err = self.run_wrapper(fx)
        self.assertNotEqual(code, 0)
        self.assertIn("repository disagrees", err)

    def test_attempt_id_containing_a_control_character_is_rejected(self):
        fx = self.build(with_ops=False)
        evil_name = "wfc-injected\nCo-Authored-By: someone-else <x@example.com>"
        evil_state = fx.state.parent / evil_name
        fx.state.rename(evil_state)
        plan_path = evil_state / "operator-plan.json"
        plan = json.loads(plan_path.read_text())
        plan["state_root"] = str(evil_state)
        plan_path.write_text(json.dumps(plan))
        os.environ["HOME"] = str(fx.home)
        out2, err2 = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out2), contextlib.redirect_stderr(err2):
            code = cli_main(["archive", str(evil_state), str(fx.repo)])
        self.assertNotEqual(code, 0)
        self.assertIn("control character", err2.getvalue())
        self.assertEqual(git(fx.repo, "log", "--oneline").strip().count("\n"), 0)

    def test_state_root_parent_containing_a_control_character_is_rejected(self):
        fx = self.build(with_ops=False)
        evil_parent = fx.home / "wfc-state\nCo-Authored-By: forged <x@example.com>"
        evil_state = evil_parent / fx.attempt
        fx.state.parent.rename(evil_parent)
        plan_path = evil_state / "operator-plan.json"
        plan = json.loads(plan_path.read_text())
        plan["state_root"] = str(evil_state)
        plan_path.write_text(json.dumps(plan))
        os.environ["HOME"] = str(fx.home)
        before = self.head(fx.repo)
        out2, err2 = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out2), contextlib.redirect_stderr(err2):
            code = cli_main(["archive", str(evil_state), str(fx.repo)])
        self.assertNotEqual(code, 0)
        self.assertIn("state root: contains a control character", err2.getvalue())
        self.assertFalse((fx.repo / "archive" / "unclassified" / fx.attempt).exists())
        self.assertEqual(self.head(fx.repo), before)

    def test_semantic_json_path_containing_a_control_character_is_rejected(self):
        fx = self.build()
        evil_parent = self.base / "curated\nCo-Authored-By: forged <x@example.com>"
        evil_parent.mkdir()
        semantic = evil_parent / "semantic.json"
        write_json(semantic, {"domain": "self-development", "workstream": "fixture"})
        before = self.head(fx.repo)

        code, out, err = self.run_wrapper(fx, "--semantic-json", str(semantic))

        self.assertNotEqual(code, 0)
        self.assertIn("--semantic-json path", err)
        self.assertIn("control character", err)
        self.assertFalse((fx.repo / "archive" / "unclassified" / fx.attempt).exists())
        self.assertEqual(self.head(fx.repo), before)

    def test_evidence_repository_without_an_archive_directory_is_rejected(self):
        fx = self.build()
        shutil.rmtree(fx.repo / "archive")
        git(fx.repo, "add", "-A")
        git(fx.repo, "commit", "-q", "-m", "remove archive directory")
        code, out, err = self.run_wrapper(fx)
        self.assertNotEqual(code, 0)
        self.assertIn("no archive/ directory", err)

    def test_wrong_argument_count_is_rejected(self):
        os.environ["HOME"] = str(self.base)
        with self.assertRaises(SystemExit) as excinfo:
            cli_main(["archive", str(self.base)])
        self.assertEqual(excinfo.exception.code, 2)


class GitBehaviourTests(WrapperTestCase):
    def test_commit_stages_only_the_new_archive_destination(self):
        fx = self.build()
        self.archive_normally(fx)
        prefix = f"archive/unclassified/{fx.attempt}/"
        paths = self.committed_paths(fx.repo)
        self.assertTrue(paths)
        self.assertTrue(all(p.startswith(prefix) for p in paths), paths)
        self.assertTrue((fx.repo / "stray" / "junk.txt").is_file())
        self.assertNotIn("stray/junk.txt", paths)

    def test_commit_subject_names_the_attempt(self):
        fx = self.build()
        proc = self.archive_normally(fx)
        subject = git(fx.repo, "log", "-1", "--format=%s").strip()
        self.assertEqual(subject, f"evidence: archive {fx.attempt}")
        # Adapted: bounded_path_commit commits a bare subject with no body;
        # the identity narrative is on stdout instead (see module docstring).
        self.assertIn(RUN_ID, proc.stdout)

    def test_no_push_is_attempted(self):
        fx = self.build(with_remote=True)
        proc = self.archive_normally(fx)
        self.assertIn("push:       not performed", proc.stdout)
        self.assertEqual(git(fx.repo, "ls-remote", "origin").strip(), "")
        self.assertNotEqual(self.head(fx.repo), "")

    def test_run_ledger_is_not_touched(self):
        fx = self.build()
        ledger = fx.repo / "project_memory" / "run_ledger.adoc"
        ledger.parent.mkdir(parents=True)
        ledger.write_text("= fixture ledger\n")
        git(fx.repo, "add", "-A")
        git(fx.repo, "commit", "-q", "-m", "fixture ledger")
        self.archive_normally(fx)
        self.assertEqual(ledger.read_text(), "= fixture ledger\n")
        self.assertNotIn("project_memory/run_ledger.adoc", self.committed_paths(fx.repo))


class SourceImmutabilityTests(WrapperTestCase):
    def test_state_and_ops_trees_are_left_byte_identical(self):
        fx = self.build()

        def snapshot(root: Path) -> dict[str, bytes]:
            return {str(p.relative_to(root)): p.read_bytes()
                    for p in sorted(root.rglob("*")) if p.is_file()}

        before = (snapshot(fx.state), snapshot(fx.ops))
        self.archive_normally(fx)
        self.assertEqual((snapshot(fx.state), snapshot(fx.ops)), before)


class StagingAndRetryTests(WrapperTestCase):
    """New BP5 behavior absent from the ported scripts: staging, atomic
    placement and idempotent retry, plus the narrowed cleanliness rule."""

    def test_kill_after_placement_before_commit_resumes_to_exactly_one_commit(self):
        fx = self.build()
        before = self.head(fx.repo)

        real_bounded_path_commit = ar.bounded_path_commit

        def exploding_commit(*args, **kwargs):
            raise RuntimeError("simulated kill between copy and commit")

        ar.bounded_path_commit = exploding_commit
        try:
            with self.assertRaises(RuntimeError):
                ar.archive_run(state_root=fx.state, evidence_repo=fx.repo, home=fx.home)
        finally:
            ar.bounded_path_commit = real_bounded_path_commit

        dest = fx.repo / "archive" / "unclassified" / fx.attempt
        self.assertTrue((dest / "manifest.json").is_file(), "placement must survive a killed commit")
        self.assertEqual(self.head(fx.repo), before, "no commit must exist yet")
        manifest_before_retry = self.manifest(dest)

        proc = self.archive_normally(fx)
        self.assertEqual(git(fx.repo, "rev-list", "--count", f"{before}..HEAD").strip(), "1")
        self.assertEqual(self.manifest(dest)["copied_files"], manifest_before_retry["copied_files"])
        self.assertIn("ARCHIVED WFC RUN", proc.stdout)

    def test_kill_before_placement_leaves_no_destination_and_retry_succeeds(self):
        fx = self.build()
        before = self.head(fx.repo)

        real_collect_ops_files = ar.collect_ops_files

        def exploding_collect_ops_files(*args, **kwargs):
            raise RuntimeError("simulated kill during copy")

        ar.collect_ops_files = exploding_collect_ops_files
        try:
            with self.assertRaises(RuntimeError):
                ar.archive_run(state_root=fx.state, evidence_repo=fx.repo, home=fx.home)
        finally:
            ar.collect_ops_files = real_collect_ops_files

        dest = fx.repo / "archive" / "unclassified" / fx.attempt
        self.assertFalse(dest.exists(), "a copy killed before placement must leave no destination")
        self.assertEqual(self.head(fx.repo), before)
        self.assertEqual(git(fx.repo, "status", "--porcelain"), "", "a stale staging directory must not count as dirty")

        self.archive_normally(fx)
        self.assertEqual(git(fx.repo, "rev-list", "--count", f"{before}..HEAD").strip(), "1")
        self.assertTrue((dest / "manifest.json").is_file())

    def test_second_archive_of_the_same_attempt_is_a_no_op(self):
        fx = self.build()
        first = self.archive_normally(fx)
        head_after_first = self.head(fx.repo)
        manifest_after_first = self.manifest(fx.repo / "archive" / "unclassified" / fx.attempt)

        second = self.archive_normally(fx)

        self.assertEqual(self.head(fx.repo), head_after_first, "a second identical archive must make no commit")
        self.assertIn("ARCHIVE ALREADY UP TO DATE", second.stdout)
        manifest_after_second = self.manifest(fx.repo / "archive" / "unclassified" / fx.attempt)
        self.assertEqual(manifest_after_second["copied_files"], manifest_after_first["copied_files"])
        self.assertNotEqual(first.stdout, "")  # sanity: the first call did write something

    def test_existing_destination_from_a_genuinely_different_run_is_refused_not_overwritten(self):
        fx = self.build()
        self.archive_normally(fx)
        dest = fx.repo / "archive" / "unclassified" / fx.attempt
        manifest_before = self.manifest(dest)
        head_before = self.head(fx.repo)

        # A second, unrelated Run whose state happens to produce the same
        # attempt id (same state-root basename) but different run identity,
        # consistently across every source so this reaches the destination-
        # conflict check rather than the cross-source agreement check.
        new_run_id = "RUN-GENUINELY-DIFFERENT-0000000000000001"
        for path in (
            fx.state / "operator-summary.json",
            fx.state / "operator-plan.json",
            fx.state / "checkouts" / CHECKOUT / "run.json",
        ):
            payload = json.loads(path.read_text())
            payload["run_id"] = new_run_id
            path.write_text(json.dumps(payload))

        code, out, err = self.run_wrapper(fx)
        self.assertNotEqual(code, 0)
        self.assertIn("different content", err)
        self.assertEqual(self.head(fx.repo), head_before)
        self.assertEqual(self.manifest(dest), manifest_before)

    def test_unrelated_dirty_path_elsewhere_blocks_archiving_and_is_left_untouched(self):
        fx = self.build()
        unrelated = fx.repo / "unrelated-note.txt"
        unrelated.write_text("someone else's work in progress\n")
        before = self.head(fx.repo)

        code, out, err = self.run_wrapper(fx)

        self.assertNotEqual(code, 0)
        self.assertIn("not clean outside the archive destination", err)
        self.assertEqual(unrelated.read_text(), "someone else's work in progress\n")
        self.assertEqual(self.head(fx.repo), before)
        self.assertFalse((fx.repo / "archive" / "unclassified" / fx.attempt).exists())
        # The unrelated dirty path must remain exactly as it was: still
        # untracked, not swept into any commit, not reverted.
        status = git(fx.repo, "status", "--porcelain")
        self.assertIn("unrelated-note.txt", status)

    def test_stale_staging_from_a_prior_killed_attempt_does_not_block_a_fresh_archive(self):
        fx = self.build()
        staging = fx.repo / ".wfc-archive-staging" / fx.attempt
        staging.mkdir(parents=True)
        (staging / "stale.txt").write_text("leftover from a killed attempt\n")

        self.archive_normally(fx)
        dest = fx.repo / "archive" / "unclassified" / fx.attempt
        self.assertTrue((dest / "manifest.json").is_file())
        self.assertFalse(staging.exists(), "a fresh attempt must clear stale staging, not merge with it")

    def test_unauthorized_pre_staged_index_state_is_refused(self):
        fx = self.build()
        (fx.repo / "someone-elses-change.txt").write_text("not an archive\n")
        git(fx.repo, "add", "someone-elses-change.txt")
        before = self.head(fx.repo)

        code, out, err = self.run_wrapper(fx)

        self.assertNotEqual(code, 0)
        self.assertIn("pre-existing staged state", err)
        self.assertEqual(self.head(fx.repo), before)
        self.assertEqual(git(fx.repo, "diff", "--cached", "--name-only").strip(), "someone-elses-change.txt")
        self.assertFalse((fx.repo / "archive" / "unclassified" / fx.attempt).exists())


if __name__ == "__main__":
    unittest.main()

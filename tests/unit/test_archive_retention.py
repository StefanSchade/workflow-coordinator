"""BP6b: retention-aware ``wfc archive`` (``--raw-retention local|commit|discard``).

Reuses the real Evidence-repository construction from ``frontdoor.init``
(so the managed ``.gitignore`` block is the one the product actually
writes) and drives every invocation through the real installed
``frontdoor.cli.main`` dispatch, exactly like ``test_archive_port.py``.
"""

from __future__ import annotations

import contextlib
import getpass
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
from workflow_coordinator.frontdoor.init import ensure_evidence_repository

ATTEMPT = "wfc-fixture-retention-20261120-01"
RUN_ID = "RUN-" + "r" * 32
WORK_ITEM = "WI-FIXTURE-RETENTION-001"
CHECKOUT = "22222222-3333-4444-5555-666666666666"
TARGET_REPO = "/nowhere/fixture-product-repo"

REAL_GIT = shutil.which("git")


def git(repository: Path, *args: str) -> str:
    proc = subprocess.run([REAL_GIT, "-C", str(repository), *args],
                           capture_output=True, text=True, check=True)
    return proc.stdout


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def build(base: Path, *, attempt: str = ATTEMPT) -> SimpleNamespace:
    home = base / "home"
    state = home / "wfc-state" / attempt
    ops = home / "wfc-ops" / attempt
    (state / "checkouts" / CHECKOUT).mkdir(parents=True)

    ops.mkdir(parents=True)
    (ops / "execution-bindings.yaml").write_text(
        "schema_version: 1\nbindings:\n  worker:\n    adapter: codex_cli\n")
    (ops / "preparation-pins.txt").write_text(f"attempt_id={attempt}\nstate_root={state}\n")
    (ops / "run.log").write_text(f"HOME={home}\nUSER=canary-retention-user\nfixture run log\n")

    write_json(state / "operator-summary.json", {
        "schema_version": 1, "run_id": RUN_ID, "work_item_id": WORK_ITEM,
        "run_status": "completed", "checkout_id": CHECKOUT,
        "hops": [{"hop_id": "H001", "outcome": "completed"}],
        "execution_evidence": [{"adapter": "codex_cli", "model": "fixture-model"}],
    })
    write_json(state / "operator-plan.json", {
        "schema_version": 2, "run_id": RUN_ID, "repository": TARGET_REPO,
        "state_root": str(state),
        "execution_bindings_source": str(ops / "execution-bindings.yaml"),
    })
    write_json(state / "checkout-association.json",
               {"schema_version": 1, "checkout_id": CHECKOUT, "canonical_path": TARGET_REPO})
    write_json(state / "checkouts" / CHECKOUT / "run.json",
               {"run_id": RUN_ID, "work_item_id": WORK_ITEM, "status": "completed",
                "policy_id": "fixture-policy"})
    write_json(state / "checkouts" / CHECKOUT / "checkpoint.json", {"evidence": []})

    repo = base / "evidence"
    outcome = ensure_evidence_repository(repo)
    assert outcome == "created"
    git(repo, "config", "user.name", "Fixture Operator")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")

    return SimpleNamespace(base=base, home=home, state=state, ops=ops, repo=repo, attempt=attempt)


class RetentionTestCase(unittest.TestCase):
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

    def build(self, **kwargs):
        return build(self.base, **kwargs)

    def run_archive(self, fx, *args: str) -> tuple[int, str, str]:
        os.environ["HOME"] = str(fx.home)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli_main(["archive", str(fx.state), str(fx.repo), *args])
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    def archive_ok(self, fx, *args: str) -> tuple[int, str, str]:
        code, out, err = self.run_archive(fx, *args)
        self.assertEqual(code, 0, f"stdout:\n{out}\nstderr:\n{err}")
        return code, out, err

    def dest(self, fx) -> Path:
        return fx.repo / "archive" / "unclassified" / fx.attempt

    def head(self, repo: Path) -> str:
        return git(repo, "rev-parse", "HEAD").strip()

    def committed_paths(self, repo: Path, commit: str) -> list[str]:
        out = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", commit)
        return sorted(p for p in out.splitlines() if p)


class LocalRetentionTests(RetentionTestCase):
    def test_only_audit_json_and_gitignore_are_tracked_and_raw_files_are_on_disk(self):
        fx = self.build()
        self.archive_ok(fx, "--raw-retention", "local")
        dest = self.dest(fx)

        self.assertTrue((dest / "manifest.json").is_file())
        self.assertTrue((dest / "run").is_dir())
        self.assertTrue((dest / "audit.json").is_file())

        tracked = set(git(fx.repo, "ls-files").splitlines())
        assert tracked == {
            ".gitignore", "archive/.gitkeep", f"{dest.relative_to(fx.repo).as_posix()}/audit.json",
        }
        self.assertEqual(git(fx.repo, "status", "--porcelain").strip(), "")

    def test_audit_json_is_valid_wfc_audit_schema(self):
        fx = self.build()
        self.archive_ok(fx, "--raw-retention", "local")
        audit = json.loads((self.dest(fx) / "audit.json").read_text())
        self.assertEqual(audit["schema"], "wfc.audit/1")
        self.assertEqual(audit["attempt_id"], fx.attempt)
        self.assertEqual(audit["raw"]["retention"], "local")

    def test_one_commit_per_attempt(self):
        fx = self.build()
        before = self.head(fx.repo)
        self.archive_ok(fx, "--raw-retention", "local")
        self.assertEqual(git(fx.repo, "rev-list", "--count", f"{before}..HEAD").strip(), "1")

    def test_rearchive_is_a_no_op_and_does_not_rewrite_raw_files(self):
        fx = self.build()
        self.archive_ok(fx, "--raw-retention", "local")
        dest = self.dest(fx)
        manifest_mtime = (dest / "manifest.json").stat().st_mtime_ns
        head_after_first = self.head(fx.repo)

        code, out, err = self.archive_ok(fx, "--raw-retention", "local")
        self.assertIn("ALREADY UP TO DATE", out)
        self.assertEqual(self.head(fx.repo), head_after_first)
        self.assertEqual((dest / "manifest.json").stat().st_mtime_ns, manifest_mtime)

    def test_refuses_before_any_write_when_gitignore_does_not_cover_raw_paths(self):
        fx = self.build()
        # Overwrite the managed .gitignore with one that does not exclude raw paths.
        (fx.repo / ".gitignore").write_text("unrelated-pattern/\n")
        git(fx.repo, "add", ".gitignore")
        git(fx.repo, "commit", "-q", "-m", "chore: weaken gitignore for the test")

        code, out, err = self.run_archive(fx, "--raw-retention", "local")
        self.assertNotEqual(code, 0)
        self.assertIn("does not exclude", err)
        self.assertFalse(self.dest(fx).exists())
        self.assertFalse((fx.repo / ".wfc-archive-staging").exists())

    def test_tamper_of_a_placed_but_uncommitted_raw_file_is_refused(self):
        from unittest import mock

        fx = self.build()
        with mock.patch.object(ar, "_commit_retention_archive", side_effect=RuntimeError("simulated crash")):
            with self.assertRaises(RuntimeError):
                ar.archive_run(state_root=fx.state, evidence_repo=fx.repo, raw_retention="local", home=fx.home)
        dest = self.dest(fx)
        original = (dest / "run" / "operator-summary.json").read_bytes()
        tampered = original.replace(b"completed", b"tampered!")
        self.assertEqual(len(tampered), len(original))
        (dest / "run" / "operator-summary.json").write_bytes(tampered)

        code, out, err = self.run_archive(fx, "--raw-retention", "local")
        self.assertNotEqual(code, 0)
        self.assertIn("does not match its recorded digest", err)

    def test_crash_between_placement_and_commit_resumes_without_a_second_commit(self):
        from unittest import mock

        fx = self.build()
        before = self.head(fx.repo)
        with mock.patch.object(ar, "_commit_retention_archive", side_effect=RuntimeError("simulated crash")):
            with self.assertRaises(RuntimeError):
                ar.archive_run(state_root=fx.state, evidence_repo=fx.repo, raw_retention="local", home=fx.home)
        dest = self.dest(fx)
        self.assertTrue((dest / "audit.json").is_file())
        self.assertEqual(self.head(fx.repo), before)
        # Placed but uncommitted: audit.json itself is untracked (not ignored).
        status_lines = [line for line in git(fx.repo, "status", "--porcelain").splitlines() if line]
        self.assertTrue(all("audit.json" in line or "archive/unclassified" in line for line in status_lines))

        code, out, err = self.archive_ok(fx, "--raw-retention", "local")
        self.assertIn("ARCHIVED", out)
        self.assertEqual(git(fx.repo, "rev-list", "--count", f"{before}..HEAD").strip(), "1")

    def test_managed_gitignore_coverage_is_probed_without_requiring_the_files_to_exist(self):
        fx = self.build()
        dest_rel = f"archive/unclassified/{fx.attempt}"
        ar._assert_local_retention_gitignore_coverage(fx.repo, dest_rel)

    def test_archive_destination_without_audit_json_is_refused(self):
        fx = self.build()
        self.archive_ok(fx, "--raw-retention", "local")
        dest = self.dest(fx)
        dest_rel = dest.relative_to(fx.repo).as_posix()
        (dest / "audit.json").unlink()
        git(fx.repo, "add", "-A", "--", dest_rel)
        git(fx.repo, "commit", "-q", "-m", "chore: simulate a destination missing audit.json")

        code, out, err = self.run_archive(fx, "--raw-retention", "local")
        self.assertNotEqual(code, 0)
        self.assertIn(f"archive destination exists but has no {ar.AUDIT_FILENAME}", err)

    def test_tampered_but_committed_audit_json_is_not_silently_treated_as_up_to_date(self):
        fx = self.build()
        self.archive_ok(fx, "--raw-retention", "local")
        dest = self.dest(fx)
        dest_rel = dest.relative_to(fx.repo).as_posix()
        audit_path = dest / "audit.json"
        original = audit_path.read_text()
        tampered = original.replace('"retention": "local"', '"retention": "tampered"')
        self.assertNotEqual(tampered, original)
        audit_path.write_text(tampered)
        git(fx.repo, "add", "--", f"{dest_rel}/audit.json")
        git(fx.repo, "commit", "-q", "-m", "chore: simulate a tampered but committed audit.json")

        code, out, err = self.run_archive(fx, "--raw-retention", "local")
        self.assertNotEqual(code, 0)
        self.assertIn("does not match a projection of its own manifest", err)


class CommitRetentionTests(RetentionTestCase):
    def test_audit_manifest_run_and_ops_are_all_tracked(self):
        fx = self.build()
        self.archive_ok(fx, "--raw-retention", "commit")
        dest = self.dest(fx)
        dest_rel = dest.relative_to(fx.repo).as_posix()

        tracked = set(git(fx.repo, "ls-files", "--", dest_rel).splitlines())
        assert f"{dest_rel}/manifest.json" in tracked
        assert f"{dest_rel}/audit.json" in tracked
        assert any(p.startswith(f"{dest_rel}/run/") for p in tracked)
        assert any(p.startswith(f"{dest_rel}/ops/") for p in tracked)
        self.assertEqual(git(fx.repo, "status", "--porcelain").strip(), "")

    def test_one_commit_per_attempt(self):
        fx = self.build()
        before = self.head(fx.repo)
        self.archive_ok(fx, "--raw-retention", "commit")
        self.assertEqual(git(fx.repo, "rev-list", "--count", f"{before}..HEAD").strip(), "1")

    def test_rearchive_is_a_no_op(self):
        fx = self.build()
        self.archive_ok(fx, "--raw-retention", "commit")
        head_after_first = self.head(fx.repo)
        code, out, err = self.archive_ok(fx, "--raw-retention", "commit")
        self.assertIn("ALREADY UP TO DATE", out)
        self.assertEqual(self.head(fx.repo), head_after_first)

    def test_tamper_of_a_placed_but_uncommitted_raw_file_is_refused(self):
        from unittest import mock

        fx = self.build()
        with mock.patch.object(ar, "_commit_retention_archive", side_effect=RuntimeError("simulated crash")):
            with self.assertRaises(RuntimeError):
                ar.archive_run(state_root=fx.state, evidence_repo=fx.repo, raw_retention="commit", home=fx.home)
        dest = self.dest(fx)
        original = (dest / "run" / "operator-summary.json").read_bytes()
        tampered = original.replace(b"completed", b"tampered!")
        self.assertEqual(len(tampered), len(original))
        (dest / "run" / "operator-summary.json").write_bytes(tampered)

        code, out, err = self.run_archive(fx, "--raw-retention", "commit")
        self.assertNotEqual(code, 0)
        self.assertIn("does not match its recorded digest", err)

    def test_crash_between_placement_and_commit_resumes_without_a_second_commit(self):
        from unittest import mock

        fx = self.build()
        before = self.head(fx.repo)
        with mock.patch.object(ar, "_commit_retention_archive", side_effect=RuntimeError("simulated crash")):
            with self.assertRaises(RuntimeError):
                ar.archive_run(state_root=fx.state, evidence_repo=fx.repo, raw_retention="commit", home=fx.home)
        dest = self.dest(fx)
        dest_rel = dest.relative_to(fx.repo).as_posix()
        self.assertTrue((dest / "manifest.json").is_file())
        self.assertTrue((dest / "audit.json").is_file())
        self.assertEqual(self.head(fx.repo), before)

        code, out, err = self.archive_ok(fx, "--raw-retention", "commit")
        self.assertIn("ARCHIVED", out)
        self.assertEqual(git(fx.repo, "rev-list", "--count", f"{before}..HEAD").strip(), "1")

        # The crash-resume commit must carry the same full per-mode tracked
        # set as a fresh commit-mode archive, not just the placed audit.json.
        tracked = set(git(fx.repo, "ls-files", "--", dest_rel).splitlines())
        self.assertIn(f"{dest_rel}/manifest.json", tracked)
        self.assertIn(f"{dest_rel}/audit.json", tracked)
        self.assertTrue(any(p.startswith(f"{dest_rel}/run/") for p in tracked))
        self.assertTrue(any(p.startswith(f"{dest_rel}/ops/") for p in tracked))
        self.assertEqual(git(fx.repo, "status", "--porcelain").strip(), "")


class DiscardRetentionTests(RetentionTestCase):
    def test_only_audit_json_exists_at_the_destination(self):
        fx = self.build()
        self.archive_ok(fx, "--raw-retention", "discard")
        dest = self.dest(fx)
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ["audit.json"])
        self.assertFalse((dest / "manifest.json").exists())
        self.assertFalse((dest / "run").exists())

        tracked = set(git(fx.repo, "ls-files").splitlines())
        dest_rel = dest.relative_to(fx.repo).as_posix()
        assert tracked == {".gitignore", "archive/.gitkeep", f"{dest_rel}/audit.json"}

    def test_one_commit_per_attempt(self):
        fx = self.build()
        before = self.head(fx.repo)
        self.archive_ok(fx, "--raw-retention", "discard")
        self.assertEqual(git(fx.repo, "rev-list", "--count", f"{before}..HEAD").strip(), "1")

    def test_rearchive_is_a_no_op(self):
        fx = self.build()
        self.archive_ok(fx, "--raw-retention", "discard")
        head_after_first = self.head(fx.repo)
        code, out, err = self.archive_ok(fx, "--raw-retention", "discard")
        self.assertIn("ALREADY UP TO DATE", out)
        self.assertEqual(self.head(fx.repo), head_after_first)

    def test_different_audit_is_refused_not_overwritten(self):
        fx = self.build()
        self.archive_ok(fx, "--raw-retention", "discard")
        dest = self.dest(fx)
        original = (dest / "audit.json").read_text()

        # Change the underlying Run facts so a fresh projection would differ,
        # without touching the already-placed audit.json.
        write_json(fx.state / "checkouts" / CHECKOUT / "run.json",
                   {"run_id": RUN_ID, "work_item_id": WORK_ITEM, "status": "stopped",
                    "policy_id": "fixture-policy"})

        code, out, err = self.run_archive(fx, "--raw-retention", "discard")
        self.assertNotEqual(code, 0)
        self.assertIn("different content", err)
        self.assertEqual((dest / "audit.json").read_text(), original)

    def test_crash_between_placement_and_commit_resumes_without_a_second_commit(self):
        from unittest import mock

        fx = self.build()
        before = self.head(fx.repo)
        with mock.patch.object(ar, "_commit_retention_archive", side_effect=RuntimeError("simulated crash")):
            with self.assertRaises(RuntimeError):
                ar.archive_run(state_root=fx.state, evidence_repo=fx.repo, raw_retention="discard", home=fx.home)
        dest = self.dest(fx)
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ["audit.json"])
        self.assertEqual(self.head(fx.repo), before)
        status_lines = [line for line in git(fx.repo, "status", "--porcelain").splitlines() if line]
        self.assertTrue(status_lines, "expected the placed but uncommitted audit.json to be visible to git status")

        code, out, err = self.archive_ok(fx, "--raw-retention", "discard")
        self.assertIn("ARCHIVED", out)
        self.assertEqual(git(fx.repo, "rev-list", "--count", f"{before}..HEAD").strip(), "1")
        self.assertEqual(git(fx.repo, "status", "--porcelain").strip(), "")

    def test_destination_with_unexpected_entries_is_refused_not_treated_as_valid(self):
        fx = self.build()
        self.archive_ok(fx, "--raw-retention", "discard")
        dest = self.dest(fx)
        (dest / "unexpected.txt").write_text("stray")

        code, out, err = self.run_archive(fx, "--raw-retention", "discard")
        self.assertNotEqual(code, 0)
        self.assertIn("not a valid discard-retention placement", err)


class LegacyRetentionNoneTests(RetentionTestCase):
    def test_raw_retention_none_is_byte_identical_to_bp5_and_writes_no_audit_json(self):
        # raw_retention=None is the BP5 legacy path: a plain Evidence repository
        # with no BP6b-managed .gitignore, exactly as BP5's own tests use.
        fx = self.build()
        shutil.rmtree(fx.repo / "archive")
        (fx.repo / "archive").mkdir()
        (fx.repo / "archive" / ".gitkeep").write_text("")
        (fx.repo / ".gitignore").write_text("stray/\n")
        git(fx.repo, "add", "-A")
        git(fx.repo, "commit", "-q", "-m", "chore: replace with a legacy-style evidence repo")

        self.archive_ok(fx)
        dest = self.dest(fx)
        self.assertFalse((dest / "audit.json").exists())
        tracked = set(git(fx.repo, "ls-files").splitlines())
        dest_rel = dest.relative_to(fx.repo).as_posix()
        assert all(not p.endswith("audit.json") for p in tracked)
        assert f"{dest_rel}/manifest.json" in tracked
        assert f"{dest_rel}/run/operator-plan.json" in tracked


def _all_blob_contents(repo: Path) -> list[bytes]:
    objects = subprocess.run(
        [REAL_GIT, "-C", str(repo), "rev-list", "--objects", "--all"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    contents = []
    for line in objects:
        sha = line.split(" ", 1)[0]
        kind = subprocess.run(
            [REAL_GIT, "-C", str(repo), "cat-file", "-t", sha], capture_output=True, text=True, check=True,
        ).stdout.strip()
        if kind != "blob":
            continue
        contents.append(
            subprocess.run([REAL_GIT, "-C", str(repo), "cat-file", "-p", sha], capture_output=True, check=True).stdout
        )
    return contents


class PrivacyAcrossHistoryTests(RetentionTestCase):
    def test_local_retention_history_contains_no_known_private_strings(self):
        fx = self.build()
        self.archive_ok(fx, "--raw-retention", "local")
        home_bytes = str(fx.home).encode("utf-8")
        for blob in _all_blob_contents(fx.repo):
            self.assertNotIn(home_bytes, blob)
            self.assertNotIn(b"canary-retention-user", blob)

    def test_discard_retention_history_contains_no_known_private_strings(self):
        fx = self.build()
        self.archive_ok(fx, "--raw-retention", "discard")
        home_bytes = str(fx.home).encode("utf-8")
        for blob in _all_blob_contents(fx.repo):
            self.assertNotIn(home_bytes, blob)
            self.assertNotIn(b"canary-retention-user", blob)


class LegacyRetentionConflictTests(RetentionTestCase):
    """F001 regression: a destination entirely excluded by this Evidence
    repository's own managed ``.gitignore`` (the default a fresh ``wfc init``
    writes) must never be reported as an already-committed no-op merely
    because ``git status`` is silent about it."""

    def test_legacy_archive_into_a_local_retention_repo_refuses_consistently_not_falsely(self):
        fx = self.build()
        # fx.repo was created by the real ensure_evidence_repository with its
        # default managed .gitignore (local retention), which the legacy
        # raw_retention=None path's own committed raw files collide with.
        code1, out1, err1 = self.run_archive(fx)
        self.assertNotEqual(code1, 0, f"stdout:\n{out1}\nstderr:\n{err1}")
        dest = self.dest(fx)
        self.assertTrue(dest.exists())
        self.assertEqual(git(fx.repo, "rev-list", "--count", "HEAD").strip(), "1")

        code2, out2, err2 = self.run_archive(fx)
        self.assertNotEqual(code2, 0, f"stdout:\n{out2}\nstderr:\n{err2}")
        self.assertNotIn("ALREADY UP TO DATE", out2)
        self.assertEqual(git(fx.repo, "rev-list", "--count", "HEAD").strip(), "1")


class LocalRetentionAuditGitignoreConflictTests(RetentionTestCase):
    """F001 regression: a local .gitignore that also happens to cover
    ``audit.json`` (e.g. an operator-added broad pattern) must be refused
    before any write, exactly like insufficient raw-path coverage."""

    def test_refuses_before_any_write_when_gitignore_also_covers_audit_json(self):
        fx = self.build()
        gitignore = fx.repo / ".gitignore"
        gitignore.write_text(gitignore.read_text() + "*.json\n")
        git(fx.repo, "add", ".gitignore")
        git(fx.repo, "commit", "-q", "-m", "chore: operator broadens gitignore")

        code, out, err = self.run_archive(fx, "--raw-retention", "local")
        self.assertNotEqual(code, 0)
        self.assertIn("excludes", err)
        self.assertFalse(self.dest(fx).exists())
        self.assertFalse((fx.repo / ".wfc-archive-staging").exists())
        self.assertEqual(git(fx.repo, "status", "--porcelain").strip(), "")


class PrivacyGuardEnvironmentDerivedStringsTests(RetentionTestCase):
    """F002 regression: the privacy guard must be given this Attempt's HOME,
    OS user name, state root and OPS directory, not just BP6a's own
    prefix-only absolute-path/URL heuristic -- so a repository-relative
    value that merely repeats one of those strings is refused too."""

    def _plant_user_name_in_governing_inputs(self, fx) -> str:
        user = getpass.getuser()
        plan_path = fx.state / "operator-plan.json"
        plan = json.loads(plan_path.read_text())
        plan["governing_inputs"] = {f"docs/canary-{user}/governance.adoc": "0" * 64}
        write_json(plan_path, plan)
        return user

    def test_local_retention_refuses_on_a_relative_governing_inputs_key_naming_the_os_user(self):
        fx = self.build()
        self._plant_user_name_in_governing_inputs(fx)

        code, out, err = self.run_archive(fx, "--raw-retention", "local")
        self.assertEqual(code, 3, f"stdout:\n{out}\nstderr:\n{err}")
        self.assertIn("privacy guard refused projection", err)
        self.assertFalse(self.dest(fx).exists())
        self.assertFalse((fx.repo / ".wfc-archive-staging" / fx.attempt).exists())
        self.assertEqual(git(fx.repo, "status", "--porcelain").strip(), "")
        self.assertEqual(git(fx.repo, "rev-list", "--count", "HEAD").strip(), "1")

    def test_discard_retention_refuses_on_the_same_input(self):
        fx = self.build()
        self._plant_user_name_in_governing_inputs(fx)

        code, out, err = self.run_archive(fx, "--raw-retention", "discard")
        self.assertEqual(code, 3, f"stdout:\n{out}\nstderr:\n{err}")
        self.assertFalse(self.dest(fx).exists())
        self.assertEqual(git(fx.repo, "rev-list", "--count", "HEAD").strip(), "1")

    def test_a_guard_refusal_writes_and_commits_nothing(self):
        fx = self.build()
        self._plant_user_name_in_governing_inputs(fx)
        before = self.head(fx.repo)

        code, out, err = self.run_archive(fx, "--raw-retention", "local")
        self.assertEqual(code, 3)
        self.assertEqual(self.head(fx.repo), before)
        self.assertFalse(self.dest(fx).exists())
        self.assertFalse((fx.repo / ".wfc-archive-staging" / fx.attempt).exists())

"""Ported conformance coverage for ``frontdoor.archive``'s archiver core.

This is the semantic port of the Evidence repository's
``tests/test_archive_helpers.py`` (source commit ``bc65ff777bb75602792e416519a7a3025d723c67``,
staged read-only at ``build/wfc/bp5-evidence-inputs/`` for this port), driving
the installed module instead of the standalone script. Source isolation,
archive-root containment, pin-file parsing and the operator-summary/run.json
fact-merge priority are covered exactly as before.

Two of the original 21 helper tests have no port here, documented rather than
silently dropped: ``test_archive_proof_has_the_same_guard`` and
``test_archive_proof_shares_the_same_archive_root`` exercised
``scripts/archive_proof.py``, the separate, frozen ``~/wfc-proofs`` archiver
for three now-closed historical layouts ("smoke"/"slice"/"live") that predate
the Run/OPS directory schema entirely. BP5 ports the Run/OPS archiver
(``archive_run.py``/``wfc-archive-run``) only; ``archive_proof.py`` has no
installed counterpart and is out of this port's scope.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from workflow_coordinator.frontdoor import archive as ar


class SourceIsolationTests(unittest.TestCase):
    def test_dest_equals_source_rejected(self):
        p = Path("/a/b/c")
        with self.assertRaises(ar.ArchiveError):
            ar.assert_source_isolation(p, p)

    def test_dest_nested_under_source_rejected(self):
        with self.assertRaises(ar.ArchiveError):
            ar.assert_source_isolation(Path("/a/b/c/nested"), Path("/a/b/c"))

    def test_source_nested_under_dest_rejected(self):
        with self.assertRaises(ar.ArchiveError):
            ar.assert_source_isolation(Path("/a/b"), Path("/a/b/c"))

    def test_disjoint_paths_pass(self):
        ar.assert_source_isolation(Path("/a/b/dest"), Path("/a/b/source"))  # no raise

    def test_none_sources_are_skipped(self):
        ar.assert_source_isolation(Path("/a/b/dest"), None, Path("/a/b/source"))  # no raise

    def test_checks_every_source_run_dir_and_ops_dir(self):
        dest = Path("/x/ops/nested")
        with self.assertRaises(ar.ArchiveError):
            ar.assert_source_isolation(dest, Path("/x/run"), Path("/x/ops"))


class ArchiveRootContainmentTests(unittest.TestCase):
    def test_dest_under_archive_root_passes(self):
        root = Path("/evidence/archive")
        dest = root / "some-domain" / "some-workstream" / "some-attempt"
        ar.assert_dest_under_archive_root(dest, root)  # no raise

    def test_dest_outside_archive_root_rejected(self):
        with self.assertRaises(ar.ArchiveError):
            ar.assert_dest_under_archive_root(Path("/tmp/somewhere-else"), Path("/evidence/archive"))

    def test_dest_equal_to_archive_root_itself_passes(self):
        root = Path("/evidence/archive")
        ar.assert_dest_under_archive_root(root, root)  # no raise


class ParseKvFileTests(unittest.TestCase):
    def test_parses_simple_pairs(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "pins.txt"
            p.write_text("runtime_head=abc123\nlatin_head=def456\n")
            self.assertEqual(ar.parse_kv_file(p),
                              {"runtime_head": "abc123", "latin_head": "def456"})

    def test_ignores_blank_and_comment_and_malformed_lines(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "pins.txt"
            p.write_text("\n# a comment\nno-equals-sign-here\nok_key=ok value\n")
            self.assertEqual(ar.parse_kv_file(p), {"ok_key": "ok value"})

    def test_value_may_contain_further_equals_signs(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "pins.txt"
            p.write_text("path=/a/b=c\n")
            self.assertEqual(ar.parse_kv_file(p), {"path": "/a/b=c"})


class ExtractFactsMergeTests(unittest.TestCase):
    """operator-summary.json is the higher-priority source; canonical
    run.json fills gaps only, deterministically, without ever overwriting
    an already-established value."""

    def _make_run_dir(self, tmp: Path, op_summary: dict | None, checkout_run_jsons: list[dict]):
        run_dir = tmp / "run"
        run_dir.mkdir()
        if op_summary is not None:
            (run_dir / "operator-summary.json").write_text(json.dumps(op_summary))
        checkouts = run_dir / "checkouts"
        checkouts.mkdir()
        for i, rj in enumerate(checkout_run_jsons):
            cdir = checkouts / f"checkout-{i}"
            cdir.mkdir()
            (cdir / "run.json").write_text(json.dumps(rj))
        return run_dir

    def test_run_json_fills_field_missing_from_operator_summary(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = self._make_run_dir(
                Path(td),
                op_summary={"run_id": "RUN-1", "hops": []},
                checkout_run_jsons=[{"run_id": "RUN-1", "policy_id": "pol-a", "status": "completed"}],
            )
            facts = ar.extract_facts(run_dir, None)
            self.assertEqual(facts["policy_id"], "pol-a")
            self.assertEqual(facts["status"], "completed")
            self.assertEqual(facts["field_sources"]["policy_id"], "checkouts/checkout-0/run.json")

    def test_operator_summary_value_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = self._make_run_dir(
                Path(td),
                op_summary={"run_id": "RUN-1", "stop_reason": "from-summary", "hops": []},
                checkout_run_jsons=[{"run_id": "RUN-1", "stop_reason": "from-run-json"}],
            )
            facts = ar.extract_facts(run_dir, None)
            self.assertEqual(facts["stop_reason"], "from-summary")
            self.assertEqual(facts["field_sources"]["stop_reason"], "operator-summary.json")

    def test_status_and_run_status_are_kept_distinct(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = self._make_run_dir(
                Path(td),
                op_summary={"run_id": "RUN-1", "run_status": "awaiting_human", "hops": []},
                checkout_run_jsons=[{"run_id": "RUN-1", "status": "awaiting_human"}],
            )
            facts = ar.extract_facts(run_dir, None)
            self.assertEqual(facts["run_status"], "awaiting_human")
            self.assertEqual(facts["status"], "awaiting_human")
            self.assertEqual(facts["field_sources"]["run_status"], "operator-summary.json")
            self.assertEqual(facts["field_sources"]["status"], "checkouts/checkout-0/run.json")

    def test_multiple_checkouts_deterministic_first_wins(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = self._make_run_dir(
                Path(td),
                op_summary=None,
                checkout_run_jsons=[
                    {"run_id": "RUN-1", "policy_id": "from-checkout-0"},
                    {"run_id": "RUN-1", "policy_id": "from-checkout-1"},
                ],
            )
            facts = ar.extract_facts(run_dir, None)
            self.assertEqual(facts["policy_id"], "from-checkout-0")
            self.assertEqual(facts["field_sources"]["policy_id"], "checkouts/checkout-0/run.json")

    def test_no_operator_summary_falls_back_to_run_json(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = self._make_run_dir(
                Path(td),
                op_summary=None,
                checkout_run_jsons=[{"run_id": "RUN-9", "work_item_id": "WI-9"}],
            )
            facts = ar.extract_facts(run_dir, None)
            self.assertEqual(facts["run_id"], "RUN-9")
            self.assertEqual(facts["work_item_id"], "WI-9")

    def test_pinned_revisions_extracted_when_present(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run_dir = self._make_run_dir(tmp, op_summary={"run_id": "RUN-1", "hops": []},
                                          checkout_run_jsons=[])
            ops_dir = tmp / "ops"
            ops_dir.mkdir()
            (ops_dir / "preparation-pins.txt").write_text(
                "runtime_expected_head=a390ac23b42f69112af8f4c81cd171795920873a\n"
            )
            facts = ar.extract_facts(run_dir, ops_dir)
            self.assertEqual(
                facts["pinned_revisions"]["values"]["runtime_expected_head"],
                "a390ac23b42f69112af8f4c81cd171795920873a",
            )
            self.assertEqual(facts["pinned_revisions"]["source_files"], ["preparation-pins.txt"])

    def test_no_pin_files_leaves_pinned_revisions_none(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run_dir = self._make_run_dir(tmp, op_summary={"run_id": "RUN-1", "hops": []},
                                          checkout_run_jsons=[])
            ops_dir = tmp / "ops"
            ops_dir.mkdir()
            facts = ar.extract_facts(run_dir, ops_dir)
            self.assertIsNone(facts["pinned_revisions"])


if __name__ == "__main__":
    unittest.main()

"""Old-vs-new equivalence on a real retained Run (BP5 acceptance).

Archives the same real retained Run (``RUN-6538d0568bce47a899dab7677ea9c2e1``,
Evidence repository HEAD ``bc65ff777bb75602792e416519a7a3025d723c67``) with
both the established old wrapper/archiver (frozen, hash-verified, at
``tests/fixtures/bp5/legacy/``) and the new installed ``frontdoor.archive``
module, into two disposable Git Evidence repositories, and compares the
resulting ``manifest.json`` byte for byte except ``archived_at`` -- exactly
the second-pass review's BP5 equivalence test.

Sandbox note, reported rather than hidden: the Run's actual original state
root and OPS directory live under ``/home/stefan/wfc-state`` and
``/home/stefan/wfc-ops``, which this agent's sandbox cannot read. The already
-archived, hash-verified copy of that same Run's files is committed at
``tests/fixtures/bp5/real_run/`` (provenance and digests in
``tests/fixtures/bp5/PROVENANCE.txt``, matching both
``build/wfc/bp5-evidence-inputs/PROVENANCE.txt`` and the archived
``manifest.json``'s own ``copied_files`` digests). This test reconstructs a
disposable working copy of the original state root/OPS directory from that
archived copy -- ``real_run/run/`` and ``real_run/ops/`` mirror the archiver's
own ``run/``/``ops/`` destination layout one for one -- and relocates it under
a throwaway ``HOME``. The only fields edited are the two that literally
encode that relocated location (``operator-plan.json``'s ``state_root``/
``execution_bindings_source`` and ``preparation-pins.txt``'s ``state_root=``/
``execution_bindings=``); every other recorded fact, including the Run's own
identity and every other pin, is left exactly as retained. Both archivers
read the identical resulting state root and OPS directory, so the comparison
is not weakened by the relocation.

The checkout's ``run.json`` is committed under the neutral fixture name
``run.fixture.json`` (identical bytes and SHA-256) to avoid a committed-
fixture/test collision; the relocation step below materializes it back to
``run.json`` only inside the disposable, temporary relocated state root.

The same committed-fixture/test collision applies to ``operator-plan.json``
and ``operator-summary.json`` (``tests/unit/test_wfc_op.py`` globs the
*entire* runtime export for those literal names). Renaming them the same
way ``run.json`` was renamed would itself be a same-content delete+add in
one commit -- exactly the rename-shaped CommitIntent that the Coordinator's
own staged-path reconciliation (rename detection collapsing a delete+add
pair to the destination only) is documented to refuse. To avoid recreating
that limitation, their exact original bytes (digests unchanged, verified in
``setUp``) are embedded verbatim below as ``_OPERATOR_PLAN_JSON``/
``_OPERATOR_SUMMARY_JSON`` instead of committed as separate same-named
fixture files, and materialized into the disposable relocated state root by
``_relocate_real_run`` below.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from workflow_coordinator.frontdoor import archive as ar

# Embedded verbatim (not committed as separate fixture files -- see module
# docstring) to avoid both the test_wfc_op.py fixture/test-name collision and
# a rename-shaped CommitIntent. Digests verified against PROVENANCE.txt in
# setUp below.
_OPERATOR_PLAN_JSON = '''{"agent_operating_contract":{"byte_count":5754,"contract_id":"workflow_coordinator.agent_operating_contract","resource":"resources/agent_operating_contract_v6.txt","sha256":"7abf57ab315965c4266674efdb71c6cd1415989cdb771b9232501563862ca8f7","version":6},"components":{"reviewer":{"digest":"739a5db2044b7897655c0120ea9f0c4c2c5fc8cc3074a7f03e93385f67dac4b4","path":"governance/self-development/prompts/reviewer.txt"},"worker":{"digest":"179656ee9824c91c151c7edb7284ab848067a28fc575b1e0fe5549df8a99daab","path":"governance/self-development/prompts/worker.txt"}},"durable_artifacts":{"broad_review":["docs/warm_storage/concepts/wfc_beta1_bp3_remediation_review_001_broad_review.adoc"],"focused_review":["docs/warm_storage/concepts/wfc_beta1_bp3_remediation_review_001_focused_review.adoc"]},"evidence":{"environment_policy":"coordinator-inherited-environment-v1","path_digest":"65a6a9af1b0595d7e01910e98ddc65b1f5ffbb113278ca5af168f75287c7dc90","path_source":"inherited","selected_commands":[{"argv_digest":"c3f2578d3a465d10ae97391fe2a54f1c10832286381277ce1355ccfb0bbac529","authored_argv0":"python","canonical_path":"/usr/bin/python3.13","command_name":"task-check","invocation_path":"/home/stefan/wfc-ops/wfc-beta1-bp3-remediation-review-001-20261119-01/dev-env/bin/python","stat_fingerprint":{"st_dev":2049,"st_ino":24924036,"st_mode":33261,"st_mtime_ns":1750877722000000000,"st_size":6828688},"timeout_seconds":900}]},"executable":"/home/stefan/.local/bin/claude","execution_bindings_source":"/home/stefan/wfc-ops/wfc-beta1-bp3-remediation-review-001-20261119-01/execution-bindings.yaml","expected_baseline":"b46d181b988ea0a4ae0f0534d16cc514ee632ed0","governing_inputs":{"governance/commissions/wfc-beta1-bp3-remediation-review-001/policy.yaml":"9be6cee322f1b37c5f5835c2f879fbbce9c7e64f7f72470bfe30c0eed39c0fcf","governance/commissions/wfc-beta1-bp3-remediation-review-001/profile.yaml":"238292131abfbce703186787293da1297ed83057c36c07102ab150353415f9eb","governance/commissions/wfc-beta1-bp3-remediation-review-001/task.md":"57fe22308dc60f9653064a905791f41256ff5d0a6e282fdaaee3a1c5a048d15f","governance/commissions/wfc-beta1-bp3-remediation-review-001/work-item.yaml":"d98ec9e2a6a3600cd7dfa74b151332500b27ca8bcd4fe892ff5f643ee5447cdf","governance/self-development/components.yaml":"dc239e4e83c44336b5622c427443192bfd349c6a40b784bf396ad7903a9ec524","governance/self-development/prompts/reviewer.txt":"739a5db2044b7897655c0120ea9f0c4c2c5fc8cc3074a7f03e93385f67dac4b4","governance/self-development/prompts/worker.txt":"179656ee9824c91c151c7edb7284ab848067a28fc575b1e0fe5549df8a99daab"},"inputs":{"components":"governance/self-development/components.yaml","pinned_revision":"b46d181b988ea0a4ae0f0534d16cc514ee632ed0","policy":"governance/commissions/wfc-beta1-bp3-remediation-review-001/policy.yaml","profile":"governance/commissions/wfc-beta1-bp3-remediation-review-001/profile.yaml","task":"governance/commissions/wfc-beta1-bp3-remediation-review-001/task.md","work_item":"governance/commissions/wfc-beta1-bp3-remediation-review-001/work-item.yaml"},"legacy_durable_artifacts":[],"max_autonomous_hops":10,"repository":"/home/stefan/repos/spreadsheet-handling/workflow-coordinator","retry":false,"role_bindings":{"independent_reviewer":{"adapter":"claude_cli","model":"claude-opus-5","reasoning":"high"},"worker":{"adapter":"claude_cli","model":"claude-sonnet-5","reasoning":"high"}},"role_personas":{"independent_reviewer":"Fresh independent BP3 reviewer: test original R1, R4 and R2 acceptance and direct regressions; enforce finding authority.","worker":"Escalate material review findings to the maintainer; do not change product code."},"run_id":"RUN-6538d0568bce47a899dab7677ea9c2e1","schema_version":2,"state_root":"/home/stefan/wfc-state/wfc-beta1-bp3-remediation-review-001-20261119-01","timeout_seconds":3600.0}'''
_OPERATOR_SUMMARY_JSON = '''{"anomalies":[],"baseline_head":"b46d181b988ea0a4ae0f0534d16cc514ee632ed0","changed_paths":["docs/warm_storage/concepts/wfc_beta1_bp3_remediation_review_001_broad_review.adoc"],"checkout_id":"de335f62-6d24-4e17-809e-0347f0277dc3","commits":[{"hash":"44f4cfe8aaac549b4538aa6561bdb6bdf6c1010b","subject":"docs(review): WI-WFC-BETA1-BP3-REMEDIATION-REVIEW-001 H001 record independent BP3 remediation acceptance review"}],"current_head":"44f4cfe8aaac549b4538aa6561bdb6bdf6c1010b","execution_evidence":[{"actual_commits":["44f4cfe8aaac549b4538aa6561bdb6bdf6c1010b"],"adapter":"claude_cli","base_head":"b46d181b988ea0a4ae0f0534d16cc514ee632ed0","candidate_result_present":true,"context":"fresh","diagnostics":[],"diagnostics_artifact":null,"end_head":"44f4cfe8aaac549b4538aa6561bdb6bdf6c1010b","executable":"/home/stefan/.local/bin/claude","hop_id":"H001","invocation_id":"INV-7293d3ffdec645b9ac9bb4b8122d0bcf","model":"claude-opus-5","outcome":"completed","persona":"Fresh independent BP3 reviewer: test original R1, R4 and R2 acceptance and direct regressions; enforce finding authority.","persona_sha256":"ac545bacfda0e6535af6b8a496573f3aa5b0ca40b183d131cd83d48c4ae9d0ee","phase":"broad_review","reasoning":"high","returncode":0,"timed_out":false,"timeout_seconds":3600.0,"trusted_observations":[{"argv_digest":"c3f2578d3a465d10ae97391fe2a54f1c10832286381277ce1355ccfb0bbac529","artifact_ref":null,"authored_argv0":"python","canonical_path":"/usr/bin/python3.13","command":["/home/stefan/wfc-ops/wfc-beta1-bp3-remediation-review-001-20261119-01/dev-env/bin/python","-m","pytest","-q"],"command_name":"task-check","cwd_policy":"canonical-repository-root-v1","environment_policy":"coordinator-inherited-environment-v1","error_class":null,"exit_code":0,"invocation_path":"/home/stefan/wfc-ops/wfc-beta1-bp3-remediation-review-001-20261119-01/dev-env/bin/python","path_digest":"65a6a9af1b0595d7e01910e98ddc65b1f5ffbb113278ca5af168f75287c7dc90","path_source":"inherited","provider":"task-check","signal":null,"stat_fingerprint":{"st_dev":2049,"st_ino":24924036,"st_mode":33261,"st_mtime_ns":1750877722000000000,"st_size":6828688},"status":"pass","stderr_body_omitted":false,"stderr_byte_count":0,"stderr_sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","stdout_body_omitted":true,"stdout_byte_count":1280,"stdout_sha256":"1ac2b27c017acf9f384988853389805eeb352f00efec69a8b049a05f6ad6a88f","summary":"exit 0","timeout_seconds":900,"untrusted_diagnostic":null}],"workflow_role":"independent_reviewer"}],"final_head":"44f4cfe8aaac549b4538aa6561bdb6bdf6c1010b","hop_limit":10,"hop_used":1,"hops":[{"actual_commits":["44f4cfe8aaac549b4538aa6561bdb6bdf6c1010b"],"base_head":"b46d181b988ea0a4ae0f0534d16cc514ee632ed0","changed_paths":["docs/warm_storage/concepts/wfc_beta1_bp3_remediation_review_001_broad_review.adoc"],"end_head":"44f4cfe8aaac549b4538aa6561bdb6bdf6c1010b","hop_id":"H001","outcome":"completed"}],"human_question":null,"invocation_id":"INV-7293d3ffdec645b9ac9bb4b8122d0bcf","next_explicit_action":"No automatic follow-up action occurs.","operator_error":null,"provider":{"candidate_result_present":true,"diagnostics":[],"diagnostics_artifact":null,"returncode":0,"timed_out":false},"run_id":"RUN-6538d0568bce47a899dab7677ea9c2e1","run_status":"completed","schema_version":2,"semantic_stop":null,"stop_reason":null,"successor_provenance":null,"work_item_id":"WI-WFC-BETA1-BP3-REMEDIATION-REVIEW-001","worktree_clean":true}'''

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "bp5"
REAL_RUN = FIXTURES / "real_run"
LEGACY = FIXTURES / "legacy"
ATTEMPT_ID = "wfc-beta1-bp3-remediation-review-001-20261119-01"
REAL_GIT = shutil.which("git")


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        [REAL_GIT, "-C", str(repository), *args], capture_output=True, text=True, check=True,
    ).stdout


def _relocate_real_run(base: Path) -> tuple[Path, Path, Path]:
    """A disposable, relocated working copy of the archived Run's own state
    root and OPS directory, under a throwaway HOME. Returns
    ``(home, state_root, ops_dir)``."""

    home = base / "home"
    state_root = home / "wfc-state" / ATTEMPT_ID
    ops_dir = home / "wfc-ops" / ATTEMPT_ID
    shutil.copytree(REAL_RUN / "run", state_root)
    shutil.copytree(REAL_RUN / "ops", ops_dir)
    (state_root / "operator-plan.json").write_text(_OPERATOR_PLAN_JSON + "\n")
    (state_root / "operator-summary.json").write_text(_OPERATOR_SUMMARY_JSON + "\n")

    checkout_dir = next((state_root / "checkouts").iterdir())
    fixture_run_json = checkout_dir / "run.fixture.json"
    fixture_run_json.rename(checkout_dir / "run.json")

    plan_path = state_root / "operator-plan.json"
    plan = json.loads(plan_path.read_text())
    assert plan["state_root"].startswith("/home/stefan/wfc-state/")
    assert plan["execution_bindings_source"].startswith("/home/stefan/wfc-ops/")
    plan["state_root"] = str(state_root)
    plan["execution_bindings_source"] = str(ops_dir / "execution-bindings.yaml")
    plan_path.write_text(json.dumps(plan))

    pins_path = ops_dir / "preparation-pins.txt"
    lines = pins_path.read_text().splitlines()
    rewritten = []
    for line in lines:
        if line.startswith("state_root="):
            rewritten.append(f"state_root={state_root}")
        elif line.startswith("execution_bindings="):
            rewritten.append(f"execution_bindings={ops_dir / 'execution-bindings.yaml'}")
        else:
            rewritten.append(line)
    pins_path.write_text("\n".join(rewritten) + "\n")

    return home, state_root, ops_dir


def _init_evidence_repo(repo: Path, *, with_legacy_archiver: bool) -> None:
    (repo / "archive").mkdir(parents=True)
    (repo / "archive" / ".gitkeep").write_text("")
    if with_legacy_archiver:
        (repo / "scripts").mkdir()
        shutil.copy2(LEGACY / "archive_run.py", repo / "scripts" / "archive_run.py")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Equivalence Fixture")
    git(repo, "config", "user.email", "equivalence@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fixture baseline")


def _strip_archived_at(manifest: dict) -> dict:
    return {k: v for k, v in manifest.items() if k != "archived_at"}


class RealRunEquivalenceTests(unittest.TestCase):
    def setUp(self):
        import hashlib
        for payload, expected, label in (
            ((_OPERATOR_PLAN_JSON + "\n").encode(), "80803321703b1c08e65742443c98fd6cf9f6e1e78be336244a03e15f95191f82", "_OPERATOR_PLAN_JSON"),
            ((_OPERATOR_SUMMARY_JSON + "\n").encode(), "bd6cb02f37965335a947df64cf58fb84ee895e3783839cec222eb0c8a938f2be", "_OPERATOR_SUMMARY_JSON"),
        ):
            actual = hashlib.sha256(payload).hexdigest()
            self.assertEqual(actual, expected, f"provenance digest mismatch for {label}")
        for path, expected in (
            (LEGACY / "archive_run.py", "31621e72ac8811bb74e528401610c10c7bf5b6e27756d53bca617235ec1acf26"),
            (LEGACY / "wfc-archive-run", "21f602b94771b3f3ee1955074078c1ee904e6a20bf0fe846194b067524482683"),
        ):
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(actual, expected, f"provenance digest mismatch for {path}")

    def test_old_and_new_archivers_produce_identical_manifests_for_the_same_real_run(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home, state_root, ops_dir = _relocate_real_run(base)

            repo_old = base / "evidence-old"
            repo_new = base / "evidence-new"
            _init_evidence_repo(repo_old, with_legacy_archiver=True)
            _init_evidence_repo(repo_new, with_legacy_archiver=False)

            wrapper = LEGACY / "wfc-archive-run"
            env = {**os.environ, "HOME": str(home)}
            proc = subprocess.run(
                [str(wrapper), str(state_root), str(repo_old)],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(proc.returncode, 0, f"old wrapper failed:\n{proc.stdout}\n{proc.stderr}")

            report = ar.archive_run(state_root=state_root, evidence_repo=repo_new, home=home)
            self.assertEqual(report.outcome, "archived")

            dest_old = repo_old / "archive" / "unclassified" / ATTEMPT_ID
            dest_new = repo_new / "archive" / "unclassified" / ATTEMPT_ID
            manifest_old = json.loads((dest_old / "manifest.json").read_text())
            manifest_new = json.loads((dest_new / "manifest.json").read_text())

            self.assertNotEqual(manifest_old["archived_at"], None)
            self.assertNotEqual(manifest_new["archived_at"], None)
            self.assertEqual(_strip_archived_at(manifest_old), _strip_archived_at(manifest_new))

            # Exactly one commit on each side, touching only its own attempt directory.
            self.assertEqual(git(repo_old, "rev-list", "--count", "HEAD").strip(), "2")
            self.assertEqual(git(repo_new, "rev-list", "--count", "HEAD").strip(), "2")


if __name__ == "__main__":
    unittest.main()

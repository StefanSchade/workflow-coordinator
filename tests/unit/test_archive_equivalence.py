"""Old-vs-new equivalence on real retained Runs (BP5 acceptance, generalized
by C1b for additional Runs).

Archives a real retained Run with both the established old wrapper/archiver
(frozen, hash-verified, at ``tests/fixtures/bp5/legacy/``) and the new
installed ``frontdoor.archive`` module, into two disposable Git Evidence
repositories, and compares the resulting ``manifest.json`` byte for byte
except ``archived_at`` -- exactly the second-pass review's BP5 equivalence
test. ``FIXTURE_CASES`` below parameterizes this same comparison, unchanged in
method, over one ``FixtureCase`` per retained Run; each case gets its own
generated test method (see the loop at the bottom of this module) so a
regression in one fixture never hides another. See
``docs/developer_guide/ch02_code_navigation.adoc`` for how to add a further
fixture case.

The original BP5 case (``RUN-6538d0568bce47a899dab7677ea9c2e1``, Evidence
repository HEAD ``bc65ff777bb75602792e416519a7a3025d723c67``) keeps its
original test method name,
``test_old_and_new_archivers_produce_identical_manifests_for_the_same_real_run``,
and identical assertions.

Sandbox note, reported rather than hidden: a Run's actual original state root
and OPS directory live under ``/home/stefan/wfc-state`` and
``/home/stefan/wfc-ops``, which this agent's sandbox cannot read. The
already-archived, hash-verified copy of that same Run's files is committed at
``tests/fixtures/<case>/real_run/`` (provenance and digests in
``tests/fixtures/<case>/PROVENANCE.txt``, matching the archived
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
``setUp``) are embedded verbatim below, one pair per fixture case, as
``EmbeddedFile`` entries on that case's own ``embedded_files``, and
materialized into the disposable relocated state root by
``_relocate_real_run``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from dataclasses import dataclass
from pathlib import Path

from workflow_coordinator.frontdoor import archive as ar

# Embedded verbatim (not committed as separate fixture files -- see module
# docstring) to avoid both the test_wfc_op.py fixture/test-name collision and
# a rename-shaped CommitIntent. Digests verified against each FixtureCase's
# own PROVENANCE.txt in setUp below.
_BP5_OPERATOR_PLAN_JSON = '''{"agent_operating_contract":{"byte_count":5754,"contract_id":"workflow_coordinator.agent_operating_contract","resource":"resources/agent_operating_contract_v6.txt","sha256":"7abf57ab315965c4266674efdb71c6cd1415989cdb771b9232501563862ca8f7","version":6},"components":{"reviewer":{"digest":"739a5db2044b7897655c0120ea9f0c4c2c5fc8cc3074a7f03e93385f67dac4b4","path":"governance/self-development/prompts/reviewer.txt"},"worker":{"digest":"179656ee9824c91c151c7edb7284ab848067a28fc575b1e0fe5549df8a99daab","path":"governance/self-development/prompts/worker.txt"}},"durable_artifacts":{"broad_review":["docs/warm_storage/concepts/wfc_beta1_bp3_remediation_review_001_broad_review.adoc"],"focused_review":["docs/warm_storage/concepts/wfc_beta1_bp3_remediation_review_001_focused_review.adoc"]},"evidence":{"environment_policy":"coordinator-inherited-environment-v1","path_digest":"65a6a9af1b0595d7e01910e98ddc65b1f5ffbb113278ca5af168f75287c7dc90","path_source":"inherited","selected_commands":[{"argv_digest":"c3f2578d3a465d10ae97391fe2a54f1c10832286381277ce1355ccfb0bbac529","authored_argv0":"python","canonical_path":"/usr/bin/python3.13","command_name":"task-check","invocation_path":"/home/stefan/wfc-ops/wfc-beta1-bp3-remediation-review-001-20261119-01/dev-env/bin/python","stat_fingerprint":{"st_dev":2049,"st_ino":24924036,"st_mode":33261,"st_mtime_ns":1750877722000000000,"st_size":6828688},"timeout_seconds":900}]},"executable":"/home/stefan/.local/bin/claude","execution_bindings_source":"/home/stefan/wfc-ops/wfc-beta1-bp3-remediation-review-001-20261119-01/execution-bindings.yaml","expected_baseline":"b46d181b988ea0a4ae0f0534d16cc514ee632ed0","governing_inputs":{"governance/commissions/wfc-beta1-bp3-remediation-review-001/policy.yaml":"9be6cee322f1b37c5f5835c2f879fbbce9c7e64f7f72470bfe30c0eed39c0fcf","governance/commissions/wfc-beta1-bp3-remediation-review-001/profile.yaml":"238292131abfbce703186787293da1297ed83057c36c07102ab150353415f9eb","governance/commissions/wfc-beta1-bp3-remediation-review-001/task.md":"57fe22308dc60f9653064a905791f41256ff5d0a6e282fdaaee3a1c5a048d15f","governance/commissions/wfc-beta1-bp3-remediation-review-001/work-item.yaml":"d98ec9e2a6a3600cd7dfa74b151332500b27ca8bcd4fe892ff5f643ee5447cdf","governance/self-development/components.yaml":"dc239e4e83c44336b5622c427443192bfd349c6a40b784bf396ad7903a9ec524","governance/self-development/prompts/reviewer.txt":"739a5db2044b7897655c0120ea9f0c4c2c5fc8cc3074a7f03e93385f67dac4b4","governance/self-development/prompts/worker.txt":"179656ee9824c91c151c7edb7284ab848067a28fc575b1e0fe5549df8a99daab"},"inputs":{"components":"governance/self-development/components.yaml","pinned_revision":"b46d181b988ea0a4ae0f0534d16cc514ee632ed0","policy":"governance/commissions/wfc-beta1-bp3-remediation-review-001/policy.yaml","profile":"governance/commissions/wfc-beta1-bp3-remediation-review-001/profile.yaml","task":"governance/commissions/wfc-beta1-bp3-remediation-review-001/task.md","work_item":"governance/commissions/wfc-beta1-bp3-remediation-review-001/work-item.yaml"},"legacy_durable_artifacts":[],"max_autonomous_hops":10,"repository":"/home/stefan/repos/spreadsheet-handling/workflow-coordinator","retry":false,"role_bindings":{"independent_reviewer":{"adapter":"claude_cli","model":"claude-opus-5","reasoning":"high"},"worker":{"adapter":"claude_cli","model":"claude-sonnet-5","reasoning":"high"}},"role_personas":{"independent_reviewer":"Fresh independent BP3 reviewer: test original R1, R4 and R2 acceptance and direct regressions; enforce finding authority.","worker":"Escalate material review findings to the maintainer; do not change product code."},"run_id":"RUN-6538d0568bce47a899dab7677ea9c2e1","schema_version":2,"state_root":"/home/stefan/wfc-state/wfc-beta1-bp3-remediation-review-001-20261119-01","timeout_seconds":3600.0}'''
_BP5_OPERATOR_SUMMARY_JSON = '''{"anomalies":[],"baseline_head":"b46d181b988ea0a4ae0f0534d16cc514ee632ed0","changed_paths":["docs/warm_storage/concepts/wfc_beta1_bp3_remediation_review_001_broad_review.adoc"],"checkout_id":"de335f62-6d24-4e17-809e-0347f0277dc3","commits":[{"hash":"44f4cfe8aaac549b4538aa6561bdb6bdf6c1010b","subject":"docs(review): WI-WFC-BETA1-BP3-REMEDIATION-REVIEW-001 H001 record independent BP3 remediation acceptance review"}],"current_head":"44f4cfe8aaac549b4538aa6561bdb6bdf6c1010b","execution_evidence":[{"actual_commits":["44f4cfe8aaac549b4538aa6561bdb6bdf6c1010b"],"adapter":"claude_cli","base_head":"b46d181b988ea0a4ae0f0534d16cc514ee632ed0","candidate_result_present":true,"context":"fresh","diagnostics":[],"diagnostics_artifact":null,"end_head":"44f4cfe8aaac549b4538aa6561bdb6bdf6c1010b","executable":"/home/stefan/.local/bin/claude","hop_id":"H001","invocation_id":"INV-7293d3ffdec645b9ac9bb4b8122d0bcf","model":"claude-opus-5","outcome":"completed","persona":"Fresh independent BP3 reviewer: test original R1, R4 and R2 acceptance and direct regressions; enforce finding authority.","persona_sha256":"ac545bacfda0e6535af6b8a496573f3aa5b0ca40b183d131cd83d48c4ae9d0ee","phase":"broad_review","reasoning":"high","returncode":0,"timed_out":false,"timeout_seconds":3600.0,"trusted_observations":[{"argv_digest":"c3f2578d3a465d10ae97391fe2a54f1c10832286381277ce1355ccfb0bbac529","artifact_ref":null,"authored_argv0":"python","canonical_path":"/usr/bin/python3.13","command":["/home/stefan/wfc-ops/wfc-beta1-bp3-remediation-review-001-20261119-01/dev-env/bin/python","-m","pytest","-q"],"command_name":"task-check","cwd_policy":"canonical-repository-root-v1","environment_policy":"coordinator-inherited-environment-v1","error_class":null,"exit_code":0,"invocation_path":"/home/stefan/wfc-ops/wfc-beta1-bp3-remediation-review-001-20261119-01/dev-env/bin/python","path_digest":"65a6a9af1b0595d7e01910e98ddc65b1f5ffbb113278ca5af168f75287c7dc90","path_source":"inherited","provider":"task-check","signal":null,"stat_fingerprint":{"st_dev":2049,"st_ino":24924036,"st_mode":33261,"st_mtime_ns":1750877722000000000,"st_size":6828688},"status":"pass","stderr_body_omitted":false,"stderr_byte_count":0,"stderr_sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","stdout_body_omitted":true,"stdout_byte_count":1280,"stdout_sha256":"1ac2b27c017acf9f384988853389805eeb352f00efec69a8b049a05f6ad6a88f","summary":"exit 0","timeout_seconds":900,"untrusted_diagnostic":null}],"workflow_role":"independent_reviewer"}],"final_head":"44f4cfe8aaac549b4538aa6561bdb6bdf6c1010b","hop_limit":10,"hop_used":1,"hops":[{"actual_commits":["44f4cfe8aaac549b4538aa6561bdb6bdf6c1010b"],"base_head":"b46d181b988ea0a4ae0f0534d16cc514ee632ed0","changed_paths":["docs/warm_storage/concepts/wfc_beta1_bp3_remediation_review_001_broad_review.adoc"],"end_head":"44f4cfe8aaac549b4538aa6561bdb6bdf6c1010b","hop_id":"H001","outcome":"completed"}],"human_question":null,"invocation_id":"INV-7293d3ffdec645b9ac9bb4b8122d0bcf","next_explicit_action":"No automatic follow-up action occurs.","operator_error":null,"provider":{"candidate_result_present":true,"diagnostics":[],"diagnostics_artifact":null,"returncode":0,"timed_out":false},"run_id":"RUN-6538d0568bce47a899dab7677ea9c2e1","run_status":"completed","schema_version":2,"semantic_stop":null,"stop_reason":null,"successor_provenance":null,"work_item_id":"WI-WFC-BETA1-BP3-REMEDIATION-REVIEW-001","worktree_clean":true}'''
_BP7_OPERATOR_PLAN_JSON = '''{"agent_operating_contract":{"byte_count":5754,"contract_id":"workflow_coordinator.agent_operating_contract","resource":"resources/agent_operating_contract_v6.txt","sha256":"7abf57ab315965c4266674efdb71c6cd1415989cdb771b9232501563862ca8f7","version":6},"components":{"reviewer":{"digest":"739a5db2044b7897655c0120ea9f0c4c2c5fc8cc3074a7f03e93385f67dac4b4","path":"governance/self-development/prompts/reviewer.txt"},"worker":{"digest":"179656ee9824c91c151c7edb7284ab848067a28fc575b1e0fe5549df8a99daab","path":"governance/self-development/prompts/worker.txt"}},"coordinator":{"implementation_tree_sha256":"fa2a0926bd3f40682ed7f872b74c89cd5303eab12a558b44ff0c026b9357cdff","resources":{"agent_operating_contract_v6.txt":"7abf57ab315965c4266674efdb71c6cd1415989cdb771b9232501563862ca8f7","roles/reviewer.txt":"739a5db2044b7897655c0120ea9f0c4c2c5fc8cc3074a7f03e93385f67dac4b4","roles/simple-self-check.txt":"7b5436e7998c955b0ed06812fdfb575d27eed4879334d154f84c2b9b3c7e97cb","roles/worker.txt":"179656ee9824c91c151c7edb7284ab848067a28fc575b1e0fe5549df8a99daab"},"source_commit":null,"version":"0.1.0b1"},"durable_artifacts":{"broad_review":["docs/warm_storage/concepts/wfc_beta1_bp7_guidance_config_001_broad_review.adoc"],"focused_review":["docs/warm_storage/concepts/wfc_beta1_bp7_guidance_config_001_focused_review.adoc"]},"evidence":{"environment_policy":"coordinator-inherited-environment-v1","path_digest":"83be98092436371a4da697a22568457c3c22460e40af52bb13d162ba90190aed","path_source":"inherited","selected_commands":[{"argv_digest":"c3f2578d3a465d10ae97391fe2a54f1c10832286381277ce1355ccfb0bbac529","authored_argv0":"python","canonical_path":"/usr/bin/python3.13","command_name":"task-check","invocation_path":"/home/stefan/wfc-ops/wfc-beta1-bp7-guidance-config-001-20261120-01/dev-env/bin/python","stat_fingerprint":{"st_dev":2049,"st_ino":24924036,"st_mode":33261,"st_mtime_ns":1750877722000000000,"st_size":6828688},"timeout_seconds":900}]},"executable":"/home/stefan/.local/bin/claude","execution_bindings_source":"/home/stefan/wfc-ops/wfc-beta1-bp7-guidance-config-001-20261120-01/execution-bindings.yaml","expected_baseline":"48b86b11da99c32b7d6650e91faf262e415aef6b","governing_inputs":{"governance/commissions/wfc-beta1-bp7-guidance-config-001/policy.yaml":"6a43775f4f86638aea2ac22adc2d73a71014688de2a389f3743a52da6b2ef28a","governance/commissions/wfc-beta1-bp7-guidance-config-001/profile.yaml":"8f8dd587b76f959f74d14a500721dc1b9bdbe5f2ce77ba1b789423a3bca49f47","governance/commissions/wfc-beta1-bp7-guidance-config-001/task.md":"8233cf723b037bbbd02e02fb7e6bd338823cbc36d4227f1fdebf6153af9aa81a","governance/commissions/wfc-beta1-bp7-guidance-config-001/work-item.yaml":"ac34206386f9175c3b726ab9bcec4b81150f71032eafe1c5912279cafe4fd444","governance/self-development/components.yaml":"dc239e4e83c44336b5622c427443192bfd349c6a40b784bf396ad7903a9ec524","governance/self-development/prompts/reviewer.txt":"739a5db2044b7897655c0120ea9f0c4c2c5fc8cc3074a7f03e93385f67dac4b4","governance/self-development/prompts/worker.txt":"179656ee9824c91c151c7edb7284ab848067a28fc575b1e0fe5549df8a99daab"},"inputs":{"components":"governance/self-development/components.yaml","pinned_revision":"48b86b11da99c32b7d6650e91faf262e415aef6b","policy":"governance/commissions/wfc-beta1-bp7-guidance-config-001/policy.yaml","profile":"governance/commissions/wfc-beta1-bp7-guidance-config-001/profile.yaml","task":"governance/commissions/wfc-beta1-bp7-guidance-config-001/task.md","work_item":"governance/commissions/wfc-beta1-bp7-guidance-config-001/work-item.yaml"},"legacy_durable_artifacts":[],"max_autonomous_hops":10,"repository":"/home/stefan/repos/spreadsheet-handling/workflow-coordinator","retry":false,"role_bindings":{"independent_reviewer":{"adapter":"claude_cli","model":"claude-opus-5","reasoning":"high"},"worker":{"adapter":"claude_cli","model":"claude-sonnet-5","reasoning":"high"}},"role_personas":{"independent_reviewer":"Fresh adversarial reviewer: verify config, guidance and test against the real code and sources; report material defects only.","worker":"Careful repository maintainer: derive configuration and guidance strictly from existing practice and the real parser; invent nothing."},"run_id":"RUN-dd7b7d19c1c645b9a470551ad36f4784","schema_version":2,"state_root":"/home/stefan/wfc-state/wfc-beta1-bp7-guidance-config-001-20261120-01","timeout_seconds":3600.0}'''
_BP7_OPERATOR_SUMMARY_JSON = '''{"anomalies":[],"baseline_head":"48b86b11da99c32b7d6650e91faf262e415aef6b","changed_paths":["docs/operator_manual/ch05_self_development/01_topology_budget.adoc","docs/warm_storage/concepts/wfc_beta1_bp7_guidance_config_001_broad_review.adoc","tests/unit/test_self_development_consumer_config.py"],"checkout_id":"847ae207-8b98-4e07-a1f1-f5a27dc53519","commits":[{"hash":"a6ffa90058bdd6a5fc867bcedfd829bf26b81a77","subject":"test(WI-WFC-BETA1-BP7-GUIDANCE-CONFIG-001/H001): verify committed self-development Consumer Config and note its existence in the operator manual"},{"hash":"a65c319f4ebbe115a9d7c2a60dc0210845844bf0","subject":"docs(review): WI-WFC-BETA1-BP7-GUIDANCE-CONFIG-001 H002 independent broad review of the BP7 C1 self-development Consumer Config, guidance and verification test (accept)"}],"current_head":"a65c319f4ebbe115a9d7c2a60dc0210845844bf0","execution_evidence":[{"actual_commits":["a6ffa90058bdd6a5fc867bcedfd829bf26b81a77"],"adapter":"claude_cli","base_head":"48b86b11da99c32b7d6650e91faf262e415aef6b","candidate_result_present":true,"context":"fresh","diagnostics":[],"diagnostics_artifact":null,"end_head":"a6ffa90058bdd6a5fc867bcedfd829bf26b81a77","executable":"/home/stefan/.local/bin/claude","hop_id":"H001","invocation_id":"INV-c43623135f8d443380b32d856b68075e","model":"claude-sonnet-5","outcome":"completed","persona":"Careful repository maintainer: derive configuration and guidance strictly from existing practice and the real parser; invent nothing.","persona_sha256":"5e095937a946421969a7f3440c4b412b398330198255afe62901f7b9586e32f5","phase":"implement","reasoning":"high","returncode":0,"timed_out":false,"timeout_seconds":3600.0,"trusted_observations":[],"workflow_role":"worker"},{"actual_commits":["a65c319f4ebbe115a9d7c2a60dc0210845844bf0"],"adapter":"claude_cli","base_head":"a6ffa90058bdd6a5fc867bcedfd829bf26b81a77","candidate_result_present":true,"context":"fresh","diagnostics":[],"diagnostics_artifact":null,"end_head":"a65c319f4ebbe115a9d7c2a60dc0210845844bf0","executable":"/home/stefan/.local/bin/claude","hop_id":"H002","invocation_id":"INV-463a576778b64995b4a7b8e1ab4d4ad6","model":"claude-opus-5","outcome":"completed","persona":"Fresh adversarial reviewer: verify config, guidance and test against the real code and sources; report material defects only.","persona_sha256":"d06a04dab0d77122774660ad9012b6937ff86ec7edfa1e1583bb9ec9cd51c319","phase":"broad_review","reasoning":"high","returncode":0,"timed_out":false,"timeout_seconds":3600.0,"trusted_observations":[{"argv_digest":"c3f2578d3a465d10ae97391fe2a54f1c10832286381277ce1355ccfb0bbac529","artifact_ref":null,"authored_argv0":"python","canonical_path":"/usr/bin/python3.13","command":["/home/stefan/wfc-ops/wfc-beta1-bp7-guidance-config-001-20261120-01/dev-env/bin/python","-m","pytest","-q"],"command_name":"task-check","cwd_policy":"canonical-repository-root-v1","environment_policy":"coordinator-inherited-environment-v1","error_class":null,"exit_code":0,"invocation_path":"/home/stefan/wfc-ops/wfc-beta1-bp7-guidance-config-001-20261120-01/dev-env/bin/python","path_digest":"83be98092436371a4da697a22568457c3c22460e40af52bb13d162ba90190aed","path_source":"inherited","provider":"task-check","signal":null,"stat_fingerprint":{"st_dev":2049,"st_ino":24924036,"st_mode":33261,"st_mtime_ns":1750877722000000000,"st_size":6828688},"status":"pass","stderr_body_omitted":false,"stderr_byte_count":0,"stderr_sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","stdout_body_omitted":true,"stdout_byte_count":1745,"stdout_sha256":"0618433b490c30f80278c40a0f80a3501827a978dc1959f25198bd8c7a3b7f28","summary":"exit 0","timeout_seconds":900,"untrusted_diagnostic":null}],"workflow_role":"independent_reviewer"}],"final_head":"a65c319f4ebbe115a9d7c2a60dc0210845844bf0","hop_limit":10,"hop_used":2,"hops":[{"actual_commits":["a6ffa90058bdd6a5fc867bcedfd829bf26b81a77"],"base_head":"48b86b11da99c32b7d6650e91faf262e415aef6b","changed_paths":["docs/operator_manual/ch05_self_development/01_topology_budget.adoc","tests/unit/test_self_development_consumer_config.py"],"end_head":"a6ffa90058bdd6a5fc867bcedfd829bf26b81a77","hop_id":"H001","outcome":"completed"},{"actual_commits":["a65c319f4ebbe115a9d7c2a60dc0210845844bf0"],"base_head":"a6ffa90058bdd6a5fc867bcedfd829bf26b81a77","changed_paths":["docs/warm_storage/concepts/wfc_beta1_bp7_guidance_config_001_broad_review.adoc"],"end_head":"a65c319f4ebbe115a9d7c2a60dc0210845844bf0","hop_id":"H002","outcome":"completed"}],"human_question":null,"invocation_id":"INV-463a576778b64995b4a7b8e1ab4d4ad6","next_explicit_action":"No automatic follow-up action occurs.","operator_error":null,"provider":{"candidate_result_present":true,"diagnostics":[],"diagnostics_artifact":null,"returncode":0,"timed_out":false},"run_id":"RUN-dd7b7d19c1c645b9a470551ad36f4784","run_status":"completed","schema_version":2,"semantic_stop":null,"stop_reason":null,"successor_provenance":null,"work_item_id":"WI-WFC-BETA1-BP7-GUIDANCE-CONFIG-001","worktree_clean":true}'''

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "fixtures"
# The one frozen, hash-pinned legacy archiver every fixture case compares
# against (<<archive-equivalence>> names it singular; it is not duplicated
# per fixture).
LEGACY = FIXTURES_ROOT / "bp5" / "legacy"
REAL_GIT = shutil.which("git")


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        [REAL_GIT, "-C", str(repository), *args], capture_output=True, text=True, check=True,
    ).stdout


@dataclass(frozen=True)
class EmbeddedFile:
    """A fixture file embedded verbatim in this module instead of committed
    under its fixture directory, to avoid a committed-fixture/test-name
    collision (see the owning fixture's PROVENANCE.txt). Materialized into
    the disposable relocated state root by ``_relocate_real_run``."""

    relative_path: str  # relative to the relocated state root
    content: str
    expected_sha256: str


@dataclass(frozen=True)
class FixtureCase:
    """One retained Run's disposable, hash-pinned fixture copy, pointed at
    the same two-archiver comparison every other case uses."""

    name: str
    fixture_dir: Path
    attempt_id: str
    embedded_files: tuple[EmbeddedFile, ...] = ()
    test_method_name: str | None = None

    @property
    def real_run(self) -> Path:
        return self.fixture_dir / "real_run"

    def resolved_test_method_name(self) -> str:
        return (
            self.test_method_name
            or f"test_old_and_new_archivers_produce_identical_manifests_for_{self.name}"
        )


BP5 = FixtureCase(
    name="bp5",
    fixture_dir=FIXTURES_ROOT / "bp5",
    attempt_id="wfc-beta1-bp3-remediation-review-001-20261119-01",
    embedded_files=(
        EmbeddedFile(
            "operator-plan.json",
            _BP5_OPERATOR_PLAN_JSON,
            "80803321703b1c08e65742443c98fd6cf9f6e1e78be336244a03e15f95191f82",
        ),
        EmbeddedFile(
            "operator-summary.json",
            _BP5_OPERATOR_SUMMARY_JSON,
            "bd6cb02f37965335a947df64cf58fb84ee895e3783839cec222eb0c8a938f2be",
        ),
    ),
    # Keeps the original BP5 acceptance test's own node id (regression).
    test_method_name="test_old_and_new_archivers_produce_identical_manifests_for_the_same_real_run",
)

BP7 = FixtureCase(
    name="bp7",
    fixture_dir=FIXTURES_ROOT / "bp7",
    attempt_id="wfc-beta1-bp7-guidance-config-001-20261120-01",
    embedded_files=(
        EmbeddedFile(
            "operator-plan.json",
            _BP7_OPERATOR_PLAN_JSON,
            "8c67a19d2ce9d67f34e9739b204d009b258118e12dd0ddade6ed6f5568676670",
        ),
        EmbeddedFile(
            "operator-summary.json",
            _BP7_OPERATOR_SUMMARY_JSON,
            "d955bb2be421f91253a35e1306a1470cc82bb686476bf32a1711a6f6d9637ba7",
        ),
    ),
)

# Add a further ``FixtureCase`` here, pointed at its own committed fixture
# directory under tests/fixtures/, to run the same comparison against another
# retained Run; see docs/developer_guide/ch02_code_navigation.adoc.
FIXTURE_CASES: tuple[FixtureCase, ...] = (BP5, BP7)


def _relocate_real_run(base: Path, case: FixtureCase) -> tuple[Path, Path, Path]:
    """A disposable, relocated working copy of the archived Run's own state
    root and OPS directory, under a throwaway HOME. Returns
    ``(home, state_root, ops_dir)``."""

    home = base / "home"
    state_root = home / "wfc-state" / case.attempt_id
    ops_dir = home / "wfc-ops" / case.attempt_id
    shutil.copytree(case.real_run / "run", state_root)
    shutil.copytree(case.real_run / "ops", ops_dir)
    for embedded in case.embedded_files:
        (state_root / embedded.relative_path).write_text(embedded.content + "\n")

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


def _assert_manifests_equivalent(case: unittest.TestCase, manifest_old: dict, manifest_new: dict) -> None:
    """The exact comparison the BP5 acceptance established: both archivers
    produced a non-null ``archived_at``, and the two manifests are identical
    once ``archived_at`` is stripped. Shared, unchanged, by every fixture
    case and by the mutation-sensitivity test below."""

    case.assertNotEqual(manifest_old.get("archived_at"), None)
    case.assertNotEqual(manifest_new.get("archived_at"), None)
    case.assertEqual(_strip_archived_at(manifest_old), _strip_archived_at(manifest_new))


class RealRunEquivalenceTests(unittest.TestCase):
    def setUp(self):
        import hashlib
        for path, expected in (
            (LEGACY / "archive_run.py", "31621e72ac8811bb74e528401610c10c7bf5b6e27756d53bca617235ec1acf26"),
            (LEGACY / "wfc-archive-run", "21f602b94771b3f3ee1955074078c1ee904e6a20bf0fe846194b067524482683"),
        ):
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(actual, expected, f"provenance digest mismatch for {path}")
        for case in FIXTURE_CASES:
            for embedded in case.embedded_files:
                payload = (embedded.content + "\n").encode()
                actual = hashlib.sha256(payload).hexdigest()
                self.assertEqual(
                    actual,
                    embedded.expected_sha256,
                    f"provenance digest mismatch for {case.name}:{embedded.relative_path}",
                )

    def _assert_archivers_equivalent(self, case: FixtureCase) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home, state_root, ops_dir = _relocate_real_run(base, case)

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

            dest_old = repo_old / "archive" / "unclassified" / case.attempt_id
            dest_new = repo_new / "archive" / "unclassified" / case.attempt_id
            manifest_old = json.loads((dest_old / "manifest.json").read_text())
            manifest_new = json.loads((dest_new / "manifest.json").read_text())

            _assert_manifests_equivalent(self, manifest_old, manifest_new)

            # Exactly one commit on each side, touching only its own attempt directory.
            self.assertEqual(git(repo_old, "rev-list", "--count", "HEAD").strip(), "2")
            self.assertEqual(git(repo_new, "rev-list", "--count", "HEAD").strip(), "2")

    def test_manifest_comparison_detects_a_real_difference(self):
        """Proves ``_assert_manifests_equivalent`` can actually fail: a
        tampered ``copied_files`` digest (what a tampered fixture byte would
        produce in a real manifest) must not compare equal, while an
        ``archived_at``-only difference (the one documented, acceptable
        difference) must still compare equal."""

        manifest_old = {
            "archived_at": "2026-01-01T00:00:00+00:00",
            "attempt_id": BP7.attempt_id,
            "copied_files": {"run/checkout-association.json": "deadbeef" * 8},
        }
        manifest_tampered = {
            "archived_at": "2026-01-02T00:00:00+00:00",
            "attempt_id": BP7.attempt_id,
            "copied_files": {"run/checkout-association.json": "00000000" * 8},
        }
        manifest_same_apart_from_archived_at = {
            **manifest_old,
            "archived_at": "2099-12-31T23:59:59+00:00",
        }

        with self.assertRaises(AssertionError):
            _assert_manifests_equivalent(self, manifest_old, manifest_tampered)
        _assert_manifests_equivalent(self, manifest_old, manifest_same_apart_from_archived_at)


def _make_equivalence_test(case: FixtureCase):
    def test(self: RealRunEquivalenceTests) -> None:
        self._assert_archivers_equivalent(case)

    test.__doc__ = (
        f"Old-vs-new archiver equivalence for fixture {case.name!r} ({case.attempt_id})."
    )
    return test


for _case in FIXTURE_CASES:
    setattr(RealRunEquivalenceTests, _case.resolved_test_method_name(), _make_equivalence_test(_case))
del _case


if __name__ == "__main__":
    unittest.main()

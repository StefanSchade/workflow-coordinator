"""BP6c privacy fix: a short OS user name must not refuse by mere substring.

BP6b's focused review (observation 1) found that ``LOGNAME``/``USER`` set to
a short value like ``ops`` or ``ci`` made every retention-aware archive
refuse, because the privacy guard matched the user name as a plain substring
against every value *and key* of the projection -- and short tokens like
``ops``/``ci``/``run`` occur incidentally all over ``wfc.audit/1`` (for
example ``run.hops_used``, or ``checkout-asso**ci**ation.json`` inside a raw
file path). The fix: the user name is matched only as a whole path component
(split on ``/``/``\\``) or a whole token (split on anything but letters,
digits and underscore); the home directory, state root, OPS directory and
scratch paths keep ordinary substring matching.
"""

from __future__ import annotations

import json

import pytest

from workflow_coordinator.frontdoor import archive as ar
from workflow_coordinator.frontdoor import audit as au


class TestWholeComponentOrTokenMatch:
    def test_short_user_name_as_an_incidental_substring_is_not_a_match(self):
        assert not au._whole_component_or_token_match("run.hops_used", ["ops"])
        assert not au._whole_component_or_token_match(
            "run/checkouts/x/checkout-association.json", ["ci"],
        )
        assert not au._whole_component_or_token_match("archive.json", ["a"])

    def test_user_name_as_a_whole_path_component_is_a_match(self):
        assert au._whole_component_or_token_match("/home/ops/wfc-state/attempt", ["ops"])
        assert au._whole_component_or_token_match("state/ops/run.log", ["ops"])

    def test_user_name_as_a_whole_token_is_a_match(self):
        assert au._whole_component_or_token_match("owned-by-ops-today", ["ops"])
        assert au._whole_component_or_token_match("user=ci;role=worker", ["ci"])

    def test_empty_or_no_names_never_match(self):
        assert not au._whole_component_or_token_match("anything", [])
        assert not au._whole_component_or_token_match("anything", [""])


class TestStringViolationKeepsSubstringMatchingForPrivateStrings:
    def test_private_strings_still_match_as_plain_substrings(self):
        assert au._string_violation("prefix-/home/user-suffix", ["/home/user"])
        assert not au._string_violation("unrelated value", ["/home/user"])

    def test_private_tokens_do_not_match_as_plain_substrings(self):
        assert not au._string_violation("run.hops_used", [], ["ops"])
        assert au._string_violation("state/ops/run.log", [], ["ops"])

    def test_both_kinds_combine(self):
        assert au._string_violation("/home/user/x", ["/home/user"], ["ops"])
        assert au._string_violation("a/ops/b", ["/home/user"], ["ops"])
        assert not au._string_violation("a/opslike/b", ["/home/user"], ["ops"])


def _manifest_attempt(tmp_path, *, path_segment: str | None):
    attempt_dir = tmp_path / "attempt"
    checkout_id = "11111111-2222-3333-4444-555555555555"
    run_doc = {
        "run_id": "RUN-" + "a" * 32, "work_item_id": "WI-FIXTURE", "status": "completed",
        "hop_used": 1, "hop_limit": 3, "baseline_head": "0" * 40, "current_head": "1" * 40,
        "hops": [], "findings": [],
    }
    run_rel = f"run/checkouts/{checkout_id}/run.json"
    run_path = attempt_dir / run_rel
    run_path.parent.mkdir(parents=True)
    run_path.write_text(json.dumps(run_doc), encoding="utf-8")
    copied = [{"path": run_rel, "sha256": "f" * 64, "bytes": run_path.stat().st_size}]
    if path_segment:
        # A raw file path segment that incidentally equals the short user
        # name (a real leak as a path component).
        leak_rel = f"run/{path_segment}/marker.txt"
        leak_path = attempt_dir / leak_rel
        leak_path.parent.mkdir(parents=True, exist_ok=True)
        leak_path.write_text("x", encoding="utf-8")
        copied.append({"path": leak_rel, "sha256": "e" * 64, "bytes": 1})
    manifest = {"attempt_id": "wfc-fixture-user-match-20261120-01", "copied_files": copied}
    (attempt_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return attempt_dir


class TestProjectAuditHonorsPrivateTokens:
    def test_short_user_name_no_longer_falsely_refuses(self, tmp_path):
        attempt_dir = _manifest_attempt(tmp_path, path_segment=None)
        audit = au.project_audit(attempt_dir, raw_retention="local", private_tokens=("ops",))
        assert audit["run"]["run_id"].startswith("RUN-")

    def test_real_user_name_path_component_leak_is_still_refused(self, tmp_path):
        attempt_dir = _manifest_attempt(tmp_path, path_segment="ops")
        with pytest.raises(au.ProjectionError):
            au.project_audit(attempt_dir, raw_retention="local", private_tokens=("ops",))

    def test_mere_substring_leak_is_not_refused_by_the_token_rule_alone(self, tmp_path):
        # "checkout-association.json" contains "ci" as a substring, never as
        # a whole path component or token -- the real run.json relpath here
        # already carries this, so this just pins that project_audit succeeds.
        attempt_dir = _manifest_attempt(tmp_path, path_segment=None)
        audit = au.project_audit(attempt_dir, raw_retention="local", private_tokens=("ci",))
        assert audit["schema"] == "wfc.audit/1"


class TestArchiveSuppliesUserNameAsATokenOnly:
    def test_attempt_private_strings_splits_user_into_tokens(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ar.getpass, "getuser", lambda: "ops")
        home = tmp_path / "home"
        state = tmp_path / "state"
        strings, tokens = ar._attempt_private_strings(home_dir=home, state_root=state, ops_dir=None)
        assert tokens == ("ops",)
        assert str(home) in strings
        assert str(state) in strings
        assert "ops" not in strings

"""Offline conformance tests for the closed Claude Code CLI adapter."""

from __future__ import annotations

import json
import os
import shutil
import stat
import textwrap
from pathlib import Path

import pytest

from workflow_coordinator import claude_adapter as claude_adapter_module
from workflow_coordinator.adapter import (
    ROUTING_RESULT_ENVELOPE_CONTRACT, AgentExecutionRequest, Invocation,
    ResultValidationError, validated_result,
)
from workflow_coordinator.claude_adapter import ClaudeCliAdapter, ClaudeCliError


ROUTES = ("broad_review",)


def request(repository: Path, invocation: Invocation | None = None, timeout: float = 2) -> AgentExecutionRequest:
    return AgentExecutionRequest(invocation or Invocation("RUN-C", "H001", "INV-C"), "make the bounded change", repository, ROUTING_RESULT_ENVELOPE_CONTRACT, timeout, permitted_routes=ROUTES)


def envelope(invocation: Invocation, *, run_id: str | None = None) -> dict:
    return {"schema_version": 1, "run_id": run_id or invocation.run_id, "hop_id": invocation.hop_id, "invocation_id": invocation.invocation_id, "result": {"schema_version": 1, "outcome": "completed", "requested_route": "broad_review", "scope_changed": False, "requires_human": False, "escalation": None, "findings": [], "claimed_commits": [], "commit_intent": None, "evidence_refs": [], "summary": "done"}}


def fake(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "fake-claude"
    executable.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body), encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def adapter(tmp_path: Path, executable: Path) -> ClaudeCliAdapter:
    return ClaudeCliAdapter(tmp_path, model="claude-model", reasoning_effort="high", executable=str(executable))


def test_exact_closed_argv_profile_settings_cwd_prompt_and_environment(tmp_path: Path):
    observed = tmp_path / "observed.json"
    executable = fake(tmp_path, f'''\
        import json, os, pathlib, sys
        arguments = sys.argv[1:]
        schema = json.loads(arguments[arguments.index("--json-schema") + 1])
        settings = pathlib.Path(arguments[arguments.index("--settings") + 1])
        pathlib.Path({str(observed)!r}).write_text(json.dumps({{
            "argv": arguments, "cwd": os.getcwd(), "scrub": os.environ.get("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"),
            "schema": schema, "settings": json.loads(settings.read_text()),
        }}))
        print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
                          "structured_output": {envelope(Invocation("RUN-C", "H001", "INV-C"))!r}}}))
    ''')
    outcome = adapter(tmp_path, executable).execute(request(tmp_path))
    observation = json.loads(observed.read_text())
    argv = observation["argv"]
    assert argv[:4] == ["-p", "--output-format", "json", "--json-schema"]
    schema = observation["schema"]
    assert argv[4] == json.dumps(schema, separators=(",", ":"), sort_keys=True)
    assert argv[5:] == ["--model", "claude-model", "--effort", "high", "--restricted", "--safe-mode", "--strict-mcp-config", "--tools", "Read,Glob,Grep,Edit,Write,Bash", "--settings", str(tmp_path / "claude-closed-settings.json"), "--permission-mode", "acceptEdits", "--permission-prompts", "none", "--no-session-persistence", "make the bounded change"]
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert schema["required"] == ["schema_version", "run_id", "hop_id", "invocation_id", "result"]
    assert set(schema["properties"]) == set(schema["required"])
    assert schema["properties"]["schema_version"]["enum"] == [1]
    assert schema["properties"]["run_id"]["enum"] == ["RUN-C"]
    assert schema["properties"]["hop_id"]["enum"] == ["H001"]
    assert schema["properties"]["invocation_id"]["enum"] == ["INV-C"]
    result_schema = schema["properties"]["result"]
    assert result_schema["properties"]["schema_version"]["enum"] == [1]
    assert result_schema["required"] == [
        "schema_version", "outcome", "requested_route", "scope_changed", "requires_human",
        "escalation", "findings", "claimed_commits", "commit_intent", "evidence_refs", "summary",
    ]
    assert result_schema["properties"]["outcome"]["enum"] == [
        "completed", "changes_required", "blocked", "failed",
    ]
    assert result_schema["properties"]["requested_route"]["enum"] == ["broad_review"]
    forbidden = ("codex", "--mcp-config", "--allowed-tools", "--fallback-model", "--resume", "--continue", "--add-dir", "--dangerously-skip-permissions")
    assert not any(item in argv for item in forbidden)
    assert observation["cwd"] == str(tmp_path) and observation["scrub"] is None
    scratch = str(ClaudeCliAdapter._external_scratch_dir(request(tmp_path)).resolve())
    assert observation["settings"] == {"sandbox": {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False, "filesystem": {"denyRead": ["~/"], "allowRead": [str(tmp_path), scratch], "allowWrite": [scratch]}, "network": {"allowedDomains": []}}, "permissions": {"allow": ["Edit", "Write"], "deny": ["WebFetch", "WebSearch"], "blockReadsOutsideWorkingDirectories": True}}
    assert outcome.returncode == 0 and not outcome.timed_out
    assert json.loads(outcome.candidate_result or "{}") == envelope(Invocation("RUN-C", "H001", "INV-C"))


def test_edit_grant_uses_only_resolved_repository_as_working_directory(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(repository, target_is_directory=True)
    observed = tmp_path / "grant.json"
    executable = fake(tmp_path, f'''\
        import json, os, pathlib, sys
        args = sys.argv[1:]
        settings = json.loads(pathlib.Path(args[args.index("--settings") + 1]).read_text())
        pathlib.Path({str(observed)!r}).write_text(json.dumps({{
            "cwd": os.getcwd(), "args": args, "settings": settings,
            "scrub": os.environ.get("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"),
        }}))
    ''')
    adapter(tmp_path, executable).execute(request(alias))
    result = json.loads(observed.read_text())
    assert result["cwd"] == str(repository.resolve())
    assert result["scrub"] is None
    assert "--restricted" in result["args"]
    assert "--add-dir" not in result["args"]
    assert result["settings"]["permissions"]["allow"] == ["Edit", "Write"]
    scratch = str(ClaudeCliAdapter._external_scratch_dir(request(alias)).resolve())
    assert result["settings"]["sandbox"]["filesystem"] == {
        "denyRead": ["~/"], "allowRead": [str(repository.resolve()), scratch], "allowWrite": [scratch],
    }


def test_child_environment_is_explicit_and_extra_names_are_additive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/example/home")
    monkeypatch.setenv("USER", "example")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("UNRELATED_SECRET", "never-copy")
    monkeypatch.setenv("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "nested-session")
    monkeypatch.delenv("WFC_CLAUDE_EXTRA_ENV_NAMES", raising=False)
    observed = tmp_path / "environment.json"
    executable = fake(tmp_path, f'''\
        import json, os, pathlib
        pathlib.Path({str(observed)!r}).write_text(json.dumps(dict(os.environ)))
    ''')
    instance = adapter(tmp_path, executable)
    instance.execute(request(tmp_path))
    baseline = json.loads(observed.read_text())
    expected_tmp = str(instance._short_subprocess_tmp_dir(request(tmp_path)))
    assert baseline == {"PATH": "/usr/bin:/bin", "HOME": "/example/home", "USER": "example",
                        "LANG": "C.UTF-8", "TMPDIR": expected_tmp, "TMP": expected_tmp, "TEMP": expected_tmp}

    monkeypatch.setenv("WFC_CLAUDE_EXTRA_ENV_NAMES", "EXTRA_ONE, EXTRA_TWO,MISSING")
    monkeypatch.setenv("EXTRA_ONE", "old")
    extended = adapter(tmp_path, executable)
    monkeypatch.setenv("EXTRA_ONE", "at-launch")
    monkeypatch.setenv("EXTRA_TWO", "second")
    monkeypatch.setenv("WFC_CLAUDE_EXTRA_ENV_NAMES", "UNRELATED_SECRET")
    extended.execute(request(tmp_path))
    assert json.loads(observed.read_text()) == {**baseline, "EXTRA_ONE": "at-launch", "EXTRA_TWO": "second"}


@pytest.mark.parametrize("names", ["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", "CLAUDE_CODE_SESSION_ID", "X=value", "TMPDIR", "OK,,OTHER"])
def test_extra_environment_names_reject_reserved_or_non_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: str):
    monkeypatch.setenv("WFC_CLAUDE_EXTRA_ENV_NAMES", names)
    with pytest.raises(ValueError, match="invalid or reserved"):
        adapter(tmp_path, tmp_path / "unused")


def test_successful_wrapper_candidate_is_still_validated_by_wfc(tmp_path: Path):
    invocation = Invocation("RUN-C", "H001", "INV-C")
    executable = fake(tmp_path, f'''\
        import json
        print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "structured_output": {envelope(invocation)!r}}}))
    ''')
    outcome = adapter(tmp_path, executable).execute(request(tmp_path, invocation))
    assert validated_result(outcome.candidate_result or "", invocation).summary == "done"


@pytest.mark.parametrize("wrapper", ["not-json", json.dumps({"type": "result", "subtype": "error", "is_error": True}), json.dumps({"type": "result", "subtype": "success", "is_error": False}), json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": "not-an-object"})])
def test_zero_exit_malformed_or_unsuccessful_wrapper_never_yields_a_candidate(tmp_path: Path, wrapper: str):
    outcome = adapter(tmp_path, fake(tmp_path, f"print({wrapper!r})\n")).execute(request(tmp_path))
    assert outcome.returncode == 0 and not outcome.timed_out and outcome.candidate_result is None


def test_malformed_extracted_envelope_is_not_repaired_or_accepted(tmp_path: Path):
    wrapper = json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": {"bad": True}})
    outcome = adapter(tmp_path, fake(tmp_path, f"print({wrapper!r})\n")).execute(request(tmp_path))
    assert outcome.candidate_result == '{"bad": true}'
    with pytest.raises(ResultValidationError):
        validated_result(outcome.candidate_result, Invocation("RUN-C", "H001", "INV-C"))


def test_mismatched_correlation_is_never_repaired(tmp_path: Path):
    invocation = Invocation("RUN-C", "H001", "INV-C")
    executable = fake(tmp_path, f'''\
        import json
        print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "structured_output": {envelope(invocation, run_id="RUN-OTHER")!r}}}))
    ''')
    outcome = adapter(tmp_path, executable).execute(request(tmp_path, invocation))
    assert json.loads(outcome.candidate_result or "{}")["run_id"] == "RUN-OTHER"
    with pytest.raises(ResultValidationError, match="does not correlate"):
        validated_result(outcome.candidate_result or "", invocation)


def test_nonzero_valid_looking_output_and_timeout_partial_output_are_transport_only(tmp_path: Path):
    good = json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": envelope(Invocation("RUN-C", "H001", "INV-C"))})
    outcome = adapter(tmp_path, fake(tmp_path, f"import sys\nprint({good!r})\nsys.exit(9)\n")).execute(request(tmp_path))
    assert outcome.returncode == 9 and outcome.candidate_result is None
    slow = fake(tmp_path, "import sys, time\nsys.stdout.write('{\\\"partial\\\":')\nsys.stdout.flush()\ntime.sleep(10)\n")
    outcome = adapter(tmp_path, slow).execute(request(tmp_path, timeout=0.05))
    assert outcome.timed_out and outcome.candidate_result is None and "partial" in outcome.stdout


def test_local_startup_failure_and_unroutable_request_never_launch(tmp_path: Path):
    with pytest.raises(ClaudeCliError, match="pre-acceptance executable launch failure"):
        adapter(tmp_path, tmp_path / "missing-claude").execute(request(tmp_path))
    executable = tmp_path / "must-not-run"
    bad = AgentExecutionRequest(Invocation("RUN-C", "H001", "INV-C"), "p", tmp_path, ROUTING_RESULT_ENVELOPE_CONTRACT, 1, permitted_routes=())
    with pytest.raises(ClaudeCliError, match="no permitted routes"):
        adapter(tmp_path, executable).execute(bad)
    assert not executable.exists()


def test_subprocess_tmpdir_is_short_and_cleaned_up_regardless_of_ambient_tmpdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    # A realistic long, human-readable ambient TMPDIR, as WFC's own launch.sh
    # sets it (Attempt-scoped scratch root); this is what previously leaked
    # into the Claude subprocess and broke its Bash-tool sandbox bridge.
    long_ambient = tmp_path / (
        "wfc-bubblewrap-fs-isolation-001-review-001-correction-002-20261106-01"
    )
    long_ambient.mkdir()
    monkeypatch.setenv("TMPDIR", str(long_ambient))
    monkeypatch.setenv("TMP", str(long_ambient))
    monkeypatch.setenv("TEMP", str(long_ambient))

    observed = tmp_path / "tmpdir_observed.json"
    executable = fake(tmp_path, f'''\
        import json, os, pathlib
        pathlib.Path({str(observed)!r}).write_text(json.dumps({{
            "tmpdir": os.environ.get("TMPDIR"), "tmp": os.environ.get("TMP"),
            "temp": os.environ.get("TEMP"),
            "tmpdir_exists": os.path.isdir(os.environ.get("TMPDIR", "")),
            "tmpdir_is_symlink": os.path.islink(os.environ.get("TMPDIR", "")),
            "tmpdir_realpath": os.path.realpath(os.environ.get("TMPDIR", "")),
        }}))
        print('{{"type":"result","subtype":"success","is_error":false}}')
    ''')
    settings = tmp_path / "claude-closed-settings.json"
    adapter(tmp_path, executable).execute(request(tmp_path))
    observation = json.loads(observed.read_text())
    granted = json.loads(settings.read_text())["sandbox"]["filesystem"]

    # Same short value across all three variables, independent of the long
    # ambient one, with enough headroom for Claude Code's own
    # claude-<uid>/<mangled-cwd>/<session-uuid>/<socket> suffix to stay
    # under the 108-byte AF_UNIX sun_path limit.
    assert observation["tmpdir"] == observation["tmp"] == observation["temp"]
    assert len(observation["tmpdir"]) <= 40
    assert observation["tmpdir"] != str(long_ambient)
    assert observation["tmpdir_exists"] is True

    # The short TMPDIR is a symlink; its real target resolves under the
    # dedicated external scratch location (the ambient long_ambient this
    # test stands in for), not under the short root itself.
    assert observation["tmpdir_is_symlink"] is True
    assert observation["tmpdir_realpath"] != observation["tmpdir"]
    assert observation["tmpdir_realpath"].startswith(str(long_ambient) + os.sep)

    # The sandbox filesystem grant names exactly that real resolved target,
    # never the symlink and never the external scratch's own ancestor.
    assert granted["allowRead"][-1] == observation["tmpdir_realpath"]
    assert granted["allowWrite"] == [observation["tmpdir_realpath"]]

    # Cleaned up again once the subprocess has exited: neither the symlink
    # itself nor its real target still exist (a dangling symlink would still
    # report not-exists via Path.exists(), so is_symlink() is checked too).
    tmpdir_path = Path(observation["tmpdir"])
    assert not tmpdir_path.is_symlink()
    assert not tmpdir_path.exists()
    assert not Path(observation["tmpdir_realpath"]).exists()


def test_subprocess_tmpdir_is_cleaned_up_even_on_timeout(tmp_path: Path):
    observed = tmp_path / "tmpdir_on_timeout.json"
    slow = fake(tmp_path, f'''\
        import json, os, pathlib, sys, time
        pathlib.Path({str(observed)!r}).write_text(json.dumps({{
            "tmpdir": os.environ.get("TMPDIR"),
            "tmpdir_realpath": os.path.realpath(os.environ.get("TMPDIR", "")),
        }}))
        sys.stdout.write("partial")
        sys.stdout.flush()
        time.sleep(10)
    ''')
    outcome = adapter(tmp_path, slow).execute(request(tmp_path, timeout=0.05))
    assert outcome.timed_out
    recorded = json.loads(observed.read_text())
    recorded_tmpdir = Path(recorded["tmpdir"])
    assert not recorded_tmpdir.is_symlink()
    assert not recorded_tmpdir.exists()
    assert not Path(recorded["tmpdir_realpath"]).exists()


def test_subprocess_tmpdir_symlink_and_real_target_are_cleaned_up_on_launch_failure(tmp_path: Path):
    observed = tmp_path / "tmpdir_on_failure.json"
    executable = fake(tmp_path, f'''\
        import json, os, pathlib, sys
        pathlib.Path({str(observed)!r}).write_text(json.dumps({{
            "tmpdir": os.environ.get("TMPDIR"),
            "tmpdir_realpath": os.path.realpath(os.environ.get("TMPDIR", "")),
        }}))
        sys.exit(7)
    ''')
    outcome = adapter(tmp_path, executable).execute(request(tmp_path))
    assert outcome.returncode == 7
    recorded = json.loads(observed.read_text())
    recorded_tmpdir = Path(recorded["tmpdir"])
    assert not recorded_tmpdir.is_symlink()
    assert not recorded_tmpdir.exists()
    assert not Path(recorded["tmpdir_realpath"]).exists()


def test_external_scratch_root_falls_back_when_ambient_default_coincides_with_short_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    # The stock Ubuntu/WSL default: no WFC launcher has exported
    # TMPDIR/TMP/TEMP, so tempfile.gettempdir() is consulted -- and on an
    # ordinary host that is _SHORT_TMP_ROOT itself. Without a distinct
    # fallback this collapses the short symlink and its own real target
    # onto one path, breaking every invocation (see the finding this
    # correction addresses).
    #
    # _SHORT_TMP_ROOT is rebound to a dedicated tmp_path sibling, distinct
    # from _WFC_FALLBACK_SCRATCH_ROOT below. The real constant is the host's
    # actual /tmp, which pytest's own tmp_path fixture may itself sit under
    # when no WFC launcher has relocated TMPDIR for this test process --
    # exactly the coincidence this test isolates, not a path this test's own
    # fixture should reproduce by accident.
    short_root_dir = tmp_path / "short-root"
    short_root_dir.mkdir()
    monkeypatch.setattr(claude_adapter_module, "_SHORT_TMP_ROOT", short_root_dir)
    monkeypatch.delenv("TMPDIR", raising=False)
    monkeypatch.delenv("TMP", raising=False)
    monkeypatch.delenv("TEMP", raising=False)
    monkeypatch.setattr(
        claude_adapter_module.tempfile, "gettempdir",
        lambda: str(claude_adapter_module._SHORT_TMP_ROOT),
    )
    fallback_root = tmp_path / "fallback-root"
    monkeypatch.setattr(claude_adapter_module, "_WFC_FALLBACK_SCRATCH_ROOT", fallback_root)

    observed = tmp_path / "coincidence_observed.json"
    executable = fake(tmp_path, f'''\
        import json, os, pathlib
        pathlib.Path({str(observed)!r}).write_text(json.dumps({{
            "tmpdir": os.environ.get("TMPDIR"),
            "tmpdir_realpath": os.path.realpath(os.environ.get("TMPDIR", "")),
        }}))
        print('{{"type":"result","subtype":"success","is_error":false}}')
    ''')
    settings = tmp_path / "claude-closed-settings.json"
    outcome = adapter(tmp_path, executable).execute(request(tmp_path))

    # The degenerate case does not raise, does not launch against a
    # collapsed path, and does not leave the real target un-granted.
    assert outcome.returncode == 0 and not outcome.timed_out
    observation = json.loads(observed.read_text())
    granted = json.loads(settings.read_text())["sandbox"]["filesystem"]
    tmpdir_path = Path(observation["tmpdir"])
    realpath = Path(observation["tmpdir_realpath"])
    short_root = claude_adapter_module._SHORT_TMP_ROOT.resolve()

    assert realpath != tmpdir_path
    assert realpath != short_root and short_root not in realpath.parents
    assert short_root == short_root_dir.resolve()
    assert str(realpath).startswith(str(fallback_root.resolve()) + os.sep)
    assert granted["allowWrite"] == [str(realpath)]

    # Cleaned up afterward like every other outcome.
    assert not tmpdir_path.is_symlink() and not tmpdir_path.exists()
    assert not realpath.exists()


def test_external_scratch_root_falls_back_when_operator_sets_tmpdir_to_short_root_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    # The same coincidence occurs whenever an operator or environment sets
    # TMPDIR=/tmp explicitly, independent of the gettempdir() platform
    # default exercised above.
    #
    # _SHORT_TMP_ROOT is rebound to a dedicated tmp_path sibling, distinct
    # from _WFC_FALLBACK_SCRATCH_ROOT below, for the same reason as the
    # preceding test: pytest's own tmp_path fixture may itself sit under the
    # host's real /tmp when no WFC launcher has relocated TMPDIR, which must
    # not be mistaken for the coincidence this test means to isolate.
    short_root_dir = tmp_path / "short-root"
    short_root_dir.mkdir()
    monkeypatch.setattr(claude_adapter_module, "_SHORT_TMP_ROOT", short_root_dir)
    monkeypatch.setenv("TMPDIR", str(short_root_dir))
    monkeypatch.setenv("TMP", str(short_root_dir))
    monkeypatch.setenv("TEMP", str(short_root_dir))
    fallback_root = tmp_path / "fallback-root"
    monkeypatch.setattr(claude_adapter_module, "_WFC_FALLBACK_SCRATCH_ROOT", fallback_root)

    observed = tmp_path / "explicit_coincidence_observed.json"
    executable = fake(tmp_path, f'''\
        import json, os, pathlib
        pathlib.Path({str(observed)!r}).write_text(json.dumps({{
            "tmpdir": os.environ.get("TMPDIR"),
            "tmpdir_realpath": os.path.realpath(os.environ.get("TMPDIR", "")),
        }}))
        print('{{"type":"result","subtype":"success","is_error":false}}')
    ''')
    outcome = adapter(tmp_path, executable).execute(request(tmp_path))

    assert outcome.returncode == 0 and not outcome.timed_out
    observation = json.loads(observed.read_text())
    tmpdir_path = Path(observation["tmpdir"])
    realpath = Path(observation["tmpdir_realpath"])
    short_root_resolved = claude_adapter_module._SHORT_TMP_ROOT.resolve()

    assert short_root_resolved == short_root_dir.resolve()
    assert realpath != short_root_resolved and short_root_resolved not in realpath.parents
    assert not tmpdir_path.is_symlink() and not tmpdir_path.exists()
    assert not realpath.exists()


def test_leftover_at_derived_paths_is_safely_superseded_when_owned_by_current_user(tmp_path: Path):
    instance = adapter(tmp_path, fake(tmp_path, '''\
        print('{"type":"result","subtype":"success","is_error":false}')
    '''))
    pending = request(tmp_path)
    tmp_dir = instance._short_subprocess_tmp_dir(pending)
    real_dir = instance._external_scratch_dir(pending)
    stale_target = real_dir.parent / "stale-unrelated-target"
    try:
        # A leftover real directory from an unclean prior shutdown, with
        # stale content inside it that a naive non-recursive cleanup would
        # miss.
        real_dir.mkdir(parents=True)
        (real_dir / "stale-leftover-file").write_text("from a previous attempt")
        # A leftover short symlink pointing at a (now different, soon to be
        # recreated) target, also from a prior invocation.
        stale_target.mkdir()
        tmp_dir.symlink_to(stale_target)

        outcome = instance.execute(pending)

        assert outcome.returncode == 0 and not outcome.timed_out
        assert not tmp_dir.is_symlink() and not tmp_dir.exists()
        assert not real_dir.exists()
    finally:
        shutil.rmtree(real_dir, ignore_errors=True)
        shutil.rmtree(stale_target, ignore_errors=True)
        if tmp_dir.is_symlink():
            tmp_dir.unlink()


def test_leftover_at_derived_path_not_owned_by_current_user_is_rejected_not_touched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    instance = adapter(tmp_path, tmp_path / "must-not-run")
    pending = request(tmp_path)
    real_dir = instance._external_scratch_dir(pending)
    real_dir.mkdir(parents=True)
    marker = real_dir / "not-wfc-owned-marker"
    marker.write_text("do not touch")
    try:
        impostor_uid = os.geteuid() + 1
        monkeypatch.setattr(claude_adapter_module.os, "geteuid", lambda: impostor_uid)
        with pytest.raises(ClaudeCliError, match="not owned by the current user"):
            instance.execute(pending)
        # Rejected, not superseded: the unrelated leftover is left exactly
        # as it was found.
        assert marker.read_text() == "do not touch"
    finally:
        shutil.rmtree(real_dir, ignore_errors=True)


def test_zero_exit_sandbox_refusal_does_not_retry_or_invent_transport_failure(tmp_path: Path):
    observed = tmp_path / "calls"
    executable = fake(tmp_path, f'''\
        import pathlib
        pathlib.Path({str(observed)!r}).write_text("one")
        print('{{"type":"result","subtype":"success","is_error":false}}')
    ''')
    outcome = adapter(tmp_path, executable).execute(request(tmp_path))
    assert observed.read_text() == "one"
    assert outcome.returncode == 0 and not outcome.timed_out and outcome.candidate_result is None

"""Layer 1: the boundary through the real dispatch path (<<qualification>>).

Offline, network-free, no provider credentials -- a fake provider executable
committed inside the synthetic repository each test builds, dispatched
through the real ``vertical.run_one_hop`` path (via
``slice4.run_real_one_hop``) with a real ``bwrap``. Every case constructs its
own ``BoundaryGrant`` directly (mirroring what ``operator.py``'s dispatch
frame derives) and injects a pre-built adapter, so the
dispatch-level positive and negative cases are exercised without depending on
the full CLI/preflight plumbing covered separately in
``tests/unit/test_execution_boundary_dispatch_preflight.py``.

This sandbox cannot start `bwrap` (unprivileged user namespaces are
unavailable here); these cases are the implementation Commission's own
deliverable and are authoritative under the Coordinator's task-check, run in
an environment where `bwrap` works.
"""
from __future__ import annotations

import subprocess
import textwrap
import time
from pathlib import Path

import pytest

from workflow_coordinator.adapter import Invocation
from workflow_coordinator.claude_adapter import ClaudeCliAdapter
from workflow_coordinator.codex_adapter import CodexCliAdapter, CodexCliError
from workflow_coordinator.execution_launcher import BoundaryGrant, derive_boundary_grant
from workflow_coordinator.prompt import PromptComponent, load_agent_operating_contract
from workflow_coordinator.slice4 import Slice4Error, checkout_state_root, register_checkout, run_real_one_hop
from tests.support import phase, profile, route

from dataclasses import replace


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *args), check=True, text=True, capture_output=True,
    ).stdout.strip()


def _init_repo(repository: Path) -> None:
    repository.mkdir(parents=True, exist_ok=True)
    subprocess.run(("git", "init", str(repository)), check=True, capture_output=True)
    _git(repository, "config", "user.email", "fixture@example.invalid")
    _git(repository, "config", "user.name", "Fixture")


def _commit_all(repository: Path, subject: str) -> str:
    _git(repository, "add", "-A")
    _git(repository, "commit", "-m", subject)
    return _git(repository, "rev-parse", "HEAD")


def _write_fake_provider(repository: Path, body: str) -> Path:
    """A fake provider committed inside the repository's own baseline
    commit, reachable inside the view at its own absolute path
    (<<provider-reachability>>)."""
    executable = repository / "fake-provider.py"
    executable.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
    executable.chmod(0o755)
    return executable


def _components() -> dict[str, PromptComponent]:
    return {
        "worker": PromptComponent("worker", "worker.txt", "PIN", "worker"),
        "testing": PromptComponent("testing", "testing.txt", "PIN", "testing"),
        "independent_reviewer": PromptComponent("independent_reviewer", "reviewer.txt", "PIN", "reviewer"),
    }


def _channel(tmp_path: Path, name: str = "channel") -> Path:
    channel = tmp_path / name
    channel.mkdir(parents=True, exist_ok=True)
    return channel


def _auth(tmp_path: Path) -> Path:
    auth = tmp_path / "auth-token"
    auth.write_text("fixture-token")
    return auth


def _codex_grant(repository: Path, tmp_path: Path, *, writable: tuple[str, ...]) -> BoundaryGrant:
    return BoundaryGrant(
        mode="bubblewrap",
        repository=repository,
        writable_repository_paths=writable,
        readable_host_paths=(_auth(tmp_path),),
        provider_channel=_channel(tmp_path),
        provider_channel_writable=True,
    )


def _claude_grant(repository: Path, tmp_path: Path, *, writable: tuple[str, ...]) -> BoundaryGrant:
    return BoundaryGrant(
        mode="bubblewrap",
        repository=repository,
        writable_repository_paths=writable,
        readable_host_paths=(_auth(tmp_path),),
        provider_channel=_channel(tmp_path, "claude-channel"),
        provider_channel_writable=False,
    )


_CODEX_RESULT = (
    '{"schema_version":1,"run_id":"RUN-1","hop_id":"H001","invocation_id":"INV-1",'
    '"result":{"schema_version":1,"status":"completed","requested_route":"done",'
    '"scope_changed":false,"requires_human":false,"escalation":null,"findings":[],'
    '"claimed_commits":[],"commit_intent":{"paths":["%s"],"subject":"docs(workflow): WI-1 H001 fixture"},'
    '"evidence_refs":[],"summary":"done"}}'
)


def _run_dispatch(run, workflow, repository, components, *, state, association, adapter, executable):
    return run_real_one_hop(
        run, workflow, repository, components,
        agent_operating_contract=load_agent_operating_contract(),
        state_root=state, association=association,
        task_payload="fixture", invocation_id="INV-1", persona="fixture persona",
        timeout_seconds=15, executable=str(executable), adapter=adapter,
    )


from tests.support import run_for  # noqa: E402


def _worker_run(workflow, head: str):
    return replace(run_for(workflow, budget=1), current_head=head, baseline_head=head)


# -- Positive cases -------------------------------------------------------


def test_p1_worker_writes_inside_authorized_scope_and_candidate_is_accepted(tmp_path):
    repository = tmp_path / "repo"
    _init_repo(repository)
    fake = _write_fake_provider(repository, f'''\
        import pathlib, sys
        sys.stdin.read()
        args = sys.argv[1:]
        output = pathlib.Path(args[args.index("--output-last-message") + 1])
        pathlib.Path("authorized.txt").write_text("written by provider\\n")
        output.write_text({_CODEX_RESULT % "authorized.txt"!r})
    ''')
    (repository / "authorized.txt").write_text("")
    head = _commit_all(repository, "test: baseline")
    workflow = profile({"work": replace(
        phase({"done": route("complete")}, role="worker"), authorized_scope=("authorized.txt", "fake-provider.py"),
    )})
    state = tmp_path / "state"
    association = register_checkout(state, repository)
    grant = _codex_grant(repository, tmp_path, writable=("authorized.txt",))
    adapter = CodexCliAdapter(grant.provider_channel, model="m", reasoning_effort="high",
                               executable=str(fake), boundary=grant)
    result = _run_dispatch(
        _worker_run(workflow, head), workflow, repository, _components(),
        state=state, association=association, adapter=adapter, executable=fake,
    )
    assert result.reduction.run.status.value == "completed"
    assert (repository / "authorized.txt").read_text() == "written by provider\n"


def test_p2_worker_writes_into_an_already_existing_authorized_directory(tmp_path):
    repository = tmp_path / "repo"
    _init_repo(repository)
    fake = _write_fake_provider(repository, f'''\
        import pathlib, sys
        sys.stdin.read()
        args = sys.argv[1:]
        output = pathlib.Path(args[args.index("--output-last-message") + 1])
        pathlib.Path("new-dir/created.txt").write_text("fresh\\n")
        output.write_text({_CODEX_RESULT % "new-dir/created.txt"!r})
    ''')
    head = _commit_all(repository, "test: baseline")
    workflow = profile({"work": replace(
        phase({"done": route("complete")}, role="worker"), authorized_scope=("new-dir", "fake-provider.py"),
    )})
    state = tmp_path / "state"
    association = register_checkout(state, repository)
    # operator.py's real dispatch frame would refuse "new-dir" by name (F5,
    # AbsentWritableScopeEntryError): derive_boundary_grant only resolves a
    # not-yet-existing entry when it is a declared durable artifact. This
    # test constructs the directory itself, before building the grant
    # directly, to exercise the unrelated positive case -- an authorized
    # directory that already exists when the Hop starts -- without going
    # through operator.py's preflight.
    target = repository / "new-dir"
    target.mkdir(mode=0o700)
    grant = _codex_grant(repository, tmp_path, writable=("new-dir",))
    adapter = CodexCliAdapter(grant.provider_channel, model="m", reasoning_effort="high",
                               executable=str(fake), boundary=grant)
    result = _run_dispatch(
        _worker_run(workflow, head), workflow, repository, _components(),
        state=state, association=association, adapter=adapter, executable=fake,
    )
    assert result.reduction.run.status.value == "completed"
    assert (repository / "new-dir" / "created.txt").read_text() == "fresh\n"


def test_p3_reviewer_writes_its_artifact_and_a_write_outside_that_directory_is_prevented(tmp_path):
    """A reviewer-shaped invocation's durable artifact does not yet exist
    when the phase starts, so its grant is the artifact's existing parent
    directory, not the artifact itself (<<mounts>>, residual risk 3): the
    assertion is "nothing outside that directory", not artifact-level
    confinement."""
    repository = tmp_path / "repo"
    _init_repo(repository)
    (repository / "docs" / "warm_storage").mkdir(parents=True)
    (repository / "docs" / "warm_storage" / "existing_record.adoc").write_text("prior record\n")
    outside = repository / "outside.txt"
    outside.write_text("unchanged\n")
    fake = _write_fake_provider(repository, '''\
        import pathlib, sys, json
        sys.stdin.read()
        args = sys.argv[1:]
        output = pathlib.Path(args[args.index("--output-last-message") + 1])
        pathlib.Path("docs/warm_storage/new_review.adoc").write_text("the review\\n")
        blocked = False
        try:
            pathlib.Path("outside.txt").write_text("breached\\n")
        except OSError:
            blocked = True
        output.write_text(json.dumps({"schema_version":1,"run_id":"RUN-1","hop_id":"H001",
                                       "invocation_id":"INV-1","result":{"schema_version":1,
                                       "verdict":"accept","requested_route":"accept",
                                       "scope_changed":False,"requires_human":False,"escalation":None,
                                       "findings":[],"claimed_commits":[],
                                       "commit_intent":{"paths":["docs/warm_storage/new_review.adoc"],
                                       "subject":"docs(review): WI-1 H001 fixture"},
                                       "evidence_refs":[],"summary":"blocked=" + str(blocked)}}))
    ''')
    _commit_all(repository, "test: baseline")
    workflow = profile({"review": replace(
        phase({"accept": route("complete")}, role="independent_reviewer", review="gate"),
        authorized_scope=("docs/warm_storage/new_review.adoc",),
    )})
    state = tmp_path / "state"
    association = register_checkout(state, repository)
    grant = derive_boundary_grant(
        mode="bubblewrap", repository=repository,
        authorized_scope=("docs/warm_storage/new_review.adoc",),
        artifact_paths=("docs/warm_storage/new_review.adoc",),
        provider_channel=_channel(tmp_path), provider_channel_writable=True,
        authentication_path=_auth(tmp_path),
    )
    assert grant.writable_repository_paths == ("docs/warm_storage",)
    adapter = CodexCliAdapter(grant.provider_channel, model="m", reasoning_effort="high",
                               executable=str(fake), boundary=grant)
    head = _git(repository, "rev-parse", "HEAD")
    result = _run_dispatch(
        replace(run_for(workflow, budget=1), current_head=head, baseline_head=head),
        workflow, repository, _components(),
        state=state, association=association, adapter=adapter, executable=fake,
    )
    assert result.reduction.run.status.value == "completed"
    assert (repository / "docs" / "warm_storage" / "new_review.adoc").read_text() == "the review\n"
    assert outside.read_text() == "unchanged\n"


def test_p4_provider_channel_both_directions_including_rename(tmp_path):
    """Claude-shaped: reads the generated settings from a read-only channel
    mount. Codex-shaped: writes the result file in place and, separately, by
    rename over it (<<channel>>)."""
    repository = tmp_path / "repo"
    _init_repo(repository)
    claude_fake = _write_fake_provider(repository, '''\
        import json, pathlib, sys
        args = sys.argv[1:]
        settings = pathlib.Path(args[args.index("--settings") + 1])
        json.loads(settings.read_text())  # must be readable inside the view
        print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                          "structured_output": {
                              "schema_version": 1, "run_id": "RUN-1", "hop_id": "H001",
                              "invocation_id": "INV-1",
                              "result": {"status": "completed"},
                          }}))
    ''')
    _commit_all(repository, "test: baseline")
    grant = _claude_grant(repository, tmp_path, writable=())
    adapter = ClaudeCliAdapter(grant.provider_channel, model="m", reasoning_effort="high",
                                executable=str(claude_fake), boundary=grant)
    from workflow_coordinator.adapter import AgentExecutionRequest, ROUTING_RESULT_ENVELOPE_CONTRACT
    from workflow_coordinator.model import ResultContractKind
    request = AgentExecutionRequest(
        Invocation("RUN-1", "H001", "INV-1"), "hello", repository,
        ROUTING_RESULT_ENVELOPE_CONTRACT, 15.0, result_contract_kind=ResultContractKind.WORK_RESULT,
        permitted_routes=("done",),
    )
    outcome = adapter.execute(request)
    assert outcome.returncode == 0, outcome.stderr
    assert outcome.candidate_result is not None

    codex_rename_fake = _write_fake_provider(repository, '''\
        import pathlib, sys
        sys.stdin.read()
        args = sys.argv[1:]
        output = pathlib.Path(args[args.index("--output-last-message") + 1])
        tmp = output.with_suffix(".tmp")
        tmp.write_text(output.read_text() or "placeholder")
        tmp.rename(output)
        import json
        output.write_text(json.dumps({"schema_version":1,"run_id":"RUN-1","hop_id":"H001",
                                       "invocation_id":"INV-1","result":{"schema_version":1,
                                       "status":"completed","requested_route":"done",
                                       "scope_changed":False,"requires_human":False,"escalation":None,
                                       "findings":[],"claimed_commits":[],"commit_intent":None,
                                       "evidence_refs":[],"summary":"renamed"}}))
    ''')
    _commit_all(repository, "test: add codex fake")
    grant2 = _codex_grant(repository, tmp_path, writable=())
    codex_adapter = CodexCliAdapter(grant2.provider_channel, model="m", reasoning_effort="high",
                                     executable=str(codex_rename_fake), boundary=grant2)
    request2 = AgentExecutionRequest(
        Invocation("RUN-2", "H001", "INV-2"), "hello", repository,
        ROUTING_RESULT_ENVELOPE_CONTRACT, 15.0, permitted_routes=("done",),
    )
    outcome2 = codex_adapter.execute(request2)
    assert outcome2.returncode == 0, outcome2.stderr
    assert outcome2.candidate_result is not None


def test_p5_linked_git_worktree_dispatches_successfully(tmp_path):
    main_repo = tmp_path / "main"
    _init_repo(main_repo)
    (main_repo / "README.md").write_text("main\n")
    _commit_all(main_repo, "test: baseline")
    worktree = tmp_path / "worktree"
    subprocess.run(
        ("git", "-C", str(main_repo), "worktree", "add", str(worktree), "-b", "feature"),
        check=True, capture_output=True,
    )
    fake = _write_fake_provider(worktree, f'''\
        import pathlib, sys
        sys.stdin.read()
        args = sys.argv[1:]
        output = pathlib.Path(args[args.index("--output-last-message") + 1])
        pathlib.Path("authorized.txt").write_text("from worktree\\n")
        output.write_text({_CODEX_RESULT % "authorized.txt"!r})
    ''')
    (worktree / "authorized.txt").write_text("")
    head = _commit_all(worktree, "test: add fake provider")
    workflow = profile({"work": replace(
        phase({"done": route("complete")}, role="worker"), authorized_scope=("authorized.txt", "fake-provider.py"),
    )})
    state = tmp_path / "state"
    association = register_checkout(state, worktree)
    grant = _codex_grant(worktree, tmp_path, writable=("authorized.txt",))
    adapter = CodexCliAdapter(grant.provider_channel, model="m", reasoning_effort="high",
                               executable=str(fake), boundary=grant)
    result = _run_dispatch(
        _worker_run(workflow, head), workflow, worktree, _components(),
        state=state, association=association, adapter=adapter, executable=fake,
    )
    assert result.reduction.run.status.value == "completed"
    assert (worktree / "authorized.txt").read_text() == "from worktree\n"


# -- Negative cases ---------------------------------------------------------


def test_n1_write_outside_authorized_grant_is_prevented(tmp_path):
    repository = tmp_path / "repo"
    _init_repo(repository)
    protected = repository / "protected.txt"
    protected.write_text("unchanged\n")
    fake = _write_fake_provider(repository, '''\
        import pathlib, sys
        sys.stdin.read()
        args = sys.argv[1:]
        output = pathlib.Path(args[args.index("--output-last-message") + 1])
        attempt_failed = True
        try:
            pathlib.Path("protected.txt").write_text("breached\\n")
            attempt_failed = False
        except OSError:
            pass
        import json
        output.write_text(json.dumps({"schema_version":1,"run_id":"RUN-1","hop_id":"H001",
                                       "invocation_id":"INV-1","result":{"schema_version":1,
                                       "status":"completed" if attempt_failed else "blocked",
                                       "requested_route":"done","scope_changed":False,
                                       "requires_human":False,"escalation":None,"findings":[],
                                       "claimed_commits":[],"commit_intent":None,"evidence_refs":[],
                                       "summary":"attempt_failed=" + str(attempt_failed)}}))
    ''')
    _commit_all(repository, "test: baseline")
    grant = _codex_grant(repository, tmp_path, writable=())
    adapter = CodexCliAdapter(grant.provider_channel, model="m", reasoning_effort="high",
                               executable=str(fake), boundary=grant)
    from workflow_coordinator.adapter import AgentExecutionRequest, ROUTING_RESULT_ENVELOPE_CONTRACT
    request = AgentExecutionRequest(
        Invocation("RUN-1", "H001", "INV-1"), "hello", repository,
        ROUTING_RESULT_ENVELOPE_CONTRACT, 15.0, permitted_routes=("done",),
    )
    outcome = adapter.execute(request)
    assert outcome.returncode == 0, outcome.stderr
    assert '"attempt_failed=True"' in (outcome.candidate_result or "")
    assert protected.read_text() == "unchanged\n"


def test_n9_file_creation_authorized_parent_vs_unauthorized_parent(tmp_path):
    repository = tmp_path / "repo"
    _init_repo(repository)
    (repository / "authorized-dir").mkdir()
    (repository / "unauthorized-dir").mkdir()
    fake = _write_fake_provider(repository, '''\
        import pathlib, sys, json
        sys.stdin.read()
        args = sys.argv[1:]
        output = pathlib.Path(args[args.index("--output-last-message") + 1])
        ok = False
        try:
            pathlib.Path("authorized-dir/new.txt").write_text("ok\\n")
            ok = True
        except OSError:
            pass
        blocked = False
        try:
            pathlib.Path("unauthorized-dir/new.txt").write_text("bad\\n")
        except OSError:
            blocked = True
        output.write_text(json.dumps({"schema_version":1,"run_id":"RUN-1","hop_id":"H001",
                                       "invocation_id":"INV-1","result":{"schema_version":1,
                                       "status":"completed","requested_route":"done",
                                       "scope_changed":False,"requires_human":False,"escalation":None,
                                       "findings":[],"claimed_commits":[],"commit_intent":None,
                                       "evidence_refs":[],
                                       "summary":"ok=" + str(ok) + " blocked=" + str(blocked)}}))
    ''')
    _commit_all(repository, "test: baseline")
    grant = _codex_grant(repository, tmp_path, writable=("authorized-dir",))
    adapter = CodexCliAdapter(grant.provider_channel, model="m", reasoning_effort="high",
                               executable=str(fake), boundary=grant)
    from workflow_coordinator.adapter import AgentExecutionRequest, ROUTING_RESULT_ENVELOPE_CONTRACT
    request = AgentExecutionRequest(
        Invocation("RUN-1", "H001", "INV-1"), "hello", repository,
        ROUTING_RESULT_ENVELOPE_CONTRACT, 15.0, permitted_routes=("done",),
    )
    outcome = adapter.execute(request)
    assert outcome.returncode == 0, outcome.stderr
    assert "ok=True blocked=True" in (outcome.candidate_result or "")
    assert not (repository / "unauthorized-dir" / "new.txt").exists()
    assert (repository / "authorized-dir" / "new.txt").read_text() == "ok\n"


def test_n10_cannot_read_another_checkouts_run_state_or_home(tmp_path):
    other_checkout_secret = tmp_path / "other-checkout" / "run.json"
    other_checkout_secret.parent.mkdir(parents=True)
    other_checkout_secret.write_text('{"secret": true}')
    repository = tmp_path / "repo"
    _init_repo(repository)
    fake = _write_fake_provider(repository, f'''\
        import pathlib, sys, json
        sys.stdin.read()
        args = sys.argv[1:]
        output = pathlib.Path(args[args.index("--output-last-message") + 1])
        reachable = pathlib.Path({str(other_checkout_secret)!r}).exists()
        output.write_text(json.dumps({{"schema_version":1,"run_id":"RUN-1","hop_id":"H001",
                                       "invocation_id":"INV-1","result":{{"schema_version":1,
                                       "status":"completed","requested_route":"done",
                                       "scope_changed":False,"requires_human":False,"escalation":None,
                                       "findings":[],"claimed_commits":[],"commit_intent":None,
                                       "evidence_refs":[],"summary":"reachable=" + str(reachable)}}}}))
    ''')
    _commit_all(repository, "test: baseline")
    grant = _codex_grant(repository, tmp_path, writable=())
    adapter = CodexCliAdapter(grant.provider_channel, model="m", reasoning_effort="high",
                               executable=str(fake), boundary=grant)
    from workflow_coordinator.adapter import AgentExecutionRequest, ROUTING_RESULT_ENVELOPE_CONTRACT
    request = AgentExecutionRequest(
        Invocation("RUN-1", "H001", "INV-1"), "hello", repository,
        ROUTING_RESULT_ENVELOPE_CONTRACT, 15.0, permitted_routes=("done",),
    )
    outcome = adapter.execute(request)
    assert outcome.returncode == 0, outcome.stderr
    assert "reachable=False" in (outcome.candidate_result or "")
    assert other_checkout_secret.read_text() == '{"secret": true}'


def test_n11_codex_channel_write_access_never_reaches_run_state_files(tmp_path):
    """The channel directory is writable for Codex, but the five Run-state
    file names are absent from the view no matter what the agent writes
    there, and the host-side Run-state directory (outside the channel) is
    byte-identical afterward (<<channel>>)."""
    repository = tmp_path / "repo"
    _init_repo(repository)
    run_state_names = ("run.json", "checkpoint.json", "dispatch.json", "execution-evidence.json", "usage.json")
    fake = _write_fake_provider(repository, f'''\
        import pathlib, sys, json
        sys.stdin.read()
        args = sys.argv[1:]
        output = pathlib.Path(args[args.index("--output-last-message") + 1])
        names = {run_state_names!r}
        present = [name for name in names if (output.parent / name).exists()]
        write_failed = []
        for name in names:
            try:
                (output.parent / name).write_text("agent-written")
            except OSError:
                write_failed.append(name)
        output.write_text(json.dumps({{"schema_version":1,"run_id":"RUN-1","hop_id":"H001",
                                       "invocation_id":"INV-1","result":{{"schema_version":1,
                                       "status":"completed","requested_route":"done",
                                       "scope_changed":False,"requires_human":False,"escalation":None,
                                       "findings":[],"claimed_commits":[],"commit_intent":None,
                                       "evidence_refs":[],
                                       "summary":"present=" + str(present) + " write_failed=" + str(sorted(write_failed))}}}}))
    ''')
    _commit_all(repository, "test: baseline")
    state = tmp_path / "state"
    association = register_checkout(state, repository)
    run_root = state / "checkouts" / association.checkout_id
    for name in run_state_names:
        (run_root / name).write_text("host-trusted")
    before = {name: (run_root / name).read_text() for name in run_state_names}

    grant = _codex_grant(repository, tmp_path, writable=())
    adapter = CodexCliAdapter(grant.provider_channel, model="m", reasoning_effort="high",
                               executable=str(fake), boundary=grant)
    from workflow_coordinator.adapter import AgentExecutionRequest, ROUTING_RESULT_ENVELOPE_CONTRACT
    request = AgentExecutionRequest(
        Invocation("RUN-1", "H001", "INV-1"), "hello", repository,
        ROUTING_RESULT_ENVELOPE_CONTRACT, 15.0, permitted_routes=("done",),
    )
    outcome = adapter.execute(request)
    assert outcome.returncode == 0, outcome.stderr
    assert "present=[]" in (outcome.candidate_result or "")
    for name in run_state_names:
        assert (run_root / name).read_text() == before[name]


def test_n7_hardlink_alias_created_before_launch_refuses_through_the_real_dispatch_path(tmp_path):
    """N7, the already-covered-but-dispatch-level half: a hardlink alias
    created before launch is rejected by the primitive's ``st_nlink != 1``
    defence (``tests/integration/test_execution_boundary_bwrap.py`` already
    proves this at the ``validate()``/``build_argv()``/``run()`` level); this
    case re-asserts it through the real CLI adapter's own ``execute()``, so
    the refusal is also proven at the dispatch surface the Coordinator
    actually calls. The refusal must happen before any provider process
    starts -- a not-started ``CodexCliError`` -- and the protected target
    must be unchanged."""
    repository = tmp_path / "repo"
    _init_repo(repository)
    writable = repository / "writable"
    writable.mkdir()
    protected = repository / "protected.txt"
    protected.write_text("protected content\n")
    alias = writable / "alias"
    alias.hardlink_to(protected)
    _commit_all(repository, "test: baseline")
    started_marker = tmp_path / "provider-started"
    fake = _write_fake_provider(repository, f'''\
        import pathlib
        pathlib.Path({str(started_marker)!r}).write_text("started")
    ''')
    grant = _codex_grant(repository, tmp_path, writable=("writable",))
    adapter = CodexCliAdapter(grant.provider_channel, model="m", reasoning_effort="high",
                               executable=str(fake), boundary=grant)
    from workflow_coordinator.adapter import AgentExecutionRequest, ROUTING_RESULT_ENVELOPE_CONTRACT
    request = AgentExecutionRequest(
        Invocation("RUN-1", "H001", "INV-1"), "hello", repository,
        ROUTING_RESULT_ENVELOPE_CONTRACT, 15.0, permitted_routes=("done",),
    )
    with pytest.raises(CodexCliError):
        adapter.execute(request)
    assert not started_marker.exists(), "the provider process must never have started"
    assert protected.read_text() == "protected content\n"


def test_n13_killing_a_timed_out_invocation_leaves_no_surviving_descendant(tmp_path):
    """N13, the behavioral half (the argv-flag half -- ``--unshare-pid`` and
    ``--die-with-parent`` present, ``--as-pid-1`` absent -- is covered by
    ``tests/unit/test_execution_boundary_config.py``): the fake provider
    double-forks a long sleeper that detaches into its own session via
    ``setsid()``, escaping the host-level process group the adapter's
    ``killpg`` targets. The invocation times out, the adapter runs its real
    kill sequence (SIGTERM, then SIGKILL on the sandboxed process group), and
    the detached descendant still dies, because it never left the sandbox's
    own PID namespace: when that namespace's PID 1 (bwrap itself, since
    ``--as-pid-1`` is not passed) dies, the kernel kills every remaining
    process in the namespace (<<mounts>>). The descendant's liveness is
    observed indirectly, by a heartbeat file it writes every 0.1s to the
    (host-visible) provider channel: it must stop advancing once the
    adapter's kill sequence has returned control."""
    repository = tmp_path / "repo"
    _init_repo(repository)
    channel = _channel(tmp_path)
    heartbeat = channel / "heartbeat.txt"
    fake = _write_fake_provider(repository, f'''\
        import os, pathlib, time
        heartbeat = pathlib.Path({str(heartbeat)!r})
        pid = os.fork()
        if pid == 0:
            os.setsid()
            pid2 = os.fork()
            if pid2 == 0:
                while True:
                    heartbeat.write_text(str(time.time()))
                    time.sleep(0.1)
            os._exit(0)
        os.waitpid(pid, 0)
        time.sleep(60)
    ''')
    _commit_all(repository, "test: baseline")
    grant = _codex_grant(repository, tmp_path, writable=())

    from workflow_coordinator.execution_boundary import build_argv
    from workflow_coordinator.execution_launcher import validate_grant
    argv = build_argv(validate_grant(grant), ['/bin/true'])
    assert '--unshare-pid' in argv
    assert '--die-with-parent' in argv
    assert '--as-pid-1' not in argv

    adapter = CodexCliAdapter(grant.provider_channel, model="m", reasoning_effort="high",
                               executable=str(fake), boundary=grant)
    from workflow_coordinator.adapter import AgentExecutionRequest, ROUTING_RESULT_ENVELOPE_CONTRACT
    request = AgentExecutionRequest(
        Invocation("RUN-1", "H001", "INV-1"), "hello", repository,
        ROUTING_RESULT_ENVELOPE_CONTRACT, 1.5, permitted_routes=("done",),
    )
    outcome = adapter.execute(request)
    assert outcome.timed_out is True
    assert heartbeat.exists(), "the detached descendant never ran"
    first = heartbeat.read_text()
    time.sleep(1.0)
    second = heartbeat.read_text()
    assert first == second, "a descendant survived the adapter's kill sequence and kept writing"


# -- F1/F2: the started signal and the per-invocation channel ---------------


def _forge_everything_reachable(channel: Path) -> str:
    """The script body a forging fake provider runs before exiting
    nonzero: delete, recreate and overwrite every file it can reach in the
    writable channel, including a name that looks like the retired marker.
    None of this may change the started/not-started classification (F1):
    the classification now rests on a pipe the confined process never has
    open at all."""
    return f'''\
        import pathlib, shutil, sys
        sys.stdin.read()
        channel = pathlib.Path({str(channel)!r})
        for child in channel.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        (channel / ".wfc-boundary-started").write_text("forged")
        (channel / "result-message.json").write_text("forged result")
        sys.exit(1)
    '''


def test_f1_a_provider_that_ran_and_forged_every_reachable_file_is_still_classified_as_started(tmp_path):
    """(1), the started half, Codex-shaped: a provider that actually ran,
    then deleted and forged every file it can reach in the writable channel
    (including a file named like the retired marker) and exited nonzero, is
    still classified as a *started* provider -- the classification no
    longer reads anything the confined process can reach at all."""
    repository = tmp_path / "repo"
    _init_repo(repository)
    grant = _codex_grant(repository, tmp_path, writable=())
    channel = grant.provider_channel
    fake = _write_fake_provider(repository, _forge_everything_reachable(channel))
    _commit_all(repository, "test: baseline")
    adapter = CodexCliAdapter(channel, model="m", reasoning_effort="high", executable=str(fake), boundary=grant)
    from workflow_coordinator.adapter import AgentExecutionRequest, ROUTING_RESULT_ENVELOPE_CONTRACT
    request = AgentExecutionRequest(
        Invocation("RUN-1", "H001", "INV-1"), "hello", repository,
        ROUTING_RESULT_ENVELOPE_CONTRACT, 15.0, permitted_routes=("done",),
    )
    outcome = adapter.execute(request)
    assert outcome.timed_out is False
    assert outcome.returncode == 1
    assert "Codex CLI exited 1" in outcome.diagnostics


def test_f1_claude_shaped_provider_that_ran_and_forged_the_scratch_is_still_classified_as_started(tmp_path):
    """(1), the started half, Claude-shaped: the Claude channel is
    read-only, so the external scratch bound at ``/tmp/s`` is the only
    writable, host-visible location reachable at all; forging it (and
    writing a file shaped like the retired marker there) must not change
    the classification either."""
    repository = tmp_path / "repo"
    _init_repo(repository)
    fake = _write_fake_provider(repository, '''\
        import pathlib, sys
        scratch = pathlib.Path("/tmp/s")
        (scratch / ".wfc-boundary-started").write_text("forged")
        for child in scratch.iterdir():
            try:
                child.unlink()
            except IsADirectoryError:
                pass
        sys.exit(1)
    ''')
    _commit_all(repository, "test: baseline")
    grant = _claude_grant(repository, tmp_path, writable=())
    adapter = ClaudeCliAdapter(grant.provider_channel, model="m", reasoning_effort="high",
                                executable=str(fake), boundary=grant)
    from workflow_coordinator.adapter import AgentExecutionRequest, ROUTING_RESULT_ENVELOPE_CONTRACT
    request = AgentExecutionRequest(
        Invocation("RUN-1", "H001", "INV-1"), "hello", repository,
        ROUTING_RESULT_ENVELOPE_CONTRACT, 15.0, permitted_routes=("done",),
    )
    outcome = adapter.execute(request)
    assert outcome.timed_out is False
    assert outcome.returncode == 1
    assert "Claude CLI exited 1" in outcome.diagnostics


def test_f1_a_boundary_that_fails_before_exec_is_still_classified_as_not_started(tmp_path):
    """(1), the not-started half, exercising the real signal-based
    classification rather than the pre-launch ``validate()`` shortcut N7
    already covers: the command `bwrap` is asked to exec does not exist, so
    `bwrap` forks (writing `child-pid`) and then fails to exec it -- never
    writing an `exit-code` document. This is fact (c) from the maintainer's
    own candidate description: an exec failure looks, on the wire, exactly
    like a mount-setup failure, and both must classify as not-started."""
    repository = tmp_path / "repo"
    _init_repo(repository)
    (repository / "placeholder.txt").write_text("placeholder\n")
    _commit_all(repository, "test: baseline")
    grant = _codex_grant(repository, tmp_path, writable=())
    missing_executable = str(repository / "does-not-exist")
    adapter = CodexCliAdapter(grant.provider_channel, model="m", reasoning_effort="high",
                               executable=missing_executable, boundary=grant)
    from workflow_coordinator.adapter import AgentExecutionRequest, ROUTING_RESULT_ENVELOPE_CONTRACT
    request = AgentExecutionRequest(
        Invocation("RUN-1", "H001", "INV-1"), "hello", repository,
        ROUTING_RESULT_ENVELOPE_CONTRACT, 15.0, permitted_routes=("done",),
    )
    with pytest.raises(CodexCliError, match="execution boundary failed before the provider started"):
        adapter.execute(request)


_RETRY_RESULT = (
    '{"schema_version":1,"run_id":"RUN-1","hop_id":"H001","invocation_id":"INV-1",'
    '"result":{"schema_version":1,"status":"completed","requested_route":"retry",'
    '"scope_changed":false,"requires_human":false,"escalation":null,"findings":[],'
    '"claimed_commits":[],"commit_intent":{"paths":["%s"],"subject":"docs(workflow): WI-1 H001 fixture"},'
    '"evidence_refs":[],"summary":"hop1"}}'
)


def test_f2_a_second_boundary_hop_is_refused_without_any_state_from_the_first_hop_leaking_in(tmp_path):
    """(2): two consecutive boundary-selected Hops of one Run through the
    real dispatch path, the channel directory derived exactly as
    ``operator.py``'s dispatch frame derives it (per invocation, under the
    Run's own state root). The first Hop succeeds and leaves its own result
    in its own channel directory. The second Hop is given a genuine
    ``bwrap``-setup failure (P5/P6): its provider executable does not
    exist, so `bwrap` forks (writing `child-pid`) and then fails to exec
    it, writing no `exit-code` document -- the real started-signal
    classification, not the pre-launch ``validate()`` shortcut N7 already
    covers. The second Hop must be classified not-started by that real
    decision, and nothing about Hop 1's channel, result or state may be
    visible to or required by it."""
    repository = tmp_path / "repo"
    _init_repo(repository)
    (repository / "writable").mkdir()
    fake = _write_fake_provider(repository, f'''\
        import pathlib, sys
        sys.stdin.read()
        args = sys.argv[1:]
        output = pathlib.Path(args[args.index("--output-last-message") + 1])
        pathlib.Path("writable/hop1.txt").write_text("hop1\\n")
        output.write_text({_RETRY_RESULT % "writable/hop1.txt"!r})
    ''')
    head = _commit_all(repository, "test: baseline")
    workflow = profile({"work": replace(
        phase({"done": route("complete"), "retry": route("goto", "work")}, role="worker"),
        authorized_scope=("writable", "fake-provider.py"),
    )})
    state = tmp_path / "state"
    association = register_checkout(state, repository)
    run_root = checkout_state_root(state, association)
    auth = _auth(tmp_path)

    def _grant_for(invocation_id: str) -> BoundaryGrant:
        channel = run_root / "provider-channel" / invocation_id
        channel.mkdir(parents=True)
        return BoundaryGrant(
            mode="bubblewrap", repository=repository, writable_repository_paths=("writable",),
            readable_host_paths=(auth,), provider_channel=channel, provider_channel_writable=True,
        )

    run = replace(run_for(workflow, budget=2), current_head=head, baseline_head=head)
    grant1 = _grant_for("INV-1")
    adapter1 = CodexCliAdapter(grant1.provider_channel, model="m", reasoning_effort="high",
                                executable=str(fake), boundary=grant1)
    hop1 = run_real_one_hop(
        run, workflow, repository, _components(),
        agent_operating_contract=load_agent_operating_contract(),
        state_root=state, association=association,
        task_payload="fixture", invocation_id="INV-1", persona="fixture persona",
        timeout_seconds=15, executable=str(fake), adapter=adapter1,
    )
    assert hop1.reduction.run.status.value == "ready"
    assert hop1.reduction.run.hop_used == 1
    assert (repository / "writable" / "hop1.txt").read_text() == "hop1\n"
    hop1_channel_contents = sorted(p.name for p in grant1.provider_channel.iterdir())
    assert "result-message.json" in hop1_channel_contents

    # Hop 2 gets a genuine bwrap-setup failure, not a host-side mutation: its
    # provider executable does not exist, so bwrap forks and then fails to
    # exec it, exactly fact (c) of the maintainer's candidate description.
    grant2 = _grant_for("INV-2")
    missing_executable = str(repository / "does-not-exist")
    adapter2 = CodexCliAdapter(grant2.provider_channel, model="m", reasoning_effort="high",
                                executable=missing_executable, boundary=grant2)
    with pytest.raises(Slice4Error, match="execution boundary failed before the provider started"):
        run_real_one_hop(
            hop1.reduction.run, workflow, repository, _components(),
            agent_operating_contract=load_agent_operating_contract(),
            state_root=state, association=association,
            task_payload="fixture", invocation_id="INV-2", persona="fixture persona",
            timeout_seconds=15, executable=missing_executable, adapter=adapter2,
        )
    assert grant1.provider_channel != grant2.provider_channel
    # Hop 1's own channel and result are exactly as Hop 1 left them -- Hop
    # 2's not-started classification reads nothing from it and writes
    # nothing into it.
    assert sorted(p.name for p in grant1.provider_channel.iterdir()) == hop1_channel_contents
    assert not (run_root / "dispatch.json").exists()

import os
from pathlib import Path

import pytest

from workflow_coordinator.execution_boundary import EXTERNAL_SCRATCH_DESTINATION, BoundaryConfigurationError
from workflow_coordinator.execution_launcher import (
    AbsentWritableScopeEntryError, BoundaryGrant, LaunchPlan, StartedSignal, close_started_signal_write_end,
    derive_boundary_grant, direct_grant, missing_writable_targets, normalized_writable_scope,
    open_started_signal, prepare, prepare_bubblewrap, prepare_direct, provider_started,
)


def test_prepare_direct_is_byte_identical_to_the_plan(tmp_path: Path):
    grant = direct_grant(tmp_path)
    plan = LaunchPlan(grant, ("claude", "-p", "hello"), tmp_path, {"PATH": "/usr/bin"})
    prepared = prepare(plan)
    assert prepared.argv == plan.argv
    assert prepared.cwd == plan.cwd
    assert prepared.environment == plan.environment
    assert prepared.boundary == "direct"
    assert prepare_direct(plan) == prepared


def test_prepare_direct_reads_nothing_from_the_grant(tmp_path: Path):
    """An adapter constructed without a boundary behaves exactly as it does
    today, regardless of what a direct-mode grant happens to carry."""
    from workflow_coordinator.execution_launcher import PreparedLaunch

    grant = BoundaryGrant(mode="direct", repository=tmp_path, writable_repository_paths=("src",))
    plan = LaunchPlan(grant, ("codex",), None, None)
    assert prepare(plan) == PreparedLaunch(("codex",), None, None, "direct")


def test_unrecognised_mode_is_a_refusal_never_a_direct_launch(tmp_path: Path):
    grant = BoundaryGrant(mode="chroot", repository=tmp_path)
    plan = LaunchPlan(grant, ("claude",), tmp_path, None)
    with pytest.raises(BoundaryConfigurationError, match="unrecognised boundary mode"):
        prepare(plan)


def test_normalized_writable_scope_drops_contained_entries():
    assert normalized_writable_scope(["src", "src/pkg", "tests", "tests/unit/x.py"]) == ("src", "tests")


def test_normalized_writable_scope_deduplicates_and_sorts():
    assert normalized_writable_scope(["b", "a", "a"]) == ("a", "b")


def test_normalized_writable_scope_identity_for_already_disjoint_entries():
    assert normalized_writable_scope(["docs/a.adoc", "docs/b.adoc", "src"]) == ("docs/a.adoc", "docs/b.adoc", "src")


@pytest.mark.parametrize("malformed", ["/etc/passwd", "..", "../etc", "a/../b", "a/..", ""])
def test_normalized_writable_scope_refuses_a_list_that_cannot_be_normalized(malformed: str):
    """N15: an entry that is not a canonical repository-relative path (an
    absolute path, an empty entry, or one escaping through ``..``) cannot be
    reconciled into a disjoint repository-relative set at all, and is
    refused before dispatch -- never silently compared against the rest."""
    with pytest.raises(BoundaryConfigurationError, match="cannot be normalized to a disjoint set"):
        normalized_writable_scope(["src", malformed])


def test_derive_boundary_grant_rejects_unknown_mode(tmp_path: Path):
    with pytest.raises(BoundaryConfigurationError, match="unrecognised boundary mode"):
        derive_boundary_grant(mode="chroot", repository=tmp_path, authorized_scope=())


def test_derive_boundary_grant_direct_mode_carries_no_scope_or_channel(tmp_path: Path):
    grant = derive_boundary_grant(
        mode="direct", repository=tmp_path, authorized_scope=("src", "tests"),
        provider_channel=tmp_path / "channel",
    )
    assert grant.mode == "direct"
    assert grant.writable_repository_paths == ()
    assert grant.provider_channel is None


def test_derive_boundary_grant_resolves_a_not_yet_existing_artifact_to_its_existing_parent(tmp_path: Path):
    """The realistic reviewer-artifact shape: the artifact file does not yet
    exist, but the directory holding it (and other records) already does.
    Known file-shaped only because it is named in ``artifact_paths`` (the
    executing phase's own declared durable artifacts); the grant must be
    that existing directory, with nothing pre-created -- pre-creating a
    directory at the artifact's own path would block the agent from ever
    writing the artifact as a file."""
    (tmp_path / "docs" / "warm_storage").mkdir(parents=True)
    (tmp_path / "docs" / "warm_storage" / "other_record.adoc").write_text("x")
    grant = derive_boundary_grant(
        mode="bubblewrap", repository=tmp_path,
        authorized_scope=("docs/warm_storage/new_review.adoc",),
        artifact_paths=("docs/warm_storage/new_review.adoc",),
    )
    assert grant.writable_repository_paths == ("docs/warm_storage",)
    assert missing_writable_targets(grant) == ()


def test_derive_boundary_grant_refuses_an_absent_non_artifact_entry_even_with_an_existing_ancestor(
    tmp_path: Path,
):
    """F5: no file extension is a reliable shape signal (<<mounts>>), so an
    absent entry that is not a declared durable artifact is refused by name
    -- never silently pre-created as a directory -- regardless of whether an
    existing ancestor is available."""
    (tmp_path / "docs" / "warm_storage").mkdir(parents=True)
    with pytest.raises(AbsentWritableScopeEntryError, match="docs/warm_storage/new_chapter"):
        derive_boundary_grant(
            mode="bubblewrap", repository=tmp_path,
            authorized_scope=("docs/warm_storage/new_chapter",),
        )


def test_derive_boundary_grant_refuses_an_absent_entry_with_no_existing_ancestor_either(tmp_path: Path):
    with pytest.raises(AbsentWritableScopeEntryError, match="brand-new/nested/dir"):
        derive_boundary_grant(
            mode="bubblewrap", repository=tmp_path, authorized_scope=("brand-new/nested/dir",),
        )


def test_derive_boundary_grant_grants_an_existing_entry_directly(tmp_path: Path):
    (tmp_path / "src").mkdir()
    grant = derive_boundary_grant(mode="bubblewrap", repository=tmp_path, authorized_scope=("src",))
    assert grant.writable_repository_paths == ("src",)
    assert missing_writable_targets(grant) == ()


def test_derive_boundary_grant_never_overgrants_to_the_repository_root(tmp_path: Path):
    """A brand-new top-level entry (whose only existing ancestor would be
    the repository root itself) is refused, never resolved to granting the
    whole repository and never silently pre-created."""
    with pytest.raises(AbsentWritableScopeEntryError):
        derive_boundary_grant(mode="bubblewrap", repository=tmp_path, authorized_scope=("new-top-level.txt",))


def test_derive_boundary_grant_refuses_a_declared_artifact_with_no_existing_ancestor_at_all(tmp_path: Path):
    """The degenerate case of the artifact-resolution rule: when not even
    the artifact's own repository-root ancestor exists, the Opus review's
    F5 detail flagged the old behaviour as pre-creating the artifact's own
    path as a directory too -- now refused by the same named error rather
    than silently widened to the repository root."""
    with pytest.raises(AbsentWritableScopeEntryError, match="no existing ancestor"):
        derive_boundary_grant(
            mode="bubblewrap", repository=tmp_path,
            authorized_scope=("brand-new/nested/review.adoc",),
            artifact_paths=("brand-new/nested/review.adoc",),
        )


def test_derive_boundary_grant_bubblewrap_mode_normalizes_scope_and_auth(tmp_path: Path):
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    auth = tmp_path / "auth"
    channel = tmp_path / "channel"
    grant = derive_boundary_grant(
        mode="bubblewrap", repository=tmp_path, authorized_scope=("src", "src/pkg", "tests"),
        provider_channel=channel, provider_channel_writable=True, authentication_path=auth,
    )
    assert grant.writable_repository_paths == ("src", "tests")
    assert grant.provider_channel == channel
    assert grant.provider_channel_writable is True
    assert grant.readable_host_paths == (auth,)


def test_missing_writable_targets_only_applies_to_bubblewrap_mode(tmp_path: Path):
    grant = BoundaryGrant(mode="direct", repository=tmp_path, writable_repository_paths=("absent",))
    assert missing_writable_targets(grant) == ()


def test_missing_writable_targets_reports_only_absent_entries(tmp_path: Path):
    (tmp_path / "existing").mkdir()
    grant = BoundaryGrant(
        mode="bubblewrap", repository=tmp_path, writable_repository_paths=("existing", "absent"),
    )
    assert missing_writable_targets(grant) == (tmp_path / "absent",)


def test_prepare_bubblewrap_refuses_a_populated_writable_host_paths(tmp_path: Path):
    (tmp_path / '.git').mkdir()
    grant = BoundaryGrant(mode="bubblewrap", repository=tmp_path, writable_host_paths=(tmp_path / "x",))
    plan = LaunchPlan(grant, ("true",), None, None)
    with pytest.raises(BoundaryConfigurationError, match="no writable host mount kind"):
        prepare_bubblewrap(plan)


def test_prepare_bubblewrap_builds_argv_through_the_primitive(tmp_path: Path):
    (tmp_path / '.git').mkdir()
    (tmp_path / 'src').mkdir()
    grant = BoundaryGrant(mode="bubblewrap", repository=tmp_path, writable_repository_paths=("src",))
    read_fd, write_fd = os.pipe()
    try:
        plan = LaunchPlan(grant, ("/bin/true",), None, {"PATH": "/usr/bin"}, status_fd=write_fd)
        prepared = prepare(plan)
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert prepared.boundary == "bubblewrap"
    assert prepared.cwd is None
    assert prepared.environment == {"PATH": "/usr/bin"}
    assert prepared.argv[-2:] == ("--", "/bin/true")
    assert str(tmp_path / 'src') in prepared.argv


def test_prepare_bubblewrap_requires_a_status_descriptor(tmp_path: Path):
    """F1: a bubblewrap launch with no started-signal pipe is refused before
    any process starts -- the confined agent must never be the only proof
    that a provider started, so the launcher does not silently proceed
    without a trusted signal."""
    (tmp_path / '.git').mkdir()
    grant = BoundaryGrant(mode="bubblewrap", repository=tmp_path)
    plan = LaunchPlan(grant, ("/bin/true",), None, None)
    with pytest.raises(BoundaryConfigurationError, match="started-signal status descriptor"):
        prepare_bubblewrap(plan)


def test_prepare_bubblewrap_emits_json_status_fd_and_no_marker_wrapper(tmp_path: Path):
    """F1: the command argv is the plan's own argv, unwrapped -- no
    ``.wfc-boundary-started`` marker file and no ``/bin/sh`` touch-first
    wrapper; the trusted signal is `bwrap`'s own ``--json-status-fd``."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    grant = BoundaryGrant(mode="bubblewrap", repository=repo)
    read_fd, write_fd = os.pipe()
    try:
        plan = LaunchPlan(grant, ("/bin/true",), None, None, status_fd=write_fd)
        prepared = prepare(plan)
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert prepared.argv[-2:] == ("--", "/bin/true")
    index = prepared.argv.index("--json-status-fd")
    assert prepared.argv[index + 1] == str(write_fd)
    assert ".wfc-boundary-started" not in " ".join(prepared.argv)
    assert "sh" not in prepared.argv[-3:-1]


def test_open_started_signal_round_trips_through_the_pipe():
    signal = open_started_signal()
    try:
        os.write(signal.write_fd, b'{"child-pid": 1}{"exit-code": 0}')
        close_started_signal_write_end(signal)
        assert provider_started(signal) is True
    finally:
        os.close(signal.read_fd)


def test_provider_started_false_when_only_a_child_pid_document_arrives():
    """bwrap writes `child-pid` as soon as it forks, before it knows whether
    exec-ing the wrapped command will even succeed; its presence alone must
    never be read as proof the provider started."""
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b'{"child-pid": 123}')
    os.close(write_fd)
    signal = StartedSignal(read_fd, write_fd)
    try:
        assert provider_started(signal) is False
    finally:
        os.close(read_fd)


def test_provider_started_false_when_the_pipe_is_empty():
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    signal = StartedSignal(read_fd, write_fd)
    try:
        assert provider_started(signal) is False
    finally:
        os.close(read_fd)


def test_provider_started_true_for_a_nonzero_exit_code_document():
    """A command that ran and exited nonzero is still a started provider
    (<<enablement>>, error classification): the exit-code value itself does
    not matter, only that the document exists at all."""
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b'{"child-pid": 1}{"exit-code": 3}')
    os.close(write_fd)
    signal = StartedSignal(read_fd, write_fd)
    try:
        assert provider_started(signal) is True
    finally:
        os.close(read_fd)


def test_prepare_bubblewrap_with_an_invalid_grant_refuses_before_any_process(tmp_path: Path):
    """No real .git here: validate() refuses, and no subprocess is ever built."""
    grant = BoundaryGrant(mode="bubblewrap", repository=tmp_path)
    plan = LaunchPlan(grant, ("/bin/true",), None, None)
    with pytest.raises(BoundaryConfigurationError):
        prepare(plan)

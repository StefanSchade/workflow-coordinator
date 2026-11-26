from pathlib import Path

import pytest

from workflow_coordinator.execution_boundary import EXTERNAL_SCRATCH_DESTINATION, BoundaryConfigurationError
from workflow_coordinator.execution_launcher import (
    BoundaryGrant, LaunchPlan, boundary_started_marker, derive_boundary_grant, direct_grant,
    missing_writable_targets, normalized_writable_scope, prepare, prepare_bubblewrap, prepare_direct,
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


def test_derive_boundary_grant_without_artifact_paths_pre_creates_a_nested_directory(tmp_path: Path):
    """No file extension is a reliable shape signal (<<mounts>>): a missing
    entry absent from ``artifact_paths`` is treated as a directory to be
    pre-created at its own path, even when an existing ancestor is
    available -- the worker-phase default, as opposed to the declared-
    artifact case above."""
    (tmp_path / "docs" / "warm_storage").mkdir(parents=True)
    grant = derive_boundary_grant(
        mode="bubblewrap", repository=tmp_path,
        authorized_scope=("docs/warm_storage/new_chapter",),
    )
    assert grant.writable_repository_paths == ("docs/warm_storage/new_chapter",)
    assert missing_writable_targets(grant) == (tmp_path / "docs/warm_storage/new_chapter",)


def test_derive_boundary_grant_pre_creates_a_directory_with_no_existing_ancestor(tmp_path: Path):
    grant = derive_boundary_grant(
        mode="bubblewrap", repository=tmp_path, authorized_scope=("brand-new/nested/dir",),
    )
    assert grant.writable_repository_paths == ("brand-new/nested/dir",)
    assert missing_writable_targets(grant) == (tmp_path / "brand-new/nested/dir",)


def test_derive_boundary_grant_grants_an_existing_entry_directly(tmp_path: Path):
    (tmp_path / "src").mkdir()
    grant = derive_boundary_grant(mode="bubblewrap", repository=tmp_path, authorized_scope=("src",))
    assert grant.writable_repository_paths == ("src",)
    assert missing_writable_targets(grant) == ()


def test_derive_boundary_grant_never_overgrants_to_the_repository_root(tmp_path: Path):
    """A brand-new top-level entry (whose only existing ancestor would be
    the repository root itself) is pre-created, never resolved to granting
    the whole repository."""
    grant = derive_boundary_grant(mode="bubblewrap", repository=tmp_path, authorized_scope=("new-top-level.txt",))
    assert grant.writable_repository_paths == ("new-top-level.txt",)
    assert missing_writable_targets(grant) == (tmp_path / "new-top-level.txt",)


def test_derive_boundary_grant_bubblewrap_mode_normalizes_scope_and_auth(tmp_path: Path):
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
    plan = LaunchPlan(grant, ("/bin/true",), None, {"PATH": "/usr/bin"})
    prepared = prepare(plan)
    assert prepared.boundary == "bubblewrap"
    assert prepared.cwd is None
    assert prepared.environment == {"PATH": "/usr/bin"}
    assert prepared.argv[-2:] == ("--", "/bin/true")
    assert str(tmp_path / 'src') in prepared.argv


def test_boundary_started_marker_absent_for_direct_mode(tmp_path: Path):
    assert boundary_started_marker(direct_grant(tmp_path)) is None


def test_boundary_started_marker_absent_without_a_writable_outside_location(tmp_path: Path):
    grant = BoundaryGrant(mode="bubblewrap", repository=tmp_path)
    assert boundary_started_marker(grant) is None


def test_boundary_started_marker_prefers_writable_channel_over_scratch(tmp_path: Path):
    channel = tmp_path / "channel"
    scratch = tmp_path / "scratch"
    grant = BoundaryGrant(
        mode="bubblewrap", repository=tmp_path, provider_channel=channel,
        provider_channel_writable=True, external_scratch=scratch,
    )
    in_view, host = boundary_started_marker(grant)
    assert host == channel / ".wfc-boundary-started"
    assert str(in_view) == str(host)  # the channel is mounted at its own path (identity)


def test_boundary_started_marker_falls_back_to_external_scratch(tmp_path: Path):
    scratch = tmp_path / "scratch"
    grant = BoundaryGrant(mode="bubblewrap", repository=tmp_path, external_scratch=scratch)
    in_view, host = boundary_started_marker(grant)
    assert host == scratch / ".wfc-boundary-started"
    assert str(in_view) == f"{EXTERNAL_SCRATCH_DESTINATION}/.wfc-boundary-started"


def test_prepare_bubblewrap_wraps_the_command_to_touch_the_marker_first(tmp_path: Path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.git').mkdir()
    channel = tmp_path / "channel"
    channel.mkdir()
    grant = BoundaryGrant(
        mode="bubblewrap", repository=repo, provider_channel=channel, provider_channel_writable=True,
    )
    plan = LaunchPlan(grant, ("/bin/true",), None, None)
    prepared = prepare(plan)
    assert prepared.argv[-3:] == ('sh', str(channel / '.wfc-boundary-started'), '/bin/true')
    assert ': > "$1"; shift; exec "$@"' in prepared.argv


def test_prepare_bubblewrap_with_an_invalid_grant_refuses_before_any_process(tmp_path: Path):
    """No real .git here: validate() refuses, and no subprocess is ever built."""
    grant = BoundaryGrant(mode="bubblewrap", repository=tmp_path)
    plan = LaunchPlan(grant, ("/bin/true",), None, None)
    with pytest.raises(BoundaryConfigurationError):
        prepare(plan)

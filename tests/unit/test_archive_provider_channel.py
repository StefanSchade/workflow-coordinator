"""The provider-channel archive extension keeps both file shapes working.

Moving the three provider-channel files one level deeper (<<channel>>) must
not stop a recorded historical run (whose files sit directly in the
checkout directory) from archiving unchanged, and must additionally reach
the same three files when they live in the new ``provider-channel``
subdirectory.
"""
from pathlib import Path

from workflow_coordinator.frontdoor.archive import _plan_run_files, collect_run_files


def _run_dir_with_checkout(tmp_path: Path, checkout_id: str = "checkout-1") -> Path:
    run_dir = tmp_path / "run"
    checkout_dir = run_dir / "checkouts" / checkout_id
    checkout_dir.mkdir(parents=True)
    return checkout_dir


def test_legacy_depth_one_checkout_files_still_archive_unchanged(tmp_path):
    checkout_dir = _run_dir_with_checkout(tmp_path)
    (checkout_dir / "run.json").write_text("{}")
    (checkout_dir / "structured-result.schema.json").write_text("{}")
    (checkout_dir / "result-message.json").write_text("{}")

    planned = _plan_run_files(checkout_dir.parents[1])
    assert {p.name for p in planned} == {"run.json", "structured-result.schema.json", "result-message.json"}
    assert all(p.parent == checkout_dir for p in planned)


def test_provider_channel_subdirectory_files_are_also_archived(tmp_path):
    checkout_dir = _run_dir_with_checkout(tmp_path)
    (checkout_dir / "run.json").write_text("{}")
    channel_dir = checkout_dir / "provider-channel"
    channel_dir.mkdir()
    (channel_dir / "structured-result.schema.json").write_text('{"channel": true}')
    (channel_dir / "result-message.json").write_text("channel result")
    (channel_dir / "claude-closed-settings.json").write_text("{}")

    run_dir = checkout_dir.parents[1]
    planned = _plan_run_files(run_dir)
    assert {p.name for p in planned} == {
        "run.json", "structured-result.schema.json", "result-message.json", "claude-closed-settings.json",
    }
    channel_planned = {p for p in planned if p.parent == channel_dir}
    assert len(channel_planned) == 3

    dest_root = tmp_path / "dest"
    copied: list[dict] = []
    collect_run_files(run_dir, dest_root, copied)
    relative_paths = {entry["path"] for entry in copied}
    assert "run/checkouts/checkout-1/run.json" in relative_paths
    assert "run/checkouts/checkout-1/provider-channel/structured-result.schema.json" in relative_paths
    assert "run/checkouts/checkout-1/provider-channel/result-message.json" in relative_paths
    assert "run/checkouts/checkout-1/provider-channel/claude-closed-settings.json" in relative_paths
    for entry in copied:
        if entry["path"].endswith("provider-channel/structured-result.schema.json"):
            assert (dest_root / entry["path"]).read_text() == '{"channel": true}'

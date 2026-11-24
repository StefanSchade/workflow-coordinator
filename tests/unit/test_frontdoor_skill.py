"""``wfc skill install``/``status``/``show`` (FTR-WFC-AGENT-SKILL-DISTRIBUTION,
slice S2): the installed-state machine for both hosts and both scopes, status
semantics including host independence, project-scope write boundaries, and
the dispatcher. Every test that installs uses a temporary ``HOME``, never the
real one.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from workflow_coordinator.frontdoor import skill
from workflow_coordinator.frontdoor.cli import main as cli_main
from workflow_coordinator.implementation_identity import VERSION

HOSTS = ("claude", "codex")


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *args), text=True, capture_output=True, check=True,
    ).stdout.strip()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return home


def _run(args: list[str]) -> int:
    return cli_main(["skill", *args])


def _user_destination(home: Path, host: str) -> Path:
    if host == "claude":
        return home / ".claude" / "skills" / "wfc"
    return home / ".agents" / "skills" / "wfc"


def _tree_bytes(destination: Path) -> dict[str, bytes]:
    """Installed files only, excluding the top-level marker."""

    return {
        path.relative_to(destination).as_posix(): path.read_bytes()
        for path in sorted(destination.rglob("*"))
        if path.is_file() and path.relative_to(destination).as_posix() != skill.MARKER_NAME
    }


def _write_tree(destination: Path, content: dict[str, bytes]) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for relative, data in content.items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


_FOREIGN_CONTENT = {
    "SKILL.md": b"not a wfc skill at all\n",
    "references/setup.md": b"unrelated content\n",
    "references/commission.md": b"unrelated content\n",
    "references/stops-and-evidence.md": b"unrelated content\n",
}


def _write_stale_installation(destination: Path, *, version: str = "0.0.0-old") -> None:
    """A WFC-installed copy of an older, different skill version: its own
    files match its own marker exactly, and differ from today's package."""

    _write_tree(destination, _FOREIGN_CONTENT)
    hashes = {relative: hashlib.sha256(data).hexdigest() for relative, data in _FOREIGN_CONTENT.items()}
    marker = {"schema": 1, "wfc_version": version, "files": hashes}
    (destination / skill.MARKER_NAME).write_text(json.dumps(marker), encoding="utf-8")


def _write_modified_installation(destination: Path) -> None:
    """A once-WFC-installed copy (valid marker) subsequently hand-edited:
    differs from both its own marker and the package."""

    _write_stale_installation(destination)
    (destination / "SKILL.md").write_bytes(b"hand-edited after install\n")


def _write_foreign_installation(destination: Path) -> None:
    """No marker at all, and not byte-identical to the package."""

    _write_tree(destination, _FOREIGN_CONTENT)


# --------------------------------------------------------------------------
# Install: the state machine, both hosts, user scope.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("host", HOSTS)
def test_install_absent_creates_installation(isolated_home, host):
    destination = _user_destination(isolated_home, host)
    assert not destination.exists()
    assert _run(["install", host]) == 0
    assert destination.is_dir()
    marker = json.loads((destination / skill.MARKER_NAME).read_text(encoding="utf-8"))
    assert marker == {"schema": 1, "wfc_version": VERSION, "files": marker["files"]}
    assert "SKILL.md" in marker["files"]
    for value in marker["files"].values():
        assert len(value) == 64


@pytest.mark.parametrize("host", HOSTS)
def test_install_second_time_is_idempotent_with_identical_bytes(isolated_home, host):
    assert _run(["install", host]) == 0
    destination = _user_destination(isolated_home, host)
    before = _tree_bytes(destination)
    assert _run(["install", host]) == 0
    assert _tree_bytes(destination) == before


@pytest.mark.parametrize("host", HOSTS)
def test_install_stale_is_replaced_without_force(isolated_home, host):
    destination = _user_destination(isolated_home, host)
    _write_stale_installation(destination)
    assert _run(["install", host]) == 0
    marker = json.loads((destination / skill.MARKER_NAME).read_text(encoding="utf-8"))
    assert marker["wfc_version"] == VERSION
    assert (destination / "SKILL.md").read_bytes() != _FOREIGN_CONTENT["SKILL.md"]


@pytest.mark.parametrize("host", HOSTS)
def test_install_modified_refuses_without_force_and_changes_nothing(isolated_home, host):
    destination = _user_destination(isolated_home, host)
    _write_modified_installation(destination)
    before = _tree_bytes(destination)
    assert _run(["install", host]) == 3
    assert _tree_bytes(destination) == before


@pytest.mark.parametrize("host", HOSTS)
def test_install_foreign_refuses_without_force_and_changes_nothing(isolated_home, host):
    destination = _user_destination(isolated_home, host)
    _write_foreign_installation(destination)
    before = _tree_bytes(destination)
    assert _run(["install", host]) == 3
    assert _tree_bytes(destination) == before


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("writer", [_write_modified_installation, _write_foreign_installation])
def test_install_force_replaces_modified_and_foreign(isolated_home, host, writer):
    destination = _user_destination(isolated_home, host)
    writer(destination)
    assert _run(["install", host, "--force"]) == 0
    marker = json.loads((destination / skill.MARKER_NAME).read_text(encoding="utf-8"))
    assert marker["wfc_version"] == VERSION
    assert _tree_bytes(destination) == skill._packaged_files()


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize(
    "writer_and_code",
    [(None, 0), (_write_stale_installation, 0), (_write_modified_installation, 3), (_write_foreign_installation, 3)],
)
def test_install_dry_run_leaves_filesystem_identical_and_returns_real_code(isolated_home, host, writer_and_code):
    writer, expected_code = writer_and_code
    destination = _user_destination(isolated_home, host)
    if writer is not None:
        writer(destination)
    before_exists = destination.exists()
    before = _tree_bytes(destination) if before_exists else None
    assert _run(["install", host, "--dry-run"]) == expected_code
    after_exists = destination.exists()
    assert after_exists == before_exists
    if before_exists:
        assert _tree_bytes(destination) == before


@pytest.mark.parametrize("host", HOSTS)
def test_install_dry_run_on_absent_writes_nothing(isolated_home, host):
    destination = _user_destination(isolated_home, host)
    assert _run(["install", host, "--dry-run"]) == 0
    assert not destination.exists()


@pytest.mark.parametrize("host", HOSTS)
def test_install_symlink_destination_refused_with_or_without_force(isolated_home, host, tmp_path):
    destination = _user_destination(isolated_home, host)
    destination.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "symlink-target"
    target.mkdir()
    (target / "marker.txt").write_text("not a skill\n", encoding="utf-8")
    destination.symlink_to(target)

    assert _run(["install", host]) == 2
    assert destination.is_symlink()
    assert destination.resolve() == target.resolve()

    assert _run(["install", host, "--force"]) == 2
    assert destination.is_symlink()
    assert destination.resolve() == target.resolve()
    assert (target / "marker.txt").read_text(encoding="utf-8") == "not a skill\n"


def test_claude_config_dir_is_honoured(isolated_home, monkeypatch, tmp_path):
    custom = tmp_path / "custom-claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(custom))
    assert _run(["install", "claude"]) == 0
    assert (custom / "skills" / "wfc" / "SKILL.md").is_file()
    assert not (isolated_home / ".claude").exists()


@pytest.mark.parametrize("host", HOSTS)
def test_skills_dir_is_honoured(isolated_home, host, tmp_path):
    custom = tmp_path / "relocated-skills"
    assert _run(["install", host, "--skills-dir", str(custom)]) == 0
    assert (custom / "wfc" / "SKILL.md").is_file()
    assert not _user_destination(isolated_home, host).exists()


# --------------------------------------------------------------------------
# Status semantics.
# --------------------------------------------------------------------------

def _status_json(host: str, **extra_args: str) -> dict:
    args = ["status", host, "--json"]
    for flag, value in extra_args.items():
        args.extend([f"--{flag.replace('_', '-')}", value])
    captured: dict = {}

    import io
    import contextlib

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rc = _run(args)
    assert rc == 0
    captured = json.loads(buffer.getvalue())
    return captured


def _status_text(host: str, capsys: pytest.CaptureFixture, **kwargs) -> str:
    capsys.readouterr()
    args = ["status", host]
    for flag, value in kwargs.items():
        args.extend([f"--{flag.replace('_', '-')}", value])
    rc = _run(args)
    assert rc == 0
    return capsys.readouterr().out


def test_status_json_shape_for_current_installation(isolated_home):
    assert _run(["install", "claude"]) == 0
    payload = _status_json("claude", scope="user")
    assert payload["host"] == "claude"
    assert payload["packaged_version"] == VERSION
    assert len(payload["installations"]) == 1
    installation = payload["installations"][0]
    assert installation["scope"] == "user"
    assert installation["state"] == "current"
    assert installation["installed_version"] == VERSION
    assert Path(installation["path"]) == _user_destination(isolated_home, "claude")


def test_status_absent_is_not_an_error(isolated_home):
    payload = _status_json("codex", scope="user")
    assert payload["installations"][0]["state"] == "absent"
    assert payload["installations"][0]["installed_version"] is None


@pytest.mark.parametrize("host", HOSTS)
def test_status_scope_selects_exactly_one(isolated_home, host, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(("git", "init", "-q", str(repo)), check=True)
    payload = _status_json(host, scope="user")
    assert len(payload["installations"]) == 1
    assert payload["installations"][0]["scope"] == "user"
    payload = _status_json(host, scope="project", repository=str(repo))
    assert len(payload["installations"]) == 1
    assert payload["installations"][0]["scope"] == "project"


def test_status_skills_dir_selects_exactly_one(isolated_home, tmp_path):
    custom = tmp_path / "custom-dir"
    payload = _status_json("claude", **{"skills-dir": str(custom)})
    assert len(payload["installations"]) == 1
    assert payload["installations"][0]["scope"] == "custom"
    assert Path(payload["installations"][0]["path"]) == custom / "wfc"


def test_status_default_reports_user_and_project_inside_git_work_tree(isolated_home, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(("git", "init", "-q", str(repo)), check=True)
    payload = _status_json("claude", repository=str(repo))
    assert {entry["scope"] for entry in payload["installations"]} == {"user", "project"}


def test_status_default_omits_project_outside_git_work_tree(isolated_home, tmp_path):
    outside = tmp_path / "plain-directory"
    outside.mkdir()
    payload = _status_json("claude", repository=str(outside))
    assert [entry["scope"] for entry in payload["installations"]] == ["user"]


def test_status_text_block_shape(isolated_home, capsys):
    assert _run(["install", "claude"]) == 0
    text = _status_text("claude", capsys, scope="user")
    lines = text.splitlines()
    assert lines[0] == "HOST: claude"
    assert lines[1] == "SCOPE: user"
    assert lines[2].startswith("PATH: ")
    assert lines[3] == "STATE: current"
    assert lines[4] == f"INSTALLED VERSION: {VERSION}"
    assert lines[5] == ""
    assert lines[6] == f"PACKAGED VERSION: {VERSION}"


def test_status_installed_version_is_dash_without_valid_marker(isolated_home, capsys):
    destination = _user_destination(isolated_home, "claude")
    _write_foreign_installation(destination)
    text = _status_text("claude", capsys, scope="user")
    assert "INSTALLED VERSION: -" in text.splitlines()


@pytest.mark.parametrize(
    "held,other",
    [("claude", "codex"), ("codex", "claude")],
)
def test_status_independence_between_hosts_both_directions(isolated_home, capsys, held, other):
    assert _run(["install", held]) == 0
    baseline = _status_text(held, capsys, scope="user")

    # other host absent (already true) -- held's status is unaffected.
    assert _status_text(held, capsys, scope="user") == baseline

    # other host stale.
    _write_stale_installation(_user_destination(isolated_home, other))
    assert _status_text(held, capsys, scope="user") == baseline

    # other host modified.
    _write_modified_installation(_user_destination(isolated_home, other))
    assert _status_text(held, capsys, scope="user") == baseline


# --------------------------------------------------------------------------
# Project scope: writes only the one destination, never commits or stages.
# --------------------------------------------------------------------------

def test_project_scope_refused_outside_git_work_tree(isolated_home, tmp_path):
    outside = tmp_path / "not-a-repository"
    outside.mkdir()
    assert _run(["install", "claude", "--scope", "project", "--repository", str(outside)]) == 2
    assert _run(["status", "claude", "--scope", "project", "--repository", str(outside)]) == 2
    assert not (outside / ".claude").exists()


def test_project_scope_install_touches_only_destination(isolated_home, tmp_path):
    repo = tmp_path / "consumer-repo"
    repo.mkdir()
    subprocess.run(("git", "init", "-q", str(repo)), check=True)
    subprocess.run(("git", "-C", str(repo), "config", "user.email", "consumer@example.invalid"), check=True)
    subprocess.run(("git", "-C", str(repo), "config", "user.name", "Consumer test"), check=True)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(repo), "add", "README.md"), check=True)
    subprocess.run(("git", "-C", str(repo), "commit", "-q", "-m", "chore: seed"), check=True)
    head_before = _git(repo, "rev-parse", "HEAD")
    index_before = _git(repo, "ls-files", "-s")

    assert _run(["install", "claude", "--scope", "project", "--repository", str(repo)]) == 0

    assert _git(repo, "status", "--porcelain").splitlines() == ["?? .claude/"]
    assert _git(repo, "rev-parse", "HEAD") == head_before
    assert _git(repo, "ls-files", "-s") == index_before
    assert not (repo / ".wfc").exists()
    assert (repo / ".claude" / "skills" / "wfc" / "SKILL.md").is_file()


# --------------------------------------------------------------------------
# ``show``.
# --------------------------------------------------------------------------

def test_show_default_prints_skill_md(capsys):
    assert _run(["show"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("---\nname: wfc\n")


def test_show_list_prints_paths_with_digests(capsys):
    assert _run(["show", "--list"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert any(line.startswith("SKILL.md  sha256:") for line in lines)
    assert len(lines) == 4


def test_show_unknown_file_refuses(capsys):
    assert _run(["show", "does-not-exist.md"]) == 2


# --------------------------------------------------------------------------
# Dispatcher.
# --------------------------------------------------------------------------

def _refuse_if_called(*_args, **_kwargs):
    raise AssertionError("must not reach operator.main")


@pytest.mark.parametrize(
    "args",
    [["skill"], ["skill", "nonsense"], ["skill", "status"], ["skill", "install"]],
)
def test_dispatcher_usage_errors_return_2_and_never_reach_operator(monkeypatch, args):
    monkeypatch.setattr("workflow_coordinator.operator.main", _refuse_if_called)
    with pytest.raises(SystemExit) as excinfo:
        cli_main(args)
    assert excinfo.value.code == 2


@pytest.mark.parametrize(
    "args",
    [
        ["skill", "--help"],
        ["skill", "install", "--help"],
        ["skill", "status", "--help"],
        ["skill", "show", "--help"],
    ],
)
def test_dispatcher_help_returns_0_and_never_reaches_operator(monkeypatch, args):
    monkeypatch.setattr("workflow_coordinator.operator.main", _refuse_if_called)
    with pytest.raises(SystemExit) as excinfo:
        cli_main(args)
    assert excinfo.value.code == 0


def test_dispatcher_valid_install_never_reaches_operator(monkeypatch, isolated_home):
    monkeypatch.setattr("workflow_coordinator.operator.main", _refuse_if_called)
    assert _run(["install", "claude"]) == 0

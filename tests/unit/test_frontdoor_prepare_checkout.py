"""``wfc prepare-checkout``: the invariants, the hook trust boundary, and the
prepared-versus-unprepared proof (FTR-WFC-RUN-CHECKOUT-PREPARATION, D4 A).

Everything here uses real ``git`` and real subprocesses in ``tmp_path``, and
nothing needs the network. The central acceptance is
``test_prepared_checkout_passes_the_baseline_probe_an_unprepared_one_fails``:
one unmodified toy probe command, invoked exactly as
``lifecycle._do_probe`` invokes it (same argv, same working directory,
inheriting the launching process's environment), failing in an unprepared
checkout and passing in a prepared one.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from workflow_coordinator.frontdoor import prepare_checkout as P
from workflow_coordinator.frontdoor.cli import main as cli_main
from workflow_coordinator.frontdoor.journal import (
    CanonicalCheckout,
    Journal,
    canonical_checkout,
    checkout_directory,
    checkout_identity,
    journal_path,
)
from workflow_coordinator.git_facts import observe_repository

# The toy consumer's committed baseline probe. It needs exactly what a real
# prepared environment supplies: a prepared tool first on PATH, the hook's own
# declared variable, and a writable TMPDIR *outside* the worktree (it writes
# its marker there, so a probe that passes also leaves the tree clean).
PROBE = """#!/bin/sh
set -eu
command -v toytool >/dev/null 2>&1 || { echo "toytool is not on PATH" >&2; exit 1; }
[ "${TOY_READY:-}" = "1" ] || { echo "TOY_READY is not set" >&2; exit 1; }
: > "${TMPDIR:?TMPDIR is not set}/probe-marker"
toytool
"""

# The toy consumer's committed preparation hook. fd 3 is the declaration
# channel; stdout is redirected to stderr so no diagnostic can ever reach it.
HOOK = """#!/bin/sh
set -eu
checkout="$1"
scratch="$2"
base_commit="$3"
exec 3>&1
exec 1>&2
echo "toy hook preparing ${checkout} from ${base_commit}"
mkdir -p "${scratch}/toolbin"
printf '#!/bin/sh\\necho toytool ok\\n' > "${scratch}/toolbin/toytool"
chmod 755 "${scratch}/toolbin/toytool"
printf 'WFC_PREPARE_PATH_PREPEND=%s\\n' "${scratch}/toolbin" >&3
printf 'TOY_READY=1\\n' >&3
"""

FAILING_HOOK = """#!/bin/sh
echo "this hook cannot prepare anything" >&2
exit 7
"""

CONFIG_TEMPLATE = """schema_version: 1
repository_name: toy
governance_dir: governance
work_item_scope_ceiling:
  - src
max_autonomous_hops: 4
default_execution:
  worker:
    adapter: codex_cli
    model: toy-model
    reasoning: medium
"""


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments), capture_output=True, text=True, check=True,
    )
    return completed.stdout.strip()


def _write(path: Path, text: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def _toy_repository(root: Path, *, hook_text: str = HOOK, config: str | None = None) -> Path:
    """A committed toy consumer repository: a probe, a hook, maybe a config."""

    repository = root / "origin"
    repository.mkdir(parents=True)
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Toy Consumer")
    _git(repository, "config", "user.email", "toy@example.invalid")
    _write(repository / "check.sh", PROBE, executable=True)
    _write(repository / "tools" / "prepare.sh", hook_text, executable=True)
    if config is not None:
        _write(repository / ".wfc" / "config.yaml", config)
    _git(repository, "add", "-A")
    _git(repository, "commit", "-m", "toy consumer baseline")
    return repository


@pytest.fixture
def toy(tmp_path, monkeypatch):
    """A toy repository plus an isolated lifecycle state root.

    ``XDG_STATE_HOME`` is redirected so the one-Run-per-checkout check reads
    a journal root of this test's own, never the operator's.
    """

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    repository = _toy_repository(tmp_path)
    checkouts = tmp_path / "checkouts"
    checkouts.mkdir()
    return repository, checkouts, state


def _linked_worktree(repository: Path, path: Path, branch: str) -> Path:
    """One more working tree of the same repository, as every Run gets."""

    _git(repository, "worktree", "add", "-b", branch, str(path), "HEAD")
    return path


def _prepare(repository: Path, target: Path, **overrides):
    arguments = {
        "repository": repository, "target": target, "branch": "work/toy",
        "hook": "tools/prepare.sh",
    }
    arguments.update(overrides)
    return P.prepare_checkout(**arguments)


def _probe_as_the_lifecycle_does(worktree: Path, argv=("./check.sh",)):
    """Exactly ``lifecycle._do_probe``'s own invocation: the declared argv, the
    repository as working directory, and no ``env=`` -- the probe inherits the
    launching process's environment."""

    return subprocess.run(list(argv), cwd=str(worktree), capture_output=True, timeout=120)


# --------------------------------------------------------------------------
# The central acceptance: prepared passes a probe unprepared fails.
# --------------------------------------------------------------------------

def test_prepared_checkout_passes_the_baseline_probe_an_unprepared_one_fails(
    toy, monkeypatch, tmp_path,
):
    repository, checkouts, _ = toy

    # Unprepared: a plain `git worktree add`, which is what the operator did
    # by hand before this command existed.
    unprepared = checkouts / "unprepared"
    _git(repository, "worktree", "add", "-b", "work/unprepared", str(unprepared), "HEAD")
    failed = _probe_as_the_lifecycle_does(unprepared)
    assert failed.returncode != 0
    assert b"toytool is not on PATH" in failed.stderr

    # Prepared: the same probe, unmodified, in a checkout this command made.
    preparation = _prepare(repository, checkouts / "prepared")
    for name, value in preparation.environment.items():
        monkeypatch.setenv(name, value)
    passed = _probe_as_the_lifecycle_does(preparation.worktree)
    assert passed.returncode == 0, passed.stderr
    assert b"toytool ok" in passed.stdout

    # The prepared probe also leaves the tree clean, which is why TMPDIR is a
    # sibling directory rather than anything inside the worktree.
    assert _git(preparation.worktree, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert (preparation.tmpdir / "probe-marker").is_file()
    assert not P._is_inside(preparation.tmpdir, preparation.worktree)


def test_env_file_sourced_by_a_real_shell_matches_the_reported_environment(toy):
    repository, checkouts, _ = toy
    preparation = _prepare(repository, checkouts / "prepared")

    script = f'set -a; . {preparation.env_file}; set +a; env'
    completed = subprocess.run(
        ("sh", "-c", script), capture_output=True, text=True, check=True, env=dict(os.environ),
    )
    sourced = dict(
        line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line
    )
    for name, value in preparation.environment.items():
        assert sourced[name] == value, name
    # The file writes PATH in its prepend form, so it stays correct in a shell
    # whose own PATH differs; sourcing it twice is harmless.
    twice = subprocess.run(
        ("sh", "-c", f'set -a; . {preparation.env_file}; . {preparation.env_file}; set +a; echo "$PATH"'),
        capture_output=True, text=True, check=True, env=dict(os.environ),
    )
    assert twice.stdout.strip().startswith(f"{preparation.path_prepend}:{preparation.path_prepend}:")


# --------------------------------------------------------------------------
# The one-Run-per-checkout invariant, enforced earlier and never relaxed.
# --------------------------------------------------------------------------

def test_prospective_checkout_identity_equals_the_real_one(toy):
    """The producer/consumer seam: the identity this command decides on for a
    path that does not exist yet must be the identity ``canonical_checkout``
    reports once the worktree is there."""

    repository, checkouts, _ = toy
    preparation = _prepare(repository, checkouts / "prepared")
    assert preparation.checkout_id == canonical_checkout(preparation.worktree).checkout_id
    assert preparation.checkout_id != canonical_checkout(repository).checkout_id


def test_refuses_a_path_whose_checkout_identity_already_has_a_lifecycle(toy, tmp_path):
    repository, checkouts, state = toy
    target = checkouts / "spent"
    source = canonical_checkout(repository)
    spent = checkout_directory(
        CanonicalCheckout(
            checkout_id=checkout_identity(target.resolve(), source.git_common_dir),
            toplevel=target.resolve(), git_common_dir=source.git_common_dir,
        ),
        root=state / "wfc",
    )
    Journal(journal_path(spent)).append("authorized", {"run_id": "RUN-toy"})

    with pytest.raises(P.PreparationRefusal) as refusal:
        _prepare(repository, target)
    assert refusal.value.name == "refused_existing_lifecycle"
    # Nothing was created, and the spent journal is untouched: this command
    # never makes a spent checkout reusable.
    assert not target.exists()
    assert len(Journal(journal_path(spent)).read_all()) == 1


def test_a_prepared_checkout_has_no_journal_of_its_own(toy):
    repository, checkouts, state = toy
    preparation = _prepare(repository, checkouts / "prepared")
    checkout_state = checkout_directory(
        canonical_checkout(preparation.worktree), root=state / "wfc",
    )
    assert not journal_path(checkout_state).exists()
    assert not (state / "wfc" / "checkouts").exists()


# --------------------------------------------------------------------------
# The other named refusals, and what they leave behind.
# --------------------------------------------------------------------------

def test_refuses_an_existing_path(toy):
    repository, checkouts, _ = toy
    target = checkouts / "taken"
    target.mkdir()
    with pytest.raises(P.PreparationRefusal) as refusal:
        _prepare(repository, target)
    assert refusal.value.name == "refused_existing_path"
    assert list(target.iterdir()) == []


def test_refuses_an_existing_scratch_path(toy):
    repository, checkouts, _ = toy
    (checkouts / "fresh-scratch").mkdir()
    with pytest.raises(P.PreparationRefusal) as refusal:
        _prepare(repository, checkouts / "fresh")
    assert refusal.value.name == "refused_existing_path"
    assert not (checkouts / "fresh").exists()


def test_refuses_a_missing_parent(toy):
    repository, checkouts, _ = toy
    with pytest.raises(P.PreparationRefusal) as refusal:
        _prepare(repository, checkouts / "nowhere" / "fresh")
    assert refusal.value.name == "refused_missing_parent"


def test_refuses_a_path_inside_the_repository(toy):
    repository, _, _ = toy
    with pytest.raises(P.PreparationRefusal) as refusal:
        _prepare(repository, repository / "nested")
    assert refusal.value.name == "refused_path_inside_repository"
    assert not (repository / "nested").exists()
    assert _git(repository, "status", "--porcelain=v1", "--untracked-files=all") == ""


def test_registered_working_trees_lists_every_tree_including_the_main_one(toy):
    repository, checkouts, _ = toy
    sibling = _linked_worktree(repository, checkouts / "sibling", "work/sibling")
    both = {repository.resolve(), sibling.resolve()}
    # Seen from either tree: a repository is its common directory and *all*
    # its working trees, so the boundary set is the same from both sides.
    assert set(P.registered_working_trees(repository)) == both
    assert set(P.registered_working_trees(sibling)) == both
    assert set(P.repository_boundaries(
        canonical_checkout(repository), P.registered_working_trees(repository),
    )) == both | {canonical_checkout(repository).git_common_dir}


def test_refuses_a_checkout_path_inside_another_working_tree_of_the_repository(toy):
    """The repository has many working trees sharing one common directory, and
    preparing into another of them dirties *that* tree: its next pre-Hop
    boundary check would fail and a launch there would be refused as not at
    the clean authorized baseline. One fresh worktree per Run is this
    repository's own topology, so this is the ordinary case, not an exotic
    one."""

    repository, checkouts, _ = toy
    sibling = _linked_worktree(repository, checkouts / "sibling", "work/sibling")

    with pytest.raises(P.PreparationRefusal) as refusal:
        _prepare(repository, sibling / "run-checkout", branch="work/nested")
    assert refusal.value.name == "refused_path_inside_repository"
    assert not (sibling / "run-checkout").exists()
    assert not (sibling / "run-checkout-scratch").exists()
    # The bystanding tree is untouched, so a Run living in it can still launch.
    assert _git(sibling, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert observe_repository(sibling).clean


def test_refuses_a_checkout_path_inside_the_main_tree_seen_from_a_linked_worktree(toy):
    """The other direction: whichever working tree ``--repository`` names, the
    refusal covers every other tree of the same repository."""

    repository, checkouts, _ = toy
    sibling = _linked_worktree(repository, checkouts / "sibling", "work/sibling")

    with pytest.raises(P.PreparationRefusal) as refusal:
        _prepare(sibling, repository / "run-checkout", branch="work/nested")
    assert refusal.value.name == "refused_path_inside_repository"
    assert not (repository / "run-checkout").exists()
    assert _git(repository, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert observe_repository(repository).clean


def test_refuses_a_scratch_path_inside_another_working_tree_of_the_repository(toy):
    repository, checkouts, _ = toy
    sibling = _linked_worktree(repository, checkouts / "sibling", "work/sibling")

    with pytest.raises(P.PreparationRefusal) as refusal:
        _prepare(
            repository, checkouts / "fresh", scratch=sibling / "scratch", branch="work/fresh",
        )
    assert refusal.value.name == "refused_path_inside_repository"
    assert "scratch path" in str(refusal.value)
    # Refused before the first effect: neither path was created.
    assert not (checkouts / "fresh").exists()
    assert not (sibling / "scratch").exists()
    assert _git(sibling, "status", "--porcelain=v1", "--untracked-files=all") == ""


def test_refuses_a_scratch_directory_inside_the_checkout(toy):
    repository, checkouts, _ = toy
    target = checkouts / "fresh"
    with pytest.raises(P.PreparationRefusal) as refusal:
        _prepare(repository, target, scratch=target / "tmp")
    assert refusal.value.name == "refused_scratch_inside_checkout"


def test_refuses_a_dirty_base(toy):
    repository, checkouts, _ = toy
    (repository / "untracked.txt").write_text("not committed\n", encoding="utf-8")
    with pytest.raises(P.PreparationRefusal) as refusal:
        _prepare(repository, checkouts / "fresh")
    assert refusal.value.name == "refused_dirty_base"
    assert "untracked.txt" in str(refusal.value)
    assert (repository / "untracked.txt").is_file()


def test_refuses_an_existing_branch(toy):
    repository, checkouts, _ = toy
    _git(repository, "branch", "work/toy")
    with pytest.raises(P.PreparationRefusal) as refusal:
        _prepare(repository, checkouts / "fresh")
    assert refusal.value.name == "refused_existing_branch"
    assert _git(repository, "rev-parse", "--verify", "refs/heads/work/toy")


def test_refuses_an_unknown_base_ref(toy):
    repository, checkouts, _ = toy
    with pytest.raises(P.PreparationRefusal) as refusal:
        _prepare(repository, checkouts / "fresh", base_ref="no/such/ref")
    assert refusal.value.name == "refused_unknown_base_ref"


def test_writes_nothing_outside_the_two_created_directories_and_removes_nothing(toy, tmp_path):
    repository, checkouts, _ = toy

    def worktree_files():
        return {
            path: path.stat().st_mtime_ns
            for path in sorted(repository.rglob("*"))
            if path.is_file() and ".git" not in path.relative_to(repository).parts
        }

    before = worktree_files()
    git_entries_before = set((repository / ".git").rglob("*"))
    sibling = checkouts / "bystander.txt"
    sibling.write_text("untouched\n", encoding="utf-8")

    preparation = _prepare(repository, checkouts / "prepared")

    # Every working-tree file that existed is still there, byte-for-byte
    # untouched; and nothing at all under .git was removed. The *additions*
    # under .git (the worktree administration and the new branch ref) are
    # Git's own doing and the one documented exception.
    assert worktree_files() == before
    assert git_entries_before <= set((repository / ".git").rglob("*"))
    assert _git(repository, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert sibling.read_text(encoding="utf-8") == "untouched\n"

    # The only new entries beside the repository are the two this command owns.
    assert {entry.name for entry in checkouts.iterdir()} == {
        "bystander.txt", preparation.worktree.name, preparation.scratch.name,
    }
    # Everything the command itself wrote is inside the scratch directory.
    assert preparation.env_file.parent == preparation.scratch
    assert preparation.tmpdir.parent == preparation.scratch


def test_prepares_worktree_scratch_and_env_file_without_a_hook(toy):
    repository, checkouts, _ = toy
    preparation = _prepare(repository, checkouts / "bare", hook=None, use_hook=False)
    assert preparation.hook is None
    assert preparation.worktree.is_dir() and preparation.tmpdir.is_dir()
    assert preparation.environment == {"TMPDIR": str(preparation.tmpdir)}
    assert "export TMPDIR=" in preparation.env_file.read_text(encoding="utf-8")
    # An unprepared-but-created checkout still fails the probe: creating the
    # worktree is not what makes the probe pass, the hook is.
    assert _probe_as_the_lifecycle_does(preparation.worktree).returncode != 0


def test_summary_names_the_next_step_and_authorizes_nothing(toy):
    repository, checkouts, _ = toy
    summary = P.render_summary(_prepare(repository, checkouts / "prepared"))
    assert "NEXT EXPLICIT ACTION: nothing was authorized" in summary
    assert "source " in summary and "wfc run --request" in summary
    assert "Nothing was removed." in summary


# --------------------------------------------------------------------------
# The hook trust boundary.
# --------------------------------------------------------------------------

def test_refuses_a_hook_outside_the_repository(toy, tmp_path):
    repository, checkouts, _ = toy
    outside = tmp_path / "evil.sh"
    _write(outside, "#!/bin/sh\nexit 0\n", executable=True)
    for index, named in enumerate((str(outside), "../evil.sh", "./tools/prepare.sh")):
        with pytest.raises(P.PreparationRefusal) as refusal:
            _prepare(repository, checkouts / f"fresh-{index}", hook=named)
        assert refusal.value.name == "refused_hook_outside_repository", named
        assert not (checkouts / f"fresh-{index}").exists()


def test_refuses_a_hook_that_is_not_committed_at_the_base_ref(tmp_path, monkeypatch):
    """The hook is read at the base ref, never from a working tree: a hook
    added in a later commit does not exist for an earlier base."""

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    repository = _toy_repository(tmp_path)
    base = _git(repository, "rev-parse", "HEAD")
    _write(repository / "tools" / "later.sh", HOOK, executable=True)
    _git(repository, "add", "-A")
    _git(repository, "commit", "-m", "add a second hook")
    checkouts = tmp_path / "checkouts"
    checkouts.mkdir()

    with pytest.raises(P.PreparationRefusal) as refusal:
        _prepare(repository, checkouts / "fresh", hook="tools/later.sh", base_ref=base)
    assert refusal.value.name == "refused_hook_not_committed"
    assert not (checkouts / "fresh").exists()

    # At the commit that does carry it, the same hook is accepted.
    preparation = _prepare(repository, checkouts / "later", hook="tools/later.sh")
    assert preparation.hook == "tools/later.sh"


def test_refuses_a_committed_hook_that_is_not_executable(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    repository = _toy_repository(tmp_path)
    _write(repository / "tools" / "plain.sh", HOOK)
    _git(repository, "add", "-A")
    _git(repository, "commit", "-m", "a non-executable hook")
    checkouts = tmp_path / "checkouts"
    checkouts.mkdir()

    with pytest.raises(P.PreparationRefusal) as refusal:
        _prepare(repository, checkouts / "fresh", hook="tools/plain.sh")
    assert refusal.value.name == "refused_hook_not_executable"
    assert not (checkouts / "fresh").exists()


def test_refuses_a_committed_hook_that_is_a_symlink(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    repository = _toy_repository(tmp_path)
    (repository / "tools" / "linked.sh").symlink_to("/bin/sh")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-m", "a symlinked hook")
    assert _git(repository, "ls-tree", "HEAD", "--", "tools/linked.sh").startswith("120000")
    checkouts = tmp_path / "checkouts"
    checkouts.mkdir()

    with pytest.raises(P.PreparationRefusal) as refusal:
        _prepare(repository, checkouts / "fresh", hook="tools/linked.sh")
    assert refusal.value.name == "refused_hook_symlink"
    assert not (checkouts / "fresh").exists()


def test_refuses_a_hook_file_that_is_not_the_blob_the_base_ref_names(toy):
    """The re-check between deciding and executing, exercised directly: the
    file in the created worktree must be the committed blob itself."""

    repository, checkouts, _ = toy
    preparation = _prepare(repository, checkouts / "prepared")
    entry = P.committed_entry(repository, preparation.base_commit, "tools/prepare.sh")
    assert entry is not None and entry.mode == "100755"

    (preparation.worktree / "tools" / "prepare.sh").write_text(
        "#!/bin/sh\nexit 0\n", encoding="utf-8",
    )
    with pytest.raises(P.PreparationRefusal) as refusal:
        P._require_hook_is_the_base_blob(preparation.worktree, "tools/prepare.sh", entry)
    assert refusal.value.name == "refused_hook_not_identical_to_base_blob"


def test_refuses_a_symlinked_hook_path_component_in_the_checkout(toy, tmp_path):
    repository, checkouts, _ = toy
    preparation = _prepare(repository, checkouts / "prepared")
    entry = P.committed_entry(repository, preparation.base_commit, "tools/prepare.sh")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _write(elsewhere / "prepare.sh", HOOK, executable=True)
    tools = preparation.worktree / "tools"
    for child in tools.iterdir():
        child.unlink()
    tools.rmdir()
    tools.symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(P.PreparationRefusal) as refusal:
        P._require_hook_is_the_base_blob(preparation.worktree, "tools/prepare.sh", entry)
    assert refusal.value.name == "refused_hook_symlink"


def test_a_failing_hook_is_a_named_refusal_that_leaves_everything_in_place(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    repository = _toy_repository(tmp_path, hook_text=FAILING_HOOK)
    checkouts = tmp_path / "checkouts"
    checkouts.mkdir()

    with pytest.raises(P.PreparationRefusal) as refusal:
        _prepare(repository, checkouts / "fresh")
    error = refusal.value
    assert error.name == "refused_hook_failed"
    assert "exit code 7" in str(error)
    assert "untrusted" in error.detail and "cannot prepare anything" in error.detail
    # Created, reported, and never removed.
    assert error.created.worktree == (checkouts / "fresh").resolve()
    assert error.created.scratch == (checkouts / "fresh-scratch").resolve()
    assert error.created.worktree.is_dir() and error.created.scratch.is_dir()
    assert (error.created.worktree / "check.sh").is_file()
    assert "LEFT IN PLACE" in "\n".join(P._left_in_place_lines(error.created))
    # No launch environment is written for a failed preparation.
    assert not (error.created.scratch / P.ENV_FILE_NAME).exists()


def test_a_hanging_hook_times_out_by_name(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    repository = _toy_repository(tmp_path, hook_text="#!/bin/sh\nsleep 30\n")
    checkouts = tmp_path / "checkouts"
    checkouts.mkdir()

    with pytest.raises(P.PreparationRefusal) as refusal:
        _prepare(repository, checkouts / "fresh", hook_timeout_seconds=1.0)
    assert refusal.value.name == "refused_hook_timeout"
    assert refusal.value.created.worktree is not None


def test_refuses_a_path_prepend_outside_the_prepared_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    repository = _toy_repository(
        tmp_path,
        hook_text="#!/bin/sh\nprintf 'WFC_PREPARE_PATH_PREPEND=/usr/bin\\n'\n",
    )
    checkouts = tmp_path / "checkouts"
    checkouts.mkdir()

    with pytest.raises(P.PreparationRefusal) as refusal:
        _prepare(repository, checkouts / "fresh")
    assert refusal.value.name == "refused_hook_path_prepend"


def test_the_hook_is_given_the_checkout_scratch_and_base_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    repository = _toy_repository(
        tmp_path,
        hook_text=(
            "#!/bin/sh\n"
            "printf 'TOY_ARGV=%s|%s|%s\\n' \"$1\" \"$2\" \"$3\"\n"
            "printf 'TOY_CWD=%s\\n' \"$(pwd -P)\"\n"
            "printf 'TOY_TMPDIR=%s\\n' \"$TMPDIR\"\n"
        ),
    )
    checkouts = tmp_path / "checkouts"
    checkouts.mkdir()

    preparation = _prepare(repository, checkouts / "fresh")
    expected = f"{preparation.worktree}|{preparation.scratch}|{preparation.base_commit}"
    assert preparation.environment["TOY_ARGV"] == expected
    assert Path(preparation.environment["TOY_CWD"]).resolve() == preparation.worktree
    assert preparation.environment["TOY_TMPDIR"] == str(preparation.tmpdir)


# --------------------------------------------------------------------------
# The declaration channel: bounded, strictly parsed, never trusted.
# --------------------------------------------------------------------------

def test_parses_declarations_and_separates_the_reserved_path_prepend():
    prepend, declarations = P.parse_hook_declarations(
        b"WFC_TEST_PROFILE=cp313-linux-x86_64\n\nWFC_PREPARE_PATH_PREPEND=/a/bin\nA_B2=x y\n"
    )
    assert prepend == "/a/bin"
    assert declarations == {"WFC_TEST_PROFILE": "cp313-linux-x86_64", "A_B2": "x y"}


@pytest.mark.parametrize(
    "stdout, expected",
    [
        (b"installing wheels...\n", "refused_hook_output"),
        (b"lower_case=1\n", "refused_hook_output"),
        (b"A=" + b"x" * (P.HOOK_VALUE_LIMIT_BYTES + 1) + b"\n", "refused_hook_output"),
        (b"A=one\x07two\n", "refused_hook_output"),
        (b"A=\xff\xfe\n", "refused_hook_output"),
        (b"A=1\nA=2\n", "refused_hook_output"),
        (b"\n".join(b"V%d=1" % index for index in range(P.HOOK_DECLARATION_LIMIT + 1)),
         "refused_hook_output"),
        (b"X=1\n" * (P.HOOK_STDOUT_LIMIT_BYTES // 4 + 1), "refused_hook_output"),
        (b"PATH=/anything\n", "refused_hook_reserved_variable"),
        (b"TMPDIR=/anything\n", "refused_hook_reserved_variable"),
    ],
)
def test_refuses_hook_stdout_that_is_not_exactly_the_declaration_channel(stdout, expected):
    with pytest.raises(P.PreparationRefusal) as refusal:
        P.parse_hook_declarations(stdout)
    assert refusal.value.name == expected


def test_composed_environment_keeps_path_and_tmpdir_wfc_s_own():
    environment = P.compose_launch_environment(
        environ={"PATH": "/usr/bin", "TMPDIR": "/elsewhere"},
        tmpdir=Path("/scratch/tmp"), path_prepend=Path("/scratch/venv/bin"),
        declarations={"WFC_TEST_PROFILE": "cp313-linux-x86_64"},
    )
    assert environment == {
        "TMPDIR": "/scratch/tmp",
        "PATH": "/scratch/venv/bin:/usr/bin",
        "WFC_TEST_PROFILE": "cp313-linux-x86_64",
    }


def test_env_file_quotes_values_and_writes_path_in_prepend_form():
    rendered = P.render_env_file(
        environment={"TMPDIR": "/scratch/tmp", "PATH": "/scratch/bin:/usr/bin", "A": "x y'z"},
        worktree=Path("/checkouts/fresh"), branch="work/toy", base_commit="abc123",
        hook="tools/prepare.sh", path_prepend=Path("/scratch/bin"),
    )
    assert 'export PATH=/scratch/bin:"$PATH"' in rendered
    assert "export A='x y'\"'\"'z'" in rendered
    assert "export TMPDIR=/scratch/tmp" in rendered
    assert "# hook:        tools/prepare.sh" in rendered


# --------------------------------------------------------------------------
# The declaration's own source: the committed Consumer Config, at the base ref.
# --------------------------------------------------------------------------

def test_declares_the_hook_from_the_committed_consumer_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    repository = _toy_repository(tmp_path, config=CONFIG_TEMPLATE)
    base_without_declaration = _git(repository, "rev-parse", "HEAD")
    _write(
        repository / ".wfc" / "config.yaml",
        CONFIG_TEMPLATE + "checkout_preparation_hook: tools/prepare.sh\n",
    )
    _git(repository, "add", "-A")
    _git(repository, "commit", "-m", "declare the preparation hook")
    checkouts = tmp_path / "checkouts"
    checkouts.mkdir()

    # Declared at HEAD: used without any --hook flag.
    declared = P.prepare_checkout(
        repository=repository, target=checkouts / "declared", branch="work/declared",
    )
    assert declared.hook == "tools/prepare.sh"
    assert declared.environment["TOY_READY"] == "1"

    # The declaration is read at the base ref, not from the working tree: at
    # the earlier commit there is none, and nothing runs.
    undeclared = P.prepare_checkout(
        repository=repository, target=checkouts / "undeclared", branch="work/undeclared",
        base_ref=base_without_declaration,
    )
    assert undeclared.hook is None
    assert undeclared.environment == {"TMPDIR": str(undeclared.tmpdir)}

    # And --no-hook overrides the committed declaration.
    disabled = P.prepare_checkout(
        repository=repository, target=checkouts / "disabled", branch="work/disabled",
        use_hook=False,
    )
    assert disabled.hook is None


def test_refuses_a_committed_config_it_cannot_parse(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    repository = _toy_repository(tmp_path, config="schema_version: 1\nnot_a_key: 1\n")
    checkouts = tmp_path / "checkouts"
    checkouts.mkdir()
    with pytest.raises(P.PreparationRefusal) as refusal:
        P.prepare_checkout(
            repository=repository, target=checkouts / "fresh", branch="work/toy",
        )
    assert refusal.value.name == "refused_config"
    assert not (checkouts / "fresh").exists()


def test_refuses_a_committed_config_whose_bytes_are_not_utf_8(tmp_path, monkeypatch):
    """A named refusal, not a traceback. The config's bytes are the
    consumer's, so a strict text decode of Git's output must not escape as an
    unhandled exception -- the same defect class ``git_facts._run_git_raw``
    reads bytes for."""

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    repository = _toy_repository(tmp_path)
    config = repository / ".wfc" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes(b"schema_version: 1\nrepository_name: \xff\xfe\n")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-m", "a committed config that is not utf-8")
    checkouts = tmp_path / "checkouts"
    checkouts.mkdir()

    with pytest.raises(P.PreparationRefusal) as refusal:
        P.prepare_checkout(
            repository=repository, target=checkouts / "fresh", branch="work/toy",
        )
    assert refusal.value.name == "refused_config"
    assert "not valid UTF-8" in str(refusal.value)
    assert not (checkouts / "fresh").exists()


def test_no_declaration_and_no_config_file_prepares_without_a_hook(toy):
    repository, checkouts, _ = toy
    preparation = P.prepare_checkout(
        repository=repository, target=checkouts / "fresh", branch="work/toy",
    )
    assert preparation.hook is None


# --------------------------------------------------------------------------
# The CLI surface.
# --------------------------------------------------------------------------

def test_cli_dispatches_prepare_checkout_and_prints_the_summary(toy, capsys):
    repository, checkouts, _ = toy
    code = cli_main([
        "prepare-checkout", "--repository", str(repository),
        "--path", str(checkouts / "cli"), "--branch", "work/cli",
        "--hook", "tools/prepare.sh",
    ])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "one fresh Run checkout prepared" in captured.out
    assert (checkouts / "cli" / "check.sh").is_file()
    assert (checkouts / "cli-scratch" / P.ENV_FILE_NAME).is_file()


def test_cli_refusal_prints_the_refusal_name_and_exits_two(toy, capsys):
    repository, checkouts, _ = toy
    (checkouts / "taken").mkdir()
    code = cli_main([
        "prepare-checkout", "--repository", str(repository),
        "--path", str(checkouts / "taken"), "--branch", "work/cli",
    ])
    captured = capsys.readouterr()
    assert code == 2
    assert "wfc prepare-checkout: refused_existing_path:" in captured.err


def test_help_exits_zero_and_never_falls_through_to_the_legacy_operator(monkeypatch):
    """The dispatch contract: an unrecognized first word reaches an unrelated
    legacy entry point, so the new subcommand must be dispatched by name."""

    def refuse(*arguments, **keywords):  # pragma: no cover - must not run
        raise AssertionError("cli dispatch fell through to workflow_coordinator.operator")

    monkeypatch.setattr("workflow_coordinator.operator.main", refuse)
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["prepare-checkout", "--help"])
    assert exit_info.value.code == 0


def test_hook_and_no_hook_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        P._parse_args(["--path", "/x", "--branch", "b", "--hook", "h", "--no-hook"])


def test_wfc_s_own_hook_is_committed_executable_and_declares_what_the_suite_needs():
    """WFC's own hook (``scripts/wfc_prepare_checkout_env.sh``) must satisfy
    this command's own hook rules, and stay development tooling: the product
    knows nothing about bundles, profiles or venvs."""

    root = Path(__file__).resolve().parents[2]
    hook = root / "scripts" / "wfc_prepare_checkout_env.sh"
    assert hook.is_file() and not hook.is_symlink()
    assert hook.stat().st_mode & stat.S_IXUSR
    text = hook.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    for declaration in (
        "WFC_PREPARE_PATH_PREPEND", "WFC_TEST_INPUTS", "WFC_TEST_PROFILE", "cp313-linux-x86_64",
    ):
        assert declaration in text
    assert "--no-index --find-links" in text and "-e '.[dev]'" in text
    assert "wfc_test_inputs.py verify" in text
    # The venv belongs in the scratch directory, never in the worktree.
    assert 'venv="${scratch}/venv"' in text
    # Nothing in the product itself knows about any of this.
    product = (root / "src" / "workflow_coordinator").rglob("*.py")
    for module in product:
        source = module.read_text(encoding="utf-8")
        assert "WFC_TEST_INPUTS" not in source, module
        assert "wfc_prepare_checkout_env" not in source, module

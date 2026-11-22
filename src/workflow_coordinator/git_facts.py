"""Direct Git facts and fail-closed Slice-2 transaction checks.

This is deliberately a small seam: every value below is obtained from Git, not
from an invocation result.  It neither invokes an agent nor repairs a checkout.
"""

from __future__ import annotations

import os
import re
import subprocess
import hashlib
import stat
import tempfile
import shutil
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Callable

from .model import CommitIntent


class GitPreflightError(RuntimeError):
    """The repository is not a safe boundary for a coordinator Hop."""


class TrustedCommitError(GitPreflightError):
    """The narrow Coordinator-owned commit boundary refused or could not commit."""

    def __init__(self, message: str, *, delta: GitDelta | None = None) -> None:
        super().__init__(message)
        self.delta = delta


@dataclass(frozen=True)
class GitFacts:
    repository: Path
    head: str
    tracked_changes: tuple[str, ...]
    untracked_changes: tuple[str, ...]
    unresolved_submodules: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not self.tracked_changes and not self.untracked_changes


@dataclass(frozen=True)
class GitDelta:
    base_head: str
    end_head: str
    actual_commits: tuple[str, ...]
    changed_paths: tuple[str, ...]
    claimed_commits: tuple[str, ...]
    claim_mismatch: bool
    anomalies: tuple[str, ...]


@dataclass(frozen=True)
class TrustedCommit:
    """The only factual result of the Coordinator's one-commit write surface."""

    commit: str
    delta: GitDelta


@dataclass(frozen=True)
class NewPathCommitIntent:
    """A Coordinator-owned request to commit exact, newly created leaf files.

    Unlike ``model.CommitIntent`` (an agent's semantic request materialized
    inside a Hop), this is supplied directly by a Coordinator-owned caller
    acting outside any Hop -- ``wfc init``, the archive port -- and is never
    agent-authored.
    """

    paths: tuple[str, ...]
    subject: str


@dataclass(frozen=True)
class SalvageObject:
    """One untrusted, byte-addressed candidate object; never Git authority."""

    object_id: str
    path: str | None
    kind: str
    content: bytes
    content_kind: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def salvage_inventory(
    repository: Path,
    *,
    base_head: str,
    allowed_paths: tuple[str, ...],
    max_objects: int = 128,
    max_object_bytes: int = 1_048_576,
    max_total_bytes: int = 8_388_608,
) -> tuple[SalvageObject, ...]:
    """Read a bounded candidate delta without touching Git or the worktree.

    The caller owns publication; this function deliberately has no write path.
    """
    identity = repository_identity(repository)
    _reject_unsafe_scope_special_files(identity, allowed_paths)
    # Git may refresh an index while comparing it with the worktree, even for
    # `diff`/`ls-files`.  Give the whole capture an isolated index copy so a
    # platform-specific refresh can never touch the registered checkout.
    copied_index = _isolated_index_copy(identity, prefix="wfc-salvage-index-")
    try:
        capture_env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_INDEX_FILE": str(copied_index)}
        if _run_git(identity, "cat-file", "-e", f"{base_head}^{{commit}}", env=capture_env).returncode:
            raise GitPreflightError("salvage base HEAD is not a commit")
        return _salvage_inventory_with_index(
            identity, base_head=base_head, allowed_paths=allowed_paths,
            max_objects=max_objects, max_object_bytes=max_object_bytes,
            max_total_bytes=max_total_bytes, env=capture_env,
        )
    finally:
        try:
            os.unlink(copied_index)
        except FileNotFoundError:
            pass


def _salvage_inventory_with_index(
    identity: Path, *, base_head: str, allowed_paths: tuple[str, ...], max_objects: int,
    max_object_bytes: int, max_total_bytes: int, env: dict[str, str],
) -> tuple[SalvageObject, ...]:
    """Capture helper using an isolated caller-owned Git index copy."""
    if not allowed_paths:
        raise GitPreflightError("salvage requires an explicit authorized scope")
    if max_objects < 1 or max_object_bytes < 1 or max_total_bytes < 1:
        raise GitPreflightError("salvage limits must be positive")
    def capture_git(*args: str, check: bool = True) -> str:
        completed = _run_git(identity, *args, env=env)
        if check and completed.returncode:
            raise GitPreflightError(completed.stderr.strip() or "Git command failed")
        return completed.stdout.strip()

    changed = tuple(filter(None, capture_git("diff", "--no-renames", "--no-ext-diff", "--name-only", base_head).splitlines()))
    outside = [
        path for path in changed
        if not any(path == root or path.startswith(f"{root.rstrip('/')}/") for root in allowed_paths)
    ]
    if outside:
        raise GitPreflightError("salvage tracked path is outside authorized scope: " + ", ".join(outside))
    binary_tracked = any(
        line.startswith("-\t") or line.split("\t", 1)[0] == "-"
        for line in capture_git("diff", "--no-renames", "--numstat", "--no-ext-diff", base_head).splitlines()
    )
    for path in changed:
        # A tracked change is not implicitly safe merely because Git can encode
        # it in a diff.  Existing worktree objects must remain ordinary files
        # below the checkout; deletions have no object to dereference.
        candidate = identity / path
        if not candidate.exists() and not candidate.is_symlink():
            continue
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(identity)
            metadata = candidate.lstat()
        except (OSError, ValueError) as error:
            raise GitPreflightError(f"salvage tracked path is unsafe: {path}") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise GitPreflightError(f"salvage tracked path is not a regular in-repository file: {path}")
        if metadata.st_size > max_object_bytes:
            raise GitPreflightError(f"salvage tracked object exceeds size limit: {path}")
    diff = _run_git(identity, "diff", "--no-renames", "--binary", "--no-ext-diff", base_head, env=env).stdout.encode("utf-8")
    objects: list[SalvageObject] = []
    total_bytes = 0
    if diff:
        if len(diff) > max_object_bytes:
            raise GitPreflightError("salvage tracked diff exceeds object size limit")
        if len(diff) > max_total_bytes:
            raise GitPreflightError("salvage total bytes exceeds limit")
        objects.append(SalvageObject(
            "tracked-diff", None, "tracked_diff", diff,
            "binary" if binary_tracked else "text",
        ))
        total_bytes += len(diff)
        if len(objects) > max_objects:
            raise GitPreflightError("salvage object count exceeds limit")
    names = tuple(filter(None, capture_git("ls-files", "--others", "--exclude-standard").splitlines()))
    for path in names:
        if not any(path == root or path.startswith(f"{root.rstrip('/')}/") for root in allowed_paths):
            raise GitPreflightError(f"salvage untracked path is outside authorized scope: {path}")
        candidate = identity / path
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(identity)
            metadata = candidate.lstat()
        except (OSError, ValueError) as error:
            raise GitPreflightError(f"salvage path is unsafe: {path}") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise GitPreflightError(f"salvage path is not a regular in-repository file: {path}")
        if metadata.st_size > max_object_bytes:
            raise GitPreflightError(f"salvage object exceeds size limit: {path}")
        try:
            content = resolved.read_bytes()
        except OSError as error:
            raise GitPreflightError(f"salvage path cannot be read: {path}") from error
        if len(content) != metadata.st_size or len(content) > max_object_bytes:
            raise GitPreflightError(f"salvage path changed during capture: {path}")
        if total_bytes + len(content) > max_total_bytes:
            raise GitPreflightError("salvage total bytes exceeds limit")
        try:
            content_kind = "text" if "\x00" not in content.decode("utf-8") else "binary"
        except UnicodeDecodeError:
            content_kind = "binary"
        objects.append(SalvageObject(
            f"untracked:{path}", path, "untracked_file", content, content_kind,
        ))
        total_bytes += len(content)
        if len(objects) > max_objects:
            raise GitPreflightError("salvage object count exceeds limit")
    return tuple(objects)


def _reject_unsafe_scope_special_files(repository: Path, allowed_paths: tuple[str, ...]) -> None:
    """Fail closed for special filesystem objects Git does not report as untracked.

    ``git ls-files --others`` omits objects such as FIFOs on some platforms.
    They are still candidate material below the authorized path and must not be
    silently ignored as though export had established a safe complete view.
    """
    def inspect(path: Path, *, display: str) -> None:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return
        except OSError as error:
            raise GitPreflightError(f"salvage path cannot be inspected: {display}") from error
        if stat.S_ISDIR(metadata.st_mode):
            try:
                with os.scandir(path) as entries:
                    for entry in entries:
                        inspect(Path(entry.path), display=f"{display.rstrip('/')}/{entry.name}")
            except OSError as error:
                raise GitPreflightError(f"salvage path cannot be inspected: {display}") from error
            return
        if not stat.S_ISREG(metadata.st_mode):
            raise GitPreflightError(f"salvage path is not a regular in-repository file: {display}")

    for allowed in allowed_paths:
        inspect(repository / allowed, display=allowed)


def salvage_omissions(repository: Path, *, allowed_paths: tuple[str, ...]) -> tuple[str, ...]:
    """Return auditable non-captured ignored paths without reading their bytes."""
    identity = repository_identity(repository)
    if not allowed_paths:
        raise GitPreflightError("salvage requires an explicit authorized scope")
    copied_index = _isolated_index_copy(identity, prefix="wfc-salvage-omissions-index-")
    try:
        completed = _run_git(
            identity, "ls-files", "--others", "--ignored", "--exclude-standard",
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_INDEX_FILE": str(copied_index)},
        )
        if completed.returncode:
            raise GitPreflightError(completed.stderr.strip() or "Git command failed")
        ignored = tuple(filter(None, completed.stdout.splitlines()))
    finally:
        try:
            os.unlink(copied_index)
        except FileNotFoundError:
            pass
    return tuple(
        f"ignored path omitted: {path}"
        for path in ignored
        if any(path == root or path.startswith(f"{root.rstrip('/')}/") for root in allowed_paths)
    )


_CONVENTIONAL_SUBJECT = re.compile(r"^[a-z][a-z0-9-]*(?:\([^)]+\))?!?: ")


GitCommand = Callable[..., str]


def _run_git(
    repository: Path,
    *args: str,
    env: dict[str, str] | None = None,
    trusted: bool = False,
) -> subprocess.CompletedProcess[str]:
    configuration = (
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "commit.gpgSign=false",
        "-c",
        "core.fsmonitor=false",
    ) if trusted else ()
    return subprocess.run(
        ("git", "-C", str(repository), *configuration, *args),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        shell=False,
    )


def _run_git_raw(
    repository: Path, *args: str, env: dict[str, str] | None = None, trusted: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    """Like ``_run_git`` but with undecoded stdout/stderr.

    A worktree path need not be valid UTF-8; a strict text decode of Git's
    output (as ``_run_git`` performs) raises ``UnicodeDecodeError`` for such a
    path, which would otherwise escape candidate preservation as an unhandled
    exception rather than a named, journaled refusal. This read-only helper
    lets a caller decode such output itself, with ``os.fsdecode`` symmetry.
    Git also reports an unindexable path (for example one it cannot open) in
    *stderr*, so any preservation-owned command that can fail on worktree
    content -- not only the status read -- needs this same undecoded path.
    """
    configuration = (
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "commit.gpgSign=false",
        "-c",
        "core.fsmonitor=false",
    ) if trusted else ()
    return subprocess.run(
        ("git", "-C", str(repository), *configuration, *args),
        check=False,
        text=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        shell=False,
    )


def _git(repository: Path, *args: str, check: bool = True) -> str:
    completed = _run_git(repository, *args)
    if check and completed.returncode:
        raise GitPreflightError(completed.stderr.strip() or "Git command failed")
    return completed.stdout.strip()


def _readonly_git(repository: Path, *args: str, check: bool = True) -> str:
    """Observe Git with optional index locks disabled.

    Git's otherwise read-only porcelain may refresh the index.  Salvage and
    ordinary observation promise not to alter the checkout, so keep those
    calls on this lock-free read path rather than the trusted-commit path.
    """
    environment = dict(os.environ)
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    completed = _run_git(repository, *args, env=environment)
    if check and completed.returncode:
        raise GitPreflightError(completed.stderr.strip() or "Git command failed")
    return completed.stdout.strip()


def _trusted_git_environment() -> dict[str, str]:
    """Keep ordinary process settings but remove every inherited Git control."""

    return {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}


def _trusted_git(repository: Path, *args: str, check: bool = True) -> str:
    """Run one internal trusted-commit Git command with fixed execution policy.

    This private helper is deliberately not a generic Git service.  It removes
    inherited Git routing/configuration controls, resolves hooks to ``/dev/null``
    (a non-writable non-directory), disables commit signing and fsmonitor, and
    never invokes a shell.  ``trusted_commit`` uses it for every Git subprocess it
    starts; read-only public observation intentionally retains ordinary Git policy.
    """

    completed = _run_git(
        repository,
        *args,
        env=_trusted_git_environment(),
        trusted=True,
    )
    if check and completed.returncode:
        raise GitPreflightError(completed.stderr.strip() or "Git command failed")
    return completed.stdout.strip()


def _git_succeeds(repository: Path, *args: str) -> bool:
    return _run_git(repository, *args).returncode == 0


def _trusted_git_succeeds(repository: Path, *args: str) -> bool:
    return _run_git(
        repository,
        *args,
        env=_trusted_git_environment(),
        trusted=True,
    ).returncode == 0


def _repository_identity(repository: Path, *, git_command: GitCommand) -> Path:
    """Return Git's resolved work-tree identity, rejecting non-repositories."""

    return Path(git_command(repository, "rev-parse", "--show-toplevel")).resolve()


def repository_identity(repository: Path) -> Path:
    return _repository_identity(repository, git_command=_readonly_git)


def repository_head(repository: Path) -> str:
    """Return HEAD using only ref plumbing, never a working-tree status read.

    Unlike ``observe_repository``, this runs no ``git status`` (or any other
    command that can consult a repository-configured ``core.fsmonitor``
    helper) -- it is nothing but ``git rev-parse HEAD``.
    """

    identity = _repository_identity(repository, git_command=_readonly_git)
    return _readonly_git(identity, "rev-parse", "HEAD")


def _contains_identity_token(subject: str, identity: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_-]){re.escape(identity)}(?![A-Za-z0-9_-])", subject) is not None


def commit_subject_error(subject: str, *, work_item_id: str, hop_id: str) -> str | None:
    """Apply the existing ordinary workflow-commit subject postcondition early."""

    if subject != subject.strip() or "\n" in subject or "\r" in subject:
        return "commit subject must be one non-empty line"
    if not _CONVENTIONAL_SUBJECT.match(subject):
        return "commit subject is not Conventional-Commit-compatible"
    if not _contains_identity_token(subject, work_item_id) or not _contains_identity_token(subject, hop_id):
        return "commit subject lacks WorkItem/Hop identity"
    return None


def _normalized_path(path: str, *, context: str, allow_root: bool = False) -> str:
    if not path or path != path.strip() or "\\" in path:
        raise TrustedCommitError(f"{context} must be a normalized repository-relative path")
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise TrustedCommitError(f"{context} must not escape the repository")
    normalized = candidate.as_posix()
    if normalized == "." and not allow_root:
        raise TrustedCommitError(f"{context} must name an explicit file path")
    if normalized != path:
        raise TrustedCommitError(f"{context} is ambiguous after normalization")
    return normalized


def _validated_intent_paths(intent: CommitIntent, allowed_paths: tuple[str, ...]) -> tuple[str, ...]:
    paths = tuple(_normalized_path(path, context="commit intent path") for path in intent.paths)
    if len(set(paths)) != len(paths):
        raise TrustedCommitError("commit intent has duplicate paths")
    scope = tuple(
        _normalized_path(path, context="authorized scope", allow_root=True) for path in allowed_paths
    )
    for path in paths:
        if not any(allowed == "." or path == allowed or path.startswith(f"{allowed}/") for allowed in scope):
            raise TrustedCommitError(f"commit intent path is outside authorized scope: {path}")
    return paths


def _changed_worktree_paths(
    repository: Path, *, git_command: GitCommand = _git
) -> tuple[str, ...]:
    """Return every non-ignored path differing from HEAD without broad staging."""

    modified = tuple(
        filter(None, git_command(repository, "diff", "--no-ext-diff", "--name-only", "HEAD").splitlines())
    )
    untracked = tuple(
        filter(None, git_command(repository, "ls-files", "--others", "--exclude-standard").splitlines())
    )
    return tuple(dict.fromkeys((*modified, *untracked)))


def _staged_paths(repository: Path, *, git_command: GitCommand = _git) -> tuple[str, ...]:
    """Return every staged leaf path, rename-blind like the other observations.

    ``--no-renames`` is not cosmetic here: with rename detection enabled a
    same-content delete plus add is reported as the destination only, so an
    exact-leaf comparison against a CommitIntent naming both leaves refused a
    legitimate rename (BUG-WFC-TRUSTED-COMMIT-RENAME-RECONCILIATION). Every
    other path projection in this module already passes the flag; staying
    rename-blind admits nothing, because the source leaf's authorization and
    its dirty state are checked on their own.
    """

    return tuple(
        filter(
            None,
            git_command(
                repository, "diff", "--no-renames", "--no-ext-diff", "--cached", "--name-only"
            ).splitlines(),
        )
    )


_REGULAR_STAGE_MODES = {"100644", "100755"}


def _staged_non_regular_paths(
    repository: Path, paths: tuple[str, ...], *, git_command: GitCommand
) -> tuple[str, ...]:
    """Return staged paths whose index entry mode is not an ordinary file.

    Guards the narrow window between the pre-staging ``lstat`` validation and
    the commit itself: a path swapped for a symlink (or anything else) right
    before ``git add`` staged it must never reach the committed tree, even
    though ``git add`` already staged it.
    """

    output = git_command(repository, "ls-files", "--stage", "--", *paths)
    offending: list[str] = []
    for line in filter(None, output.splitlines()):
        mode, remainder = line.split(" ", 1)
        _, path = remainder.split("\t", 1)
        if mode not in _REGULAR_STAGE_MODES:
            offending.append(path)
    return tuple(offending)


def _observe_repository(repository: Path, *, git_command: GitCommand) -> GitFacts:
    """Independently observe HEAD and Git's tracked/non-ignored worktree state."""

    identity = _repository_identity(repository, git_command=git_command)
    status = git_command(identity, "status", "--porcelain=v1", "--untracked-files=all")
    tracked: list[str] = []
    untracked: list[str] = []
    for line in status.splitlines():
        if line.startswith("?? "):
            untracked.append(line[3:])
        elif line:
            tracked.append(line)
    submodules = tuple(
        line for line in git_command(identity, "submodule", "status").splitlines() if line[:1] in {"-", "+"}
    )
    return GitFacts(
        repository=identity,
        head=git_command(identity, "rev-parse", "HEAD"),
        tracked_changes=tuple(tracked),
        untracked_changes=tuple(untracked),
        unresolved_submodules=submodules,
    )


def observe_repository(repository: Path) -> GitFacts:
    identity = _repository_identity(repository, git_command=_readonly_git)
    copied_index = _isolated_index_copy(identity, prefix="wfc-observe-index-")
    try:
        environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_INDEX_FILE": str(copied_index)}

        def observation_git(repo: Path, *args: str, check: bool = True) -> str:
            completed = _run_git(repo, *args, env=environment)
            if check and completed.returncode:
                raise GitPreflightError(completed.stderr.strip() or "Git command failed")
            return completed.stdout.strip()

        return _observe_repository(identity, git_command=observation_git)
    finally:
        try:
            os.unlink(copied_index)
        except FileNotFoundError:
            pass


def preflight_hop(
    repository: Path, *, expected_repository: Path, expected_head: str
) -> GitFacts:
    """Check the auditable pre-Hopf boundary before any state write/invocation."""

    facts = observe_repository(repository)
    anomalies: list[str] = []
    if facts.repository != expected_repository.resolve():
        anomalies.append("repository identity mismatch")
    if facts.head != expected_head:
        anomalies.append("unexpected HEAD movement")
    if facts.tracked_changes:
        anomalies.append("dirty tracked state")
    if facts.untracked_changes:
        anomalies.append("non-ignored untracked state")
    if facts.unresolved_submodules:
        anomalies.append("unresolved submodule identity")
    if anomalies:
        raise GitPreflightError("; ".join(anomalies))
    return facts


def preflight_state_root(repository: Path, state_root: Path) -> None:
    """Require the state-root candidate to be ignored by a tracked .gitignore.

    ``git check-ignore`` alone is deliberately insufficient: its verbose source
    must itself be a tracked `.gitignore`, excluding `.git/info/exclude`.
    """

    identity = repository_identity(repository)
    candidate = state_root if state_root.is_absolute() else identity / state_root
    try:
        relative = candidate.resolve().relative_to(identity).as_posix()
    except ValueError as error:
        raise GitPreflightError("configured state root must be inside this repository") from error
    probe = f"{relative}/.coordinator-preflight"
    completed = subprocess.run(
        ("git", "-C", str(identity), "check-ignore", "-v", "--no-index", probe),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise GitPreflightError(
            "configured state root requires a tracked repository .gitignore rule"
        )
    source = completed.stdout.split("\t", 1)[0].rsplit(":", 2)[0]
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = identity / source_path
    try:
        source_relative = source_path.resolve().relative_to(identity).as_posix()
    except ValueError:
        source_relative = ""
    tracked = subprocess.run(
        ("git", "-C", str(identity), "ls-files", "--error-unmatch", "--", source_relative),
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0
    if not source_relative.endswith(".gitignore") or not tracked:
        raise GitPreflightError(
            "configured state root requires a tracked repository .gitignore rule; local excludes do not qualify"
        )
    clean_authority = subprocess.run(
        ("git", "-C", str(identity), "diff", "--quiet", "HEAD", "--", source_relative),
        check=False,
    ).returncode == 0
    if not clean_authority:
        raise GitPreflightError(
            "configured state root authority .gitignore must be committed and clean"
        )


def _observe_hop(
    repository: Path,
    *,
    base_head: str,
    claimed_commits: tuple[str, ...] = (),
    allowed_paths: tuple[str, ...] = (),
    pinned_paths: tuple[str, ...] = (),
    max_commits: int | None = None,
    claims_existing_commits: bool = True,
    git_command: GitCommand,
    git_succeeds: Callable[..., bool],
) -> GitDelta:
    """Observe the post-invocation range and classify repository anomalies."""

    facts = _observe_repository(repository, git_command=git_command)
    identity = facts.repository
    anomalies: list[str] = []
    base_is_commit = git_succeeds(identity, "cat-file", "-e", f"{base_head}^{{commit}}")
    if not base_is_commit:
        anomalies.append("unresolved base anchor")
        commits: tuple[str, ...] = ()
        changed: tuple[str, ...] = ()
    else:
        ancestor = git_succeeds(identity, "merge-base", "--is-ancestor", base_head, facts.head)
        if not ancestor:
            anomalies.append("divergent or rewritten ancestry")
            commits = ()
            changed = ()
        else:
            commits = tuple(
                git_command(identity, "rev-list", "--reverse", f"{base_head}..{facts.head}").splitlines()
            )
            changed = tuple(
                git_command(identity, "diff", "--no-renames", "--no-ext-diff", "--name-only", f"{base_head}..{facts.head}").splitlines()
            )
            merges = git_command(identity, "rev-list", "--merges", f"{base_head}..{facts.head}")
            if merges:
                anomalies.append("forbidden merge commit")
    if facts.tracked_changes:
        anomalies.append("dirty tracked state")
    if facts.untracked_changes:
        anomalies.append("non-ignored untracked state")
    if facts.unresolved_submodules:
        anomalies.append("unresolved submodule identity")
    if max_commits is not None and len(commits) > max_commits:
        anomalies.append("commit safety-cap breach")
    if allowed_paths:
        for path in changed:
            if not any(path == allowed or path.startswith(f"{allowed.rstrip('/')}/") for allowed in allowed_paths):
                anomalies.append(f"out-of-scope changed path: {path}")
    for path in changed:
        if path in pinned_paths:
            anomalies.append(f"pinned policy/profile mutation: {path}")
    return GitDelta(
        base_head=base_head,
        end_head=facts.head,
        actual_commits=commits,
        changed_paths=changed,
        claimed_commits=claimed_commits,
        # An empty claim is still a claim mismatch on historical agent-owned
        # commit paths.  The Coordinator-created path explicitly opts out: the
        # agent made semantic intent, not a claim that a commit already existed.
        claim_mismatch=claims_existing_commits and tuple(claimed_commits) != commits,
        anomalies=tuple(anomalies),
    )


def observe_hop(
    repository: Path,
    *,
    base_head: str,
    claimed_commits: tuple[str, ...] = (),
    allowed_paths: tuple[str, ...] = (),
    pinned_paths: tuple[str, ...] = (),
    max_commits: int | None = None,
    claims_existing_commits: bool = True,
) -> GitDelta:
    return _observe_hop(
        repository,
        base_head=base_head,
        claimed_commits=claimed_commits,
        allowed_paths=allowed_paths,
        pinned_paths=pinned_paths,
        max_commits=max_commits,
        claims_existing_commits=claims_existing_commits,
        git_command=_git,
        git_succeeds=_git_succeeds,
    )


def _configured_external_filters(repository: Path) -> tuple[str, ...]:
    """Return effective clean/process filter keys that could execute at ``git add``.

    A worktree ``.gitattributes`` file can select a named filter, while the
    executable clean/process command lives in Git configuration.  Trusted mutation
    therefore refuses any such effective configuration rather than allowing
    agent-authored attributes to select an operator-configured process.
    """

    configured = _trusted_git(
        repository,
        "config",
        "--null",
        "--name-only",
        "--get-regexp",
        r"^filter\..*\.(clean|process)$",
        check=False,
    )
    return tuple(filter(None, configured.split("\0")))


def trusted_commit(
    repository: Path,
    *,
    expected_repository: Path,
    base_head: str,
    allowed_paths: tuple[str, ...],
    intent: CommitIntent,
    work_item_id: str,
    hop_id: str,
) -> TrustedCommit:
    """Create exactly one ordinary commit over an already-mutated explicit path set.

    This is intentionally the sole Coordinator Git write surface.  It validates
    all semantic and worktree facts before staging and exposes no agent-selected
    argv or broad-tree operation.
    """

    identity = _repository_identity(repository, git_command=_trusted_git)
    if identity != expected_repository.resolve():
        raise TrustedCommitError("repository identity mismatch")
    if filters := _configured_external_filters(identity):
        raise TrustedCommitError(
            "configured clean/process filter is not permitted for trusted commit: "
            + ", ".join(filters)
        )
    facts = _observe_repository(identity, git_command=_trusted_git)
    if facts.head != base_head:
        raise TrustedCommitError("unexpected HEAD movement")
    if facts.unresolved_submodules:
        raise TrustedCommitError("unresolved submodule identity")
    subject_error = commit_subject_error(intent.subject, work_item_id=work_item_id, hop_id=hop_id)
    if subject_error:
        raise TrustedCommitError(subject_error)
    paths = _validated_intent_paths(intent, allowed_paths)
    if _staged_paths(identity, git_command=_trusted_git):
        raise TrustedCommitError("pre-existing staged state")
    actual_paths = _changed_worktree_paths(identity, git_command=_trusted_git)
    unexpected = sorted(set(actual_paths) - set(paths))
    missing = sorted(set(paths) - set(actual_paths))
    if unexpected:
        raise TrustedCommitError(f"unexpected dirty path: {', '.join(unexpected)}")
    if missing:
        raise TrustedCommitError(f"commit intent path has no worktree mutation: {', '.join(missing)}")

    try:
        _trusted_git(identity, "add", "--", *paths)
    except GitPreflightError as error:
        raise TrustedCommitError(f"staging failed: {error}") from error
    staged = _staged_paths(identity, git_command=_trusted_git)
    if set(staged) != set(paths):
        raise TrustedCommitError("staged paths differ from commit intent")
    if _trusted_git(identity, "rev-parse", "HEAD") != base_head:
        raise TrustedCommitError("unexpected HEAD movement before commit")
    try:
        _trusted_git(identity, "commit", "--no-verify", "-m", intent.subject, "--")
    except GitPreflightError as error:
        raise TrustedCommitError(f"commit failed: {error}") from error

    delta = _observe_hop(
        identity,
        base_head=base_head,
        allowed_paths=allowed_paths,
        max_commits=1,
        claims_existing_commits=False,
        git_command=_trusted_git,
        git_succeeds=_trusted_git_succeeds,
    )
    subject = _trusted_git(identity, "show", "-s", "--format=%s", delta.end_head)
    invalid = (
        len(delta.actual_commits) != 1
        or delta.actual_commits[0] != delta.end_head
        or set(delta.changed_paths) != set(paths)
        or subject != intent.subject
        or bool(delta.anomalies)
    )
    if invalid:
        raise TrustedCommitError("post-commit observation does not match commit intent", delta=delta)
    return TrustedCommit(commit=delta.end_head, delta=delta)


# --------------------------------------------------------------------------
# BP4b: bounded, non-authoritative candidate preservation.
#
# A NEW additive seam only -- it touches no code above this line. It reuses
# the existing trusted-execution and scope-safety primitives but never stages
# into, nor reads as truth, the registered index; it writes only a private
# temporary index, new blob/tree/commit objects and -- on success -- one
# create-only ref. Branch, HEAD, the live index, the worktree, the stash and
# Git configuration are never touched. Capture is never adoption, acceptance,
# scope widening or Finding transfer, and its quiescence source is always
# ``"unattested"``. Locking (the BP4a ownership ``flock``) is owned entirely
# by the caller in ``frontdoor.preservation``; this function assumes nothing
# about it.
# --------------------------------------------------------------------------


class PreservationRefused(GitPreflightError):
    """A candidate-preservation capture predicate failed; the repository is unchanged."""


@dataclass(frozen=True)
class PreservedCandidate:
    """The one factual result of ``preserve_candidate``."""

    status: str  # "preserved" or "not_needed"
    ref: str | None
    commit: str | None
    tree: str | None
    file_count: int
    bytes_total: int
    quiescence: str = "unattested"


PRESERVATION_MAX_FILES = 2048
PRESERVATION_MAX_TOTAL_BYTES = 64 * 1024 * 1024

_SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _isolated_index_copy(identity: Path, *, prefix: str) -> Path:
    """A throwaway copy of the live index; reads through it never touch the real one.

    The copy's mtime must match the live index file's own mtime, not the time
    of the copy. Git's own racy-clean protection (the defence against a
    same-second, same-size worktree edit being wrongly read as clean) decides
    whether to trust a cache entry's stat by comparing that entry's mtime
    against the *index file's* mtime. A plain byte copy stamps a fresh, later
    mtime on the copy, which can quietly defeat that protection and make
    ``git status`` read through the copy report a genuinely dirty path as
    clean. ``copy2`` carries the original mtime forward so the copy is exactly
    as racy-safe as the live index it stands in for. Shared by every call site
    that hands Git an isolated index copy; ``prefix`` keeps each site's own
    ``mkstemp`` naming.
    """

    index_location = Path(_readonly_git(identity, "rev-parse", "--git-path", "index"))
    if not index_location.is_absolute():
        index_location = identity / index_location
    descriptor, copied_index = tempfile.mkstemp(prefix=prefix)
    os.close(descriptor)
    if index_location.exists():
        shutil.copy2(index_location, copied_index)
    return Path(copied_index)


_FILTER_KEY_PATTERN = re.compile(rb"^filter\..*\.(clean|process)$")


def _configured_external_filters_byte_safe(repository: Path) -> tuple[str, ...]:
    """Byte-safe counterpart of ``_configured_external_filters`` for preservation only.

    ``_configured_external_filters`` (frozen BP4a code, shared with
    ``trusted_commit`` and ``bounded_path_commit``) asks Git's own
    ``--get-regexp`` to find the key. That query depends on the active
    locale: under a UTF-8 locale Git's regex engine does not match a
    configured filter key whose subsection is not valid UTF-8, even though
    Git still applies that filter when building a tree. ``git config --list
    -z`` instead returns every key as raw, NUL-separated bytes with no
    locale-dependent matching, so candidate preservation reads it directly
    and applies the identical key pattern itself.
    """

    completed = _run_git_raw(
        repository, "config", "--list", "-z", env=_trusted_git_environment(), trusted=True,
    )
    if completed.returncode:
        raise PreservationRefused(os.fsdecode(completed.stderr).strip() or "Git command failed")
    matches: list[str] = []
    for entry in filter(None, completed.stdout.split(b"\0")):
        key = entry.split(b"\n", 1)[0]
        if _FILTER_KEY_PATTERN.match(key):
            matches.append(os.fsdecode(key))
    return tuple(matches)


def _build_candidate_tree(identity: Path, *, base_head: str, changed_paths: tuple[str, ...]) -> str:
    """Write one tree object: ``base_head`` overlaid with the worktree content of ``changed_paths``.

    Builds into a fresh private temporary index on every call -- never the
    registered index -- and discards that index file immediately after.
    """

    descriptor, private_index = tempfile.mkstemp(prefix="wfc-preserve-tree-")
    os.close(descriptor)
    try:
        env = {**_trusted_git_environment(), "GIT_OPTIONAL_LOCKS": "0", "GIT_INDEX_FILE": private_index}

        def build_git(*args: str) -> bytes:
            completed = _run_git_raw(identity, *args, env=env, trusted=True)
            if completed.returncode:
                raise PreservationRefused(os.fsdecode(completed.stderr).strip() or "Git command failed")
            return completed.stdout.rstrip(b"\n")

        build_git("read-tree", base_head)
        if changed_paths:
            build_git("add", "-A", "--", *changed_paths)
        return os.fsdecode(build_git("write-tree"))
    finally:
        try:
            os.unlink(private_index)
        except FileNotFoundError:
            pass


def preserve_candidate(
    repository: Path,
    *,
    expected_repository: Path,
    base_head: str,
    allowed_paths: tuple[str, ...],
    run_id: str,
    max_files: int = PRESERVATION_MAX_FILES,
    max_total_bytes: int = PRESERVATION_MAX_TOTAL_BYTES,
) -> PreservedCandidate:
    """Capture unadopted worktree content as one inert, non-authoritative ref.

    Every refusal below leaves branch, HEAD, the live index, the worktree and
    every other ref byte-identical. The ownership-lock boundary is the
    caller's responsibility (``frontdoor.preservation``); this function
    performs no Git write beyond the private temporary index used to build
    the candidate tree, the new tree/commit objects, and -- on success -- one
    create-only ``refs/wfc/preserved/<run_id>/<n>``.
    """

    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise PreservationRefused("run id is not a safe ref path component")
    if not allowed_paths:
        raise PreservationRefused("preservation requires an explicit authorized scope")
    identity = _repository_identity(repository, git_command=_readonly_git)
    if identity != expected_repository.resolve():
        raise PreservationRefused("repository identity mismatch")
    if filters := _configured_external_filters_byte_safe(identity):
        raise PreservationRefused(
            "configured clean/process filter is not permitted for candidate preservation: "
            + ", ".join(filters)
        )

    copied_index = _isolated_index_copy(identity, prefix="wfc-preserve-index-")
    try:
        read_env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_INDEX_FILE": str(copied_index)}

        def read_git(*args: str) -> str:
            completed = _run_git(identity, *args, env=read_env)
            if completed.returncode:
                raise PreservationRefused(completed.stderr.strip() or "Git command failed")
            # Only the trailing newline is incidental; a leading status-line
            # character (for example the significant leading space of a
            # porcelain " M path" entry) must never be eaten by a blanket
            # ``str.strip()``.
            return completed.stdout.rstrip("\n")

        def read_git_raw(*args: str) -> bytes:
            completed = _run_git_raw(identity, *args, env=read_env)
            if completed.returncode:
                raise PreservationRefused(os.fsdecode(completed.stderr).strip() or "Git command failed")
            return completed.stdout.rstrip(b"\n")

        if read_git("rev-parse", "HEAD") != base_head:
            raise PreservationRefused("unexpected HEAD movement since classification")
        # ``-z`` (not the newline-terminated default) is required here: Git's
        # default porcelain output applies C-style quoting to any path with a
        # space, a quote, a backslash or a non-ASCII byte (core.quotePath),
        # and the quoted literal would then fail the in_scope() containment
        # test for an ordinary in-scope filename.  ``-z`` reports every path
        # verbatim, NUL-separated, with no quoting to undo. The read is raw
        # bytes, decoded with ``os.fsdecode`` (not Git's own text decode),
        # because a worktree path need not be valid UTF-8 and a strict decode
        # would raise instead of yielding a refusal or a capture.
        status = read_git_raw("status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
        raw_entries = status.split(b"\0") if status else []
        if raw_entries and raw_entries[-1] == b"":
            raw_entries.pop()
        entries = [os.fsdecode(entry) for entry in raw_entries]
        tracked_paths: list[str] = []
        untracked_paths: list[str] = []
        for entry in entries:
            if entry.startswith("?? "):
                untracked_paths.append(entry[3:])
            elif entry:
                tracked_paths.append(entry[3:])
        submodules = tuple(
            line for line in os.fsdecode(read_git_raw("submodule", "status")).splitlines()
            if line[:1] in {"-", "+"}
        )
        if submodules:
            raise PreservationRefused("unresolved submodule identity")
        if not tracked_paths and not untracked_paths:
            return PreservedCandidate(
                status="not_needed", ref=None, commit=None, tree=None, file_count=0, bytes_total=0,
            )
    finally:
        try:
            os.unlink(copied_index)
        except FileNotFoundError:
            pass

    changed_paths = tuple(sorted(dict.fromkeys((*tracked_paths, *untracked_paths))))
    try:
        scope = tuple(_normalized_path(path, context="authorized scope", allow_root=True) for path in allowed_paths)
    except GitPreflightError as error:
        raise PreservationRefused(str(error)) from error

    def in_scope(path: str) -> bool:
        return any(root == "." or path == root or path.startswith(f"{root}/") for root in scope)

    outside = [path for path in changed_paths if not in_scope(path)]
    if outside:
        raise PreservationRefused("preservation path is outside authorized scope: " + ", ".join(outside))

    try:
        _reject_unsafe_scope_special_files(identity, allowed_paths)
    except GitPreflightError as error:
        raise PreservationRefused(str(error)) from error

    file_count = 0
    total_bytes = 0
    for path in changed_paths:
        candidate = identity / path
        if not candidate.exists() and not candidate.is_symlink():
            continue  # a tracked deletion has no worktree object to size-check.
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(identity)
            metadata = candidate.lstat()
        except (OSError, ValueError) as error:
            raise PreservationRefused(f"preservation path is unsafe: {path}") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise PreservationRefused(f"preservation path is not a regular in-repository file: {path}")
        file_count += 1
        total_bytes += metadata.st_size
    if file_count > max_files:
        raise PreservationRefused(f"preservation file count exceeds limit: {file_count} > {max_files}")
    if total_bytes > max_total_bytes:
        raise PreservationRefused(f"preservation total bytes exceeds limit: {total_bytes} > {max_total_bytes}")

    first_tree = _build_candidate_tree(identity, base_head=base_head, changed_paths=changed_paths)
    second_tree = _build_candidate_tree(identity, base_head=base_head, changed_paths=changed_paths)
    if first_tree != second_tree:
        raise PreservationRefused("worktree is not stable: two consecutive tree observations differed")
    tree = second_tree

    try:
        existing = _trusted_git(
            identity, "for-each-ref", "--format=%(refname)%09%(objectname)%09%(tree)",
            f"refs/wfc/preserved/{run_id}/",
        )
    except GitPreflightError as error:
        raise PreservationRefused(str(error)) from error
    used_indices: list[int] = []
    for line in filter(None, existing.splitlines()):
        name, commit_name, tree_name = line.split("\t")
        suffix = name.rsplit("/", 1)[-1]
        if suffix.isdigit():
            used_indices.append(int(suffix))
        if tree_name == tree:
            return PreservedCandidate(
                status="preserved", ref=name, commit=commit_name, tree=tree,
                file_count=file_count, bytes_total=total_bytes,
            )

    message = (
        f"wfc preserve: non-authoritative candidate capture for {run_id} "
        "(quiescence unattested; not adoption)"
    )
    try:
        commit = _trusted_git(identity, "commit-tree", tree, "-p", base_head, "-m", message)
    except GitPreflightError as error:
        raise PreservationRefused(str(error)) from error

    next_index = max(used_indices, default=0) + 1
    attempts_remaining = len(used_indices) + 8
    while True:
        candidate_ref = f"refs/wfc/preserved/{run_id}/{next_index}"
        try:
            _trusted_git(identity, "update-ref", candidate_ref, commit, "")
        except GitPreflightError as error:
            attempts_remaining -= 1
            if attempts_remaining <= 0:
                raise PreservationRefused(f"could not create a preservation ref: {error}") from error
            next_index += 1
            continue
        return PreservedCandidate(
            status="preserved", ref=candidate_ref, commit=commit, tree=tree,
            file_count=file_count, bytes_total=total_bytes,
        )


def bounded_path_commit(
    repository: Path,
    *,
    expected_repository: Path,
    base_head: str,
    allowed_paths: tuple[str, ...],
    intent: NewPathCommitIntent,
    work_item_id: str,
    hop_id: str,
) -> TrustedCommit:
    """Commit only exactly named, newly created leaf files; refuse everything else.

    A narrower sibling of ``trusted_commit`` for Coordinator-owned callers
    acting outside an agent Hop.  It reuses the same trusted Git execution
    policy, hook/filter refusal and post-commit observation, but additionally
    refuses any intent path that already names a blob at ``base_head`` --
    there is no "existing file" case here; modification and deletion of an
    already-tracked path are both refused the same way, by that one check,
    and only exact new leaves are ever committed.  A directory-shaped path is
    refused with a clear diagnostic rather than expanded to the files beneath
    it -- this does not decide
    BUG-WFC-COMMIT-INTENT-DIRECTORY-PATH-RECONCILIATION's open choice between
    its Option 1 and Option 2, it only keeps this narrower primitive on
    Option 1's leaf-exact side of that choice.
    """

    identity = _repository_identity(repository, git_command=_trusted_git)
    if identity != expected_repository.resolve():
        raise TrustedCommitError("repository identity mismatch")
    if filters := _configured_external_filters(identity):
        raise TrustedCommitError(
            "configured clean/process filter is not permitted for bounded path commit: "
            + ", ".join(filters)
        )
    facts = _observe_repository(identity, git_command=_trusted_git)
    if facts.head != base_head:
        raise TrustedCommitError("unexpected HEAD movement")
    if facts.unresolved_submodules:
        raise TrustedCommitError("unresolved submodule identity")
    subject_error = commit_subject_error(intent.subject, work_item_id=work_item_id, hop_id=hop_id)
    if subject_error:
        raise TrustedCommitError(subject_error)
    paths = _validated_intent_paths(intent, allowed_paths)
    for path in paths:
        if _trusted_git_succeeds(identity, "cat-file", "-e", f"{base_head}:{path}"):
            raise TrustedCommitError(f"commit intent path already exists at base HEAD: {path}")
        try:
            lstat = (identity / path).lstat()
        except FileNotFoundError:
            raise TrustedCommitError(f"commit intent path does not exist in the worktree: {path}") from None
        if stat.S_ISDIR(lstat.st_mode):
            raise TrustedCommitError(f"commit intent path is a directory, not a leaf file: {path}")
        if stat.S_ISLNK(lstat.st_mode) or not stat.S_ISREG(lstat.st_mode):
            raise TrustedCommitError(f"commit intent path is not a regular file: {path}")
    if _staged_paths(identity, git_command=_trusted_git):
        raise TrustedCommitError("pre-existing staged state")
    actual_paths = _changed_worktree_paths(identity, git_command=_trusted_git)
    unexpected = sorted(set(actual_paths) - set(paths))
    missing = sorted(set(paths) - set(actual_paths))
    if unexpected:
        raise TrustedCommitError(f"unexpected dirty path: {', '.join(unexpected)}")
    if missing:
        raise TrustedCommitError(f"commit intent path has no worktree mutation: {', '.join(missing)}")

    try:
        _trusted_git(identity, "add", "--", *paths)
    except GitPreflightError as error:
        raise TrustedCommitError(f"staging failed: {error}") from error
    staged = _staged_paths(identity, git_command=_trusted_git)
    if set(staged) != set(paths):
        raise TrustedCommitError("staged paths differ from commit intent")
    if swapped := _staged_non_regular_paths(identity, paths, git_command=_trusted_git):
        raise TrustedCommitError(
            "staged path is no longer a regular file (validated, then swapped before "
            "commit); left staged in the index but not committed: " + ", ".join(swapped)
        )
    if _trusted_git(identity, "rev-parse", "HEAD") != base_head:
        raise TrustedCommitError("unexpected HEAD movement before commit")
    try:
        _trusted_git(identity, "commit", "--no-verify", "-m", intent.subject, "--")
    except GitPreflightError as error:
        raise TrustedCommitError(f"commit failed: {error}") from error

    delta = _observe_hop(
        identity,
        base_head=base_head,
        allowed_paths=allowed_paths,
        max_commits=1,
        claims_existing_commits=False,
        git_command=_trusted_git,
        git_succeeds=_trusted_git_succeeds,
    )
    subject = _trusted_git(identity, "show", "-s", "--format=%s", delta.end_head)
    invalid = (
        len(delta.actual_commits) != 1
        or delta.actual_commits[0] != delta.end_head
        or set(delta.changed_paths) != set(paths)
        or subject != intent.subject
        or bool(delta.anomalies)
    )
    if invalid:
        raise TrustedCommitError("post-commit observation does not match commit intent", delta=delta)
    return TrustedCommit(commit=delta.end_head, delta=delta)

"""Direct Git facts and fail-closed Slice-2 transaction checks.

This is deliberately a small seam: every value below is obtained from Git, not
from an invocation result.  It neither invokes an agent nor repairs a checkout.
"""

from __future__ import annotations

import os
import re
import subprocess
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


def _git(repository: Path, *args: str, check: bool = True) -> str:
    completed = _run_git(repository, *args)
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
    return _repository_identity(repository, git_command=_git)


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
    return tuple(
        filter(
            None,
            git_command(repository, "diff", "--no-ext-diff", "--cached", "--name-only").splitlines(),
        )
    )


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
    return _observe_repository(repository, git_command=_git)


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
                git_command(identity, "diff", "--no-ext-diff", "--name-only", f"{base_head}..{facts.head}").splitlines()
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

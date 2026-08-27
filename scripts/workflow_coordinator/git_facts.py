"""Direct Git facts and fail-closed Slice-2 transaction checks.

This is deliberately a small seam: every value below is obtained from Git, not
from an invocation result.  It neither invokes an agent nor repairs a checkout.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitPreflightError(RuntimeError):
    """The repository is not a safe boundary for a coordinator Hop."""


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


def _git(repository: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *args),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and completed.returncode:
        raise GitPreflightError(completed.stderr.strip() or "Git command failed")
    return completed.stdout.strip()


def repository_identity(repository: Path) -> Path:
    """Return Git's resolved work-tree identity, rejecting non-repositories."""

    return Path(_git(repository, "rev-parse", "--show-toplevel")).resolve()


def observe_repository(repository: Path) -> GitFacts:
    """Independently observe HEAD and Git's tracked/non-ignored worktree state."""

    identity = repository_identity(repository)
    status = _git(identity, "status", "--porcelain=v1", "--untracked-files=all")
    tracked: list[str] = []
    untracked: list[str] = []
    for line in status.splitlines():
        if line.startswith("?? "):
            untracked.append(line[3:])
        elif line:
            tracked.append(line)
    submodules = tuple(
        line for line in _git(identity, "submodule", "status").splitlines() if line[:1] in {"-", "+"}
    )
    return GitFacts(
        repository=identity,
        head=_git(identity, "rev-parse", "HEAD"),
        tracked_changes=tuple(tracked),
        untracked_changes=tuple(untracked),
        unresolved_submodules=submodules,
    )


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


def observe_hop(
    repository: Path,
    *,
    base_head: str,
    claimed_commits: tuple[str, ...] = (),
    allowed_paths: tuple[str, ...] = (),
    pinned_paths: tuple[str, ...] = (),
    max_commits: int | None = None,
) -> GitDelta:
    """Observe the post-invocation range and classify repository anomalies."""

    facts = observe_repository(repository)
    identity = facts.repository
    anomalies: list[str] = []
    ancestor = subprocess.run(
        ("git", "-C", str(identity), "merge-base", "--is-ancestor", base_head, facts.head),
        check=False,
    ).returncode == 0
    if not ancestor:
        anomalies.append("divergent or rewritten ancestry")
        commits: tuple[str, ...] = ()
        changed: tuple[str, ...] = ()
    else:
        commits = tuple(_git(identity, "rev-list", "--reverse", f"{base_head}..{facts.head}").splitlines())
        changed = tuple(_git(identity, "diff", "--name-only", f"{base_head}..{facts.head}").splitlines())
        merges = _git(identity, "rev-list", "--merges", f"{base_head}..{facts.head}")
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
        claim_mismatch=tuple(claimed_commits) != commits,
        anomalies=tuple(anomalies),
    )

"""Installed ``wfc prepare-checkout`` -- prepare one fresh Run checkout
(FTR-WFC-RUN-CHECKOUT-PREPARATION, D4 Option A; Beta-2 plan slice O4).

Four effects, in this order, then a printed summary: a Git worktree of the
consumer repository at a *fresh* path on a new branch; a sibling scratch
directory with a ``tmp/`` subdirectory for ``TMPDIR``; the consumer's own
declared preparation hook; and one sourceable launch-environment file in that
scratch directory.  It does not run ``wfc plan``/``wfc request``/``wfc run``,
reads and writes no lifecycle journal, allocates no Run, authorizes nothing
and calls no provider.

*It removes nothing*, in any code path, including every refusal path: a
refusal after the worktree or scratch directory exists reports both and
leaves them exactly where they are.  Reuse of a spent checkout is not this
command's subject and is not made possible by it -- the one-Run-per-checkout
invariant is enforced *earlier* here, by refusing a target path whose
prospective canonical checkout identity already carries a lifecycle, instead
of spending a lifecycle on the discovery at ``wfc run`` (friction entry 12).

The preparation hook is the safety-relevant part, and its boundary is
deliberately narrow: WFC is responsible for the safe invocation and for the
invariants (a hook is always an executable blob *committed at the base ref*,
never a file from a Run's candidate or a Worker's working tree; its output is
bounded, strictly parsed and never trusted; it has a timeout; its failure is a
named refusal), the consumer is responsible for the hook's content -- exactly
as for the declared evidence command ``wfc run`` executes verbatim.  A
committed hook is code the maintainer approved by committing it: it runs on
the operator's host, as the operator, with the operator's privileges and
environment, and can write anywhere the operator can.  WFC bounds its own
writes, not the hook's.  See the record's Design section for the full
statement of what this does not protect against.

Note: ``lifecycle._prepare_checkout`` is an unrelated private helper that only
opens the journal of an *existing* checkout; nothing here touches it.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from ..commissioning import CommissionError, _path
from ..evidence import UNTRUSTED_DIAGNOSTIC_CAPTURE_LIMIT_BYTES
from ..git_facts import GitPreflightError, observe_repository
from .config import consumer_config_from_yaml
from .journal import (
    CanonicalCheckout,
    JournalError,
    canonical_checkout,
    checkout_directory,
    checkout_identity,
    journal_path,
    require_disjoint_journal_root,
    state_home,
)
from .plan import DEFAULT_CONFIG_PATH

# The hook's stdout is a declaration channel, nothing else; every bound here
# is a named refusal rather than a truncation, so a hook that leaks its
# diagnostics to stdout fails loudly at once instead of exporting something
# nobody authored.
DECLARATION_NAME_RE = re.compile(r"\A[A-Z][A-Z0-9_]{0,63}\Z")
HOOK_STDOUT_LIMIT_BYTES = 65_536
HOOK_DECLARATION_LIMIT = 64
HOOK_VALUE_LIMIT_BYTES = 4_096
PATH_PREPEND_NAME = "WFC_PREPARE_PATH_PREPEND"
RESERVED_DECLARATION_NAMES = ("PATH", "TMPDIR")

# Same number as the kernel's evidence cap (``model.py``): a preparation hook
# that builds a venv and installs a package offline is the same order of work
# as the baseline probe it exists to make pass.
DEFAULT_HOOK_TIMEOUT_SECONDS = 900.0

ENV_FILE_NAME = "launch-env.sh"
SCRATCH_TMPDIR_NAME = "tmp"
_EXECUTABLE_BLOB_MODE = "100755"
_SYMLINK_MODE = "120000"


@dataclass
class CreatedPaths:
    """What this invocation itself created, for honest refusal reporting.

    Never a removal list: nothing in this module removes anything.  A refusal
    raised after one of these exists says so and leaves it in place.
    """

    worktree: Path | None = None
    branch: str | None = None
    scratch: Path | None = None


class PreparationRefusal(Exception):
    """A named refusal of ``wfc prepare-checkout``.

    ``name`` is this command's own refusal vocabulary, printed as part of
    every message.  It is deliberately *not* a lifecycle classification:
    nothing here writes a journal, and ``lifecycle.EXIT_CODES`` is untouched.
    """

    def __init__(self, name: str, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.name = name
        self.detail = detail
        self.created = CreatedPaths()


@dataclass(frozen=True)
class CommittedEntry:
    """One entry of the tree at the base ref: mode, type and object id."""

    mode: str
    object_type: str
    object_id: str
    path: str


@dataclass(frozen=True)
class Preparation:
    """Everything one successful preparation produced, for the summary and
    for tests -- no hidden state, nothing read back from disk later."""

    repository: Path
    worktree: Path
    branch: str
    base_commit: str
    scratch: Path
    tmpdir: Path
    env_file: Path
    hook: str | None
    checkout_id: str
    environment: Mapping[str, str] = field(default_factory=dict)
    path_prepend: Path | None = None


# --------------------------------------------------------------------------
# Decisions: pure functions over already-observed facts.
# --------------------------------------------------------------------------

def _is_inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _overlaps(first: Path, second: Path) -> bool:
    return _is_inside(first, second) or _is_inside(second, first)


def resolve_scratch_path(target: Path, scratch: str | os.PathLike[str] | None) -> Path:
    """The sibling scratch directory: ``<target>-scratch`` unless given.

    A sibling rather than a child, because ``TMPDIR`` inside the worktree is
    exactly what dirties the tree and refuses launch.
    """

    if scratch is not None:
        return Path(scratch).resolve()
    return (target.parent / f"{target.name}-scratch").resolve()


def parse_hook_declarations(stdout: bytes) -> tuple[str | None, dict[str, str]]:
    """Parse the hook's stdout strictly: ``NAME=VALUE`` lines and nothing else.

    Returns the one reserved ``WFC_PREPARE_PATH_PREPEND`` value (or ``None``)
    and the other declarations.  Everything that is not exactly this shape is
    a refusal, including a stray diagnostic line: the hook contract puts
    diagnostics on stderr.
    """

    if len(stdout) > HOOK_STDOUT_LIMIT_BYTES:
        raise PreparationRefusal(
            "refused_hook_output",
            f"the hook wrote {len(stdout)} bytes to stdout, over the "
            f"{HOOK_STDOUT_LIMIT_BYTES}-byte declaration limit (diagnostics belong on stderr)",
        )
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PreparationRefusal(
            "refused_hook_output", f"the hook's stdout is not valid UTF-8: {error}",
        ) from error

    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > HOOK_DECLARATION_LIMIT:
        raise PreparationRefusal(
            "refused_hook_output",
            f"the hook declared {len(lines)} variables, over the limit of {HOOK_DECLARATION_LIMIT}",
        )

    path_prepend: str | None = None
    declarations: dict[str, str] = {}
    for line in lines:
        name, separator, value = line.partition("=")
        if not separator:
            raise PreparationRefusal(
                "refused_hook_output",
                "the hook wrote a stdout line that is not NAME=VALUE (diagnostics belong on "
                f"stderr): {line!r}",
            )
        if not DECLARATION_NAME_RE.match(name):
            raise PreparationRefusal(
                "refused_hook_output",
                f"the hook declared a variable name outside [A-Z][A-Z0-9_]*: {name!r}",
            )
        if name in RESERVED_DECLARATION_NAMES:
            raise PreparationRefusal(
                "refused_hook_reserved_variable",
                f"the hook declared {name}, which wfc owns: TMPDIR is the scratch directory "
                f"this command created, and PATH is composed by this command from "
                f"{PATH_PREPEND_NAME}",
            )
        if (name == PATH_PREPEND_NAME and path_prepend is not None) or name in declarations:
            raise PreparationRefusal(
                "refused_hook_output", f"the hook declared {name} more than once",
            )
        if len(value.encode("utf-8")) > HOOK_VALUE_LIMIT_BYTES:
            raise PreparationRefusal(
                "refused_hook_output",
                f"the hook's value for {name} is over the {HOOK_VALUE_LIMIT_BYTES}-byte limit",
            )
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
            raise PreparationRefusal(
                "refused_hook_output", f"the hook's value for {name} contains a control character",
            )
        if name == PATH_PREPEND_NAME:
            path_prepend = value
        else:
            declarations[name] = value
    return path_prepend, declarations


def compose_launch_environment(
    *, environ: Mapping[str, str], tmpdir: Path, path_prepend: Path | None,
    declarations: Mapping[str, str],
) -> dict[str, str]:
    """The exported delta the baseline probe and the in-Hop task-check need.

    Both inherit the *launching* process's environment, so this is what a
    later shell has to enter: ``TMPDIR`` pointing at the created scratch
    directory, the hook's prepared directory first in ``PATH``, and whatever
    else the hook declared.  ``PATH``/``TMPDIR`` can never come from the hook
    (``parse_hook_declarations`` refuses them), so this composition is wholly
    wfc's own.
    """

    environment = {"TMPDIR": str(tmpdir)}
    if path_prepend is not None:
        inherited = environ.get("PATH", "")
        environment["PATH"] = f"{path_prepend}:{inherited}" if inherited else str(path_prepend)
    environment.update(declarations)
    return environment


def render_env_file(
    *, environment: Mapping[str, str], worktree: Path, branch: str, base_commit: str,
    hook: str | None, path_prepend: Path | None,
) -> str:
    """The sourceable ``export`` block, shell-quoted and deterministic.

    ``PATH`` is written in its prepend form so the file stays correct in a
    shell whose own ``PATH`` differs from the preparing one, and so sourcing
    it twice is harmless (the same directory lands first again).
    """

    lines = [
        "# wfc prepare-checkout launch environment.",
        "# Source this before `wfc plan`/`wfc request`/`wfc run` for this checkout:",
        "# the baseline probe and the in-Hop task-check inherit the launching environment.",
        f"# checkout:    {worktree}",
        f"# branch:      {branch}",
        f"# base commit: {base_commit}",
        f"# hook:        {hook if hook is not None else '(none declared)'}",
        "# Every value below is either wfc's own (TMPDIR, the PATH composition) or one the",
        "# hook declared on its stdout. Sourcing this file more than once is harmless.",
        "",
    ]
    for name in sorted(environment):
        if name == "PATH" and path_prepend is not None:
            lines.append(f'export PATH={shlex.quote(str(path_prepend))}:"$PATH"')
        else:
            lines.append(f"export {name}={shlex.quote(environment[name])}")
    return "\n".join(lines) + "\n"


def render_summary(preparation: Preparation) -> str:
    """The short, exact next-step summary; this command runs nothing further."""

    hook = preparation.hook
    hook_line = (
        f"{hook} (committed executable at {preparation.base_commit})"
        if hook is not None else "(none declared)"
    )
    lines = [
        "wfc prepare-checkout -- one fresh Run checkout prepared",
        "",
        f"checkout:     {preparation.worktree}",
        f"branch:       {preparation.branch} (new, from {preparation.base_commit})",
        f"checkout id:  {preparation.checkout_id} (no lifecycle; one Run per checkout)",
        f"scratch:      {preparation.scratch}",
        f"TMPDIR:       {preparation.tmpdir}",
        f"launch env:   {preparation.env_file}",
        f"hook:         {hook_line}",
        "",
        "Launch environment the file above exports"
        + (" (everything besides TMPDIR/PATH is the hook's own declaration):" if hook else ":"),
    ]
    for name in sorted(preparation.environment):
        if name == "PATH" and preparation.path_prepend is not None:
            lines.append(f"  PATH={preparation.path_prepend}:$PATH")
        else:
            lines.append(f"  {name}={preparation.environment[name]}")
    lines.extend([
        "",
        "NEXT EXPLICIT ACTION: nothing was authorized, launched or classified by this command, "
        "and no Run exists yet. To launch one in the prepared checkout:",
        f"  source {preparation.env_file}",
        f"  cd {preparation.worktree}",
        "  wfc plan <task> ...        # preview, writes nothing",
        "  wfc run --request <path> --yes",
        "",
        "Nothing was removed. Outside the two directories above, the only writes are Git's own "
        "worktree administration and the new branch ref in the repository's Git directory.",
    ])
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Effects: Git, the filesystem, and the consumer's hook.
# --------------------------------------------------------------------------

def _untrusted_tail(label: str, value: bytes) -> str:
    """Bounded, untrusted-labelled diagnostic text; printed only, never stored.

    Reuses the in-Hop evidence path's own limit rather than inventing a new
    one, exactly as the baseline probe's refusal does.
    """

    limit = UNTRUSTED_DIAGNOSTIC_CAPTURE_LIMIT_BYTES
    tail = value[-limit:]
    notice = f" (showing the last {limit} of {len(value)} bytes)" if len(value) > limit else ""
    return f"{label} tail, untrusted{notice}:\n{tail.decode('utf-8', 'backslashreplace')}"


def _git(repository: Path, *arguments: str, refusal: str = "refused_git") -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments), capture_output=True, text=True,
    )
    if completed.returncode:
        raise PreparationRefusal(
            refusal,
            f"git {' '.join(arguments)} failed in {repository}",
            detail=_untrusted_tail("git stderr", completed.stderr.encode("utf-8", "replace")),
        )
    return completed.stdout.strip()


def _git_raw(repository: Path, *arguments: str, refusal: str = "refused_git") -> str:
    """Like ``_git`` but without stripping: for reading a committed blob."""

    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments), capture_output=True, text=True,
    )
    if completed.returncode:
        raise PreparationRefusal(
            refusal,
            f"git {' '.join(arguments)} failed in {repository}",
            detail=_untrusted_tail("git stderr", completed.stderr.encode("utf-8", "replace")),
        )
    return completed.stdout


def _git_succeeds(repository: Path, *arguments: str) -> bool:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments), capture_output=True, text=True,
    )
    return completed.returncode == 0


def committed_entry(repository: Path, commit: str, relative_path: str) -> CommittedEntry | None:
    """The tree entry ``relative_path`` has at ``commit``, or ``None``.

    ``None`` means "not committed at the base ref" -- which includes a path
    whose own parent is a committed *symlink*, since such a path simply does
    not exist as a tree path.
    """

    output = _git(repository, "ls-tree", commit, "--", relative_path)
    if not output:
        return None
    information, _, path = output.partition("\t")
    fields = information.split()
    if len(fields) != 3 or path != relative_path:
        return None
    mode, object_type, object_id = fields
    return CommittedEntry(mode=mode, object_type=object_type, object_id=object_id, path=path)


def _declared_hook(
    repository: Path, base_commit: str, *, config_path: str, hook: str | None, use_hook: bool,
) -> str | None:
    """Resolve which hook to run: ``--no-hook``, ``--hook``, or the committed
    Consumer Config's optional ``checkout_preparation_hook`` key.

    The *declaration* is read from the config committed at the base ref, never
    from the working tree: an uncommitted edit of ``.wfc/config.yaml`` must not
    be able to redirect a command that runs executable code on the operator's
    host.  A missing or uncommitted config file means "no declaration"; a
    committed but unparseable one is a refusal.
    """

    if not use_hook:
        return None
    if hook is not None:
        return _validated_hook_path(hook, origin="--hook")
    try:
        normalized_config = _path(config_path, context="--config")
    except CommissionError as error:
        raise PreparationRefusal("refused_config", str(error)) from error
    entry = committed_entry(repository, base_commit, normalized_config)
    if entry is None or entry.object_type != "blob":
        return None
    text = _git_raw(repository, "show", f"{base_commit}:{normalized_config}", refusal="refused_config")
    try:
        config = consumer_config_from_yaml(text)
    except CommissionError as error:
        raise PreparationRefusal(
            "refused_config",
            f"the Consumer Config committed at {base_commit} as {normalized_config} cannot be "
            f"parsed: {error}",
        ) from error
    if config.checkout_preparation_hook is None:
        return None
    return _validated_hook_path(
        config.checkout_preparation_hook, origin=f"{normalized_config} checkout_preparation_hook",
    )


def _validated_hook_path(hook: str, *, origin: str) -> str:
    try:
        return _path(hook, context=f"preparation hook ({origin})")
    except CommissionError as error:
        raise PreparationRefusal(
            "refused_hook_outside_repository",
            f"{error}; a hook is always a path inside the repository, committed at the base ref",
        ) from error


def _require_safe_committed_hook(repository: Path, base_commit: str, hook: str) -> CommittedEntry:
    """Refuse an unsafe hook *before* anything is created."""

    entry = committed_entry(repository, base_commit, hook)
    if entry is None:
        raise PreparationRefusal(
            "refused_hook_not_committed",
            f"the preparation hook {hook} is not a single committed entry at {base_commit}; a "
            "hook is never read from an uncommitted file, a Run's candidate or a Worker's "
            "working tree",
        )
    if entry.mode == _SYMLINK_MODE:
        raise PreparationRefusal(
            "refused_hook_symlink",
            f"the preparation hook {hook} is a symlink at {base_commit}; a symlink could point "
            "out of the tree or be swapped between the check and the execution",
        )
    if entry.object_type != "blob" or entry.mode != _EXECUTABLE_BLOB_MODE:
        raise PreparationRefusal(
            "refused_hook_not_executable",
            f"the preparation hook {hook} is not an executable file at {base_commit} (mode "
            f"{entry.mode}, type {entry.object_type}; expected mode {_EXECUTABLE_BLOB_MODE})",
        )
    return entry


def _require_hook_is_the_base_blob(worktree: Path, hook: str, entry: CommittedEntry) -> Path:
    """Re-check the file actually about to be executed, in the new worktree.

    The worktree is a checkout of the base commit, so the committed blob and
    the on-disk file must agree; this closes the gap between deciding and
    executing, and refuses a symlinked path component the committed tree
    could not express.
    """

    hook_file = worktree / hook
    current = worktree
    for component in Path(hook).parts:
        current = current / component
        if current.is_symlink():
            raise PreparationRefusal(
                "refused_hook_symlink", f"a component of the hook path is a symlink: {current}",
            )
    if not hook_file.is_file():
        raise PreparationRefusal(
            "refused_hook_not_committed", f"the hook is not a regular file in the checkout: {hook_file}",
        )
    on_disk = _git(worktree, "hash-object", "--no-filters", "--", hook)
    if on_disk != entry.object_id:
        raise PreparationRefusal(
            "refused_hook_not_identical_to_base_blob",
            f"the hook file in the prepared checkout ({on_disk}) is not the blob the base ref "
            f"names ({entry.object_id})",
        )
    if not hook_file.stat().st_mode & stat.S_IXUSR:
        raise PreparationRefusal(
            "refused_hook_not_executable", f"the hook file is not executable: {hook_file}",
        )
    return hook_file


def _run_hook(
    *, hook_file: Path, hook: str, worktree: Path, scratch: Path, tmpdir: Path, base_commit: str,
    timeout_seconds: float, environ: Mapping[str, str],
) -> tuple[Path | None, dict[str, str]]:
    """Run the consumer's hook once, bound its output, trust none of it.

    Given: the checkout path, the scratch path and the base commit on argv,
    the created worktree as working directory, and the operator's own
    environment plus ``TMPDIR``.  The environment is deliberately *not*
    scrubbed: a venv build needs the host toolchain, and pretending to
    isolate a program that already runs with the operator's privileges would
    be a safety claim this command cannot keep.
    """

    environment = {**environ, "TMPDIR": str(tmpdir)}
    try:
        completed = subprocess.run(
            (str(hook_file), str(worktree), str(scratch), base_commit),
            cwd=str(worktree), capture_output=True, timeout=timeout_seconds, env=environment,
        )
    except subprocess.TimeoutExpired as error:
        raise PreparationRefusal(
            "refused_hook_timeout",
            f"the preparation hook {hook} exceeded {timeout_seconds} seconds",
            detail=_untrusted_tail("hook stderr", error.stderr or b""),
        ) from error
    except OSError as error:
        raise PreparationRefusal(
            "refused_hook_failed", f"the preparation hook {hook} could not be executed: {error}",
        ) from error
    if completed.returncode:
        raise PreparationRefusal(
            "refused_hook_failed",
            f"the preparation hook {hook} failed with exit code {completed.returncode}",
            detail=_untrusted_tail("hook stdout", completed.stdout)
            + "\n"
            + _untrusted_tail("hook stderr", completed.stderr),
        )
    declared_prepend, declarations = parse_hook_declarations(completed.stdout)
    prepend = None
    if declared_prepend is not None:
        prepend = _validated_path_prepend(declared_prepend, worktree=worktree, scratch=scratch)
    return prepend, declarations


def _validated_path_prepend(declared: str, *, worktree: Path, scratch: Path) -> Path:
    candidate = Path(declared)
    if not candidate.is_absolute():
        raise PreparationRefusal(
            "refused_hook_path_prepend",
            f"{PATH_PREPEND_NAME} must be an absolute path, got: {declared!r}",
        )
    resolved = candidate.resolve()
    if not resolved.is_dir():
        raise PreparationRefusal(
            "refused_hook_path_prepend", f"{PATH_PREPEND_NAME} is not an existing directory: {resolved}",
        )
    if not (_is_inside(resolved, worktree) or _is_inside(resolved, scratch)):
        raise PreparationRefusal(
            "refused_hook_path_prepend",
            f"{PATH_PREPEND_NAME} is outside the prepared checkout and scratch directory: "
            f"{resolved}; a hook may only put a directory it prepared itself in front of PATH",
        )
    return resolved


def existing_lifecycle_evidence(checkout_dir: Path) -> str | None:
    """What makes the prospective checkout identity already spent, if anything."""

    journal = journal_path(checkout_dir)
    if journal.exists():
        return f"a lifecycle journal already exists at {journal}"
    if checkout_dir.is_dir() and any(checkout_dir.iterdir()):
        return f"lifecycle state for this checkout identity already exists at {checkout_dir}"
    return None


def _require_outside_repository(path: Path, *, kind: str, source: CanonicalCheckout) -> None:
    if _overlaps(path, source.toplevel) or _overlaps(path, source.git_common_dir):
        raise PreparationRefusal(
            "refused_path_inside_repository",
            f"the {kind} path is inside (or contains) the repository: {path}; an untracked "
            "file anywhere in the tree dirties it and refuses launch",
        )


def _require_fresh_leaf(given: str | os.PathLike[str], *, kind: str) -> Path:
    path = Path(given)
    if os.path.lexists(path):
        raise PreparationRefusal(
            "refused_existing_path",
            f"the {kind} path already exists: {path}; this command creates a *fresh* path and "
            "never reuses or removes anything",
        )
    resolved = path.resolve()
    if not resolved.parent.is_dir():
        raise PreparationRefusal(
            "refused_missing_parent",
            f"the parent directory of the {kind} path does not exist: {resolved.parent}",
        )
    return resolved


def prepare_checkout(
    *, repository: str | os.PathLike[str], target: str | os.PathLike[str], branch: str,
    base_ref: str = "HEAD", scratch: str | os.PathLike[str] | None = None,
    hook: str | None = None, use_hook: bool = True, config_path: str = DEFAULT_CONFIG_PATH,
    hook_timeout_seconds: float = DEFAULT_HOOK_TIMEOUT_SECONDS,
    environ: Mapping[str, str] | None = None,
) -> Preparation:
    """Prepare one fresh Run checkout, or refuse by name having created nothing
    it can avoid creating -- and having removed nothing in either case."""

    environ = os.environ if environ is None else environ
    created = CreatedPaths()
    try:
        return _prepare(
            repository=repository, target=target, branch=branch, base_ref=base_ref,
            scratch=scratch, hook=hook, use_hook=use_hook, config_path=config_path,
            hook_timeout_seconds=hook_timeout_seconds, environ=environ, created=created,
        )
    except PreparationRefusal as error:
        error.created = created
        raise


def _prepare(
    *, repository, target, branch, base_ref, scratch, hook, use_hook, config_path,
    hook_timeout_seconds, environ, created: CreatedPaths,
) -> Preparation:
    # --- decide everything decidable before the first effect -------------
    try:
        source = canonical_checkout(Path(repository))
    except (GitPreflightError, JournalError, subprocess.CalledProcessError) as error:
        raise PreparationRefusal(
            "refused_git", f"not a usable Git repository: {repository} ({error})",
        ) from error
    repository_root = source.toplevel

    base_commit = _git(
        repository_root, "rev-parse", "--verify", f"{base_ref}^{{commit}}",
        refusal="refused_unknown_base_ref",
    )

    try:
        facts = observe_repository(repository_root)
    except GitPreflightError as error:
        raise PreparationRefusal(
            "refused_git", f"cannot observe the repository at {repository_root}: {error}",
        ) from error
    if not facts.clean or facts.unresolved_submodules:
        changed = sorted({*facts.tracked_changes, *facts.untracked_changes})
        shown = ", ".join(changed[:10]) + (" ..." if len(changed) > 10 else "")
        raise PreparationRefusal(
            "refused_dirty_base",
            f"the repository is not clean at {repository_root}; a prepared checkout starts from "
            f"a committed baseline the operator can name (changed: {shown or 'unresolved submodules'})",
        )

    # Where the two paths *are* is decided before whether they exist: a path
    # inside the repository, or a scratch directory nested in the checkout, is
    # the more actionable refusal of the two.
    worktree = Path(target).resolve()
    _require_outside_repository(worktree, kind="checkout", source=source)
    _require_fresh_leaf(target, kind="checkout")

    scratch_path = resolve_scratch_path(worktree, scratch)
    _require_outside_repository(scratch_path, kind="scratch", source=source)
    if _overlaps(scratch_path, worktree):
        raise PreparationRefusal(
            "refused_scratch_inside_checkout",
            f"the scratch path {scratch_path} and the checkout path {worktree} are nested; "
            "TMPDIR must lie outside the worktree",
        )
    _require_fresh_leaf(scratch_path, kind="scratch")

    prospective = CanonicalCheckout(
        checkout_id=checkout_identity(worktree, source.git_common_dir),
        toplevel=worktree, git_common_dir=source.git_common_dir,
    )
    journal_root = state_home()
    try:
        require_disjoint_journal_root(journal_root, prospective)
    except JournalError as error:
        raise PreparationRefusal("refused_journal_root", str(error)) from error
    spent = existing_lifecycle_evidence(checkout_directory(prospective, root=journal_root))
    if spent is not None:
        raise PreparationRefusal(
            "refused_existing_lifecycle",
            f"{spent}: the checkout identity this path would have is already spent (one Run per "
            "canonical checkout). Choose a new path -- this command never makes a spent checkout "
            "reusable and removes nothing",
        )

    if _git_succeeds(repository_root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"):
        raise PreparationRefusal(
            "refused_existing_branch",
            f"branch {branch} already exists; this command creates a branch and never moves, "
            "reuses or deletes a ref",
        )

    hook_relative = _declared_hook(
        repository_root, base_commit, config_path=config_path, hook=hook, use_hook=use_hook,
    )
    entry = (
        _require_safe_committed_hook(repository_root, base_commit, hook_relative)
        if hook_relative is not None else None
    )

    # --- effects ---------------------------------------------------------
    _git(repository_root, "worktree", "add", "-b", branch, str(worktree), base_commit)
    created.worktree, created.branch = worktree, branch

    tmpdir = scratch_path / SCRATCH_TMPDIR_NAME
    try:
        scratch_path.mkdir(mode=0o700)
        created.scratch = scratch_path
        tmpdir.mkdir(mode=0o700)
    except OSError as error:
        raise PreparationRefusal(
            "refused_create", f"cannot create the scratch directory {scratch_path}: {error}",
        ) from error

    path_prepend: Path | None = None
    declarations: dict[str, str] = {}
    if hook_relative is not None and entry is not None:
        hook_file = _require_hook_is_the_base_blob(worktree, hook_relative, entry)
        path_prepend, declarations = _run_hook(
            hook_file=hook_file, hook=hook_relative, worktree=worktree, scratch=scratch_path,
            tmpdir=tmpdir, base_commit=base_commit, timeout_seconds=hook_timeout_seconds,
            environ=environ,
        )

    environment = compose_launch_environment(
        environ=environ, tmpdir=tmpdir, path_prepend=path_prepend, declarations=declarations,
    )
    env_file = scratch_path / ENV_FILE_NAME
    try:
        env_file.write_text(
            render_env_file(
                environment=environment, worktree=worktree, branch=branch,
                base_commit=base_commit, hook=hook_relative, path_prepend=path_prepend,
            ),
            encoding="utf-8",
        )
    except OSError as error:
        raise PreparationRefusal(
            "refused_create", f"cannot write the launch environment {env_file}: {error}",
        ) from error
    return Preparation(
        repository=repository_root, worktree=worktree, branch=branch, base_commit=base_commit,
        scratch=scratch_path, tmpdir=tmpdir, env_file=env_file, hook=hook_relative,
        checkout_id=prospective.checkout_id, environment=environment, path_prepend=path_prepend,
    )


# --------------------------------------------------------------------------
# CLI.
# --------------------------------------------------------------------------

def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wfc prepare-checkout",
        description=(
            "Prepare one fresh Run checkout: a Git worktree at a fresh path on a new branch, a "
            "sibling scratch directory for TMPDIR, the consumer's own declared preparation hook, "
            "and one sourceable launch-environment file. Removes nothing, ever. Runs no `wfc "
            "plan`/`wfc run`, writes no journal and authorizes nothing. Refuses, by name, a "
            "path whose checkout identity already carries a lifecycle, an existing path, a path "
            "inside the repository, a dirty base and an unsafe hook."
        ),
    )
    parser.add_argument("--repository", default=".",
                        help="the consumer repository to make a worktree of (default: .)")
    parser.add_argument("--path", required=True, metavar="PATH",
                        help="the fresh checkout path to create; must not exist")
    parser.add_argument("--branch", required=True, metavar="NAME",
                        help="the new branch to create for the checkout; must not exist")
    parser.add_argument("--base-ref", default="HEAD", metavar="REF",
                        help="the commit-ish the checkout starts from (default: HEAD)")
    parser.add_argument("--scratch", default=None, metavar="PATH",
                        help="the scratch directory to create (default: <path>-scratch, a sibling)")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, metavar="PATH",
                        help="repository-relative Consumer Config whose optional "
                             "checkout_preparation_hook key declares the hook; read from the "
                             f"commit named by --base-ref (default: {DEFAULT_CONFIG_PATH})")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--hook", default=None, metavar="PATH",
                       help="repository-relative path of an executable committed at --base-ref to "
                            "run as this repository's preparation hook, overriding the declaration")
    group.add_argument("--no-hook", action="store_true",
                       help="prepare the checkout, scratch directory and launch environment with "
                            "no hook at all")
    parser.add_argument("--hook-timeout-seconds", type=float,
                        default=DEFAULT_HOOK_TIMEOUT_SECONDS, metavar="SECONDS",
                        help=f"hook timeout (default: {DEFAULT_HOOK_TIMEOUT_SECONDS})")
    args = parser.parse_args(argv)
    if not args.hook_timeout_seconds > 0:
        parser.error("--hook-timeout-seconds must be a positive number of seconds")
    return args


def _left_in_place_lines(created: CreatedPaths) -> list[str]:
    lines = []
    if created.worktree is not None:
        lines.append(
            f"The worktree {created.worktree} (branch {created.branch}) was created and is LEFT "
            "IN PLACE; nothing was removed."
        )
    if created.scratch is not None:
        lines.append(f"The scratch directory {created.scratch} was created and is LEFT IN PLACE.")
    if lines:
        lines.append(
            "Inspect them, then prepare the next attempt at a *different* path: this path's "
            "checkout identity is unaffected, but reusing it is not this command's business."
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        preparation = prepare_checkout(
            repository=args.repository, target=args.path, branch=args.branch,
            base_ref=args.base_ref, scratch=args.scratch, hook=args.hook,
            use_hook=not args.no_hook, config_path=args.config,
            hook_timeout_seconds=args.hook_timeout_seconds,
        )
    except PreparationRefusal as error:
        print(f"wfc prepare-checkout: {error.name}: {error}", file=sys.stderr)
        if error.detail:
            print(error.detail, file=sys.stderr)
        for line in _left_in_place_lines(error.created):
            print(line, file=sys.stderr)
        return 2
    print(render_summary(preparation), end="")
    return 0

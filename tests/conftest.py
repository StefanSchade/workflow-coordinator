"""Place pytest-owned temporary trees on the repository's test scratch."""

import os
from pathlib import Path

import pytest


@pytest.hookimpl(trylast=True)
def pytest_configure(config):
    # Explicit pytest settings retain pytest's own semantics, including its
    # destructive reset of a user-specified --basetemp.
    if config.getoption("basetemp") or os.environ.get("PYTEST_DEBUG_TEMPROOT"):
        return

    repository = Path(__file__).resolve().parent.parent
    supplied = next(
        (os.environ[k] for k in ("TMPDIR", "TMP", "TEMP") if os.environ.get(k)), None
    )
    root = Path(supplied).expanduser() if supplied else repository / "tmp" / "pytest"
    try:
        if supplied:
            root = root.resolve(strict=True)
            if not root.is_dir() or root == Path("/tmp") or Path("/tmp") in root.parents:
                raise ValueError("scratch must be a directory outside /tmp")
            if root == repository or (
                repository in root.parents and not root.is_relative_to(repository / "tmp")
            ):
                raise ValueError("scratch inside the checkout must be under ignored tmp/")
            root = root / "wfc-pytest"
            if root.is_symlink():
                raise ValueError("pytest scratch directory must not be a symlink")
            root.mkdir(mode=0o700, exist_ok=True)
        else:
            if (repository / "tmp").is_symlink() or root.is_symlink():
                raise ValueError("repository scratch directories must not be symlinks")
            root.mkdir(parents=True, exist_ok=True)
            root = root.resolve(strict=True)
            if repository not in root.parents or root.is_relative_to(Path("/tmp")):
                raise ValueError("repository default must be inside the checkout and outside /tmp")
        # Use pytest's own numbered directories, locks and retention policy.
        # Limit this override to allocation; subprocesses and tempfile keep
        # their original environment and behavior.
        os.environ["PYTEST_DEBUG_TEMPROOT"] = str(root)
        try:
            config._tmp_path_factory.getbasetemp()
        finally:
            del os.environ["PYTEST_DEBUG_TEMPROOT"]
    except (OSError, ValueError) as exc:
        raise pytest.UsageError(f"Cannot create pytest temporary base under {root}: {exc}") from exc


def pytest_report_header(config):
    base = config._tmp_path_factory._basetemp
    if base is not None:
        return f"pytest temporary base: {base}"

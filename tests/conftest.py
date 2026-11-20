"""Pytest configuration: verified offline inputs and repository test scratch.

The required full suite builds real virtual environments and runs real PEP-517
isolated builds.  Before any test runs, this module verifies the explicitly
selected third-party wheel bundle (``WFC_TEST_INPUTS``/``WFC_TEST_PROFILE``)
against the repository profile and then makes the whole process tree use only
those inputs.  Missing, incompatible or corrupt inputs fail configuration; they
never skip tests or fall back to an index or a cache.  See the Developer Guide.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parent.parent
PARTIAL_FLAG = "--wfc-partial"
_STATE = pytest.StashKey()
_REQUIRED_REAL = frozenset({
    "tests/architecture/test_offline_distribution.py::test_clean_isolated_distribution_and_wheel_install",
    "tests/unit/test_neutral_consumer_evidence.py::test_shell_consumer_uses_real_evidence_runner",
})


def pytest_collection_finish(session):
    state = session.config.stash.get(_STATE, {})
    if state.get("mode") == "required":
        state["selected_real"] = {item.nodeid for item in session.items} & _REQUIRED_REAL
        state["real_reports"] = {}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    state = item.config.stash.get(_STATE, {})
    if state.get("mode") == "required" and item.nodeid in _REQUIRED_REAL:
        state.setdefault("real_reports", {}).setdefault(item.nodeid, {})[report.when] = report.outcome


_SETUP_HELP = (
    "The required test suite needs verified offline inputs. Set WFC_TEST_INPUTS to the bundle "
    "directory and WFC_TEST_PROFILE to the profile id (for example cp313-linux-x86_64); create or "
    "check a bundle with scripts/wfc_test_inputs.py fetch|verify (see docs/developer_guide). "
    f"A clearly partial subset run needs {PARTIAL_FLAG} with explicit test paths and is never "
    "required-suite acceptance."
)


def _tool():
    path = REPOSITORY / "scripts" / "wfc_test_inputs.py"
    spec = importlib.util.spec_from_file_location("wfc_test_inputs", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["wfc_test_inputs"] = module
    spec.loader.exec_module(module)
    return module


def pytest_addoption(parser):
    parser.addoption(
        PARTIAL_FLAG, action="store_true", dest="wfc_partial", default=False,
        help="explicit partial subset run; never required-suite acceptance",
    )


def _whole_suite(arg):
    path = Path(str(arg).split("::")[0]).absolute()
    return path.resolve() in (REPOSITORY, REPOSITORY / "tests")


def _drop_pip_settings():
    for key in [k for k in os.environ if k.upper().startswith("PIP_")]:
        del os.environ[key]


def _configure_offline_inputs(config):
    option = config.option
    if option.collectonly or option.help or option.version:
        config.stash[_STATE] = {"mode": "inert"}
        return
    tool = _tool()
    partial = option.wfc_partial
    if partial:
        # Only the literal command line may select partial mode: PYTEST_ADDOPTS or ini
        # options must not turn a required run into a partial one.
        if PARTIAL_FLAG not in tuple(config.invocation_params.args):
            raise pytest.UsageError(f"{PARTIAL_FLAG} is honoured only when given directly on the command line")
        selected = option.file_or_dir or []
        if not selected or any(_whole_suite(arg) for arg in selected):
            raise pytest.UsageError(f"{PARTIAL_FLAG} requires explicit test paths below tests/, not the whole suite")
    bundle = os.environ.get("WFC_TEST_INPUTS")
    profile_id = os.environ.get("WFC_TEST_PROFILE")
    if partial and not bundle and not profile_id:
        # No inputs: stay offline and fail closed; never reach for an index or a cache.
        _drop_pip_settings()
        os.environ.update(tool.sanitized_pip_settings())
        config.stash[_STATE] = {"mode": "partial", "bundle": None}
        return
    if not bundle or not profile_id:
        raise pytest.UsageError("WFC_TEST_INPUTS and WFC_TEST_PROFILE must both be set. " + _SETUP_HELP)
    bundle_path = Path(os.path.abspath(bundle))
    try:
        profile = tool.load_profile(profile_id)
        tool.verify_bundle(profile, bundle_path)
    except tool.InputsError as exc:
        details = "\n".join(f"  - {problem}" for problem in exc.problems)
        raise pytest.UsageError(
            f"offline test inputs at {bundle_path} (profile {profile_id}) are not usable:\n{details}\n"
            + _SETUP_HELP
        ) from exc
    # Venvs, pip, PEP-517 build isolation and subprocess fixtures all inherit this.
    environment = tool.offline_environment(dict(os.environ), bundle_path, profile_id)
    _drop_pip_settings()
    os.environ.update({k: v for k, v in environment.items() if k.startswith(("PIP_", "WFC_TEST_"))})
    config.stash[_STATE] = {
        "mode": "partial" if partial else "required",
        "bundle": bundle_path, "profile_id": profile_id, "profile": profile, "tool": tool,
        "wheels": len(profile["wheels"]),
    }


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    state = session.config.stash.get(_STATE, None)
    if not state or not state.get("bundle"):
        return
    if state.get("mode") == "required":
        reports = state.get("real_reports", {})
        missing = sorted(node for node in _REQUIRED_REAL if node not in state.get("selected_real", set())
                         or reports.get(node) != {"setup": "passed", "call": "passed", "teardown": "passed"})
        if missing:
            state["missing_real"] = missing
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
    try:
        state["tool"].verify_bundle(state["profile"], state["bundle"])
    except state["tool"].InputsError as exc:
        state["changed"] = exc.problems
        if session.exitstatus in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED):
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter):
    state = terminalreporter.config.stash.get(_STATE, None) or {}
    if state.get("missing_real"):
        terminalreporter.write_sep("=", "REQUIRED REAL TESTS NOT SELECTED AND PASSED", red=True, bold=True)
        for node in state["missing_real"]:
            terminalreporter.write_line(f"  - {node}")
    if state.get("changed"):
        terminalreporter.write_sep("=", "OFFLINE INPUTS CHANGED OR INVALID DURING THE RUN", red=True, bold=True)
        for problem in state["changed"]:
            terminalreporter.write_line(f"  - {problem}")
    if state.get("mode") == "partial":
        terminalreporter.write_sep(
            "=", "WFC PARTIAL RUN: subset only, NOT the required suite or its acceptance", yellow=True, bold=True
        )


@pytest.hookimpl(trylast=True)
def pytest_configure(config):
    _configure_offline_inputs(config)
    # Explicit pytest settings retain pytest's own semantics, including its
    # destructive reset of a user-specified --basetemp.
    if config.getoption("basetemp") or os.environ.get("PYTEST_DEBUG_TEMPROOT"):
        return

    repository = REPOSITORY
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
    lines = []
    base = config._tmp_path_factory._basetemp
    if base is not None:
        lines.append(f"pytest temporary base: {base}")
    state = config.stash.get(_STATE, None) or {}
    if state.get("bundle"):
        lines.append(
            f"offline inputs: profile {state['profile_id']}, {state['wheels']} wheels verified in {state['bundle']}"
        )
    elif state.get("mode") == "partial":
        lines.append("offline inputs: none (pip forced offline without find-links)")
    elif state.get("mode") == "inert":
        lines.append("offline inputs: not verified (collect-only/help/version)")
    if state.get("mode") == "partial":
        lines.append("WFC PARTIAL RUN: not the required suite")
    return lines

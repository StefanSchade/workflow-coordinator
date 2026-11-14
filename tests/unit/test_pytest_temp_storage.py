"""Exercise repository pytest configuration in real, independent processes."""

import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

REPO = Path(__file__).resolve().parents[2]
FILE = Path(__file__).resolve()


def environment(**updates):
    env = os.environ.copy()
    for key in ("TMPDIR", "TMP", "TEMP", "PYTEST_DEBUG_TEMPROOT", "PYTEST_ADDOPTS"):
        env.pop(key, None)
    env.update(updates)
    return env


def launch(env, *args):
    return subprocess.Popen(
        [sys.executable, "-m", "pytest", "-q", "-s", str(FILE), "-k", "temp_storage_probe", *args],
        cwd=REPO, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def finish(process):
    output, _ = process.communicate(timeout=60)
    assert process.returncode == 0, output
    return output


def test_temp_storage_probe(tmp_path, tmp_path_factory):
    marker = os.environ.get("WFC_TEMP_PROBE")
    if not marker:
        pytest.skip("subprocess probe")
    assert tmp_path.parent == tmp_path_factory.getbasetemp()
    assert tmp_path.is_dir()
    subprocess.run(["git", "init", "-q", str(tmp_path / "repository")], check=True)
    assert (tmp_path / "repository" / ".git").is_dir()
    Path(marker).write_text(str(tmp_path_factory.getbasetemp()))
    release = os.environ.get("WFC_TEMP_RELEASE")
    if release:
        deadline = time.monotonic() + 30
        while not Path(release).exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert Path(release).exists()
        assert tmp_path.is_dir()


def test_default_and_explicit_basetemp(tmp_path):
    marker = tmp_path / "default"
    finish(launch(environment(WFC_TEMP_PROBE=str(marker))))
    base = Path(marker.read_text())
    assert base.is_relative_to(REPO / "tmp" / "pytest")
    assert not base.is_relative_to(Path("/tmp"))
    explicit = tmp_path / "explicit"
    finish(launch(environment(WFC_TEMP_PROBE=str(marker)), "--basetemp", str(explicit)))
    assert Path(marker.read_text()) == explicit
    debug_root = tmp_path / "debug-root"
    debug_root.mkdir()
    finish(launch(environment(WFC_TEMP_PROBE=str(marker), PYTEST_DEBUG_TEMPROOT=str(debug_root))))
    assert Path(marker.read_text()).is_relative_to(debug_root)


def test_supplied_scratch_and_overlapping_runs(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    release = tmp_path / "release"
    markers = [tmp_path / "first", tmp_path / "second"]
    processes = [launch(environment(TMPDIR=str(scratch), WFC_TEMP_PROBE=str(marker),
                                    WFC_TEMP_RELEASE=str(release))) for marker in markers]
    try:
        deadline = time.monotonic() + 30
        while not all(marker.exists() for marker in markers) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert all(marker.exists() for marker in markers)
        bases = [Path(marker.read_text()) for marker in markers]
        assert bases[0] != bases[1]
        assert all(base.is_relative_to(scratch) and base.is_dir() for base in bases)
    finally:
        release.touch()
        for process in processes:
            finish(process)


def test_unsuitable_scratch_fails_clearly(tmp_path):
    process = launch(environment(TMPDIR=str(tmp_path / "missing")))
    output, _ = process.communicate(timeout=60)
    assert process.returncode != 0
    assert "Cannot create pytest temporary base" in output

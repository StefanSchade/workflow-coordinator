"""Verified offline development inputs: profile, verifier, fetch, exec, pytest gate.

Synthetic wheels and profiles keep most cases hermetic.  A few cases use the real
host (venv/ensurepip, pip configuration) or real pytest subprocesses with a copy of
``tests/conftest.py``.  No test reaches a package index.
"""

import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from email.message import Message
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

REPO = Path(__file__).resolve().parents[2]
TOOL_PATH = REPO / "scripts" / "wfc_test_inputs.py"
PROFILE_DIR = REPO / "tests" / "fixtures" / "offline-test-inputs"
REAL_PROFILE_ID = "cp313-linux-x86_64"

_spec = importlib.util.spec_from_file_location("wfc_test_inputs_under_test", TOOL_PATH)
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

PROBE = {
    "implementation": "cpython", "python": "3.13", "machine": "x86_64", "system": "linux",
    "soabi": "cpython-313-x86_64-linux-gnu", "glibc": (2, 41), "seed_pip_version": "25.1.1",
}
FAKE_HOST = {
    "implementation": "cpython", "python": "3.13", "machine": "x86_64", "system": "linux",
    "soabi": "cpython-313-x86_64-linux-gnu", "min_glibc": "2.28", "seed_pip_version": "25.1.1",
}
TAGS = ["py3-none", "py313-none", "cp313-cp313", "cp313-none"]


# ---------------------------------------------------------------- synthetic data

def flip(data):
    """Same length, different bytes (a zip's last byte is usually 0, so do not just set it)."""
    return data[:-1] + bytes([data[-1] ^ 1])


def make_wheel(directory, name="wfcdemo", version="1.0", requires=(), tag="py3-none-any",
               members=(), meta_version=None, wheel_tag=None):
    filename = f"{name}-{version}-{tag}.whl"
    dist_info = f"{name}-{version}.dist-info"
    meta = f"Metadata-Version: 2.1\nName: {name}\nVersion: {meta_version or version}\nRequires-Python: >=3.8\n"
    meta += "".join(f"Requires-Dist: {r}\n" for r in requires)
    py, abi, plat = tag.split("-")
    wheel = f"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: {wheel_tag or f'{py}-{abi}-{plat}'}\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{name}/__init__.py", "VALUE = 1\n")
        archive.writestr(f"{dist_info}/METADATA", meta)
        archive.writestr(f"{dist_info}/WHEEL", wheel)
        archive.writestr(f"{dist_info}/RECORD", "")
        for member in members:
            archive.writestr(member, "x")
    data = buffer.getvalue()
    if directory is not None:
        (Path(directory) / filename).write_bytes(data)
    return {
        "filename": filename, "name": name, "version": version, "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(), "requires_python": ">=3.8",
        "requires_dist": list(requires),
        "artifact_url": f"https://files.pythonhosted.org/packages/aa/bb/{filename}",
        "metadata_url": f"https://pypi.org/pypi/{name}/{version}/json",
    }, data


def make_profile(entries, host=None, profile_id="synthetic"):
    return {
        "format_version": 1, "profile_id": profile_id, "host": host or dict(FAKE_HOST),
        "compatible_tags": list(TAGS), "wheels": list(entries),
    }


def real_host():
    probe = tool.probe_host()
    host = {
        "implementation": probe["implementation"], "python": probe["python"], "machine": probe["machine"],
        "system": probe["system"], "soabi": probe["soabi"],
        "min_glibc": f"{probe['glibc'][0]}.{probe['glibc'][1]}", "seed_pip_version": probe["seed_pip_version"],
    }
    return host


@pytest.fixture
def bundle(tmp_path):
    directory = tmp_path / "bundle"
    directory.mkdir(mode=0o755)
    entry, data = make_wheel(directory)
    entry_b, _ = make_wheel(directory, name="wfcother", version="2.1", requires=["wfcdemo>=1"])
    profile = make_profile([entry, entry_b])
    os.chmod(directory, 0o755)
    return profile, directory


def problems(profile, directory, probe=PROBE):
    with pytest.raises(tool.InputsError) as info:
        tool.verify_bundle(profile, directory, probe)
    return "\n".join(info.value.problems)


# ------------------------------------------------- real profile and P1 closure

def test_real_profile_pins_equal_the_p1_candidate_inventory():
    profile = tool.load_profile(REAL_PROFILE_ID)
    candidate_file = PROFILE_DIR / "py313-linux-x86_64-candidate.json"
    candidate = json.loads(candidate_file.read_text())
    assert profile["derived_from"]["sha256"] == hashlib.sha256(candidate_file.read_bytes()).hexdigest()
    assert profile["derived_from"]["local_manifest_sha256"] == candidate["local_manifest_sha256"]
    mapped = [
        {**{k: w[k] for k in ("filename", "name", "version", "size", "sha256", "requires_python", "requires_dist")},
         "artifact_url": w["pypi_artifact_url"], "metadata_url": w["pypi_metadata_url"]}
        for w in candidate["wheels"]
    ]
    assert profile["wheels"] == mapped and len(mapped) == 12
    assert all(w["pypi_sha256_match"] for w in candidate["wheels"])
    assert profile["host"]["machine"] == "x86_64" and profile["host"]["python"] == "3.13"


def _pinned(profile):
    return {canonicalize_name(w["name"]): Version(w["version"]) for w in profile["wheels"]}


def test_real_profile_closure_covers_pyproject_and_every_applicable_marker():
    profile = tool.load_profile(REAL_PROFILE_ID)
    project = tomllib.loads((REPO / "pyproject.toml").read_text())
    declared = {
        "build_requires": project["build-system"]["requires"],
        "dependencies": project["project"]["dependencies"],
        "dev": project["project"]["optional-dependencies"]["dev"],
    }
    recorded = {k: profile["pyproject"][k] for k in declared}
    assert recorded == declared, "pyproject dependency metadata changed: re-review the pinned closure"
    pins = _pinned(profile)
    environment = {
        "python_version": "3.13", "python_full_version": "3.13.5", "os_name": "posix", "sys_platform": "linux",
        "platform_system": "Linux", "platform_machine": "x86_64", "implementation_name": "cpython",
        "platform_python_implementation": "CPython", "extra": "",
    }
    reached, queue = set(), [Requirement(r) for group in declared.values() for r in group]
    while queue:
        requirement = queue.pop()
        name = canonicalize_name(requirement.name)
        assert name in pins, f"{requirement} has no pinned wheel"
        assert requirement.specifier.contains(pins[name], prereleases=True), f"{requirement} vs {pins[name]}"
        if name in reached:
            continue
        reached.add(name)
        wheel = next(w for w in profile["wheels"] if canonicalize_name(w["name"]) == name)
        for text in wheel["requires_dist"]:
            child = Requirement(text)
            if child.marker is None or child.marker.evaluate(environment):
                queue.append(child)
    assert reached == set(pins), f"pinned but unreachable: {set(pins) - reached}"


# ------------------------------------------------------------- profile loading

@pytest.mark.parametrize("profile_id", ["../cp313", "", "UPPER", "a/b", "x" * 80])
def test_profile_id_cannot_escape_the_profile_directory(profile_id):
    with pytest.raises(tool.InputsError):
        tool.load_profile(profile_id)


def test_missing_profile_is_a_clear_error(tmp_path):
    with pytest.raises(tool.InputsError, match="not found"):
        tool.load_profile("absent", tmp_path)


@pytest.mark.parametrize("mutate, message", [
    (lambda e: e.update(artifact_url="http://files.pythonhosted.org/x/" + e["filename"]), "https"),
    (lambda e: e.update(artifact_url="https://evil.example/x/" + e["filename"]), "https"),
    (lambda e: e.update(filename="../evil-1.0-py3-none-any.whl"), "unsafe"),
    (lambda e: e.update(sha256="ABC"), "sha256"),
    (lambda e: e.update(size=0), "size"),
])
def test_profile_validation_rejects_unsafe_entries(tmp_path, mutate, message):
    entry, _ = make_wheel(None)
    mutate(entry)
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps(make_profile([entry])))
    with pytest.raises(tool.InputsError, match=message):
        tool.load_profile("synthetic", tmp_path)


def test_profile_rejects_duplicate_wheels_and_wrong_id(tmp_path):
    entry, _ = make_wheel(None)
    (tmp_path / "synthetic.json").write_text(json.dumps(make_profile([entry, entry])))
    with pytest.raises(tool.InputsError, match="duplicate"):
        tool.load_profile("synthetic", tmp_path)
    (tmp_path / "synthetic.json").write_text(json.dumps(make_profile([entry], profile_id="other")))
    with pytest.raises(tool.InputsError, match="does not match"):
        tool.load_profile("synthetic", tmp_path)


# -------------------------------------------------------------------- verify

def test_verify_accepts_exactly_the_pinned_bundle(bundle):
    profile, directory = bundle
    assert len(tool.verify_bundle(profile, directory, PROBE)) == 2


def test_verify_rejects_missing_extra_and_nested_entries(bundle):
    profile, directory = bundle
    victim = directory / profile["wheels"][0]["filename"]
    victim.unlink()
    (directory / "notes.txt").write_text("x")
    (directory / ".hidden").write_text("x")
    (directory / "sub").mkdir()
    text = problems(profile, directory)
    assert f"missing: {profile['wheels'][0]['filename']}" in text
    assert "notes.txt" in text and ".hidden" in text and "sub" in text


def test_verify_rejects_same_size_corruption_truncation_and_growth(bundle):
    profile, directory = bundle
    first, second = (directory / w["filename"] for w in profile["wheels"])
    data = first.read_bytes()
    first.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    assert "SHA-256 differs" in problems(profile, directory)
    first.write_bytes(data)
    first.write_bytes(data[:-5])
    assert "size" in problems(profile, directory)
    first.write_bytes(data + b"x")
    assert "exceeds the pinned size" in problems(profile, directory)
    first.write_bytes(data)
    tool.verify_bundle(profile, directory, PROBE)
    assert second.exists()


def test_verify_rejects_symlink_fifo_world_writable_and_symlinked_directory(bundle, tmp_path):
    profile, directory = bundle
    name = profile["wheels"][0]["filename"]
    original = (directory / name).read_bytes()
    elsewhere = tmp_path / "elsewhere.whl"
    elsewhere.write_bytes(original)
    (directory / name).unlink()
    (directory / name).symlink_to(elsewhere)
    assert "without following links" in problems(profile, directory)
    (directory / name).unlink()
    os.mkfifo(directory / name)
    assert "not a regular file" in problems(profile, directory)
    (directory / name).unlink()
    (directory / name).write_bytes(original)
    os.chmod(directory / name, 0o666)
    assert "world-writable" in problems(profile, directory)
    os.chmod(directory / name, 0o644)
    os.chmod(directory, 0o777)
    assert "world-writable" in problems(profile, directory)
    os.chmod(directory, 0o755)
    link = tmp_path / "link"
    link.symlink_to(directory)
    assert "must not be a symlink" in problems(profile, link)
    tool.verify_bundle(profile, directory, PROBE)


def test_verify_rejects_a_missing_directory(tmp_path):
    entry, _ = make_wheel(None)
    assert "cannot open" in problems(make_profile([entry]), tmp_path / "absent")


@pytest.mark.parametrize("kwargs, message", [
    ({"meta_version": "9.9"}, "name/version"),
    ({"requires": ["surprise>=1"]}, "Requires-Dist"),
    ({"members": ["../escape.py"]}, "unsafe archive member"),
    ({"members": ["/abs.py"]}, "unsafe archive member"),
    ({"wheel_tag": "py2-none-any"}, "WHEEL tags"),
])
def test_verify_checks_embedded_metadata_and_archive_members(tmp_path, kwargs, message):
    directory = tmp_path / "b"
    directory.mkdir()
    pinned, _ = make_wheel(None)
    bad, _ = make_wheel(directory, **kwargs)
    pinned.update(sha256=bad["sha256"], size=bad["size"])  # bytes match the pin; content does not
    assert message in problems(make_profile([pinned]), directory)


def test_verify_rejects_non_wheel_bytes_with_a_matching_pin(tmp_path):
    directory = tmp_path / "b"
    directory.mkdir()
    entry, _ = make_wheel(None)
    data = b"not a zip archive"
    (directory / entry["filename"]).write_bytes(data)
    entry.update(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
    assert "not a valid wheel" in problems(make_profile([entry]), directory)


# ----------------------------------------------------------------- host / tags

@pytest.mark.parametrize("change", [
    {"python": "3.12"}, {"implementation": "pypy"}, {"machine": "aarch64"}, {"system": "darwin"},
    {"soabi": "cpython-312"}, {"seed_pip_version": "24.0"}, {"glibc": (2, 17)}, {"glibc": None},
])
def test_host_mismatch_is_refused_with_guidance(bundle, change):
    profile, directory = bundle
    assert "only the stated host" in problems(profile, directory, {**PROBE, **change})


@pytest.mark.parametrize("tag, glibc, accepted", [
    ("py3-none-any", (2, 28), True),
    ("cp313-cp313-manylinux_2_28_x86_64", (2, 28), True),
    ("cp313-cp313-manylinux_2_28_x86_64", (2, 27), False),
    ("cp313-cp313-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64", (2, 28), True),
    ("cp313-cp313-manylinux2014_x86_64", (2, 28), True),
    ("py3-none-manylinux_2_17_aarch64", (2, 28), False),
    ("cp313-cp313-musllinux_1_2_x86_64", (2, 28), False),
    ("cp312-cp312-manylinux_2_17_x86_64", (2, 28), False),
    ("cp313-abi3-manylinux_2_17_x86_64", (2, 28), False),
    ("py3-none-win_amd64", (2, 28), False),
    ("py3-none-linux_x86_64", (2, 28), True),
])
def test_wheel_tag_compatibility(tag, glibc, accepted):
    profile = make_profile([])
    filename = f"pkg-1.0-{tag}.whl"
    probe = {**PROBE, "glibc": glibc}
    if accepted:
        tool.check_wheel_compatible(filename, profile, probe)
    else:
        with pytest.raises(tool.InputsError, match="not accepted"):
            tool.check_wheel_compatible(filename, profile, probe)


def test_incompatible_wheel_tag_in_a_bundle_is_refused(tmp_path):
    directory = tmp_path / "b"
    directory.mkdir()
    entry, _ = make_wheel(directory, tag="cp312-cp312-manylinux_2_17_x86_64")
    assert "not accepted" in problems(make_profile([entry]), directory)


# ----------------------------------------------------------------------- fetch

def test_fetch_from_transferred_directory_creates_a_verified_bundle(bundle, tmp_path):
    profile, source = bundle
    (source / "MANIFEST.sha256").write_text("not part of the profile")
    os.chmod(source / "MANIFEST.sha256", 0o644)
    target = tmp_path / "new-bundle"
    notes = tool.fetch_bundle(profile, target, from_dir=source, probe=PROBE)
    assert any("MANIFEST.sha256" in n for n in notes)
    assert sorted(os.listdir(target)) == sorted(w["filename"] for w in profile["wheels"])
    tool.verify_bundle(profile, target, PROBE)
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]
    with pytest.raises(tool.InputsError, match="already exists"):
        tool.fetch_bundle(profile, target, from_dir=source, probe=PROBE)


@pytest.mark.parametrize("damage", ["missing", "corrupt", "symlink"])
def test_fetch_from_directory_leaves_nothing_on_any_bad_input(bundle, tmp_path, damage):
    profile, source = bundle
    victim = source / profile["wheels"][1]["filename"]
    data = victim.read_bytes()
    victim.unlink()
    if damage == "corrupt":
        victim.write_bytes(flip(data))
    elif damage == "symlink":
        (tmp_path / "real.whl").write_bytes(data)
        victim.symlink_to(tmp_path / "real.whl")
    target = tmp_path / "new-bundle"
    with pytest.raises(tool.InputsError):
        tool.fetch_bundle(profile, target, from_dir=source, probe=PROBE)
    assert not target.exists()
    assert sorted(p.name for p in tmp_path.iterdir() if p.name not in ("real.whl", "bundle")) == []


def test_fetch_refuses_a_missing_parent_directory(bundle, tmp_path):
    profile, source = bundle
    with pytest.raises(tool.InputsError, match="parent directory"):
        tool.fetch_bundle(profile, tmp_path / "no" / "bundle", from_dir=source, probe=PROBE)


class FakeResponse:
    def __init__(self, data, url):
        self._data, self._url = data, url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, limit=-1):
        return self._data if limit < 0 else self._data[:limit]

    def geturl(self):
        return self._url


class FakeOpener:
    def __init__(self, payloads, final_url=None):
        self.payloads, self.final_url, self.requested = payloads, final_url, []

    def open(self, url, timeout=None):
        self.requested.append(url)
        return FakeResponse(self.payloads[url.rsplit("/", 1)[1]], self.final_url or url)


def test_fetch_downloads_only_the_pinned_https_urls(tmp_path):
    first, data_a = make_wheel(None, name="wfcone")
    second, data_b = make_wheel(None, name="wfctwo")
    profile = make_profile([first, second])
    opener = FakeOpener({first["filename"]: data_a, second["filename"]: data_b})
    tool.fetch_bundle(profile, tmp_path / "b", opener=opener, probe=PROBE)
    assert opener.requested == [first["artifact_url"], second["artifact_url"]]
    tool.verify_bundle(profile, tmp_path / "b", PROBE)


@pytest.mark.parametrize("mode", ["wrong-bytes", "oversize", "foreign-final-url", "transport-error"])
def test_fetch_download_failures_leave_nothing(tmp_path, mode):
    entry, data = make_wheel(None, name="wfcone")
    profile = make_profile([entry])
    served = {"wrong-bytes": flip(data), "oversize": data + b"extra"}.get(mode, data)
    final = "https://evil.example/" + entry["filename"] if mode == "foreign-final-url" else None
    opener = FakeOpener({entry["filename"]: served}, final)
    if mode == "transport-error":
        opener.open = lambda url, timeout=None: (_ for _ in ()).throw(OSError("network unreachable"))
    with pytest.raises(tool.InputsError):
        tool.fetch_bundle(profile, tmp_path / "b", opener=opener, probe=PROBE)
    assert list(tmp_path.iterdir()) == []


def test_redirects_are_limited_to_the_pinned_artifact_host():
    handler = tool._StrictRedirects()
    request = urllib.request.Request("https://files.pythonhosted.org/a.whl")
    for url in ("https://evil.example/a.whl", "http://files.pythonhosted.org/a.whl"):
        with pytest.raises(tool.InputsError, match="refusing redirect"):
            handler.redirect_request(request, None, 302, "Found", Message(), url)
    assert handler.redirect_request(request, None, 302, "Found", Message(), "https://files.pythonhosted.org/b.whl")


# ----------------------------------------------------------------- environment

def test_offline_environment_discards_hostile_pip_state_and_keeps_the_rest(tmp_path):
    hostile = {
        "PIP_INDEX_URL": "http://127.0.0.1:9/", "PIP_EXTRA_INDEX_URL": "http://x", "PIP_CONFIG_FILE": "/evil",
        "PIP_FIND_LINKS": "/elsewhere", "PIP_REQUIRE_VIRTUALENV": "0", "PIP_USER": "1", "PIP_TARGET": "/t",
        "PIP_CONSTRAINT": "/c", "PIP_TRUSTED_HOST": "x", "PIP_PROXY": "http://p", "PIP_NO_BUILD_ISOLATION": "1",
        "pip_cache_dir": "/lower", "PIP_NO_INDEX": "0", "PIP_NO_CACHE_DIR": "0",
    }
    unrelated = {"PATH": "/bin", "HOME": "/h", "HTTPS_PROXY": "http://proxy", "TMPDIR": "/t", "LANG": "C", "PIPELINE": "1"}
    env = tool.offline_environment({**hostile, **unrelated}, Path("rel/dir"), "prof")
    assert {k: v for k, v in env.items() if k in unrelated} == unrelated
    assert sorted(k for k in env if k.upper().startswith("PIP_")) == [
        "PIP_CONFIG_FILE", "PIP_DISABLE_PIP_VERSION_CHECK", "PIP_FIND_LINKS", "PIP_NO_CACHE_DIR", "PIP_NO_INDEX",
        "PIP_NO_INPUT",
    ]
    assert env["PIP_CONFIG_FILE"] == os.devnull and env["PIP_NO_INDEX"] == "1" and env["PIP_NO_CACHE_DIR"] == "1"
    assert Path(env["PIP_FIND_LINKS"]).is_absolute() and env["PIP_FIND_LINKS"].endswith("rel/dir")
    assert env["WFC_TEST_INPUTS"] == env["PIP_FIND_LINKS"] and env["WFC_TEST_PROFILE"] == "prof"
    assert "PIP_USER" in hostile  # the input mapping is never mutated
    assert tool.sanitized_pip_settings()["PIP_NO_INPUT"] == "1"


# ------------------------------------------------------------------------ exec

@pytest.fixture
def host_bundle(tmp_path):
    """Synthetic bundle/profile whose host section describes the running host."""
    directory = tmp_path / "bundle"
    directory.mkdir(mode=0o755)
    entry, _ = make_wheel(directory)
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    profile = make_profile([entry], host=real_host(), profile_id="synthetic-host")
    (profile_dir / "synthetic-host.json").write_text(json.dumps(profile))
    return profile, directory, profile_dir


def run_tool(profile_dir, command, bundle_dir, *argv, env=None):
    return subprocess.run(
        [sys.executable, "-I", str(TOOL_PATH), "--profile-dir", str(profile_dir), command,
         "--profile", "synthetic-host", "--bundle", str(bundle_dir), *argv],
        text=True, capture_output=True, env=env or os.environ.copy(),
    )


def test_cli_verify_and_exec_run_a_literal_argv_without_a_shell(host_bundle, tmp_path):
    _, directory, profile_dir = host_bundle
    assert run_tool(profile_dir, "verify", directory).returncode == 0
    marker = tmp_path / "pwned"
    payload = f"$(touch {marker}); `touch {marker}` && touch {marker}"
    probe = "import json,os,sys;print(json.dumps([sys.argv[1:],os.environ['WFC_TEST_INPUTS']]))"
    result = run_tool(profile_dir, "exec", directory, "--", sys.executable, "-c", probe, payload, "a b")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [[payload, "a b"], str(directory)]
    assert not marker.exists()


def test_exec_verifies_before_running_and_never_runs_on_corruption(host_bundle, tmp_path):
    profile, directory, profile_dir = host_bundle
    marker = tmp_path / "ran"
    wheel = directory / profile["wheels"][0]["filename"]
    wheel.write_bytes(flip(wheel.read_bytes()))
    result = run_tool(profile_dir, "exec", directory, "--", sys.executable, "-c", f"open({str(marker)!r}, 'w')")
    assert result.returncode == tool.TOOL_FAILURE and "SHA-256 differs" in result.stderr
    assert not marker.exists()
    assert run_tool(profile_dir, "verify", directory).returncode == 1


def test_exec_reverifies_after_the_command_and_passes_child_codes(host_bundle):
    _, directory, profile_dir = host_bundle
    assert run_tool(profile_dir, "exec", directory, "--", sys.executable, "-c", "raise SystemExit(7)").returncode == 7
    tamper = f"open({str(directory / 'stray.whl')!r}, 'w').write('x')"
    result = run_tool(profile_dir, "exec", directory, "--", sys.executable, "-c", tamper)
    assert result.returncode == tool.TOOL_FAILURE
    assert "changed or became invalid" in result.stderr and "stray.whl" in result.stderr


def test_exec_unknown_command_reports_127(host_bundle):
    _, directory, profile_dir = host_bundle
    result = run_tool(profile_dir, "exec", directory, "--", "wfc-no-such-command-xyz")
    assert result.returncode == 127 and "cannot execute" in result.stderr


def test_exec_without_a_command_is_a_usage_error(host_bundle):
    _, directory, profile_dir = host_bundle
    assert run_tool(profile_dir, "exec", directory).returncode == 2


def test_cli_fetch_from_dir_and_refusals(host_bundle, tmp_path):
    _, directory, profile_dir = host_bundle
    target = tmp_path / "copy"
    result = run_tool(profile_dir, "fetch", target, "--from-dir", str(directory))
    assert result.returncode == 0 and "fetched and verified 1 wheels" in result.stdout
    assert run_tool(profile_dir, "verify", target).returncode == 0
    again = run_tool(profile_dir, "fetch", target, "--from-dir", str(directory))
    assert again.returncode == 1 and "already exists" in again.stderr


# ------------------------------------------------ hostile pip configuration

@pytest.fixture(scope="module")
def pip_venv(tmp_path_factory):
    path = tmp_path_factory.mktemp("pip-venv") / "venv"
    # ensurepip supplies the seed pip from the interpreter distribution; no index is involved.
    subprocess.run([sys.executable, "-m", "venv", str(path)], check=True, capture_output=True, text=True)
    return path


def hostile_environment(tmp_path):
    home = tmp_path / "home"
    (home / ".config" / "pip").mkdir(parents=True)
    (home / ".pip").mkdir()
    conf = "[global]\nindex-url = http://127.0.0.1:9/simple\nextra-index-url = http://127.0.0.1:9/extra\n"
    conf += "require-virtualenv = false\nno-index = false\nprefix = /nonexistent-prefix\n"
    for location in (home / ".config" / "pip" / "pip.conf", home / ".pip" / "pip.conf", tmp_path / "evil.conf"):
        location.write_text(conf)
    env = os.environ.copy()
    env.update({
        "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"), "PIP_CONFIG_FILE": str(tmp_path / "evil.conf"),
        "PIP_INDEX_URL": "http://127.0.0.1:9/simple", "PIP_EXTRA_INDEX_URL": "http://127.0.0.1:9/extra",
        "PIP_FIND_LINKS": "/nonexistent-links", "PIP_NO_INDEX": "0", "PIP_USER": "1", "PIP_TARGET": str(tmp_path / "t"),
        "PIP_REQUIRE_VIRTUALENV": "0", "PIP_PROXY": "http://127.0.0.1:9", "PIP_TRUSTED_HOST": "127.0.0.1",
        "PIP_CONSTRAINT": "/nonexistent-constraints", "PIP_NO_BUILD_ISOLATION": "1", "PIP_CACHE_DIR": str(tmp_path / "c"),
    })
    return env


def test_hostile_ambient_pip_configuration_cannot_reach_an_index_or_other_inputs(host_bundle, pip_venv, tmp_path):
    profile, directory, profile_dir = host_bundle
    env = hostile_environment(tmp_path)
    python = str(pip_venv / "bin" / "python")
    config = run_tool(profile_dir, "exec", directory, "--", python, "-m", "pip", "config", "list", env=env)
    assert config.returncode == 0, config.stderr
    effective = sorted(line.split("=", 1)[0] for line in config.stdout.splitlines())
    assert effective == sorted(f":env:.{k}" for k in (
        "config-file", "disable-pip-version-check", "find-links", "no-cache-dir", "no-index", "no-input"))
    assert f"find-links='{directory}'" in config.stdout and "127.0.0.1" not in config.stdout
    install = run_tool(profile_dir, "exec", directory, "--", python, "-m", "pip", "install", "wfcdemo", env=env)
    assert install.returncode == 0, install.stdout + install.stderr
    probe = subprocess.run([python, "-c", "import wfcdemo;print(wfcdemo.VALUE)"], capture_output=True, text=True)
    assert probe.stdout.strip() == "1"
    assert not (tmp_path / "t").exists() and not (tmp_path / "c").exists()


def test_missing_distribution_fails_offline_without_index_or_cache_fallback(host_bundle, pip_venv, tmp_path):
    _, directory, profile_dir = host_bundle
    env = hostile_environment(tmp_path)
    python = str(pip_venv / "bin" / "python")
    result = run_tool(profile_dir, "exec", directory, "--", python, "-m", "pip", "install", "wfc-not-in-the-bundle", env=env)
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "No matching distribution" in output and "127.0.0.1" not in output and "Connection" not in output


# ---------------------------------------------------- pytest configuration gate

def make_project(tmp_path):
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    (project / "tests").mkdir()
    shutil.copy(TOOL_PATH, project / "scripts" / "wfc_test_inputs.py")
    shutil.copy(REPO / "tests" / "conftest.py", project / "tests" / "conftest.py")
    # Own ini file: otherwise this repository's pyproject (addopts -q) is discovered upward.
    (project / "pytest.ini").write_text("[pytest]\n")
    profiles = project / "tests" / "fixtures" / "offline-test-inputs"
    profiles.mkdir(parents=True)
    directory = tmp_path / "bundle"
    directory.mkdir(mode=0o755)
    entry, _ = make_wheel(directory)
    (profiles / "synthetic-host.json").write_text(
        json.dumps(make_profile([entry], host=real_host(), profile_id="synthetic-host"))
    )
    (project / "tests" / "test_probe.py").write_text(
        "import json, os\n"
        "def test_probe():\n"
        "    keys = [k for k in os.environ if k.upper().startswith(('PIP_', 'WFC_TEST'))]\n"
        "    open(os.environ['PROBE_OUT'], 'w').write(json.dumps({k: os.environ[k] for k in keys}))\n"
        "    if os.environ.get('PROBE_TAMPER'):\n"
        "        open(os.path.join(os.environ['PROBE_TAMPER'], 'stray.whl'), 'w').write('x')\n"
    )
    return project, directory


def pytest_run(project, tmp_path, *args, env_updates=None, cwd=None):
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("PIP_", "WFC_TEST", "PYTEST_")) and k not in ("TMPDIR", "TMP", "TEMP")}
    env["PROBE_OUT"] = str(tmp_path / "probe.json")
    env.update(env_updates or {})
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", f"--basetemp={tmp_path / 'bt'}",
         "--rootdir", str(project), *args],
        cwd=cwd or project, env=env, text=True, capture_output=True,
    )
    return result, result.stdout + result.stderr


def selected(directory, **extra):
    return {"WFC_TEST_INPUTS": str(directory), "WFC_TEST_PROFILE": "synthetic-host", **extra}


def test_required_run_without_or_with_half_configured_inputs_is_a_clear_error(tmp_path):
    project, directory = make_project(tmp_path)
    for updates in ({}, {"WFC_TEST_INPUTS": str(directory)}, {"WFC_TEST_PROFILE": "synthetic-host"}):
        result, output = pytest_run(project, tmp_path, "tests", env_updates=updates)
        assert result.returncode == 4, output
        assert "WFC_TEST_INPUTS and WFC_TEST_PROFILE must both be set" in output
        assert "passed" not in output and "skipped" not in output and not (tmp_path / "probe.json").exists()


@pytest.mark.parametrize("damage, expected", [
    ("corrupt", "SHA-256 differs"), ("missing", "missing:"), ("extra", "unexpected entry"),
])
def test_required_run_with_bad_inputs_fails_before_any_test(tmp_path, damage, expected):
    project, directory = make_project(tmp_path)
    wheel = next(directory.iterdir())
    if damage == "corrupt":
        wheel.write_bytes(flip(wheel.read_bytes()))
    elif damage == "missing":
        wheel.unlink()
    else:
        (directory / "extra.whl").write_text("x")
    result, output = pytest_run(project, tmp_path, "tests", env_updates=selected(directory))
    assert result.returncode == 4 and expected in output and "are not usable" in output
    assert not (tmp_path / "probe.json").exists()


def test_unknown_profile_is_refused(tmp_path):
    project, directory = make_project(tmp_path)
    result, output = pytest_run(project, tmp_path, "tests", env_updates={**selected(directory), "WFC_TEST_PROFILE": "nope"})
    assert result.returncode == 4 and "not found" in output


def test_required_run_applies_sanitized_offline_configuration_to_the_process_tree(tmp_path):
    project, directory = make_project(tmp_path)
    hostile = {"PIP_INDEX_URL": "http://127.0.0.1:9/", "PIP_FIND_LINKS": "/elsewhere", "PIP_NO_INDEX": "0",
               "PIP_USER": "1", "PIP_CONFIG_FILE": "/evil"}
    result, output = pytest_run(project, tmp_path, "tests", env_updates=selected(directory, **hostile))
    assert result.returncode == 1 and "REQUIRED REAL TESTS NOT SELECTED AND PASSED" in output
    assert f"offline inputs: profile synthetic-host, 1 wheels verified in {directory}" in output
    seen = json.loads((tmp_path / "probe.json").read_text())
    assert seen == {
        "PIP_CONFIG_FILE": os.devnull, "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_FIND_LINKS": str(directory),
        "PIP_NO_CACHE_DIR": "1", "PIP_NO_INDEX": "1", "PIP_NO_INPUT": "1",
        "WFC_TEST_INPUTS": str(directory), "WFC_TEST_PROFILE": "synthetic-host",
    }


def test_relative_input_path_is_made_absolute_for_nested_processes(tmp_path):
    project, directory = make_project(tmp_path)
    relative = os.path.relpath(directory, project)
    result, output = pytest_run(project, tmp_path, "tests", env_updates=selected(relative))
    assert result.returncode == 1 and "REQUIRED REAL TESTS NOT SELECTED AND PASSED" in output
    assert json.loads((tmp_path / "probe.json").read_text())["WFC_TEST_INPUTS"] == str(directory)


def test_inputs_modified_during_the_run_fail_an_otherwise_passing_run(tmp_path):
    project, directory = make_project(tmp_path)
    result, output = pytest_run(project, tmp_path, "tests", env_updates=selected(directory, PROBE_TAMPER=str(directory)))
    assert result.returncode == 1 and "CHANGED OR INVALID DURING THE RUN" in output and "stray.whl" in output


def test_collect_only_is_inert_and_never_reports_verified_inputs(tmp_path):
    project, _ = make_project(tmp_path)
    result, output = pytest_run(project, tmp_path, "--collect-only", "tests")
    assert result.returncode == 0 and "not verified" in output


def test_partial_mode_is_explicit_labelled_and_fails_closed_offline(tmp_path):
    project, _ = make_project(tmp_path)
    result, output = pytest_run(project, tmp_path, "--wfc-partial", "tests/test_probe.py")
    assert result.returncode == 0, output
    assert "WFC PARTIAL RUN" in output and "NOT the required suite" in output
    seen = json.loads((tmp_path / "probe.json").read_text())
    assert seen["PIP_NO_INDEX"] == "1" and seen["PIP_CONFIG_FILE"] == os.devnull and "PIP_FIND_LINKS" not in seen


def test_partial_mode_with_supplied_inputs_still_verifies_them(tmp_path):
    project, directory = make_project(tmp_path)
    result, output = pytest_run(project, tmp_path, "--wfc-partial", "tests/test_probe.py", env_updates=selected(directory))
    assert result.returncode == 0 and "wheels verified" in output and "PARTIAL RUN" in output
    next(directory.iterdir()).write_bytes(b"corrupt")
    result, output = pytest_run(project, tmp_path, "--wfc-partial", "tests/test_probe.py", env_updates=selected(directory))
    assert result.returncode == 4 and "are not usable" in output


def test_partial_mode_cannot_stand_in_for_the_whole_suite(tmp_path):
    project, directory = make_project(tmp_path)
    for args in (("--wfc-partial",), ("--wfc-partial", "tests"), ("--wfc-partial", ".")):
        result, output = pytest_run(project, tmp_path, *args)
        assert result.returncode == 4 and "not the whole suite" in output, (args, output)
    # Only the literal command line selects partial mode: environment/ini additions cannot.
    result, output = pytest_run(project, tmp_path, "tests/test_probe.py", env_updates={"PYTEST_ADDOPTS": "--wfc-partial"})
    assert result.returncode == 4 and "directly on the command line" in output
    assert "PARTIAL RUN" not in output


# ----------------------------------------------- the running (real) suite itself

def test_this_process_tree_is_offline_with_the_verified_inputs(request):
    partial = request.config.option.wfc_partial
    expected = {"PIP_CONFIG_FILE", "PIP_DISABLE_PIP_VERSION_CHECK", "PIP_NO_CACHE_DIR", "PIP_NO_INDEX", "PIP_NO_INPUT"}
    if not (partial and not os.environ.get("WFC_TEST_INPUTS")):
        expected |= {"PIP_FIND_LINKS"}
    assert {k for k in os.environ if k.upper().startswith("PIP_")} == expected
    assert os.environ["PIP_NO_INDEX"] == "1" and os.environ["PIP_NO_CACHE_DIR"] == "1"
    assert os.environ["PIP_CONFIG_FILE"] == os.devnull
    if "PIP_FIND_LINKS" in expected:
        bundle = Path(os.environ["PIP_FIND_LINKS"])
        assert bundle.is_absolute() and os.environ["WFC_TEST_INPUTS"] == str(bundle)
        # The conftest gate verified this directory against the repository profile; re-check here.
        profile = tool.load_profile(os.environ["WFC_TEST_PROFILE"])
        assert len(tool.verify_bundle(profile, bundle)) == len(profile["wheels"])
        result = subprocess.run(
            [sys.executable, "-I", str(TOOL_PATH), "verify", "--profile", profile["profile_id"], "--bundle", str(bundle)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr


def test_bundle_directory_modes_are_not_loosened_by_verification(bundle):
    profile, directory = bundle
    before = {p.name: stat.S_IMODE(p.stat().st_mode) for p in directory.iterdir()}
    tool.verify_bundle(profile, directory, PROBE)
    assert {p.name: stat.S_IMODE(p.stat().st_mode) for p in directory.iterdir()} == before

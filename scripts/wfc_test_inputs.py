#!/usr/bin/env python3
"""Verified offline third-party inputs for WFC's own development tests.

Development tooling only.  It is not part of the installed package, a
consumer prerequisite, a dependency service or a package manager.  The
standard library is enough for every operation.

Operations (nothing runs implicitly, nothing refreshes in the background):

  fetch   Create a NEW bundle directory holding exactly the wheels pinned by a
          repository profile, either downloaded over HTTPS from the pinned
          official URLs (explicit network operation) or copied from a
          transferred directory (``--from-dir``).  Every byte is checked.
  verify  Check an existing bundle directory against the profile and the host.
  exec    Verify, then run a literal argv (no shell) with a sanitized,
          deterministic offline pip environment, then verify again.

A profile is ``tests/fixtures/offline-test-inputs/<profile-id>.json``.  A bundle
directory is trusted only because its contents match the repository profile,
never because of where it is stored.
"""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import io
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent
PROFILE_DIR = REPOSITORY / "tests" / "fixtures" / "offline-test-inputs"
PROFILE_FORMAT_VERSION = 1
ARTIFACT_HOSTS = frozenset({"files.pythonhosted.org"})
TOOL_FAILURE = 125  # exec: the tool, not the child, failed (cf. env(1), timeout(1))

_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+]*-[A-Za-z0-9_.+!]+(-[0-9][A-Za-z0-9_.]*)?-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+\.whl$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class InputsError(Exception):
    """Inputs are missing, incompatible, corrupt or unsafe.  ``problems`` lists each."""

    def __init__(self, *problems: str):
        super().__init__("; ".join(problems))
        self.problems = list(problems)


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# ------------------------------------------------------------------ profile

def load_profile(profile_id: str, profile_dir: Path | None = None) -> dict:
    if not isinstance(profile_id, str) or not _PROFILE_ID.match(profile_id):
        raise InputsError(f"invalid profile id {profile_id!r}: expected lowercase letters, digits, '.', '_' or '-'")
    path = (profile_dir or PROFILE_DIR) / f"{profile_id}.json"
    try:
        if path.is_symlink() or not path.is_file():
            raise InputsError(f"profile {profile_id!r} not found as a regular file at {path}")
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InputsError(f"profile {profile_id!r} unreadable: {exc}") from exc
    _validate_profile(profile, profile_id)
    return profile


def _validate_profile(profile: object, profile_id: str) -> None:
    def bad(msg: str):
        raise InputsError(f"profile {profile_id!r} invalid: {msg}")

    if not isinstance(profile, dict):
        bad("not an object")
    if profile.get("format_version") != PROFILE_FORMAT_VERSION:
        bad(f"format_version must be {PROFILE_FORMAT_VERSION}")
    if profile.get("profile_id") != profile_id:
        bad("profile_id does not match its file name")
    host = profile.get("host")
    if not isinstance(host, dict) or not all(
        isinstance(host.get(k), str) and host[k]
        for k in ("implementation", "python", "machine", "system", "soabi", "min_glibc", "seed_pip_version")
    ):
        bad("host section incomplete")
    if not re.fullmatch(r"\d+\.\d+", host["min_glibc"]) or not re.fullmatch(r"\d+\.\d+", host["python"]):
        bad("host python/min_glibc must be MAJOR.MINOR")
    pairs = profile.get("compatible_tags")
    if not isinstance(pairs, list) or not pairs or not all(
        isinstance(p, str) and re.fullmatch(r"[A-Za-z0-9_]+-[A-Za-z0-9_]+", p) for p in pairs
    ):
        bad("compatible_tags must be a non-empty list of 'pythontag-abitag'")
    wheels = profile.get("wheels")
    if not isinstance(wheels, list) or not wheels:
        bad("wheels must be a non-empty list")
    seen: set[str] = set()
    for w in wheels:
        if not isinstance(w, dict):
            bad("wheel entry is not an object")
        fn = w.get("filename")
        if not isinstance(fn, str) or not _FILENAME.match(fn) or "/" in fn or fn.startswith("."):
            bad(f"unsafe or malformed wheel filename {fn!r}")
        if fn in seen:
            bad(f"duplicate wheel {fn}")
        seen.add(fn)
        if not isinstance(w.get("sha256"), str) or not _SHA256.match(w["sha256"]):
            bad(f"{fn}: sha256 must be 64 lowercase hex digits")
        if not isinstance(w.get("size"), int) or isinstance(w["size"], bool) or w["size"] <= 0:
            bad(f"{fn}: size must be a positive integer")
        for key in ("name", "version"):
            if not isinstance(w.get(key), str) or not w[key]:
                bad(f"{fn}: {key} missing")
        if not isinstance(w.get("requires_dist"), list):
            bad(f"{fn}: requires_dist must be a list")
        url = urllib.parse.urlsplit(str(w.get("artifact_url")))
        if url.scheme != "https" or url.hostname not in ARTIFACT_HOSTS or Path(url.path).name != fn:
            bad(f"{fn}: artifact_url must be an https URL on {sorted(ARTIFACT_HOSTS)} ending in the filename")
        if urllib.parse.urlsplit(str(w.get("metadata_url"))).scheme != "https":
            bad(f"{fn}: metadata_url must be https")


# --------------------------------------------------------------------- host

def probe_host() -> dict:
    glibc = None
    try:
        text = os.confstr("CS_GNU_LIBC_VERSION") or ""
        match = re.fullmatch(r"glibc (\d+)\.(\d+).*", text)
        if match:
            glibc = (int(match.group(1)), int(match.group(2)))
    except (ValueError, OSError):
        pass
    try:
        import ensurepip

        seed = ensurepip.version()
    except Exception:  # noqa: BLE001 - absent ensurepip is reported as a mismatch
        seed = None
    return {
        "implementation": sys.implementation.name,
        "python": f"{sys.version_info[0]}.{sys.version_info[1]}",
        "machine": platform.machine(),
        "system": "linux" if sys.platform.startswith("linux") else sys.platform,
        "soabi": sysconfig.get_config_var("SOABI"),
        "glibc": glibc,
        "seed_pip_version": seed,
    }


def check_host(profile: dict, probe: dict | None = None) -> None:
    probe = probe or probe_host()
    spec = profile["host"]
    problems = []
    for key in ("implementation", "python", "machine", "system", "soabi", "seed_pip_version"):
        if probe.get(key) != spec[key]:
            problems.append(f"host {key} is {probe.get(key)!r}, profile requires {spec[key]!r}")
    need = tuple(int(x) for x in spec["min_glibc"].split("."))
    if probe.get("glibc") is None or tuple(probe["glibc"]) < need:
        problems.append(f"host glibc is {probe.get('glibc')!r}, profile requires >= {spec['min_glibc']}")
    if problems:
        raise InputsError(
            *problems,
            "this profile qualifies only the stated host; another host needs its own reviewed profile",
        )


def _platform_ok(tag: str, machine: str, glibc: tuple | None) -> bool:
    if tag == "any":
        return True
    if tag == f"linux_{machine}":
        return True
    legacy = {"manylinux1": (2, 5), "manylinux2010": (2, 12), "manylinux2014": (2, 17)}
    match = re.fullmatch(r"(manylinux1|manylinux2010|manylinux2014)_(.+)", tag)
    if match:
        return match.group(2) == machine and glibc is not None and glibc >= legacy[match.group(1)]
    match = re.fullmatch(r"manylinux_(\d+)_(\d+)_(.+)", tag)
    if match:
        return match.group(3) == machine and glibc is not None and glibc >= (int(match.group(1)), int(match.group(2)))
    return False


def wheel_tags(filename: str) -> tuple[list[str], list[str], list[str]]:
    parts = filename[: -len(".whl")].split("-")
    if len(parts) not in (5, 6):
        raise InputsError(f"{filename}: not a wheel filename")
    return parts[-3].split("."), parts[-2].split("."), parts[-1].split(".")


def check_wheel_compatible(filename: str, profile: dict, probe: dict | None = None) -> None:
    probe = probe or probe_host()
    pys, abis, plats = wheel_tags(filename)
    pairs = set(profile["compatible_tags"])
    glibc = tuple(probe["glibc"]) if probe.get("glibc") else None
    pair_ok = any(f"{p}-{a}" in pairs for p in pys for a in abis)
    plat_ok = any(_platform_ok(t, profile["host"]["machine"], glibc) for t in plats)
    if not (pair_ok and plat_ok):
        raise InputsError(f"{filename}: tags are not accepted by the profile for this host")


# ------------------------------------------------------------------- bundle

def _read_regular(dir_fd: int, name: str, limit: int) -> bytes:
    """Read one regular, non-symlink entry within ``limit`` bytes."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd)
    except OSError as exc:
        raise InputsError(f"{name}: cannot open without following links ({exc.strerror})") from exc
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise InputsError(f"{name}: not a regular file")
        if info.st_mode & stat.S_IWOTH:
            raise InputsError(f"{name}: world-writable")
        if info.st_size > limit:
            raise InputsError(f"{name}: {info.st_size} bytes exceeds the pinned size {limit}")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise InputsError(f"{name}: larger than the pinned size {limit}")
    return data


def _open_directory(path: Path) -> int:
    try:
        if path.is_symlink():
            raise InputsError(f"{path}: the bundle directory itself must not be a symlink")
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise InputsError(f"{path}: cannot open bundle directory ({exc.strerror})") from exc
    if os.fstat(fd).st_mode & stat.S_IWOTH:
        os.close(fd)
        raise InputsError(f"{path}: bundle directory is world-writable")
    return fd


def check_wheel_content(data: bytes, entry: dict) -> None:
    """Embedded metadata must agree with the profile; no unsafe archive members."""
    fn = entry["filename"]
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise InputsError(f"{fn}: not a valid wheel archive") from exc
    with archive:
        names = archive.namelist()
        for member in names:
            parts = member.split("/")
            if member.startswith("/") or "\\" in member or ".." in parts or "\x00" in member:
                raise InputsError(f"{fn}: unsafe archive member {member!r}")
        stem = fn[: -len(".whl")].split("-")
        dist_info = f"{stem[0]}-{stem[1]}.dist-info"
        try:
            meta = email.parser.Parser().parsestr(archive.read(f"{dist_info}/METADATA").decode("utf-8"), headersonly=True)
            wheel = email.parser.Parser().parsestr(archive.read(f"{dist_info}/WHEEL").decode("utf-8"), headersonly=True)
        except KeyError as exc:
            raise InputsError(f"{fn}: missing {dist_info}/METADATA or WHEEL") from exc
        # Only top-level dist-info counts: some wheels vendor other distributions.
        if sum(1 for n in names if re.fullmatch(r"[^/]+\.dist-info/METADATA", n)) != 1:
            raise InputsError(f"{fn}: expected exactly one dist-info METADATA")
    if normalize(meta.get("Name", "")) != normalize(entry["name"]) or meta.get("Version") != entry["version"]:
        raise InputsError(f"{fn}: embedded name/version {meta.get('Name')} {meta.get('Version')} differ from the profile")
    if sorted(meta.get_all("Requires-Dist") or []) != sorted(entry["requires_dist"]):
        raise InputsError(f"{fn}: embedded Requires-Dist differs from the profile; closure must be re-reviewed")
    if (meta.get("Requires-Python") or "") != (entry.get("requires_python") or ""):
        raise InputsError(f"{fn}: embedded Requires-Python differs from the profile")
    pys, abis, plats = wheel_tags(fn)
    expected = {f"{p}-{a}-{t}" for p in pys for a in abis for t in plats}
    if set(wheel.get_all("Tag") or []) != expected:
        raise InputsError(f"{fn}: WHEEL tags differ from the filename tags")


def verify_bundle(profile: dict, bundle: Path, probe: dict | None = None) -> list[dict]:
    """Return the verified entries or raise InputsError listing every problem."""
    check_host(profile, probe)
    problems: list[str] = []
    fd = _open_directory(Path(bundle))
    try:
        expected = {w["filename"]: w for w in profile["wheels"]}
        present = set(os.listdir(fd))
        problems += [f"missing: {n}" for n in sorted(set(expected) - present)]
        problems += [f"unexpected entry (extra, hidden, nested or temporary): {n}" for n in sorted(present - set(expected))]
        for name in sorted(present & set(expected)):
            entry = expected[name]
            try:
                data = _read_regular(fd, name, entry["size"])
                if len(data) != entry["size"]:
                    raise InputsError(f"{name}: size {len(data)} differs from pinned {entry['size']}")
                if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    raise InputsError(f"{name}: SHA-256 differs from the profile")
                check_wheel_compatible(name, profile, probe)
                check_wheel_content(data, entry)
            except InputsError as exc:
                problems += exc.problems
    finally:
        os.close(fd)
    if problems:
        raise InputsError(*problems)
    return list(profile["wheels"])


# -------------------------------------------------------------------- fetch

class _StrictRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urllib.parse.urlsplit(newurl)
        if url.scheme != "https" or url.hostname not in ARTIFACT_HOSTS:
            raise InputsError(f"refusing redirect to {newurl!r}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(entry: dict, opener) -> bytes:
    limit = entry["size"]
    try:
        with opener.open(entry["artifact_url"], timeout=120) as response:
            final = urllib.parse.urlsplit(response.geturl())
            if final.scheme != "https" or final.hostname not in ARTIFACT_HOSTS:
                raise InputsError(f"{entry['filename']}: served from unexpected location {response.geturl()!r}")
            data = response.read(limit + 1)
    except InputsError:
        raise
    except Exception as exc:  # noqa: BLE001 - any transport failure is a fetch failure
        raise InputsError(f"{entry['filename']}: download failed: {exc}") from exc
    return data


def fetch_bundle(profile: dict, bundle: Path, *, from_dir: Path | None = None, opener=None,
                 probe: dict | None = None) -> list[str]:
    """Create ``bundle`` (must not exist) with exactly the pinned wheels, verified.

    Returns notes (e.g. ignored extra files in a transferred source).  The new
    directory appears only after every wheel verified; otherwise nothing remains.
    """
    check_host(profile, probe)
    bundle = Path(bundle)
    if os.path.lexists(bundle):
        raise InputsError(f"{bundle}: already exists; fetch creates a NEW directory and never overwrites")
    if not bundle.parent.is_dir():
        raise InputsError(f"{bundle.parent}: parent directory does not exist")
    notes: list[str] = []
    source_fd = None
    if from_dir is None:
        opener = opener or urllib.request.build_opener(_StrictRedirects())
    else:
        source_fd = _open_directory(Path(from_dir))
        extra = sorted(set(os.listdir(source_fd)) - {w["filename"] for w in profile["wheels"]})
        notes += [f"ignored non-profile entry in source: {n}" for n in extra]
    staging = bundle.parent / f".{bundle.name}.fetching-{os.getpid()}"
    os.mkdir(staging, 0o700)
    try:
        for entry in profile["wheels"]:
            if source_fd is not None:
                data = _read_regular(source_fd, entry["filename"], entry["size"])
            else:
                data = _download(entry, opener)
            if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise InputsError(f"{entry['filename']}: bytes do not match the pinned size/SHA-256; rejected")
            check_wheel_compatible(entry["filename"], profile, probe)
            check_wheel_content(data, entry)
            fd = os.open(staging / entry["filename"], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, "wb") as out:
                out.write(data)
        os.chmod(staging, 0o755)
        verify_bundle(profile, staging, probe)
        os.rename(staging, bundle)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        if source_fd is not None:
            os.close(source_fd)
    return notes


# ---------------------------------------------------------------------- env

def offline_environment(base: dict, bundle: Path, profile_id: str) -> dict:
    """Deterministic offline pip settings; every inherited ``PIP_*`` is discarded.

    Unrelated variables (PATH, HOME, proxies, ...) are preserved unchanged.
    ``PIP_CONFIG_FILE=/dev/null`` makes pip ignore global, user and site files.
    """
    env = {k: v for k, v in base.items() if not k.upper().startswith("PIP_")}
    env.update(sanitized_pip_settings())
    if bundle is not None:
        env["PIP_FIND_LINKS"] = str(os.path.abspath(bundle))
        env["WFC_TEST_INPUTS"] = str(os.path.abspath(bundle))
        env["WFC_TEST_PROFILE"] = profile_id
    return env


def sanitized_pip_settings() -> dict:
    return {
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_NO_INDEX": "1",
        "PIP_NO_CACHE_DIR": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
    }


# ---------------------------------------------------------------------- CLI

def _run_exec(profile: dict, profile_id: str, bundle: Path, argv: list[str]) -> int:
    verify_bundle(profile, bundle)
    env = offline_environment(dict(os.environ), bundle, profile_id)
    try:
        code = subprocess.run(argv, env=env, shell=False).returncode
    except OSError as exc:
        print(f"wfc_test_inputs: cannot execute {argv[0]!r}: {exc.strerror}", file=sys.stderr)
        code = 127
    try:
        verify_bundle(profile, bundle)
    except InputsError as exc:
        _report("inputs changed or became invalid while the command ran", exc)
        return TOOL_FAILURE
    return 128 - code if code < 0 else code


def _report(heading: str, exc: InputsError) -> None:
    print(f"wfc_test_inputs: {heading}:", file=sys.stderr)
    for problem in exc.problems:
        print(f"  - {problem}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wfc_test_inputs.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--profile-dir", type=Path, default=None, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("fetch", "verify", "exec"):
        p = sub.add_parser(name)
        p.add_argument("--profile", required=True, help="profile id, e.g. cp313-linux-x86_64")
        p.add_argument("--bundle", required=True, type=Path,
                       help="bundle directory (fetch: must not exist)" if name == "fetch" else "bundle directory")
        if name == "fetch":
            p.add_argument("--from-dir", type=Path, default=None,
                           help="copy pinned wheels from a transferred directory instead of downloading")
        if name == "exec":
            p.add_argument("argv", nargs=argparse.REMAINDER, help="-- command and arguments, run without a shell")
    args = parser.parse_args(argv)
    try:
        profile = load_profile(args.profile, args.profile_dir)
        if args.command == "fetch":
            for note in fetch_bundle(profile, args.bundle, from_dir=args.from_dir):
                print(f"note: {note}")
            print(f"fetched and verified {len(profile['wheels'])} wheels into {args.bundle}")
            return 0
        if args.command == "verify":
            verify_bundle(profile, args.bundle)
            print(f"verified {len(profile['wheels'])} wheels in {args.bundle} against profile {args.profile}")
            return 0
        command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        if not command:
            parser.error("exec requires a command after --")
        return _run_exec(profile, args.profile, args.bundle, command)
    except (InputsError, OSError) as exc:
        _report("refused", exc if isinstance(exc, InputsError) else InputsError(f"{exc.__class__.__name__}: {exc}"))
        return TOOL_FAILURE if args.command == "exec" else 1


if __name__ == "__main__":
    sys.exit(main())

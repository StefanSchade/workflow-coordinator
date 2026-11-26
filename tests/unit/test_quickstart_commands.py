"""BP9: the consumer quickstart's documented commands must match the real CLI.

Extracts every fenced ``[source,shell]`` block from the consumer quickstart,
finds every ``wfc <subcommand> ...`` invocation in it, and checks the
subcommand and every flag token against the real argparse parser each
Front-Door module actually builds -- not a hand-maintained list of flags that
could silently drift from ``src/``. A flag renamed or removed in the real
parser, or newly added as required without being documented, fails this test.
"""

from __future__ import annotations

import argparse
import functools
import re
import shlex
from pathlib import Path
from typing import Callable

import pytest

from workflow_coordinator.frontdoor import archive as ARCHIVE
from workflow_coordinator.frontdoor import audit as AUDIT
from workflow_coordinator.frontdoor import emit_request as EMIT_REQUEST
from workflow_coordinator.frontdoor import init as INIT
from workflow_coordinator.frontdoor import lifecycle as LIFECYCLE
from workflow_coordinator.frontdoor import plan as PLAN
from workflow_coordinator.frontdoor import report as REPORT
from workflow_coordinator.frontdoor import skill as SKILL

QUICKSTART = (
    Path(__file__).resolve().parent.parent.parent
    / "docs" / "operator_manual" / "ch09_consumer_quickstart" / "01_quickstart.adoc"
)

_SHELL_BLOCK_RE = re.compile(r"\[source,shell\]\n-{4,}\n(.*?)\n-{4,}", re.DOTALL)
_CONTINUATION_RE = re.compile(r"\\\n\s*")

# One parse-args entry point per real CLI subcommand, exactly as
# ``frontdoor.cli.main`` dispatches it.
_PARSE_ARGS: dict[str, Callable[[list[str]], argparse.Namespace]] = {
    "init": INIT._parse_args,
    "plan": PLAN._parse_args,
    "request": EMIT_REQUEST._parse_args,
    "run": LIFECYCLE._parse_run_args,
    "resume": functools.partial(LIFECYCLE._parse_simple_args, "wfc resume"),
    "status": functools.partial(LIFECYCLE._parse_simple_args, "wfc status"),
    "audit": AUDIT._parse_args,
    "report": REPORT._parse_args,
    "archive": ARCHIVE._parse_args,
    "skill": SKILL._parse_args,
}


def _real_parser(parse_args: Callable[[list[str]], argparse.Namespace]) -> argparse.ArgumentParser:
    """The actual ``argparse.ArgumentParser`` a Front-Door module builds.

    Intercepts ``ArgumentParser.parse_args`` itself (rather than calling it)
    so the parser is captured before any required-argument or value
    validation runs -- this needs no argv that would actually satisfy the
    subcommand's real requirements.
    """

    captured: dict[str, argparse.ArgumentParser] = {}
    original = argparse.ArgumentParser.parse_args

    def fake(self: argparse.ArgumentParser, args=None, namespace=None):
        captured["parser"] = self
        raise SystemExit(0)

    argparse.ArgumentParser.parse_args = fake  # type: ignore[assignment]
    try:
        with pytest.raises(SystemExit):
            parse_args([])
    finally:
        argparse.ArgumentParser.parse_args = original  # type: ignore[assignment]
    return captured["parser"]


@functools.lru_cache(maxsize=None)
def _allowed_flags(subcommand: str) -> frozenset[str]:
    parser = _real_parser(_PARSE_ARGS[subcommand])
    return frozenset(parser._option_string_actions.keys())


@functools.lru_cache(maxsize=None)
def _required_flags(subcommand: str) -> frozenset[str]:
    parser = _real_parser(_PARSE_ARGS[subcommand])
    flags = set()
    for action in parser._actions:
        if action.option_strings and action.required:
            flags.update(action.option_strings)
    return frozenset(flags)


def _shell_blocks(text: str) -> list[str]:
    return _SHELL_BLOCK_RE.findall(text)


def _wfc_commands(block: str) -> list[list[str]]:
    """Every logical ``wfc ...`` command line in one fenced shell block."""

    joined = _CONTINUATION_RE.sub(" ", block)
    commands = []
    for line in joined.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = shlex.split(line)
        if tokens and tokens[0] == "wfc":
            commands.append(tokens)
    return commands


def _quickstart_text() -> str:
    return QUICKSTART.read_text(encoding="utf-8")


def test_quickstart_file_exists():
    assert QUICKSTART.is_file()


def test_quickstart_documents_at_least_one_command_per_shown_subcommand():
    # Kills a mutation (or an editing mistake) that silently drops every
    # ``wfc`` invocation from the quickstart's fenced shell blocks.
    commands = [cmd for block in _shell_blocks(_quickstart_text()) for cmd in _wfc_commands(block)]
    assert commands, "the quickstart has no fenced `wfc ...` shell command"
    subcommands = {cmd[1] for cmd in commands if len(cmd) > 1}
    assert subcommands & set(_PARSE_ARGS), "no real wfc subcommand is documented"


def test_every_documented_subcommand_and_flag_exists_on_the_real_cli():
    # Kills a mutation that renames or removes a flag the quickstart
    # documents (the flag would vanish from the real parser's option
    # strings, and the lookup below would raise), or that changes the
    # dispatch contract so a documented subcommand no longer exists.
    for block in _shell_blocks(_quickstart_text()):
        for tokens in _wfc_commands(block):
            if len(tokens) < 2:
                continue
            subcommand = tokens[1]
            if subcommand.startswith("-"):
                # The bare `wfc --version --json` form: not a subcommand.
                assert subcommand in {"--version"}
                assert set(tokens[1:]) <= {"--version", "--json"}
                continue
            assert subcommand in _PARSE_ARGS, f"undocumented or unknown wfc subcommand: {subcommand}"
            allowed = _allowed_flags(subcommand)
            for token in tokens[2:]:
                if not token.startswith("-"):
                    continue
                flag = token.split("=", 1)[0]
                assert flag in allowed, (
                    f"`wfc {subcommand} {flag}` is documented in the quickstart but is not a real "
                    f"flag of the installed CLI (known flags: {sorted(allowed)})"
                )


def test_documented_commands_supply_every_required_flag():
    # Kills a mutation that adds a new required flag to a subcommand the
    # quickstart already documents, without updating the documented
    # invocation -- the quickstart would otherwise silently go stale.
    for block in _shell_blocks(_quickstart_text()):
        for tokens in _wfc_commands(block):
            if len(tokens) < 2 or tokens[1].startswith("-"):
                continue
            subcommand = tokens[1]
            documented_flags = {token.split("=", 1)[0] for token in tokens[2:] if token.startswith("-")}
            missing = _required_flags(subcommand) - documented_flags
            assert not missing, f"`wfc {subcommand}` example is missing required flag(s): {sorted(missing)}"

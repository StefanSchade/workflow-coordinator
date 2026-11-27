"""Installed ``wfc request`` -- emit the Consumer Request ``wfc plan`` only
previews (D1 Option B, FTR-WFC-OPERATOR-SCAFFOLDING-AND-CLI-ERGONOMICS O5).

Takes exactly the same inputs as ``wfc plan`` (``plan.add_plan_input_arguments``)
plus an explicit ``--output`` path, resolves them through the identical
``plan.resolve_plan_inputs`` compile path -- the same request-construction
code ``consumer_request_from_plan_inputs`` -- and writes the resulting
``ConsumerRequest``'s own ``raw_text`` verbatim to that path. Nothing here
is authorized: the written bytes grant no authority of their own. ``wfc run
--request <path>`` re-reads the file from a plain filesystem path, re-parses
it under the closed BP2b schema, and re-digests it before anything is
authorized or committed, exactly as it does for a hand-authored file; this
command changes none of that.

Refuses, before any write: an existing destination (unless ``--force``), and
a destination inside the consumer repository's own worktree (unless
``--allow-inside-repository``) -- an emitted, uncommitted file inside the
tree would otherwise dirty exactly the clean tree ``wfc run`` requires,
turning an unauthorized preview artifact into something that *looks* like it
is part of the Commission. Both rules are the Design decision in
``FTR-WFC-OPERATOR-SCAFFOLDING-AND-CLI-ERGONOMICS.adoc``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .plan import (
    PlanInputError,
    ResolvedPlanInputs,
    add_plan_input_arguments,
    render_commission_preview_lines,
    resolve_plan_inputs,
)


class OutputPathError(Exception):
    """A refusal to write the emitted Consumer Request to ``--output``."""


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def resolve_output_path(args: argparse.Namespace, *, repository: Path) -> Path:
    """Resolve and refuse ``--output`` before any write, per the Design
    decision: an existing file is refused unless ``--force``; a destination
    inside the consumer repository's own worktree is refused unless
    ``--allow-inside-repository`` -- both are checked on the plain,
    unresolved-further filesystem path, mirroring ``wfc run --request``'s own
    "plain filesystem path" contract for this same kind of file."""

    output_path = Path(args.output).resolve()
    if output_path.exists() and not args.force:
        raise OutputPathError(f"refusing to overwrite existing file: {output_path} (pass --force to overwrite)")
    if _is_inside(output_path, repository) and not args.allow_inside_repository:
        raise OutputPathError(
            f"refusing an output path inside the consumer repository's own worktree: {output_path} "
            "(an emitted, uncommitted request would dirty the clean tree `wfc run` requires; pass "
            "--allow-inside-repository to write there anyway)"
        )
    return output_path


def render_request_summary(*, inputs: ResolvedPlanInputs, output_path: Path) -> str:
    """The same shared preview body ``wfc plan`` prints, plus this command's
    own closing statement: what was written, and that nothing above was an
    authorization."""

    header = (
        f"wfc request -- commission {inputs.request.commission_id} / work item {inputs.request.work_item_id}"
    )
    lines = render_commission_preview_lines(
        header=header, task=inputs.task, request=inputs.request, config=inputs.config,
        compiled=inputs.compiled, envelope=inputs.envelope,
    )
    lines.append("")
    lines.append(f"Consumer Request written to: {output_path}")
    lines.append(
        "NEXT EXPLICIT ACTION: nothing was authorized by this command -- no project command, "
        "provider call, Commission write or Git mutation. The file above grants no authority: "
        "`wfc run --request ...` re-reads it from a plain filesystem path, re-parses it under "
        "the closed BP2b schema and re-digests it before anything is authorized or committed. "
        "scope, program, personas, max_autonomous_hops, the ordered evidence commands and "
        "authority_source are this operator's own judgement, merely carried here -- never "
        "supplied, validated or authorized by this command."
    )
    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wfc request",
        description=(
            "Emit the Consumer Request `wfc plan` previews: same inputs, same shared "
            "request-construction/compile path, plus an explicit --output path. Prints the "
            "same projected digests `wfc plan` would for identical inputs. Makes no Git "
            "mutation and no provider call, and authorizes nothing -- `wfc run --request` "
            "still re-parses and re-digests the written file under the closed schema."
        ),
    )
    add_plan_input_arguments(parser)
    parser.add_argument("--output", required=True, metavar="PATH",
                         help="plain filesystem path to write the Consumer Request YAML to")
    parser.add_argument("--force", action="store_true",
                         help="overwrite an existing --output file (refused by default)")
    parser.add_argument("--allow-inside-repository", action="store_true",
                         help="allow --output inside the consumer repository's own worktree "
                              "(refused by default: an emitted, uncommitted file would dirty "
                              "the clean tree `wfc run` requires)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        inputs = resolve_plan_inputs(args)
    except PlanInputError as error:
        print(f"wfc request: {error}", file=sys.stderr)
        return 2

    try:
        output_path = resolve_output_path(args, repository=inputs.repository)
    except OutputPathError as error:
        print(f"wfc request: {error}", file=sys.stderr)
        return 2

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(inputs.request.raw_text, encoding="utf-8")

    print(render_request_summary(inputs=inputs, output_path=output_path), end="")
    return 0

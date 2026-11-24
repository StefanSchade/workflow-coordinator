"""BP6a: the consolidated ``wfc report``, computed from an audit projection.

``build_report`` interprets nothing the engine did not persist: every field
it reports is read from ``wfc.audit/1`` (see ``frontdoor.audit``) by name.
It never claims a Run was pushed, merged or otherwise acted on beyond what
the audit's own facts record, and it never reports an unknown or stopped
status as success.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from .audit import AuditError, ProjectionError, project_audit

REPORT_SCHEMA = "wfc.report/1"

# Fixed table: the one line naming the next human choice, keyed by the
# reported status. Anything not in this table (together with "completed",
# which needs no further action) is reported as "unknown", never as success.
#
# BP6b (review observation): this table now covers every real
# ``model.RunStatus`` value and every ``frontdoor.lifecycle.EXIT_CODES``
# classification, not only the six a BP6a archived fixture can carry --
# ``test_report_status_coverage.py`` pins that coverage against both
# vocabularies directly (without importing ``lifecycle`` here, which would
# pull its Git/subprocess dependencies into this deliberately pure module).
# A status outside this table, including a genuinely unrecognized future
# one, is still reported as "unknown", never silently as success.
_NEXT_HUMAN_CHOICE = {
    # model.RunStatus: non-terminal states an archived Attempt should not
    # normally carry, but a mid-flight or malformed archive still can.
    "ready": "the Run has not started; launch it, or inspect why it has not",
    "running": "the Run is still in progress; wait, then re-check with `wfc report`",
    "awaiting_supervisor": "a human must inspect the pending supervisor step and advance it",
    "suspended": "a human must resolve the suspending prerequisite, then run `wfc resume`",
    # model.RunStatus / frontdoor.lifecycle.EXIT_CODES: shared terminal/stop names.
    # "awaiting_human" has two distinct shapes (WI-WFC-RECOVERY-ROUTING-001):
    # see the ``human_question_present``-conditional override in
    # ``build_report`` below -- this default applies only when that signal is
    # unavailable (a legacy or malformed audit), so it never regresses to
    # promising a question that provably does not exist.
    "awaiting_human": "a human must answer the open question, then run `wfc resume`",
    "awaiting_human_budget": "a human must authorize additional hop budget, then run `wfc resume`",
    "reconcile_required": "a human must reconcile the Run's conflicting state before resuming",
    "stopped": "a human must decide how to proceed; this Run will not resume on its own",
    # frontdoor.lifecycle.EXIT_CODES: controller-level classifications.
    "interrupted": "a human must inspect the Run and decide whether to resume it",
    "engine_active": "the engine is still running; wait for it to finish, then re-check",
    "engine_exited_before_state": "a human must inspect the Run directly; the engine exited before writing a terminal state",
    "uncertain_launch": "a human must determine whether the engine actually launched before taking any further action",
    "inconsistent": "a human must inspect the Run directly; its recorded state is internally inconsistent",
    "launch_failed_no_engine": "a human must diagnose the launch failure before retrying",
    "refused_unauthorized": "a human must review and re-authorize this lifecycle step",
    "refused_drift": "a human must reconcile the repository, which drifted from what was authorized",
    "refused_prelaunch": "a human must inspect the pre-launch refusal before retrying",
    "refused_probe": "a human must fix the environment or the failing baseline check before retrying",
    "refused_identity_changed": "a human must review the changed implementation identity before retrying",
    "refused_launch_exhausted": "a human must inspect the Run directly; its launch retry budget is exhausted",
    "refused_active_lifecycle": "a human must resolve the other already-active lifecycle first",
    "refused_journal_root": "a human must inspect the Run directly; its lifecycle journal root is invalid",
    "refused_no_lifecycle": "a human must start a lifecycle for this Run before it can proceed",
    "not_launched": "the Run has not been launched yet; launch it, or inspect why it has not",
    "lifecycle_active": "the lifecycle is still active; wait, then re-check with `wfc report`",
    "no_lifecycle": "a human must inspect why no lifecycle journal exists for this Run",
    "refused_not_recoverable": "a human must inspect the named refusal reason; nothing was changed by the attempt",
}
_UNKNOWN_NEXT_ACTION = "status is unknown; a human must inspect this Run directly"
_KNOWN_STATUSES = frozenset({"completed", *_NEXT_HUMAN_CHOICE})


class ReportError(RuntimeError):
    """A refusal to build or render a report from malformed audit input."""


def build_report(audit: Mapping[str, Any], *, archive_ref: str | None = None) -> dict:
    """Project ``wfc.report/1`` from an already-built ``wfc.audit/1`` mapping."""

    run = audit.get("run") or {}
    governance = audit.get("governance")
    lifecycle = audit.get("lifecycle")

    classification = lifecycle.get("classification") if isinstance(lifecycle, Mapping) else None
    raw_status = classification or run.get("status")
    status = raw_status if raw_status in _KNOWN_STATUSES else "unknown"
    success = status == "completed"

    program = None
    if isinstance(governance, Mapping):
        profile_id, profile_revision = governance.get("profile_id"), governance.get("profile_revision")
        if profile_id and profile_revision:
            program = f"{profile_id}@{profile_revision}"

    findings = audit.get("findings") or []
    open_blocking = [
        finding.get("finding_id")
        for finding in findings
        if finding.get("blocking") and finding.get("state") == "open"
    ]

    hops = audit.get("hops") or []
    commits = [commit for hop in hops for commit in (hop.get("commits") or [])]
    evidence_lines = [
        {"hop_id": hop.get("hop_id"), **item}
        for hop in hops
        for item in (hop.get("evidence") or [])
    ]

    limits = list(audit.get("limits") or [])
    not_done = ["nothing was pushed"]
    if not success:
        not_done.append("the task is not complete")
    if limits:
        not_done.append("limits: " + ", ".join(sorted(limits)))

    next_action = None
    if not success:
        if status == "awaiting_human" and run.get("human_question_present") is False:
            # No open human question is recorded: "answer the open question"
            # would name a path that does not exist (the exact incident this
            # WorkItem fixes). `wfc resume` itself now decides, from the
            # Run's own recorded facts, whether this is automatically
            # recoverable or must refuse with a named reason -- this text
            # promises only that real, existing path.
            next_action = (
                "a human must inspect the recorded mechanical stop facts; "
                "run `wfc resume`, which recovers it automatically if it is "
                "eligible, or refuses with a named reason if it is not"
            )
        else:
            next_action = _NEXT_HUMAN_CHOICE.get(status, _UNKNOWN_NEXT_ACTION)

    return {
        "schema": REPORT_SCHEMA,
        "attempt_id": audit.get("attempt_id"),
        "status": status,
        "status_known": status != "unknown",
        "stop_reason": run.get("stop_reason"),
        "run_id": run.get("run_id"),
        "work_item_id": run.get("work_item_id"),
        "program": program,
        "hops_used": run.get("hops_used"),
        "hop_limit": run.get("hop_limit"),
        "commits": commits,
        "evidence": evidence_lines,
        "findings_open_blocking": open_blocking,
        "archive_ref": archive_ref,
        "limits": limits,
        "next_action": next_action,
        "not_done": not_done,
    }


def render_report_text(report: Mapping[str, Any]) -> str:
    lines = [f"WFC RUN  {report.get('attempt_id') or ''}", ""]

    budget = ""
    if report.get("hop_limit") is not None:
        budget = f"  {report.get('hops_used')} of {report.get('hop_limit')} hops"
    program = f"  {report['program']}" if report.get("program") else ""
    lines.append(f"outcome     {report['status']}{program}{budget}  run {report.get('run_id') or ''}")
    if not report.get("status_known"):
        lines.append("            status is unknown -- not reported as success")
    if report.get("stop_reason"):
        lines.append(f"stop reason {report['stop_reason']}")

    if report.get("commits"):
        lines.append("commits     " + "  ".join(report["commits"]))
    else:
        lines.append("commits     none")

    if report.get("evidence"):
        for item in report["evidence"]:
            status_bit = item.get("status") or item.get("error_class") or "unknown"
            exit_bit = "" if item.get("exit_code") is None else f"  exit {item['exit_code']}"
            lines.append(f"evidence    {item.get('command_name')}  {status_bit}{exit_bit}  ({item.get('hop_id')})")
    else:
        lines.append("evidence    none")

    if report.get("findings_open_blocking"):
        lines.append("findings    open blocking: " + ", ".join(report["findings_open_blocking"]))
    else:
        lines.append("findings    none open")

    if report.get("archive_ref"):
        lines.append(f"archive     {report['archive_ref']}")

    if report.get("next_action"):
        lines.append(f"next        {report['next_action']}")

    for item in report.get("not_done") or []:
        lines.append(f"not done    {item}")

    return "\n".join(lines) + "\n"


def render_report_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def _load_audit_source(path: Path) -> dict:
    """Accept either an attempt directory or an already-written ``audit.json``."""

    if path.is_dir():
        return project_audit(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReportError(f"{path} is not a readable audit.json or attempt directory: {error}") from error
    if not isinstance(data, dict):
        raise ReportError(f"{path} does not contain a JSON object")
    return data


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wfc report",
        description="Render the consolidated result of an archived Attempt from its audit projection.",
    )
    parser.add_argument("source", help="an archived Attempt directory, or an audit.json path")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        audit = _load_audit_source(Path(args.source))
        report = build_report(audit)
    except ProjectionError as error:
        print(f"wfc report: {error}", file=sys.stderr)
        return 3
    except (ReportError, AuditError) as error:
        print(f"wfc report: {error}", file=sys.stderr)
        return 2
    print(render_report_json(report) if args.as_json else render_report_text(report), end="")
    return 0

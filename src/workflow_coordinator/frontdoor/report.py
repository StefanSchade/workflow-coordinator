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
from .config import consumer_config_from_yaml, resolve_evidence
from ..commissioning import CommissionError, _path
from ..git_facts import GitPreflightError, repository_identity
from ..reducer import MECHANICAL_PREEMPTION_PREFIX

DEFAULT_CONFIG_PATH = ".wfc/config.yaml"

REPORT_SCHEMA = "wfc.report/1"

# The honest next-action line for a recoverable mechanical stop (WI-WFC-
# RECOVERY-ROUTING-001): `wfc resume` itself decides, from the Run's own
# recorded facts, whether this is automatically recoverable or must refuse
# with a named reason -- this text promises only that real, existing path.
# Shared by both shapes a recoverable mechanical stop can be reported under:
# a legacy-classified "awaiting_human" Attempt with no recorded human
# question, and the new "reconcile_required" classification Part A gives a
# recoverable mechanical stop going forward. Both uses require the
# `mechanical_preemption:` stop-reason prefix below -- a question-less
# "awaiting_human" stop that is NOT a recoverable mechanical one (e.g. an
# open blocking Finding) gets its own, separate text instead; see
# `_NO_RECOVERY_NEXT_ACTION`.
_MECHANICAL_RECOVERY_NEXT_ACTION = (
    "a human must inspect the recorded mechanical stop facts; "
    "run `wfc resume`, which recovers it automatically if it is "
    "eligible, or refuses with a named reason if it is not"
)

# A question-less "awaiting_human" stop that is also not a recoverable
# mechanical one: `wfc resume`'s own eligibility check requires the
# `mechanical_preemption:` prefix, so for this shape it silently no-ops
# rather than recovering or refusing by name. Naming `wfc resume` here would
# repeat the incident this WorkItem fixes; this text promises neither an
# open question (there is none) nor a recovery (there is none).
_NO_RECOVERY_NEXT_ACTION = (
    "a human must inspect the recorded stop facts directly; no open "
    "question is recorded, this is not a recoverable mechanical stop, "
    "and no automated recovery exists for it"
)

# O10B: a `reconcile_required` Run whose reconcile reason is `interruption`
# -- the invocation was interrupted after its dispatch marker existed, so
# its Hop is already charged and is never replayed. The fixed table's
# generic "a human must reconcile the Run's conflicting state before
# resuming" is wrong for this shape in both halves: there may be no
# conflicting state at all, and `wfc resume` does now recover it where its
# own predicate holds. `--preview` is named first because it is the
# read-only way to learn which of the two it is.
_INTERRUPTION_RECOVERY_NEXT_ACTION = (
    "a human must validate current reality after an interrupted invocation, whose "
    "Hop is already charged and is never replayed; run `wfc resume --preview` to "
    "see whether the interruption is provably quiescent, then `wfc resume`, which "
    "continues this same Run or refuses with a named reason"
)

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
    # "awaiting_human" has three distinct shapes (WI-WFC-RECOVERY-ROUTING-001):
    # see the ``human_question_present``/``mechanical_stop``-conditional
    # overrides in ``build_report`` below -- this default applies only when
    # a human question is actually recorded (or that signal is unavailable,
    # a legacy or malformed audit), so it never regresses to promising a
    # question that provably does not exist, nor a recovery that provably
    # does not happen.
    "awaiting_human": "a human must answer the open question, then run `wfc resume`",
    # O8: before this slice this line named `wfc resume`, which never
    # answered a budget stop at all (its recovery predicate requires a
    # `mechanical_preemption:` stop reason). It now names the command that
    # does, and the authority it actually requires.
    "awaiting_human_budget": (
        "a human must decide whether to authorize additional Hop budget; on the "
        "maintainer's explicit instruction run `wfc extend-budget --additional-hops N "
        "--authority \"<the instruction>\"`, which records the extension and continues "
        "the same Run, or prepare a successor Commission instead"
    ),
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
    "refused_budget_extension": (
        "a human must inspect the named refusal reason; no extension was recorded and "
        "nothing was changed, so the Run keeps its stop and every charged Hop"
    ),
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

    usage = audit.get("usage")
    usage_reported = (
        isinstance(usage, Mapping)
        and (usage["coverage_counts"]["complete"] + usage["coverage_counts"]["partial"]) > 0
    )
    # Nothing was actually reported by any invocation (every record, if any,
    # carries coverage "none"): keep the pre-usage report wording and
    # behavior exactly as before, per the task's explicit requirement.
    usage_report = (
        {
            "coverage_counts": dict(usage["coverage_counts"]),
            "totals": usage["totals"],
            "by_role": usage["by_role"],
        }
        if usage_reported
        else None
    )

    stop_reason = run.get("stop_reason")
    mechanical_stop = isinstance(stop_reason, str) and stop_reason.startswith(
        MECHANICAL_PREEMPTION_PREFIX
    )

    no_question = status == "awaiting_human" and run.get("human_question_present") is False

    # O10B: the audit's closed-enum projection of the Run's reconcile reason.
    # Absent from every archive built before that slice, which therefore
    # keeps the fixed table's generic `reconcile_required` line unchanged.
    interruption_reconcile = run.get("reconcile_reason") == "interruption"

    next_action = None
    if not success:
        if no_question and mechanical_stop:
            # No open human question is recorded, and the stop is a
            # recoverable mechanical one: "answer the open question" would
            # name a path that does not exist (the exact incident this
            # WorkItem fixes), but `wfc resume` genuinely does.
            next_action = _MECHANICAL_RECOVERY_NEXT_ACTION
        elif status == "reconcile_required" and mechanical_stop:
            # Part A's whole point: a recoverable mechanical stop now
            # classifies "reconcile_required" rather than terminal
            # "awaiting_human". The fixed table's "a human must reconcile
            # the Run's conflicting state before resuming" asserts a
            # conflicting state the recovery predicate guarantees is absent,
            # and withholds the one path that actually exists for it.
            next_action = _MECHANICAL_RECOVERY_NEXT_ACTION
        elif status == "reconcile_required" and interruption_reconcile:
            # O10B: the one other `reconcile_required` shape with a real
            # recovery path. Kept narrow on purpose: every other reconcile
            # reason, and a legacy archive with none recorded, keeps the
            # fixed table's generic line, which is fail-closed.
            next_action = _INTERRUPTION_RECOVERY_NEXT_ACTION
        elif no_question:
            # No open human question is recorded, but the stop is NOT a
            # recoverable mechanical one (e.g. an open blocking Finding):
            # `wfc resume`'s own eligibility check requires the
            # `mechanical_preemption:` prefix, so for this shape it silently
            # no-ops rather than recovering or refusing by name. Naming it
            # here would repeat the incident under a different trigger.
            next_action = _NO_RECOVERY_NEXT_ACTION
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
        # O8: the audit's structural projection of every recorded maintainer
        # Hop-budget extension, carried through unchanged (an empty list for
        # every Run that was never extended).
        "budget_extensions": run.get("budget_extensions") or [],
        "commits": commits,
        "evidence": evidence_lines,
        "findings_open_blocking": open_blocking,
        "archive_ref": archive_ref,
        "limits": limits,
        "next_action": next_action,
        "not_done": not_done,
        "usage": usage_report,
    }


_USAGE_METRIC_LABELS = (
    ("input_tokens", "input"),
    ("output_tokens", "output"),
    ("cache_read_tokens", "cache_read"),
    ("cache_creation_tokens", "cache_creation"),
    ("reasoning_output_tokens", "reasoning"),
)


def _render_usage_metrics(metrics: Mapping[str, Any]) -> str:
    """One compact line fragment per figure; a trailing ``+`` marks a total
    that is a lower bound (some contributing invocation's figure was
    unknown) -- never presented as exact when it is not."""

    parts = []
    for key, label in _USAGE_METRIC_LABELS:
        figure = metrics[key]
        if figure["value"] is None:
            continue
        marker = "+" if figure["lower_bound"] else ""
        parts.append(f"{label}={figure['value']}{marker}")
    return " ".join(parts) if parts else "none reported"


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

    for extension in report.get("budget_extensions") or []:
        lines.append(
            f"budget ext  +{extension.get('additional_hops')} hops "
            f"({extension.get('hop_limit_before')} -> {extension.get('hop_limit_after')})  "
            f"used {extension.get('hop_used_at_grant')} at grant  "
            f"{extension.get('granted_at')}  "
            f"authority sha256:{extension.get('authority_sha256')}"
        )

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

    if report.get("usage"):
        usage = report["usage"]
        counts = usage["coverage_counts"]
        lines.append(
            "usage       coverage "
            f"complete={counts['complete']} partial={counts['partial']} none={counts['none']}"
        )
        lines.append(f"usage total {_render_usage_metrics(usage['totals'])}")
        for role in sorted(usage["by_role"]):
            lines.append(f"usage role  {role}  {_render_usage_metrics(usage['by_role'][role])}")

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


def resolve_attempt_from_repository(repository: str, config_relpath: str) -> Path:
    """Resolve the one archived Attempt's ``audit.json`` from ``repository``'s
    own Consumer Config, via ``frontdoor.config.resolve_evidence`` (WI-WFC-
    BETA2-O5-001, D1 Option B's companion report-side change).

    ``--repository`` here names the CONSUMER repository, never the Evidence
    repository or an Attempt directly -- the positional ``source`` argument
    still does that. Refuses (``ReportError``) when the Evidence location
    cannot be resolved at all, when it holds no archived Attempt yet, and
    when it holds more than one: this command resolves *an* Attempt, singular,
    and never guesses which one among several."""

    try:
        root = repository_identity(Path(repository))
    except GitPreflightError as error:
        raise ReportError(str(error)) from error

    try:
        _path(config_relpath, context="--config")
    except CommissionError as error:
        raise ReportError(str(error)) from error

    config_file = root / config_relpath
    try:
        config_text = config_file.read_text(encoding="utf-8")
    except OSError as error:
        raise ReportError(
            f"cannot read Consumer Config '{config_file}': {error} (run `wfc init` first, or pass --config)"
        ) from error

    try:
        config = consumer_config_from_yaml(config_text)
        settings = resolve_evidence(config, root)
    except CommissionError as error:
        raise ReportError(f"cannot resolve the Evidence location from {root}'s Consumer Config: {error}") from error

    if not settings.path.is_dir():
        raise ReportError(
            f"unresolvable Evidence location: no Evidence repository at {settings.path} "
            "(run `wfc init` against it, or pass an Attempt directory or audit.json path directly as SOURCE)"
        )

    candidates = sorted(settings.path.glob("archive/**/audit.json"))
    if not candidates:
        raise ReportError(
            f"unresolvable Evidence location: no archived Attempt found under {settings.path} "
            "(pass an Attempt directory or audit.json path directly as SOURCE once a Run has been archived)"
        )
    if len(candidates) > 1:
        listed = ", ".join(str(candidate) for candidate in candidates)
        raise ReportError(
            f"ambiguous input: {len(candidates)} archived Attempts found under {settings.path}; "
            f"pass one explicitly as SOURCE instead of --repository: {listed}"
        )
    return candidates[0]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wfc report",
        description=(
            "Render the consolidated result of an archived Attempt from its audit projection. "
            "SOURCE (an archived Attempt directory, or an audit.json path) names the Evidence/"
            "Attempt location directly. --repository instead names the CONSUMER repository and "
            "resolves the Evidence location from its own Consumer Config, refusing if that "
            "resolves to zero or more than one archived Attempt. Pass exactly one of the two."
        ),
    )
    parser.add_argument("source", nargs="?", default=None,
                         help="an archived Attempt directory, or an audit.json path "
                              "(mutually exclusive with --repository)")
    parser.add_argument("--repository", default=None,
                         help="the CONSUMER repository whose own Consumer Config names the Evidence "
                              "location (mutually exclusive with SOURCE)")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                         help=f"Consumer Config path, relative to --repository; used only with "
                              f"--repository (default: {DEFAULT_CONFIG_PATH})")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args(argv)


def _resolve_source(args: argparse.Namespace) -> Path:
    if args.source is not None and args.repository is not None:
        raise ReportError(
            f"ambiguous input: pass either SOURCE ({args.source!r}) or --repository "
            f"({args.repository!r}), not both"
        )
    if args.source is not None:
        return Path(args.source)
    if args.repository is None:
        raise ReportError("either SOURCE or --repository is required")
    return resolve_attempt_from_repository(args.repository, args.config)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        source = _resolve_source(args)
        audit = _load_audit_source(source)
        report = build_report(audit)
    except ProjectionError as error:
        print(f"wfc report: {error}", file=sys.stderr)
        return 3
    except (ReportError, AuditError) as error:
        print(f"wfc report: {error}", file=sys.stderr)
        return 2
    print(render_report_json(report) if args.as_json else render_report_text(report), end="")
    return 0

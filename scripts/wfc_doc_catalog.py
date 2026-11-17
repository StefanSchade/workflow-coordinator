#!/usr/bin/env python3
"""Generate and query a local, non-authoritative documentation projection.

`generate`, `check` and `query` never read Git history, the network or the clock.
`provenance` is a separate, read-only, query-time report that does read Git
history (and the live documentation projection); its output is never committed.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = Path("build/wfc/document_catalog.jsonl")
LIMIT = 1024 * 1024
FIELDS = (
    "WorkItem",
    "Run",
    "Hop",
    "Candidate",
    "Type",
    "Status",
    "Authority",
    "Maturity",
    "Updated",
)
IDENTITY = FIELDS[:4]
META = {
    "generated": True,
    "derived": True,
    "authoritative": False,
    "source_declarations": "unverified source declarations",
}
TOKEN = re.compile(
    r"\b(?:(?:WI|RUN|INV|FTR|BUG|DEC)-[A-Za-z0-9][A-Za-z0-9-]*|H[0-9]{3,}|[0-9a-f]{40})\b", re.I
)
ATTR = re.compile(r"\{([^{}]+)\}")
LINK = re.compile(
    r'(?:link|xref):([^\s\[]+)\[[^\]]*\]|(?<!!)\[[^\]\n]*\]\(([^\s)]+)(?:\s+"[^"\n]*")?\)'
)


def project(root):
    records, unresolved = [], []
    for file in sorted((root / "docs").rglob("*")):
        if not file.is_file() or file.suffix not in (".adoc", ".md"):
            continue
        path = file.relative_to(root).as_posix()
        lines = file.read_text(encoding="utf-8").splitlines()
        title = section = None
        declarations, attributes = {}, {}
        in_header, current = True, None
        for line in lines:
            s = line.strip()
            if m := re.match(r"^:([\w-]+):\s*(.*)$", s):
                attributes[m[1]] = m[2]
                current = None
                continue
            if (
                not s
                or re.match(r"^(?:ifn?def|endif)::", s)
                or s.startswith(("[[", "anchor:", "//"))
            ):
                current = None
                continue
            heading = re.match(r"^(={1,6}|#{1,6})\s+(.+?)\s*#*$", s)
            if heading:
                level, value = heading.groups()
                if len(level) == 1 and title is None:
                    title = value
                elif len(level) > 1 and section is None:
                    section = value
                    in_header = False
                current = None
                continue
            # Any definition-list term starts a new declaration, even when
            # its label is outside the supported set or contains spaces.
            if in_header and (m := re.match(r"^([^:]+)::\s*(.*)$", s)):
                current = m[1]
                if current in FIELDS:
                    declarations.setdefault(current, []).append(m[2])
                continue
            if in_header and current and not re.match(r"^(?:\[|\* |\.|include::)", s):
                if current in FIELDS:
                    declarations[current][-1] += "\n" + line
                continue
            in_header = False
            current = None
        ids = set(TOKEN.findall(file.stem + " " + (title or "")))
        for key in IDENTITY:
            for value in declarations.get(key, []):
                ids.update(TOKEN.findall(value))
        links = set()
        for match in LINK.finditer("\n".join(lines)):
            target = match[1] or match[2]
            for _ in range(10):
                expanded = ATTR.sub(lambda m: attributes.get(m[1], m[0]), target)
                if expanded == target:
                    break
                target = expanded
            if ATTR.search(target):
                unresolved.append((path, target, "unresolved attribute"))
                continue
            try:
                parsed = urlsplit(target)
            except ValueError:
                unresolved.append((path, target, "malformed target"))
                continue
            if parsed.scheme or parsed.netloc or target.startswith("//"):
                continue
            target_path = unquote(parsed.path)
            if not target_path:
                continue
            if target_path.startswith("/"):
                unresolved.append((path, target, "absolute target"))
                continue
            resolved = Path(os.path.normpath(str(file.parent / target_path)))
            try:
                rel = resolved.relative_to(root).as_posix()
            except ValueError:
                unresolved.append((path, target, "outside repository"))
                continue
            links.add(rel)
            if not resolved.exists():
                unresolved.append((path, target, "missing target"))
        parts = Path(path).parts
        stem = file.stem
        date = re.search(r"\b\d{4}-\d{2}-\d{2}\b", stem.replace("_", " "))
        records.append(
            {
                "path": path,
                "title": title,
                "section_title": section,
                "home": parts[1] if len(parts) > 2 else "docs",
                "group": "/".join(parts[1:-1]),
                "name_tags": [x for x in re.split(r"[_\W]+", stem) if x],
                "date_in_name": date.group() if date else None,
                "ids": sorted(ids),
                "source_declarations": declarations,
                "links": sorted(links),
            }
        )
    return records, sorted(set(unresolved))


def encode(records):
    data = "".join(
        json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n" for x in [META, *records]
    ).encode("utf-8")
    if len(data) > LIMIT:
        raise ValueError(f"catalog is {len(data)} bytes; limit is {LIMIT}")
    return data


def generate(output, data):
    if output.exists() and output.read_bytes() == data:
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output.parent, prefix=".catalog-", delete=False
        ) as stream:
            name = stream.name
            stream.write(data)
        os.replace(name, output)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)
    return True


def read_catalog(output):
    if not output.exists():
        raise ValueError("catalog missing; run python3 scripts/wfc_doc_catalog.py generate")
    data = output.read_bytes()
    rows = [json.loads(line) for line in data.splitlines()]
    if not rows or rows[0] != META:
        raise ValueError("invalid catalog metadata; regenerate the catalog")
    return data, rows[1:]


def memory_paths(root):
    def walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k == "source_path" and isinstance(v, str):
                    yield v
                else:
                    yield from walk(v)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)

    for path in sorted((root / "project_memory/canonical").glob("*.json")):
        yield from walk(json.loads(path.read_text(encoding="utf-8")))


def diagnostics(root, records, unresolved):
    for path, target, reason in unresolved:
        print(f"link: {path}: {target} ({reason})")
    paths = {r["path"] for r in records}
    for path in sorted(set(memory_paths(root)) - paths):
        print(f"Project Memory source_path not in catalog: {path}")
    print(
        f"Informational: {len(unresolved)} unresolved links; "
        f"{len(set(memory_paths(root)) - paths)} Project Memory source_path misses"
    )


# ---------------------------------------------------------------------------
# Provenance: read-only, query-time joins over Git history and the live
# projection. Observed facts, derived candidates and unlinked material are kept
# apart; nothing here is written, committed or treated as authority.
# ---------------------------------------------------------------------------

COMMISSIONS = "governance/commissions"
WI = r"WI-[A-Z0-9]+(?:-[A-Z0-9]+)*"
HOP = r"H[0-9]{3}"
WI_RE = re.compile(rf"(?<![A-Za-z0-9-])({WI})(?![A-Za-z0-9-])")
HOP_RE = re.compile(rf"(?<![A-Za-z0-9-])({HOP})(?![A-Za-z0-9])")
BRACKETED = re.compile(rf"\[({WI})\s+({HOP})\]")
SCOPED = re.compile(rf"\(({WI})[/,]({HOP})\)")
ADJACENT = re.compile(rf"(?<![A-Za-z0-9-])({WI})\s+({HOP})(?![A-Za-z0-9])")
# The three pinned forms; the others are separately named so nothing is dropped.
FORMS = ("bracketed", "trailing", "mid_subject", "scoped", "split", "workitem_only")
RUN_RE = re.compile(r"\bRUN-[0-9a-f]{32}\b")
SHA_RE = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{7,40}(?![0-9A-Za-z])")
RANGE_RE = re.compile(r"([0-9a-f]{7,40})\.\.\.?([0-9a-f]{7,40})")
RUN_LABEL = re.compile(r"^(?:Re)?(?:review Hop identity|Run)$", re.I)
HOP_LABEL = re.compile(r"^Hop(?: / role)?$")
CANDIDATE_LABEL = re.compile(r"^(?:corrected |reviewed )?candidate\b(?!.*range$)", re.I)
RANGE_LABEL = re.compile(r"\brange$", re.I)
PROSE_PREAMBLE = {
    "runs": re.compile(r"\bRun\s+`?(RUN-[0-9a-f]{32})"),
    "hops": re.compile(rf"\bHop\s+`?({HOP})\b"),
    "candidates": re.compile(r"\bCandidate\s+`?([0-9a-f]{7,40})\b"),
    "baselines": re.compile(r"\breviewed against\s+`?([0-9a-f]{7,40})\b"),
    "verdict": re.compile(r"\bDisposition:\s*\*?([A-Za-z_ ]+?)\*?[,.]"),
}
DEF_LINE = re.compile(r"^([A-Z][^:\n]{0,60}?)::(?:\s+(.*))?$")
VERDICT_HEADING = re.compile(r"^(?:[\w-]+ )?(?:re)?(?:review )?(?:verdict|disposition)$", re.I)
FINDING_HEADING = re.compile(
    r"^(?:Finding\s+)?([A-Z]{1,6}-?[0-9]{1,3}(?:-[A-Z]{1,3}[0-9]{1,3})?)(?=\s|:|$)"
)
PATH_CITE = re.compile(
    r"`((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+|[A-Za-z0-9_.-]+\.(?:py|adoc|md|yaml|json))"
    r"(?::[0-9,-]+)?`"
)


def git(root, *args):
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
    try:
        done = subprocess.run(
            ["git", "-c", "core.quotepath=off", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
    except OSError as exc:
        raise ValueError(f"git unavailable: {exc}") from exc
    if done.returncode:
        raise ValueError(f"git {args[0]} failed: {done.stderr.strip() or done.returncode}")
    return done.stdout


def parse_commits(text):
    commits = []
    for record in text.split("\x1e"):
        record = record.strip("\n")
        if record:
            sha, date, subject, body = (record.split("\x1f", 3) + [""])[:4]
            commits.append({"sha": sha, "date": date, "subject": subject, "body": body})
    return commits


COMMIT_FORMAT = "--format=%H%x1f%cI%x1f%s%x1f%b%x1e"


def body_mentions(commits, wi):
    """Commits whose subject has no token for `wi` but whose body names it (older convention)."""
    pattern = re.compile(rf"(?<![A-Za-z0-9-]){re.escape(wi)}(?![A-Za-z0-9-])")
    found = []
    for c in commits:
        if pattern.search(c.get("body", "")) and not any(
            t["work_item"] == wi for t in subject_tokens(c["subject"])
        ):
            found.append({**c, "runs": sorted(set(RUN_RE.findall(c["body"])))})
    return found


def history(root):
    git(root, "rev-parse", "--verify", "HEAD")
    return parse_commits(git(root, "log", COMMIT_FORMAT))


def subject_tokens(subject):
    """Return every WorkItem token of a subject with its Hop and form."""
    used, found = [], []

    def free(span):
        return not any(span[0] < u[1] and u[0] < span[1] for u in used)

    for pattern, form in ((BRACKETED, None), (SCOPED, "scoped"), (ADJACENT, None)):
        for m in pattern.finditer(subject):
            if not free(m.span()):
                continue
            used.append(m.span())
            kind = form or (
                "bracketed"
                if pattern is BRACKETED
                else ("trailing" if not subject[m.end() :].strip() else "mid_subject")
            )
            found.append((m.start(), {"work_item": m[1], "hop": m[2], "form": kind}))
    rest = [m for m in WI_RE.finditer(subject) if free(m.span())]
    hops = [m for m in HOP_RE.finditer(subject) if free(m.span())]
    for m in rest:
        split = len(rest) == 1 and len(hops) == 1
        found.append(
            (
                m.start(),
                {
                    "work_item": m[1],
                    "hop": hops[0][1] if split else None,
                    "form": "split" if split else "workitem_only",
                },
            )
        )
    return [token for _, token in sorted(found, key=lambda x: x[0])]


def token_index(commits):
    index = {}
    for commit in commits:
        for token in subject_tokens(commit["subject"]):
            index.setdefault(token["work_item"], []).append({**commit, **token})
    return index


def yaml_scalar(text, key):
    m = re.search(rf"^{key}:\s*['\"]?([^\s'\"#]+)", text, re.M)
    return m[1] if m else None


def yaml_items(text, key):
    """Return `- item` values under a top-level key (and nested lists beneath it)."""
    items, active = [], False
    for line in text.splitlines():
        if re.match(rf"^{key}:", line):
            active = True
            continue
        if active and line and not line[0].isspace() and not line.startswith("- "):
            break
        if active and (m := re.match(r"^\s*-\s+['\"]?([^'\"#\n]+?)['\"]?\s*$", line)):
            items.append(m[1])
    return items


def commission_dirs(root):
    base = root / COMMISSIONS
    result = []
    for directory in sorted(p for p in base.glob("*") if p.is_dir()) if base.is_dir() else []:
        rel = directory.relative_to(root).as_posix()
        item = directory / "work-item.yaml"
        text = item.read_text(encoding="utf-8") if item.is_file() else ""
        result.append(
            {
                "dir": rel,
                "work_item": yaml_scalar(text, "work_item_id"),
                "schema_version": yaml_scalar(text, "schema_version"),
                "program": next(
                    (x.split("=", 1)[1] for x in yaml_items(text, "profile_labels")
                     if x.startswith("program_id=")),
                    None,
                ),
                "revision": next(
                    (x.split("=", 1)[1] for x in yaml_items(text, "profile_labels")
                     if x.startswith("program_revision=")),
                    None,
                ),
                "scope": yaml_items(text, "scope"),
                "artifacts": yaml_items(text, "durable_artifacts"),
            }
        )
    return result


def commissioning_commits(root, commits):
    """Map each Commission directory to the oldest commit that added a file in it."""
    out = git(root, "log", "--reverse", "--diff-filter=A", "--name-only", "--format=%x1e%H", "--", COMMISSIONS)
    by_sha = {c["sha"]: c for c in commits}
    first = {}
    for record in out.split("\x1e"):
        lines = [x for x in record.splitlines() if x]
        if not lines:
            continue
        for name in lines[1:]:
            parts = name.split("/")
            if len(parts) >= 4:
                first.setdefault("/".join(parts[:3]), by_sha.get(lines[0]))
    return first


def def_fields(text):
    """Yield (label, value, line_no) for definition-list terms with continuation lines."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = DEF_LINE.match(line.strip()) if not line.startswith(("|", " ")) else None
        if not m:
            continue
        value = [m[2] or ""]
        for nxt in lines[i + 1 :]:
            if not nxt.strip() or DEF_LINE.match(nxt.strip()) or re.match(r"^(={1,6}|#{1,6})\s", nxt):
                break
            value.append(nxt.strip())
        yield m[1], " ".join(x for x in value if x), i + 1


def normalize_verdict(raw):
    text = re.sub(r"[*`]", "", raw).strip().lower()
    if re.match(r"accept(ed)?\b", text):
        return "accept"
    if re.match(r"changes[_ ]required\b", text):
        return "changes_required"
    return None


def review_record(text):
    """Targeted, independently tested extraction of one review document's identity.

    Only these rules are applied; everything else is listed as unsupported.
    """
    run, hops, candidates, ranges, verdicts, other = [], [], [], [], [], {}
    for label, value, line in def_fields(text):
        if RUN_LABEL.match(label):
            run += RUN_RE.findall(value)
            hops += re.findall(rf"\b{HOP}\b", value)
        elif HOP_LABEL.match(label):
            hops += re.findall(rf"\b{HOP}\b", value)
        elif RANGE_LABEL.search(label):
            ranges += [f"{a}..{b}" for a, b in RANGE_RE.findall(value)]
        elif CANDIDATE_LABEL.match(label):
            candidates += SHA_RE.findall(value)
        elif label in ("Verdict", "Outcome", "Disposition"):
            kind = normalize_verdict(value)
            if label == "Disposition" and kind is None:
                key = "Disposition (finding-level or free text)"
                other[key] = other.get(key, 0) + 1
                continue
            grade = bool(re.match(r"^\W*[A-Z]\s+--", value))
            verdicts.append(
                {"source": label + "::", "line": line, "raw": value, "verdict": kind,
                 "note": "grade-scale verdict; meaning not mapped" if grade else None}
            )
        elif label != "WorkItem":
            other[label] = other.get(label, 0) + 1
    preamble = "\n".join(text.splitlines()[:14])
    prose = not run and not verdicts and bool(PROSE_PREAMBLE["runs"].search(preamble))
    if prose:
        run += PROSE_PREAMBLE["runs"].findall(preamble)
        hops += PROSE_PREAMBLE["hops"].findall(preamble)
        candidates += PROSE_PREAMBLE["candidates"].findall(preamble)
        baseline = PROSE_PREAMBLE["baselines"].findall(preamble)
        if baseline and candidates:
            ranges += [f"{baseline[0]}..{candidates[0]}"]
        if m := PROSE_PREAMBLE["verdict"].search(preamble):
            verdicts.append({"source": "prose preamble", "line": 1, "raw": m[1],
                             "verdict": normalize_verdict(m[1]), "note": None})
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^={2,3}\s+(.+?)\s*$", line)
        if m and VERDICT_HEADING.match(m[1]):
            body = next((x.strip() for x in lines[i + 1 : i + 6] if x.strip()), "")
            kind = normalize_verdict(body)
            # Only an explicit accept/changes-required statement counts; a bare
            # finding-level "*Closed.*"/"*OPEN.*" under a Disposition heading does not.
            if kind:
                verdicts.append(
                    {"source": "== " + m[1], "line": i + 2, "raw": body, "verdict": kind, "note": None}
                )
    unique = lambda xs: sorted(set(xs))  # noqa: E731
    labels, seen = [], {}
    for i, line in enumerate(lines):
        m = re.match(r"^={2,5}\s+(.+?)\s*$", line)
        if m and (f := FINDING_HEADING.match(m[1])):
            labels.append(f[1])
            seen[f[1]] = seen.get(f[1], 0) + 1
    shape = "prose-preamble" if prose and not verdicts else "none"
    if verdicts:
        first = verdicts[0]
        shape = (
            "grade" if first["note"]
            else "modern" if first["source"] == "Verdict::" and re.search(r"^WorkItem::", text, re.M)
            else "outcome-header" if first["source"] == "Outcome::"
            else "verdict-header" if first["source"] in ("Verdict::", "Disposition::")
            else "prose-preamble" if first["source"] == "prose preamble"
            else "verdict-section"
        )
    return {
        "shape": shape,
        "runs": unique(run),
        "hops": unique(hops),
        "candidates": unique(candidates),
        "ranges": unique(ranges),
        "verdicts": verdicts,
        "finding_labels": labels,
        "duplicate_finding_labels": sorted(k for k, v in seen.items() if v > 1),
        "unsupported_labels": dict(sorted(other.items())),
    }


def resolve_sha(token, commits):
    matches = [c for c in commits if c["sha"].startswith(token)]
    if len(matches) == 1:
        return {"status": "found", "sha": matches[0]["sha"], "subject": matches[0]["subject"]}
    if matches:
        return {"status": "ambiguous", "matches": len(matches)}
    return {"status": "not_in_history"}


def is_review_like(path, record):
    return "review" in Path(path).stem.lower() or bool(record["verdicts"])


def document_relations(wi, dirs, records):
    """Documents tied to a WorkItem by K1 identity ids or a declared durable artifact."""
    by_path = {r["path"]: r for r in records}
    relations, missing = {}, []
    for r in records:
        if wi.casefold() in {x.casefold() for x in r["ids"]}:
            relations.setdefault(r["path"], []).append("identity (K1 ids)")
    for d in dirs:
        for artifact in d["artifacts"]:
            if not artifact.endswith((".adoc", ".md")):
                continue
            if artifact in by_path:
                relations.setdefault(artifact, []).append("declared durable artifact")
            else:
                moved = sorted(p for p in by_path if Path(p).name == Path(artifact).name)
                missing.append({"declared": artifact, "same_filename_now_at": moved})
    return relations, missing


def work_item_report(root, wi, commits=None, records=None):
    commits = history(root) if commits is None else commits
    records = project(root)[0] if records is None else records
    tokens = token_index(commits).get(wi, [])
    dirs = [d for d in commission_dirs(root) if d["work_item"] == wi]
    first = commissioning_commits(root, commits)
    relations, missing = document_relations(wi, dirs, records)
    docs, runs = [], {}
    for path, why in sorted(relations.items()):
        record = review_record((root / path).read_text(encoding="utf-8"))
        docs.append({"path": path, "relation": " + ".join(why), "review": record})
        for run in record["runs"]:
            runs.setdefault(run, []).append(path)
    pattern = re.compile(rf"(?<![A-Za-z0-9-]){re.escape(wi)}(?![A-Za-z0-9-])")
    known = set(relations)
    mentions = []
    for base in ("docs", "project_memory"):
        for file in sorted((root / base).rglob("*")) if (root / base).is_dir() else []:
            rel = file.relative_to(root).as_posix()
            if file.suffix in (".adoc", ".md", ".json") and file.is_file() and rel not in known:
                if pattern.search(file.read_text(encoding="utf-8", errors="replace")):
                    mentions.append(rel)
    for rel in list(mentions):
        if rel.endswith((".adoc", ".md")) and "review" in Path(rel).stem.lower():
            mentions.remove(rel)
            docs.append(
                {
                    "path": rel,
                    "relation": "text mention in a review-like filename (derived; may concern other WorkItems)",
                    "review": review_record((root / rel).read_text(encoding="utf-8")),
                }
            )
    associations = []
    for doc in docs:
        rec = doc["review"]
        for kind, values in (
            ("candidate", rec["candidates"]),
            ("range", [x for r in rec["ranges"] for x in r.split("..")]),
        ):
            for value in values:
                res = resolve_sha(value, commits)
                note = None
                if res["status"] == "found":
                    attached = [t["form"] for t in subject_tokens(res["subject"]) if t["work_item"] == wi]
                    note = "subject carries this WorkItem" if attached else "subject carries no token for this WorkItem"
                associations.append({"document": doc["path"], "kind": kind, "value": value, **res, "note": note})
    exclusive = sorted({d["dir"] for d in dirs} | set(relations))
    shared = sorted(
        {
            x
            for d in dirs
            for x in d["scope"]
            if x.startswith("docs/backlog/") and x.endswith((".adoc", ".md")) and Path(x).name != "_open.adoc"
        }
        - set(exclusive)
    )
    commissioning = [first[d["dir"]] for d in dirs if first.get(d["dir"])]
    bodies = body_mentions(commits, wi)
    skip = {c["sha"] for c in commissioning} | {t["sha"] for t in tokens} | {b["sha"] for b in bodies}

    def untokened(paths, after=None):
        touching, elsewhere = [], 0
        if not paths:
            return touching, elsewhere
        for c in parse_commits(git(root, "log", COMMIT_FORMAT, "--", *paths)):
            if c["sha"] in skip or (after and c["date"] < after):
                continue
            if subject_tokens(c["subject"]):
                elsewhere += 1
            else:
                touching.append(c)
        return touching, elsewhere

    unlinked, other = untokened(exclusive)
    since = min((c["date"] for c in commissioning), default=None)
    weak, weak_other = untokened(shared, since)
    return {
        "work_item": wi,
        "candidate_commit": commits[0] if commits else None,
        "commission": [{**d, "commissioning_commit": first.get(d["dir"])} for d in dirs],
        "commits": tokens,
        "body_commits": bodies,
        "documents": docs,
        "declared_missing": missing,
        "body_mentions": mentions,
        "runs": runs,
        "associations": associations,
        "unlinked_touching": unlinked,
        "touching_other_workitem": other,
        "exclusive_paths": exclusive,
        "shared_records": shared,
        "shared_untokened_after_commissioning": list(reversed(weak)),
        "shared_other_workitem": weak_other,
    }


def short(sha):
    return sha[:10] if sha else "-"


def render_work_item(report):
    wi, head = report["work_item"], report["candidate_commit"]
    out = [f"WorkItem: {wi}", f"Candidate commit: {short(head['sha'])} {head['date']} {head['subject']}"]
    out.append("Legend: observed = read from a file or subject; derived = candidate leads, not attribution.")
    out.append("== Commission (observed: work-item.yaml work_item_id; commit = oldest add under the directory)")
    if not report["commission"]:
        out.append("  no Commission directory carries this identifier at the candidate commit")
    for d in report["commission"]:
        c = d["commissioning_commit"]
        out.append(f"  directory: {d['dir']} program={d['program']}@{d['revision']} schema={d['schema_version']}")
        if c:
            has = "token in subject" if subject_tokens(c["subject"]) else "subject carries no WorkItem token"
            out.append(f"  commissioning commit: {short(c['sha'])} {c['date']} {c['subject']} ({has})")
        else:
            out.append("  commissioning commit: not found")
    out.append(f"== Commits whose subject carries the identifier (observed; {len(report['commits'])})")
    for form in FORMS:
        for c in report["commits"]:
            if c["form"] == form:
                out.append(f"  {form:13} {c['hop'] or '-':4} {short(c['sha'])} {c['date']} {c['subject']}")
    out.append(f"== Commits whose body, not subject, names the identifier (observed text; older convention; {len(report['body_commits'])})")
    for c in report["body_commits"]:
        runs = f" runs named in body: {', '.join(c['runs'])}" if c["runs"] else ""
        out.append(f"  {short(c['sha'])} {c['date']} {c['subject'][:80]}{runs}")
    out.append(f"== Documents (observed: K1 identity ids and/or work-item.yaml durable artifacts; {len(report['documents'])})")
    for d in report["documents"]:
        r = d["review"]
        out.append(f"  {d['path']} [{d['relation']}]")
        out.append(f"    shape={r['shape']} runs={r['runs'] or '-'} hops={r['hops'] or '-'} candidates={[short(x) for x in r['candidates']] or '-'}")
        if r["ranges"]:
            out.append(f"    ranges={[x for x in r['ranges']]}")
        for v in r["verdicts"]:
            out.append(f"    verdict[{v['source']} L{v['line']}] normalized={v['verdict'] or 'unrecognized'} raw={v['raw'][:70]!r}" + (f" ({v['note']})" if v["note"] else ""))
        if r["finding_labels"]:
            out.append(f"    finding headings: {r['finding_labels']}" + (f" duplicate={r['duplicate_finding_labels']}" if r["duplicate_finding_labels"] else ""))
        if not r["verdicts"]:
            out.append("    no verdict extracted (not a review shape this tool knows, or none present)")
        if r["unsupported_labels"]:
            out.append(f"    unsupported labels (not extracted): {dict(list(r['unsupported_labels'].items())[:8])}")
    for m in report["declared_missing"]:
        found = ", ".join(m["same_filename_now_at"]) or "not found"
        out.append(f"  declared artifact absent at candidate commit: {m['declared']} (derived: same filename now at: {found})")
    out.append(f"== Documents mentioning the identifier elsewhere in docs/ or project_memory/ (observed text match; {len(report['body_mentions'])})")
    out += [f"  {p}" for p in report["body_mentions"]]
    out.append("== Run identifiers (observed in review headers; Runs named in commit bodies listed above, unverified)")
    out += [f"  {run}: {', '.join(paths)}" for run, paths in sorted(report["runs"].items())] or ["  none extracted"]
    out.append("== Candidate and range commits from review headers (derived by resolving against history)")
    for a in report["associations"]:
        extra = a.get("note") or (f"{a['matches']} matches" if a["status"] == "ambiguous" else "")
        out.append(f"  {a['document']} {a['kind']} {a['value'][:12]}: {a['status']} {extra}".rstrip())
    if not report["associations"]:
        out.append("  none")
    out.append(f"== Unlinked: commits without any WorkItem token touching this WorkItem's own paths (derived; {len(report['unlinked_touching'])})")
    for c in report["unlinked_touching"]:
        out.append(f"  {short(c['sha'])} {c['date']} {c['subject']}")
    out.append(f"  not attributed: {report['touching_other_workitem']} further commit(s) touching these paths carry another WorkItem token")
    out.append("  own paths: " + (", ".join(report["exclusive_paths"]) or "none"))
    weak = report["shared_untokened_after_commissioning"]
    out.append(f"== Weak candidates: untokened commits after commissioning touching shared records named in scope ({len(weak)}; oldest 6 shown)")
    out += [f"  {short(c['sha'])} {c['date']} {c['subject']}" for c in weak[:6]]
    out.append("  shared records: " + (", ".join(report["shared_records"]) or "none"))
    out.append("== Limits: an untokened commit touching none of these paths cannot be linked; acceptance or promotion commits")
    out.append("   appear only as weak candidates via shared records; absence of a link reflects era conventions, not a defect.")
    return "\n".join(out)


def review_leads(root, path, commits=None, records=None, limit=8):
    commits = history(root) if commits is None else commits
    records = project(root)[0] if records is None else records
    file = root / path
    if not file.is_file():
        raise ValueError(f"no such document: {path}")
    text = file.read_text(encoding="utf-8")
    added = parse_commits(git(root, "log", "--follow", "--diff-filter=A", COMMIT_FORMAT, "--", path))
    since = added[-1] if added else None
    cited = sorted(set(m[1] for m in PATH_CITE.finditer(text)))
    existing = [c for c in cited if (root / c).exists()]
    later = {}
    for c in existing:
        spec = f"{since['sha']}..HEAD" if since else "HEAD"
        later[c] = parse_commits(git(root, "log", COMMIT_FORMAT, spec, "--", c))
    return {
        "path": path,
        "record": review_record(text),
        "added_by": since,
        "cited_paths_present": existing,
        "cited_paths_absent": [c for c in cited if c not in existing],
        "later_commits": {k: {"count": len(v), "newest": v[:limit]} for k, v in later.items()},
        "linked_from": sorted(r["path"] for r in records if path in r["links"]),
    }


def render_leads(report):
    r = report["record"]
    out = [f"Review: {report['path']}", "Derived leads only; they are not evidence that anything was addressed."]
    a = report["added_by"]
    out.append("added by (oldest add following renames): " + (f"{short(a['sha'])} {a['date']} {a['subject']}" if a else "not found"))
    out.append(f"shape={r['shape']} runs={r['runs'] or '-'} hops={r['hops'] or '-'} candidates={[short(x) for x in r['candidates']] or '-'}")
    out.append(f"finding headings: {r['finding_labels'] or 'none extracted'}")
    out.append("cited paths present at candidate commit: " + (", ".join(report["cited_paths_present"]) or "none"))
    out.append("cited paths absent at candidate commit (moved, removed, renamed or not a path): " + (", ".join(report["cited_paths_absent"]) or "none"))
    for p, info in sorted(report["later_commits"].items()):
        out.append(f"later commits on {p}: {info['count']}")
        out += [f"  {short(c['sha'])} {c['date']} {c['subject']}" for c in info["newest"]]
    out.append("linked from (K1 live inbound links): " + (", ".join(report["linked_from"]) or "none"))
    return "\n".join(out)


CARVEOUT_MAP = "docs/cold_storage/sources/pre-0.1.0b1/provenance/incubator-commit-map.tsv"


def carveout_stats(root, commits):
    """Read the recorded filter-repo old->new commit map (observed; absent map -> None)."""
    file = root / CARVEOUT_MAP
    if not file.is_file():
        return None
    rows = [x.split() for x in file.read_text(encoding="utf-8").splitlines()[1:] if x.strip()]
    mapped = {r[1] for r in rows if len(r) > 1 and set(r[1]) != {"0"}}
    return {
        "old_commits": len(rows),
        "mapped_to_new": len(mapped),
        "mapped_in_history": len(mapped & {c["sha"] for c in commits}),
    }


def coverage_report(root, commits=None, records=None):
    commits = history(root) if commits is None else commits
    records = project(root)[0] if records is None else records
    index = token_index(commits)
    dirs = commission_dirs(root)
    first = commissioning_commits(root, commits)
    forms = {f: 0 for f in FORMS}
    by_month = {}
    for c in commits:
        for t in subject_tokens(c["subject"]):
            forms[t["form"]] += 1
            by_month.setdefault(c["date"][:7], {}).setdefault(t["form"], 0)
            by_month[c["date"][:7]][t["form"]] += 1
    ids = {d["work_item"] for d in dirs if d["work_item"]}
    review_docs = {}
    for r in records:
        rec = review_record((root / r["path"]).read_text(encoding="utf-8"))
        if is_review_like(r["path"], rec):
            review_docs[r["path"]] = rec
    per = []
    for d in dirs:
        wi = d["work_item"]
        toks = index.get(wi, []) if wi else []
        owned = []
        if wi:
            related, _ = document_relations(wi, [d], records)
            owned = [p for p in related if p in review_docs]
        runs = sorted({x for p in owned for x in review_docs[p]["runs"]})
        cands = [
            x
            for p in owned
            for x in review_docs[p]["candidates"] + [e for r in review_docs[p]["ranges"] for e in r.split("..")]
        ]
        per.append(
            {
                "dir": d["dir"],
                "work_item": wi,
                "commissioning_commit": bool(first.get(d["dir"])),
                "commissioning_token": bool(first.get(d["dir"]) and subject_tokens(first[d["dir"]]["subject"])),
                "token_commits": len(toks),
                "review_docs": len(owned),
                "runs": len(runs),
                "candidates": len(cands),
                "candidates_resolved": sum(resolve_sha(x, commits)["status"] == "found" for x in cands),
            }
        )
    shapes = {}
    for rec in review_docs.values():
        shapes[rec["shape"]] = shapes.get(rec["shape"], 0) + 1
    programs = {}
    for d in dirs:
        key = f"schema {d['schema_version']} {d['program']}@{d['revision']}"
        programs[key] = programs.get(key, 0) + 1
    dates = [c["date"] for c in commits]
    first_dates = sorted(c["date"] for c in first.values() if c)
    all_tokens = sum(1 for c in commits if subject_tokens(c["subject"]))
    body_only = [c for c in commits if not subject_tokens(c["subject"]) and WI_RE.search(c["body"])]
    return {
        "commits_body_only_token": len(body_only),
        "commits_body_only_with_run": sum(bool(RUN_RE.search(c["body"])) for c in body_only),
        "candidate_commit": commits[0],
        "commits": len(commits),
        "period": [min(dates), max(dates)],
        "commits_with_token": all_tokens,
        "forms": forms,
        "forms_by_month": by_month,
        "distinct_work_items_in_subjects": len(index),
        "subject_work_items_without_directory": len(set(index) - ids),
        "commission_dirs": len(dirs),
        "carveout": carveout_stats(root, commits),
        "commissioning_period": [first_dates[0], first_dates[-1]] if first_dates else None,
        "per_commission": per,
        "governance_generations": programs,
        "review_documents": len(review_docs),
        "review_shapes": dict(sorted(shapes.items())),
        "reviews_with_run": sum(bool(r["runs"]) for r in review_docs.values()),
        "reviews_with_candidate": sum(bool(r["candidates"] or r["ranges"]) for r in review_docs.values()),
        "reviews_with_recognized_verdict": sum(any(v["verdict"] for v in r["verdicts"]) for r in review_docs.values()),
        "reviews_with_finding_headings": sum(bool(r["finding_labels"]) for r in review_docs.values()),
    }


def render_coverage(c):
    head, per = c["candidate_commit"], c["per_commission"]
    n = len(per) or 1

    def pct(k):
        count = sum(1 for p in per if k(p))
        return f"{count}/{len(per)} ({100 * count // n}%)"

    out = [
        "Coverage (query-time; depends on Git history; not committed)",
        f"Candidate commit: {head['sha']} {head['date']} {head['subject']}",
        f"Commission population: {c['commission_dirs']} directories under {COMMISSIONS}/ at the candidate commit",
        f"History: {c['commits']} commits, {c['period'][0]} .. {c['period'][1]}; commissioning commits {c['commissioning_period'] and (c['commissioning_period'][0] + ' .. ' + c['commissioning_period'][1])}",
        "Repository generations (documented, not derived): spreadsheet-handling incubator -> git filter-repo carve-out",
        "  (docs/cold_storage/sources/pre-0.1.0b1/provenance/carveout.yaml, incubator-commit-map.tsv) -> standalone repository.",
        "  Earlier work predates both the carve-out and the Commission directories and cannot be joined to a Commission directory.",
        "  Recorded carve-out map: "
        + (
            f"{c['carveout']['old_commits']} incubator commits, {c['carveout']['mapped_to_new']} mapped to rewritten commits, "
            f"{c['carveout']['mapped_in_history']} of those present in this history"
            if c["carveout"]
            else "not present"
        ),
        "Governance generations (observed in work-item.yaml): "
        + "; ".join(f"{k}: {v}" for k, v in sorted(c["governance_generations"].items())),
        f"Subject tokens: {c['commits_with_token']} of {c['commits']} commits carry >=1 WorkItem token; forms {c['forms']}",
        f"  {c['commits_body_only_token']} further commits name a WorkItem only in the message body (older convention);"
        f" {c['commits_body_only_with_run']} of them also name a Run identifier there (observed text, not verified)",
        f"  {c['distinct_work_items_in_subjects']} distinct WorkItem identifiers appear in subjects; {c['subject_work_items_without_directory']} have no Commission directory (older conventions; not a defect)",
        "Per Commission directory:",
        f"  commissioning commit found: {pct(lambda p: p['commissioning_commit'])}; its subject carries a token: {pct(lambda p: p['commissioning_token'])}",
        f"  >=1 token commit: {pct(lambda p: p['token_commits'])}; >=1 review-like document (identity or declared artifact): {pct(lambda p: p['review_docs'])}",
        f"  >=1 Run identifier from headers: {pct(lambda p: p['runs'])}; >=1 candidate SHA resolved in history: {pct(lambda p: p['candidates_resolved'])}",
        f"Review-like documents under docs/ (live projection): {c['review_documents']}; shapes {c['review_shapes']}",
        f"  with Run id: {c['reviews_with_run']}; with candidate or range SHA: {c['reviews_with_candidate']}; with recognized verdict: {c['reviews_with_recognized_verdict']}; with finding headings: {c['reviews_with_finding_headings']}",
        "Cannot be linked mechanically: commit -> Run identifier (absent from recent subjects; some older bodies name one); acceptance/promotion commits without a token",
        "  (unless they touch a shared record named in scope); finding identity across reviews (labels are review-local); documents without a review",
        "  header; anything from the Evidence repository (outside this repository). Missing links reflect era conventions and the",
        "  filter-repo carve-out, not a present process defect. No global linkage of unidentifiable commits is claimed.",
        "Reproduce: git rev-parse HEAD; git log --format='%H %cI %s'; git log --reverse --diff-filter=A --name-only -- governance/commissions;",
        "  python3 scripts/wfc_doc_catalog.py provenance --coverage",
    ]
    return "\n".join(out)


def provenance_command(root, args):
    if args.coverage:
        report = coverage_report(root)
        text = render_coverage(report)
    elif args.review:
        report = review_leads(root, args.review, limit=args.limit)
        text = render_leads(report)
    else:
        report = work_item_report(root, args.work_item)
        text = render_work_item(report)
    print(json.dumps(report, ensure_ascii=False, indent=1) if args.json else text)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("generate")
    sub.add_parser("check")
    query = sub.add_parser("query")
    query.add_argument("--live", action="store_true")
    filters = query.add_mutually_exclusive_group(required=True)
    for option in ("id", "title", "refs-to", "refs-from"):
        filters.add_argument("--" + option)
    prov = sub.add_parser("provenance", help="read-only Git/Commission/review join (query-time)")
    prov.add_argument("--json", action="store_true")
    prov.add_argument("--limit", type=int, default=8, help="later commits shown per cited path")
    target = prov.add_mutually_exclusive_group(required=True)
    target.add_argument("--work-item")
    target.add_argument("--review", help="repository-relative review document path")
    target.add_argument("--coverage", action="store_true")
    args = parser.parse_args(argv)
    output = ROOT / OUTPUT
    try:
        if args.command == "generate":
            records, _ = project(ROOT)
            data = encode(records)
            changed = generate(output, data)
            print(
                f"{len(records)} records, {len(data)} bytes; "
                + ("written" if changed else "unchanged")
            )
        elif args.command == "provenance":
            return provenance_command(ROOT, args)
        elif args.command == "check":
            old, previous = read_catalog(output)
            records, unresolved = project(ROOT)
            fresh = encode(records)
            before = {r["path"]: r for r in previous}
            after = {r["path"]: r for r in records}
            for label, paths in [
                ("added", after.keys() - before.keys()),
                ("removed", before.keys() - after.keys()),
                ("changed", {p for p in before.keys() & after.keys() if before[p] != after[p]}),
            ]:
                for path in sorted(paths):
                    print(f"{label}: {path}")
            diagnostics(ROOT, records, unresolved)
            print("catalog " + ("current" if old == fresh else "drifted"))
            return int(old != fresh)
        else:
            records = project(ROOT)[0] if args.live else read_catalog(output)[1]
            for r in records:
                match = (
                    args.id
                    and any(args.id.casefold() == x.casefold() for x in r["ids"])
                    or args.title
                    and args.title.casefold() in (r["title"] or "").casefold()
                    or args.refs_to
                    and args.refs_to in r["links"]
                    or args.refs_from
                    and args.refs_from == r["path"]
                )
                if match:
                    print(json.dumps(r, ensure_ascii=False))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"catalog: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

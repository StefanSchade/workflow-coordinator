#!/usr/bin/env python3
"""Generate and query a local, non-authoritative documentation projection."""

import argparse
import json
import os
from pathlib import Path
import re
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

import pathlib
import sys


def main() -> int:
    docs = sorted(pathlib.Path("docs").rglob("*.adoc"))
    if not docs:
        print("checks/doc_titles.py: no *.adoc file found under docs/", file=sys.stderr)
        return 1
    for path in docs:
        first_line = path.read_text(encoding="utf-8").splitlines()[:1]
        if not first_line or not first_line[0].startswith("= "):
            print(f"checks/doc_titles.py: {path} does not start with '= '", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

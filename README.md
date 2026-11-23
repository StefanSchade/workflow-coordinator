# Workflow Coordinator

Workflow Coordinator is a Git-based coordinator for controlled agentic
execution: explicit authorization boundaries, independent review, trusted
commits and recorded run evidence. This first beta is qualified and
practically focused on software-development workflows, on one qualified
platform (CPython 3.13, Linux x86-64). Its core mechanics are designed to
stay domain-neutral for work on versioned artifacts more broadly -- an
enabling component of a longer-term "Agentic Anything Factory" direction, a
strategic target image rather than a feature this release delivers.

Workflow Coordinator is a bounded, repository-native engine for serial agent
workflows with explicit Git, evidence, authority, and human-stop boundaries.

## Where to start

- **Install / Quickstart** -- [consumer quickstart](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch09_consumer_quickstart/01_quickstart.adoc): prerequisites, installation and the plan/run/status walkthrough.
- **Run a small demo** -- [a first consumer example](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch09_consumer_quickstart/02_first_run_example.adoc): one verified, copy-pasteable Run on a tiny demo repository.
- **Claude Code + WFC** -- [the Level-1 operating pattern](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch10_level1_orchestration/_chapter.adoc): using an external Claude Code session to decompose a goal into bounded Commissions that WFC executes.
- **Operator Manual** -- [the full manual](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/operator_manual.adoc): all ten chapters, including failure modes and recovery.
- **Limitations / Release Notes** -- this Beta's [honest limitations](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/operator_manual/ch09_consumer_quickstart/01_quickstart.adoc#beta-limitations) and the [release notes](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/release_notes/release_notes.adoc).
- **Developer Guide** -- [setup, test selection and code navigation](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/developer_guide/developer_guide.adoc).

The supported package requires Python 3.10 or newer. Once 0.1.0b1 is
published to PyPI, install the exact pre-release with
`python3 -m pip install 'workflow-coordinator==0.1.0b1'`; this paragraph
describes that installable state without claiming the package has been
published yet. From a source checkout, install with
`python3 -m pip install .` instead -- the form developers of this
repository use. Either way, inspect the operator with `wfc --help`. Real
provider execution always requires separate consumer authority and is never
implied by installation.

For detailed setup, test selection and code navigation, see the [Developer Guide](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/developer_guide/developer_guide.adoc).
For development, install `python3 -m pip install '.[dev]'`. The small Makefile
provides `make test`, `make lint`, `make format-check`, `make check`,
`make coverage`, `make coverage-html`, and `make build`. Set `PYTHON` to the
development interpreter when needed, for example `make coverage PYTHON=.venv/bin/python`.
The coverage targets run the test suite with branch measurement; the terminal
report shows missing lines and the HTML report is written to `build/htmlcov/`. `make lint` runs repository-wide Ruff correctness checks;
`make format-check` covers the snapshot implementation and its architecture and
unit tests. Use `make snapshot` (or `make snapshot-multi`) to create compact,
line-numbered WFC-prefixed repository snapshots in `build/snapshots/`; the output
includes a table of contents and a repository tree.

Project Memory lives in [`project_memory/`](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/project_memory/README.adoc), whose
canonical JSON tables are authoritative. `make memory-export` renders them as an
ODS workbook; after editing it, `make memory-import` validates the converted
workbook and writes it back into the canonical JSON as ordinary working-tree
changes. Git is the review boundary: inspect the result with `git diff`, accept
it with a commit, reject it with `git restore`. There is no separate Project
Memory promotion protocol. `make memory-roundtrip-check` is the independent
transformation invariant and writes neither canonical JSON nor the workbook;
`make memory-setup` installs the pinned maintainer-only spreadsheet tooling.

The [standalone authority index](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/README.adoc) is the authoritative
classification of current documentation, executable surfaces, provenance,
history, and planning evidence. Start there before maintaining or operating the
project. The compact [task router](https://github.com/StefanSchade/workflow-coordinator/blob/v0.1.0b1/docs/ai_info/_ai_info.adoc) provides the
normal current-reading path for a specific task.

The installed `wfc --version --json` reports the executing package version,
implementation tree SHA-256, reviewed resource SHA-256 digests and source commit
when established. A null `source_commit` means unknown: wheel and source archive
installs do not establish a Git commit merely from their contents. In a clean
checkout, the commit is reported only after the package files are verified
against it. The tree digest covers package files by relative path and bytes,
excluding Python bytecode and `__pycache__`; it does not depend on the current
working directory. Reviewed role texts and the operating contract are checked
before dispatch. Existing operator arguments continue through `wfc` unchanged.

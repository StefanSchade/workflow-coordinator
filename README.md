# Workflow Coordinator

Workflow Coordinator is a bounded, repository-native engine for serial agent
workflows with explicit Git, evidence, authority, and human-stop boundaries.

The supported package requires Python 3.10 or newer. Install it with
`python3 -m pip install .`; inspect the operator with `wfc --help`. Real provider
execution always requires separate consumer authority and is never implied by
installation.

For development, install `python3 -m pip install '.[dev]'`. The small Makefile
provides `make test`, `make lint`, `make format-check`, `make check`, and
`make build`. `make lint` runs repository-wide Ruff correctness checks;
`make format-check` covers the snapshot implementation and its architecture and
unit tests. Use `make snapshot` (or `make snapshot-multi`) to create compact,
line-numbered WFC-prefixed repository snapshots in `build/snapshots/`; the output
includes a table of contents and a repository tree.

Project Memory lives in [`project_memory/`](project_memory/README.adoc), whose
canonical JSON tables are authoritative. `make memory-export` renders them as an
ODS workbook; after editing it, `make memory-import` validates the converted
workbook and writes it back into the canonical JSON as ordinary working-tree
changes. Git is the review boundary: inspect the result with `git diff`, accept
it with a commit, reject it with `git restore`. There is no separate Project
Memory promotion protocol. `make memory-roundtrip-check` is the independent
transformation invariant and writes neither canonical JSON nor the workbook;
`make memory-setup` installs the pinned maintainer-only spreadsheet tooling.

The [standalone authority index](docs/README.adoc) is the authoritative
classification of current documentation, executable surfaces, provenance,
history, and planning evidence. Start there before maintaining or operating the
project. The compact [task router](docs/ai_info/_ai_info.adoc) provides the
normal current-reading path for a specific task.

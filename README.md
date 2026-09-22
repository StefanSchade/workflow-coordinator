# Workflow Coordinator

Workflow Coordinator is a bounded, repository-native engine for serial agent
workflows with explicit Git, evidence, authority, and human-stop boundaries.

The supported package requires Python 3.10 or newer. Install it with
`python -m pip install .`; inspect the operator with `wfc --help`. Real provider
execution always requires separate consumer authority and is never implied by
installation.

For development, install `python -m pip install '.[dev]'`. The small Makefile
provides `make test`, `make lint`, `make format-check`, `make check`, and
`make build`. Use `make snapshot` (or `make snapshot-multi`) to create compact,
line-numbered WFC-prefixed repository snapshots in `wfc_snapshots/`; the output
includes a table of contents and a repository tree.

The [standalone authority index](docs/README.adoc) is the authoritative
classification of current documentation, executable surfaces, provenance,
history, and planning evidence. Start there before maintaining or operating the
project.

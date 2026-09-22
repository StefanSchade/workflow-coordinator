# Workflow Coordinator

Workflow Coordinator is a bounded, repository-native engine for serial agent
workflows with explicit Git, evidence, authority, and human-stop boundaries.

The supported package requires Python 3.10 or newer. Install it with
`python -m pip install .`; inspect the operator with `wfc --help`. Real provider
execution always requires separate consumer authority and is never implied by
installation.

For development, install `python -m pip install '.[dev]'`, run
`python -m pytest`, and build distributions with `python -m build`.

The [standalone authority index](docs/README.adoc) is the authoritative
classification of current documentation, executable surfaces, provenance,
history, and planning evidence. Start there before maintaining or operating the
project.

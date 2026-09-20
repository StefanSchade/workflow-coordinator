# Workflow Coordinator

Workflow Coordinator is a bounded, repository-native engine for serial agent
workflows with explicit Git, evidence, authority, and human-stop boundaries.

The supported package requires Python 3.10 or newer. Install it with
`python -m pip install .`; inspect the operator with `wfc --help`. Real provider
execution always requires separate consumer authority and is never implied by
installation.

For development, install `python -m pip install '.[dev]'`, run
`python -m pytest`, and build distributions with `python -m build`.

Current standalone authority is this README and [the architecture
overview](docs/architecture.md). Imported Core-incubator documents are retained
only as historical provenance under `docs/imported/core-incubator/`. Mechanical
carve-out provenance is recorded in `docs/provenance/carveout.yaml` and
`docs/provenance/incubator-commit-map.tsv`.

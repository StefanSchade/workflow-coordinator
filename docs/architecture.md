# Architecture

`workflow_coordinator` is a small `src`-layout Python package. Its operator owns
deterministic admission, serial workflow progression, Git observation and
bounded mechanical commits, evidence execution, persistence, and stop routing.
The provider-neutral execution port separates those semantics from the Codex
CLI adapter; consumers own task inputs, repository policy, workflow profiles,
and authorization.

The package-owned A3 operating contract is loaded through `importlib.resources`
and cannot be selected or replaced by a consumer. Durable consumer evidence and
repository mutations remain governed by the admitted consumer inputs.

This document and the repository README are current standalone authority.
Everything below `docs/imported/core-incubator/` is historical pre-split
material only. `docs/provenance/` records the mechanical extraction lineage.

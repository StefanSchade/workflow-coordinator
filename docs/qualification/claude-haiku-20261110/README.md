# Claude Haiku qualification fixture — 2026-11-10

This directory contains historical artifacts from a live qualification run of the WFC Claude CLI adapter.

- `fixture.txt` — input fixture modified during the test.
- `nested/results.txt` — output created by the agent in a previously nonexistent subdirectory.

**Purpose:** Verify that a Claude agent launched through WFC can read and modify an existing file and create files in new directories.

**Provenance:** `wfc-claude-haiku-live-smoke-003`, implementation commit `aac9ae5c`.

**Status:** Historical qualification evidence, not an active product resource or a general-purpose test fixture.

The committed files demonstrate the resulting filesystem changes. Statements in `nested/results.txt` about individual tool invocations are agent-reported, not independently verified tool traces.

Retain this directory for historical traceability. Do not interpret it as proof that every outstanding Claude adapter acceptance criterion has been satisfied.

PYTHON ?= python3
SHEETS_RUN ?= sheets-run

MEMORY_DIR := project_memory
MEMORY_PIPELINE_DIR := $(MEMORY_DIR)/pipelines/memory
MEMORY_WORKBOOK := project_memory.ods
MEMORY_PROTOCOL := $(PYTHON) scripts/memory_proposal.py --sheets-run $(SHEETS_RUN)

.PHONY: test lint format-check check build snapshot snapshot-multi
.PHONY: memory-setup check-sheets-run memory-export
.PHONY: memory-roundtrip-check memory-preview memory-promote

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check .

format-check:
	$(PYTHON) -m ruff format --check scripts/memory_proposal.py scripts/wfc_snapshot.py \
		tests/unit/test_memory_proposal.py tests/unit/test_wfc_snapshot.py \
		tests/architecture/test_package_boundaries.py

check: lint format-check test

build:
	$(PYTHON) -m build

snapshot: snapshot-multi

snapshot-multi:
	$(PYTHON) scripts/wfc_snapshot.py

# Project Memory spreadsheet round trip.
#
# canonical JSON -> project_memory.ods -> edit -> preview the proposal -> approve.
# project_memory/canonical/*.json stays authoritative; the workbook is an editing
# source and project_memory/staging/ is the materialised review boundary.
#
# Three distinct meanings, one target each:
#
#   memory-roundtrip-check  transformation invariant -- an UNEDITED workbook must
#                           reimport to canonical unchanged; a delta is a failure.
#   memory-preview          proposal preview -- import, then show the proposed
#                           canonical delta as review information (exit 0) and
#                           record which baseline and proposal were displayed.
#   memory-promote          operator approval -- promote exactly the previewed
#                           proposal, then invalidate it.
#
# Each target is a single process that sequences import, comparison, and mutation
# internally, so no `make -j` interleaving can diff before importing, replace a
# reviewed proposal, or promote a state that was never displayed.

memory-setup:
	$(PYTHON) -m pip install -r requirements/spreadsheet-handling.txt

check-sheets-run:
	@command -v $(SHEETS_RUN) >/dev/null 2>&1 || { \
		echo "$(SHEETS_RUN) not found. Run 'make memory-setup', or pass SHEETS_RUN=<path>." >&2; \
		exit 127; \
	}

memory-export: check-sheets-run
	$(SHEETS_RUN) --config $(MEMORY_PIPELINE_DIR)/json_to_ods.yaml
	@echo $(MEMORY_WORKBOOK)

memory-roundtrip-check:
	$(MEMORY_PROTOCOL) roundtrip-check

memory-preview:
	$(MEMORY_PROTOCOL) preview

memory-promote:
	$(MEMORY_PROTOCOL) promote

PYTHON ?= python3
SHEETS_RUN ?= sheets-run

MEMORY_DIR := project_memory
MEMORY_CANONICAL_DIR := $(MEMORY_DIR)/canonical
MEMORY_STAGING_DIR := $(MEMORY_DIR)/staging
MEMORY_PIPELINE_DIR := $(MEMORY_DIR)/pipelines/memory
MEMORY_WORKBOOK := project_memory.ods
# `_meta.yaml` is workbook presentation metadata written by sheets-run, not
# canonical memory data; `.gitignore` is the tracked staging marker.
MEMORY_DIFF_EXCLUDES := --exclude=_meta.yaml --exclude=.gitignore

.PHONY: test lint format-check check build snapshot snapshot-multi
.PHONY: memory-setup check-sheets-run memory-export memory-import
.PHONY: memory-diff-reimport memory-check memory-promote memory-promote-reimport-checked

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check .

format-check:
	$(PYTHON) -m ruff format --check scripts/wfc_snapshot.py tests/unit/test_wfc_snapshot.py tests/architecture/test_package_boundaries.py

check: lint format-check test

build:
	$(PYTHON) -m build

snapshot: snapshot-multi

snapshot-multi:
	$(PYTHON) scripts/wfc_snapshot.py

# Project Memory spreadsheet round trip.
#
# canonical JSON -> project_memory.ods -> staging JSON -> diff -> explicit promotion.
# project_memory/canonical/*.json stays authoritative; the workbook and staging
# are review surfaces only.

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

memory-import: check-sheets-run
	@test -f $(MEMORY_WORKBOOK) || { \
		echo "Missing $(MEMORY_WORKBOOK). Run 'make memory-export' first." >&2; \
		exit 1; \
	}
	@mkdir -p $(MEMORY_STAGING_DIR)
	@find $(MEMORY_STAGING_DIR) -mindepth 1 ! -name .gitignore -exec rm -rf {} +
	$(SHEETS_RUN) --config $(MEMORY_PIPELINE_DIR)/ods_to_json.yaml
	@echo $(MEMORY_STAGING_DIR)

memory-diff-reimport:
	diff -ru $(MEMORY_DIFF_EXCLUDES) $(MEMORY_CANONICAL_DIR) $(MEMORY_STAGING_DIR)

memory-check: memory-import memory-diff-reimport

memory-promote:
	@test -d $(MEMORY_STAGING_DIR) || { \
		echo "Missing $(MEMORY_STAGING_DIR). Run 'make memory-import' or 'make memory-check' first." >&2; \
		exit 1; \
	}
	cp -f $(MEMORY_STAGING_DIR)/*.json $(MEMORY_CANONICAL_DIR)/

# Sub-makes keep check strictly before promote even under `make -j`, so an
# unverified staging snapshot can never be promoted.
memory-promote-reimport-checked:
	$(MAKE) memory-check
	$(MAKE) memory-promote

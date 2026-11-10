PYTHON ?= python3
SHEETS_RUN ?= sheets-run

MEMORY_WORKBOOK := $(PYTHON) scripts/memory_workbook.py --sheets-run $(SHEETS_RUN)

.PHONY: test lint format-check check build snapshot snapshot-multi coverage coverage-html
.PHONY: memory-setup memory-export memory-import memory-roundtrip-check

test:
	$(PYTHON) -m pytest -q

coverage:
	@mkdir -p build
	COVERAGE_FILE=build/.coverage $(PYTHON) -m pytest --cov=workflow_coordinator --cov-report=term-missing

coverage-html:
	@mkdir -p build
	COVERAGE_FILE=build/.coverage $(PYTHON) -m pytest --cov=workflow_coordinator --cov-report=html:build/htmlcov

lint:
	$(PYTHON) -m ruff check .

format-check:
	$(PYTHON) -m ruff format --check scripts/memory_workbook.py scripts/wfc_snapshot.py \
		tests/unit/test_memory_workbook.py tests/unit/test_wfc_snapshot.py \
		tests/architecture/test_package_boundaries.py

check: lint format-check test

build:
	$(PYTHON) -m build

snapshot: snapshot-multi

snapshot-multi:
	$(PYTHON) scripts/wfc_snapshot.py

# Project Memory spreadsheet round trip.
#
# Two persistent representations, Git as the review boundary:
#
#   project_memory/canonical/*.json   authoritative Project Memory
#   project_memory.ods                an alternate editable representation
#
#   memory-export           canonical JSON -> workbook.
#   memory-import           edited workbook -> validated temporary JSON ->
#                           canonical JSON, as ordinary working-tree changes.
#                           Review them with `git diff`; accept with a commit,
#                           reject with `git restore`.
#   memory-roundtrip-check  transformation invariant -- canonical JSON -> ODS ->
#                           JSON must reproduce canonical exactly, without
#                           writing canonical or the maintainer's workbook.
#
# No target is a prerequisite of another and each is one self-contained process,
# so no `make` invocation mode -- `-j` included -- can act on stale output.

memory-setup:
	$(PYTHON) -m pip install -r requirements/spreadsheet-handling.txt

memory-export:
	$(MEMORY_WORKBOOK) export

memory-import:
	$(MEMORY_WORKBOOK) import

memory-roundtrip-check:
	$(MEMORY_WORKBOOK) roundtrip-check

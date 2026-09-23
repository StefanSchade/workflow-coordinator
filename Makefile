PYTHON ?= python

.PHONY: test lint format-check check build snapshot snapshot-multi

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

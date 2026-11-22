PYTHON ?= python
export PYTHONPATH := .
export PDR_OFFLINE := 1

.PHONY: test smoke report

test:
	$(PYTHON) -m pytest -q

smoke:
	PYTHON=$(PYTHON) bash scripts/smoke.sh

report:
	$(PYTHON) -m pdr.cli report

PYTHON ?= python3

.PHONY: test lint build smoke

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests

lint:
	$(PYTHON) -m py_compile src/agent_ci_failure_packet/*.py tests/test_cli.py

build: lint

smoke:
	PYTHONPATH=src $(PYTHON) -m agent_ci_failure_packet examples/ci-failure.log --title "Publish guard CI failure" --format json > /tmp/agent-ci-failure-packet.json

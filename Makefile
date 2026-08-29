.PHONY: test validate web seed installer release clean

PYTHON ?= python3

export PYTHONPATH := $(CURDIR)/src

# Dependency-free project validation.
test:
	bash scripts/test.sh

validate:
	$(PYTHON) -m compileall -q src tests
	go test ./...
	npm run typecheck
	bash -n scripts/install.sh scripts/build-installer.sh scripts/build-release.sh scripts/test.sh bin/gmd

web:
	npm ci
	npm run build

seed:
	GMD_DATA_DIR=$(CURDIR)/data \
	GMD_DATABASE_PATH=$(CURDIR)/data/catalog.sqlite3 \
	GMD_SEED_DIR=$(CURDIR)/seed \
	$(PYTHON) -m gmd bootstrap

installer:
	bash scripts/build-installer.sh

release:
	bash scripts/build-release.sh

clean:
	rm -rf .test-output .pytest_cache
	find src tests -type d -name __pycache__ -prune -exec rm -rf {} +

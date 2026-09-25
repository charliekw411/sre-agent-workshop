.DEFAULT_GOAL := help

PYTHON ?= python3
NPM    ?= npm
DIST   ?= dist
PORT   ?= 8000

.PHONY: help
help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-24s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Install the documentation toolchain
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt
	$(NPM) ci

.PHONY: test-web
test-web: ## Test the authenticated site controls
	$(NPM) test

.PHONY: build-web
build-web: ## Bundle MSAL and the interactive incident launcher
	$(NPM) run build:web

.PHONY: serve
serve: install build-web ## Serve the workshop site locally with live reload
	$(PYTHON) -m mkdocs serve --dev-addr localhost:$(PORT)

.PHONY: build-docs-website
build-docs-website: install test-web build-web ## Build the workshop site into $(DIST)
	$(PYTHON) -m mkdocs build --strict --site-dir $(DIST)

.PHONY: clean
clean: ## Remove generated site output
	rm -rf $(DIST) site

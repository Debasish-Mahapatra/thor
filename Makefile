# Thor (a working name). `make help` lists the targets.

ROOT := $(abspath .)
UV ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)
PY := backend/.venv/bin/python
# The display name; the Swift product and executable stay "Thor".
APP_NAME := Thor
APP := build/$(APP_NAME).app
ICON := build/AppIcon.icns
UI_SOURCES := $(shell find ui/src ui/public -type f) ui/index.html ui/vite.config.ts ui/tsconfig.json

.PHONY: help setup test check dev app run browser dmg clean

help:
	@echo "make setup    create the Python venv and build the interface"
	@echo "make test     run the backend, interface and shell tests"
	@echo "make check    print the dependency report"
	@echo "make dev      backend + Vite with live reload, in your browser"
	@echo "make app      build $(APP) for development (uses this folder's Python)"
	@echo "make dmg      build build/$(APP_NAME).dmg: a self-contained app that installs the rest itself"
	@echo "make run      build and open the app (same as ./run.sh)"
	@echo "make browser  run $(APP_NAME) in your browser instead of the app window"

setup: backend/.venv/.synced ui/dist/index.html

backend/.venv/.synced: backend/pyproject.toml
	cd backend && $(UV) sync --python 3.12
	@touch $@

ui/node_modules/.installed: ui/package.json
	cd ui && npm install --no-audit --no-fund
	@touch $@

ui/dist/index.html: ui/node_modules/.installed $(UI_SOURCES)
	cd ui && npm run build

test: setup
	cd backend && .venv/bin/python -m pytest -q
	cd ui && npm run typecheck && npm test
	cd shell && swift build -q

check: backend/.venv/.synced
	@$(PY) -m thor check

dev: setup
	./scripts/dev.sh

$(ICON): shell/make_icon.py backend/.venv/.synced
	rm -rf build/AppIcon.iconset
	$(PY) shell/make_icon.py build/AppIcon.iconset
	iconutil -c icns build/AppIcon.iconset -o $@

$(APP): setup $(ICON) shell/Info.plist $(shell find shell/Sources -type f) shell/Package.swift
	cd shell && swift build -c release -q
	rm -rf $(APP)
	mkdir -p $(APP)/Contents/MacOS $(APP)/Contents/Resources
	cp shell/.build/release/Thor $(APP)/Contents/MacOS/Thor
	cp shell/Info.plist $(APP)/Contents/Info.plist
	cp $(ICON) $(APP)/Contents/Resources/AppIcon.icns
	printf '{"python": "%s", "workdir": "%s", "ui": "%s"}\n' \
		"$(ROOT)/$(PY)" "$(ROOT)/backend" "$(ROOT)/ui/dist" > $(APP)/Contents/Resources/backend.json
	codesign --force --sign - $(APP)

app: $(APP)

run: $(APP)
	open $(APP)

dmg:
	./scripts/build_dmg.sh

browser: setup
	cd backend && .venv/bin/python -m thor serve --open --ui ../ui/dist

clean:
	rm -rf build ui/dist shell/.build

.PHONY: help check doctor install uninstall start stop restart status linux appimage appimage-release pacman electron-linux electron-appimage electron-appimage-release electron-dev macos desktop-exe desktop-dmg windows aws-upload aws-upload-temp local-releases
.PHONY: publish-forgejo publish-github publish-github-dry publish-remotes
.PHONY: start-bg stop-bg restart-bg logs logs-clear _ensure_navin set-version
.PHONY: lint clean clean-logs clean-all clean_python_cache native desktop-parity

.DEFAULT_GOAL := help

GREEN=\033[0;32m
RED=\033[0;31m
YELLOW=\033[1;33m
CYAN=\033[0;36m
BLUE=\033[0;34m
NC=\033[0m

PYTHON ?= python3
VENV ?= $(CURDIR)/.venv
PIP := $(VENV)/bin/pip
NAVIN := $(VENV)/bin/navin
RUFF := $(VENV)/bin/ruff

# Prefer the live WebUI/WebSocket port from ~/.navin/config.json. The Makefile
# used to hardcode 8765 while many installs listen on 8766, which made
# `make restart-bg` report health KO and broke the Vite proxy (HTTP 500).
WEBUI_PORT ?= $(shell python3 -c "import json, pathlib; p=pathlib.Path.home()/'.navin'/'config.json';\
	print((json.load(open(p)).get('channels') or {}).get('websocket', {}).get('port', 8765) if p.is_file() else 8765)" 2>/dev/null || echo 8765)
GATEWAY_PORT ?= $(shell python3 -c "import json, pathlib; p=pathlib.Path.home()/'.navin'/'config.json';\
	print((json.load(open(p)).get('gateway') or {}).get('port', 18791) if p.is_file() else 18791)" 2>/dev/null || echo 18791)
PORT ?= $(WEBUI_PORT)
HEALTH_URL ?= http://127.0.0.1:$(WEBUI_PORT)/health

LOG_DIR ?= $(CURDIR)/logs

help:
	@echo "$(CYAN)Navin Backend - commandes disponibles:$(NC)"
	@echo ""
	@echo "  Installation:"
	@echo "    install          Créer .venv et installer le package (editable + dev)"
	@echo "    uninstall        Supprimer le .venv"
	@echo "    doctor           Vérifier les outils système (node, ffmpeg, jq, ...)"
	@echo ""
	@echo "  Gateway (foreground):"
	@echo "    start            Démarrer navin gateway"
	@echo "    stop             Arrêter le gateway"
	@echo "    restart          Redémarrer le gateway"
	@echo "    status           Vérifier si le gateway tourne"
	@echo ""
	@echo "  Gateway (background):"
	@echo "    start-bg         Démarrer en background"
	@echo "    stop-bg          Arrêter le process background"
	@echo "    restart-bg       Redémarrer en background"
	@echo "    logs             Suivre les logs gateway"
	@echo ""
	@echo "  Développement:"
	@echo "    lint             Lancer ruff"
	@echo "    native           Compiler navin-core, navin-sandbox (obligatoire Linux/macOS) et navin-engine"
	@echo "    desktop-parity   Locks auto web↔Tauri + checklist manuelle (hostChrome, explorateur, terminal)"
	@echo ""
	@echo "  Packaging (distribution 100 % Tauri - app desktop native Rust):"
	@echo "    linux            Sidecar Linux (exécutable autonome) dans os/linux/bin/"
	@echo "    appimage         Tout-en-un Linux : sidecar frais + AppImage + .deb + .rpm + .pkg.tar.zst (pacman) dans os/linux/"
	@echo "    appimage-release Bundles Linux compatibles distros anciennes (via Docker)"
	@echo "    pacman           Alias de appimage (produit aussi le .pkg.tar.zst Arch)"
	@echo "    electron-linux    Sidecar Linux pour la coque Electron (même moteur que make linux)"
	@echo "    electron-appimage Bundles Electron locaux : AppImage + .deb + .rpm dans os/linux/electron/"
	@echo "    electron-appimage-release Bundles Electron via Docker (Ubuntu 24.04), pour distribution"
	@echo "    electron-dev     Fenêtre Electron contre le backend du venv (pas de paquet)"
	@echo "    windows          Tout-en-un Windows : prérequis + sidecar + .msi + setup .exe signés + vérif"
	@echo "                     Signature Azure Trusted Signing si packaging/windows/signing.env existe"
	@echo "    desktop-exe      App desktop Windows Tauri (.msi + setup .exe) dans os/windows/x64/"
	@echo "    macos            Sidecar macOS (exécutable autonome) dans os/macos/<arch>/ (sur un Mac)"
	@echo "    desktop-dmg      Tout-en-un macOS : sidecars frais + .dmg arm64 / x64 dans os/macos/"
	@echo ""
	@echo "  Publication:"
	@echo "    aws-upload       Téléverser les builds de os/ vers S3 (v<VERSION>/, écrase si même nom)"
	@echo "                     + met à jour releases.json (le site l'affiche automatiquement)"
	@echo "    local-releases   Prépare site/front/public/downloads pour curl localhost:3100/install"
	@echo "    aws-upload-temp  Téléverser les templates apps vers S3 (templates/v1/, écrase)"
	@echo "                     make aws-upload-temp SLUG=crm  pour un seul slug"
	@echo "    media-assets     Regénérer catalog.json + masters media manquants (local)"
	@echo "    aws-upload-media Générer si besoin + téléverser les masters media vers S3"
	@echo "                     (media-templates/v1/, identifiants dans .env)"
	@echo ""
	@echo "  Versionnage (VERSION=x.y.z sur n'importe quelle cible de build):"
	@echo "    make windows VERSION=1.1.0     build Windows estampillé 1.1.0"
	@echo "    make appimage VERSION=1.1.0    build Linux estampillé 1.1.0"
	@echo "    make electron-linux VERSION=1.2.1 && make electron-appimage-release VERSION=1.2.1"
	@echo "    make aws-upload VERSION=1.1.0  publie v1.1.0/ + manifeste site"
	@echo ""
	@echo "  Git (Forgejo privé / GitHub public):"
	@echo "    publish-forgejo     Pousser la branche prod vers Forgejo (arbre complet)"
	@echo "    publish-github-dry  Aperçu de l'arbre public CLI (sans site / desktop / AWS / navin.live)"
	@echo "    publish-github      Pousser cet arbre filtré vers GitHub main (navin-agi)"
	@echo "    publish-remotes     Afficher / créer les remotes origin + github"
	@echo ""
	@echo "  Nettoyage:"
	@echo "    clean            Cache Python + logs locaux"
	@echo "    clean-all        clean + uninstall"
	@echo ""
	@echo "  Tout-en-un (backend + frontend):"
	@echo "    sh scripts/start.sh   Installer si besoin + tout démarrer (options: --backend, --front, --fg, --install)"
	@echo "    sh scripts/stop.sh    Tout arrêter"
	@echo ""
	@echo "$(BLUE)Gateway: $(HEALTH_URL)$(NC)"

check:
	@command -v $(PYTHON) >/dev/null 2>&1 || { echo "$(RED)python3 non installé$(NC)"; exit 1; }

install: check
	@echo "$(CYAN)Installation backend Navin...$(NC)"
	@if [ ! -d "$(VENV)" ]; then $(PYTHON) -m venv "$(VENV)"; fi
	@$(PIP) install -U pip
	@$(PIP) install -e ".[dev]"
	@$(VENV)/bin/python -c "from navin.agent.tools.sandbox import ensure_native_sandbox; p = ensure_native_sandbox(); print('navin-sandbox:', p or 'MISSING - install rustup then make native')"
	@echo "$(GREEN)✓ Backend installé ($(VENV))$(NC)"
	@$(NAVIN) doctor || true

# Même diagnostic que dans les versions packagées: une seule implémentation.
doctor: _ensure_navin
	@$(NAVIN) doctor

# Web local ↔ Tauri: locks auto + checklist manuelle avant release desktop.
desktop-parity:
	@sh packaging/desktop-parity.sh

# Version distribuée = version de l'app desktop Tauri (source de vérité).
# Passer VERSION=x.y.z à make windows/appimage/desktop-dmg/aws-upload tamponne
# d'abord cette version dans tauri.conf.json, Cargo.toml, package.json et
# pyproject.toml : tous les artefacts sortent avec le bon numéro.
VERSION ?= $(shell python3 -c "import json; print(json.load(open('desktop/src-tauri/tauri.conf.json'))['version'])" 2>/dev/null || echo 1.0.0)

# Tamponne VERSION dans toutes les sources de vérité (no-op si déjà à jour).
set-version:
	@bash scripts/set-version.sh "$(VERSION)"

# Sidecar Linux : l'exécutable autonome que l'app desktop Tauri embarque.
linux: set-version
	@sh packaging/linux/build-offline.sh

# Bundles desktop Tauri Linux (AppImage + .deb + .rpm + .pkg.tar.zst) autour du sidecar.
# Le sidecar est TOUJOURS reconstruit d'abord (dépendance `linux`) : un bundle
# ne doit jamais embarquer un backend/webui périmé. Pour itérer sur la coque
# Tauri seule : `sh packaging/linux/build-appimage.sh` directement.
# `make linux` ne produit pas les paquets; `make appimage` les construit tous.
appimage: linux
	@sh packaging/linux/build-appimage.sh

pacman: appimage

# Release AppImage built inside Ubuntu 22.04 (Docker) so it runs on older
# distros too (glibc >= 2.35). Use this one for anything given to users.
appimage-release: set-version
	@sh packaging/linux/build-appimage-docker.sh

# Option Electron / Chromium, parallèle à linux / appimage / appimage-release.
# Tauri reste inchangé. VERSION=x.y.z tamponne desktop-electron/package.json
# via set-version, comme pour Tauri.
#
#   make electron-linux VERSION=1.2.1 && make electron-appimage-release VERSION=1.2.1
electron-linux: set-version
	@sh packaging/linux/build-offline.sh

electron-appimage: electron-linux
	@sh packaging/linux/build-electron.sh

electron-appimage-release: set-version
	@sh packaging/linux/build-electron-docker.sh

electron-dev:
	@cd desktop-electron && { [ -x node_modules/.bin/electron ] || npm install; }
	@# Session-wide ELECTRON_OZONE_PLATFORM_HINT=wayland (Omarchy/UWSM) is
	# read by Chromium before main.js. NVIDIA GBM cannot paint Wayland buffers.
	@if [ -n "$$WAYLAND_DISPLAY" ] && [ -n "$$DISPLAY" ] && { [ -e /proc/driver/nvidia/version ] || [ -e /dev/nvidia0 ]; }; then \
		export ELECTRON_OZONE_PLATFORM_HINT=x11; \
		echo "Navin desktop GPU: ELECTRON_OZONE_PLATFORM_HINT=x11 (NVIDIA Wayland)"; \
	fi; \
	NAVIN_DESKTOP_BIN="$(VENV)/bin/navin" $(CURDIR)/desktop-electron/node_modules/.bin/electron "$(CURDIR)/desktop-electron"

# Tout-en-un Windows: installe les prérequis manquants via winget (Rust MSVC,
# Node, Python, Build Tools C++), puis construit l'app desktop Tauri.
windows: desktop-exe

# Windows desktop app (.msi + NSIS setup .exe), from Windows or WSL interop.
# WSLENV pousse les identifiants Azure Trusted Signing exportés dans WSL vers
# le process Windows (sinon build-desktop.ps1 lit packaging/windows/signing.env).
desktop-exe: set-version
	@if command -v powershell.exe >/dev/null 2>&1; then \
		WSLENV=$${WSLENV:+$$WSLENV:}AZURE_TENANT_ID/u:AZURE_CLIENT_ID/u:AZURE_CLIENT_SECRET/u:NAVIN_SKIP_SIDECAR_BUILD/u \
		powershell.exe -NoProfile -ExecutionPolicy Bypass -File "packaging\\windows\\build-desktop.ps1"; \
	else \
		echo "powershell.exe not found - run packaging/windows/build-desktop.ps1 on Windows"; exit 1; \
	fi

# macOS desktop app (.dmg), on a Mac of the target architecture.
# Sidecars TOUJOURS reconstruits d'abord (dépendance `macos`), même garantie
# de fraîcheur que Linux/Windows. Pour itérer sur la coque Tauri seule :
# `sh packaging/macos/build-desktop.sh` directement - il refuse d'embarquer
# un sidecar dont le tampon os/macos/<arch>/navin-dist/VERSION ne correspond
# pas à la version de tauri.conf.json (relancer `make macos` dans ce cas).
desktop-dmg: macos
	@sh packaging/macos/build-desktop.sh

# Sidecar macOS : l'exécutable autonome que l'app desktop Tauri embarque.
macos: set-version
	@sh packaging/macos/build-offline.sh

# Deux remotes : Forgejo (prod, arbre complet) et GitHub (main, sans site/abo/AWS).
publish-remotes:
	@bash scripts/publish-git.sh remotes

publish-forgejo:
	@bash scripts/publish-git.sh forgejo

publish-github-dry:
	@bash scripts/publish-git.sh github --dry-run

publish-github:
	@bash scripts/publish-git.sh github

# Publie les artefacts de os/ vers S3 sous v<VERSION>/ :
# - même version + même nom : le fichier est écrasé sur S3 ;
# - nouvelle version : un nouveau préfixe v<version>/ est créé.
# Met aussi à jour releases.json (manifeste des versions) à la racine du
# bucket : le site le lit dynamiquement, plus rien à éditer à la main.
aws-upload:
	@bash scripts/publish-os-to-s3.sh "$(VERSION)"

# One-liner local: curl http://localhost:3100/install | bash
# (navin.live / S3 unchanged; this only fills site/front/public/downloads).
local-releases:
	@bash scripts/stage-local-releases.sh "$(VERSION)"

# Publie les templates apps sur S3 (templates/v1/<slug>.tar.gz + catalog.json).
# Ecrase les clés existantes. SLUG=crm limite à un template.
SLUG ?=
aws-upload-temp:
	@bash scripts/publish-templates-to-s3.sh "$(SLUG)"

# Templates media (Marketing / Montage) : regénère le catalogue puis les
# masters manquants (images Unsplash + clips MP4 Ken Burns via ffmpeg).
# Idempotent : ne retélécharge / ne re-rend que ce qui manque.
media-assets:
	@$(PYTHON) templates/media/build_catalog.py
	@$(PYTHON) templates/media/build_assets.py

# Publie les masters media + catalog.json vers S3 (media-templates/v1/).
# Identifiants lus dans .env (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY...).
aws-upload-media: media-assets
	@bash scripts/publish-media-templates-to-s3.sh

uninstall:
	@rm -rf "$(VENV)"
	@echo "$(GREEN)✓ .venv supprimé$(NC)"

_ensure_navin:
	@if [ ! -x "$(NAVIN)" ]; then \
		echo "$(RED).venv manquant. Lancez: make install$(NC)"; \
		exit 1; \
	fi

start: _ensure_navin
	@echo "$(GREEN)Démarrage navin gateway (gateway $(GATEWAY_PORT), webui $(WEBUI_PORT))...$(NC)"
	@$(NAVIN) gateway --port "$(GATEWAY_PORT)"

stop: _ensure_navin
	@$(NAVIN) gateway stop

restart: stop start

status: _ensure_navin
	@$(NAVIN) gateway status
	@if curl -sfS "$(HEALTH_URL)" >/dev/null 2>&1; then \
		echo "$(GREEN)✓ Health OK: $(HEALTH_URL)$(NC)"; \
	else \
		echo "$(YELLOW)Health: $(HEALTH_URL) ne répond pas$(NC)"; \
	fi

start-bg: _ensure_navin
	@echo "$(GREEN)Démarrage navin gateway en background (gateway $(GATEWAY_PORT), webui $(WEBUI_PORT))...$(NC)"
	@out=`$(NAVIN) gateway --background --port "$(GATEWAY_PORT)" 2>&1`; code=$$?; \
	echo "$$out"; \
	if [ $$code -ne 0 ] && ! echo "$$out" | grep -q "already_running"; then exit $$code; fi
	@ready=0; \
	for _ in 1 2 3 4 5 6 7 8 9 10; do \
		if curl -sfS "$(HEALTH_URL)" >/dev/null 2>&1; then ready=1; break; fi; \
		sleep 1; \
	done; \
	if [ "$$ready" = "1" ]; then \
		echo "$(GREEN)✓ Health OK: $(HEALTH_URL)$(NC)"; \
	else \
		echo "$(RED)✗ Gateway non joignable sur $(HEALTH_URL)$(NC)"; \
		echo "$(YELLOW)Cause fréquente: aucune clé API / modèle dans ~/.navin/config.json$(NC)"; \
		echo "$(YELLOW)Logs: $(NAVIN) gateway logs --no-follow$(NC)"; \
		$(NAVIN) gateway logs --no-follow --tail 30 2>/dev/null || true; \
		exit 1; \
	fi

stop-bg: stop

restart-bg: _ensure_navin
	@# Do not force --port $(PORT): that flag is the gateway health port
	# (gateway.port), not channels.websocket.port. Forcing 8765 broke installs
	# whose WebUI listens on 8766 and left Vite proxying into a dead port.
	@$(NAVIN) gateway restart --port "$(GATEWAY_PORT)"
	@ready=0; \
	for _ in 1 2 3 4 5 6 7 8 9 10; do \
		if curl -sfS "$(HEALTH_URL)" >/dev/null 2>&1; then ready=1; break; fi; \
		if curl -sfS "http://127.0.0.1:$(GATEWAY_PORT)/health" >/dev/null 2>&1; then ready=1; break; fi; \
		sleep 1; \
	done; \
	if [ "$$ready" != "1" ]; then \
		echo "$(RED)✗ Restart OK côté CLI mais health KO - voir: make logs$(NC)"; \
		echo "$(YELLOW)WebUI health: $(HEALTH_URL)  Gateway health: http://127.0.0.1:$(GATEWAY_PORT)/health$(NC)"; \
		exit 1; \
	fi

logs: _ensure_navin
	@$(NAVIN) gateway logs

logs-clear:
	@mkdir -p "$(LOG_DIR)"
	@rm -f "$(LOG_DIR)"/*.log "$(LOG_DIR)"/*.pid
	@echo "$(GREEN)✓ Logs locaux effacés$(NC)"

lint: _ensure_navin
	@$(RUFF) check navin/

# Crates natives Rust. navin-core accelere grep/git/index (optionnel).
# navin-sandbox est le jail OS des commandes: obligatoire sur Linux/macOS.
# navin-engine est le daemon du module Evolve (optionnel).
native: _ensure_navin
	@command -v cargo >/dev/null 2>&1 || { echo "$(RED)Rust manquant - installez rustup (https://rustup.rs)$(NC)"; exit 1; }
	@$(PIP) install -q maturin
	@cd navin-core && "$(VENV)/bin/maturin" build --release --out dist
	@# Le wheel le plus récent = celui qu'on vient de builder. dist/ peut aussi
	@# contenir des wheels d'autres plateformes (win_amd64 après un build
	@# Windows) que pip refuserait ici.
	@# --ignore-installed: écrase sans désinstaller - un build interrompu peut
	@# laisser le paquet à moitié supprimé et faire échouer la désinstallation.
	@$(PIP) install -q --ignore-installed --no-deps "$$(ls -t navin-core/dist/*.whl | head -n 1)"
	@$(VENV)/bin/python -c "import navin_core; print('navin_core', navin_core.__version__)"
	@cd navin-sandbox && cargo build --release
	@mkdir -p navin/resources/bin
	@cp navin-sandbox/target/release/navin-sandbox navin/resources/bin/navin-sandbox
	@chmod +x navin/resources/bin/navin-sandbox
	@chmod +x navin/resources/bin/navin-sandbox
	@# Le moteur vit dans son propre dépôt (submodule): un clone frais arrive
	@# avec le répertoire vide.
	@test -f crates/navin-engine/Cargo.toml || git submodule update --init crates/navin-engine
	@cd crates/navin-engine && cargo build --release
	@cp crates/navin-engine/target/release/navin-engine navin/resources/bin/navin-engine
	@echo "$(GREEN)✓ Extension native + binaires sandbox et moteur installés$(NC)"

clean-logs: logs-clear

clean_python_cache:
	@find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.pyc' -not -path './.venv/*' -delete 2>/dev/null || true

clean: clean_python_cache clean-logs
	@echo "$(GREEN)✓ Nettoyage terminé$(NC)"

clean-all: clean uninstall
	@echo "$(RED)✓ Nettoyage complet (venv inclus)$(NC)"
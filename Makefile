.PHONY: help check doctor install uninstall start stop restart status binary exe
.PHONY: start-bg stop-bg restart-bg logs logs-clear _ensure_navin
.PHONY: lint clean clean-logs clean-all clean_python_cache

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

PORT ?= 8765
HEALTH_URL ?= http://127.0.0.1:$(PORT)/health

LOG_DIR ?= $(CURDIR)/logs

help:
	@echo "$(CYAN)Navin Backend — commandes disponibles:$(NC)"
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
	@echo ""
	@echo "  Packaging:"
	@echo "    binary           Binaire standalone PyInstaller (dist/navin-standalone/)"
	@echo "    exe              Launcher Windows navin.exe (dist/navin.exe, via WSL/Windows)"
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
	@echo "$(GREEN)✓ Backend installé ($(VENV))$(NC)"
	@sh scripts/doctor.sh

doctor:
	@sh scripts/doctor.sh

# Standalone binary (PyInstaller one-folder build → dist/navin-standalone/)
binary: _ensure_navin
	@$(PIP) install -q pyinstaller
	@cd packaging/pyinstaller && ../../$(VENV)/bin/pyinstaller navin.spec --noconfirm --distpath ../../dist
	@echo "$(GREEN)✓ dist/navin-standalone/navin$(NC)"

# Windows launcher navin.exe (from WSL via interop, or native PowerShell)
exe:
	@if command -v powershell.exe >/dev/null 2>&1; then \
		powershell.exe -NoProfile -ExecutionPolicy Bypass -File "packaging\\windows\\build-launcher.ps1"; \
	else \
		echo "$(RED)powershell.exe introuvable — buildez sous Windows/WSL ou via GitHub Actions (release-binaries.yml)$(NC)"; \
		exit 1; \
	fi

uninstall:
	@rm -rf "$(VENV)"
	@echo "$(GREEN)✓ .venv supprimé$(NC)"

_ensure_navin:
	@if [ ! -x "$(NAVIN)" ]; then \
		echo "$(RED).venv manquant. Lancez: make install$(NC)"; \
		exit 1; \
	fi

start: _ensure_navin
	@echo "$(GREEN)Démarrage navin gateway (port $(PORT))...$(NC)"
	@$(NAVIN) gateway --port "$(PORT)"

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
	@echo "$(GREEN)Démarrage navin gateway en background (port $(PORT))...$(NC)"
	@out=`$(NAVIN) gateway --background --port "$(PORT)" 2>&1`; code=$$?; \
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
	@$(NAVIN) gateway restart --port "$(PORT)"
	@ready=0; \
	for _ in 1 2 3 4 5 6 7 8 9 10; do \
		if curl -sfS "$(HEALTH_URL)" >/dev/null 2>&1; then ready=1; break; fi; \
		sleep 1; \
	done; \
	if [ "$$ready" != "1" ]; then \
		echo "$(RED)✗ Restart OK côté CLI mais health KO — voir: make logs$(NC)"; \
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

clean-logs: logs-clear

clean_python_cache:
	@find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.pyc' -not -path './.venv/*' -delete 2>/dev/null || true

clean: clean_python_cache clean-logs
	@echo "$(GREEN)✓ Nettoyage terminé$(NC)"

clean-all: clean uninstall
	@echo "$(RED)✓ Nettoyage complet (venv inclus)$(NC)"

.PHONY: help check install uninstall start stop restart status
.PHONY: start-bg stop-bg restart-bg logs logs-clear
.PHONY: build lint clean clean-all

.DEFAULT_GOAL := help

GREEN=\033[0;32m
RED=\033[0;31m
YELLOW=\033[1;33m
CYAN=\033[0;36m
BLUE=\033[0;34m
NC=\033[0m

PORT ?= 5173
HOST ?= 127.0.0.1
# Même source que le Makefile racine : channels.websocket.port dans
# ~/.navin/config.json (souvent 8766). Un 8765 figé faisait échouer
# /webui/bootstrap (HTTP 500 / ECONNREFUSED) dès que le gateway n'écoute
# plus sur le défaut historique.
WEBUI_PORT ?= $(shell python3 -c "import json, pathlib; p=pathlib.Path.home()/'.navin'/'config.json';\
	print((json.load(open(p)).get('channels') or {}).get('websocket', {}).get('port', 8765) if p.is_file() else 8765)" 2>/dev/null || echo 8765)
NAVIN_API_URL ?= http://127.0.0.1:$(WEBUI_PORT)

LOG_DIR ?= $(CURDIR)/logs
FRONTEND_LOG ?= $(LOG_DIR)/frontend.log
FRONTEND_PID ?= $(LOG_DIR)/frontend.pid
VITE_MATCH ?= [v]ite

# npm et package-lock.json uniquement : c'est le couple que la CI et les trois
# scripts de packaging résolvent. Quand bun était accepté aussi, bun.lock et
# package-lock.json résolvaient 112 paquets sur 878 dans des versions
# différentes, donc un dev buildait une WebUI que personne ne livre.
PKG := npm
INSTALL_CMD := npm ci
DEV_BIN := npm
DEV_ARGS := run dev
BUILD_CMD := npm run build
LINT_CMD := npm run lint

help:
	@echo "$(CYAN)Navin WebUI - commandes disponibles:$(NC)"
	@echo ""
	@echo "  Installation:"
	@echo "    install          Installer les dépendances ($(PKG))"
	@echo "    uninstall        Supprimer node_modules"
	@echo ""
	@echo "  Dev server (foreground):"
	@echo "    start            Démarrer Vite (proxy API → $(NAVIN_API_URL))"
	@echo "    stop             Arrêter Vite"
	@echo "    restart          Redémarrer Vite"
	@echo "    status           Vérifier si Vite tourne"
	@echo ""
	@echo "  Dev server (background):"
	@echo "    start-bg         Démarrer en background"
	@echo "    stop-bg          Arrêter le process background"
	@echo "    restart-bg       Redémarrer en background"
	@echo "    logs             tail -f des logs"
	@echo "    logs-clear       Effacer les logs"
	@echo ""
	@echo "  Développement:"
	@echo "    build            Build production → ../navin/web/dist"
	@echo "    lint             Lancer eslint"
	@echo ""
	@echo "  Nettoyage:"
	@echo "    clean            Logs + caches Vite"
	@echo "    clean-all        clean + uninstall"
	@echo ""
	@echo "$(BLUE)Dev: http://$(HOST):$(PORT)  |  API: $(NAVIN_API_URL)$(NC)"

check:
	@command -v node >/dev/null 2>&1 || { echo "$(RED)Node.js non installé$(NC)"; exit 1; }
	@command -v npm >/dev/null 2>&1 || { echo "$(RED)npm requis (source de vérité: package-lock.json)$(NC)"; exit 1; }
	@echo "$(GREEN)✓ Package manager: $(PKG)$(NC)"

install: check
	@echo "$(CYAN)Installation frontend Navin ($(PKG))...$(NC)"
	@$(INSTALL_CMD)
	@echo "$(GREEN)✓ Frontend installé$(NC)"

uninstall:
	@rm -rf node_modules
	@echo "$(GREEN)✓ node_modules supprimé$(NC)"

start:
	@test -d node_modules || { echo "$(RED)node_modules manquant. Lancez: make install$(NC)"; exit 1; }
	@echo "$(GREEN)Démarrage WebUI sur http://$(HOST):$(PORT)...$(NC)"
	@echo "$(CYAN)Proxy API: $(NAVIN_API_URL)$(NC)"
	@NAVIN_API_URL="$(NAVIN_API_URL)" $(DEV_BIN) $(DEV_ARGS)

stop:
	@if [ -f "$(FRONTEND_PID)" ]; then \
		pid=`cat "$(FRONTEND_PID)"`; \
		kill $$pid 2>/dev/null || true; \
		rm -f "$(FRONTEND_PID)"; \
	fi
	@pkill -f "$(VITE_MATCH)" 2>/dev/null || true
	@sleep 1
	@pkill -9 -f "$(VITE_MATCH)" 2>/dev/null || true
	@for p in `lsof -ti :$(PORT) 2>/dev/null`; do kill $$p 2>/dev/null || true; done
	@echo "$(GREEN)✓ Frontend arrêté$(NC)"

restart: stop start

status:
	@if [ -f "$(FRONTEND_PID)" ]; then \
		pid=`cat "$(FRONTEND_PID)"`; \
		if kill -0 $$pid 2>/dev/null; then \
			echo "$(GREEN)✓ Frontend: en cours (PID $$pid)$(NC)"; \
			exit 0; \
		fi; \
	fi
	@if pgrep -f "$(VITE_MATCH)" >/dev/null 2>&1; then \
		echo "$(GREEN)✓ Frontend: en cours (process Vite trouvé)$(NC)"; \
	else \
		echo "$(RED)✗ Frontend: arrêté$(NC)"; \
	fi

start-bg:
	@test -d node_modules || { echo "$(RED)node_modules manquant. Lancez: make install$(NC)"; exit 1; }
	@mkdir -p "$(LOG_DIR)"
	@if [ -f "$(FRONTEND_PID)" ]; then \
		pid=`cat "$(FRONTEND_PID)"`; \
		if kill -0 $$pid 2>/dev/null; then \
			echo "$(YELLOW)Frontend déjà lancé (PID $$pid)$(NC)"; \
			exit 0; \
		fi; \
		rm -f "$(FRONTEND_PID)"; \
	fi
	@NAVIN_API_URL="$(NAVIN_API_URL)" nohup $(DEV_BIN) $(DEV_ARGS) >"$(FRONTEND_LOG)" 2>&1 & echo $$! >"$(FRONTEND_PID)"
	@sleep 2
	@pid=`cat "$(FRONTEND_PID)" 2>/dev/null || true`; \
	if [ -n "$$pid" ] && kill -0 $$pid 2>/dev/null; then \
		echo "$(GREEN)✓ Frontend démarré (PID $$pid, log: $(FRONTEND_LOG))$(NC)"; \
		echo "$(CYAN)http://$(HOST):$(PORT)$(NC)"; \
	else \
		echo "$(RED)Échec du démarrage. Voir $(FRONTEND_LOG)$(NC)"; \
		rm -f "$(FRONTEND_PID)"; \
		exit 1; \
	fi

stop-bg: stop

restart-bg: stop-bg start-bg

logs:
	@mkdir -p "$(LOG_DIR)"
	@touch "$(FRONTEND_LOG)"
	@tail -f "$(FRONTEND_LOG)"

logs-clear:
	@mkdir -p "$(LOG_DIR)"
	@> "$(FRONTEND_LOG)"
	@echo "$(GREEN)✓ Logs effacés$(NC)"

build:
	@test -d node_modules || { echo "$(RED)node_modules manquant. Lancez: make install$(NC)"; exit 1; }
	@echo "$(CYAN)Build production...$(NC)"
	@$(BUILD_CMD)
	@echo "$(GREEN)✓ Build → ../navin/web/dist$(NC)"

lint:
	@test -d node_modules || { echo "$(RED)node_modules manquant. Lancez: make install$(NC)"; exit 1; }
	@$(LINT_CMD)

clean:
	@rm -rf "$(LOG_DIR)" .vite coverage dist
	@rm -f frontend.log frontend.pid nohup.out
	@echo "$(GREEN)✓ Nettoyage frontend terminé$(NC)"

clean-all: clean uninstall
	@echo "$(RED)✓ Nettoyage complet frontend$(NC)"

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
NAVIN_API_URL ?= http://127.0.0.1:8765

LOG_DIR ?= $(CURDIR)/logs
FRONTEND_LOG ?= $(LOG_DIR)/frontend.log
FRONTEND_PID ?= $(LOG_DIR)/frontend.pid
VITE_MATCH ?= [v]ite

# Prefer bun when available (repo ships bun.lock), else npm.
HAS_BUN := $(shell command -v bun >/dev/null 2>&1 && echo yes || echo no)
ifeq ($(HAS_BUN),yes)
  PKG := bun
  INSTALL_CMD := bun install
  DEV_BIN := bun
  DEV_ARGS := run dev
  BUILD_CMD := bun run build
  LINT_CMD := bun run lint
else
  PKG := npm
  INSTALL_CMD := npm install
  DEV_BIN := npm
  DEV_ARGS := run dev
  BUILD_CMD := npm run build
  LINT_CMD := npm run lint
endif

help:
	@echo "$(CYAN)Navin WebUI — commandes disponibles:$(NC)"
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
	@if ! command -v bun >/dev/null 2>&1 && ! command -v npm >/dev/null 2>&1; then \
		echo "$(RED)bun ou npm requis$(NC)"; \
		exit 1; \
	fi
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

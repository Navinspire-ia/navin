# Shared helpers for scripts/install.sh, start.sh, stop.sh, restart.sh, status.sh.
# POSIX sh. Sourced only. Callers set ROOT first.

# shellcheck disable=SC2034

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

VENV="${ROOT}/.venv"
NAVIN="${VENV}/bin/navin"
NAVIN_CLI="${VENV}/bin/navin-cli"
WEBUI_DIR="${ROOT}/webui"
WEBUI_PORT_DEFAULT=5173
WEBUI_LOG_DIR="${WEBUI_DIR}/logs"
WEBUI_LOG="${WEBUI_LOG_DIR}/frontend.log"
WEBUI_PID="${WEBUI_LOG_DIR}/frontend.pid"

SCOPE="all"
DO_INSTALL=0
FG=0
SKIP_SYSTEM=0

info() { printf "%b%s%b\n" "$CYAN" "$*" "$NC"; }
ok() { printf "%b%s%b\n" "$GREEN" "$*" "$NC"; }
warn() { printf "%b%s%b\n" "$YELLOW" "$*" "$NC"; }
die() { printf "%b%s%b\n" "$RED" "$*" "$NC" >&2; exit 1; }

parse_dev_args() {
    SCOPE="all"
    DO_INSTALL=0
    FG=0
    SKIP_SYSTEM=0
    want_front=0
    want_back=0
    want_all=0
    for arg in "$@"; do
        case "$arg" in
            front|--front|--frontend) want_front=1 ;;
            backend|--backend) want_back=1 ;;
            all|--all) want_all=1 ;;
            --fg|--foreground) FG=1 ;;
            --install) DO_INSTALL=1 ;;
            --no-system|--skip-system) SKIP_SYSTEM=1 ;;
            -h|--help) return 2 ;;
            *) die "Option inconnue: $arg" ;;
        esac
    done
    if [ "$want_all" = "1" ] || { [ "$want_front" = "1" ] && [ "$want_back" = "1" ]; }; then
        SCOPE="all"
    elif [ "$want_front" = "1" ]; then
        SCOPE="front"
    elif [ "$want_back" = "1" ]; then
        SCOPE="backend"
    else
        SCOPE="all"
    fi
}

_config_port() {
    key="$1"
    default="$2"
    python3 -c "
import json, pathlib
p = pathlib.Path.home() / '.navin' / 'config.json'
print((json.load(open(p)).get(${key}) or {}).get('port', ${default}) if p.is_file() else ${default})
" 2>/dev/null || printf '%s\n' "$default"
}

webui_api_port() {
    python3 -c "
import json, pathlib
p = pathlib.Path.home() / '.navin' / 'config.json'
print((json.load(open(p)).get('channels') or {}).get('websocket', {}).get('port', 8765) if p.is_file() else 8765)
" 2>/dev/null || echo 8765
}

gateway_port() {
    _config_port "'gateway'" 18790
}

python_ok() {
    "$1" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null
}

find_python() {
    if [ -n "${PYTHON:-}" ]; then
        python_ok "$PYTHON" || die "PYTHON=$PYTHON doit etre Python 3.11+"
        printf '%s\n' "$PYTHON"
        return 0
    fi
    for c in python3.13 python3.12 python3.11 python3; do
        if command -v "$c" >/dev/null 2>&1 && python_ok "$c"; then
            command -v "$c"
            return 0
        fi
    done
    return 1
}

node_major() {
    node -p "parseInt(process.versions.node.split('.')[0], 10)" 2>/dev/null || echo 0
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1
}

detect_pm() {
    uname_s="$(uname -s 2>/dev/null || echo unknown)"
    if [ "$uname_s" = Darwin ]; then
        if need_cmd brew; then
            printf '%s\n' brew
        else
            printf '%s\n' macos-nobrew
        fi
        return 0
    fi
    if need_cmd apt-get; then
        printf '%s\n' apt
    elif need_cmd dnf; then
        printf '%s\n' dnf
    elif need_cmd yum; then
        printf '%s\n' yum
    elif need_cmd pacman; then
        printf '%s\n' pacman
    elif need_cmd zypper; then
        printf '%s\n' zypper
    else
        printf '%s\n' unknown
    fi
}

run_as_root() {
    if [ "$(id -u)" = "0" ]; then
        "$@"
    elif need_cmd sudo; then
        sudo "$@"
    else
        die "sudo requis pour: $*"
    fi
}

run_pkg() {
    pm="$1"
    shift
    case "$pm" in
        apt)
            run_as_root env DEBIAN_FRONTEND=noninteractive apt-get update -y
            run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
            ;;
        dnf)
            run_as_root dnf install -y "$@"
            ;;
        yum)
            run_as_root yum install -y "$@"
            ;;
        pacman)
            run_as_root pacman -Sy --needed --noconfirm "$@"
            ;;
        zypper)
            run_as_root zypper --non-interactive install -y "$@"
            ;;
        brew)
            brew install "$@"
            ;;
        *)
            die "Gestionnaire de paquets inconnu"
            ;;
    esac
}

ensure_system_deps() {
    if [ "${NAVIN_SKIP_SYSTEM:-0}" = "1" ] || [ "$SKIP_SYSTEM" = "1" ]; then
        return 0
    fi

    missing_py=0
    missing_node=0
    missing_make=0
    missing_git=0
    missing_ffmpeg=0
    find_python >/dev/null || missing_py=1
    if ! need_cmd node || [ "$(node_major)" -lt 18 ]; then
        missing_node=1
    fi
    need_cmd npm || missing_node=1
    need_cmd make || missing_make=1
    need_cmd git || missing_git=1
    need_cmd ffmpeg || missing_ffmpeg=1

    if [ "$missing_py" = "0" ] && [ "$missing_node" = "0" ] && [ "$missing_make" = "0" ] && [ "$missing_git" = "0" ]; then
        if [ "$missing_ffmpeg" = "1" ]; then
            warn "ffmpeg absent (media limite). Installation si le paquet existe."
        else
            return 0
        fi
    fi

    pm="$(detect_pm)"
    case "$pm" in
        macos-nobrew)
            die "macOS: installez Homebrew (https://brew.sh) puis relancez. Ou installez Python 3.11+, Node 18+, git et make."
            ;;
        unknown)
            die "Installez Python 3.11+, Node 18+ / npm, git et make, puis relancez."
            ;;
    esac

    info "Dependances systeme ($pm)..."
    case "$pm" in
        apt)
            pkgs="python3 python3-venv python3-pip python3-dev make git curl ca-certificates nodejs npm"
            if [ "$missing_py" = "1" ]; then
                pkgs="$pkgs python3.11 python3.11-venv python3.11-dev"
            fi
            [ "$missing_ffmpeg" = "1" ] && pkgs="$pkgs ffmpeg"
            # shellcheck disable=SC2086
            run_pkg apt $pkgs
            ;;
        dnf)
            pkgs="python3 python3-pip python3-devel make git gcc nodejs npm"
            # shellcheck disable=SC2086
            run_pkg dnf $pkgs
            if [ "$missing_ffmpeg" = "1" ]; then
                run_pkg dnf ffmpeg-free || run_pkg dnf ffmpeg || warn "ffmpeg non installe (depot rpmfusion ?)"
            fi
            ;;
        yum)
            pkgs="python3 python3-pip python3-devel make git gcc nodejs npm"
            [ "$missing_ffmpeg" = "1" ] && pkgs="$pkgs ffmpeg"
            # shellcheck disable=SC2086
            run_pkg yum $pkgs
            ;;
        pacman)
            pkgs="python python-pip base-devel git nodejs npm"
            [ "$missing_ffmpeg" = "1" ] && pkgs="$pkgs ffmpeg"
            # shellcheck disable=SC2086
            run_pkg pacman $pkgs
            ;;
        zypper)
            pkgs="python311 python311-pip make git nodejs npm"
            [ "$missing_ffmpeg" = "1" ] && pkgs="$pkgs ffmpeg"
            # shellcheck disable=SC2086
            run_pkg zypper $pkgs
            ;;
        brew)
            pkgs=""
            [ "$missing_py" = "1" ] && pkgs="$pkgs python@3.12"
            [ "$missing_node" = "1" ] && pkgs="$pkgs node"
            [ "$missing_git" = "1" ] && pkgs="$pkgs git"
            [ "$missing_make" = "1" ] && pkgs="$pkgs make"
            [ "$missing_ffmpeg" = "1" ] && pkgs="$pkgs ffmpeg"
            if [ -n "$pkgs" ]; then
                # shellcheck disable=SC2086
                run_pkg brew $pkgs
            fi
            ;;
    esac

    find_python >/dev/null || die "Python 3.11+ introuvable apres installation systeme."
    need_cmd node || die "Node.js introuvable apres installation systeme."
    [ "$(node_major)" -ge 18 ] || die "Node.js 18+ requis (actuel: $(node -v 2>/dev/null || echo absent))."
    need_cmd npm || die "npm introuvable."
    need_cmd git || die "git introuvable."
    need_cmd make || warn "make absent: utilisez sh scripts/install.sh et sh scripts/start.sh."
}

ensure_navin() {
    if [ ! -x "$NAVIN" ]; then
        die ".venv manquant. Lancez: make install   ou   sh scripts/install.sh"
    fi
}

install_backend() {
    py="$(find_python)" || die "Python 3.11+ requis."
    info "Installation backend ($py)..."
    if [ ! -d "$VENV" ]; then
        "$py" -m venv "$VENV" || die "Impossible de creer $VENV (paquet python3-venv ?)"
    fi
    "$VENV/bin/pip" install -U pip
    "$VENV/bin/pip" install -e "${ROOT}[dev]"
    if [ ! -x "$NAVIN" ] || [ ! -x "$NAVIN_CLI" ]; then
        die "Install backend incomplete: navin / navin-cli absents dans .venv/bin"
    fi
    "$VENV/bin/python" -c "from navin.agent.tools.sandbox import ensure_native_sandbox; p = ensure_native_sandbox(); print('navin-sandbox:', p or 'MISSING - rustup puis make native')" || true
    ok "Backend installe ($VENV)"
}

install_frontend() {
    need_cmd node || die "Node.js 18+ requis."
    need_cmd npm || die "npm requis."
    [ "$(node_major)" -ge 18 ] || die "Node.js 18+ requis (actuel: $(node -v))."
    [ -f "${WEBUI_DIR}/package-lock.json" ] || die "webui/package-lock.json introuvable."
    info "Installation frontend (npm ci)..."
    (cd "$WEBUI_DIR" && npm ci)
    ok "Frontend installe"
}

has_make() {
    need_cmd make && [ -f "${WEBUI_DIR}/Makefile" ]
}

start_backend_bg() {
    ensure_navin
    gp="$(gateway_port)"
    wp="$(webui_api_port)"
    info "Demarrage gateway (health ${gp}, webui ${wp})..."
    out="$("$NAVIN" gateway --background --port "$gp" 2>&1)" || code=$?
    code="${code:-0}"
    printf '%s\n' "$out"
    if [ "$code" -ne 0 ] && ! printf '%s\n' "$out" | grep -q "already_running"; then
        return "$code"
    fi
    health="http://127.0.0.1:${wp}/health"
    ghealth="http://127.0.0.1:${gp}/health"
    ready=0
    i=0
    while [ "$i" -lt 10 ]; do
        if curl -sfS "$health" >/dev/null 2>&1 || curl -sfS "$ghealth" >/dev/null 2>&1; then
            ready=1
            break
        fi
        i=$((i + 1))
        sleep 1
    done
    if [ "$ready" = "1" ]; then
        ok "Health OK: $health"
        return 0
    fi
    warn "Gateway non joignable sur $health"
    warn "Cause frequente: aucune cle API / modele dans ~/.navin/config.json"
    "$NAVIN" gateway logs --no-follow --tail 30 2>/dev/null || true
    return 1
}

start_backend_fg() {
    ensure_navin
    gp="$(gateway_port)"
    info "Demarrage navin gateway au premier plan (port ${gp})..."
    exec "$NAVIN" gateway --port "$gp"
}

start_front_bg() {
    if [ ! -d "${WEBUI_DIR}/node_modules" ]; then
        die "node_modules manquant. Lancez: make install   ou   sh scripts/install.sh front"
    fi
    if has_make; then
        make -C "$WEBUI_DIR" start-bg
        return $?
    fi
    mkdir -p "$WEBUI_LOG_DIR"
    if [ -f "$WEBUI_PID" ]; then
        pid="$(cat "$WEBUI_PID")"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            warn "Frontend deja lance (PID $pid)"
            return 0
        fi
        rm -f "$WEBUI_PID"
    fi
    api="http://127.0.0.1:$(webui_api_port)"
    info "Demarrage WebUI (proxy API ${api})..."
    (
        cd "$WEBUI_DIR" || exit 1
        NAVIN_API_URL="$api" nohup npm run dev >"$WEBUI_LOG" 2>&1 &
        echo $! >"$WEBUI_PID"
    )
    sleep 2
    pid="$(cat "$WEBUI_PID" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        ok "Frontend demarre (PID $pid)  http://127.0.0.1:${WEBUI_PORT_DEFAULT}"
        return 0
    fi
    rm -f "$WEBUI_PID"
    die "Echec frontend. Voir $WEBUI_LOG"
}

stop_backend() {
    if [ -x "$NAVIN" ]; then
        "$NAVIN" gateway stop || true
    else
        warn "Pas de .venv/bin/navin - rien a arreter cote gateway."
    fi
}

_kill_pidfile() {
    file="$1"
    if [ -f "$file" ]; then
        pid="$(cat "$file" 2>/dev/null || true)"
        if [ -n "$pid" ]; then
            kill "$pid" 2>/dev/null || true
            if need_cmd pkill; then
                pkill -P "$pid" 2>/dev/null || true
            fi
            sleep 1
            kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$file"
    fi
}

_kill_port() {
    port="$1"
    if need_cmd lsof; then
        for p in $(lsof -ti ":$port" 2>/dev/null); do
            kill "$p" 2>/dev/null || true
        done
        return 0
    fi
    if need_cmd fuser; then
        fuser -k "${port}/tcp" 2>/dev/null || true
    fi
}

stop_front() {
    if has_make; then
        make -C "$WEBUI_DIR" stop || true
        return 0
    fi
    _kill_pidfile "$WEBUI_PID"
    _kill_port "$WEBUI_PORT_DEFAULT"
    ok "Frontend arrete"
}

status_backend() {
    if [ ! -x "$NAVIN" ]; then
        warn "Backend: .venv absent"
        return 1
    fi
    "$NAVIN" gateway status || true
    wp="$(webui_api_port)"
    gp="$(gateway_port)"
    if curl -sfS "http://127.0.0.1:${wp}/health" >/dev/null 2>&1; then
        ok "Health OK: http://127.0.0.1:${wp}/health"
    elif curl -sfS "http://127.0.0.1:${gp}/health" >/dev/null 2>&1; then
        ok "Health OK: http://127.0.0.1:${gp}/health"
    else
        warn "Health: gateway / WebUI ne repondent pas"
        return 1
    fi
}

status_front() {
    if has_make; then
        make -C "$WEBUI_DIR" status
        return $?
    fi
    if [ -f "$WEBUI_PID" ]; then
        pid="$(cat "$WEBUI_PID")"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            ok "Frontend: en cours (PID $pid)"
            return 0
        fi
    fi
    warn "Frontend: arrete"
    return 1
}

print_urls() {
    wp="$(webui_api_port)"
    printf "  Gateway / WebUI integree : %bhttp://127.0.0.1:%s%b\n" "$CYAN" "$wp" "$NC"
    if [ "$SCOPE" = "all" ] || [ "$SCOPE" = "front" ]; then
        printf "  WebUI dev (hot reload)   : %bhttp://127.0.0.1:%s%b\n" "$CYAN" "$WEBUI_PORT_DEFAULT" "$NC"
    fi
    printf "  CLI source               : %s\n" "$NAVIN_CLI"
    printf "  Logs backend             : make logs\n"
    printf "  Logs frontend            : make -C webui logs\n"
    printf "  Arret                    : make stop   ou   sh scripts/stop.sh\n"
}

#!/bin/sh
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
#
# Keep one out-of-memory kill under WSL from stopping every terminal.
#
# WSL starts every terminal, and everything a terminal runs, in systemd's
# init.scope. With systemd's default OOMPolicy=stop, the kernel killing any
# one of those processes for memory makes systemd stop the whole scope: on
# 2026-11-05 one 68 MB browser process took eight navin-cli and all their
# terminals with it. A drop-in with OOMPolicy=continue limits the loss to the
# process the kernel killed. Only init.scope changes, the default of every
# other unit stays, and nothing happens outside WSL or without systemd.
#
# Usage: wsl_oom_guard.sh status|install|remove
#
# The same file is the postinst and the prerm of the Navin .deb and the %post
# and the %preun of the .rpm, which call it with their own arguments:
# configure, abort-*, 1 and 2 install; remove and 0 remove; upgrade and the
# other dpkg steps change nothing. The pacman package, the CLI installer, the
# Windows engine (through wsl.exe -u root) and navin doctor --install run it
# with install or remove.
#
# Prints one line, "navin-wsl-oom-guard: <state>: <detail>", which is what the
# callers read. Exits 0 even when the change failed, so that a failure never
# aborts a package operation; 2 without an argument.

DROPIN_DIR=/etc/systemd/system/init.scope.d
DROPIN=$DROPIN_DIR/90-navin-oom.conf
MARK='# Written by Navin'

say() {
    printf 'navin-wsl-oom-guard: %s\n' "$*"
}

on_wsl() {
    case "$(uname -r 2>/dev/null)" in
        *[Mm]icrosoft*|*WSL*) return 0 ;;
    esac
    return 1
}

# Empty when systemd does not run this system (WSL without systemd, a container).
oom_policy() {
    systemctl show init.scope -p OOMPolicy --value </dev/null 2>/dev/null
}

written_by_navin() {
    [ -f "$DROPIN" ] && grep -q "^$MARK" "$DROPIN" 2>/dev/null
}

is_root() {
    [ "$(id -u)" = 0 ]
}

# Written next to the target and renamed over it: a reader never sees half a file.
write_dropin() {
    mkdir -p "$DROPIN_DIR" || return 1
    tmp="$DROPIN.navin-$$"
    if ! {
        printf '%s: under WSL every terminal runs in init.scope, and with the\n' "$MARK"
        printf '# default OOMPolicy=stop one out-of-memory kill stops all of them.\n'
        printf '# Undo: delete this file, then run: systemctl daemon-reload\n'
        printf '[Scope]\nOOMPolicy=continue\n'
    } >"$tmp"; then
        rm -f "$tmp"
        return 1
    fi
    if ! chmod 644 "$tmp" || ! mv -f "$tmp" "$DROPIN"; then
        rm -f "$tmp"
        return 1
    fi
}

guard_status() {
    if ! on_wsl; then
        say "not-needed: not WSL"
        return
    fi
    policy=$(oom_policy)
    if [ -z "$policy" ]; then
        say "not-needed: systemd does not run this system"
    elif [ "$policy" != continue ]; then
        say "needed: init.scope OOMPolicy=$policy"
    elif written_by_navin; then
        say "protected: init.scope OOMPolicy=continue"
    else
        say "protected: init.scope OOMPolicy=continue (set outside Navin)"
    fi
}

guard_install() {
    if ! on_wsl; then
        say "skipped: not WSL"
        return
    fi
    policy=$(oom_policy)
    if [ -z "$policy" ]; then
        say "skipped: systemd does not run this system"
        return
    fi
    if [ "$policy" = continue ]; then
        say "present: init.scope OOMPolicy=continue"
        return
    fi
    if ! is_root; then
        say "failed: needs root (run it with sudo)"
        return
    fi
    if ! write_dropin; then
        say "failed: cannot write $DROPIN"
        return
    fi
    # Reloading reads unit files again; it restarts nothing that runs.
    if ! systemctl daemon-reload </dev/null 2>/dev/null; then
        say "failed: systemctl daemon-reload"
        return
    fi
    policy=$(oom_policy)
    if [ "$policy" != continue ]; then
        say "failed: init.scope still has OOMPolicy=${policy:-unknown}"
        return
    fi
    say "applied: init.scope OOMPolicy=continue"
}

guard_remove() {
    if ! written_by_navin; then
        if [ -f "$DROPIN" ]; then
            say "kept: $DROPIN was not written by Navin"
        else
            say "absent: nothing to remove"
        fi
        return
    fi
    if ! is_root; then
        say "failed: needs root (run it with sudo)"
        return
    fi
    if ! rm -f "$DROPIN"; then
        say "failed: cannot delete $DROPIN"
        return
    fi
    # Only an empty directory goes: other drop-ins there are not Navin's.
    rmdir "$DROPIN_DIR" 2>/dev/null
    if [ -n "$(oom_policy)" ] && ! systemctl daemon-reload </dev/null 2>/dev/null; then
        say "failed: systemctl daemon-reload"
        return
    fi
    say "removed: $DROPIN"
}

case "${1:-}" in
    status) guard_status ;;
    install | configure | abort-upgrade | abort-remove | abort-deconfigure | 1 | 2) guard_install ;;
    remove | purge | 0) guard_remove ;;
    '')
        echo "usage: wsl_oom_guard.sh status|install|remove" >&2
        exit 2
        ;;
    *) say "unchanged: nothing to do for $1" ;;
esac
exit 0

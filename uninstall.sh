#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'; NC='\033[0m'
info() { echo -e "${GREEN}[jbo]${NC} $*"; }

SOURCE_LINE='[ -f ~/.config/jbo/functions.sh ] && source ~/.config/jbo/functions.sh'

# ── path helpers ──────────────────────────────────────────────────────────────
_win_to_unix() {
    if command -v wslpath >/dev/null 2>&1; then
        wslpath -u "$1" 2>/dev/null || true
    elif command -v cygpath >/dev/null 2>&1; then
        cygpath -u "$1"
    fi
}

# Remove shell config
if [ -d "$HOME/.config/jbo" ]; then
    rm -rf "$HOME/.config/jbo"
    info "Removed ~/.config/jbo"
fi

# Remove jbo-wrap binary
if [ -f "$HOME/.local/bin/jbo-wrap" ]; then
    rm -f "$HOME/.local/bin/jbo-wrap"
    info "Removed ~/.local/bin/jbo-wrap"
fi

# Remove Windows handler files
if { [ -n "${MSYSTEM:-}" ] || [ -n "${MINGW_PREFIX:-}" ]; } && [ -n "${LOCALAPPDATA:-}" ]; then
    LOCALAPPDATA_WIN="$LOCALAPPDATA"
else
    LOCALAPPDATA_WIN=$(powershell.exe -NoProfile -Command 'Write-Output $env:LOCALAPPDATA' </dev/null 2>/dev/null | tr -d '\r')
fi
LOCALAPPDATA_UNIX=$(_win_to_unix "$LOCALAPPDATA_WIN")

# Remove new-style jbo/ subdirectory
if [ -d "$LOCALAPPDATA_UNIX/jbo" ]; then
    rm -rf "$LOCALAPPDATA_UNIX/jbo"
    info "Removed $LOCALAPPDATA_WIN\\jbo\\"
fi

# Remove old-style loose files (upgrade cleanup)
for f in jbo-handler.vbs jbo-handler.ps1; do
    if [ -f "$LOCALAPPDATA_UNIX/$f" ]; then
        rm -f "$LOCALAPPDATA_UNIX/$f"
        info "Removed $LOCALAPPDATA_WIN\\$f"
    fi
done

# Remove protocol registration
reg.exe delete "HKCU\\Software\\Classes\\jbo" /f </dev/null >& /dev/null && info "Removed jbo:// protocol registration"

# Remove source line from RC files
for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
    if [ -f "$rc" ] && grep -qF "$SOURCE_LINE" "$rc"; then
        grep -vF "$SOURCE_LINE" "$rc" > "$rc.jbo.tmp" && mv "$rc.jbo.tmp" "$rc"
        info "Removed source line from $rc"
    fi
done

echo ""
info "jbo uninstalled."

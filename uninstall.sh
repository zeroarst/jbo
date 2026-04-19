#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'; NC='\033[0m'
info() { echo -e "${GREEN}[jbo]${NC} $*"; }

SOURCE_LINE='[ -f ~/.config/jbo/functions.sh ] && source ~/.config/jbo/functions.sh'

# Remove shell config
if [ -d "$HOME/.config/jbo" ]; then
    rm -rf "$HOME/.config/jbo"
    info "Removed ~/.config/jbo"
fi

# Remove Windows handler files
LOCALAPPDATA_WIN=$(powershell.exe -NoProfile -Command 'Write-Output $env:LOCALAPPDATA' 2>/dev/null | tr -d '\r')
LOCALAPPDATA_WSL=$(wslpath "$LOCALAPPDATA_WIN")
for f in jbo-handler.vbs jbo-handler.ps1; do
    if [ -f "$LOCALAPPDATA_WSL/$f" ]; then
        rm -f "$LOCALAPPDATA_WSL/$f"
        info "Removed $LOCALAPPDATA_WIN\\$f"
    fi
done

# Remove protocol registration
reg.exe delete "HKCU\\Software\\Classes\\jbo" /f >& /dev/null && info "Removed jbo:// protocol registration"

# Remove source line from RC files
for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
    if [ -f "$rc" ] && grep -qF "$SOURCE_LINE" "$rc"; then
        # Use a temp file to avoid sed -i portability issues
        grep -vF "$SOURCE_LINE" "$rc" > "$rc.jbo.tmp" && mv "$rc.jbo.tmp" "$rc"
        info "Removed source line from $rc"
    fi
done

echo ""
info "jbo uninstalled."

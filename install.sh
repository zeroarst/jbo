#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://raw.githubusercontent.com/zeroarst/jbo/main"
INSTALL_DIR="$HOME/.config/jbo"
SOURCE_LINE='[ -f ~/.config/jbo/functions.sh ] && source ~/.config/jbo/functions.sh'

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
info()  { echo -e "${GREEN}[jbo]${NC} $*"; }
warn()  { echo -e "${YELLOW}[jbo]${NC} $*" >&2; }
error() { echo -e "${RED}[jbo]${NC} $*" >&2; }

# ── detect LOCALAPPDATA ───────────────────────────────────────────────────────
LOCALAPPDATA_WIN=$(powershell.exe -NoProfile -Command 'Write-Output $env:LOCALAPPDATA' </dev/null 2>/dev/null | tr -d '\r')
LOCALAPPDATA_WSL=$(wslpath "$LOCALAPPDATA_WIN" </dev/null)

# ── IDE discovery ─────────────────────────────────────────────────────────────
find_ide() {
    local pattern="$1"
    powershell.exe -NoProfile -Command "
        \$paths = @(
            'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
            'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
            'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
        )
        \$found = Get-ItemProperty \$paths -ErrorAction SilentlyContinue |
            Where-Object { \$_.DisplayName -match '$pattern' } |
            Select-Object -ExpandProperty InstallLocation -First 1
        if (\$found) { Write-Output \$found }
    " </dev/null 2>/dev/null | tr -d '\r'
}

detect_exe() {
    local dir="$1" exe_name="$2"
    if [ -n "$dir" ]; then
        local wsl_dir
        wsl_dir=$(wslpath "$dir" </dev/null 2>/dev/null || true)
        if [ -f "$wsl_dir/bin/$exe_name" ]; then
            echo "$dir\\bin\\$exe_name"
            return
        fi
    fi
    # Fallback: scan common locations
    powershell.exe -NoProfile -Command "
        Get-ChildItem 'C:\','D:\' -Recurse -Depth 8 -ErrorAction SilentlyContinue |
        Where-Object { \$_.Name -eq '$exe_name' } |
        Select-Object -ExpandProperty FullName -First 1
    " </dev/null 2>/dev/null | tr -d '\r'
}

echo ""
info "Detecting JetBrains IDEs..."

WS_DIR=$(find_ide "WebStorm")
AS_DIR=$(find_ide "Android Studio")
IJ_DIR=$(find_ide "IntelliJ IDEA")

WS_EXE=$(detect_exe "$WS_DIR" "webstorm64.exe")
AS_EXE=$(detect_exe "$AS_DIR" "studio64.exe")
IJ_EXE=$(detect_exe "$IJ_DIR" "idea64.exe")

echo ""
echo "  WebStorm       : ${WS_EXE:-not found}"
echo "  Android Studio : ${AS_EXE:-not found}"
echo "  IntelliJ IDEA  : ${IJ_EXE:-not found}"
echo ""

prompt_override() {
    local name="$1" current="$2"
    local answer
    if [ -z "$current" ]; then
        warn "$name not found automatically."
        if [ -t 0 ]; then
            printf "  Enter Windows path to %s exe (or leave blank to skip): " "$name" >&2
            read -r answer
            echo "$answer"
        else
            echo ""
        fi
    else
        if [ -t 0 ]; then
            printf "  %s: %s — correct? [Y/n] " "$name" "$current" >&2
            read -r answer
            case "$answer" in
                [nN]*) printf "  Enter correct path: " >&2; read -r answer; echo "$answer";;
                *)     echo "$current";;
            esac
        else
            echo "$current"
        fi
    fi
}

WS_EXE=$(prompt_override  "WebStorm"       "$WS_EXE")
AS_EXE=$(prompt_override  "Android Studio" "$AS_EXE")
IJ_EXE=$(prompt_override  "IntelliJ IDEA"  "$IJ_EXE")

# ── download templates ────────────────────────────────────────────────────────
echo ""
info "Installing to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"

if command -v curl >/dev/null 2>&1; then
    fetch() { curl -fsSL "$1" -o "$2"; }
elif command -v wget >/dev/null 2>&1; then
    fetch() { wget -qO "$2" "$1"; }
else
    error "curl or wget is required."
    exit 1
fi

# If running from a local clone use local files, otherwise download
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/src/functions.sh" ]; then
    cp "$SCRIPT_DIR/src/functions.sh"    "$INSTALL_DIR/functions.sh"
    cp "$SCRIPT_DIR/src/jbo-handler.ps1" "$LOCALAPPDATA_WSL/jbo-handler.ps1"
    cp "$SCRIPT_DIR/src/jbo-handler.vbs" "$LOCALAPPDATA_WSL/jbo-handler.vbs"
else
    fetch "$REPO_URL/src/functions.sh"    "$INSTALL_DIR/functions.sh"
    fetch "$REPO_URL/src/jbo-handler.ps1" "$LOCALAPPDATA_WSL/jbo-handler.ps1"
    fetch "$REPO_URL/src/jbo-handler.vbs" "$LOCALAPPDATA_WSL/jbo-handler.vbs"
fi

# ── substitute placeholders ───────────────────────────────────────────────────
sub() {
    local placeholder="$1" value="$2" file="$3"
    [ -n "$value" ] && sed -i "s|${placeholder}|${value}|g" "$file"
}

sub "__WEBSTORM_EXE__" "$WS_EXE" "$INSTALL_DIR/functions.sh"
sub "__AS_EXE__"       "$AS_EXE" "$INSTALL_DIR/functions.sh"
sub "__IJ_EXE__"       "$IJ_EXE" "$INSTALL_DIR/functions.sh"

sub "__WEBSTORM_EXE__" "$WS_EXE" "$LOCALAPPDATA_WSL/jbo-handler.ps1"
sub "__AS_EXE__"       "$AS_EXE" "$LOCALAPPDATA_WSL/jbo-handler.ps1"
sub "__IJ_EXE__"       "$IJ_EXE" "$LOCALAPPDATA_WSL/jbo-handler.ps1"

# ── register jbo:// protocol ──────────────────────────────────────────────────
info "Registering jbo:// protocol handler..."
VBS_WIN="$LOCALAPPDATA_WIN\\jbo-handler.vbs"
reg.exe add "HKCU\\Software\\Classes\\jbo"                   /ve /d "URL:JetBrains Open Protocol" /f </dev/null >& /dev/null
reg.exe add "HKCU\\Software\\Classes\\jbo"                   /v "URL Protocol" /d "" /f </dev/null >& /dev/null
reg.exe add "HKCU\\Software\\Classes\\jbo\\shell\\open\\command" /ve \
    /d "wscript.exe \"$VBS_WIN\" \"%1\"" /f </dev/null >& /dev/null

# ── update RC files ───────────────────────────────────────────────────────────
updated_any=false
for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
    if [ -f "$rc" ] && ! grep -qF "$SOURCE_LINE" "$rc"; then
        echo "" >> "$rc"
        echo "$SOURCE_LINE" >> "$rc"
        info "Added source line to $rc"
        updated_any=true
    fi
done

# ── done ──────────────────────────────────────────────────────────────────────
echo ""
info "Installation complete!"
echo ""
echo "  Restart your terminal, or run:"
echo "    source ~/.config/jbo/functions.sh"
echo ""
echo "  Usage:"
echo "    wso src/Foo.ts:42          # open in WebStorm"
echo "    aso app/src/Main.kt:10     # open in Android Studio"
echo "    ijo src/Main.java:5        # open in IntelliJ"
echo "    echo \"\$(ws_link src/Foo.ts:42)\"  # clickable link"
echo ""

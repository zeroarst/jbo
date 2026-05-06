# JBO: JetBrains WSL File Navigation
# Installed by jbo — https://github.com/zeroarst/jbo

_JB_WEBSTORM='__WEBSTORM_EXE__'
_JB_ANDROIDSTUDIO='__AS_EXE__'
_JB_INTELLIJ='__IJ_EXE__'

_jb_to_winpath() {
    if command -v wslpath >/dev/null 2>&1; then
        wslpath -w "$1"
    elif command -v cygpath >/dev/null 2>&1; then
        cygpath -w "$1"
    else
        local p="$1"
        case "$p" in
            /[a-zA-Z]/*)
                local drive="${p:1:1}"
                p="${drive^^}:${p:2}"
                echo "${p//\//\\}";;
            *) echo "${p//\//\\}";;
        esac
    fi
}

_jb_open() {
    case "$1" in
        *__*__*) echo "[jbo] IDE not configured: $1. Edit ~/.config/jbo/functions.sh"; return 1;;
    esac
    local exe="$1" spec="$2" file line winpath
    file="${spec%:*}"; line="${spec##*:}"
    [ "$file" = "$spec" ] && line=1
    winpath=$(_jb_to_winpath "$(realpath -m "$file")")
    powershell.exe -NoProfile -NonInteractive -Command \
        "& '$exe' --line $line '$winpath'" 2>/dev/null
}

wso() { _jb_open "$_JB_WEBSTORM"       "$1"; }   # WebStorm
aso() { _jb_open "$_JB_ANDROIDSTUDIO"  "$1"; }   # Android Studio
ijo() { _jb_open "$_JB_INTELLIJ"       "$1"; }   # IntelliJ IDEA

_jb_link() {
    local ide="$1" spec="$2" file line abspath winpath
    file="${spec%:*}"; line="${spec##*:}"
    [ "$file" = "$spec" ] && line=1
    abspath=$(realpath -m "$file")
    if [ "${TERMINAL_EMULATOR#*JetBrains}" != "$TERMINAL_EMULATOR" ]; then
        if command -v wslpath >/dev/null 2>&1; then
            printf '%s:%s' "$abspath" "$line"
        else
            printf '%s:%s' "$(_jb_to_winpath "$abspath")" "$line"
        fi
    else
        winpath=$(_jb_to_winpath "$abspath" | tr '\\' '/')
        printf '\e]8;;jbo://open?ide=%s&file=%s&line=%s\e\\%s\e]8;;\e\\' \
            "$ide" "$winpath" "$line" "$spec"
    fi
}

ws_link() { _jb_link "webstorm"       "$1"; }   # WebStorm
as_link() { _jb_link "androidstudio"  "$1"; }   # Android Studio
ij_link() { _jb_link "intellij"       "$1"; }   # IntelliJ IDEA

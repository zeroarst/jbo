# jbo — JetBrains WSL File Navigation

Open any file at a specific line in a JetBrains IDE directly from your WSL terminal — with clickable links in Windows Terminal and native hyperlinks in the JetBrains built-in terminal. Works great with AI coding agents (Claude Code, Codex) that output `file:line` references.

```bash
wso src/pages/Foo.ts:42          # open in WebStorm
aso app/src/main/java/Foo.kt:10  # open in Android Studio
ijo src/main/java/Foo.java:5     # open in IntelliJ IDEA

echo "Error at $(ws_link src/pages/Foo.ts:42)"   # clickable link in terminal
```

## Install

**One-liner:**
```bash
curl -fsSL https://raw.githubusercontent.com/zeroarst/jbo/main/install.sh | bash
```

**Or clone and run:**
```bash
git clone https://github.com/zeroarst/jbo && cd jbo && bash install.sh
```

The installer will:
1. Auto-detect your installed JetBrains IDEs
2. Ask you to confirm (or enter) the paths
3. Install shell functions to `~/.config/jbo/functions.sh`
4. Install a Windows protocol handler (`jbo://`) for clickable terminal links
5. Add a source line to your `~/.zshrc` / `~/.bashrc`

## Uninstall

```bash
bash uninstall.sh
# or if installed via curl:
curl -fsSL https://raw.githubusercontent.com/zeroarst/jbo/main/uninstall.sh | bash
```

## Usage

### Direct open

```bash
wso src/Foo.ts:42          # WebStorm
aso app/src/Main.kt:10     # Android Studio
ijo src/Main.java:5        # IntelliJ IDEA
```

Works from anywhere — the path is resolved relative to your current directory.

### Clickable links in terminal output

```bash
echo "Error at $(ws_link src/Foo.ts:42)"
echo "Crash at $(as_link app/src/Main.kt:10)"
echo "See $(ij_link src/Main.java:5)"
```

| Terminal | Behaviour |
|---|---|
| JetBrains built-in | Outputs `/mnt/d/…/Foo.ts:42` — IDE auto-hyperlinks `path:line` natively |
| Windows Terminal | Emits an OSC 8 hyperlink → `jbo://` protocol → opens IDE at the line |

## AI Agent Integration

If you use AI coding assistants in your terminal — [Claude Code](https://claude.ai/code), OpenAI Codex, or similar — jbo turns their file references into one-click IDE jumps.

**JetBrains built-in terminal:** No setup needed. The IDE already auto-hyperlinks any `path:line` pattern, so every file reference an agent outputs is instantly clickable.

**Windows Terminal:** Add the snippet below to your project's `CLAUDE.md` (or `AGENTS.md` / `GEMINI.md`) so the agent emits clickable OSC 8 hyperlinks via the jbo helpers:

```markdown
## File References
This project uses [jbo](https://github.com/zeroarst/jbo) for IDE navigation.
When running shell commands that reference a file at a specific line, wrap the
reference with the appropriate link helper so it becomes a clickable link in
Windows Terminal:

    echo "See $(ws_link src/Foo.ts:42)"      # WebStorm
    echo "See $(as_link app/Main.kt:10)"     # Android Studio
    echo "See $(ij_link src/Main.java:5)"    # IntelliJ IDEA
```

## How It Works

The core mechanism is `webstorm64.exe --line <n> <file>`. All JetBrains IDE CLIs support this flag and forward to an already-running instance.

For clickable links in Windows Terminal, a custom `jbo://` URL protocol is registered. Clicking a link runs `jbo-handler.vbs` (windowless, no flash) → `jbo-handler.ps1` → `ide64.exe --line <n> <file>`.

### Why not `jetbrains://` or `webstorm://` URLs?

| Protocol | Problem |
|---|---|
| `jetbrains://webstorm/navigate/reference?…` | `jetbrainsd` mangles the URL (adds double slash, strips query params). IDE never receives it correctly. |
| `webstorm://navigate/reference?…` | "Unsupported protocol" — `navigate` only works under `jetbrains://` internally. |
| `webstorm://open?file=…&line=…` | Received by IDE but doesn't navigate to a specific line. |

## Manual Configuration

After installation, edit `~/.config/jbo/functions.sh` to change IDE paths:

```sh
_JB_WEBSTORM='C:\Users\you\AppData\Local\Programs\WebStorm\bin\webstorm64.exe'
_JB_ANDROIDSTUDIO='C:\Program Files\Android\Android Studio\bin\studio64.exe'
_JB_INTELLIJ='C:\Program Files\JetBrains\IntelliJ IDEA 2024.1\bin\idea64.exe'
```

## Requirements

- WSL2 (Ubuntu or any distro with `bash`/`zsh`)
- Windows Terminal or JetBrains built-in terminal
- At least one JetBrains IDE installed on Windows
- `curl` or `wget` (for the one-liner install)

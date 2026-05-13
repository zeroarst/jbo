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

**Recommended: launch the agent through `jbo-wrap`.** No `CLAUDE.md` snippet, no agent-side prompting — the wrapper auto-detects paths in the agent's terminal output and converts them to clickable OSC 8 hyperlinks:

```bash
cd /your/project
jbo-wrap claude         # or:  jbo-wrap codex, jbo-wrap gemini, jbo-wrap aider
```

The first time you run it in a project, jbo-wrap prompts you to pick which IDE its links should open in (auto-recommending based on project files — e.g. `package.json` → WebStorm, `AndroidManifest.xml` → Android Studio). The choice is remembered in `~/.config/jbo/projects.conf` so future runs are silent. See [Per-project IDE preference](#per-project-ide-preference) below.

Any `src/foo.ts:42` (or `package.json`, or absolute path) the agent prints becomes clickable in Windows Terminal. See [Auto-linkify command output](#auto-linkify-command-output-jbo-wrap) below for the full behaviour.

**JetBrains built-in terminal:** No setup needed regardless. The IDE already auto-hyperlinks any `path:line` pattern, so every file reference an agent outputs is instantly clickable.

**Fallback (Windows Terminal without `jbo-wrap`):** if you run the agent directly without wrapping it, add the snippet below to your project's `CLAUDE.md` (or `AGENTS.md` / `GEMINI.md`) so the agent emits clickable OSC 8 hyperlinks via the jbo helpers:

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

## Auto-linkify command output (`jbo-wrap`)

`jbo-wrap` runs any command and rewrites file paths in its output into clickable OSC 8 hyperlinks — no manual `ws_link` calls, no agent-side prompt tweaks.

```bash
cd /your/project
jbo-wrap claude              # Claude Code's file refs become clickable
jbo-wrap npm test            # Jest/Vitest error stack paths clickable
jbo-wrap make build          # Compiler errors clickable
```

**What it detects:**

| Pattern | Example | Linkified? |
|---|---|---|
| Absolute Unix path with line | `/mnt/d/proj/src/foo.ts:42` | Always |
| Absolute Windows path with line | `C:\proj\src\foo.go:7` | Always |
| Relative path that **exists** on disk | `src/foo.js:42` | If file exists |
| Relative path without `:line` | `package.json` | If file exists (opens at line 1) |
| URLs | `https://example.com/foo.js` | Never |
| Relative path that **does not exist** | `src/typo.js:42` | Never (passes through unchanged) |

**Resolution base:** relative paths are resolved against the directory where you ran `jbo-wrap` (its startup CWD). For wrapping a long-running command like `jbo-wrap claude`, this means: `cd` into your project root first, then run `jbo-wrap claude`. All relative paths in the agent's output resolve against that root for the whole session.

**Opt-out:** set `JBO_AUTODETECT=0` to disable relative-path detection and fall back to absolute-only behaviour:

```bash
JBO_AUTODETECT=0 jbo-wrap npm test
```

**Works through ANSI rendering:** Modern terminal apps (Claude Code, Codex, prompt-toolkit-based REPLs) often emit cursor-positioning escape sequences in place of literal spaces between tokens — e.g. `text\x1b[1Csrc/foo.js:9`. jbo-wrap treats CSI/OSC escapes as path-token boundaries and strips any pre-existing OSC 8 hyperlinks before adding its own, so paths in those tools' output linkify cleanly.

**Debugging:** if a path isn't linkifying in some program's output, set `JBO_DEBUG_LOG=/tmp/jbo.log` before launching jbo-wrap. Every PTY read chunk is dumped as Python `repr()` so you can see the exact bytes upstream is sending.

### Per-project IDE preference

`jbo-wrap` remembers which IDE each project's links should open in, so you don't have to set `JBO_IDE` globally or get the wrong one when a path is clicked.

**First run in a project** — `jbo-wrap` walks up to the nearest `.git/` (or stays in CWD if there's none), shows an arrow-key picker pre-selecting an IDE based on project markers, and saves your choice:

```
jbo-wrap: choose IDE for this project
  Project root: /mnt/d/repos/my-app
  ↑/↓ move · Enter select · s skip · q/Esc quit

  ▸ WebStorm   ← recommended
    Android Studio
    IntelliJ IDEA
    Skip — use default this time, ask again next run
```

Keys: `↑/↓` (or `k/j`) to move, `Enter` to confirm, `s` to skip without saving, `q` or `Esc` to quit. If raw-mode stdin isn't available (rare — some CI shells), the picker degrades to a numeric prompt.

**Subsequent runs** — silent passthrough; the saved IDE is used.

**Auto-detect heuristic** (highest score wins, tie → WebStorm):

| Marker file(s) | → IDE |
|---|---|
| `AndroidManifest.xml`, `local.properties`, `build.gradle` with `com.android` | Android Studio |
| `pom.xml`, `build.sbt`, `*.iml`, plain `build.gradle*`/`settings.gradle*` | IntelliJ IDEA |
| `package.json`, `tsconfig.json`, `angular.json`, `next.config.*`, `nuxt.config.*`, `vite.config.*`, `svelte.config.*` | WebStorm |

**Managing the saved choice:**

```bash
jbo-wrap --show-config              # print the IDE set for the current project
jbo-wrap --reconfigure claude       # re-prompt, then run the command
jbo-wrap --validate                 # scan saved projects, offer to prune missing dirs
```

**Storage format** — `~/.config/jbo/projects.conf`, one project per line:

```
/mnt/d/repos/my-app=webstorm
/mnt/d/repos/my-android-app=androidstudio
```

Safe to hand-edit if you prefer.

**Non-interactive environments** (CI, piped stdin, no TTY): jbo-wrap skips the picker silently and falls back to `$JBO_IDE` (or `webstorm`). Set `JBO_SKIP_INIT=1` to force the same behaviour interactively.

**Limitations:**

- jbo-wrap holds the startup CWD for the whole run. It does not track `cd` inside a wrapped shell, so wrapping `zsh`/`bash` is not the intended use.
- On Git Bash (no Python), jbo-wrap falls back to an AWK pipe that supports **absolute paths only** — the relative-path / existence-check feature requires Python.

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

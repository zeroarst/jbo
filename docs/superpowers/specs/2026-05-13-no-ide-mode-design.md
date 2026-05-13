# jbo-wrap: "No IDE" / file:// link mode

**Status:** approved, ready for implementation plan
**Date:** 2026-05-13

## Problem

`jbo-wrap` currently routes every linkified path through the `jbo://` protocol, which delegates to a JetBrains IDE. Three user groups are poorly served:

1. **Users without JetBrains IDEs installed** — they can't benefit from `jbo-wrap`'s auto-linkification at all.
2. **Users who want links but prefer their OS-default opener** for a specific project (e.g. opening Markdown in Typora, JSON in VS Code).
3. **The "Skip" picker option** — currently means "don't save, fall back to webstorm this run." It exists to delay the decision but provides no real opt-out from IDE-routed links. If a user doesn't want IDE links, there is nothing for them to pick.

## Solution

Add a fourth persistent picker option — **"No IDE — open as file (no line navigation)"** — that, when chosen, makes `jbo-wrap` emit standard `file:///` URLs instead of `jbo://` URLs. Remove the existing "Skip" option from the menu; its escape-hatch behavior (abort without saving, fall back to recommended IDE for one run) is retained via `q` / `Esc` / `Ctrl-C` only.

### Picker — before / after

**Before:**

```
jbo-wrap: choose IDE for this project
  Project root: /mnt/d/repos/my-app
  ↑/↓ move · Enter select · s skip · q/Esc quit

  ❯ WebStorm   ← recommended
    Android Studio
    IntelliJ IDEA
    Skip — use default this time, ask again next run
```

**After:**

```
jbo-wrap: choose IDE for this project
  Project root: /mnt/d/repos/my-app
  ↑/↓ move · Enter select · q/Esc quit

  ❯ WebStorm   ← recommended
    Android Studio
    IntelliJ IDEA
    No IDE — open as file (no line navigation)
```

`q` / `Esc` / `Ctrl-C` still abort the picker silently: no save, fall back to recommended IDE for this one run, prompt again next run. This is the same "I don't want to commit yet" escape hatch that "Skip" used to provide, just keyboard-only.

### Storage

`~/.config/jbo/projects.conf` gains a new value for the IDE field: `file`.

```
/mnt/d/repos/my-android-app=androidstudio
/mnt/d/repos/my-typora-notes=file
```

Existing entries are untouched. Validation logic accepts `file` as a valid value.

### URL emission

When the resolved IDE is `file`, the linkifier emits:

```
file:///D:/repos/my-app/src/foo.js#L42
```

instead of:

```
jbo://open?ide=webstorm&file=D:/repos/my-app/src/foo.js&line=42
```

**Rationale for `#L42` fragment:**

- VS Code, Cursor, and a few other editors honor it natively (`Goto File:Line`).
- Other openers (Notepad, default Markdown viewers, the Windows file association dialog) ignore the fragment harmlessly.
- It is part of the URL syntax, so terminals render it as part of the hyperlink tooltip without breaking the click behavior.

When the matched path lacks a `:line` suffix (autodetect mode), the fragment is omitted: `file:///D:/repos/foo/bar.md`.

### Visible label

Unchanged. The user still sees `src/foo.js:42` in their terminal; only the underlying OSC 8 URL differs.

## Affected files

| File | Change |
|---|---|
| `src/jbo-wrap` (bash) | Picker (TUI + numeric fallback): remove `__skip__` entry, add `file` entry labelled "No IDE — open as file (no line navigation)". Update help text (drop `s skip` keybind). |
| `src/jbo-wrap` (awk fallback) | Branch on `IDE == "file"` and emit `file:///path#L<line>` instead of `jbo://…`. Keep absolute-path-only limitation. |
| `src/jbo-wrap.py` | `Linkifier._build_url`: when `self._ide_b == b'file'`, emit `file:///<path>#L<line>` (or no fragment if no line). |
| `tests/test_jbo_wrap.py` | New tests: `file://` URL emission with line, without line, and through the awk fallback. |
| `README.md` | Document the new picker option, the line-navigation tradeoff, the VS Code/Cursor fragment behavior, and a `--reconfigure` hint for switching an existing saved project to "No IDE". |

## Out of scope

- The shell functions `wso` / `aso` / `ijo` / `ws_link` / `as_link` / `ij_link` — they're explicit-IDE commands by name; adding a "no IDE" variant doesn't fit their shape.
- The Go protocol handler (`src/jbo-handler/main.go`) — `file://` URLs bypass it entirely, so no changes are needed.
- The install/uninstall scripts — the new mode requires no extra binaries or registry entries.
- Auto-detection of the user's OS file associations — out of scope; we just emit `file://` and trust the OS.

## Migration

Existing entries in `~/.config/jbo/projects.conf` keep their current IDE. The README will note that users wanting to switch a saved project to "No IDE" mode can run `jbo-wrap --reconfigure <cmd>` and pick the new option from the prompt.

## Tradeoffs

- **No portable line navigation.** Users selecting "No IDE" lose the ability to jump to a specific line in arbitrary editors. The `#L42` fragment helps VS Code / Cursor users; everyone else gets file-only navigation. This is documented at the picker label and in the README.
- **No way to mix per-file behavior.** A project is either "all IDE" or "all file://". Per-file-extension routing (e.g. open `.md` in Typora but `.js` in WebStorm) is not modeled.
- **Awk fallback parity.** Git Bash users still suffer the absolute-path-only limitation of the awk branch, but they now get `file://` mode there too.

## Verification

- Picker shows the new option, no longer shows Skip.
- `q` / `Esc` / `Ctrl-C` still abort with the old "fall back, don't save" semantics.
- Picking "No IDE" writes `<root>=file` to `projects.conf`.
- Running `jbo-wrap <cmd>` in a `file`-saved project produces output where path tokens are wrapped in OSC 8 `file:///…#L<n>` hyperlinks.
- Clicking such a link in Windows Terminal opens the file in the OS default application.
- Same behavior via the awk fallback (Git Bash with no Python).
- Existing IDE-saved projects continue to emit `jbo://` URLs unchanged.

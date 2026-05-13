#!/usr/bin/env python3
"""PTY wrapper for jbo-wrap — Unix pty (WSL) or Windows ConPTY (pywinpty).

Detects file-path tokens in the wrapped command's output and rewrites them as
OSC 8 hyperlinks pointing at `jbo://open?ide=…&file=…&line=…`. Two modes:

* autodetect (default): matches both absolute and relative paths; a relative
  path is only linkified when the resolved file actually exists on disk
  (`os.path.exists`). Resolution base is the wrapper's startup CWD.
* legacy (`JBO_AUTODETECT=0`): the original absolute-path-only behaviour,
  no filesystem checks.

Windows-style paths (`C:\\…`) are always treated as trusted absolutes — they
bypass the existence check because the handler resolves them on the Windows
side.
"""
import os
import re
import sys
import threading
from pathlib import Path
from urllib.parse import quote


# ── regexes ──────────────────────────────────────────────────────────────────

# Legacy: absolute-only with mandatory :line. The lookbehind prevents matching
# a `/...` substring inside a relative path like `src/foo.js:42`.
_LEGACY_RE = re.compile(
    rb'(?<![\w./\\:\-])'
    rb'((?:[A-Za-z]:[/\\]|/)[ -~]*?\.[a-zA-Z0-9]{1,10}):(\d+)'
    rb'(?![\w/\\])'
)

# Autodetect: absolute OR relative, optional :line. The left/right lookarounds
# refuse to start mid-token; the lookbehind also excludes `:` so URLs like
# `https://x/y.js` never start a match (the slash after `:` is in the exclusion
# class, blocking attempts after the scheme).
_FULL_RE = re.compile(
    rb'(?<![\w./\\:\-])'
    rb'((?:[A-Za-z]:[\\/])?[\w./\\\-]+\.[a-zA-Z0-9]{1,10})'
    rb'(?::(\d+))?'
    rb'(?![\w/\\])'
)

_WIN_ABS = re.compile(r'^[A-Za-z]:[\\/]')

# CSI escape sequence: ESC [ <params> <final> where final is 0x40..0x7E.
# Covers SGR (colours), cursor positioning (e.g. \x1b[1C used by Claude Code
# in place of spaces between tokens — this was the root cause of paths failing
# to linkify inside Claude Code's terminal renderer).
_CSI_RE = re.compile(rb'\x1b\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]')

# OSC escape sequence: ESC ] <params> ST (BEL or ESC \). Includes OSC 8
# hyperlinks. We strip OSC 8 wrappers from output so jbo-wrap's links don't
# nest with any pre-existing hyperlinks emitted upstream.
_OSC_RE = re.compile(rb'\x1b\].*?(?:\x07|\x1b\\)', re.DOTALL)

# Soft-wrap byte sequences a TUI renderer (e.g. Claude Code) inserts when it
# hard-wraps a long path that exceeds terminal width. When flanked on both
# sides by token-class bytes, we treat them as a no-op separator so the path
# matches as one token.
#
# A wrap "zone" is centred on an anchor — either a bare LF (with one or more
# leading `\r`, which is what raw-PTY CRLF translation produces) or a CSI
# Cursor-Down (`\x1b[<n>B`) or CSI Next-Line (`\x1b[<n>E`). Around the anchor
# we tolerate any combination of CR, other CSI escapes (colors, cursor-right,
# clearing), literal whitespace, and box/block Unicode characters — those
# make up the right-pad of the source row and the left-margin of the next
# row. Claude Code's two observed word-wrap shapes both fit this:
#   - streaming refresh:  `\r\x1b[<n>C\x1b[<n>B`
#   - committed render:   `\r\r\n\x1b[<n>C`
# Plain `\n` / `\r` standalone CAN match (zero fillers either side), but the
# soft-wrap classifier only absorbs the match when both flanks are token
# bytes and the trailing prefix isn't already a complete path token — so
# newline-separated distinct paths still stay separate.
_WRAP_RE = re.compile(
    # pre-anchor: up to 256 "filler atoms" (CR, any CSI escape, space/tab,
    # or a single 3-byte UTF-8 box/block char in U+2500..U+259F)
    rb'(?:\r|\x1b\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]'
    rb'|[ \t]|\xe2[\x94-\x96][\x80-\xbf]){0,256}'
    # anchor: LF, CSI cursor-down, or CSI next-line
    rb'(?:\n|\x1b\[\d*[BE])'
    # post-anchor: up to 64 filler atoms (typical left-margin)
    rb'(?:\r|\x1b\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]'
    rb'|[ \t]|\xe2[\x94-\x96][\x80-\xbf]){0,64}'
)

# Trailing complete-token marker: a token run ending in `.ext` (or
# `.ext:line`) looks like a finished path — so a wrap immediately after must
# be separating distinct paths, not splitting one. Used to suppress soft-wrap
# absorption. The `:line` suffix matters when a TUI renderer streams a list
# of `path.ext:line` entries with cursor-down between them (the soft-wrap
# classifier would otherwise sentinelise the inter-path bytes to `_`, which
# sits in `\w` and breaks every subsequent path's regex lookbehind).
_COMPLETE_EXT = re.compile(rb'\.[a-zA-Z0-9]{1,10}(?::\d+)?$')

# Bytes that can be part of a path-shaped token. Matches the character class
# used in `_FULL_RE`/`_LEGACY_RE`. We use this to find the chunk-boundary cut:
# if a chunk ends in a run of these bytes, that tail might be the start of a
# path whose tail arrives in the next chunk.
_TOKEN_BYTES = frozenset(
    bytes(range(ord('A'), ord('Z') + 1))
    + bytes(range(ord('a'), ord('z') + 1))
    + bytes(range(ord('0'), ord('9') + 1))
    + b'_./\\:-'
)
_MAX_BUFFER = 8192  # cap held-back bytes so a no-whitespace stream still flows

# DEC-private-mode disables written before exit so the outer terminal recovers
# even if the wrapped child died without emitting its own teardown. The set
# below is safe to emit unconditionally — disabling an un-enabled mode is a
# no-op on every terminal that implements them. `?1049l` is the exception:
# some terminals interpret "leave alternate screen" as "switch back and clear
# current view," which wipes whatever the child printed last (e.g. Claude's
# `Resume this session with: claude --resume <uuid>` summary). It's emitted
# from `_build_teardown` only when an unmatched `?1049h` was actually seen.
_TERMINAL_RESET_SAFE = (
    b'\x1b[<u'        # pop kitty keyboard stack
    b'\x1b[=0;1u'     # clear kitty keyboard flags (belt + suspenders)
    b'\x1b[?1004l'    # focus tracking off
    b'\x1b[?2004l'    # bracketed paste off
    b'\x1b[?1006l'    # SGR mouse off
    b'\x1b[?1003l'    # any-event mouse tracking off
    b'\x1b[?1002l'    # button-event mouse tracking off
    b'\x1b[?1000l'    # X11 mouse tracking off
)
_CURSOR_VISIBLE = b'\x1b[?25h'
_ALT_SCREEN_OFF = b'\x1b[?1049l'


def _build_teardown(alt_screen_active: bool) -> bytes:
    out = _TERMINAL_RESET_SAFE
    if alt_screen_active:
        out += _ALT_SCREEN_OFF
    return out + _CURSOR_VISIBLE


def _autodetect_enabled() -> bool:
    v = os.environ.get('JBO_AUTODETECT', '').lower()
    return v not in ('0', 'false', 'no', 'off')


_DEBUG_LOG = os.environ.get('JBO_DEBUG_LOG')


def _debug_log_chunk(chunk: bytes) -> None:
    if not _DEBUG_LOG:
        return
    try:
        with open(_DEBUG_LOG, 'ab') as f:
            f.write(b'--- chunk (' + str(len(chunk)).encode() + b' bytes) ---\n')
            f.write(repr(chunk).encode() + b'\n')
    except OSError:
        pass


class Linkifier:
    """Stateful linkifier. One instance per wrapped command."""

    def __init__(self, startup_cwd, ide='webstorm', autodetect=True):
        self._cwd = Path(startup_cwd).resolve()
        self._ide_b = ide.encode('utf-8') if isinstance(ide, str) else ide
        self._autodetect = autodetect
        self._cache: dict[str, str | None] = {}
        self._regex = _FULL_RE if autodetect else _LEGACY_RE
        self._buffer = b''
        self.alt_screen_active = False

    def process(self, chunk: bytes) -> bytes:
        """Feed a chunk. Returns linkified bytes for the leading portion that
        is safe to emit. Any trailing run of path-token bytes is held back so a
        path split across two chunks (common when wrapping a streaming command
        like `claude`) still gets matched as a single token."""
        if not chunk:
            return b''
        _debug_log_chunk(chunk)
        # Track alt-screen mode from bytes flowing through. Stand-alone CSI
        # `?1049h/l` is the canonical form (multi-mode like `?1049;25h` is
        # unusual and not handled here).
        if b'\x1b[?1049h' in chunk:
            self.alt_screen_active = True
        if b'\x1b[?1049l' in chunk:
            self.alt_screen_active = False
        data = self._buffer + chunk
        ansi_positions = self._ansi_positions(data)
        soft_wrap_bytes = self._classify_soft_wraps(data)
        cut = self._find_safe_cut(data, ansi_positions, soft_wrap_bytes)
        if cut == 0:
            if len(data) > _MAX_BUFFER:
                self._buffer = b''
                return self._linkify(data, ansi_positions, soft_wrap_bytes)
            self._buffer = data
            return b''
        self._buffer = data[cut:]
        leading_soft = {p for p in soft_wrap_bytes if p < cut}
        leading_ansi = {p for p in ansi_positions if p < cut}
        return self._linkify(data[:cut], leading_ansi, leading_soft)

    def flush(self) -> bytes:
        """Drain any held-back bytes. Call when the wrapped process has exited."""
        if not self._buffer:
            return b''
        data = self._buffer
        self._buffer = b''
        return self._linkify(data,
                             self._ansi_positions(data),
                             self._classify_soft_wraps(data))

    @staticmethod
    def _ansi_positions(data: bytes) -> set:
        """Indices of bytes inside CSI or OSC escape sequences."""
        positions = set()
        for m in _CSI_RE.finditer(data):
            positions.update(range(m.start(), m.end()))
        for m in _OSC_RE.finditer(data):
            positions.update(range(m.start(), m.end()))
        return positions

    @staticmethod
    def _classify_soft_wraps(data: bytes) -> set:
        """Identify wrap sequences in `data` that should be absorbed into the
        surrounding token (terminal width hard-wrap of a long path). A wrap is
        soft when:

          - flanked on both sides by token-class bytes,
          - the trailing token-byte run before it does NOT already end with a
            complete file extension (would mean prefix is itself a finished
            path and the wrap is just separating distinct paths), AND
          - the trailing token-byte run before it contains a slash — the
            prefix must look like it's mid-path. Without this guard, prose
            ending in a token byte followed by a newline-indented path would
            get joined into a non-resolving token, suppressing the link that
            should match the path alone.
        """
        soft = set()
        for m in _WRAP_RE.finditer(data):
            ws, we = m.start(), m.end()
            prev_b = data[ws - 1] if ws > 0 else 0
            next_b = data[we] if we < len(data) else 0
            if prev_b not in _TOKEN_BYTES or next_b not in _TOKEN_BYTES:
                continue
            # Trailing token-byte run before the wrap.
            i = ws
            while i > 0 and data[i - 1] in _TOKEN_BYTES:
                i -= 1
            prefix = data[i:ws]
            if _COMPLETE_EXT.search(prefix):
                continue
            if b'/' not in prefix and b'\\' not in prefix:
                continue
            soft.update(range(ws, we))
        return soft

    @staticmethod
    def _find_safe_cut(data: bytes, ansi_positions: set, soft_wrap_bytes: set) -> int:
        """Index of the first byte of the trailing held-back run. Token-class
        bytes are always held back. ANSI escape bytes are a hard boundary
        UNLESS they are part of a soft-wrap sequence (those get treated like
        token bytes so a path split exactly at the chunk boundary still
        linkifies once the tail arrives in the next chunk).

        A wrap sequence at the *very end* of the data is also held back when
        preceded by a token byte: at this point we don't yet know what comes
        next, but the next chunk might continue a token across the wrap."""
        # Also hold back any trailing wrap that's preceded by a token byte
        # AND whose prefix looks path-like (contains `/` or `\\`) — its other
        # flank is "to be determined" by the next chunk. Without the prefix
        # check, plain prose ending in a letter + wrap + CSI tail (e.g.
        # Claude Code's `Press Ctrl-C again to exit\x1b[39m   \r\x1b[1B...`
        # frame) gets its visible tail held back, leaving the cursor parked
        # mid-frame until the next chunk arrives.
        trailing_wrap = set()
        for m in _WRAP_RE.finditer(data):
            ws, we = m.start(), m.end()
            if we != len(data):
                continue
            prev_b = data[ws - 1] if ws > 0 else 0
            if prev_b not in _TOKEN_BYTES:
                continue
            j = ws
            while j > 0 and data[j - 1] in _TOKEN_BYTES:
                j -= 1
            prefix = data[j:ws]
            if b'/' not in prefix and b'\\' not in prefix:
                continue
            if _COMPLETE_EXT.search(prefix):
                continue
            trailing_wrap.update(range(ws, we))
        hold = soft_wrap_bytes | trailing_wrap

        i = len(data)
        while i > 0:
            c_pos = i - 1
            if c_pos in hold:
                i -= 1
                continue
            if c_pos in ansi_positions:
                return i
            if data[c_pos] in _TOKEN_BYTES:
                i -= 1
                continue
            return i
        return 0

    def _linkify(self, chunk: bytes, ansi_positions: set, soft_wrap_bytes: set) -> bytes:
        """Apply the path regex to a 'logical view' of the chunk where ANSI
        bytes are NUL — this makes the regex treat ANSI sequences as token
        boundaries (so a path preceded by `\\x1b[1C` matches even though the
        literal byte before it is `C`, a word char). Output uses the original
        bytes so ANSI styling around paths is preserved. OSC 8 hyperlink
        wrappers in the input are stripped from the output to avoid nesting
        with the jbo:// links we emit.

        Soft-wrap byte positions are mapped to a token-class sentinel in the
        logical view so a long path the renderer split at terminal-width
        still matches as one token. The original wrap bytes stay in the
        output (visual wrap preserved); the URL strips them out."""
        if not chunk:
            return b''
        # Build the logical view: ANSI bytes → NUL, then soft-wrap → '_'
        # (soft_wrap_bytes overrides ANSI because some wraps are CSI escapes).
        logical = bytearray(chunk)
        for pos in ansi_positions:
            logical[pos] = 0
        for pos in soft_wrap_bytes:
            logical[pos] = ord('_')
        logical_bytes = bytes(logical)

        # Find any existing OSC 8 sequences — we strip them from the output
        # so our jbo:// hyperlinks don't nest with whatever the upstream
        # process emitted.
        osc8_spans = [
            (m.start(), m.end())
            for m in _OSC_RE.finditer(chunk)
            if chunk[m.start():m.start() + 4] == b'\x1b]8;'
        ]

        def slice_skipping_osc8(start: int, end: int) -> bytes:
            if not osc8_spans:
                return chunk[start:end]
            out = bytearray()
            i = start
            for s, e in osc8_spans:
                if e <= start or s >= end:
                    continue
                if i < s:
                    out.extend(chunk[i:max(i, s)])
                i = max(i, e)
            if i < end:
                out.extend(chunk[i:end])
            return bytes(out)

        def clean_token_bytes(s: int, e: int) -> bytes:
            """Original bytes of group(1), minus any soft-wrap bytes we
            sentinelised in the logical view."""
            if not soft_wrap_bytes:
                return chunk[s:e]
            return bytes(chunk[i] for i in range(s, e) if i not in soft_wrap_bytes)

        def emit_segmented_link(m_start: int, m_end: int, url: bytes) -> bytes:
            """Walk chunk[m_start:m_end] position-by-position. Skip existing
            OSC 8 spans entirely (they get stripped from output). Split the
            visible content at soft-wrap byte boundaries and emit ONE OSC 8
            segment per "content run" — wrap bytes pass through raw between
            segments. This makes each visual row of a wrapped path an
            independently-clickable hyperlink pointing at the same URL,
            sidestepping terminals that don't honour multi-row OSC 8 hit
            regions inside TUI renderers."""
            seg_out = bytearray()
            current = bytearray()
            in_content = True  # True while collecting content bytes
            i = m_start
            while i < m_end:
                # Skip any OSC 8 span entirely.
                osc_skip = False
                for s, e in osc8_spans:
                    if s <= i < e:
                        i = e
                        osc_skip = True
                        break
                if osc_skip:
                    continue
                is_wrap = i in soft_wrap_bytes
                if (not is_wrap) != in_content:
                    # Switching modes — flush current buffer.
                    if current:
                        if in_content:
                            seg_out.extend(b'\x1b]8;;' + url + b'\x1b\\')
                            seg_out.extend(bytes(current))
                            seg_out.extend(b'\x1b]8;;\x1b\\')
                        else:
                            seg_out.extend(bytes(current))
                        current = bytearray()
                    in_content = not is_wrap
                current.append(chunk[i])
                i += 1
            if current:
                if in_content:
                    seg_out.extend(b'\x1b]8;;' + url + b'\x1b\\')
                    seg_out.extend(bytes(current))
                    seg_out.extend(b'\x1b]8;;\x1b\\')
                else:
                    seg_out.extend(bytes(current))
            return bytes(seg_out)

        out = bytearray()
        last = 0
        for m in self._regex.finditer(logical_bytes):
            out.extend(slice_skipping_osc8(last, m.start()))
            token_b = m.group(1)
            line_b = m.group(2)
            match_orig = slice_skipping_osc8(m.start(), m.end())
            if 0 in token_b:
                # ANSI inside the token — don't try to linkify.
                out.extend(match_orig)
            elif self._autodetect:
                clean_b = clean_token_bytes(m.start(1), m.end(1))
                token = clean_b.decode('utf-8', errors='replace')
                line_str = line_b.decode('ascii') if line_b else '1'
                resolved = self._resolve_if_linkable(token)
                if resolved is None:
                    out.extend(match_orig)
                else:
                    out.extend(emit_segmented_link(m.start(), m.end(),
                                                   self._build_url(resolved, line_str)))
            else:
                # legacy mode: always emit, never stat, group(2) is mandatory.
                clean_b = clean_token_bytes(m.start(1), m.end(1))
                resolved = clean_b.replace(b'\\', b'/').decode('utf-8', errors='replace')
                line_str = line_b.decode('ascii')
                out.extend(emit_segmented_link(m.start(), m.end(),
                                               self._build_url(resolved, line_str)))
            last = m.end()
        out.extend(slice_skipping_osc8(last, len(chunk)))
        return bytes(out)

    def _resolve_if_linkable(self, token: str):
        if token in self._cache:
            return self._cache[token]

        # Windows-style absolute paths: trust without stat (handler resolves on Win side).
        if _WIN_ABS.match(token):
            result = token.replace('\\', '/')
            self._cache[token] = result
            return result

        p = Path(token)
        resolved = p if p.is_absolute() else (self._cwd / token)
        try:
            exists = resolved.exists()
        except OSError:
            exists = False
        result = str(resolved).replace('\\', '/') if exists else None
        self._cache[token] = result
        return result

    def _build_url(self, resolved_path: str, line: str) -> bytes:
        return (
            b'jbo://open?ide=' + self._ide_b
            + b'&file=' + quote(resolved_path, safe='/:').encode('utf-8')
            + b'&line=' + line.encode('ascii')
        )


# ── PTY runners ──────────────────────────────────────────────────────────────

def _run_pty(args, linkifier: Linkifier):
    import fcntl
    import pty
    import select
    import signal
    import termios
    import tty

    def _sync_winsize(src, dst):
        try:
            fcntl.ioctl(dst, termios.TIOCSWINSZ,
                        fcntl.ioctl(src, termios.TIOCGWINSZ, b'\x00' * 8))
        except OSError:
            pass

    pid, master = pty.fork()
    if pid == 0:
        os.execvp(args[0], args)
        sys.exit(1)

    stdout_fd, stdin_fd = sys.stdout.fileno(), sys.stdin.fileno()
    _sync_winsize(stdout_fd, master)
    signal.signal(signal.SIGWINCH, lambda *_: _sync_winsize(stdout_fd, master))

    if not os.isatty(stdin_fd):
        sys.exit('jbo-wrap: stdin must be a terminal')
    old_attr = termios.tcgetattr(stdin_fd)
    tty.setraw(stdin_fd)

    try:
        while True:
            try:
                rlist, _, _ = select.select([master, stdin_fd], [], [])
            except (InterruptedError, ValueError):
                break
            if master in rlist:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                sys.stdout.buffer.write(linkifier.process(chunk))
                sys.stdout.buffer.flush()
            if stdin_fd in rlist:
                try:
                    chunk = os.read(stdin_fd, 1024)
                except OSError:
                    break
                if not chunk:
                    break
                os.write(master, chunk)
    finally:
        tail = linkifier.flush()
        if tail:
            sys.stdout.buffer.write(tail)
        sys.stdout.buffer.write(_build_teardown(linkifier.alt_screen_active))
        sys.stdout.buffer.flush()
        termios.tcsetattr(stdin_fd, termios.TCSANOW, old_attr)
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass


def _run_winpty(args, linkifier: Linkifier):
    import shutil
    from winpty import PtyProcess

    cols, rows = shutil.get_terminal_size((80, 24))
    proc = PtyProcess.spawn(args, dimensions=(rows, cols))
    done = threading.Event()

    def _poll_resize():
        nonlocal cols, rows
        while not done.wait(0.5):
            c, r = shutil.get_terminal_size((80, 24))
            if (c, r) != (cols, rows):
                cols, rows = c, r
                try:
                    proc.setwinsize(r, c)
                except Exception:
                    pass

    def _reader():
        while not done.is_set():
            try:
                chunk = proc.read(65536)
                if chunk:
                    if isinstance(chunk, str):
                        chunk = chunk.encode('utf-8', errors='replace')
                    sys.stdout.buffer.write(linkifier.process(chunk))
                    sys.stdout.buffer.flush()
            except EOFError:
                break
            except Exception:
                break
        done.set()

    def _writer():
        # Map Windows extended key sequences (\x00/\xe0 + scan) to ANSI escapes
        _ext = {
            'H': '\x1b[A', 'P': '\x1b[B', 'M': '\x1b[C', 'K': '\x1b[D',
            'G': '\x1b[H', 'O': '\x1b[F', 'I': '\x1b[5~', 'Q': '\x1b[6~',
            'S': '\x1b[3~', 'R': '\x1b[2~',
        }
        try:
            import msvcrt
            while not done.is_set():
                try:
                    ch = msvcrt.getwch()
                    if ch in ('\x00', '\xe0'):
                        ch2 = msvcrt.getwch()
                        seq = _ext.get(ch2, '')
                        if seq:
                            proc.write(seq)
                    else:
                        proc.write(ch)
                except Exception:
                    break
        except ImportError:
            fd = sys.stdin.fileno()
            while not done.is_set():
                try:
                    data = os.read(fd, 1024)
                    if data:
                        proc.write(data.decode('utf-8', errors='replace'))
                except OSError:
                    break

    for t in [threading.Thread(target=f, daemon=True)
              for f in (_reader, _poll_resize, _writer)]:
        t.start()

    done.wait()
    tail = linkifier.flush()
    if tail:
        sys.stdout.buffer.write(tail)
    sys.stdout.buffer.write(_build_teardown(linkifier.alt_screen_active))
    sys.stdout.buffer.flush()
    try:
        proc.wait()
    except Exception:
        pass


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit('Usage: jbo-wrap <command> [args...]')

    ide = os.environ.get('JBO_IDE', 'webstorm')
    linkifier = Linkifier(Path.cwd(), ide=ide, autodetect=_autodetect_enabled())

    try:
        import pty as _p, fcntl as _f, termios as _t  # noqa: F401
        _run_pty(args, linkifier)
        return
    except ImportError:
        pass

    try:
        from winpty import PtyProcess  # noqa: F401
        _run_winpty(args, linkifier)
        return
    except ImportError:
        pass

    sys.exit('jbo-wrap: no PTY support — run: pip install pywinpty')


if __name__ == '__main__':
    main()

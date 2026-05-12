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

    def process(self, chunk: bytes) -> bytes:
        """Feed a chunk. Returns linkified bytes for the leading portion that
        is safe to emit. Any trailing run of path-token bytes is held back so a
        path split across two chunks (common when wrapping a streaming command
        like `claude`) still gets matched as a single token."""
        if not chunk:
            return b''
        _debug_log_chunk(chunk)
        data = self._buffer + chunk
        ansi_positions = self._ansi_positions(data)
        cut = self._find_safe_cut(data, ansi_positions)
        if cut == 0:
            if len(data) > _MAX_BUFFER:
                self._buffer = b''
                return self._linkify(data, ansi_positions)
            self._buffer = data
            return b''
        self._buffer = data[cut:]
        return self._linkify(data[:cut], ansi_positions)

    def flush(self) -> bytes:
        """Drain any held-back bytes. Call when the wrapped process has exited."""
        if not self._buffer:
            return b''
        data = self._buffer
        self._buffer = b''
        return self._linkify(data, self._ansi_positions(data))

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
    def _find_safe_cut(data: bytes, ansi_positions: set) -> int:
        """Index of the first byte of the trailing token-bytes run, treating
        any byte inside an ANSI escape sequence as a hard boundary so that a
        CSI's final letter (e.g. the `C` of `\\x1b[1C`) isn't mistaken for a
        path-token character to hold back."""
        i = len(data)
        while i > 0:
            c_pos = i - 1
            if c_pos in ansi_positions:
                return i
            if data[c_pos] not in _TOKEN_BYTES:
                return i
            i -= 1
        return 0

    def _linkify(self, chunk: bytes, ansi_positions: set) -> bytes:
        """Apply the path regex to a 'logical view' of the chunk where ANSI
        bytes are NUL — this makes the regex treat ANSI sequences as token
        boundaries (so a path preceded by `\\x1b[1C` matches even though the
        literal byte before it is `C`, a word char). Output uses the original
        bytes so ANSI styling around paths is preserved. OSC 8 hyperlink
        wrappers in the input are stripped from the output to avoid nesting
        with the jbo:// links we emit."""
        if not chunk:
            return b''
        # Build the logical view: ANSI bytes → NUL.
        logical = bytearray(chunk)
        for pos in ansi_positions:
            logical[pos] = 0
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
                token = token_b.decode('utf-8', errors='replace')
                line_str = line_b.decode('ascii') if line_b else '1'
                resolved = self._resolve_if_linkable(token)
                if resolved is None:
                    out.extend(match_orig)
                else:
                    out.extend(self._build_osc8(match_orig, resolved, line_str))
            else:
                # legacy mode: always emit, never stat, group(2) is mandatory.
                resolved = token_b.replace(b'\\', b'/').decode('utf-8', errors='replace')
                line_str = line_b.decode('ascii')
                out.extend(self._build_osc8(match_orig, resolved, line_str))
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

    def _build_osc8(self, display: bytes, resolved_path: str, line: str) -> bytes:
        url = (
            b'jbo://open?ide=' + self._ide_b
            + b'&file=' + quote(resolved_path, safe='/:').encode('utf-8')
            + b'&line=' + line.encode('ascii')
        )
        return b'\x1b]8;;' + url + b'\x1b\\' + display + b'\x1b]8;;\x1b\\'


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

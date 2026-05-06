#!/usr/bin/env python3
"""PTY wrapper for jbo-wrap — Unix pty (WSL) or Windows ConPTY (pywinpty)."""
import os
import re
import sys
import threading

IDE = os.environ.get('JBO_IDE', 'webstorm')
_RE = re.compile(rb'((?:[A-Za-z]:[/\\]|/)[ -~]*\.[a-zA-Z0-9]{1,10}):(\d+)')

def _linkify(m):
    path, line = m.group(1), m.group(2)
    url = b'jbo://open?ide=' + IDE.encode() + b'&file=' + path.replace(b'\\', b'/') + b'&line=' + line
    return b'\x1b]8;;' + url + b'\x1b\\' + path + b':' + line + b'\x1b]8;;\x1b\\'


def _run_pty(args):
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
                sys.stdout.buffer.write(_RE.sub(_linkify, chunk))
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
        termios.tcsetattr(stdin_fd, termios.TCSANOW, old_attr)
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass


def _run_winpty(args):
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
                    sys.stdout.buffer.write(_RE.sub(_linkify, chunk))
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
    try:
        proc.wait()
    except Exception:
        pass


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit('Usage: jbo-wrap <command> [args...]')

    try:
        import pty as _p, fcntl as _f, termios as _t  # noqa: F401
        _run_pty(args)
        return
    except ImportError:
        pass

    try:
        from winpty import PtyProcess  # noqa: F401
        _run_winpty(args)
        return
    except ImportError:
        pass

    sys.exit('jbo-wrap: no PTY support — run: pip install pywinpty')


if __name__ == '__main__':
    main()
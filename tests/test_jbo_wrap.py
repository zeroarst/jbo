"""Tests for jbo-wrap Linkifier.

The source file uses a hyphen (src/jbo-wrap.py), so we load it via importlib.
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / 'src' / 'jbo-wrap.py'


def _load():
    spec = importlib.util.spec_from_file_location('jbo_wrap', _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


jbo_wrap = _load()
Linkifier = jbo_wrap.Linkifier


class _Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self._tmp.name).resolve()
        (self.cwd / 'src').mkdir()
        (self.cwd / 'src' / 'foo.js').write_text('')
        (self.cwd / 'src' / 'bar.py').write_text('')
        (self.cwd / 'package.json').write_text('{}')

    def tearDown(self):
        self._tmp.cleanup()

    def link(self, autodetect=True, ide='webstorm'):
        return Linkifier(self.cwd, ide=ide, autodetect=autodetect)

    def run_through(self, link, chunk):
        """Single-chunk convenience: process + flush — equivalent to one-shot
        command output that ends without trailing whitespace."""
        return link.process(chunk) + link.flush()


class TestAbsolutePaths(_Fixture):
    def test_absolute_unix_with_line(self):
        absp = (self.cwd / 'src' / 'foo.js')
        inp = str(absp).encode() + b':42'
        out = self.run_through(self.link(),inp)
        self.assertIn(b'\x1b]8;;jbo://open?ide=webstorm', out)
        self.assertIn(b'&line=42', out)
        self.assertIn(str(absp).encode().replace(b'\\', b'/'), out)
        # display text preserved at the visible end of the sequence
        self.assertTrue(out.endswith(b'\x1b]8;;\x1b\\'))

    def test_absolute_windows_path_legacy_mode(self):
        out = self.run_through(self.link(autodetect=False),rb'C:\foo\bar.go:7')
        self.assertIn(b'\x1b]8;;', out)
        self.assertIn(b'C:/foo/bar.go', out)
        self.assertIn(b'&line=7', out)

    def test_absolute_windows_path_autodetect_mode(self):
        # Windows paths bypass stat() (they're inherently trusted as absolute).
        out = self.run_through(self.link(),rb'C:\foo\bar.go:7')
        self.assertIn(b'\x1b]8;;', out)
        self.assertIn(b'C:/foo/bar.go', out)


class TestRelativePaths(_Fixture):
    def test_relative_exists_with_line(self):
        out = self.run_through(self.link(),b'see src/foo.js:42 now')
        self.assertIn(b'\x1b]8;;jbo://open', out)
        expected_file = str(self.cwd / 'src' / 'foo.js').replace('\\', '/').encode()
        self.assertIn(b'&file=' + expected_file, out)
        self.assertIn(b'&line=42', out)
        # display preserves what user wrote, including the colon-line
        self.assertIn(b'src/foo.js:42', out)

    def test_relative_does_not_exist(self):
        inp = b'see src/missing.js:42 now'
        out = self.run_through(self.link(),inp)
        self.assertEqual(out, inp)
        self.assertNotIn(b'\x1b]8;;', out)

    def test_bare_filename_exists_defaults_to_line_1(self):
        out = self.run_through(self.link(),b'check package.json please')
        self.assertIn(b'\x1b]8;;jbo://open', out)
        self.assertIn(b'&line=1', out)
        self.assertIn(b'package.json', out)

    def test_bare_filename_without_extension_not_matched(self):
        (self.cwd / 'Dockerfile').write_text('')
        out = self.run_through(self.link(),b'see Dockerfile here')
        self.assertNotIn(b'\x1b]8;;', out)

    def test_dot_slash_relative_path(self):
        out = self.run_through(self.link(),b'see ./src/foo.js:5 now')
        self.assertIn(b'\x1b]8;;', out)
        self.assertIn(b'&line=5', out)

    def test_multiple_paths_in_one_chunk(self):
        out = self.run_through(self.link(),b'src/foo.js:1 and src/bar.py:2 here')
        # Two OSC 8 starts
        self.assertEqual(out.count(b'\x1b]8;;jbo://open'), 2)


class TestUrlExclusion(_Fixture):
    def test_https_url_not_linkified(self):
        inp = b'visit https://example.com/foo.js for info'
        out = self.run_through(self.link(),inp)
        self.assertNotIn(b'\x1b]8;;', out)

    def test_http_url_with_line_not_linkified(self):
        inp = b'see http://test.com/bar.js:42 maybe'
        out = self.run_through(self.link(),inp)
        self.assertNotIn(b'\x1b]8;;', out)

    def test_real_path_alongside_url_still_linkified(self):
        inp = b'docs at https://example.com/x.js, source at src/foo.js:1'
        out = self.run_through(self.link(),inp)
        # exactly one OSC 8 wrapping the real path
        self.assertEqual(out.count(b'\x1b]8;;jbo://open'), 1)
        self.assertIn(b'src/foo.js:1', out)


class TestAutodetectToggle(_Fixture):
    def test_autodetect_off_skips_relative(self):
        out = self.run_through(self.link(autodetect=False),b'src/foo.js:42')
        self.assertNotIn(b'\x1b]8;;', out)

    def test_autodetect_off_still_handles_absolute(self):
        absp = self.cwd / 'src' / 'foo.js'
        out = self.run_through(self.link(autodetect=False),str(absp).encode() + b':42')
        self.assertIn(b'\x1b]8;;', out)

    def test_autodetect_on_handles_both(self):
        absp = self.cwd / 'src' / 'foo.js'
        inp = b'absolute=' + str(absp).encode() + b':1 and relative=src/bar.py:2'
        out = self.run_through(self.link(),inp)
        self.assertEqual(out.count(b'\x1b]8;;jbo://open'), 2)


class TestCache(_Fixture):
    def test_repeated_token_uses_cache(self):
        link = self.link()
        # trailing space ensures the path token is flushed past the buffer
        link.process(b'src/foo.js:1 ')
        self.assertIn('src/foo.js', link._cache)
        link.process(b'src/foo.js:1 again ')
        self.assertEqual(
            [k for k in link._cache if k == 'src/foo.js'],
            ['src/foo.js']
        )

    def test_non_existent_path_cached_as_none(self):
        link = self.link()
        link.process(b'src/nope.js:1 ')
        self.assertIn('src/nope.js', link._cache)
        self.assertIsNone(link._cache['src/nope.js'])


class TestIdeParameter(_Fixture):
    def test_ide_androidstudio(self):
        out = self.run_through(self.link(ide='androidstudio'),b'src/foo.js:1')
        self.assertIn(b'ide=androidstudio', out)

    def test_ide_intellij(self):
        out = self.run_through(self.link(ide='intellij'),b'src/foo.js:1')
        self.assertIn(b'ide=intellij', out)


class TestChunkBoundary(_Fixture):
    def test_path_split_across_two_chunks(self):
        link = self.link()
        # First chunk ends mid-path
        out1 = link.process(b'see src/foo.js')
        # The path token is held back; only "see " is emitted
        self.assertEqual(out1, b'see ')
        # Next chunk completes the path and adds line + trailing whitespace
        out2 = link.process(b':42 now\n')
        self.assertIn(b'\x1b]8;;jbo://open', out2)
        self.assertIn(b'&line=42', out2)
        self.assertIn(b'src/foo.js:42', out2)

    def test_path_split_at_colon(self):
        link = self.link()
        # Trailing colon held with path
        out1 = link.process(b'edit src/foo.js:')
        self.assertEqual(out1, b'edit ')
        out2 = link.process(b'7 ok\n')
        self.assertIn(b'&line=7', out2)

    def test_flush_emits_remaining_buffer(self):
        link = self.link()
        # Last chunk has no trailing whitespace — path stays in buffer
        out1 = link.process(b'final src/foo.js:5')
        # The path is buffered; only the leading text is emitted
        self.assertNotIn(b'\x1b]8;;', out1)
        # flush() linkifies and drains
        out2 = link.flush()
        self.assertIn(b'\x1b]8;;', out2)
        self.assertIn(b'src/foo.js:5', out2)
        # Subsequent flush is a no-op
        self.assertEqual(link.flush(), b'')

    def test_buffer_cap_releases_on_overflow(self):
        link = self.link()
        # Feed a chunk longer than the cap with no whitespace at all.
        # The Linkifier should give up buffering and emit it.
        big = b'x' * (jbo_wrap._MAX_BUFFER + 100)
        out = link.process(big)
        # Everything was emitted (regex finds nothing to linkify, that's fine)
        self.assertEqual(len(out), len(big))

    def test_empty_chunk_with_buffered_state(self):
        link = self.link()
        link.process(b'see src/foo.js')
        # Empty chunk should not change buffer state or emit anything
        self.assertEqual(link.process(b''), b'')
        # Buffer is intact; flush still emits the buffered token
        self.assertIn(b'\x1b]8;;', link.flush())


class TestAnsiAware(_Fixture):
    """Claude Code's terminal renderer emits \\x1b[1C (cursor-forward CSI) in
    place of literal spaces between tokens. The byte right before each path is
    then `C` (the CSI final byte), which is a word char and would otherwise
    cause the regex lookbehind to refuse the match. These tests pin the
    ANSI-aware logic in place."""

    def test_csi_cursor_forward_before_path(self):
        # Mirrors the exact pattern observed in JBO_DEBUG_LOG.
        chunk = b'text:\x1b[1Csrc/foo.js:9'
        out = self.run_through(self.link(), chunk)
        self.assertIn(b'\x1b]8;;jbo://open', out)
        self.assertIn(b'src/foo.js:9', out)

    def test_multiple_paths_separated_by_csi(self):
        chunk = (
            b'src/foo.js:9\x1b[1Csrc/bar.py:10\x1b[1Cpackage.json'
        )
        (self.cwd / 'package.json').write_text('{}')
        out = self.run_through(self.link(), chunk)
        self.assertEqual(out.count(b'\x1b]8;;jbo://open'), 3)

    def test_csi_color_code_before_path(self):
        chunk = b'\x1b[36msrc/foo.js:9\x1b[0m'
        out = self.run_through(self.link(), chunk)
        self.assertIn(b'\x1b]8;;jbo://open', out)
        # original ANSI styling is preserved around the OSC 8
        self.assertIn(b'\x1b[36m', out)
        self.assertIn(b'\x1b[0m', out)

    def test_csi_at_chunk_boundary_not_buffered_as_path(self):
        # The CSI's final letter (`C`) used to be mistaken for a path-token
        # byte and held back in the buffer, breaking subsequent matches.
        link = self.link()
        # First chunk ends right after a CSI — buffer should be empty.
        out1 = link.process(b'foo\x1b[1C')
        # Second chunk supplies a path; should linkify on its own.
        out2 = link.process(b'src/foo.js:9 done\n')
        combined = out1 + out2 + link.flush()
        self.assertIn(b'\x1b]8;;jbo://open', combined)
        self.assertIn(b'src/foo.js:9', combined)

    def test_existing_osc8_wrapper_stripped(self):
        # If the upstream renderer already wrapped a path in OSC 8 (e.g.
        # pointing at file://), we strip its wrapper from the output before
        # adding our own jbo:// link — otherwise terminals see nested links.
        chunk = (
            b'\x1b]8;;file:///some/other/url\x1b\\'
            b'src/foo.js:9'
            b'\x1b]8;;\x1b\\'
        )
        out = self.run_through(self.link(), chunk)
        # exactly one OSC 8 hyperlink wrapping the path
        self.assertEqual(out.count(b'\x1b]8;;jbo://open'), 1)
        # no leftover file:// wrapper
        self.assertNotIn(b'file:///some/other/url', out)


class TestSoftWrap(_Fixture):
    """Long paths that exceed terminal width get split mid-token by the
    renderer (Claude Code, etc.). The split typically uses CRLF or a CSI
    Next-Line escape between two token-byte runs. The linkifier must absorb
    these soft-wrap sequences so the path matches as one token; output
    preserves the wrap bytes inside the OSC 8 hyperlink (terminals render
    multi-line hyperlinks fine) while the URL strips them."""

    def _split_path(self):
        # Build a real long path that exists on disk so autodetect resolves.
        long_dir = self.cwd / 'a-really-long-subdirectory-name-that-forces-wrap'
        long_dir.mkdir()
        target = long_dir / 'extra-long-file-name-with-detail.js'
        target.write_text('')
        absp = str(target).encode()
        # Split roughly in the middle of the path.
        cut = len(absp) // 2
        return absp, absp[:cut], absp[cut:]

    def _assert_link_for_path(self, out, full_path: bytes, segments: int = 1):
        """When a match contains soft-wrap bytes the emit logic splits the
        OSC 8 into one segment per visual row (all sharing the same URL).
        Default `segments=1` for unwrapped matches; pass `segments=2` for
        path-split-across-one-wrap, `segments=3` for two wraps, etc."""
        self.assertEqual(out.count(b'\x1b]8;;jbo://open'), segments,
                         f'expected {segments} jbo:// segment(s); got {out!r}')
        clean = full_path.replace(b'\\', b'/')
        # Every segment carries the same &file= URL.
        self.assertEqual(out.count(b'&file=' + clean), segments)

    def test_crlf_inside_path(self):
        absp, head, tail = self._split_path()
        chunk = head + b'\r\n' + tail + b':42'
        out = self.run_through(self.link(), chunk)
        self._assert_link_for_path(out, absp, segments=2)
        # wrap bytes preserved in the visible display so terminal wraps correctly
        self.assertIn(b'\r\n', out)

    def test_csi_next_line_inside_path(self):
        absp, head, tail = self._split_path()
        chunk = head + b'\x1b[E' + tail + b':42'
        out = self.run_through(self.link(), chunk)
        self._assert_link_for_path(out, absp, segments=2)
        self.assertIn(b'\x1b[E', out)

    def test_csi_down_cr_inside_path(self):
        absp, head, tail = self._split_path()
        chunk = head + b'\x1b[1B\r' + tail + b':42'
        out = self.run_through(self.link(), chunk)
        self._assert_link_for_path(out, absp, segments=2)

    def test_two_paths_on_separate_lines_remain_separate(self):
        """Plain LF between two distinct paths must NOT be absorbed —
        otherwise newline-separated path lists get joined into one bad path."""
        out = self.run_through(self.link(),
                               b'src/foo.js\nsrc/bar.py')
        self.assertEqual(out.count(b'\x1b]8;;jbo://open'), 2)

    def test_two_paths_on_separate_lines_with_crlf_remain_separate(self):
        """Same as above but with CRLF — both ends are file names whose token
        bytes flank the CRLF, but the join would form a non-existent path so
        autodetect rejects it; each line must still linkify on its own."""
        out = self.run_through(self.link(),
                               b'src/foo.js\r\nsrc/bar.py')
        self.assertEqual(out.count(b'\x1b]8;;jbo://open'), 2)

    def test_claude_code_wrap_user_bubble(self):
        """Pattern #1 from Claude Code's user-message bubble:
        \\r\\x1b[1B + 4 spaces + ▎ + 1 space.

        Asserts per-row OSC 8 segmentation: TWO hyperlinks (same URL), one
        per visual row, with the wrap bytes between them as raw output."""
        absp, head, tail = self._split_path()
        chunk = head + b'\r\x1b[1B    \xe2\x96\x8e ' + tail + b':42'
        out = self.run_through(self.link(), chunk)
        # Two separate OSC 8 hyperlinks (one per row), pointing at the same URL.
        self.assertEqual(out.count(b'\x1b]8;;jbo://open'), 2)
        # Both OSC 8 segments contain the same &file= URL.
        self.assertEqual(out.count(b'&file=' + absp.replace(b'\\', b'/')), 2)
        # The wrap bytes survive between the two OSC 8 segments.
        self.assertIn(b'\xe2\x96\x8e', out)
        # The first OSC 8 close (`\x1b]8;;\x1b\\`) must come BEFORE the wrap so
        # the wrap itself is outside any hyperlink (terminal handles each row's
        # OSC 8 as a standalone single-row link).
        first_close = out.index(b'\x1b]8;;\x1b\\')
        wrap_pos = out.index(b'\r\x1b[1B')
        self.assertLess(first_close, wrap_pos)

    def test_claude_code_wrap_with_color_and_long_padding(self):
        """Pattern #2: trailing color reset + ~120 spaces (right-pad of source
        row to terminal width) + \\r\\x1b[1B + leading spaces + color +
        leading spaces + ▎ + space (next row's left margin)."""
        absp, head, tail = self._split_path()
        # Right-pad with ANSI + spaces, wrap, left-margin with ANSI + spaces + border.
        wrap = (b'\x1b[39m' + b' ' * 120 +
                b'\r\x1b[1B  \x1b[38;5;231m  \xe2\x96\x8e ')
        chunk = head + wrap + tail + b':42'
        out = self.run_through(self.link(), chunk)
        self._assert_link_for_path(out, absp, segments=2)

    def test_claude_code_wrap_with_cursor_right_moves(self):
        """Pattern #3: \\r\\x1b[4C\\x1b[1B (CR + cursor-right + cursor-down)
        + ▎ + \\x1b[1C (cursor-right). No literal spaces; padding is purely
        cursor positioning."""
        absp, head, tail = self._split_path()
        chunk = head + b'\r\x1b[4C\x1b[1B\xe2\x96\x8e\x1b[1C' + tail + b':42'
        out = self.run_through(self.link(), chunk)
        self._assert_link_for_path(out, absp, segments=2)

    def test_prose_then_indent_then_path_does_not_join(self):
        """A prose word ending before \\r\\n + spaces + a path on the next
        line must NOT be absorbed into one token — otherwise the path on the
        second line would fail to linkify even though it's a complete file."""
        chunk = b'Stack trace:\r\n    src/foo.js:9'
        out = self.run_through(self.link(), chunk)
        # The standalone src/foo.js:9 must still linkify.
        self.assertEqual(out.count(b'\x1b]8;;jbo://open'), 1)
        self.assertIn(b'&file=' + str(self.cwd / 'src' / 'foo.js').encode(), out)

    def test_wrap_split_across_chunk_boundary(self):
        """Renderer emits the wrap at exactly a PTY chunk boundary —
        held-back buffer must include the wrap bytes too, not flush them."""
        absp, head, tail = self._split_path()
        link = self.link()
        # First chunk ends with head + CRLF (no path-extension yet).
        out1 = link.process(head + b'\r\n')
        # Second chunk supplies the tail + :line.
        out2 = link.process(tail + b':42')
        combined = out1 + out2 + link.flush()
        self._assert_link_for_path(combined, absp, segments=2)


class TestTeardownPassthrough(_Fixture):
    """Regression: DEC-private-mode disables emitted by the wrapped child must
    reach the outer terminal verbatim, even when buffered across chunk
    boundaries or interleaved with token-shaped bytes. If these fail, an
    abrupt child exit leaves the outer terminal in kitty-kbd / focus-tracking /
    alt-screen mode."""

    TEARDOWN = (
        b'\x1b[<u'
        b'\x1b[=0;1u'
        b'\x1b[?1004l'
        b'\x1b[?2004l'
        b'\x1b[?1049l'
        b'\x1b[?25h'
    )

    def test_pure_teardown_single_chunk(self):
        out = self.run_through(self.link(), self.TEARDOWN)
        self.assertEqual(out, self.TEARDOWN)

    def test_token_prefix_then_teardown(self):
        inp = b'user_text' + self.TEARDOWN
        self.assertEqual(self.run_through(self.link(), inp), inp)

    def test_token_cursor_down_teardown_one_chunk(self):
        # `\x1b[1B` (Cursor Down) is the _WRAP_RE anchor — the most likely
        # culprit for swallowing trailing CSI as soft-wrap absorption.
        inp = b'src/foo.py' + b'\x1b[1B' + self.TEARDOWN
        self.assertEqual(self.run_through(self.link(), inp), inp)

    def test_token_then_teardown_split_at_cursor_down(self):
        # Path-shaped tail in one chunk, cursor-down + teardown in the next.
        link = self.link()
        first = link.process(b'src/foo.py')
        rest  = link.process(b'\x1b[1B' + self.TEARDOWN) + link.flush()
        self.assertEqual(first + rest, b'src/foo.py\x1b[1B' + self.TEARDOWN)

    def test_teardown_with_trailing_crlf(self):
        inp = b'token' + self.TEARDOWN + b'\r\n'
        self.assertEqual(self.run_through(self.link(), inp), inp)


class TestCrLfPlusCsiMidPath(_Fixture):
    """Regression: a mid-path token followed by `\\r\\r\\n\\x1b[2C` (Claude
    Code's word-wrap pattern when streaming a path list in its bubble) must
    be classified as a soft wrap. The CR(s) + LF is the wrap, the trailing
    `\\x1b[2C` is the left-margin indent of the next visual row — both belong
    to the wrap zone, otherwise `next_b` lands on `\\x1b` (not a token byte)
    and the classifier bails."""

    def _make_long_real_path(self):
        long_dir = self.cwd / 'a-really-long-subdirectory-name-that-forces-wrap'
        long_dir.mkdir()
        target = long_dir / 'extra-long-file-name-with-detail.js'
        target.write_text('')
        return target

    def test_cr_cr_lf_plus_csi_right_inside_path(self):
        target = self._make_long_real_path()
        absp = str(target).encode()
        cut = len(absp) // 2
        chunk = absp[:cut] + b'\r\r\n\x1b[2C' + absp[cut:] + b':42'
        out = self.run_through(self.link(), chunk)
        self.assertEqual(out.count(b'\x1b]8;;jbo://open'), 2)
        clean = absp.replace(b'\\', b'/')
        self.assertEqual(out.count(b'&file=' + clean), 2)
        self.assertEqual(out.count(b'&line=42'), 2)


class TestCrCrLfMidPath(_Fixture):
    """Regression: a mid-path token followed by `\\r\\r\\n` (CR+CRLF — what
    Claude Code emits when word-wrapping a long path inside its bubble) must
    be classified as a soft wrap so the two halves of the path join into one
    OSC 8 link. With `_WRAP_RE = \\r\\n`, the regex only matched the second
    `\\r` + `\\n`, leaving the leading `\\r` as `prev_b` — which fails the
    `prev_b in _TOKEN_BYTES` gate, so the wrap was never absorbed."""

    def _make_long_real_path(self):
        long_dir = self.cwd / 'a-really-long-subdirectory-name-that-forces-wrap'
        long_dir.mkdir()
        target = long_dir / 'extra-long-file-name-with-detail.js'
        target.write_text('')
        return target

    def test_cr_cr_lf_inside_path_links_as_one(self):
        # The wrap Claude emits for soft-wrapped paths is `\r\r\n`, not `\r\n`.
        target = self._make_long_real_path()
        absp = str(target).encode()
        cut = len(absp) // 2
        chunk = absp[:cut] + b'\r\r\n' + absp[cut:] + b':42'
        out = self.run_through(self.link(), chunk)
        # OSC 8 segmentation produces one link per visual row, both with the
        # same &file=…&line=42 URL.
        self.assertEqual(out.count(b'\x1b]8;;jbo://open'), 2)
        clean = absp.replace(b'\\', b'/')
        self.assertEqual(out.count(b'&file=' + clean), 2)
        self.assertEqual(out.count(b'&line=42'), 2)
        # The wrap bytes survive in the visible output between segments.
        self.assertIn(b'\r\r\n', out)


class TestCsiBetweenCompletePaths(_Fixture):
    """Regression: when Claude Code streams a path list with CSI cursor-down
    (`\\x1b[1B`) between distinct paths (each path ending in `.ext:line`), the
    soft-wrap classifier was absorbing the inter-path CSI as a wrap because
    `_COMPLETE_EXT` checked for `.ext$` but not `.ext:line$`. The absorbed
    wrap bytes get sentinelised to `_` in the logical view, which sits inside
    `\\w` and so fails the trailing `(?![\\w/\\\\])` lookahead — the regex
    backtracks, drops the `:line`, and links only the path part to line 1.

    Captured pattern from JBO_DEBUG_LOG of a streaming Claude render."""

    def test_csi_cursor_down_between_complete_paths(self):
        chunk = (
            b'src/jbo-wrap.py:170\x1b[K\r\x1b[1B  '
            b'src/jbo-wrap.py:241\x1b[K\r\x1b[2C\x1b[1B'
            b'src/jbo-wrap.py:282\r\x1b[1B  '
            b'tests/test_jbo_wrap.py:466'
        )
        # Need the test files to exist on disk for autodetect to resolve.
        (self.cwd / 'tests').mkdir()
        (self.cwd / 'tests' / 'test_jbo_wrap.py').write_text('')
        (self.cwd / 'src' / 'jbo-wrap.py').write_text('')
        out = self.run_through(self.link(), chunk)
        # Every path:line must produce an OSC 8 with the right &line=
        self.assertIn(b'&line=170', out)
        self.assertIn(b'&line=241', out)
        self.assertIn(b'&line=282', out)
        self.assertIn(b'&line=466', out)
        # And there must be exactly 4 jbo:// hyperlinks (one per path) — not
        # 4 mis-linked path-only hyperlinks pointing at line 1.
        self.assertEqual(out.count(b'\x1b]8;;jbo://open'), 4)
        self.assertEqual(out.count(b'&line=1\x1b\\'), 0)


class TestAltScreenTeardown(_Fixture):
    """Regression: `\\x1b[?1049l` (leave alternate screen) must NOT be emitted
    in the safety teardown when the child never entered alt-screen. Some
    terminals interpret it as "switch back to main and clear current view,"
    which wipes the child's final output — e.g. Claude's `Resume this session
    with: claude --resume <uuid>` summary briefly appears and vanishes."""

    def test_initial_state_is_inactive(self):
        self.assertFalse(self.link().alt_screen_active)

    def test_h_sets_active(self):
        link = self.link()
        link.process(b'\x1b[?1049h')
        self.assertTrue(link.alt_screen_active)

    def test_h_then_l_clears(self):
        link = self.link()
        link.process(b'\x1b[?1049h')
        link.process(b'\x1b[?1049l')
        self.assertFalse(link.alt_screen_active)

    def test_teardown_omits_1049l_when_never_entered(self):
        out = jbo_wrap._build_teardown(alt_screen_active=False)
        self.assertNotIn(b'\x1b[?1049l', out)
        # other mode disables stay
        self.assertIn(b'\x1b[?1004l', out)
        self.assertIn(b'\x1b[?25h', out)

    def test_teardown_includes_1049l_when_entered(self):
        out = jbo_wrap._build_teardown(alt_screen_active=True)
        self.assertIn(b'\x1b[?1049l', out)
        # ordering: ?1049l must precede ?25h so cursor visibility wins
        self.assertLess(out.index(b'\x1b[?1049l'), out.index(b'\x1b[?25h'))


class TestTrailingWrapPrefix(_Fixture):
    """Regression: trailing wrap held-back logic must only fire when the
    prefix actually looks path-like (contains `/` or `\\`). Otherwise plain
    prose that ends in a letter followed by a CSI cursor-down wrap (e.g.
    Claude Code's Ctrl+C state frame) gets its tail held back until the next
    chunk, leaving the cursor parked mid-frame with previous render residue
    showing through.

    Captured byte sequence from Claude Code's `Press Ctrl-C again to exit`
    frame, where the chunk ends with color-reset + spaces + `\\r\\x1b[1B...`
    cursor moves + `\\x1b[?2026l` (synchronized-update end)."""

    CTRL_C_FRAME = (
        b'\x1b[?2026h\x1b[2D\x1b[5B\x1b[2K\x1b[1A\x1b[2K\x1b[G\x1b[1A'
        b'\r\x1b[2C\x1b[1A'
        b'\x1b[38;5;246mPress Ctrl-C\x1b[1Cagain to exit\x1b[39m'
        b'                       '
        b'\r\x1b[1B\x1b[K\r\x1b[1B\x1b[K\r\x1b[1A\x1b[2C\x1b[3A'
        b'\x1b[?2026l'
    )

    def test_ctrl_c_frame_emits_in_full(self):
        # The whole frame ends in a CSI final byte; nothing token-shaped
        # follows the wrap, so no path can possibly continue past it — the
        # full frame must flow through immediately so cursor moves and erases
        # apply atomically inside the BeginSync/EndSync block.
        out = self.link().process(self.CTRL_C_FRAME)
        self.assertEqual(out, self.CTRL_C_FRAME)

    def test_prose_tail_before_wrap_not_buffered(self):
        # Minimal: chunk ending with letter + wrap + CSI tail. Prose "exit"
        # has no `/` so the wrap should not be classified as a trailing
        # soft-wrap. Output must equal input (no held-back).
        chunk = b'to exit\r\x1b[1B\x1b[?2026l'
        out = self.link().process(chunk)
        self.assertEqual(out, chunk)


class TestAnsiInSoftWrapBoundary(_Fixture):
    """Regression: when an ANSI escape sits inside a soft-wrap region, the
    cut returned by `_find_safe_cut` can be below the escape's byte positions
    (because `hold` is consulted before `ansi_positions`). The set of ANSI
    positions passed to `_linkify` was previously unfiltered, so
    `logical[pos] = 0` raised IndexError once it stepped past the chunk end.

    The Ctrl+C teardown stream from Claude Code reliably hits this pattern."""

    def test_ansi_inside_softwrap_after_cut_does_not_crash(self):
        # Leading space forces cut=1; the `\x1b[1B` anchor is classified as
        # a soft-wrap (flanked by token bytes, prefix `a/b` contains a slash);
        # ansi_positions includes the CSI bytes that sit beyond cut.
        link = self.link()
        chunk = b' a/b\x1b[1Bc.py'
        out = link.process(chunk)
        # Nothing emittable yet (everything past cut is buffered for the
        # cross-chunk path detector). We only assert it doesn't crash.
        self.assertEqual(out, b' ')

    def test_softwrap_with_csi_at_tail_round_trips(self):
        # End-to-end: process + flush of a soft-wrapped path with CSI inside
        # the wrap must produce a valid linkified output without crashing.
        absp = self.cwd / 'src' / 'foo.js'
        absb = str(absp).encode()
        cut = len(absb) // 2
        chunk = b'prefix ' + absb[:cut] + b'\x1b[1B' + absb[cut:] + b':9'
        out = self.run_through(self.link(), chunk)
        self.assertIn(b'\x1b]8;;jbo://open', out)


class TestEdgeCases(_Fixture):
    def test_empty_chunk(self):
        self.assertEqual(self.run_through(self.link(),b''), b'')

    def test_no_paths_in_chunk(self):
        inp = b'hello world, nothing path-shaped here'
        self.assertEqual(self.run_through(self.link(),inp), inp)

    def test_version_number_not_linkified(self):
        # 1.2.3 is not a real file
        out = self.run_through(self.link(),b'version 1.2.3 released')
        self.assertNotIn(b'\x1b]8;;', out)

    def test_ip_address_not_linkified(self):
        out = self.run_through(self.link(),b'connect to 127.0.0.1 please')
        # 127.0.0.1 doesn't exist as a file → no link
        self.assertNotIn(b'\x1b]8;;', out)


if __name__ == '__main__':
    unittest.main()

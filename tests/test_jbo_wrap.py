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

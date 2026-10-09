#!/usr/bin/env python3
"""Verify fresh HNAT readback with large logs; no host kernel controls are read."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent
HELPER = ROOT / 'files/usr/share/wifi-client-limiter/vendor-acceleration.sh'
STOCK = Path(os.environ.get('WCL_STOCK_ROOT', '/tmp/gl483-discovery'))
QEMU = Path(os.environ.get('WCL_STOCK_QEMU', '/tmp/gl-acl-qemu/usr/bin/qemu-aarch64-static'))

# Only the two hardware APIs are simulated. Parsing, shell pattern matching,
# suffix extraction and the production function itself execute unchanged.
CALL = r'''
. "$TEST_HELPER"
internet_vendor_hnat_absent() { return 1; }
internet_vendor_path() { printf '%s/hook_toggle\n' "$TEST_ROOT"; }
dmesg() {
 if [ -f "$TEST_ROOT/second-read" ]; then
  native-cat "$TEST_ROOT/after"
  return "${TEST_AFTER_STATUS:-0}"
 fi
 : > "$TEST_ROOT/second-read"
 native-cat "$TEST_ROOT/before"
 return "${TEST_BEFORE_STATUS:-0}"
}
cat() {
 [ "$1" = "$TEST_ROOT/hook_toggle" ] || return 99
 printf '%s' "${TEST_HOOK_OUTPUT:-}"
 return "${TEST_HOOK_STATUS:-0}"
}
case "${TEST_LOCALE_CHECK:-}" in
 unset) unset LC_ALL;;
 restore) LC_ALL=C.UTF-8; export LC_ALL;;
esac
internet_vendor_hook_read
result=$?
case "${TEST_LOCALE_CHECK:-}" in
 unset) [ "${LC_ALL+x}" != x ] || exit 98;;
 restore) [ "$LC_ALL" = C.UTF-8 ] || exit 97;;
esac
exit "$result"
'''


class HookReaderTests(unittest.TestCase):
    native = False

    @classmethod
    def setUpClass(cls):
        if cls.native:
            if not (QEMU.is_file() and (STOCK / 'bin/busybox').is_file()):
                raise unittest.SkipTest('audited GL4.8.3 native tools are not installed')
            cls.shell = [str(QEMU), '-L', str(STOCK), str(STOCK / 'bin/busybox'), 'ash']
        else:
            cls.shell = ['/bin/sh']

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wcl-hook-reader-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.bin = self.path / 'bin'
        self.bin.mkdir()
        for name in ('native-cat', 'tail', 'awk'):
            applet = 'cat' if name == 'native-cat' else name
            if self.native:
                command = [str(QEMU), '-L', str(STOCK), str(STOCK / 'bin/busybox'), applet]
            else:
                command = [shutil.which(applet)]
            wrapper = '#!/bin/sh\n'
            if applet in ('tail', 'awk'):
                if applet == 'tail':
                    wrapper += 'case "$1" in -c) stage=suffix;; -n) stage=anchor;; *) exit 96;; esac\n'
                else:
                    wrapper += 'case "$1" in *"matches {print}"*) stage=overlap;; *ENVIRON*) stage=unique;; *) stage=readback;; esac\n'
                # A provider printing a valid result before failing must never
                # cause a successful hardware readback.
                wrapper += 'if [ "${TEST_TOOL_FAILURE:-}" = "$stage" ]; then\n'
                wrapper += ' ' + shlex.join(command) + ' "$@"\n exit 7\nfi\n'
            wrapper += 'exec ' + shlex.join(command) + ' "$@"\n'
            tool = self.bin / name
            tool.write_text(wrapper)
            tool.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin), TEST_ROOT=str(self.path), TEST_HELPER=str(HELPER))

    def run_hook(self, before, after, **values):
        (self.path / 'before').write_bytes(before)
        (self.path / 'after').write_bytes(after)
        (self.path / 'second-read').unlink(missing_ok=True)
        start = time.monotonic()
        result = subprocess.run(self.shell + ['-c', CALL], env=dict(self.env, **values),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=4)
        self.elapsed = time.monotonic() - start
        return result

    def assert_unknown(self, before, after, **values):
        result = self.run_hook(before, after, **values)
        self.assertEqual(result.returncode, 1, result.stderr.decode(errors='replace'))
        self.assertEqual(result.stdout, b'')

    def test_large_ring_and_many_new_messages_finish_with_fresh_state(self):
        line = b'[12345.000000] wlan: status=established dev=2 no private user data\n'
        for size in (128 * 1024, 256 * 1024):
            for state, word in ((0, b'disabled'), (1, b'enabled')):
                with self.subTest(size=size, state=state):
                    before = (line * (size // len(line) + 1))[:size]
                    suffix = b'\n' + line * 512 + b'[99999.000000] value=' + str(state).encode() + b', hook is ' + word + b' now!\n'
                    result = self.run_hook(before, before + suffix)
                    self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
                    self.assertEqual(result.stdout, str(state).encode() + b'\n')

    def test_completely_replaced_log_without_boundary_is_unknown(self):
        self.assert_unknown(b'[1] original log\n', b'[2] value=0, hook is disabled now!\n')

    def test_full_ring_rotation_keeps_fresh_readback(self):
        before = b''.join(('[%d.000001] bounded log record\n' % i).encode() for i in range(4000))
        for state, word in ((0, b'disabled'), (1, b'enabled')):
            with self.subTest(state=state):
                fresh = b'[4000.000001] value=' + str(state).encode() + b', hook is ' + word + b' now!\n'
                after = before.split(b'\n', 1)[1] + fresh
                result = self.run_hook(before, after)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
                self.assertEqual(result.stdout, str(state).encode() + b'\n')

    def test_sustained_bounded_ring_with_concurrent_readers(self):
        # Every scan rotates a full ring. Other readers may add the same fresh
        # state; no old record can remain at the beginning of the next sample.
        lines = [('[%d.000001] bounded log record\n' % i).encode() for i in range(2400)]
        next_stamp = 2400
        for scan in range(32):
            before = b''.join(lines)
            state = scan % 2
            word = b'enabled' if state else b'disabled'
            count = scan % 4 + 1
            fresh = [('[%d.000001] value=%d, hook is ' % (next_stamp + i, state)).encode() + word + b' now!\n' for i in range(count)]
            next_stamp += count
            lines = lines[count:] + fresh
            result = self.run_hook(before, b''.join(lines))
            self.assertEqual(result.returncode, 0, (scan, result.stderr.decode(errors='replace')))
            self.assertEqual(result.stdout, str(state).encode() + b'\n')

    def test_rotated_old_hook_is_not_fresh(self):
        before = b'[1] old log\n[2] value=0, hook is disabled now!\n[3] boundary\n'
        after = before.split(b'\n', 1)[1] + b'[4] unrelated new record\n'
        self.assert_unknown(before, after)

    def test_rotated_hook_boundary_does_not_count_as_fresh(self):
        before = b'[1] old log\n[2] value=0, hook is disabled now!\n'
        after = before.split(b'\n', 1)[1] + b'[3] unrelated new record\n'
        self.assert_unknown(before, after)

    def test_duplicate_boundary_in_either_snapshot_is_unknown(self):
        anchor = b'[2] duplicate record\n'
        hook = b'[4] value=0, hook is disabled now!\n'
        self.assert_unknown(anchor + b'[3] old record\n' + anchor, anchor + hook)
        self.assert_unknown(b'[1] old record\n' + anchor, anchor + hook + anchor)

    def test_rotated_boundary_is_literal_with_backslashes_and_unicode(self):
        anchor = '[2] literal * ? [x] \\n \\t é 中\n'.encode()
        before = b'[1] old record\n' + anchor
        after = anchor + b'[3] value=0, hook is disabled now!\n'
        result = self.run_hook(before, after, TEST_LOCALE_CHECK='restore')
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertEqual(result.stdout, b'0\n')

    def test_conflicting_or_malformed_fresh_records_are_unknown(self):
        before = b'[1] old record\n[2] boundary\n'
        good = b'[3] value=0, hook is disabled now!\n'
        for suffix in (b'[4] value=1, hook is enabled now!\n',
                       b'[4] value=1, hook is enabled now\n',
                       b'[4] value=0, hook is enabled now!\n',
                       b'[4] value=2, hook is disabled now!\n'):
            for prefix in (before, before.split(b'\n', 1)[1]):
                with self.subTest(suffix=suffix, rotated=prefix != before):
                    self.assert_unknown(before, prefix + good + suffix)

    def test_unchanged_log_with_old_hook_message_is_unknown(self):
        data = b'[1] value=0, hook is disabled now!\n'
        self.assert_unknown(data, data)

    def test_new_unrelated_message_cannot_reuse_stale_conflicting_hooks(self):
        before = b'[1] value=1, hook is enabled now!\n[2] value=0, hook is disabled now!\n'
        self.assert_unknown(before, before + b'[3] unrelated fresh message\n')

    def test_truncated_fresh_hook_is_unknown(self):
        before = b'[1] value=0, hook is disabled now!\n'
        self.assert_unknown(before, before + b'[3] value=1, hook is enabled now\n')

    def test_unknown_or_inconsistent_fresh_value_is_unknown(self):
        before = b'[1] original log\n'
        for value, word in ((b'2', b'disabled'), (b'0', b'enabled'), (b'1', b'disabled')):
            with self.subTest(value=value, word=word):
                self.assert_unknown(before, before + b'[2] value=' + value + b', hook is ' + word + b' now!\n')

    def test_literal_prefix_metacharacters_and_utf8_are_byte_correct(self):
        before = '[1] literal * ? [x] \\ é 中\n[2] value=1, hook is enabled now!\n'.encode()
        result = self.run_hook(before, before + b'[3] value=0, hook is disabled now!\n', TEST_LOCALE_CHECK='restore')
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertEqual(result.stdout, b'0\n')

    def test_local_locale_does_not_leak_into_caller(self):
        result = self.run_hook(b'[1] log\n', b'[1] log\n[2] value=0, hook is disabled now!\n', TEST_LOCALE_CHECK='unset')
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertEqual(result.stdout, b'0\n')

    def test_dmesg_or_control_failures_remain_unknown(self):
        before = b'[1] log\n'
        after = before + b'[2] value=0, hook is disabled now!\n'
        for key, value in (('TEST_BEFORE_STATUS', '7'), ('TEST_AFTER_STATUS', '7'),
                           ('TEST_HOOK_STATUS', '7'), ('TEST_HOOK_OUTPUT', 'unexpected')):
            with self.subTest(key=key):
                self.assert_unknown(before, after, **{key: value})

    def test_partial_output_from_failed_parser_provider_is_unknown(self):
        before = b'[1] log\n'
        after = before + b'[2] value=0, hook is disabled now!\n'
        for stage in ('suffix', 'readback'):
            with self.subTest(stage=stage):
                self.assert_unknown(before, after, TEST_TOOL_FAILURE=stage)
        before = b'[1] old log\n[2] boundary\n'
        after = b'[2] boundary\n[3] value=0, hook is disabled now!\n'
        for stage in ('anchor', 'unique', 'overlap', 'readback'):
            with self.subTest(stage=stage):
                self.assert_unknown(before, after, TEST_TOOL_FAILURE=stage)


class NativeHookReaderTests(HookReaderTests):
    native = True


if __name__ == '__main__':
    unittest.main()

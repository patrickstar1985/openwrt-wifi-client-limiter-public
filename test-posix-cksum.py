#!/usr/bin/env python3
"""Check compatible fingerprints and fail-closed provider handling.

Optional WCL_STOCK_ROOT/WCL_STOCK_QEMU runs the exact extracted GL BusyBox
shell, hexdump and awk with an isolated PATH. Native binaries stay outside Git.
"""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
HELPER = ROOT/'files/usr/share/wifi-client-limiter/posix-cksum.sh'

# Host fixture replaces byte conversion only, leaving the production CRC awk
# unchanged. Native integration instead uses the actual firmware hexdump.
HOST_HEXDUMP = r'''import sys
if sys.argv[1:] != ['-v', '-e', '1/1 "%u\\n"']:
    sys.exit(2)
for value in sys.stdin.buffer.read():
    print(value)
'''


class PosixChecksumTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='wcl-cksum-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        self.bin = self.path/'bin'
        self.bin.mkdir()
        self.reference = shutil.which('cksum')
        self.assertTrue(self.reference, 'Host POSIX cksum is needed as an independent reference')
        self.shell = [shutil.which('sh')]
        self.native = False
        stock, qemu = os.environ.get('WCL_STOCK_ROOT'), os.environ.get('WCL_STOCK_QEMU')
        if stock or qemu:
            self.assertTrue(stock and qemu, 'Set both native integration variables')
            stock = Path(stock).resolve()
            prefix = [str(Path(qemu).resolve()), '-L', str(stock)]
            self.busybox = prefix+[str(stock/'bin/busybox')]
            self.assertTrue(Path(self.busybox[-1]).is_file())
            self.shell = self.busybox+['ash']
            self.native = True
            for app in ('awk', 'hexdump'):
                self.program(app, '#!/bin/sh\nexec '+shlex.join(self.busybox+[app])+' "$@"\n')
        else:
            awk = shutil.which('awk')
            self.assertTrue(awk)
            self.program('awk', '#!/bin/sh\nexec '+shlex.quote(awk)+' "$@"\n')
            self.program('hexdump', '#!'+sys.executable+'\n'+HOST_HEXDUMP)

    def program(self, name, source):
        program = self.bin/name
        program.write_text(source)
        program.chmod(0o755)

    def run_helper(self, data=b'', setup=''):
        script = self.path/'run.sh'
        script.write_text('. '+shlex.quote(str(HELPER))+'\n'+setup+'\ninternet_posix_cksum\n')
        return subprocess.run(self.shell+[str(script)], env=dict(os.environ, PATH=str(self.bin)),
                              input=data, capture_output=True, timeout=45)

    def expected(self, data):
        values = subprocess.run([self.reference], input=data, capture_output=True,
                                check=True).stdout.decode().split()
        return ':'.join(values[:2])+'\n'

    def assert_rejected(self, result):
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b'')

    def test_fallback_matches_posix_cksum_for_binary_and_text(self):
        cases = (b'', b'\x00', b'\xff', b'123456789', b'\n', b'one\r\ntwo\r\n',
                 'Grüße — 网络\n'.encode(), bytes(range(256)), b'a\x00b\xff\r\nc\n\x00',
                 b"=defaults\n.forward='REJECT'\n.input='ACCEPT'\n.output='ACCEPT'\n",
                 "=defaults\n.comment='Gerät 网络'\n.forward='REJECT'\n".encode())
        for data in cases:
            with self.subTest(data=data):
                result = self.run_helper(data)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.decode(), self.expected(data))

    def test_legacy_snapshot_fingerprint_is_unchanged(self):
        data = b"=defaults\n.forward='REJECT'\n.input='ACCEPT'\n.output='ACCEPT'\n"
        self.assertEqual(self.run_helper(data).stdout, b'2333694186:61\n')

    def test_length_boundaries_and_larger_input(self):
        for size in (255, 256, 257, 65536):
            with self.subTest(size=size):
                data = (bytes(range(256))*((size+255)//256))[:size]
                result = self.run_helper(data)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.decode(), self.expected(data))

    def test_available_cksum_is_fast_provider(self):
        self.program('cksum', '#!/bin/sh\nexec '+shlex.quote(self.reference)+' "$@"\n')
        data = b'available provider\x00\xff\n'
        result = self.run_helper(data, 'hexdump() { return 88; }')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.decode(), self.expected(data))

    def test_failing_available_cksum_never_falls_back_or_emits_fingerprint(self):
        self.assert_rejected(self.run_helper(b'test', 'cksum() { printf "123 4\\n"; return 7; }'))

    def test_malformed_cksum_output_is_rejected(self):
        for value in ('', '123', '123 4 extra', '123 x', '-1 4', '123 -1',
                      '4294967296 4', '123 9007199254740992', '1 2\n3 4', '1:2'):
            with self.subTest(value=value):
                setup = 'cksum() { printf "%s\\n" '+shlex.quote(value)+'; }'
                self.assert_rejected(self.run_helper(b'test', setup))

    def test_missing_fallback_tools_fail_without_fingerprint(self):
        for app in ('hexdump', 'awk'):
            with self.subTest(app=app):
                program = self.bin/app
                target = self.bin/(app+'.disabled')
                program.rename(target)
                try:
                    self.assert_rejected(self.run_helper(b'test'))
                finally:
                    target.rename(program)

    def test_partial_failed_hexdump_cannot_yield_fingerprint(self):
        self.assert_rejected(self.run_helper(b'test', 'hexdump() { printf "1\\n"; return 7; }'))

    def test_failing_awk_cannot_yield_fingerprint(self):
        self.assert_rejected(self.run_helper(b'test', 'awk() { return 7; }'))

    def test_partial_failed_record_validator_cannot_emit_fingerprint(self):
        setup = 'cksum() { printf "123 4\\n"; }\n'
        setup += 'awk() { '+shlex.quote(str(self.bin/'awk'))+' "$@"; return 7; }'
        self.assert_rejected(self.run_helper(b'test', setup))

    def test_invalid_byte_conversion_is_rejected(self):
        for value in ('256', '-1', 'INVALID', '1 2'):
            with self.subTest(value=value):
                setup = 'hexdump() { printf "%s\\n" '+shlex.quote(value)+'; }'
                self.assert_rejected(self.run_helper(b'test', setup))

    def test_native_busybox_contract(self):
        if not self.native:
            self.skipTest('Set native integration variables for actual firmware tools')
        result = subprocess.run(self.busybox+['cksum'], capture_output=True)
        self.assertEqual(result.returncode, 127)
        self.assertIn(b'applet not found', result.stderr)
        for app in ('awk', 'hexdump'):
            result = subprocess.run(self.busybox+[app, '--help'], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()

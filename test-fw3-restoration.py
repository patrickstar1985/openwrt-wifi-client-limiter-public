#!/usr/bin/env python3
"""Verify fw3 restoration with isolated configuration and firewall fixtures.

The production helper, backend detection and live dump validation run unchanged.
OpenWrt APIs are fixtures; no real firewall, UCI or module command runs.
Set WCL_STOCK_ROOT/WCL_STOCK_QEMU to use the extracted GL BusyBox ash.
"""
# SPDX-License-Identifier: MIT
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
HELPER = ROOT / 'files/usr/share/wifi-client-limiter/internet-policy.sh'
DUMP = '*filter\n:FORWARD ACCEPT [0:0]\nCOMMIT\n'
OFFLOAD = '*filter\n:FORWARD ACCEPT [0:0]\n-A FORWARD -j FLOWOFFLOAD\nCOMMIT\n'
HARDWARE = OFFLOAD.replace('FLOWOFFLOAD\n', 'FLOWOFFLOAD --hw\n')

HARNESS = r'''
fw3() {
 printf 'fw3 %s\n' "$*" >> "$FIX/calls"
 case "$*" in
  '-4 print') cat "$FIX/expected4"; return "$PRINT4_STATUS";;
  '-6 print') cat "$FIX/expected6"; return "$PRINT6_STATUS";;
  *) printf 'Unexpected firewall mutation\n' >&2; return 94;;
 esac
}
uci() {
 printf 'uci %s\n' "$*" >> "$FIX/calls"
 [ "$*" = '-q get firewall.@defaults[0].disable_ipv6' ] || {
  printf 'Unexpected UCI operation\n' >&2; return 94;
 }
 printf '%s\n' "$DISABLE_IPV6"
 return "$UCI_STATUS"
}
nft() { printf 'Unexpected nft operation\n' >&2; return 94; }
rmmod() { printf 'Unexpected module operation\n' >&2; return 94; }
modprobe() { printf 'Unexpected module operation\n' >&2; return 94; }
. "$HELPER"
internet_offload_restored
result=$?
printf 'RETURN=%s\n' "$result"
'''

SAVE_FIXTURE = r'''#!/bin/sh
printf '%s %s\n' "${0##*/}" "$*" >> "$FIX/calls"
[ "$*" = '-t filter' ] || exit 94
case "${0##*/}" in
 iptables-save) cat "$FIX/live4"; exit "$LIVE4_STATUS";;
 ip6tables-save) cat "$FIX/live6"; exit "$LIVE6_STATUS";;
 *) exit 94;;
esac
'''


class Fw3RestorationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='wifi-limiter-fw3-restore-')
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        (self.path / 'sys-module').mkdir()
        (self.path / 'bin').mkdir()
        # Isolate PATH so host fw4 or any native firewall tool cannot be found.
        for name in ('cat', 'grep'):
            binary = shutil.which(name)
            self.assertIsNotNone(binary, f'Missing test utility: {name}')
            (self.path / 'bin' / name).symlink_to(binary)
        for name in ('iptables-save', 'ip6tables-save'):
            fixture = self.path / 'bin' / name
            fixture.write_text(SAVE_FIXTURE)
            fixture.chmod(0o755)
        self.shell = ['/bin/sh']
        stock, qemu = os.environ.get('WCL_STOCK_ROOT'), os.environ.get('WCL_STOCK_QEMU')
        if stock or qemu:
            self.assertTrue(stock and qemu, 'Set both native integration variables')
            stock, qemu = Path(stock).resolve(), Path(qemu).resolve()
            for binary in (qemu, stock / 'bin/busybox'):
                self.assertTrue(binary.is_file(), f'Missing native test binary: {binary}')
            self.shell = [str(qemu), '-L', str(stock), str(stock / 'bin/busybox'), 'ash']
        (self.path / 'run.sh').write_text(HARNESS)

    def run_restore(self, *, disable='', uci_status=1, expected4=DUMP, expected6=DUMP,
                    print4_status=0, print6_status=0, live4=DUMP, live6=DUMP,
                    live4_status=0, live6_status=0):
        for name, body in (('expected4', expected4), ('expected6', expected6),
                           ('live4', live4), ('live6', live6)):
            (self.path / name).write_text(body)
        (self.path / 'calls').write_text('')
        environment = dict(os.environ, FIX=str(self.path), HELPER=str(HELPER),
                           INTERNET_MODULE_ROOT=str(self.path / 'sys-module'),
                           PATH=str(self.path / 'bin'), DISABLE_IPV6=disable,
                           UCI_STATUS=str(uci_status), PRINT4_STATUS=str(print4_status),
                           PRINT6_STATUS=str(print6_status), LIVE4_STATUS=str(live4_status),
                           LIVE6_STATUS=str(live6_status))
        done = subprocess.run(self.shell + [str(self.path / 'run.sh')], env=environment,
                              text=True, capture_output=True, timeout=10)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr, '')
        self.assertIn(done.stdout.strip(), ('RETURN=0', 'RETURN=1'))
        return int(done.stdout.strip().split('=')[1]), (self.path / 'calls').read_text().splitlines()

    def test_explicit_fw3_true_values_skip_ipv6_intent_print_and_prove_both_live_families(self):
        # Exact stock fw3 parser accepts only 1/true/yes. Its debug IPv6 start
        # returns1 with no output when that flag is set (native audit VA411dbc).
        for value in ('1', 'true', 'yes'):
            with self.subTest(value=value):
                status, calls = self.run_restore(disable=value, uci_status=0,
                                                 expected6='', print6_status=1)
                self.assertEqual(status, 0)
                self.assertIn('fw3 -4 print', calls)
                self.assertNotIn('fw3 -6 print', calls)
                self.assertIn('iptables-save -t filter', calls)
                self.assertIn('ip6tables-save -t filter', calls)

    def test_absent_or_unreadable_option_never_excuses_ipv6_print_failure(self):
        for value, read_status in (('', 1), ('1', 1), ('', 2)):
            with self.subTest(value=value, read_status=read_status):
                status, calls = self.run_restore(disable=value, uci_status=read_status,
                                                 expected6='', print6_status=1)
                self.assertEqual(status, 1)
                self.assertIn('fw3 -6 print', calls)

    def test_nontrue_flags_and_sysctl_only_disable_still_require_ipv6_print(self):
        # Sysctl state is not fw3's defaults.disable_ipv6 option. Keep it an
        # unrelated fixture file; production must not infer firewall intent.
        (self.path / 'ipv6-disable-all').write_text('1\n')
        for value in ('', '0', 'false', 'no', 'off', 'on', 'TRUE', 'yes\nyes'):
            with self.subTest(value=value):
                status, calls = self.run_restore(disable=value, uci_status=0,
                                                 expected6='', print6_status=1)
                self.assertEqual(status, 1)
                self.assertIn('fw3 -6 print', calls)

    def test_ambiguous_print_errors_fail_even_with_nonempty_output(self):
        for family in (4, 6):
            for print_status in (1, 2, 127):
                with self.subTest(family=family, print_status=print_status):
                    options = {'print' + str(family) + '_status': print_status,
                               'expected' + str(family): OFFLOAD}
                    status, _ = self.run_restore(**options)
                    self.assertEqual(status, 1)

    def test_ipv4_print_errors_remain_fatal_with_explicit_ipv6_disable(self):
        status, _ = self.run_restore(disable='1', uci_status=0, print4_status=1)
        self.assertEqual(status, 1)

    def test_disabled_ipv6_still_rejects_unavailable_empty_or_incomplete_live_dump(self):
        for body, status in ((DUMP, 1), ('', 0), ('*filter\nCOMMIT\n', 0)):
            with self.subTest(body=body, live_status=status):
                result, calls = self.run_restore(disable='1', uci_status=0,
                                                 live6=body, live6_status=status)
                self.assertEqual(result, 1)
                self.assertIn('ip6tables-save -t filter', calls)

    def test_disabled_ipv6_rejects_retained_software_or_hardware_offload(self):
        for body in (OFFLOAD, HARDWARE):
            with self.subTest(hardware=body == HARDWARE):
                status, _ = self.run_restore(disable='true', uci_status=0, live6=body)
                self.assertEqual(status, 1)

    def test_enabled_families_keep_offload_and_hardware_intent_comparison(self):
        for expected, live, result in ((DUMP, DUMP, 0), (OFFLOAD, OFFLOAD, 0),
                                       (HARDWARE, HARDWARE, 0), (HARDWARE, OFFLOAD, 1),
                                       (OFFLOAD, HARDWARE, 1), (OFFLOAD, DUMP, 1),
                                       (DUMP, OFFLOAD, 1)):
            for family in (4, 6):
                with self.subTest(family=family, expected=expected, live=live):
                    options = {'expected' + str(family): expected, 'live' + str(family): live}
                    status, _ = self.run_restore(**options)
                    self.assertEqual(status, result)


if __name__ == '__main__':
    unittest.main(verbosity=2)

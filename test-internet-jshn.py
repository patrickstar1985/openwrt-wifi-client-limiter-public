#!/usr/bin/env python3
"""Exercise real prefix consumers with stock GL jshn and synthetic API fixtures.

Default mode reuses the C-compatible decoder from test-jshn-discovery.py.
Set WCL_STOCK_ROOT/WCL_STOCK_QEMU to run actual firmware jshn and BusyBox ash.
No router configuration, firewall, vendor controls or network devices are used.
"""
# SPDX-License-Identifier: MIT
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
JSHN = ROOT / 'tests/fixtures/libubox-20210516-jshn.sh'
INTERNET = ROOT / 'files/usr/share/wifi-client-limiter/internet-policy.sh'
CHECKSUM_HELPER = ROOT / 'files/usr/share/wifi-client-limiter/posix-cksum.sh'
RPC = ROOT / 'files/usr/libexec/rpcd/wifi-client-limiter'
decoder_spec = importlib.util.spec_from_file_location('discovery_jshn_fixture', ROOT / 'test-jshn-discovery.py')
decoder_fixture = importlib.util.module_from_spec(decoder_spec)
decoder_spec.loader.exec_module(decoder_fixture)
RPC_PREFIX_FUNCTIONS = RPC.read_text().split('rpc_prefix_value() {', 1)[1].split(
    '# Canonicalize the complete owned table,', 1)[0]
RPC_PREFIX_FUNCTIONS = 'rpc_prefix_value() {' + RPC_PREFIX_FUNCTIONS
IPV4 = {'address': '192.0.2.1', 'mask': 24}
POOL = {'address': 'fd00:1234:abcd::', 'mask': 60}
LOCAL = {'address': 'fd00:1234:abcd:1::1', 'mask': 64}

SHIM = r'''
ubus() {
 printf '%s\n' "$*" >> "$FIX/calls"
 case "$1:$2:$3" in
  call:network.wireless:status) cat "$FIX/wireless.json";;
  call:network.interface.lan:status) cat "$FIX/lan.json";;
  *) printf 'Unexpected ubus operation\n' >&2; return 94;;
 esac
}
config_load() {
 [ "$1" = wireless ] || { printf 'Unexpected UCI package\n' >&2; return 94; }
}
config_get() {
 case "$2:$3" in
  guest2g:network|guest5g:network) eval "$1=lan";;
  *) printf 'Unexpected UCI read\n' >&2; return 94;;
 esac
}
nft() { printf 'Unexpected firewall operation\n' >&2; return 94; }
uci() { printf 'Unexpected direct UCI operation\n' >&2; return 94; }
iwpriv() { printf 'Unexpected driver operation\n' >&2; return 94; }
'''


class StockJshnInternetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='wifi-limiter-internet-jshn-')
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.shell = ['bash']
        reader = decoder_fixture.JSON_READER
        stock, qemu = os.environ.get('WCL_STOCK_ROOT'), os.environ.get('WCL_STOCK_QEMU')
        if stock or qemu:
            self.assertTrue(stock and qemu, 'Set both native integration environment variables')
            stock, qemu = Path(stock).resolve(), str(Path(qemu).resolve())
            for binary in (Path(qemu), stock / 'bin/busybox', stock / 'usr/bin/jshn'):
                self.assertTrue(binary.is_file(), f'Missing native integration binary: {binary}')
            prefix = [qemu, '-L', str(stock)]
            reader = '#!/bin/sh\nexec ' + shlex.join(prefix + [str(stock / 'usr/bin/jshn')]) + ' "$@"\n'
            self.shell = prefix + [str(stock / 'bin/busybox'), 'ash']
        (self.path / 'jshn').write_text(reader)
        (self.path / 'jshn').chmod(0o755)
        (self.path / 'state').mkdir()
        self.wireless = {
            'radio0': {'interfaces': [{'section': 'guest2g', 'ifname': 'wlan0',
                                      'config': {'mode': 'ap', 'network': ['lan']}}]},
            'radio1': {'interfaces': [{'section': 'guest5g', 'ifname': 'wlan1',
                                      'config': {'mode': 'ap', 'network': ['lan']}}]},
        }

    def run_consumer(self, consumer, lan):
        self.assertIn(consumer, ('worker', 'rpc'))
        (self.path / 'wireless.json').write_text(json.dumps(self.wireless))
        (self.path / 'lan.json').write_text(json.dumps(lan))
        (self.path / 'calls').write_text('')
        harness = self.path / 'run.sh'
        command = r'''
networks='guest2g guest5g'
INTERNET_STATE="$FIX/state"
if [ "$CONSUMER" = worker ]; then
 internet_collect > "$FIX/consumer-stdout"
 result=$?
 if [ "$result" = 0 ]; then
  INTERNET_FAMILY=netdev
  white=
  internet_render || exit 95
 fi
else
 rpc_current_prefixes > "$FIX/rpc-prefixes"
 result=$?
fi
printf 'RETURN=%s\n' "$result"
'''
        harness.write_text('. "$STOCK_JSHN"\n. "$INTERNET_HELPER"\n' +
                           RPC_PREFIX_FUNCTIONS + '\n' + SHIM + '\n' + command)
        environment = dict(os.environ, FIX=str(self.path), STOCK_JSHN=str(JSHN),
                           INTERNET_CHECKSUM_HELPER=str(CHECKSUM_HELPER),
                           INTERNET_HELPER=str(INTERNET), CONSUMER=consumer,
                           PATH=str(self.path) + os.pathsep + os.environ['PATH'])
        done = subprocess.run(self.shell + [str(harness)], env=environment, text=True,
                              capture_output=True, check=True, timeout=10)
        self.assertNotIn('Unexpected', done.stderr)
        self.assertIn(done.stdout.strip(), ('RETURN=0', 'RETURN=1'))
        output = self.path / ('state/internet-prefixes-next' if consumer == 'worker' else 'rpc-prefixes')
        prefixes = {line.strip() for line in output.read_text().splitlines() if line.strip()}
        rules = self.path / 'state/internet-policy-next'
        return int(done.stdout.strip().split('=')[1]), prefixes, rules.read_text() if rules.exists() else ''

    def assert_consumers(self, lan, expected, success=True):
        for consumer in ('worker', 'rpc'):
            with self.subTest(consumer=consumer):
                status, prefixes, rules = self.run_consumer(consumer, lan)
                self.assertEqual(status, 0 if success else 1)
                if success:
                    self.assertEqual(prefixes, set(expected))
                self.assertNotIn('6 fd00:1234:abcd::/60', prefixes)
                self.assertNotIn('ip6 daddr fd00:1234:abcd::/60 return', rules)
                if consumer == 'worker' and success:
                    self.assertIn('ether type { 0x0800, 0x86dd } counter drop', rules)

    def test_stock_shell_fixture_is_unchanged(self):
        original = b'# functions for parsing and generating json' + JSHN.read_bytes().split(
            b'# functions for parsing and generating json', 1)[1]
        self.assertEqual(hashlib.sha256(original).hexdigest(),
                         'c5056cf87f47d12625a5695cfc1f5b48df4807b9095665dd9c682ca977d1eb59')

    def test_ipv4_and_empty_delegated_local_object_preserve_ipv4_only(self):
        # Generic addresses reproduce the actual reported netifd structure.
        self.assert_consumers({'ipv4-address': [IPV4], 'ipv6-address': [],
                               'ipv6-prefix-assignment': [dict(POOL, **{'local-address': {}})]},
                              ['4 192.0.2.0/24'])

    def test_missing_optional_local_object_does_not_grant_parent_pool(self):
        self.assert_consumers({'ipv4-address': [IPV4], 'ipv6-address': [],
                               'ipv6-prefix-assignment': [POOL]}, ['4 192.0.2.0/24'])

    def test_valid_local_ipv6_uses_its_own_mask_and_not_delegated_mask(self):
        self.assert_consumers({'ipv4-address': [IPV4], 'ipv6-address': [],
                               'ipv6-prefix-assignment': [dict(POOL, **{'local-address': LOCAL})]},
                              ['4 192.0.2.0/24', '6 fd00:1234:abcd:1::1/64'])

    def test_ipv6_local_only_remains_supported(self):
        self.assert_consumers({'ipv4-address': [], 'ipv6-address': [],
                               'ipv6-prefix-assignment': [dict(POOL, **{'local-address': LOCAL})]},
                              ['6 fd00:1234:abcd:1::1/64'])

    def test_partial_or_malformed_nonempty_local_objects_are_rejected(self):
        invalid = [
            {'address': LOCAL['address']}, {'mask': 64},
            {'address': '', 'mask': ''}, {'unrecognized': True},
            {'address': 'not-an-ipv6-address', 'mask': 64},
            {'address': LOCAL['address'], 'mask': 0},
            {'address': LOCAL['address'], 'mask': 'bad-mask'},
            {'address': LOCAL['address'], 'mask': 129},
        ]
        for value in invalid:
            with self.subTest(local_address=value):
                self.assert_consumers({'ipv4-address': [IPV4], 'ipv6-address': [],
                                       'ipv6-prefix-assignment': [dict(POOL, **{'local-address': value})]},
                                      [], success=False)

    def test_present_local_address_wrong_types_are_rejected(self):
        for value in (None, [], [LOCAL], '', 'fd00::1', 64, False):
            with self.subTest(local_address=value):
                self.assert_consumers({'ipv4-address': [IPV4], 'ipv6-address': [],
                                       'ipv6-prefix-assignment': [dict(POOL, **{'local-address': value})]},
                                      [], success=False)

    def test_present_local_fields_require_string_address_and_integer_mask(self):
        invalid = [dict(LOCAL, mask=value) for value in (True, '64', 64.0)]
        invalid += [dict(LOCAL, address=value) for value in
                    (None, True, 64, 64.0, [], {}, [LOCAL['address']])]
        for value in invalid:
            with self.subTest(local_address=value):
                self.assert_consumers({'ipv4-address': [IPV4], 'ipv6-address': [],
                                       'ipv6-prefix-assignment': [dict(POOL, **{'local-address': value})]},
                                      [], success=False)

    def test_empty_assignment_without_any_local_address_never_grants_pool(self):
        for assignment in (POOL, dict(POOL, **{'local-address': {}})):
            lan = {'ipv4-address': [], 'ipv6-address': [], 'ipv6-prefix-assignment': [assignment]}
            with self.subTest(assignment=assignment):
                status, prefixes, _ = self.run_consumer('worker', lan)
                self.assertEqual(status, 1, 'Worker requires an actual local prefix')
                self.assertEqual(prefixes, set())
                _, prefixes, _ = self.run_consumer('rpc', lan)
                self.assertEqual(prefixes, set(), 'Observer must not invent a delegated-pool allowance')


if __name__ == '__main__':
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Exercise discovery with the stock GL/OpenWrt jshn shell implementation."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
ADAPTER = (ROOT / 'files/usr/share/wifi-client-limiter/station-adapter.sh').read_text()
JSHN = ROOT / 'tests/fixtures/libubox-20210516-jshn.sh'
MAC = 'aa:00:00:00:00:01'

# Replace only the architecture-specific executables. Match the stock C
# decoder's key sanitation before its generated json_add_* calls reach the
# unmodified shell library; discarded original names cannot create metadata.
JSON_READER = r'''#!/usr/bin/env python3
import json
import re
import sys

if len(sys.argv) != 3 or sys.argv[1] != '-r':
    sys.exit(2)
data = json.loads(sys.argv[2])
if not isinstance(data, dict):
    sys.exit(1)
print('json_init;')

def quote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"

def emit(name, value):
    quoted = quote(re.sub(r'[^a-zA-Z0-9]', '_', str(name)))
    if isinstance(value, dict):
        print('json_add_object ' + quoted + ';')
        for key, child in value.items():
            emit(key, child)
        print('json_close_object;')
    elif isinstance(value, list):
        print('json_add_array ' + quoted + ';')
        for index, child in enumerate(value):
            emit(index, child)
        print('json_close_array;')
    elif isinstance(value, bool):
        print('json_add_boolean ' + quoted + (' 1;' if value else ' 0;'))
    elif value is None:
        print('json_add_null ' + quoted + ';')
    elif isinstance(value, int):
        print('json_add_int ' + quoted + ' ' + str(value) + ';')
    else:
        print('json_add_string ' + quoted + ' ' + quote(value) + ';')

for key, value in data.items():
    emit(key, value)
'''

JSON_FILTER = r'''#!/usr/bin/env python3
import json
import re
import sys

if len(sys.argv) != 5 or sys.argv[1] != '-s' or sys.argv[3] != '-e':
    sys.exit(2)
query = re.fullmatch(r"@\.clients\['((?:[a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2})'\]", sys.argv[4])
if not query:
    sys.exit(2)
data = json.loads(sys.argv[2])
clients = data.get('clients')
if not isinstance(clients, dict) or query[1] not in clients:
    sys.exit(1)
print(json.dumps(clients[query[1]]))
'''

SHIM = r'''
ubus() {
 printf '%s\n' "$*" >> "$FIX/calls"
 case "$2:$3" in
  network.wireless:status) cat "$FIX/wireless.json";;
  hostapd.wlan0:get_clients) cat "$FIX/clients.json";;
  *) return 1;;
 esac
}
uci() {
 printf '%s\n' "$*" >> "$FIX/calls"
 case "$*" in
  '-q get wireless.mtk0.type') printf 'mtk\n';;
  '-q get wireless.radio0.type') printf 'mac80211\n';;
  *) return 1;;
 esac
}
iwinfo() {
 printf 'iwinfo %s\n' "$*" >> "$FIX/calls"
 [ "$1:$2" = 'ra0:assoclist' ] || return 1
 cat "$FIX/assoclist"
}
iwpriv() { printf 'Unexpected mutation\n' >&2; return 94; }
'''


class StockJshnDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='wifi-limiter-jshn-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        reader, filter_program = JSON_READER, JSON_FILTER
        self.shell = ['bash']
        # Optional integration mode executes the actual extracted ARM firmware
        # binaries; native binaries and emulators stay outside the repository.
        stock = os.environ.get('WCL_STOCK_ROOT')
        qemu = os.environ.get('WCL_STOCK_QEMU')
        if stock or qemu:
            if not stock or not qemu:
                self.fail('Set both WCL_STOCK_ROOT and WCL_STOCK_QEMU for native integration')
            stock = Path(stock).resolve()
            qemu = str(Path(qemu).resolve())
            for binary in (Path(qemu), stock/'bin/busybox', stock/'usr/bin/jshn', stock/'usr/bin/jsonfilter'):
                self.assertTrue(binary.is_file(), f'Missing native integration binary: {binary}')
            prefix = [qemu, '-L', str(stock)]
            reader = '#!/bin/sh\nexec ' + shlex.join(prefix + [str(stock/'usr/bin/jshn')]) + ' "$@"\n'
            filter_program = '#!/bin/sh\nexec ' + shlex.join(prefix + [str(stock/'usr/bin/jsonfilter')]) + ' "$@"\n'
            self.shell = prefix + [str(stock/'bin/busybox'), 'ash']
        (self.path / 'jshn').write_text(reader)
        (self.path / 'jshn').chmod(0o755)
        (self.path / 'jsonfilter').write_text(filter_program)
        (self.path / 'jsonfilter').chmod(0o755)
        (self.path / 'calls').write_text('')
        (self.path / 'clients.json').write_text(json.dumps({'clients': {MAC: {'assoc': True}}}))
        (self.path / 'assoclist').write_text(f'{MAC.upper()}  -45 dBm / -90 dBm (SNR 45)  0 ms ago\n')
        self.wireless = {'radio0': {'config': {'type': 'mac80211'}, 'interfaces': [
            {'section': 'guest', 'ifname': 'wlan0', 'config': {'mode': 'ap'}}]}}

    def run_adapter(self, command='station_clients_json'):
        (self.path / 'wireless.json').write_text(json.dumps(self.wireless))
        harness = self.path / 'run.sh'
        harness.write_text('. "$STOCK_JSHN"\n' + ADAPTER + '\n' + SHIM + '\n' + command + '\n')
        env = dict(os.environ, FIX=str(self.path), STOCK_JSHN=str(JSHN),
                   PATH=str(self.path) + os.pathsep + os.environ['PATH'])
        result = subprocess.run(self.shell + [str(harness)], env=env, capture_output=True,
                                text=True, check=True, timeout=10)
        self.assertNotIn('Unexpected mutation', result.stderr)
        return result

    def discovery(self):
        return json.loads(self.run_adapter().stdout)

    def test_fixture_preserves_original_stock_library(self):
        body = JSHN.read_bytes().split(b'# functions for parsing and generating json', 1)[1]
        original = b'# functions for parsing and generating json' + body
        self.assertEqual(hashlib.sha256(original).hexdigest(),
                         'c5056cf87f47d12625a5695cfc1f5b48df4807b9095665dd9c682ca977d1eb59')

    def test_actual_library_returns_sanitized_mac_object_keys(self):
        result = self.run_adapter('json_load "$(cat "$FIX/clients.json")"; '
                                  'json_select clients; json_get_keys keys; printf "%s\\n" "$keys"')
        self.assertEqual(result.stdout.strip(), 'aa_00_00_00_00_01')

    def test_decoder_discards_original_mac_key_before_shell_library(self):
        result = self.run_adapter('jshn -r "$(cat "$FIX/clients.json")"')
        # Captured command stream from the actual GL4.8.3 C executable.
        self.assertEqual(result.stdout, "json_init;\njson_add_object 'clients';\n"
                         "json_add_object 'aa_00_00_00_00_01';\njson_add_boolean 'assoc' 1;\n"
                         'json_close_object;\njson_close_object;\n')
        result = self.run_adapter('json_load "$(cat "$FIX/clients.json")"; json_select clients; '
                                  '_json_get_var name "N_${JSON_CUR}_aa_00_00_00_00_01"; '
                                  'printf "<%s>\\n" "$name"')
        self.assertEqual(result.stdout.strip(), '<>')

    def test_hostapd_clients_decode_sanitized_keys(self):
        self.assertEqual(self.discovery(), {'clients': [
            {'mac': MAC, 'ifname': 'wlan0', 'section': 'guest'}], 'unavailable': False})

    def test_uppercase_hostapd_mac_and_malformed_keys(self):
        keys = {MAC.upper(): {}, 'aa:00:00:00:00:zz': {}, 'aa_00_00_00_00_001': {},
                'aa:00:00:00:00:02;touch': {}, 'not-a-mac': {}}
        (self.path / 'clients.json').write_text(json.dumps({'clients': keys}))
        self.assertEqual(self.discovery()['clients'], [
            {'mac': MAC, 'ifname': 'wlan0', 'section': 'guest'}])
        self.assertNotIn('touch', (self.path / 'calls').read_text())

    def test_invalid_original_separators_are_not_decoded_as_macs(self):
        for separator in ('-', '/', '_', '.'):
            with self.subTest(separator=separator):
                key = MAC.replace(':', separator)
                (self.path / 'clients.json').write_text(json.dumps({'clients': {key: {}}}))
                self.assertEqual(self.discovery(), {'clients': [], 'unavailable': False})

    def test_original_mac_lookup_preserves_current_namespace(self):
        result = self.run_adapter('json_set_namespace discovery_fixture; station_clients_json')
        self.assertEqual(json.loads(result.stdout), {'clients': [
            {'mac': MAC, 'ifname': 'wlan0', 'section': 'guest'}], 'unavailable': False})

    def test_missing_radio_config_uses_uci_without_stdout_pollution(self):
        self.wireless = {'mtk0': {'interfaces': [
            {'section': 'guest', 'ifname': 'ra0', 'config': {'mode': 'ap'}}]}}
        self.assertEqual(self.discovery(), {'clients': [
            {'mac': MAC, 'ifname': 'ra0', 'section': 'guest'}], 'unavailable': False})
        self.assertIn('-q get wireless.mtk0.type', (self.path / 'calls').read_text())

    def test_missing_interfaces_on_another_radio_does_not_pollute_output(self):
        self.wireless['down_radio'] = {'config': {'type': 'mac80211'}}
        self.assertEqual(self.discovery(), {'clients': [
            {'mac': MAC, 'ifname': 'wlan0', 'section': 'guest'}], 'unavailable': False})

    def test_missing_ap_config_does_not_pollute_other_client_rows(self):
        self.wireless['radio0']['interfaces'].append({'section': 'incomplete', 'ifname': 'wlan1'})
        self.assertEqual(self.discovery()['clients'], [
            {'mac': MAC, 'ifname': 'wlan0', 'section': 'guest'}])

    def test_warning_suppression_is_local_to_ap_enumeration(self):
        result = self.run_adapter('station_ap_records; json_init; json_select absent || true')
        self.assertIn("WARNING: Variable 'absent' does not exist", result.stdout)


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""Run client RPC through real discovery and the stock GL jshn shell library.

The default mode substitutes only the architecture-specific C JSON programs.
Set WCL_STOCK_ROOT and WCL_STOCK_QEMU to also use actual firmware ash, applets,
jshn and jsonfilter. All network/policy APIs remain isolated readonly fixtures.
"""
import json
import os
from pathlib import Path
import runpy
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent
JSHN = ROOT / 'tests/fixtures/libubox-20210516-jshn.sh'
ADAPTER = ROOT / 'files/usr/share/wifi-client-limiter/station-adapter.sh'
RPC = ROOT / 'files/usr/libexec/rpcd/wifi-client-limiter'
GUEST = '02:ab:cd:00:00:01'
OPERATOR = '02:ab:cd:00:00:02'
JSON_READER = runpy.run_path(str(ROOT / 'test-jshn-discovery.py'))['JSON_READER']

# Complement the existing C decoder fixture with C jshn's environment writer.
# json_select itself is always the stock shell implementation, including its
# stdout warnings. Replacing that function would hide the regression.
JSON_WRITER = r'''
import json, os, sys
if sys.argv[1:] != ['-w']:
    sys.exit(2)
env = os.environ
def table(name, is_array=False):
    result = [] if is_array else {}
    for key in env.get('K_' + name, '').split():
        item = name + '_' + key
        kind = env['T_' + item]
        value = env.get(item, '')
        if kind in ('object', 'array'):
            value = table(value, kind == 'array')
        elif kind == 'boolean': value = value == '1'
        elif kind in ('int', 'double'): value = json.loads(value)
        elif kind == 'null': value = None
        elif kind != 'string': raise ValueError(kind)
        if is_array: result.append(value)
        else: result[env.get('N_' + item, key)] = value
    return result
print(json.dumps(table('J_V'), ensure_ascii=False))
'''

JSON_FILTER = r'''
import json, re, sys
args = sys.argv[1:]
if len(args) != 4 or args[0] != '-s' or args[2] != '-t': sys.exit(2)
match = re.fullmatch(r"@\['((?:[a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2})'\]", args[3])
if not match: sys.exit(2)
try: value = json.loads(args[1])[match[1]]
except (ValueError, KeyError, TypeError): sys.exit(1)
print('object' if isinstance(value, dict) else 'array' if isinstance(value, list)
      else 'string' if isinstance(value, str) else 'null' if value is None
      else 'boolean' if isinstance(value, bool) else 'int')
'''

API = r'''
import json, os, pathlib, sys
p = pathlib.Path(os.environ['FIX'])
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with (p / 'calls').open('a') as out: out.write(json.dumps([name, *args]) + '\n')
if name == 'ubus' and args == ['call', 'network.wireless', 'status']:
    sys.stdout.write((p / 'wireless.json').read_text())
elif name == 'ubus' and args == ['call', 'luci-rpc', 'getHostHints']:
    if (p / 'hints-fail').exists(): sys.exit(1)
    sys.stdout.write((p / 'hints.json').read_text())
elif name == 'iwinfo' and len(args) == 2 and args[0] in ('ra0', 'rax0') and args[1] == 'assoclist':
    sys.stdout.write((p / args[0]).read_text())
else:
    print('Unexpected API command: ' + repr([name, *args]), file=sys.stderr)
    sys.exit(94)
'''


class RpcClientDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='wifi-limiter-rpc-discovery-')
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.bin = self.path / 'bin'
        self.bin.mkdir()
        self.shell = [shutil.which('bash')]
        stock, qemu = os.environ.get('WCL_STOCK_ROOT'), os.environ.get('WCL_STOCK_QEMU')
        self.native = bool(stock or qemu)
        if self.native:
            self.assertTrue(stock and qemu, 'Set both WCL_STOCK_ROOT and WCL_STOCK_QEMU')
            stock, qemu = Path(stock).resolve(), Path(qemu).resolve()
            for binary in (qemu, stock/'bin/busybox', stock/'usr/bin/jshn', stock/'usr/bin/jsonfilter'):
                self.assertTrue(binary.is_file(), str(binary))
            prefix = [str(qemu), '-L', str(stock)]
            self.shell = prefix + [str(stock/'bin/busybox'), 'ash']
            for name in ('awk', 'grep', 'sort', 'tr', 'head', 'sed', 'cat'):
                self.executable(name, '#!/bin/sh\nexec ' + shlex.join(
                    prefix + [str(stock/'bin/busybox'), name]) + ' "$@"\n')
            for name in ('jshn', 'jsonfilter'):
                self.executable(name, '#!/bin/sh\nexec ' + shlex.join(
                    prefix + [str(stock/'usr/bin'/name)]) + ' "$@"\n')
        else:
            for name in ('awk', 'grep', 'sort', 'tr', 'head', 'sed', 'cat'):
                (self.bin/name).symlink_to(shutil.which(name))
            (self.path/'json-reader.py').write_text(JSON_READER)
            (self.path/'json-writer.py').write_text(JSON_WRITER)
            self.executable('jshn', '#!/bin/sh\ncase "$1" in -w) program=json-writer.py;; '
                            '*) program=json-reader.py;; esac\nexec ' + shlex.quote(sys.executable)
                            + ' "$FIX/$program" "$@"\n')
            self.executable('jsonfilter', '#!' + sys.executable + '\n' + JSON_FILTER)
        # No fallback PATH: a regression cannot invoke the host firewall/UCI.
        for name in ('ubus', 'iwinfo', 'uci', 'nft', 'fw3', 'fw4', 'iwpriv'):
            self.executable(name, '#!' + sys.executable + '\n' + API)
        (self.path/'calls').write_text('')
        self.policy = b'blocked\nactive=1\nverified=ra0:wifi2g rax0:wifi5g\n'
        (self.path/'active-policy').write_bytes(self.policy)
        self.wireless = {
            'mtk0': {'config': {'type': 'mtk'}, 'interfaces': [
                {'section': 'wifi2g', 'ifname': 'ra0', 'config': {'mode': 'ap'}}]},
            'mtk1': {'config': {'type': 'mtk'}, 'interfaces': [
                {'section': 'wifi5g', 'ifname': 'rax0', 'config': {'mode': 'ap'}}]},
        }
        (self.path/'wireless.json').write_text(json.dumps(self.wireless))
        self.set_clients([GUEST, OPERATOR])
        self.set_hints({GUEST.upper(): {'name': 'Guest phone'}, OPERATOR.upper(): {'name': 'Operator'}})
        helper = self.path/'helper/station-adapter.sh'
        helper.parent.mkdir()
        helper.write_text(ADAPTER.read_text().replace('/usr/share/libubox/jshn.sh', str(JSHN)))
        self.executable('station-adapter.sh', '#!/bin/sh\nexec '
                        + shlex.join(self.shell + [str(helper)]) + ' "$@"\n')
        source = RPC.read_text().split('case "${1:-}" in\n list)')[0]
        source = source.replace('/usr/share/wifi-client-limiter/station-adapter.sh',
                                str(self.bin/'station-adapter.sh'))
        # Policy verification has its own tests. Feed its verified result into
        # the actual client response assembler without any policy mutations.
        self.script = self.path/'rpc.sh'
        self.script.write_text('. ' + shlex.quote(str(JSHN)) + '\n' + source + f'''
enabled=1; dry=0; guest_internet=0; networks=' wifi2g wifi5g'; white=' {OPERATOR}'
state=blocked; active=1; backend=fw3; family=netdev; reason=policy_verified; error=
service_running=1; service_state=running; service_error=; service_detail=
verified_bindings=' ra0:wifi2g rax0:wifi5g'
rpc_clients
''')

    def executable(self, name, contents):
        path = self.bin/name
        path.write_text(contents)
        path.chmod(0o755)

    def set_clients(self, clients):
        self.clients = clients
        for index, iface in enumerate(('ra0', 'rax0')):
            macs = clients[index::2]
            (self.path/iface).write_text(''.join(
                mac.upper() + '  -45 dBm / -90 dBm (SNR 45)  0 ms ago\n' for mac in macs)
                or 'No station connected\n')

    def set_hints(self, hints):
        (self.path/'hints.json').write_text(json.dumps(hints))

    def response(self, expected=None):
        started = time.monotonic()
        result = subprocess.run(self.shell + [str(self.script)], env=dict(
            os.environ, PATH=str(self.bin), FIX=str(self.path)),
            capture_output=True, text=True, timeout=20)
        self.elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('WARNING:', result.stdout)
        self.assertNotIn('Unexpected API command', result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['internet_state'], 'blocked')
        self.assertTrue(data['active'])
        self.assertEqual(data['reason'], 'policy_verified')
        self.assertEqual((self.path/'active-policy').read_bytes(), self.policy)
        rows = {row['mac']: row for row in data['clients']}
        self.assertEqual(set(rows), set(self.clients if expected is None else expected))
        for mac, row in rows.items():
            self.assertEqual(row['internet_permission'], 'exempt' if mac == OPERATOR else 'blocked')
            self.assertEqual(row['protected'], mac == OPERATOR)
            self.assertTrue(row['managed'])
            self.assertIsInstance(row['hostname'], str)
        return data, rows

    def test_uppercase_hints_preserve_both_clients_and_outer_response(self):
        data, rows = self.response()
        self.assertFalse(data['unavailable'])
        self.assertEqual(rows[GUEST]['hostname'], 'Guest phone')
        self.assertEqual(rows[OPERATOR]['hostname'], 'Operator')

    def test_mixed_case_names_and_escaped_hostname_fallback(self):
        name = 'Phone "Wi-Fi"\nSecond line <tag> \\ room'
        self.set_hints({GUEST.replace('ab:cd', 'aB:Cd'): {'name': [], 'hostname': name},
                        OPERATOR: {'name': '', 'hostname': 'operator fallback'}})
        _, rows = self.response()
        self.assertEqual(rows[GUEST]['hostname'], name)
        self.assertEqual(rows[OPERATOR]['hostname'], 'operator fallback')

    def test_missing_optional_hints_never_drop_clients(self):
        for hints in ({}, {OPERATOR: {'name': 'Operator'}}, {GUEST: {}}, []):
            with self.subTest(hints=hints):
                self.set_hints(hints)
                data, rows = self.response()
                self.assertFalse(data['unavailable'])
                self.assertEqual(rows[GUEST]['hostname'], '')

    def test_failed_empty_and_malformed_host_hints_are_optional(self):
        for raw in ('', '{bad json', 'null', 'false', '"text"'):
            with self.subTest(raw=raw):
                (self.path/'hints.json').write_text(raw)
                data, rows = self.response()
                self.assertFalse(data['unavailable'])
                self.assertTrue(all(row['hostname'] == '' for row in rows.values()))
        (self.path/'hints-fail').touch()
        self.set_hints({GUEST: {'name': 'must not be read'}})
        _, rows = self.response()
        self.assertEqual(rows[GUEST]['hostname'], '')

    def test_wrong_hint_and_name_types_cannot_become_display_names(self):
        for value in (None, False, 42, 'text', [], {}, {'name': {'a': 'b'}},
                      {'name': None, 'hostname': []}, {'name': True, 'hostname': 42}):
            with self.subTest(value=value):
                self.set_hints({GUEST.upper(): value})
                _, rows = self.response()
                self.assertEqual(rows[GUEST]['hostname'], '')

    def test_non_mac_separators_cannot_impersonate_a_host_hint(self):
        for separator in ('_', '-', '/', '.'):
            with self.subTest(separator=separator):
                self.set_hints({GUEST.upper().replace(':', separator): {'name': 'wrong device'}})
                _, rows = self.response()
                self.assertEqual(rows[GUEST]['hostname'], '')

    def test_missing_clients_array_reports_unavailable_with_clean_json(self):
        self.executable('station-adapter.sh', '#!/bin/sh\nprintf \'%s\\n\' \'{}\'\n')
        data, _ = self.response(expected=[])
        self.assertTrue(data['unavailable'])

    def test_native_many_clients_finish_within_rpc_timeout(self):
        if not self.native:
            self.skipTest('Native firmware performance check requires WCL_STOCK_ROOT/QEMU')
        for count in (25, 50):
            with self.subTest(count=count):
                macs = [f'02:ab:cd:00:00:{index:02x}' for index in range(1, count + 1)]
                self.set_clients(macs)
                self.set_hints({mac.upper(): {'name': f'Device {index}'}
                                for index, mac in enumerate(macs)})
                _, rows = self.response()
                self.assertEqual(len(rows), count)
                self.assertTrue(all(row['hostname'] for row in rows.values()))
                print(f'Native RPC: {count} clients, {self.elapsed:.3f}s', file=sys.stderr)


if __name__ == '__main__':
    unittest.main()

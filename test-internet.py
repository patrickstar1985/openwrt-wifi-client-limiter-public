#!/usr/bin/env python3
"""Exercise the production Internet policy with isolated OpenWrt API fixtures.

The helper is sourced unchanged. Only UCI, ubus, fw3/fw4, iptables inspection, nft and JSHN are replaced.
This suite never accesses the host firewall, UCI, or network devices.
"""
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
HELPER = ROOT / 'files/usr/share/wifi-client-limiter/internet-policy.sh'
CHECKSUM_HELPER = ROOT / 'files/usr/share/wifi-client-limiter/posix-cksum.sh'
PROTECTED = '02:00:00:00:00:11'

SHIM = r'''
json_load() { printf '%s' "$1" > "$FIX/json"; jp=''; json_query exists; }
json_query() { python3 "$FIX/json-query.py" "$FIX/json" "$jp" "$1"; }
json_select() {
 local previous="$jp"
 if [ "$1" = .. ]; then jp="${jp%/*}"; else jp="$jp/$1"; fi
 json_query exists || { jp="$previous"; return 1; }
}
json_get_keys() { eval "$1=\"\$(json_query keys)\""; }
json_get_var() { eval "$1=\"\$(json_query \"$2\")\""; }
json_get_type() { eval "$1=\"\$(json_query \"type:$2\")\""; }
config_load() { :; }
config_get_bool() { config_get "$@"; }
config_list_foreach() {
 local item values
 values=; case "$2" in network) values="$NETWORKS";; whitelist) values="$WHITE";; esac
 for item in $values; do "$3" "$item"; done
}
config_get() { local value; value="$(python3 "$FIX/config-get.py" "$FIX/config.json" "$2" "$3")" || return; eval "$1=\$value"; }
valid_mac() { printf '%s\n' "$1" | LC_ALL=C grep -Eq '^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$'; }
'''

JSON_QUERY = r'''
import json, sys
try:
    value = json.load(open(sys.argv[1]))
    for key in sys.argv[2].split('/'):
        if key:
            # JSHN uses one-based numeric keys for arrays.
            value = value[int(key)-1] if isinstance(value, list) else value[key]
    op = sys.argv[3]
    if op == 'keys':
        print(' '.join(map(str, range(1, len(value)+1) if isinstance(value, list) else value.keys())))
    elif op.startswith('type:'):
        result = value.get(op[5:], None) if isinstance(value, dict) else None
        print('array' if isinstance(result,list) else 'object' if isinstance(result,dict) else 'string' if isinstance(result,str)
              else 'null' if op[5:] in value and result is None else 'boolean' if isinstance(result,bool)
              else 'int' if isinstance(result,int) else '')
    elif op != 'exists':
        result = value.get(op, '') if isinstance(value, dict) else value[int(op)-1] if isinstance(value,list) and op.isdigit() else ''
        if isinstance(result, bool): result = int(result)
        print(' '.join(map(str, result)) if isinstance(result, list) else result)
except (KeyError, IndexError, TypeError, ValueError):
    sys.exit(1)
'''

MOCK_API = r'''
#!/usr/bin/env python3
import json, os, pathlib, re, shutil, sys
p = pathlib.Path(os.environ['FIX'])
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with (p/'calls').open('a') as out:
    out.write(json.dumps([name, *args])+'\n')
settings = json.loads((p/'failures.json').read_text())
def fail(key):
    count = settings.get(key, 0)
    if count:
        settings[key] = count-1
        (p/'failures.json').write_text(json.dumps(settings))
        return True
    return False
if name == 'jsonfilter':
    # Only the readonly exact raw-MAC type lookup used for optional host hints.
    if len(args) != 4 or args[0] != '-s' or args[2] != '-t': sys.exit(2)
    match = re.fullmatch(r"@\['((?:[a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2})'\]", args[3])
    if not match: sys.exit(2)
    try:
        value = json.loads(args[1])[match[1]]
    except (ValueError, KeyError, TypeError): sys.exit(1)
    print('object' if isinstance(value, dict) else 'array' if isinstance(value, list)
          else 'string' if isinstance(value, str) else 'null' if value is None
          else 'boolean' if isinstance(value, bool) else 'int')
elif name == 'ubus':
    if args[:3] == ['call', 'network.wireless', 'status']:
        if fail('wireless'): sys.exit(1)
        sys.stdout.write((p/'wireless.json').read_text())
    elif len(args) >= 3 and args[0] == 'call' and args[1].startswith('network.interface.') and args[2] == 'status':
        network = args[1][len('network.interface.'):]
        if fail('network') or not (p/(network+'.json')).exists(): sys.exit(1)
        sys.stdout.write((p/(network+'.json')).read_text())
    elif args[:3] == ['call', 'luci-rpc', 'getHostHints']:
        print((p/'host-hints.json').read_text() if (p/'host-hints.json').exists() else '{}')
    else:
        sys.exit(1)
elif name == 'nft':
    stateless = '-s' in args
    args = [arg for arg in args if arg not in ('-s', '-nn', '-y')]
    table = p/'nft-table'
    if len(args) >= 4 and args[:2] == ['list', 'table'] and args[2] in ('inet','netdev'):
        if args[2] == 'inet' and args[3] == 'fw4': sys.exit(1 if settings.get('fw4_missing') else 0)
        if (p/('nft-old-'+args[2])).exists(): sys.exit(0)
        if not table.exists(): sys.exit(1)
        text = table.read_text()
        if stateless: print(text)
        for family in ('inet','netdev'):
            if 'add table '+family+' wifi_client_limiter' in text:
                sys.exit(0 if args[2] == family else 1)
        sys.exit(0)
    if args[:3] == ['list', 'flowtable', 'inet']:
        sys.exit(0 if settings.get('flowtable') else 1)
    if args[:2] == ['-c', '-f']:
        sys.exit(1 if fail('validation') else 0)
    if args and args[0] == '-f':
        if fail('apply'): sys.exit(1)
        new_text = pathlib.Path(args[1]).read_text()
        if table.exists():
            old_text = table.read_text()
            for family in ('inet','netdev'):
                marker = 'add table '+family+' wifi_client_limiter'
                if marker in old_text and marker not in new_text:
                    (p/('nft-old-'+family)).write_text(old_text)
        shutil.copyfile(args[1], table)
    elif len(args) >= 4 and args[:2] == ['delete', 'table'] and args[2] in ('inet','netdev'):
        if fail('delete'): sys.exit(1)
        old = p/('nft-old-'+args[2])
        if old.exists(): old.unlink()
        elif table.exists(): table.unlink()
        else: sys.exit(1)
    else: sys.exit(2)
elif name == 'fw4':
    committed = json.loads((p/'uci.json').read_text())['committed']
    enabled = any(key.endswith('.flow_offloading') and value in ('1','true','yes','on')
                  for key, value in committed.items())
    if args == ['print']:
        if fail('print'): sys.exit(1)
        print(' flowtable ft {' if enabled else 'table inet fw4 {')
    else:
        if fail('reload'): sys.exit(1)
        if not settings.get('retain_flowtable'): settings['flowtable'] = int(enabled)
        (p/'failures.json').write_text(json.dumps(settings))
elif name == 'fw3':
    committed = json.loads((p/'uci.json').read_text())['committed']
    enabled = any(key.endswith('.flow_offloading') and value in ('1','true','yes','on')
                  for key, value in committed.items())
    hardware = any(key.endswith('.flow_offloading_hw') and value in ('1','true','yes','on')
                   for key, value in committed.items())
    if 'print' in args:
        if fail('print'): sys.exit(1)
        print('*filter\n:FORWARD ACCEPT [0:0]')
        if enabled: print('-A FORWARD -j FLOWOFFLOAD'+(' --hw' if hardware else ''))
        print('COMMIT')
    else:
        if fail('reload'): sys.exit(1)
        for family in ('4','6'):
            if not settings.get('retain_flowoffload'+family):
                settings['flowoffload'+family] = int(enabled)
                settings['flowoffload_hw'+family] = int(hardware)
        (p/'failures.json').write_text(json.dumps(settings))
        if enabled: (pathlib.Path(os.environ['INTERNET_MODULE_ROOT'])/'xt_FLOWOFFLOAD').mkdir(exist_ok=True)
elif name in ('iptables-save','ip6tables-save','iptables','ip6tables'):
    family = '6' if name.startswith('ip6') else '4'
    if fail('inspect'+family): sys.exit(1)
    if settings.get('empty_dump'+family): sys.exit(0)
    if settings.get('incomplete_dump'+family): print('*filter\nCOMMIT'); sys.exit(0)
    print('*filter\n:FORWARD ACCEPT [0:0]')
    if settings.get('flowoffload'+family):
        print('-A FORWARD -j FLOWOFFLOAD'+(' --hw' if settings.get('flowoffload_hw'+family) else ''))
    print('COMMIT')
elif name in ('rmmod','modprobe','insmod'):
    if args != ['xt_FLOWOFFLOAD']: sys.exit(2)
    root = pathlib.Path(os.environ['INTERNET_MODULE_ROOT'])
    module = root/'xt_FLOWOFFLOAD'
    if name == 'rmmod':
        if fail('module_unload'): sys.exit(1)
        if settings.get('retain_module'): sys.exit(0)
        shutil.rmtree(module, ignore_errors=True)
    else:
        if fail('module_load'): sys.exit(1)
        module.mkdir(exist_ok=True)
elif name == 'uci':
    args = [arg for arg in args if arg not in ('-q', '-X')]
    op = args[0]
    store = json.loads((p/'uci.json').read_text())
    committed = store['committed']
    pending = store['pending']
    if op == 'show':
        for key, value in committed.items(): print(key+'='+str(value))
    elif op == 'changes':
        for key, value in pending.items(): print(key+'='+str(value))
    elif op == 'get':
        key = args[1]
        value = pending.get(key, committed.get(key))
        if value is None: sys.exit(1)
        print(value)
    elif op in ('set', 'delete'):
        if fail('uci_set'): sys.exit(1)
        if op == 'set': key, value = args[1].split('=', 1)
        else: key, value = args[1], None
        pending[key] = value
    elif op == 'revert':
        pending.pop(args[1], None)
    elif op == 'commit':
        if fail('commit'): sys.exit(1)
        for key, value in pending.items():
            if value is None: committed.pop(key, None)
            else: committed[key] = value
        store['pending'] = {}
        if settings.get('rename_defaults'):
            number = settings.get('commit_number', 0)+1
            settings['commit_number'] = number
            (p/'failures.json').write_text(json.dumps(settings))
            for section in [key for key,value in committed.items() if value=='defaults']:
                replacement = 'firewall.cfg'+str(number).zfill(6)
                for key in list(committed):
                    if key == section or key.startswith(section+'.'):
                        committed[replacement+key[len(section):]] = committed.pop(key)
    else: sys.exit(2)
    (p/'uci.json').write_text(json.dumps(store))
'''


class InternetHarness:
    def __init__(self, path):
        self.path = Path(path)
        (self.path/'state').mkdir()
        (self.path/'bin').mkdir()
        system = self.path/'system-bin'
        system.mkdir()
        # Keep missing OpenWrt tools genuinely missing even when the host has
        # iptables installed. No test may fall through to the host firewall.
        for name in ('sh','python3','mkdir','rmdir','rm','mv','cp','sort','wc','awk','sed',
                     'cksum','grep','tr','chmod','cmp','logger','sleep','cat','head','tail','cut'):
            target = shutil.which(name)
            if target: (system/name).symlink_to(target)
        self.command_path = str(self.path/'bin')+os.pathsep+str(system)
        (self.path/'sys-module').mkdir()
        (self.path/'json-query.py').write_text(JSON_QUERY)
        (self.path/'config-get.py').write_text(
            "import json,sys\nprint(json.load(open(sys.argv[1])).get(sys.argv[2],{}).get(sys.argv[3],''))\n")
        for name in ('ubus', 'nft', 'fw4', 'fw3', 'iptables-save', 'ip6tables-save', 'iptables', 'ip6tables', 'rmmod', 'modprobe', 'insmod', 'uci', 'jsonfilter'):
            api = self.path/'bin'/name
            api.write_text(MOCK_API.lstrip())
            api.chmod(0o755)
        # Vendor control adapters have a separate production test suite.
        (self.path/'vendor-noop.sh').write_text('internet_vendor_disable() { :; }\n'
                                               'internet_vendor_inactive() { :; }\n'
                                               'internet_vendor_restore() { :; }\n')
        self.harness = self.path/'run.sh'
        self.harness.write_text('''INTERNET_STATE="$FIX/state"
INTERNET_BACKUP_DIR="$FIX/backup"
. "$HELPER"
''' + SHIM + '''
enabled="$ENABLED"; dry="$DRY"; guest_internet="$INTERNET"; networks="$NETWORKS"; white="$WHITE"
case "$ACTION" in cleanup) internet_cleanup;; refresh) internet_firewall_refresh;; *) internet_policy;; esac
''')
        self.write('wireless.json', {'radio0': {'interfaces': [
            {'section': 'guest', 'ifname': 'wlan-guest', 'config': {'mode': 'ap', 'network':['lan']}},
            {'section': 'guest2', 'ifname': 'wlan-extra', 'config': {'mode': 'ap', 'network':['iot']}},
            {'section': 'admin', 'ifname': 'wlan-admin', 'config': {'mode': 'ap', 'network':['lan']}},
            {'section': 'station', 'ifname': 'wlan-station', 'config': {'mode': 'sta'}},
            {'section': 'broken', 'ifname': 'bad;touch owned', 'config': {'mode': 'ap', 'network':['lan']}},
        ]}})
        self.write('config.json', {'guest': {'network': 'lan'}, 'guest2': {'network': 'iot'}, 'admin': {'network': 'lan'},
                                  'main': {'enabled': '1', 'dry_run': '0', 'guest_internet': '0'}})
        self.write('lan.json', {
            'ipv4-address': [{'address': '192.168.50.1', 'mask': 24}],
            'ipv6-address': [{'address': 'fd42:50::1234', 'mask': 64}],
            'ipv6-prefix-assignment': [{
                'address': '2001:db8::', 'mask': 48,
                'local-address': {'address': '2001:db8:50::1234', 'mask': 64},
            }],
            'route': [{'target': '0.0.0.0', 'mask': 0}, {'target': '::', 'mask': 0},
                      {'target': 'fd99::', 'mask': 16}],
        })
        self.write('iot.json', {'ipv4-address': [{'address': '10.42.0.1', 'mask': 24}]})
        self.write('failures.json', {})
        self.write('uci.json', {'committed': {
            'firewall.cfgdefaults': 'defaults',
            'firewall.cfgdefaults.flow_offloading': '1',
            'firewall.cfgdefaults.flow_offloading_hw': '1',
            'firewall.keep.name': 'untouched',
        }, 'pending': {}})
        (self.path/'calls').write_text('')

    def use_fw3(self):
        (self.path/'bin/fw4').unlink(missing_ok=True)
        settings = self.read('failures.json')
        settings['fw4_missing'] = 1
        self.write('failures.json', settings)

    def write(self, name, value):
        (self.path/name).write_text(json.dumps(value))

    def read(self, name):
        return json.loads((self.path/name).read_text())

    def run(self, *, enabled=1, internet=0, dry=0, networks='guest', white=PROTECTED,
            action='policy', expect_ok=True):
        (self.path/'calls').write_text('')
        env = dict(os.environ, FIX=str(self.path), HELPER=str(HELPER),
                   INTERNET_CHECKSUM_HELPER=str(CHECKSUM_HELPER),
                   INTERNET_VENDOR_HELPER=str(self.path/'vendor-noop.sh'),
                   INTERNET_MODULE_ROOT=str(self.path/'sys-module'),
                   PATH=self.command_path,
                   ENABLED=str(enabled), INTERNET=str(internet), DRY=str(dry),
                   NETWORKS=networks, WHITE=white, ACTION=action)
        result = subprocess.run(['sh', str(self.harness)], env=env, capture_output=True,
                                text=True, timeout=30)
        if expect_ok and result.returncode:
            raise AssertionError(f'helper failed: {result.returncode}\n{result.stdout}\n{result.stderr}')
        if not expect_ok and not result.returncode:
            raise AssertionError('helper unexpectedly succeeded')
        return [json.loads(line) for line in (self.path/'calls').read_text().splitlines()]

    @property
    def status(self):
        return self.read('state/internet-status.json')

    @property
    def rules(self):
        return (self.path/'nft-table').read_text()

    @staticmethod
    def mutations(calls):
        return [call for call in calls if
                (call[0] == 'nft' and call[1] in ('-f', 'delete')) or
                (call[0] == 'uci' and any(op in call[1:] for op in ('set', 'delete', 'commit'))) or
                (call[0] in ('fw4','fw3') and 'print' not in call[1:]) or
                call[0] in ('rmmod','modprobe','insmod')]


class InternetPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='wifi-limiter-internet-')
        self.addCleanup(temporary.cleanup)
        self.h = InternetHarness(temporary.name)

    def test_allowed_disabled_and_empty_selection_do_not_create_rules(self):
        for settings, expected in (({'internet': 1}, 'allowed'), ({'enabled': 0}, 'inactive'),
                                   ({'networks': ''}, 'inactive')):
            with self.subTest(settings=settings):
                self.assertFalse(self.h.mutations(self.h.run(**settings)))
                self.assertEqual(self.h.status['internet_state'], expected)
                self.assertFalse(self.h.status['active'])
                self.assertFalse((self.h.path/'nft-table').exists())

    def test_fresh_simulation_changes_neither_firewall_nor_uci(self):
        original = self.h.read('uci.json')
        self.assertFalse(self.h.mutations(self.h.run(dry=1)))
        self.assertEqual(self.h.read('uci.json'), original)
        self.assertEqual(self.h.status['internet_state'], 'test')
        self.assertFalse((self.h.path/'backup').exists())

    def test_rules_use_selected_physical_aps_and_whitelisted_macs(self):
        self.h.run(networks='guest guest2 admin station broken', white=PROTECTED+' invalid')
        self.assertEqual(self.h.status['interfaces'], ['wlan-admin', 'wlan-extra', 'wlan-guest'])
        for iface in ('wlan-admin', 'wlan-extra', 'wlan-guest'):
            self.assertIn(f'hook ingress device "{iface}" priority -500', self.h.rules)
        self.assertNotIn('wlan-station', self.h.rules)
        self.assertNotIn('bad;touch', self.h.rules)
        self.assertNotIn('invalid', self.h.rules)
        self.assertEqual(self.h.rules.count('ether saddr '+PROTECTED+' return'), 3)
        self.assertNotIn('hostapd', str(self.h.read('config.json')))
        self.assertFalse((self.h.path/'owned').exists())

    def test_modern_netdev_renderer_blocks_single_and_double_tags_after_mac_exemption(self):
        self.h.run()
        rules = self.h.rules
        self.assertEqual(self.h.status['firewall_backend'], 'fw4')
        self.assertEqual(self.h.status['nft_family'], 'netdev')
        vlan = next(line for line in rules.splitlines() if 'ether type' in line and '0x8100' in line)
        self.assertIn('0x88a8', vlan)
        self.assertIn('0x9100', vlan)
        self.assertLess(rules.index('ether saddr '+PROTECTED), rules.index(vlan.strip()))
        self.assertLess(rules.index(vlan.strip()), rules.index('ip daddr 192.168.50.0/24'))

    def test_only_connected_prefixes_and_exact_delegated_local_subnet_are_exempt(self):
        self.h.run(networks='guest guest2')
        rules = self.h.rules
        for prefix in ('192.168.50.0/24', '10.42.0.0/24', 'fd42:50::1234/64', '2001:db8:50::1234/64'):
            self.assertIn(prefix+' return', rules)
        for remote in ('0.0.0.0/0', '::/0', 'fd99::/16', '2001:db8::/48', 'fc00::/7'):
            self.assertNotIn(remote+' return', rules)
        self.assertIn('ether type { 0x0800, 0x86dd } counter drop', rules)
        self.assertIn('255.255.255.255 udp sport 68 udp dport 67 return', rules)
        self.assertIn('224.0.0.0/24 return', rules)
        self.assertIn('fe80::/10, ff01::/16, ff02::/16', rules)
        # ARP is outside the explicit IP EtherType drops and passes on the bridge.
        self.assertNotIn('ether type arp drop', rules)

    def test_empty_optional_ipv6_local_address_preserves_ipv4_policy(self):
        self.h.use_fw3()
        for assignment in ({'address': 'fd42:8::', 'mask': 60, 'local-address': {}},
                           {'address': 'fd42:8::', 'mask': 60}):
            with self.subTest(assignment=assignment):
                self.h.write('lan.json', {
                    'ipv4-address': [{'address': '192.168.8.1', 'mask': 24}],
                    'ipv6-address': [], 'ipv6-prefix-assignment': [assignment],
                })
                self.h.run()
                self.assertEqual(self.h.status['internet_state'], 'blocked')
                self.assertIn('ip daddr 192.168.8.0/24 return', self.h.rules)
                self.assertNotIn('fd42:8::', self.h.rules)
                self.assertIn('ether saddr '+PROTECTED+' return', self.h.rules)
                self.assertIn('ether type { 0x0800, 0x86dd } counter drop', self.h.rules)

    def test_empty_assignment_does_not_hide_later_valid_local_ipv6_subnet(self):
        self.h.write('lan.json', {
            'ipv4-address': [], 'ipv6-address': [],
            'ipv6-prefix-assignment': [
                {'address': 'fd42:8::', 'mask': 60, 'local-address': {}},
                {'address': '2001:db8::', 'mask': 48,
                 'local-address': {'address': '2001:db8:50::1', 'mask': 64}},
            ],
        })
        self.h.run()
        self.assertIn('ip6 daddr 2001:db8:50::1/64 return', self.h.rules)
        self.assertNotIn('fd42:8::', self.h.rules)
        self.assertNotIn('2001:db8::/48', self.h.rules)

    def test_invalid_optional_ipv6_data_preserves_existing_rules(self):
        self.h.run()
        original = self.h.rules
        for local in (None, [], '', {'address': 'fd42:8::1'}, {'mask': 64},
                      {'address': '', 'mask': ''}, {'other': 'unused'},
                      {'address': 'fd42:8::1', 'mask': True},
                      {'address': 'fd42:8::1', 'mask': '64'},
                      {'address': 'fd42:8::1', 'mask': 64.0},
                      {'address': 'fd42:8::1', 'mask': 0}):
            with self.subTest(local=local):
                self.h.write('lan.json', {
                    'ipv4-address': [{'address': '192.168.8.1', 'mask': 24}],
                    'ipv6-address': [], 'ipv6-prefix-assignment': [
                        {'address': 'fd42:8::', 'mask': 60, 'local-address': local}],
                })
                calls = self.h.run(expect_ok=False)
                self.assertEqual(self.h.status['error'], 'network_status_failed')
                self.assertEqual(self.h.rules, original)
                self.assertFalse(self.h.mutations(calls))

    def test_empty_optional_ipv6_object_without_local_prefix_is_an_error(self):
        self.h.write('lan.json', {'ipv4-address': [], 'ipv6-address': [],
            'ipv6-prefix-assignment': [
                {'address': 'fd42:8::', 'mask': 60, 'local-address': {}}]})
        calls = self.h.run(expect_ok=False)
        self.assertEqual(self.h.status['error'], 'network_status_failed')
        self.assertFalse(self.h.mutations(calls))
        self.assertFalse((self.h.path/'nft-table').exists())

    def test_unchanged_policy_has_no_reloads_or_rule_replacement(self):
        self.h.run()
        calls = self.h.run()
        self.assertFalse(self.h.mutations(calls))
        self.assertEqual(self.h.status['internet_state'], 'blocked')
        self.assertTrue(self.h.status['offload_disabled'])

    def test_network_and_whitelist_updates_replace_one_table_atomically(self):
        self.h.run()
        self.h.write('lan.json', {'ipv4-address': [{'address': '172.20.16.1', 'mask': 20}]})
        calls = self.h.run(white='02:00:00:00:00:22')
        self.assertEqual(sum(c[0] == 'nft' and c[1] == '-f' for c in calls), 1)
        self.assertIn('add table netdev wifi_client_limiter\ndelete table netdev wifi_client_limiter\ntable netdev wifi_client_limiter {', self.h.rules)
        self.assertNotIn('flush ruleset', self.h.rules)
        self.assertIn('172.20.16.0/20 return', self.h.rules)
        self.assertNotIn('192.168.50.0/24', self.h.rules)
        self.assertNotIn(PROTECTED, self.h.rules)
        self.assertIn('02:00:00:00:00:22', self.h.rules)
        self.assertEqual(self.h.rules, (self.h.path/'state/internet-policy-applied').read_text())
        self.assertEqual(self.h.read('uci.json')['committed']['firewall.keep.name'], 'untouched')

    def test_missing_live_table_is_reapplied_after_external_firewall_flush(self):
        self.h.run()
        (self.h.path/'nft-table').unlink()
        calls = self.h.run()
        self.assertEqual(sum(c[0] == 'nft' and c[1] == '-f' for c in calls), 1)
        self.assertEqual(self.h.status['internet_state'], 'blocked')

    def test_snapshot_or_validation_errors_preserve_last_table_and_report_error(self):
        self.h.run()
        old = self.h.rules
        for failure, error in (('wireless', 'network_status_failed'), ('network', 'network_status_failed'),
                               ('validation', 'rule_validation_failed'), ('apply', 'rule_apply_failed')):
            with self.subTest(failure=failure):
                self.h.write('failures.json', {failure: 1})
                calls = self.h.run(white='02:00:00:00:00:22', expect_ok=False)
                self.assertEqual(self.h.rules, old)
                self.assertEqual(self.h.status['error'], error)
                self.assertEqual(self.h.status['internet_state'], 'error')
                self.assertTrue(self.h.status['active'])
                if failure in ('wireless', 'network', 'validation'):
                    self.assertFalse(self.h.mutations(calls))

    def test_zero_or_invalid_prefix_never_turns_into_internet_exemption(self):
        self.h.run()
        old = self.h.rules
        for address in ({'address': '0.0.0.0', 'mask': 0}, {'address': '999.1.1.1', 'mask': 24},
                        {'address': '192.168.50.1; touch owned', 'mask': 24}):
            with self.subTest(address=address):
                self.h.write('lan.json', {'ipv4-address': [address]})
                self.h.run(expect_ok=False)
                self.assertEqual(self.h.rules, old)
                self.assertEqual(self.h.status['error'], 'network_status_failed')

    def test_failed_disable_reload_is_retried_without_claiming_blocked(self):
        self.h.write('failures.json', {'reload': 1})
        self.h.run(expect_ok=False)
        self.assertEqual(self.h.status['internet_state'], 'error')
        self.assertEqual(self.h.status['error'], 'offload_disable_failed')
        self.assertTrue((self.h.path/'backup/offload-reload-required').exists())
        calls = self.h.run()
        self.assertEqual(sum(c[0] == 'fw4' and c[1:] == ['reload'] for c in calls), 1)
        self.assertFalse((self.h.path/'backup/offload-reload-required').exists())
        self.assertEqual(self.h.status['internet_state'], 'blocked')
        self.assertFalse(any(c[0] == 'nft' and c[1] == '-f' for c in calls))

    def test_backup_restores_originally_absent_options(self):
        store = self.h.read('uci.json')
        store['committed'].pop('firewall.cfgdefaults.flow_offloading_hw')
        self.h.write('uci.json', store)
        original = store['committed'].copy()
        self.h.run()
        self.assertTrue(any(line.split()[2:] == ['flow_offloading_hw', '-']
                            for line in (self.h.path/'backup/offload-backup').read_text().splitlines()))
        self.h.run(internet=1)
        self.assertEqual(self.h.read('uci.json')['committed'], original)
        self.assertFalse((self.h.path/'backup/offload-backup').exists())
        self.assertFalse((self.h.path/'state/firewall/policy.nft').exists())
        self.assertFalse((self.h.path/'nft-table').exists())

    def test_restore_respects_manual_option_edits_and_removed_options(self):
        self.h.run()
        store = self.h.read('uci.json')
        store['committed']['firewall.cfgdefaults.flow_offloading'] = 'false'
        store['committed'].pop('firewall.cfgdefaults.flow_offloading_hw')
        self.h.write('uci.json', store)
        self.h.run(action='cleanup')
        committed = self.h.read('uci.json')['committed']
        self.assertEqual(committed['firewall.cfgdefaults.flow_offloading'], 'false')
        self.assertNotIn('firewall.cfgdefaults.flow_offloading_hw', committed)

    def test_pending_firewall_changes_are_never_committed_on_disable_or_restore(self):
        store = self.h.read('uci.json')
        store['pending']['firewall.keep.name'] = 'another LuCI tab'
        self.h.write('uci.json', store)
        calls = self.h.run(expect_ok=False)
        self.assertFalse(any(c[0] == 'uci' and 'commit' in c for c in calls))
        self.assertEqual(self.h.read('uci.json'), store)
        self.assertEqual(self.h.status['error'], 'offload_disable_failed')
        store['pending'] = {}
        self.h.write('uci.json', store)
        self.h.run()
        store = self.h.read('uci.json')
        store['pending']['firewall.keep.name'] = 'another LuCI tab'
        self.h.write('uci.json', store)
        calls = self.h.run(action='cleanup', expect_ok=False)
        self.assertEqual(self.h.read('uci.json'), store)
        self.assertFalse(any(c[0] == 'uci' and 'commit' in c for c in calls))
        self.assertEqual(self.h.status['error'], 'offload_restore_failed')
        self.assertTrue((self.h.path/'backup/offload-backup').exists())

    def test_cleanup_restore_reload_failure_retains_backup_and_retries(self):
        original = self.h.read('uci.json')['committed']
        self.h.run()
        self.h.write('failures.json', {'reload': 1})
        self.h.run(action='cleanup', expect_ok=False)
        self.assertFalse((self.h.path/'nft-table').exists())
        self.assertEqual(self.h.status['error'], 'offload_restore_failed')
        self.assertTrue((self.h.path/'backup/offload-backup').exists())
        calls = self.h.run(action='cleanup')
        self.assertEqual(sum(c[0] == 'fw4' and c[1:] == ['reload'] for c in calls), 1)
        self.assertEqual(self.h.read('uci.json')['committed'], original)
        self.assertFalse((self.h.path/'backup/offload-backup').exists())
        self.assertEqual(self.h.status['internet_state'], 'inactive')

    def test_dry_run_transition_releases_only_own_rules_and_restores_offload(self):
        original = self.h.read('uci.json')['committed']
        self.h.run()
        calls = self.h.run(dry=1)
        self.assertEqual(self.h.status['internet_state'], 'test')
        self.assertFalse(self.h.status['active'])
        self.assertFalse((self.h.path/'nft-table').exists())
        self.assertEqual(self.h.read('uci.json')['committed'], original)
        self.assertEqual([c[4] for c in calls if c[0] == 'nft' and c[1] == 'delete'], ['wifi_client_limiter'])
        self.assertFalse((self.h.path/'state/internet-policy-applied').exists())
        self.assertFalse(self.h.mutations(self.h.run(dry=1)))

    def test_removed_or_non_ap_selection_removes_previous_rules(self):
        self.h.run()
        self.h.run(networks='station broken missing')
        self.assertEqual(self.h.status['internet_state'], 'inactive')
        self.assertFalse((self.h.path/'nft-table').exists())

    def test_native_reload_callback_rebuilds_renamed_aps_without_recursive_reload(self):
        self.h.run()
        wireless = self.h.read('wireless.json')
        wireless['radio0']['interfaces'][0]['ifname'] = 'wlan-renamed'
        self.h.write('wireless.json', wireless)
        (self.h.path/'nft-table').unlink()
        calls = self.h.run(action='refresh')
        self.assertIn('device "wlan-renamed"', self.h.rules)
        self.assertNotIn('device "wlan-guest"', self.h.rules)
        self.assertEqual(self.h.status['internet_state'], 'blocked')
        self.assertFalse(any(c[0] == 'fw4' or (c[0] == 'uci' and 'commit' in c) for c in calls))

    def test_native_reload_callback_handles_down_ap_without_poisoning_fw4(self):
        self.h.run()
        self.h.write('wireless.json', {'radio0': {'interfaces': []}})
        calls = self.h.run(action='refresh')
        self.assertFalse((self.h.path/'nft-table').exists())
        self.assertEqual(self.h.status['internet_state'], 'inactive')
        self.assertFalse(any(c[0] == 'fw4' for c in calls))
        self.assertFalse(any(c[0] == 'nft' and c[1] == '-f' for c in calls))
        self.h.write('failures.json', {'wireless': 1})
        self.h.run(action='refresh', expect_ok=False)
        self.assertEqual(self.h.status['internet_state'], 'error')

    def test_reload_callback_never_waits_for_worker_or_resurrects_stopped_policy(self):
        self.h.run()
        lock = self.h.path/'state/policy-lock'
        lock.mkdir()
        (lock/'pid').write_text(str(os.getpid()))
        self.assertFalse(self.h.mutations(self.h.run(action='refresh')))
        (lock/'pid').unlink()
        lock.rmdir()
        self.h.run(action='cleanup')
        self.assertFalse(self.h.mutations(self.h.run(action='refresh')))
        self.assertFalse((self.h.path/'nft-table').exists())

    def test_retained_live_flowtable_reports_error_until_reload_really_removes_it(self):
        self.h.write('failures.json', {'flowtable': 1, 'retain_flowtable': 1})
        self.h.run(expect_ok=False)
        self.assertEqual(self.h.status['error'], 'offload_disable_failed')
        self.h.write('failures.json', {'flowtable': 1})
        self.h.run()
        self.assertEqual(self.h.status['internet_state'], 'blocked')

    def test_anonymous_defaults_id_changes_do_not_lose_restore_target(self):
        self.h.write('failures.json', {'rename_defaults': 1})
        self.h.run()
        store = self.h.read('uci.json')['committed']
        self.assertNotIn('firewall.cfgdefaults', store)
        self.assertEqual(store['firewall.cfg000001.flow_offloading'], '0')
        self.h.run(action='cleanup')
        store = self.h.read('uci.json')['committed']
        self.assertEqual(store['firewall.cfg000002.flow_offloading'], '1')
        self.assertEqual(store['firewall.cfg000002.flow_offloading_hw'], '1')
        self.assertFalse((self.h.path/'backup/offload-backup').exists())

    def test_failed_commit_reverts_only_plugin_edits_and_retries(self):
        original = self.h.read('uci.json')
        self.h.write('failures.json', {'commit': 1})
        self.h.run(expect_ok=False)
        self.assertEqual(self.h.read('uci.json'), original)
        self.assertEqual(self.h.status['error'], 'offload_disable_failed')
        self.h.run()
        self.assertEqual(self.h.status['internet_state'], 'blocked')

    def test_manual_offload_edit_while_active_becomes_the_restored_intent(self):
        self.h.run()
        store = self.h.read('uci.json')
        store['committed']['firewall.cfgdefaults.flow_offloading'] = 'false'
        self.h.write('uci.json', store)
        self.h.run()
        self.assertEqual(self.h.read('uci.json')['committed']['firewall.cfgdefaults.flow_offloading'], '0')
        self.h.run(action='cleanup')
        self.assertEqual(self.h.read('uci.json')['committed']['firewall.cfgdefaults.flow_offloading'], 'false')

    def test_ambiguous_defaults_fingerprint_does_not_change_another_section(self):
        self.h.run()
        store = self.h.read('uci.json')
        store['committed']['firewall.another'] = 'defaults'
        store['committed']['firewall.another.flow_offloading'] = '1'
        self.h.write('uci.json', store)
        calls = self.h.run(action='cleanup', expect_ok=False)
        self.assertEqual(self.h.read('uci.json'), store)
        self.assertFalse(any(c[0] == 'uci' and 'commit' in c for c in calls))
        self.assertTrue((self.h.path/'backup/offload-backup').exists())

    def test_successful_reload_without_restored_live_flowtable_retains_backup(self):
        self.h.run()
        self.h.write('failures.json', {'retain_flowtable': 1, 'flowtable': 0})
        self.h.run(action='cleanup', expect_ok=False)
        self.assertEqual(self.h.status['error'], 'offload_restore_failed')
        self.assertTrue((self.h.path/'backup/offload-backup').exists())
        self.assertTrue((self.h.path/'backup/offload-reload-required').exists())
        self.h.write('failures.json', {})
        self.h.run(action='cleanup')
        self.assertFalse((self.h.path/'backup/offload-backup').exists())
        self.assertEqual(self.h.status['internet_state'], 'inactive')


class LegacyInternetPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='wifi-limiter-fw3-')
        self.addCleanup(temporary.cleanup)
        self.h = InternetHarness(temporary.name)
        self.h.use_fw3()

    def test_stock_fw3_uses_netdev_early_ingress_for_both_ip_families(self):
        calls = self.h.run()
        self.assertEqual(self.h.status['firewall_backend'], 'fw3')
        self.assertEqual(self.h.status['nft_family'], 'netdev')
        self.assertEqual(self.h.status['internet_state'], 'blocked')
        self.assertIn('table netdev wifi_client_limiter', self.h.rules)
        self.assertIn('hook ingress device "wlan-guest" priority -500', self.h.rules)
        self.assertIn('ip daddr 192.168.50.0/24 return', self.h.rules)
        self.assertIn('ip6 daddr fd42:50::1234/64 return', self.h.rules)
        self.assertIn('ether saddr '+PROTECTED+' return', self.h.rules)
        self.assertTrue(any(c[0] == 'fw3' and 'reload' in c for c in calls))
        self.assertTrue(any(c[0] == 'iptables-save' for c in calls))
        self.assertTrue(any(c[0] == 'ip6tables-save' for c in calls))
        self.assertFalse(any(c[0] == 'fw4' for c in calls))

    def test_fw3_unmodified_scans_and_reload_callback_never_churn(self):
        self.h.run()
        self.assertFalse(self.h.mutations(self.h.run()))
        self.assertFalse(self.h.mutations(self.h.run(action='refresh')))
        self.assertEqual(self.h.status['internet_state'], 'blocked')

    def test_fw3_retained_ipv4_or_ipv6_flowoffload_is_an_error_and_retried(self):
        for family in ('4','6'):
            with self.subTest(family=family):
                settings = {'fw4_missing': 1, 'flowoffload'+family: 1, 'retain_flowoffload'+family: 1}
                self.h.write('failures.json', settings)
                self.h.run(expect_ok=False)
                self.assertEqual(self.h.status['error'], 'offload_disable_failed')
                self.assertEqual(self.h.status['internet_state'], 'error')
                self.h.write('failures.json', {'fw4_missing': 1, 'flowoffload'+family: 1})
                calls = self.h.run()
                self.assertEqual(self.h.status['internet_state'], 'blocked')
                self.assertTrue(any(c[0] == 'fw3' and 'reload' in c for c in calls))
                self.h.run(action='cleanup')

    def test_fw3_unreadable_ipv4_or_ipv6_rules_never_claim_blocked(self):
        for family in ('4','6'):
            with self.subTest(family=family):
                self.h.write('failures.json', {'fw4_missing': 1, 'inspect'+family: 2})
                self.h.run(expect_ok=False)
                self.assertEqual(self.h.status['internet_state'], 'error')
                self.assertEqual(self.h.status['error'], 'offload_disable_failed')
                self.h.write('failures.json', {'fw4_missing': 1})
                self.h.run()
                self.h.run(action='cleanup')

    def test_fw3_failed_reload_retry_preserves_owned_options(self):
        self.h.write('failures.json', {'fw4_missing': 1, 'reload': 1})
        self.h.run(expect_ok=False)
        self.assertEqual(self.h.status['error'], 'offload_disable_failed')
        self.assertTrue((self.h.path/'backup/offload-reload-required').exists())
        self.h.run()
        self.assertEqual(self.h.status['internet_state'], 'blocked')
        self.assertEqual(self.h.read('uci.json')['committed']['firewall.cfgdefaults.flow_offloading'], '0')

    def test_fw3_restore_verifies_ipv4_and_ipv6_after_successful_reload(self):
        original = self.h.read('uci.json')['committed']
        self.h.run()
        self.h.write('failures.json', {'fw4_missing': 1, 'retain_flowoffload6': 1, 'flowoffload6': 0})
        self.h.run(action='cleanup', expect_ok=False)
        self.assertEqual(self.h.status['error'], 'offload_restore_failed')
        self.assertTrue((self.h.path/'backup/offload-backup').exists())
        self.h.write('failures.json', {'fw4_missing': 1})
        self.h.run(action='cleanup')
        self.assertEqual(self.h.read('uci.json')['committed'], original)
        self.assertFalse((self.h.path/'backup/offload-backup').exists())
        self.assertTrue(self.h.read('failures.json')['flowoffload4'])
        self.assertTrue(self.h.read('failures.json')['flowoffload6'])

    def test_fw3_fresh_simulation_and_transition_do_not_leave_rules(self):
        original = self.h.read('uci.json')['committed']
        self.assertFalse(self.h.mutations(self.h.run(dry=1)))
        self.h.run()
        self.h.run(dry=1)
        self.assertEqual(self.h.status['internet_state'], 'test')
        self.assertFalse((self.h.path/'nft-table').exists())
        self.assertEqual(self.h.read('uci.json')['committed'], original)

    def test_missing_fw3_ipv6_inspector_fails_before_any_mutation(self):
        (self.h.path/'bin/ip6tables-save').unlink()
        calls = self.h.run(expect_ok=False)
        self.assertFalse(self.h.mutations(calls))
        self.assertEqual(self.h.status['internet_state'], 'error')
        self.assertEqual(self.h.status['error'], 'firewall_backend_required')

    def test_fw3_vlan_drop_follows_mac_exception_and_precedes_local_prefixes(self):
        self.h.run()
        rules = self.h.rules
        vlan = next(line for line in rules.splitlines() if 'ether type' in line and '0x8100' in line)
        self.assertIn('0x88a8', vlan)
        self.assertIn('0x9100', vlan)
        self.assertIn('drop', vlan)
        self.assertLess(rules.index('ether saddr '+PROTECTED), rules.index(vlan.strip()))
        self.assertLess(rules.index(vlan.strip()), rules.index('ip daddr 192.168.50.0/24'))
        self.assertIn('0x0800', rules)
        self.assertIn('0x86dd', rules)

    def test_fw3_successful_but_empty_or_incomplete_live_dump_is_unsafe(self):
        for option in ('empty_dump4','empty_dump6','incomplete_dump4','incomplete_dump6'):
            with self.subTest(option=option):
                self.h.write('failures.json', {'fw4_missing': 1, option: 1})
                self.h.run(expect_ok=False)
                self.assertEqual(self.h.status['error'], 'offload_disable_failed')
                self.assertEqual(self.h.status['internet_state'], 'error')
                self.h.write('failures.json', {'fw4_missing': 1})
                self.h.run()
                self.h.run(action='cleanup')

    def test_old_inet_policy_migrates_only_after_new_netdev_validation_succeeds(self):
        # Simulate upgrading the modern backend from its earlier inet renderer.
        fw4 = self.h.path/'bin/fw4'
        fw4.write_text(MOCK_API.lstrip())
        fw4.chmod(0o755)
        original = ('add table inet wifi_client_limiter\n'
                    'delete table inet wifi_client_limiter\n'
                    'table inet wifi_client_limiter { chain previous {} }\n')
        (self.h.path/'nft-table').write_text(original)
        (self.h.path/'state/internet-policy-applied').write_text(original)
        self.h.write('failures.json', {'validation': 1})
        self.h.run(expect_ok=False)
        self.assertEqual(self.h.rules, original)
        self.assertEqual(self.h.status['error'], 'rule_validation_failed')
        self.assertTrue(self.h.status['active'])
        calls = self.h.run()
        self.assertIn('table netdev wifi_client_limiter', self.h.rules)
        self.assertFalse((self.h.path/'nft-old-inet').exists())
        self.assertIn(['nft','delete','table','inet','wifi_client_limiter'], calls)
        self.assertFalse(any(call[:4] == ['nft','delete','table','netdev'] for call in calls))
        self.assertEqual(self.h.status['firewall_backend'], 'fw4')
        self.assertEqual(self.h.status['nft_family'], 'netdev')
        self.assertEqual(self.h.status['internet_state'], 'blocked')

    def test_loaded_native_flowoffload_cache_is_unloaded_and_original_presence_restored(self):
        module = self.h.path/'sys-module/xt_FLOWOFFLOAD'
        module.mkdir()
        calls = self.h.run()
        self.assertIn(['rmmod','xt_FLOWOFFLOAD'], calls)
        self.assertFalse(module.exists())
        self.assertEqual(self.h.status['internet_state'], 'blocked')
        self.assertEqual((self.h.path/'backup/native-flowoffload-backup').read_text().strip(), '1')
        self.h.run(action='cleanup')
        self.assertTrue(module.exists())
        self.assertFalse((self.h.path/'backup/native-flowoffload-backup').exists())

    def test_native_cache_unload_failure_or_retained_module_is_retried(self):
        module = self.h.path/'sys-module/xt_FLOWOFFLOAD'
        for failure in ('module_unload','retain_module'):
            with self.subTest(failure=failure):
                module.mkdir(exist_ok=True)
                self.h.write('failures.json', {'fw4_missing': 1, failure: 1})
                self.h.run(expect_ok=False)
                self.assertTrue(module.exists())
                self.assertEqual(self.h.status['internet_state'], 'error')
                self.assertEqual(self.h.status['error'], 'offload_disable_failed')
                self.assertTrue((self.h.path/'backup/native-flowoffload-backup').exists())
                self.h.write('failures.json', {'fw4_missing': 1})
                calls = self.h.run()
                self.assertIn(['rmmod','xt_FLOWOFFLOAD'], calls)
                self.assertFalse(module.exists())
                self.assertEqual(self.h.status['internet_state'], 'blocked')
                self.h.run(action='cleanup')

    def test_native_module_restore_failure_keeps_backup_until_retry_succeeds(self):
        store = self.h.read('uci.json')
        store['committed']['firewall.cfgdefaults.flow_offloading'] = '0'
        store['committed']['firewall.cfgdefaults.flow_offloading_hw'] = '0'
        self.h.write('uci.json', store)
        module = self.h.path/'sys-module/xt_FLOWOFFLOAD'
        module.mkdir()
        self.h.run()
        self.h.write('failures.json', {'fw4_missing': 1, 'module_load': 1})
        self.h.run(action='cleanup', expect_ok=False)
        self.assertEqual(self.h.status['error'], 'native_module_restore_failed')
        self.assertTrue((self.h.path/'backup/native-flowoffload-backup').exists())
        self.assertFalse(module.exists())
        self.h.run(action='cleanup')
        self.assertTrue(module.exists())
        self.assertFalse((self.h.path/'backup/native-flowoffload-backup').exists())

    def test_originally_absent_module_is_not_force_unloaded_after_native_restore(self):
        module = self.h.path/'sys-module/xt_FLOWOFFLOAD'
        self.assertFalse(module.exists())
        self.h.run()
        self.assertEqual((self.h.path/'backup/native-flowoffload-backup').read_text().strip(), '0')
        calls = self.h.run(action='cleanup')
        self.assertTrue(module.exists(), 'restored native rules should autoload their target module')
        self.assertFalse(any(call[0] == 'rmmod' for call in calls))
        self.assertFalse((self.h.path/'backup/native-flowoffload-backup').exists())

    def test_missing_native_cache_unloader_fails_before_firewall_mutations(self):
        (self.h.path/'bin/rmmod').unlink()
        calls = self.h.run(expect_ok=False)
        self.assertFalse(self.h.mutations(calls))
        self.assertEqual(self.h.status['internet_state'], 'error')
        self.assertEqual(self.h.status['error'], 'firewall_backend_required')

    def test_native_cache_restore_supports_insmod_when_modprobe_is_unavailable(self):
        (self.h.path/'bin/modprobe').unlink()
        store = self.h.read('uci.json')
        store['committed']['firewall.cfgdefaults.flow_offloading'] = '0'
        store['committed']['firewall.cfgdefaults.flow_offloading_hw'] = '0'
        self.h.write('uci.json', store)
        module = self.h.path/'sys-module/xt_FLOWOFFLOAD'
        module.mkdir()
        self.h.run()
        calls = self.h.run(action='cleanup')
        self.assertIn(['insmod','xt_FLOWOFFLOAD'], calls)
        self.assertTrue(module.exists())

    def test_missing_module_visibility_fails_before_any_firewall_mutation(self):
        (self.h.path/'sys-module').rmdir()
        calls = self.h.run(expect_ok=False)
        self.assertFalse(self.h.mutations(calls))
        self.assertEqual(self.h.status['error'], 'firewall_backend_required')
        self.assertEqual(self.h.status['internet_state'], 'error')


class InternetStatusRpcTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='wifi-limiter-status-')
        self.addCleanup(temporary.cleanup)
        self.h = InternetHarness(temporary.name)
        shim = self.h.path/'jshn.sh'
        shim.write_text(SHIM + r"""
json_init() { output='{'; separator=; }
json_add_string() { local encoded; encoded="$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$2")"; output="$output$separator\"$1\":$encoded"; separator=,; }
json_add_boolean() { local value=false; [ "$2" != 1 ] || value=true; output="$output$separator\"$1\":$value"; separator=,; }
json_add_array() { output="$output$separator\"$1\":["; separator=; }
json_add_object() { output="$output$separator{"; separator=; }
json_close_object() { output="$output}"; separator=,; }
json_close_array() { output="$output]"; separator=,; }
json_set_namespace() { [ -z "$2" ] || eval "$2=default"; }
json_dump() { printf '%s}\n' "$output"; }
""")
        source = (ROOT/'files/usr/libexec/rpcd/wifi-client-limiter').read_text()
        # Only absolute OpenWrt paths are relocated; all RPC control flow runs unchanged.
        source = source.replace('/usr/share/libubox/jshn.sh', str(shim))
        source = source.replace('/tmp/wifi-client-limiter', str(self.h.path/'state'))
        source = source.replace('/etc/config/wifi-client-limiter', str(self.h.path/'committed.conf'))
        source = source.replace('/proc/$pid/cmdline', str(self.h.path/'proc')+'/$pid/cmdline')
        source = source.replace('/usr/share/wifi-client-limiter/station-adapter.sh', str(self.h.path/'station-adapter.sh'))
        self.config()
        (self.h.path/'proc/123').mkdir(parents=True)
        (self.h.path/'proc/123/cmdline').write_bytes(b'/bin/sh\x00/usr/sbin/wifi-client-limiter\x00')
        self.h.write('state/service-status.json', {'service_state': 'running', 'pid': 123, 'error': '', 'detail': ''})
        (self.h.path/'ap-records').write_text('wlan-guest guest hostapd\nwlan-admin admin hostapd\n')
        self.h.write('clients.json', {'clients': [], 'unavailable': False})
        adapter = self.h.path/'station-adapter.sh'
        adapter.write_text('#!/bin/sh\nstation_ap_records() { cat "$FIX/ap-records"; }\n[ "$1" != clients ] || cat "$FIX/clients.json"\n')
        adapter.chmod(0o755)
        self.rpc = self.h.path/'rpc.sh'
        self.rpc.write_text(source)

    def config(self, *, enabled=1, dry=0, internet=0, networks=('guest',), white=(PROTECTED,)):
        text = "config limiter 'main'\n option enabled '%s'\n option dry_run '%s'\n option guest_internet '%s'\n" % (enabled, dry, internet)
        text += ''.join(" list network '%s'\n" % item for item in networks)
        text += ''.join(" list whitelist '%s'\n" % item for item in white)
        (self.h.path/'committed.conf').write_text(text)

    @staticmethod
    def expected(state, active, backend='fw4'):
        return {'internet_state': state, 'active': active, 'firewall_backend': backend,
                'nft_family': 'netdev'}

    def run_rpc(self, *args, ok=True):
        (self.h.path/'calls').write_text('')
        env = dict(os.environ, FIX=str(self.h.path),
                   INTERNET_CHECKSUM_HELPER=str(CHECKSUM_HELPER),
                   INTERNET_VENDOR_HELPER=str(self.h.path/'vendor-noop.sh'),
                   INTERNET_MODULE_ROOT=str(self.h.path/'sys-module'),
                   PATH=self.h.command_path)
        result = subprocess.run(['sh', str(self.rpc), *args], env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        calls = [json.loads(line) for line in (self.h.path/'calls').read_text().splitlines()]
        self.assertFalse(self.h.mutations(calls))
        self.last_rpc = json.loads(result.stdout) if result.stdout.strip() else None
        if args == ('call', 'status'):
            return {key: self.last_rpc[key] for key in ('internet_state','active','firewall_backend','nft_family')}
        return self.last_rpc

    def test_readonly_rpc_advertises_supported_methods_and_rejects_unknown_calls(self):
        self.assertEqual(self.run_rpc('list'), {'status': {}, 'clients': {}})
        self.assertEqual(self.run_rpc('call', 'status'), self.expected('error', False))
        for args in (('call', 'apply'), ('call', 'delete'), ('delete',), ()):
            with self.subTest(args=args): self.run_rpc(*args, ok=False)

    def test_rpc_does_not_claim_blocked_after_native_firewall_flush_or_offload(self):
        self.h.run()
        self.assertEqual(self.run_rpc('call', 'status'), self.expected('blocked', True))
        (self.h.path/'nft-table').unlink()
        self.assertEqual(self.run_rpc('call', 'status'), self.expected('error', False))
        (self.h.path/'nft-table').write_text('live table')
        self.h.write('failures.json', {'flowtable': 1})
        self.assertEqual(self.run_rpc('call', 'status'), self.expected('error', False))

    def test_rpc_verifies_ipv4_policy_with_empty_optional_ipv6_local_address(self):
        self.h.use_fw3()
        self.h.write('lan.json', {
            'ipv4-address': [{'address': '192.168.8.1', 'mask': 24}],
            'ipv6-address': [], 'ipv6-prefix-assignment': [
                {'address': 'fd42:8::', 'mask': 60, 'local-address': {}}],
        })
        self.h.run()
        self.assertEqual(self.run_rpc('call', 'status'), self.expected('blocked', True, 'fw3'))
        self.client_rows()
        result = self.run_rpc('call', 'clients')
        self.assertTrue(result['active'])
        self.assertEqual(result['clients'][0]['internet_permission'], 'exempt')
        self.assertEqual(result['clients'][1]['internet_permission'], 'blocked')

    def test_rpc_rejects_invalid_optional_ipv6_local_address_after_policy_applied(self):
        self.h.run()
        network = self.h.read('lan.json')
        for local in (None, [], '', {'address': 'fd42:8::1'}, {'mask': 64},
                      {'address': '', 'mask': ''}, {'other': 'unused'},
                      {'address': 'fd42:8::1', 'mask': True},
                      {'address': 'fd42:8::1', 'mask': '64'},
                      {'address': 'fd42:8::1', 'mask': 64.0}):
            with self.subTest(local=local):
                network['ipv6-prefix-assignment'] = [
                    {'address': 'fd42:8::', 'mask': 60, 'local-address': local}]
                self.h.write('lan.json', network)
                self.run_rpc('call', 'status')
                self.assertFalse(self.last_rpc['active'])
                self.assertEqual(self.last_rpc['reason'], 'network_status_failed')

    def test_malformed_json_and_unknown_mode_fall_back_to_error(self):
        path = self.h.path/'state/internet-status.json'
        path.write_text('{bad json')
        self.assertEqual(self.run_rpc('call', 'status'), self.expected('error', False))
        self.h.write('state/internet-status.json', {'internet_state': 'arbitrary', 'active': False})
        self.assertEqual(self.run_rpc('call', 'status'), self.expected('error', False))


    def test_fw3_rpc_requires_safe_readable_ipv4_and_ipv6_live_rules(self):
        self.h.use_fw3()
        self.h.run()
        self.assertEqual(self.run_rpc('call', 'status'), self.expected('blocked', True, 'fw3'))
        for family in ('4','6'):
            with self.subTest(family=family):
                self.h.write('failures.json', {'fw4_missing': 1, 'flowoffload'+family: 1})
                self.assertEqual(self.run_rpc('call', 'status'), self.expected('error', False, 'fw3'))
                self.h.write('failures.json', {'fw4_missing': 1, 'inspect'+family: 1})
                self.assertEqual(self.run_rpc('call', 'status'), self.expected('error', False, 'fw3'))
        self.h.write('failures.json', {'fw4_missing': 1})
        (self.h.path/'nft-table').unlink()
        self.assertEqual(self.run_rpc('call', 'status'), self.expected('error', False, 'fw3'))
    def test_fw3_rpc_rejects_stale_cached_native_module_without_mutating_it(self):
        self.h.use_fw3()
        self.h.run()
        module = self.h.path/'sys-module/xt_FLOWOFFLOAD'
        module.mkdir()
        self.assertEqual(self.run_rpc('call', 'status'), self.expected('error', False, 'fw3'))
        self.assertTrue(module.exists())

    def test_fw3_rpc_requires_module_visibility_to_claim_no_cached_flows(self):
        self.h.use_fw3()
        self.h.run()
        (self.h.path/'sys-module').rmdir()
        self.assertEqual(self.run_rpc('call', 'status'), self.expected('error', False, 'fw3'))

    def client_rows(self):
        self.h.write('clients.json', {'clients': [
            {'mac': PROTECTED, 'ifname': 'wlan-guest', 'section': 'guest'},
            {'mac': '02:00:00:00:00:22', 'ifname': 'wlan-guest', 'section': 'guest'},
            {'mac': '02:00:00:00:00:33', 'ifname': 'wlan-admin', 'section': 'admin'},
        ], 'unavailable': False})

    def test_clients_permissions_come_from_verified_live_rules_and_committed_policy(self):
        self.h.run()
        self.client_rows()
        hostile_name = '<img src=x onerror=alert(1)> "operator"'
        self.h.write('host-hints.json', {PROTECTED: {'name': hostile_name}})
        result = self.run_rpc('call', 'clients')
        self.assertTrue(result['active'])
        self.assertTrue(result['service_running'])
        self.assertEqual(result['reason'], 'policy_verified')
        self.assertEqual([row['internet_permission'] for row in result['clients']],
                         ['exempt', 'blocked', 'unmanaged'])
        self.assertEqual(result['clients'][0]['hostname'], hostile_name)
        self.assertEqual(result['clients'][0]['mac'], PROTECTED)
        self.assertEqual(result['clients'][0]['section'], 'guest')
        self.assertTrue(result['clients'][0]['protected'])
        self.assertTrue(result['clients'][1]['managed'])
        self.assertFalse(result['clients'][2]['managed'])

    def test_stopped_or_pid_reused_worker_does_not_claim_enforcement(self):
        self.h.run()
        self.client_rows()
        for record, command in (
            ({'service_state': 'stopped', 'pid': 0}, None),
            ({'service_state': 'running', 'pid': 999}, None),
            ({'service_state': 'running', 'pid': 123}, b'/usr/bin/printf\x00/usr/sbin/wifi-client-limiter\x00'),
        ):
            with self.subTest(record=record):
                self.h.write('state/service-status.json', record)
                if command is not None: (self.h.path/'proc/123/cmdline').write_bytes(command)
                result = self.run_rpc('call', 'clients')
                self.assertEqual(result['reason'], 'service_stopped')
                self.assertFalse(result['active'])
                self.assertFalse(result['service_running'])
                self.assertEqual(result['clients'][1]['internet_permission'], 'unknown')
        self.assertTrue((self.h.path/'nft-table').exists(), 'readonly RPC must retain rules')

    def test_startup_error_is_preserved_for_status_and_clients(self):
        self.h.write('state/service-status.json', {'service_state': 'error', 'pid': 0,
            'error': 'mtk_driver_unsupported', 'detail': 'Unsupported driver: abc123'})
        self.client_rows()
        result = self.run_rpc('call', 'clients')
        self.assertEqual(result['reason'], 'service_error')
        self.assertEqual(result['error'], 'mtk_driver_unsupported')
        self.assertEqual(result['service_detail'], 'Unsupported driver: abc123')
        self.assertFalse(result['service_running'])
        self.assertEqual(result['clients'][1]['internet_permission'], 'unknown')

    def test_pending_uci_changes_do_not_change_permissions(self):
        self.h.run()
        self.client_rows()
        store = self.h.read('uci.json')
        store['pending'].update({'wifi-client-limiter.main.guest_internet': '1',
                                 'wifi-client-limiter.main.whitelist': '02:00:00:00:00:22'})
        self.h.write('uci.json', store)
        result = self.run_rpc('call', 'clients')
        self.assertFalse(result['guest_internet'])
        self.assertEqual(result['clients'][1]['internet_permission'], 'blocked')
        self.assertFalse(result['clients'][1]['protected'])

    def test_changed_committed_whitelist_does_not_claim_stale_rule_exemption(self):
        self.h.run()
        self.client_rows()
        self.config(white=('02:00:00:00:00:22',))
        result = self.run_rpc('call', 'clients')
        self.assertEqual(result['reason'], 'config_mismatch')
        self.assertFalse(result['active'])
        self.assertTrue(result['clients'][1]['protected'])
        self.assertEqual(result['clients'][1]['internet_permission'], 'unknown')

    def test_missing_selected_ap_hook_and_renamed_interface_are_unverified(self):
        self.h.run()
        self.config(networks=('guest','guest2'))
        (self.h.path/'ap-records').write_text('wlan-guest guest hostapd\nwlan-extra guest2 hostapd\n')
        self.run_rpc('call', 'status')
        self.assertEqual(self.last_rpc['reason'], 'config_mismatch')
        self.config()
        (self.h.path/'ap-records').write_text('wlan-new guest hostapd\n')
        self.run_rpc('call', 'status')
        self.assertEqual(self.last_rpc['reason'], 'config_mismatch')

    def test_live_counters_prefix_masking_and_numeric_priority_are_normalized(self):
        import ipaddress
        import re
        self.h.run()
        table = self.h.rules
        table = re.sub(r'ip6 daddr ([0-9a-fA-F:]+/[0-9]+) return',
                       lambda match: 'ip6 daddr '+str(ipaddress.ip_network(match.group(1), strict=False))+' return', table)
        table = table.replace('priority -500;', 'priority filter - 500;')
        table = table.replace('counter drop', 'counter packets 42 bytes 8400 drop')
        table = table.replace('0x0800,', '0x800,')
        (self.h.path/'nft-table').write_text(table)
        self.assertEqual(self.run_rpc('call', 'status'), self.expected('blocked', True))

    def test_empty_table_added_bypass_and_deleted_final_drop_are_rejected(self):
        self.h.run()
        original = self.h.rules
        for rules in ('table netdev wifi_client_limiter {\n}\n',
                      original.replace('  ether saddr', '  accept\n  ether saddr', 1),
                      original.replace('  ether type { 0x0800, 0x86dd } counter drop\n','')):
            with self.subTest(rules=rules):
                (self.h.path/'nft-table').write_text(rules)
                # A matching cache cannot make an unsafe rule template credible.
                (self.h.path/'state/internet-policy-applied').write_text(rules)
                self.run_rpc('call', 'status')
                self.assertFalse(self.last_rpc['active'])
                self.assertEqual(self.last_rpc['internet_state'], 'error')

    def test_cleanup_pending_is_unknown_after_allow_test_or_disabled_changes(self):
        self.h.run()
        self.client_rows()
        for config in ({'internet':1}, {'dry':1}, {'enabled':0}, {'networks':()}):
            with self.subTest(config=config):
                self.config(**config)
                result = self.run_rpc('call', 'clients')
                self.assertEqual(result['reason'], 'cleanup_pending')
                self.assertEqual(result['clients'][1]['internet_permission'], 'unknown')
                self.assertEqual(result['clients'][2]['internet_permission'], 'unknown')
                self.assertFalse(result['active'])

    def test_allowed_test_disabled_permissions_are_truthful_without_owned_rules(self):
        self.client_rows()
        for config, permission, reason in (({'internet':1},'allowed','guest_internet_allowed'),
                                           ({'dry':1},'test','test_mode'),
                                           ({'enabled':0},'not_enforced','disabled')):
            with self.subTest(config=config):
                self.config(**config)
                result = self.run_rpc('call', 'clients')
                self.assertEqual(result['reason'], reason)
                self.assertEqual(result['clients'][1]['internet_permission'], permission)
                self.assertFalse(result['active'])

    def test_partial_client_discovery_and_malformed_client_are_explicit(self):
        self.h.run()
        self.h.write('clients.json', {'clients': [
            {'mac':'02:00:00:00:00:22','ifname':'wlan-guest','section':'guest'},
            {'mac':'bad;touch owned','ifname':'wlan-guest','section':'guest'},
        ], 'unavailable': True})
        result = self.run_rpc('call', 'clients')
        self.assertTrue(result['unavailable'])
        self.assertEqual(len(result['clients']), 1)
        self.assertFalse((self.h.path/'owned').exists())
        (self.h.path/'clients.json').write_text('{bad json')
        result = self.run_rpc('call', 'clients')
        self.assertTrue(result['unavailable'])
        self.assertEqual(result['clients'], [])

    def test_committed_config_parser_decodes_quotes_without_executing_text(self):
        self.client_rows()
        (self.h.path/'committed.conf').write_text("""config limiter main
option enabled on
option dry_run 'false'
option guest_internet \"yes\"
list network 'guest' # comment
list whitelist '02:00:00:00:00:11'
option unused '$(touch owned)'
""")
        result = self.run_rpc('call', 'clients')
        self.assertEqual(result['reason'], 'guest_internet_allowed')
        self.assertFalse((self.h.path/'owned').exists())
        (self.h.path/'committed.conf').write_text("config limiter main\noption enabled maybe\n")
        result = self.run_rpc('call', 'clients')
        self.assertEqual(result['reason'], 'config_invalid')
        self.assertFalse(result['active'])

    def test_noncanonical_uci_policy_types_are_rejected(self):
        self.config()
        original = (self.h.path/'committed.conf').read_text()
        for source in (original.replace("option enabled", "list enabled"),
                       original.replace("list network", "option network"),
                       original.replace("list whitelist", "option whitelist")):
            with self.subTest(source=source):
                (self.h.path/'committed.conf').write_text(source)
                self.run_rpc('call', 'status')
                self.assertEqual(self.last_rpc['reason'], 'config_invalid')
                self.assertFalse(self.last_rpc['active'])

    def test_changed_runtime_local_prefixes_and_network_attachment_are_unverified(self):
        self.h.run()
        network = self.h.read('lan.json')
        network['ipv4-address'] = [{'address':'192.168.77.1','mask':24}]
        self.h.write('lan.json', network)
        self.run_rpc('call', 'status')
        self.assertEqual(self.last_rpc['reason'], 'config_mismatch')
        self.assertFalse(self.last_rpc['active'])
        wireless = self.h.read('wireless.json')
        wireless['radio0']['interfaces'][0]['config']['network'] = ['iot']
        self.h.write('wireless.json', wireless)
        self.run_rpc('call', 'status')
        self.assertEqual(self.last_rpc['reason'], 'config_mismatch')

    def test_missing_runtime_network_attachment_is_not_verified(self):
        self.h.run()
        wireless = self.h.read('wireless.json')
        del wireless['radio0']['interfaces'][0]['config']['network']
        self.h.write('wireless.json', wireless)
        self.run_rpc('call', 'status')
        self.assertEqual(self.last_rpc['reason'], 'network_status_failed')
        self.assertFalse(self.last_rpc['active'])

    def test_full_host_prefix_masks_have_same_proof_after_nft_canonicalization(self):
        self.h.write('lan.json', {'ipv4-address':[{'address':'192.168.50.1','mask':32}],
                                 'ipv6-address':[{'address':'fd42:50::1234','mask':128}]})
        self.h.run()
        table = self.h.rules.replace('192.168.50.1/32','192.168.50.1').replace('fd42:50::1234/128','fd42:50::1234')
        (self.h.path/'nft-table').write_text(table)
        self.assertEqual(self.run_rpc('call', 'status'), self.expected('blocked', True))

    def test_client_topology_race_cannot_claim_uncovered_interface_blocked(self):
        self.h.run()
        self.h.write('clients.json', {'clients': [
            {'mac':'02:00:00:00:00:22','ifname':'wlan-new','section':'guest'},
        ], 'unavailable': False})
        result = self.run_rpc('call', 'clients')
        self.assertEqual(result['internet_state'], 'blocked')
        self.assertEqual(result['clients'][0]['internet_permission'], 'unknown')
        self.assertEqual(result['clients'][0]['internet_reason'], 'config_mismatch')

    def test_residual_legacy_owned_table_does_not_claim_protected_device_exemption(self):
        self.h.run()
        self.client_rows()
        (self.h.path/'nft-old-inet').write_text('table inet wifi_client_limiter {}')
        result = self.run_rpc('call', 'clients')
        self.assertEqual(result['reason'], 'cleanup_pending')
        self.assertEqual(result['clients'][0]['internet_permission'], 'unknown')

    def test_anonymous_set_permutations_preserve_exact_live_policy_proof(self):
        self.h.run()
        table = self.h.rules
        table = table.replace('{ 0x8100, 0x88a8, 0x9100 }', '{ 0x9100, 0x8100, 0x88a8 }')
        table = table.replace('{ fe80::/10, ff01::/16, ff02::/16 }', '{ ff02::/16, fe80::/10, ff01::/16 }')
        table = table.replace('{ 0x0800, 0x86dd }', '{ 0x86dd, 0x800 }')
        (self.h.path/'nft-table').write_text(table)
        self.assertEqual(self.run_rpc('call', 'status'), self.expected('blocked', True))
        calls = [json.loads(line) for line in (self.h.path/'calls').read_text().splitlines()]
        self.assertIn(['nft','-s','-nn','-y','list','table','netdev','wifi_client_limiter'], calls)

    def test_anonymous_set_extra_duplicate_or_different_members_are_not_verified(self):
        self.h.run()
        original = self.h.rules
        for previous, replacement in (
            ('{ 0x8100, 0x88a8, 0x9100 }', '{ 0x8100, 0x88a8, 0x9100, 0xffff }'),
            ('{ fe80::/10, ff01::/16, ff02::/16 }', '{ fe80::/10, ff01::/16, ff01::/16 }'),
            ('{ fe80::/10, ff01::/16, ff02::/16 }', '{ fe80::/10, ff01::/16, ff03::/16 }'),
            ('{ 0x0800, 0x86dd }', '{ 0x86dd, 0x800, 0x8100 }'),
        ):
            with self.subTest(replacement=replacement):
                table = original.replace(previous,replacement)
                (self.h.path/'nft-table').write_text(table)
                # Even matching applied text must fail the exact rule-template proof.
                (self.h.path/'state/internet-policy-applied').write_text(table)
                self.run_rpc('call','status')
                self.assertEqual(self.last_rpc['reason'], 'rules_unverified')
                self.assertFalse(self.last_rpc['active'])

    def test_nft_merged_adjacent_ipv6_multicast_intervals_are_exactly_normalized(self):
        self.h.run()
        original = self.h.rules
        for values in ('fe80::/10, ff01::-ff02:ffff:ffff:ffff:ffff:ffff:ffff:ffff',
                       'ff01::-ff02:ffff:ffff:ffff:ffff:ffff:ffff:ffff, fe80::/10'):
            with self.subTest(values=values):
                (self.h.path/'nft-table').write_text(original.replace('fe80::/10, ff01::/16, ff02::/16', values))
                self.assertEqual(self.run_rpc('call','status'), self.expected('blocked', True))

    def test_changed_ipv6_multicast_interval_endpoints_and_duplicates_are_unverified(self):
        self.h.run()
        original = self.h.rules
        for values in ('fe80::/10, ff00::-ff02:ffff:ffff:ffff:ffff:ffff:ffff:ffff',
                       'fe80::/10, ff01::-ff03:ffff:ffff:ffff:ffff:ffff:ffff:ffff',
                       'fe80::/10, ff01::-ff02:ffff:ffff:ffff:ffff:ffff:ffff:fffe',
                       'fe80::/10, ff01::1-ff02:ffff:ffff:ffff:ffff:ffff:ffff:ffff',
                       'ff01::-ff02:ffff:ffff:ffff:ffff:ffff:ffff:ffff, ff01::-ff02:ffff:ffff:ffff:ffff:ffff:ffff:ffff'):
            with self.subTest(values=values):
                table = original.replace('fe80::/10, ff01::/16, ff02::/16', values)
                (self.h.path/'nft-table').write_text(table)
                (self.h.path/'state/internet-policy-applied').write_text(table)
                self.run_rpc('call','status')
                self.assertEqual(self.last_rpc['reason'], 'rules_unverified')
                self.assertFalse(self.last_rpc['active'])

if __name__ == '__main__':
    unittest.main(verbosity=2)

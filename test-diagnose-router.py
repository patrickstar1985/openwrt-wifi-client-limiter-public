#!/usr/bin/env python3
"""Run the report with actual firmware BusyBox/jsonfilter and read-only fixtures.

python3 test-diagnose-router.py --firmware-root ROOT --qemu qemu-aarch64
ROOT must contain bin/busybox, usr/bin/jsonfilter and their original libraries.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--firmware-root', type=Path)
parser.add_argument('--qemu')
options, remaining = parser.parse_known_args()
if bool(options.firmware_root) != bool(options.qemu):
    parser.error('--firmware-root and --qemu must be supplied together')
if options.firmware_root:
    options.firmware_root = options.firmware_root.resolve()
    options.qemu = str(Path(options.qemu).resolve()) if '/' in options.qemu else options.qemu

COMMAND = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ['DIAG_FIXTURE'])
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with (root/'calls').open('a') as output:
    output.write(json.dumps([name, *args])+'\n')
fixture = json.loads((root/'fixture.json').read_text())
result = None
if name == 'opkg' and args == ['status', 'luci-app-wifi-client-limiter']:
    result = (0, 'Package: luci-app-wifi-client-limiter\nVersion: 0.5.3-1\n')
elif name == 'uci' and len(args) == 3 and args[:2] == ['-q', 'get']:
    key = args[2]
    if key == 'wifi-client-limiter.main.network' or (key.startswith('wireless.') and key.rsplit('.',1)[-1] in ('mode','network')) or (key.startswith('firewall.') and key.rsplit('.',1)[-1] in ('flow_offloading','flow_offloading_hw','disable_ipv6')):
        result = (0, fixture['uci'][key]) if key in fixture['uci'] else (1, '')
elif name == 'uci' and args == ['-q','-X','show','firewall']:
    result = (fixture.get('firewall_show_status',0), fixture['firewall_show'])
elif name == 'uci' and len(args)==4 and args[:3]==['-q','-X','show'] and args[3].startswith('firewall.'):
    key=args[3]
    item=fixture.get('firewall_sections',{}).get(key)
    result=(0,item) if item is not None else (1,'')
elif name == 'uci' and args == ['-q','changes','firewall']:
    result = (fixture.get('firewall_changes_status',0), fixture['firewall_changes'])
elif name == 'ubus' and len(args) == 6 and args[:3] == ['-t','5','call'] and args[4:] == ['status','{}']:
    item = fixture['ubus'].get(args[3], {'status': 4})
    result = (item.get('status',0), item.get('raw',json.dumps(item.get('data',{}))))
elif name == 'ubus' and args == ['-t','5','call','service','list','{"name":"wifi-client-limiter"}']:
    item = fixture['ubus'].get('service', {'status': 4})
    result = (item.get('status',0), item.get('raw',json.dumps(item.get('data',{}))))
elif name == 'iwinfo' and len(args) == 2 and args[1] == 'assoclist':
    item = fixture['iwinfo'].get(args[0], {'status': 1})
    result = (item.get('status',0), item.get('data',''))
elif name in ('iptables-save','ip6tables-save') and args == ['-M','/bin/false']:
    item = fixture[name]
    result = (item.get('status',0), item.get('data',''))
    print(item.get('error',''), file=sys.stderr, end='')
if result is None:
    (root/'MUTATION_OR_UNEXPECTED_COMMAND').write_text(json.dumps([name,*args]))
    sys.exit(99)
print(result[1], end='')
sys.exit(result[0])
'''


@unittest.skipUnless(options.firmware_root, 'actual firmware runtime not supplied')
class RouterDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='wifi-limiter-diag-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        self.bin = self.path/'bin'
        self.bin.mkdir()
        self.state = self.path/'state'
        self.state.mkdir()
        self.backup = self.path/'backup'
        self.backup.mkdir()
        (self.path/'config').mkdir()
        (self.path/'config/firewall').write_text("""config defaults
 option flow_offloading '0'
 option flow_offloading_hw '0'
 option disable_ipv6 '0'
config rule 'unrelated'
 option dest_ip 'NEVER_PRINT_DESTINATION'
 option secret 'NEVER_PRINT_CONFIG_SECRET'
""")
        (self.backup/'offload-backup').write_text('cfg001 123456:24 flow_offloading 1\ncfg001 123456:24 flow_offloading_hw 1\n')
        (self.backup/'native-flowoffload-backup').write_text('1\n')
        (self.backup/'offload-reload-required').write_text('disable\n')
        (self.backup/'vendor-acceleration-backup').write_text('NEVER_PRINT_VENDOR_BACKUP\n')
        (self.state/'internet-uci-edits').write_text('NEVER_PRINT_OWNED_EDIT\n')
        self.modules = self.path/'sys-module'
        (self.modules/'ip_tables').mkdir(parents=True)
        (self.modules/'iptable_filter').mkdir()
        (self.modules/'xt_FLOWOFFLOAD/holders').mkdir(parents=True)
        (self.modules/'xt_FLOWOFFLOAD/refcnt').write_text('2\n')
        (self.modules/'xt_FLOWOFFLOAD/holders/NEVER_PRINT_HOLDER').touch()
        (self.path/'proc/123').mkdir(parents=True)
        (self.path/'proc/net').mkdir()
        (self.path/'proc/net/ip_tables_names').write_text('filter\nnat\nmangle\n')
        (self.path/'proc/net/ip6_tables_names').write_text('filter\n')
        (self.path/'proc/sys/net/ipv6/conf/all').mkdir(parents=True)
        (self.path/'proc/sys/net/ipv6/conf/all/disable_ipv6').write_text('0\n')
        (self.path/'proc/123/cmdline').write_bytes(b'/bin/sh\0/usr/sbin/wifi-client-limiter\0')
        (self.state/'service-status.json').write_text(json.dumps({
            'service_state': 'running', 'error': '', 'detail': '', 'pid': 123}))
        (self.state/'internet-status.json').write_text(json.dumps({
            'internet_state': 'error', 'active': False, 'error': 'network_status_failed',
            'offload_disabled': True, 'firewall_backend': 'fw3', 'nft_family': 'netdev',
            'secret': 'NEVER_PRINT_RECORDED_SECRET'}))
        (self.path/'calls').write_text('')
        for name in ('uci', 'ubus', 'iwinfo', 'opkg', 'iptables-save', 'ip6tables-save', 'fw3', 'fw4', 'rmmod', 'modprobe', 'insmod'):
            target = self.bin/name
            target.write_text(COMMAND)
            target.chmod(0o755)
        # Execute the exact firmware applets/decoder; only hardware responses
        # and absolute runtime paths are substituted, with no JSON codec shim.
        import shlex
        import shutil
        self.host_cksum = shutil.which('cksum')
        self.assertTrue(self.host_cksum, 'POSIX cksum is needed to model an installed checksum tool')
        invocation = f'{shlex.quote(options.qemu)} -L {shlex.quote(str(options.firmware_root))}'
        for name in ('cat', 'tr', 'sed', 'awk', 'sort'):
            target = self.bin/name
            target.write_text(f'#!/bin/sh\nexec {invocation} {shlex.quote(str(options.firmware_root/"bin/busybox"))} {name} "$@"\n')
            target.chmod(0o755)
        target = self.bin/'cksum'
        target.write_text(f'#!/bin/sh\nexec {shlex.quote(self.host_cksum)} "$@"\n')
        target.chmod(0o755)
        target = self.bin/'jsonfilter'
        target.write_text(f'#!/bin/sh\nexec {invocation} {shlex.quote(str(options.firmware_root/"usr/bin/jsonfilter"))} "$@"\n')
        target.chmod(0o755)
        source = (ROOT/'scripts/diagnose-router-format2.sh').read_text()
        source = source.replace('/tmp/wifi-client-limiter/service-status.json', str(self.state/'service-status.json'))
        source = source.replace('/tmp/wifi-client-limiter/internet-status.json', str(self.state/'internet-status.json'))
        source = source.replace('/tmp/wifi-client-limiter/internet-uci-edits', str(self.state/'internet-uci-edits'))
        source = source.replace('/etc/wifi-client-limiter', str(self.backup))
        source = source.replace('/etc/config/firewall', str(self.path/'config/firewall'))
        source = source.replace('/sys/module', str(self.modules))
        source = source.replace('/proc/net/', str(self.path/'proc/net')+'/')
        source = source.replace('/proc/sys/', str(self.path/'proc/sys')+'/')
        source = source.replace('/proc/$diag_pid/cmdline', str(self.path/'proc')+'/$diag_pid/cmdline')
        self.script = self.path/'diagnose.sh'
        self.script.write_text(source)
        self.fixture = {
            'uci': {'wifi-client-limiter.main.network': 'wifi2g wifi5g',
                    'wireless.wifi2g.mode': 'ap', 'wireless.wifi2g.network': 'lan',
                    'wireless.wifi5g.mode': 'ap', 'wireless.wifi5g.network': 'guest',
                    'firewall.cfg001.flow_offloading': '0', 'firewall.cfg001.flow_offloading_hw': '0',
                    'firewall.cfg001.disable_ipv6': '0'},
            'firewall_show': "firewall.cfg001=defaults\nfirewall.cfg001.flow_offloading='0'\nfirewall.rule0=rule\nfirewall.rule0.dest_ip='NEVER_PRINT_DESTINATION'\n",
            'firewall_sections': {'firewall.cfg001': "firewall.cfg001=defaults\nfirewall.cfg001.flow_offloading='0'\nfirewall.cfg001.flow_offloading_hw='0'\nfirewall.cfg001.disable_ipv6='0'\nfirewall.cfg001.log_prefix='NEVER_PRINT_FINGERPRINT_SECRET'\n"},
            'firewall_changes': '',
            'iptables-save': {'data': '*nat\n:POSTROUTING ACCEPT [0:0]\n-A POSTROUTING -d 198.51.100.2 -j LOG --log-prefix "NEVER_PRINT_LOG"\nCOMMIT\n*filter\n:FORWARD ACCEPT [3:204]\n-A FORWARD -d 203.0.113.8 -j FLOWOFFLOAD --hw\n-A FORWARD -m comment --comment "NEVER_PRINT_RULE_COMMENT" -j ACCEPT\nCOMMIT\n'},
            'ip6tables-save': {'data': '*filter\n:FORWARD DROP [4:256]\n-A FORWARD -d 2001:db8::5 -j FLOWOFFLOAD\nCOMMIT\n'},
            'ubus': {
                'service': {'data': {
                    'wifi-client-limiter': {'instances': {'instance1': {'running': True, 'pid': 123,
                        'command': ['NEVER_PRINT_COMMAND'], 'env': {'PRIVATE': 'NEVER_PRINT_ENV'}}}},
                    'unrelated-service': {'instances': {'instance1': {'running': True, 'pid': 999,
                        'command': ['NEVER_PRINT_OTHER_COMMAND'], 'env': {'PRIVATE': 'NEVER_PRINT_OTHER_ENV'}}}}}},
                'wifi-client-limiter': {'data': {'internet_state': 'error', 'active': False,
                    'error': 'network_status_failed', 'service_running': True}},
                'network.wireless': {'data': {
                    'radio0': {'config': {'type': 'mtk', 'ssid': 'NEVER_PRINT_SSID', 'key': 'NEVER_PRINT_PSK'},
                        'interfaces': [{'section': 'wifi2g', 'ifname': 'ra0', 'config': {
                            'mode': 'ap', 'network': ['lan'], 'ssid': 'NEVER_PRINT_SSID', 'key': 'NEVER_PRINT_PSK'}}]},
                    'radio1': {'config': {'type': 'mtk'}, 'interfaces': [
                        {'section': 'wifi5g', 'ifname': 'rax0', 'config': {'mode': 'ap', 'network': 'guest'}},
                        {'section': 'other', 'ifname': 'ra1', 'config': {'mode': 'ap', 'network': ['other']}}]}}},
                'network.interface.lan': {'data': {'up': True, 'l3_device': 'br-lan',
                    'ipv4-address': [{'address': '192.168.8.1', 'mask': 24}],
                    'ipv6-address': [], 'ipv6-prefix-assignment': []}},
                'network.interface.guest': {'data': {'up': True, 'l3_device': 'br-guest',
                    'ipv4-address': [{'address': '192.168.9.1', 'mask': 24}],
                    'ipv6-address': [{'address': 'fd00:9::1', 'mask': 64}],
                    'ipv6-prefix-assignment': [{'address': 'fd00:9::', 'mask': 64,
                        'local-address': {'address': 'fd00:9::1', 'mask': 64}}]}},
            },
            'iwinfo': {'ra0': {'data': '02:00:00:00:00:10  -40 dBm\n'},
                       'rax0': {'data': 'No information available\n'}}
        }

    def run_report(self):
        (self.path/'fixture.json').write_text(json.dumps(self.fixture))
        command_path = str(self.bin) if getattr(self, 'isolated_path', False) else str(self.bin)+':'+os.environ['PATH']
        done = subprocess.run([options.qemu, '-L', str(options.firmware_root),
            str(options.firmware_root/'bin/busybox'), 'ash', str(self.script)],
            env=dict(os.environ, PATH=command_path, DIAG_FIXTURE=str(self.path)),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse((self.path/'MUTATION_OR_UNEXPECTED_COMMAND').exists(), done.stdout)
        self.assertNotIn('NEVER_PRINT', done.stdout)
        self.calls = [json.loads(line) for line in (self.path/'calls').read_text().splitlines()]
        return done.stdout

    def test_complete_native_report_uses_only_selected_readonly_calls(self):
        output = self.run_report()
        for expected in ('version=0.5.3-1', 'service_pid=123',
                         'worker_argv0=/bin/sh', 'worker_argv1=/usr/sbin/wifi-client-limiter',
                         'procd_instance_running=true', 'procd_instance_pid=123',
                         'recorded_error=network_status_failed', 'radio_types=mtk',
                         'runtime_wifi2g_ifname=ra0', 'runtime_wifi5g_network=guest',
                         'logical_network=lan', 'logical_network=guest', 'fd00:9::1',
                         '02:00:00:00:00:10'):
            self.assertIn(expected, output)
        self.assertNotIn('logical_network=other', output)
        self.assertEqual([call for call in self.calls if call[0] == 'iwinfo'],
                         [['iwinfo','ra0','assoclist'], ['iwinfo','rax0','assoclist']])
        self.assertEqual({call[-1].rsplit('.',1)[-1] for call in self.calls
                          if call[0] == 'uci' and call[-1].startswith('wireless.')}, {'mode','network'})

    def test_native_acceleration_report_masks_rule_content_and_reads_recovery(self):
        output = self.run_report()
        for expected in ('format 2', 'committed_defaults_parse_status=0',
                         'committed_defaults_1_flow_offloading=0', 'committed_defaults_1_disable_ipv6=0',
                         'effective_defaults_1_flow_offloading=0', 'firewall_pending_present=0',
                         'effective_defaults_1_original=cfg001',
                         'offload_backup_format_valid=1', 'offload_backup_record_1_fingerprint=123456:24',
                         'native_flowoffload_backup=1', 'offload_reload_required=disable',
                         'vendor_acceleration_backup_present=1', 'owned_uci_edits_present=1',
                         'module_inventory_visible=1', 'xt_FLOWOFFLOAD_present=1',
                         'module_ip_tables_present=1', 'module_iptable_filter_present=1',
                         'module_ip6_tables_present=0', 'module_ip6table_filter_present=0',
                         'xt_FLOWOFFLOAD_refcnt=2', 'xt_FLOWOFFLOAD_holders_count=1',
                         'ipv6_disable_all=0', 'ipv4_registered_tables_filter=1',
                         'ip6tables_save_filter_shape_valid=1', 'iptables_save_forward_counters=[3:204]',
                         'iptables_save_filter_flowoffload_rules=1', 'iptables_save_filter_hardware_flowoffload_rules=1',
                         'ip6tables_save_filter_hardware_flowoffload_rules=0'):
            self.assertIn(expected, output)
        for secret in ('198.51.100.2', '203.0.113.8', '2001:db8::5'):
            self.assertNotIn(secret, output)
        saves = [call for call in self.calls if call[0] in ('iptables-save','ip6tables-save')]
        self.assertEqual(saves, [['iptables-save','-M','/bin/false'], ['ip6tables-save','-M','/bin/false']])
        self.assertFalse(any(call[0] in ('fw3','rmmod','modprobe','insmod') for call in self.calls))

    def test_pending_flags_do_not_masquerade_as_committed_values_or_print_other_edits(self):
        self.fixture['uci']['firewall.cfg001.flow_offloading'] = '1'
        self.fixture['firewall_changes'] = "firewall.cfg001.flow_offloading='1'\nfirewall.rule0.dest_ip='NEVER_PRINT_PENDING_DESTINATION'\nfirewall.rule0.log_prefix='NEVER_PRINT_PENDING_LOG'\n"
        output = self.run_report()
        for expected in ('committed_defaults_1_flow_offloading=0', 'effective_defaults_1_flow_offloading=1',
                         'firewall_pending_present=1', 'firewall_pending_lines=3',
                         'pending_acceleration_1_option=flow_offloading', 'pending_acceleration_1_value=1'):
            self.assertIn(expected, output)

    def test_effective_defaults_fingerprint_matches_production_without_disclosing_contents(self):
        output = self.run_report()
        snapshot = self.fixture['firewall_sections']['firewall.cfg001']
        lines = [line[len('firewall.cfg001'):] for line in snapshot.splitlines()
                 if line.startswith('firewall.cfg001')]
        lines = [line for line in lines if not line.startswith(('.flow_offloading=', '.flow_offloading_hw='))]
        checksum = subprocess.check_output([self.host_cksum], input=('\n'.join(sorted(lines))+'\n').encode()).decode().split()
        self.assertIn(f'effective_defaults_1_fingerprint={checksum[0]}:{checksum[1]}', output)
        self.assertIn('effective_defaults_1_fingerprint_status=0', output)

    def test_missing_checksum_and_module_tools_are_reported_without_running_them(self):
        import shutil
        self.isolated_path = True
        (self.bin/'python3').symlink_to(shutil.which('python3'))
        for name in ('cksum','rmmod','modprobe','insmod'):
            (self.bin/name).unlink()
        output = self.run_report()
        for expected in ('tool_cksum_available=0', 'effective_defaults_1_fingerprint_status=127',
                         'tool_rmmod_available=0', 'tool_modprobe_available=0', 'tool_insmod_available=0'):
            self.assertIn(expected, output)
        self.assertNotIn('effective_defaults_1_fingerprint=', output)

    def test_malformed_recovery_and_flag_values_are_summarized_without_disclosure(self):
        (self.backup/'offload-backup').write_text('cfg001 123:24 flow_offloading NEVER_PRINT_BACKUP\n')
        (self.backup/'native-flowoffload-backup').write_text('NEVER_PRINT_MODULE_BACKUP\n')
        (self.backup/'offload-reload-required').write_text('NEVER_PRINT_RELOAD_MARKER\n')
        self.fixture['uci']['firewall.cfg001.flow_offloading'] = 'NEVER_PRINT_EFFECTIVE'
        self.fixture['firewall_changes'] = "firewall.cfg001.flow_offloading='NEVER_PRINT_PENDING'\n"
        (self.path/'config/firewall').write_text("config defaults\n option flow_offloading 'NEVER_PRINT_COMMITTED'\n")
        output = self.run_report()
        for expected in ('offload_backup_format_valid=0', 'offload_backup_invalid_records=1',
                         'native_flowoffload_backup_value_status=invalid', 'offload_reload_required_value_status=invalid',
                         'effective_defaults_1_flow_offloading_value_status=invalid',
                         'pending_acceleration_1_value=invalid', 'committed_defaults_1_flow_offloading=invalid'):
            self.assertIn(expected, output)

    def test_legacy_missing_fingerprint_shape_is_visible_but_remains_invalid(self):
        (self.backup/'offload-backup').write_text('cfg001  flow_offloading 1\ncfg001  flow_offloading_hw -\n')
        output = self.run_report()
        for expected in ('offload_backup_record_1_legacy_missing_fingerprint=1',
                         'offload_backup_record_1_original=cfg001', 'offload_backup_record_1_option=flow_offloading',
                         'offload_backup_record_1_value=1', 'offload_backup_record_2_value=-',
                         'offload_backup_legacy_missing_fingerprint_records=2',
                         'offload_backup_invalid_records=2', 'offload_backup_format_valid=0'):
            self.assertIn(expected, output)
        self.assertNotIn('offload_backup_record_1_fingerprint=', output)

    def test_unrecognized_three_field_backups_never_disclose_values(self):
        (self.backup/'offload-backup').write_text('cfg001 flow_offloading NEVER_PRINT_LEGACY_VALUE\ncfg001 NEVER_PRINT_LEGACY_OPTION 1\n')
        output = self.run_report()
        self.assertIn('offload_backup_legacy_missing_fingerprint_records=0', output)
        self.assertIn('offload_backup_invalid_records=2', output)
        self.assertIn('offload_backup_format_valid=0', output)

    def test_empty_family_and_ipv6_disabled_are_not_false_firewall_proof(self):
        (self.path/'proc/net/ip6_tables_names').write_text('')
        (self.path/'proc/sys/net/ipv6/conf/all/disable_ipv6').write_text('1\n')
        self.fixture['ip6tables-save']['data'] = ''
        output = self.run_report()
        for expected in ('ipv6_disable_all=1', 'ipv6_registered_tables_read_status=0',
                         'ipv6_registered_tables_filter=0', 'ip6tables_save_status=0',
                         'ip6tables_save_filter_shape_valid=0', 'ip6tables_save_filter_headers=0'):
            self.assertIn(expected, output)

    def test_failed_saves_and_invisible_module_inventory_remain_unknown(self):
        import shutil
        shutil.rmtree(self.modules)
        (self.path/'proc/net/ip6_tables_names').unlink()
        self.fixture['iptables-save'] = {'status': 1, 'data': 'NEVER_PRINT_FAILED_STDOUT', 'error': 'NEVER_PRINT_ERROR'}
        self.fixture['ip6tables-save'] = {'status': 4, 'error': 'NEVER_PRINT_IPV6_ERROR'}
        output = self.run_report()
        for expected in ('module_inventory_visible=0', 'xt_FLOWOFFLOAD_present=unknown',
                         'iptables_save_status=1', 'ip6tables_save_status=4', 'ipv6_registered_tables_read_status=1'):
            self.assertIn(expected, output)
        self.assertNotIn('iptables_save_filter_shape_valid=', output)
        self.assertNotIn('module_ip6table_filter_present=', output)

    def test_absent_replies_are_not_reported_as_successful_empty_data(self):
        (self.bin/'fw3').unlink()
        (self.state/'service-status.json').unlink()
        self.fixture['ubus']['network.wireless'] = {'status': 4}
        self.fixture['ubus']['wifi-client-limiter'] = {'status': 6}
        self.fixture['ubus']['network.interface.guest'] = {'status': 4}
        output = self.run_report()
        self.assertIn('service_status_read_status=1', output)
        self.assertIn('wireless_ubus_status=4', output)
        self.assertIn('limiter_ubus_status=6', output)
        self.assertIn('logical_ubus_status=4', output)
        self.assertFalse(any(call[0] == 'iwinfo' for call in self.calls))

    def test_invalid_json_does_not_reuse_the_previous_decode(self):
        (self.bin/'fw3').unlink()
        (self.state/'service-status.json').write_text('{invalid')
        (self.state/'internet-status.json').write_text('{invalid')
        self.fixture['ubus']['network.wireless'] = {'raw': '{invalid'}
        self.fixture['ubus']['wifi-client-limiter'] = {'raw': '{invalid'}
        output = self.run_report()
        self.assertIn('service_pid_status=1', output)
        self.assertIn('limiter_error_status=1', output)
        self.assertIn('recorded_error_status=1', output)
        self.assertIn('wireless_sections_status=1', output)
        self.assertNotIn('service_pid=123', output)
        self.assertFalse(any(call[0] == 'iwinfo' for call in self.calls))

    def test_fw3_skips_live_rpc_and_preserves_recorded_offload_failure(self):
        (self.state/'internet-status.json').write_text(json.dumps({
            'internet_state': 'error', 'error': 'offload_disable_failed',
            'private': 'NEVER_PRINT_RECORDED_PRIVATE'}))
        output = self.run_report()
        self.assertIn('limiter_ubus_status=skipped_fw3_table_query_may_load_modules', output)
        self.assertIn('recorded_error=offload_disable_failed', output)
        self.assertFalse(any(call[:5] == ['ubus','-t','5','call','wifi-client-limiter'] for call in self.calls))

    def test_stale_pid_does_not_disclose_another_process_arguments(self):
        (self.path/'proc/123/cmdline').write_bytes(b'/usr/bin/other\0--password\0NEVER_PRINT_SECRET\0')
        output = self.run_report()
        self.assertIn('worker_cmdline_status=unrecognized_process_arguments_omitted', output)
        self.assertNotIn('--password', output)

    def test_script_name_suffixes_never_disclose_private_arguments(self):
        for command in (
            b'/bin/sh\0/usr/sbin/wifi-client-limiter:NEVER_PRINT_SECRET\0',
            b'/usr/sbin/wifi-client-limiter:NEVER_PRINT_SECRET\0',
            b'/bin/busybox\0sh\0/usr/sbin/wifi-client-limiter:NEVER_PRINT_SECRET\0',
        ):
            with self.subTest(command=command):
                (self.path/'proc/123/cmdline').write_bytes(command)
                output = self.run_report()
                self.assertIn('worker_cmdline_status=unrecognized_process_arguments_omitted', output)
                self.assertNotIn('worker_argv', output)

    def test_procd_running_worker_is_visible_when_lifecycle_pid_is_zero(self):
        (self.state/'service-status.json').write_text(json.dumps({'service_state': 'stopped', 'pid': 0}))
        output = self.run_report()
        self.assertIn('service_pid=0', output)
        self.assertIn('procd_instance_running=true', output)
        self.assertIn('procd_instance_pid=123', output)
        self.assertIn(['ubus','-t','5','call','service','list','{"name":"wifi-client-limiter"}'], self.calls)

    def test_unrelated_procd_service_is_not_reported_as_the_limiter(self):
        del self.fixture['ubus']['service']['data']['wifi-client-limiter']
        output = self.run_report()
        self.assertIn('procd_instance_running_status=1', output)
        self.assertIn('procd_instance_pid_status=1', output)
        self.assertNotIn('procd_instance_pid=999', output)
        self.assertNotIn('unrelated-service', output)

    def test_exact_recognized_commands_omit_extra_arguments(self):
        for command, expected in (
            (b'/usr/sbin/wifi-client-limiter\0NEVER_PRINT_SECRET\0',
             'worker_argv0=/usr/sbin/wifi-client-limiter'),
            (b'/bin/ash\0/usr/sbin/wifi-client-limiter\0NEVER_PRINT_SECRET\0',
             'worker_argv1=/usr/sbin/wifi-client-limiter'),
            (b'/bin/busybox\0ash\0/usr/sbin/wifi-client-limiter\0NEVER_PRINT_SECRET\0',
             'worker_argv2=/usr/sbin/wifi-client-limiter'),
        ):
            with self.subTest(command=command):
                (self.path/'proc/123/cmdline').write_bytes(command)
                output = self.run_report()
                self.assertIn(expected, output)

    def test_shell_metacharacters_cannot_become_a_query_or_command(self):
        self.fixture['uci']['wifi-client-limiter.main.network'] += " bad';touch${IFS}/tmp/NEVER_PRINT"
        self.fixture['ubus']['network.wireless']['data']['radio0']['interfaces'].append({
            'section': "bad';anything", 'ifname': 'ra9', 'config': {'mode': 'ap', 'network': 'lan'}})
        output = self.run_report()
        self.assertIn('selected_section_name_invalid=1', output)
        self.assertIn('wireless_section_name_invalid=1', output)
        self.assertFalse(any('bad' in str(call) for call in self.calls))

    def test_runtime_network_attachment_is_included_without_changing_uci(self):
        self.fixture['ubus']['network.wireless']['data']['radio0']['interfaces'][0]['config']['network'] = ['runtime']
        self.fixture['ubus']['network.interface.runtime'] = {'data': {'up': False}}
        output = self.run_report()
        self.assertIn('logical_network=runtime', output)
        self.assertIn('uci_wifi2g_network=lan', output)
        self.assertTrue(any(call[0] == 'ubus' and call[4] == 'network.interface.runtime' for call in self.calls))

    def test_missing_runtime_mode_still_queries_known_selected_ap(self):
        del self.fixture['ubus']['network.wireless']['data']['radio0']['interfaces'][0]['config']['mode']
        output = self.run_report()
        self.assertIn('runtime_wifi2g_mode_status=1', output)
        self.assertIn('02:00:00:00:00:10', output)
        self.assertIn(['iwinfo', 'ra0', 'assoclist'], self.calls)


if __name__ == '__main__':
    unittest.main(argv=[__file__, *remaining])

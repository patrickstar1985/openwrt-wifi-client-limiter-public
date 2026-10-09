#!/usr/bin/env python3
"""Run the actual station adapter and worker against vendor-driver API fixtures."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
ADAPTER = (ROOT / 'files/usr/share/wifi-client-limiter/station-adapter.sh').read_text()
SOURCE = (ROOT / 'files/usr/sbin/wifi-client-limiter').read_text()
BODY = SOURCE[SOURCE.index('log()'):SOURCE.index('# procd runs')]
spec = importlib.util.spec_from_file_location('cooldown_fixtures', ROOT / 'test-cooldown.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
A = 'aa:00:00:00:00:01'
B = '00:00:00:00:00:02'
C = '00:00:00:00:00:03'
HASH = 'ed66a1502e819e4cc8424e50ce9c982905dabd1053bdfa7c47a7ec571652cb77'
GL483_HASH = '0b23162074e8ce928d86ae8438297de173b71f7a1f90398f0ad7f63b88c5a7e3'
KNOWN_DRIVER = ADAPTER[ADAPTER.index('station_mtk_known_driver()'):ADAPTER.index('station_mtk_stock_profile()')]

SHIM = fixtures.SHIM + r'''
uci() {
 case "$*" in
  '-q get wifi-client-limiter.main.network') printf '%s\n' "$NETWORKS";;
  '-q get wireless.guest.mode'|'-q get wireless.admin.mode'|'-q get wireless.native.mode') printf 'ap\n';;
  '-q get wireless.guest.device'|'-q get wireless.admin.device') printf 'mtk0\n';;
  '-q get wireless.native.device') printf 'radio0\n';;
  '-q get wireless.mtk0.type') printf 'mtk\n';;
  '-q get wireless.radio0.type') printf 'mac80211\n';;
  *) return 1;;
 esac
}
ubus() {
 printf '%s\t%s\t%s\n' "$2" "$3" "${4:-}" >> "$FIX/calls"
 if [ "$2" = network.wireless ]; then
  [ "$WIRELESS_FAIL" = 1 ] && return 1
  cat "$FIX/wireless.json"; return
 fi
 if [ "$3" = get_clients ]; then
  [ "$GET_FAIL" = "$2" ] && return 1
  cat "$FIX/$2.json"; return
 fi
 return 1
}
iwinfo() {
 printf 'iwinfo %s\n' "$*" >> "$FIX/driver-calls"
 [ "$IWINFO_FAIL" = "$1" ] && return 1
 cat "$FIX/$1.assoclist"
}
# This older shell-control fixture keeps raw MAC object keys. The standalone
# stock-jshn suite covers C key sanitation and original-key jsonfilter proofs.
jsonfilter() { return 1; }
iwpriv() {
 printf 'iwpriv %s\n' "$*" >> "$FIX/driver-calls"
 python3 - "$1" "$2" "$3" <<'END'
import json, os, pathlib, re, sys
path = pathlib.Path(os.environ['FIX'])
iface, command, value = sys.argv[1:]
if command != 'set':
    sys.exit(255)
operation, mac = value.split('=', 1)
if operation == 'BlShow':
    sys.exit(0)
if os.environ.get('DRIVER_FAILURE') == operation:
    print('Operation not supported', file=sys.stderr)
    sys.exit(255)
blacklist_path = path / 'native-blacklists.json'
blacklists = json.loads(blacklist_path.read_text()) if blacklist_path.exists() else {}
rows = (path / f'{iface}.assoclist').read_text().splitlines()
associated = any(row.split() and row.split()[0].lower() == mac for row in rows)
if operation == 'BlAdd':
    # Verify crash recovery metadata is already durable before driver mutation.
    journal = (path / 'state/station-bans').read_text()
    if not any(row.startswith(f'{iface} {mac} ') for row in journal.splitlines()):
        print('missing ownership journal', file=sys.stderr)
        sys.exit(2)
    if os.environ.get('OOM_ON_ADD') == '1':
        (path / 'kernel-log').write_text('[1000.000002] map.c:1034 assert pBlEntry != NULLfailed\n')
    else:
        entries = blacklists.setdefault(iface, [])
        if mac not in entries:
            entries.append(mac)
    blacklist_path.write_text(json.dumps(blacklists))
elif operation == 'BlDel':
    blacklists[iface] = [entry for entry in blacklists.get(iface, []) if entry != mac]
    blacklist_path.write_text(json.dumps(blacklists))
    sys.exit(0)
elif operation != 'DisConnectSta':
    sys.exit(255)
if os.environ.get('STILL_ASSOCIATED') != iface:
    rows = [row for row in rows if not row.split() or row.split()[0].lower() != mac]
    if not any(row.split() and re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', row.split()[0]) for row in rows):
        rows = ['No station connected']
    (path / f'{iface}.assoclist').write_text('\n'.join(rows) + '\n')
if operation == 'BlAdd' and not associated:
    print('Invalid argument', file=sys.stderr)
    sys.exit(255)
END
}
iw() { [ -f "$FIX/$2.iw" ] && cat "$FIX/$2.iw"; return 0; }
dmesg() {
 [ "$KERNEL_LOG_FAIL" != 1 ] || return 1
 [ ! -f "$FIX/kernel-log" ] || cat "$FIX/kernel-log"
 return 0
}
station_mtk_known_driver() {
 [ "$DRIVER_KNOWN" = 1 ] || { station_mtk_log 'Unsupported driver fixture'; return 1; }
 printf '%s\n' "$DRIVER_HASH"
}
station_mtk_stock_profile() { [ "$PROFILE_GOOD" = 1 ]; }
json_init() { export SERVICE_STATE= SERVICE_ERROR= SERVICE_DETAIL= SERVICE_PID=0; }
json_add_string() {
 case "$1" in
  service_state) export SERVICE_STATE="$2";;
  error) export SERVICE_ERROR="$2";;
  detail) export SERVICE_DETAIL="$2";;
  *) return 1;;
 esac
}
json_add_int() { [ "$1" = pid ] || return 1; export SERVICE_PID="$2"; }
json_dump() {
 python3 - <<'END'
import json, os
print(json.dumps({'service_state': os.environ['SERVICE_STATE'], 'error': os.environ['SERVICE_ERROR'],
                  'detail': os.environ['SERVICE_DETAIL'], 'pid': int(os.environ['SERVICE_PID'])}))
END
}
'''


class StationAdapterTests(unittest.TestCase):
    driver_hash = HASH

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='wifi-limiter-mtk-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        (self.path / 'state').mkdir()
        self.wireless = {
            'mtk0': {'config': {'type': 'mtk'}, 'interfaces': [
                {'section': 'guest', 'ifname': 'ra0', 'config': {'mode': 'ap'}},
                {'section': 'admin', 'ifname': 'ra1', 'config': {'mode': 'ap'}},
                {'section': 'station', 'ifname': 'apcli0', 'config': {'mode': 'sta'}},
            ]},
            'radio0': {'config': {'type': 'mac80211'}, 'interfaces': [
                {'section': 'native', 'ifname': 'wlan0', 'config': {'mode': 'ap'}},
            ]},
        }
        self.save_wireless()
        (self.path / 'ra0.assoclist').write_text(f'{A.upper()}  -45 dBm / -90 dBm (SNR 45)\n\tRX: 144.4 MBit/s\n')
        (self.path / 'ra1.assoclist').write_text(f'{B}  -50 dBm / -90 dBm (SNR 40)\n')
        (self.path / 'hostapd.wlan0.json').write_text(json.dumps({'clients': {C: {}}}))

    def save_wireless(self):
        (self.path / 'wireless.json').write_text(json.dumps(self.wireless))

    def run_shell(self, command, *, body=False, **overrides):
        for name in ('calls', 'log', 'driver-calls'):
            (self.path / name).write_text('')
        harness = self.path / 'run.sh'
        harness.write_text('STATE="$FIX/state"\n' + ADAPTER + '\n' +
                           (BODY if body else '') + '\n' + SHIM + '\n' + command + '\n')
        environment = dict(os.environ, FIX=str(self.path), ENABLED='1', DRY='0', MAX='25', SESSION='0',
                           INTERVAL='10', COOLDOWN='0', COOLDOWN_PRESENT='1', WINDOW='0', WINDOW_PRESENT='1',
                           NETWORKS='guest', WHITE='', GET_FAIL='', IWINFO_FAIL='', WIRELESS_FAIL='0', NOW='1000',
                           DRIVER_KNOWN='1', DRIVER_HASH=self.driver_hash, PROFILE_GOOD='1', DRIVER_FAILURE='', OOM_ON_ADD='0', KERNEL_LOG_FAIL='0',
                           STILL_ASSOCIATED='', INTERFACES='ra0 ra1')
        environment.update({key: str(value) for key, value in overrides.items()})
        return subprocess.run(['sh', str(harness)], env=environment, capture_output=True,
                              text=True, check=True, timeout=20)

    def clients(self, **overrides):
        return json.loads(self.run_shell('station_clients_json', **overrides).stdout)

    def driver_fingerprint_fixture(self):
        (self.path / 'driver-module').write_bytes(b'driver fingerprint fixture')
        return KNOWN_DRIVER.replace('"/lib/modules/$kernel/mt_wifi.ko"', '"$FIX/driver-module"') + r'''
sha256sum() { printf '%s  %s\n' "$DRIVER_HASH" "$1"; }
'''

    def test_production_fingerprint_accepts_audited_profile(self):
        result = self.run_shell(self.driver_fingerprint_fixture() + '\nstation_mtk_known_driver')
        self.assertEqual(result.stdout, self.driver_hash + '\n')
        self.assertEqual((self.path / 'driver-calls').read_text(), '')

    def test_production_fingerprint_rejects_unverified_module(self):
        self.run_shell(self.driver_fingerprint_fixture() + '\nif station_mtk_known_driver; then exit 8; fi',
                       DRIVER_HASH='0' * 64)
        self.assertIn('Unsupported MTK WiFi driver', (self.path / 'log').read_text())
        self.assertEqual((self.path / 'driver-calls').read_text(), '')

    def test_runtime_profile_error_stops_enforcement_and_keeps_worker_diagnosis(self):
        self.run_shell('if cycle; then exit 8; fi', body=True, PROFILE_GOOD=0, MAX=1, SESSION=30)
        state = json.loads((self.path / 'state/service-status.json').read_text())
        self.assertEqual(state['service_state'], 'error')
        self.assertGreater(state['pid'], 0)
        self.assertNotIn('iwpriv', (self.path / 'driver-calls').read_text())
        self.assertNotIn('get_clients', (self.path / 'calls').read_text())

    def test_runtime_profile_recovery_clears_the_previous_error(self):
        self.run_shell('if cycle; then exit 8; fi', body=True, PROFILE_GOOD=0)
        self.run_shell('cycle', body=True, PROFILE_GOOD=1)
        state = json.loads((self.path / 'state/service-status.json').read_text())
        self.assertEqual(state['service_state'], 'running')
        self.assertEqual(state['error'], '')
        self.assertIn('iwinfo ra0', (self.path / 'driver-calls').read_text())

    def test_all_ap_clients_include_unselected_operator_ap(self):
        self.assertEqual(self.clients(), {'clients': [
            {'mac': A, 'ifname': 'ra0', 'section': 'guest'},
            {'mac': B, 'ifname': 'ra1', 'section': 'admin'},
            {'mac': C, 'ifname': 'wlan0', 'section': 'native'},
        ], 'unavailable': False})
        calls = (self.path / 'driver-calls').read_text()
        self.assertNotIn('apcli0', calls)
        self.assertNotIn('iwpriv', calls)
        self.assertNotIn('hostapd.ra', (self.path / 'calls').read_text())

    def test_empty_vendor_ap_is_successfully_empty(self):
        (self.path / 'ra0.assoclist').write_text('No station connected\n')
        result = self.clients()
        self.assertFalse(result['unavailable'])
        self.assertEqual(len(result['clients']), 2)

    def test_partial_failure_preserves_other_ap_clients(self):
        result = self.clients(IWINFO_FAIL='ra0')
        self.assertTrue(result['unavailable'])
        self.assertEqual([row['mac'] for row in result['clients']], [B, C])

    def test_zero_exit_iwinfo_error_is_not_a_successful_empty_ap(self):
        (self.path / 'ra0.assoclist').write_text('No information available\n')
        result = self.clients()
        self.assertTrue(result['unavailable'])
        self.assertEqual([row['mac'] for row in result['clients']], [B, C])

    def test_unrecognized_empty_output_is_not_a_successful_empty_ap(self):
        (self.path / 'ra0.assoclist').write_text('')
        self.assertTrue(self.clients()['unavailable'])

    def test_invalid_native_json_is_truthfully_unavailable(self):
        (self.path / 'hostapd.wlan0.json').write_text('{broken')
        result = self.clients()
        self.assertTrue(result['unavailable'])
        self.assertEqual([row['mac'] for row in result['clients']], [A, B])

    def test_wireless_failure_is_truthfully_unavailable(self):
        self.assertEqual(self.clients(WIRELESS_FAIL='1'), {'clients': [], 'unavailable': True})

    def test_missing_driver_type_uses_configured_radio_type(self):
        self.wireless['mtk0'].pop('config')
        self.save_wireless()
        self.assertEqual(self.clients()['clients'][0]['mac'], A)

    def test_unknown_driver_never_silently_uses_vendor_operations(self):
        self.wireless['radio0']['config']['type'] = 'unknown'
        self.save_wireless()
        result = self.clients(GET_FAIL='hostapd.wlan0')
        self.assertTrue(result['unavailable'])
        self.assertNotIn('iwinfo wlan0', (self.path / 'driver-calls').read_text())

    def test_malicious_names_and_macs_are_not_executed_or_returned(self):
        self.wireless['mtk0']['interfaces'].extend([
            {'section': 'invalid', 'ifname': 'ra2;touch', 'config': {'mode': 'ap'}},
            {'section': 'guest"', 'ifname': 'ra3', 'config': {'mode': 'ap'}},
        ])
        self.save_wireless()
        (self.path / 'ra0.assoclist').write_text(
            f'{A.upper()} -45 dBm\nAA:00:00:00:00:ZZ -40 dBm\naa:00:00:00:00:02;touch -40 dBm\n')
        result = self.clients()
        self.assertEqual(len(result['clients']), 3)
        self.assertNotIn('touch', (self.path / 'driver-calls').read_text())

    def test_worker_records_only_selected_vendor_clients(self):
        self.run_shell('cycle', body=True)
        self.assertEqual((self.path / 'state/current').read_text(), f'{A} ra0 1000 0\n')
        self.assertNotIn('ra1', (self.path / 'driver-calls').read_text())

    def test_vendor_age_falls_back_to_first_complete_observation(self):
        self.run_shell('cycle', body=True)
        self.run_shell('cycle', body=True, NOW=1050)
        self.assertEqual((self.path / 'state/current').read_text(), f'{A} ra0 1000 50\n')

    def test_protected_vendor_client_is_not_counted(self):
        self.run_shell('cycle', body=True, WHITE=A.upper(), MAX=1)
        self.assertEqual((self.path / 'state/current').read_text(), '')
        self.assertNotIn('iwpriv', (self.path / 'driver-calls').read_text())

    def test_failed_vendor_snapshot_does_not_clean_previous_age(self):
        self.run_shell('cycle', body=True)
        self.run_shell('cycle', body=True, IWINFO_FAIL='ra0', NOW=1050, MAX=1)
        self.assertEqual((self.path / 'state'/f'ra0.{A}').read_text(), '1000\n')
        self.assertNotIn('iwpriv', (self.path / 'driver-calls').read_text())

    def test_dry_run_routes_vendor_evictions_without_driver_mutation(self):
        (self.path / 'state'/f'ra0.{A}').write_text('800\n')
        self.run_shell('cycle', body=True, DRY=1, SESSION=30, COOLDOWN=60)
        self.assertIn('TEST: Session limit', (self.path / 'log').read_text())
        self.assertNotIn('iwpriv', (self.path / 'driver-calls').read_text())

    def blacklist(self):
        path = self.path / 'native-blacklists.json'
        return json.loads(path.read_text()) if path.exists() else {}

    def hold(self, iface='ra0', mac=A, seconds=60, **overrides):
        return self.run_shell(f'station_mtk_kick {iface} {mac} {seconds}', **overrides)

    def test_associated_native_blacklist_is_verified_and_journaled(self):
        self.hold()
        self.assertEqual(self.blacklist(), {'ra0': [A]})
        self.assertEqual((self.path / 'state/station-bans').read_text(), f'ra0 {A} 1060 {self.driver_hash}\n')
        self.assertEqual((self.path / 'state/station-driver-verified').read_text(), f'{self.driver_hash}\n')
        self.assertNotIn(A, (self.path / 'ra0.assoclist').read_text().lower())
        self.assertNotIn('AccessPolicy', (self.path / 'driver-calls').read_text())
        self.assertNotIn('ACL', (self.path / 'driver-calls').read_text())

    def test_zero_pause_uses_disconnect_without_changing_blacklist(self):
        self.hold(seconds=0)
        self.assertEqual(self.blacklist(), {})
        self.assertNotIn('BlAdd', (self.path / 'driver-calls').read_text())
        self.assertIn('DisConnectSta=', (self.path / 'driver-calls').read_text())

    def test_absent_cross_ap_add_is_accepted_only_after_verified_primary(self):
        self.hold()
        self.run_shell(f'station_mtk_ban ra1 {A} 60')
        self.assertEqual(self.blacklist(), {'ra0': [A], 'ra1': [A]})
        self.assertIn(f'ra1 {A} 1060 {self.driver_hash}', (self.path / 'state/station-bans').read_text())

    def test_absent_add_without_primary_proof_has_no_mutation(self):
        self.run_shell(f'if station_mtk_ban ra1 {A} 60; then exit 8; fi')
        self.assertEqual(self.blacklist(), {})
        self.assertNotIn('BlAdd', (self.path / 'driver-calls').read_text())

    def test_unknown_driver_does_not_disconnect_or_add_any_entry(self):
        self.run_shell(f'if station_mtk_kick ra0 {A} 60; then exit 8; fi', DRIVER_KNOWN='0')
        self.assertEqual(self.blacklist(), {})
        self.assertNotIn('iwpriv', (self.path / 'driver-calls').read_text())

    def test_non_stock_profile_does_not_disconnect_or_add_any_entry(self):
        self.run_shell(f'if station_mtk_kick ra0 {A} 60; then exit 8; fi', PROFILE_GOOD='0')
        self.assertEqual(self.blacklist(), {})
        self.assertNotIn('iwpriv', (self.path / 'driver-calls').read_text())

    def test_failed_add_keeps_journal_without_claiming_success(self):
        self.run_shell(f'if station_mtk_kick ra0 {A} 60; then exit 8; fi', DRIVER_FAILURE='BlAdd')
        self.assertEqual(self.blacklist(), {})
        self.assertIn(f'ra0 {A} 1060 {self.driver_hash}', (self.path / 'state/station-bans').read_text())
        self.assertFalse((self.path / 'state/station-driver-verified').exists())

    def test_unclassified_absent_failure_is_not_accepted_as_a_ban(self):
        self.hold()
        self.run_shell(f'if station_mtk_ban ra1 {A} 60; then exit 8; fi', DRIVER_FAILURE='BlAdd')
        self.assertEqual(self.blacklist(), {'ra0': [A]})

    def test_command_success_without_disassociation_is_not_claimed(self):
        self.run_shell(f'if station_mtk_kick ra0 {A} 60; then exit 8; fi', STILL_ASSOCIATED='ra0')
        self.assertFalse((self.path / 'state/station-driver-verified').exists())
        self.assertEqual(self.blacklist(), {'ra0': [A]})

    def test_leases_expire_without_altering_unrelated_native_entries(self):
        self.hold()
        native = self.blacklist()
        native['ra0'].append(C)
        (self.path / 'native-blacklists.json').write_text(json.dumps(native))
        self.run_shell('station_mtk_prune', NOW=1059)
        self.assertEqual(self.blacklist()['ra0'], [A, C])
        self.run_shell('station_mtk_prune', NOW=1060)
        self.assertEqual(self.blacklist()['ra0'], [C])
        self.assertEqual((self.path / 'state/station-bans').read_text(), '')

    def test_cleanup_removes_only_owned_entries_and_clears_proof(self):
        self.hold()
        self.run_shell(f'station_mtk_ban ra1 {A} 60')
        self.run_shell('station_mtk_cleanup')
        self.assertEqual(self.blacklist(), {'ra0': [], 'ra1': []})
        self.assertEqual((self.path / 'state/station-bans').read_text(), '')
        self.assertFalse((self.path / 'state/station-driver-verified').exists())

    def test_cleanup_failure_retains_journal_and_can_be_retried(self):
        self.hold()
        self.run_shell('if station_mtk_cleanup; then exit 8; fi', DRIVER_FAILURE='BlDel')
        self.assertIn(f'ra0 {A}', (self.path / 'state/station-bans').read_text())
        self.run_shell('station_mtk_cleanup')
        self.assertEqual(self.blacklist()['ra0'], [])

    def test_already_absent_driver_entry_is_successfully_cleaned(self):
        self.hold()
        (self.path / 'native-blacklists.json').write_text('{}')
        self.run_shell('station_mtk_cleanup')
        self.assertEqual((self.path / 'state/station-bans').read_text(), '')

    def test_driver_reload_reapplies_owned_ban_without_extending_deadline(self):
        self.hold()
        (self.path / 'native-blacklists.json').write_text('{}')
        (self.path / 'ra0.assoclist').write_text(f'{A} -40 dBm\n')
        self.run_shell('white="$WHITE"; interfaces="$INTERFACES"; station_mtk_prune configured', NOW=1030)
        self.assertEqual(self.blacklist()['ra0'], [A])
        self.assertIn(f'ra0 {A} 1060 {self.driver_hash}', (self.path / 'state/station-bans').read_text())

    def test_oom_assertion_rejects_successful_disconnect_without_a_ban(self):
        self.run_shell(f'if station_mtk_kick ra0 {A} 60; then exit 8; fi', OOM_ON_ADD=1)
        self.assertEqual(self.blacklist(), {})
        self.assertFalse((self.path / 'state/station-driver-verified').exists())
        self.assertIn('allocation failed', (self.path / 'log').read_text())
        self.assertIn(f'ra0 {A} 1060 {self.driver_hash}', (self.path / 'state/station-bans').read_text())

    def test_unreadable_kernel_log_refuses_addition(self):
        self.run_shell(f'if station_mtk_kick ra0 {A} 60; then exit 8; fi', KERNEL_LOG_FAIL=1)
        self.assertNotIn('BlAdd', (self.path / 'driver-calls').read_text())

    def test_older_oom_assertion_does_not_reject_a_new_successful_add(self):
        (self.path / 'kernel-log').write_text('[900.000001] map.c:1034 assert pBlEntry != NULLfailed\n')
        self.hold()
        self.assertEqual(self.blacklist()['ra0'], [A])

    def test_new_protected_device_releases_owned_driver_hold(self):
        self.hold()
        self.run_shell('white="$WHITE"; interfaces="$INTERFACES"; station_mtk_prune configured', WHITE=A)
        self.assertEqual(self.blacklist()['ra0'], [])

    def test_unselected_ap_releases_only_its_owned_driver_hold(self):
        self.hold()
        self.run_shell(f'station_mtk_ban ra1 {A} 60')
        self.run_shell('white="$WHITE"; interfaces="$INTERFACES"; station_mtk_prune configured', INTERFACES='ra1')
        self.assertEqual(self.blacklist(), {'ra0': [], 'ra1': [A]})

    def test_sleep_is_bounded_by_next_expiry(self):
        self.hold()
        self.assertEqual(self.run_shell('station_mtk_sleep 3600', NOW=1055).stdout, '5\n')
        self.assertEqual(self.run_shell('station_mtk_sleep 10', NOW=1065).stdout, '1\n')

    def test_longer_existing_lease_is_not_shortened(self):
        self.hold(seconds=120)
        self.run_shell(f'station_mtk_ban ra0 {A} 10')
        self.assertIn(f'ra0 {A} 1120 {self.driver_hash}', (self.path / 'state/station-bans').read_text())

    def test_disabled_and_test_mode_release_live_holds(self):
        for options in ({'ENABLED': 0}, {'DRY': 1}):
            (self.path / 'ra0.assoclist').write_text(f'{A} -40 dBm\n')
            self.hold()
            self.run_shell('cycle || :', body=True, **options)
            self.assertEqual(self.blacklist()['ra0'], [])

    def test_preflight_guards_selected_vendor_driver_without_live_station_queries(self):
        self.run_shell('station_preflight')
        self.assertEqual((self.path / 'driver-calls').read_text(), '')
        self.run_shell('if station_preflight; then exit 8; fi', DRIVER_KNOWN='0')
        self.assertEqual((self.path / 'driver-calls').read_text(), '')
        self.run_shell('station_preflight', NETWORKS='native', DRIVER_KNOWN='0')

    def test_failed_preflight_publishes_actionable_startup_failure_without_pid(self):
        self.run_shell('if station_preflight; then exit 8; fi', DRIVER_KNOWN='0')
        status = json.loads((self.path / 'state/service-status.json').read_text())
        self.assertEqual(status['service_state'], 'error')
        self.assertEqual(status['error'], 'station_preflight_failed')
        self.assertEqual(status['pid'], 0)
        self.assertTrue(status['detail'])
        self.assertNotIn('iwpriv', (self.path / 'driver-calls').read_text())

    def test_kernel_log_preflight_error_is_specific(self):
        self.run_shell('if station_preflight; then exit 8; fi', KERNEL_LOG_FAIL='1')
        status = json.loads((self.path / 'state/service-status.json').read_text())
        self.assertEqual(status['error'], 'kernel_log_unavailable')
        self.assertEqual(status['pid'], 0)

    def test_service_diagnostic_escapes_detail_and_preserves_json_decode_state(self):
        result = self.run_shell('''json_load '{"answer":"preserved"}'
station_service_status error custom_error 'Detail with "quotes" and
a newline'
json_get_var value answer
printf '%s\n' "$value"''')
        self.assertEqual(result.stdout.strip(), 'preserved')
        status = json.loads((self.path / 'state/service-status.json').read_text())
        self.assertEqual(status['detail'], 'Detail with "quotes" and\na newline')
        self.assertGreater(status['pid'], 0)
        self.assertFalse(list((self.path / 'state').glob('service-status.json.tmp-*')))

    def test_invalid_configuration_reports_error_and_valid_reload_recovers(self):
        self.run_shell('cycle || :', body=True, MAX='invalid')
        status = json.loads((self.path / 'state/service-status.json').read_text())
        self.assertEqual(status['error'], 'configuration_invalid')
        self.assertGreater(status['pid'], 0)
        self.assertEqual((self.path / 'driver-calls').read_text(), '')
        self.run_shell('cycle', body=True)
        status = json.loads((self.path / 'state/service-status.json').read_text())
        self.assertEqual(status['service_state'], 'running')
        self.assertEqual(status['error'], '')

    def init_fixture(self):
        helpers = self.path / 'helpers'
        helpers.mkdir()
        shim = self.path / 'api.sh'
        shim.write_text(SHIM)
        adapter = helpers / 'station-adapter.sh'
        adapter.write_text('#!/bin/sh\nSTATE="$FIX/state"\n. "' + str(shim) + '"\n' +
                           ADAPTER.replace('/usr/share/libubox/jshn.sh', str(shim)))
        adapter.chmod(0o755)
        for name in ('internet-policy.sh', 'vendor-acceleration.sh'):
            path = helpers / name
            path.write_text('#!/bin/sh\nexit 0\n')
            path.chmod(0o755)
        source = (ROOT / 'files/etc/init.d/wifi-client-limiter').read_text()
        source = source.replace('directory=/tmp/wifi-client-limiter', 'directory="$FIX/state"')
        source = source.replace('/usr/share/wifi-client-limiter', str(helpers))
        source = source.replace('/tmp/wifi-client-limiter/lock', str(self.path / 'state/lock'))
        return source + '''
nft() { :; }
fw3() { :; }
procd_open_instance() { printf 'procd-open\n' >> "$FIX/calls"; }
procd_set_param() { :; }
procd_close_instance() { printf 'procd-close\n' >> "$FIX/calls"; }
'''

    def test_init_preflight_failure_is_visible_without_registering_a_worker(self):
        self.run_shell(self.init_fixture() + '\nif start_service; then exit 8; fi', DRIVER_KNOWN='0')
        status = json.loads((self.path / 'state/service-status.json').read_text())
        self.assertEqual(status['service_state'], 'error')
        self.assertEqual(status['error'], 'station_preflight_failed')
        self.assertEqual(status['pid'], 0)
        self.assertNotIn('procd-open', (self.path / 'calls').read_text())

    def test_init_stop_reports_stopped_after_cleanup(self):
        self.run_shell(self.init_fixture() + '\nstart_service; service_stopped')
        self.assertIn('procd-close', (self.path / 'calls').read_text())
        status = json.loads((self.path / 'state/service-status.json').read_text())
        self.assertEqual(status, {'service_state': 'stopped', 'error': '', 'detail': '', 'pid': 0})

    def test_worker_shutdown_cleanup_failure_remains_visible(self):
        self.run_shell('station_mtk_cleanup() { return 1; }; internet_cleanup() { :; }; worker_shutdown', body=True)
        status = json.loads((self.path / 'state/service-status.json').read_text())
        self.assertEqual(status['service_state'], 'error')
        self.assertEqual(status['error'], 'cleanup_failed')

    def test_stale_private_lock_is_recovered(self):
        directory = self.path / 'state/station-policy-lock'
        directory.mkdir()
        (directory / 'pid').write_text('2147483647\n')
        self.hold()
        self.assertFalse(directory.exists())

    def test_invalid_journal_is_kept_without_driver_mutation(self):
        journal = self.path / 'state/station-bans'
        journal.write_text(f'ra0;evil {A} 999 {self.driver_hash}\n')
        self.run_shell('if station_mtk_cleanup; then exit 8; fi')
        self.assertIn('ra0;evil', journal.read_text())
        self.assertNotIn('iwpriv', (self.path / 'driver-calls').read_text())

    def test_failed_journal_append_prevents_no_ttl_driver_mutation(self):
        self.run_shell(f'ln -s /dev/full "$STATE/station-bans-new-$$"; if station_mtk_kick ra0 {A} 60; then exit 8; fi')
        self.assertEqual(self.blacklist(), {})
        self.assertNotIn('BlAdd', (self.path / 'driver-calls').read_text())
        self.assertEqual((self.path / 'state/station-bans').read_text(), '')

    def test_failed_prune_output_keeps_original_ownership_journal(self):
        self.hold()
        original = (self.path / 'state/station-bans').read_text()
        self.run_shell('ln -s /dev/full "$STATE/station-bans-new-$$"; if station_mtk_prune; then exit 8; fi')
        self.assertEqual((self.path / 'state/station-bans').read_text(), original)
        self.assertEqual(self.blacklist()['ra0'], [A])

    def test_age_cleanup_keeps_other_dotted_runtime_files(self):
        (self.path / 'state/vendor-status.json').write_text('{}')
        (self.path / 'state/internet-status.json').write_text('{}')
        (self.path / 'state/worker.tmp').write_text('snapshot')
        (self.path / 'state'/f'ra0.{C}').write_text('20\n')
        self.run_shell('cycle', body=True)
        self.assertTrue((self.path / 'state/vendor-status.json').exists())
        self.assertTrue((self.path / 'state/internet-status.json').exists())
        self.assertTrue((self.path / 'state/worker.tmp').exists())
        self.assertFalse((self.path / 'state'/f'ra0.{C}').exists())

    def test_live_vendor_once_is_rejected_before_any_policy_or_station_mutation(self):
        self.run_shell('if once_preflight; then cycle; exit 8; fi', body=True, COOLDOWN=60)
        self.assertEqual((self.path / 'driver-calls').read_text(), '')
        self.assertEqual((self.path / 'calls').read_text(), '')
        self.assertEqual(self.blacklist(), {})

    def test_dry_vendor_once_remains_available(self):
        self.run_shell('once_preflight && cycle', body=True, DRY=1, COOLDOWN=60)
        self.assertNotIn('iwpriv', (self.path / 'driver-calls').read_text())

    def test_native_once_remains_available(self):
        self.run_shell('once_preflight && cycle', body=True, NETWORKS='native')
        self.assertIn('hostapd.wlan0', (self.path / 'calls').read_text())

    def seed_three_vendor_guests(self):
        (self.path / 'ra0.assoclist').write_text(f'{A} -40 dBm\n{B} -45 dBm\n')
        (self.path / 'ra1.assoclist').write_text(f'{C} -50 dBm\n')
        for iface, mac, since in [('ra0', A, 800), ('ra0', B, 900), ('ra1', C, 950)]:
            (self.path / 'state'/f'{iface}.{mac}').write_text(f'{since}\n')

    def test_full_vendor_count_eviction_bans_both_aps_and_remembers_deadlines(self):
        self.seed_three_vendor_guests()
        self.run_shell('cycle', body=True, NETWORKS='guest admin', MAX=2, COOLDOWN=60, WINDOW=300)
        self.assertEqual(self.blacklist(), {'ra0': [A], 'ra1': [A]})
        self.assertEqual((self.path / 'state/recent').read_text(), f'{A} 1000 1060 1300\n')
        remaining = (self.path / 'state/remaining').read_text()
        self.assertNotIn(A, remaining)
        self.assertIn(B, remaining)
        self.assertIn(C, remaining)

    def test_full_vendor_returner_cannot_displace_existing_guests_after_pause(self):
        self.seed_three_vendor_guests()
        self.run_shell('cycle', body=True, NETWORKS='guest admin', MAX=2, COOLDOWN=60, WINDOW=300)
        self.run_shell('station_mtk_prune', NOW=1060)
        (self.path / 'ra1.assoclist').write_text(f'{C} -50 dBm\n{A} -40 dBm\n')
        self.run_shell('cycle', body=True, NETWORKS='guest admin', MAX=2, COOLDOWN=60, WINDOW=300, NOW=1080)
        self.assertEqual(self.blacklist(), {'ra0': [A], 'ra1': [A]})
        self.assertNotIn(f'BlAdd={B}', (self.path / 'driver-calls').read_text())
        self.assertNotIn(f'BlAdd={C}', (self.path / 'driver-calls').read_text())
        self.assertEqual((self.path / 'state/recent').read_text(), f'{A} 1000 1060 1300\n')
        self.assertIn(f'ra0 {A} 1100 {self.driver_hash}', (self.path / 'state/station-bans').read_text())

    def test_full_vendor_returner_uses_free_slot_after_pause(self):
        self.seed_three_vendor_guests()
        self.run_shell('cycle', body=True, NETWORKS='guest admin', MAX=2, COOLDOWN=60, WINDOW=300)
        self.run_shell('station_mtk_prune', NOW=1060)
        (self.path / 'ra1.assoclist').write_text(f'{A} -40 dBm\n')
        self.run_shell('cycle', body=True, NETWORKS='guest admin', MAX=2, COOLDOWN=60, WINDOW=300, NOW=1080)
        self.assertEqual((self.path / 'state/recent').read_text(), '')
        self.assertNotIn('BlAdd', (self.path / 'driver-calls').read_text())
        self.assertIn(A, (self.path / 'state/remaining').read_text())
        self.assertIn(B, (self.path / 'state/remaining').read_text())


class GL483StationAdapterTests(StationAdapterTests):
    driver_hash = GL483_HASH


if __name__ == '__main__':
    unittest.main(verbosity=2)

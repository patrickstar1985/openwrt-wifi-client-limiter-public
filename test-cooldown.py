#!/usr/bin/env python3
"""Exercise the production cooldown worker with isolated UCI/ubus/iw fixtures."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / 'files/usr/sbin/wifi-client-limiter').read_text()
BODY = SOURCE[SOURCE.index('log()'):SOURCE.index('# procd runs')]
APS = ('ap0', 'ap1', 'ap2')
A = 'aa:00:00:00:00:01'
B = '00:00:00:00:00:02'
C = '00:00:00:00:00:03'

# Keep load_settings(), resolve_interfaces(), and the complete eviction logic
# unchanged. Only OpenWrt system APIs are replaced by local test fixtures.
SHIM = r'''
# Internet filtering is exercised separately by test-internet.py. These
# fixtures isolate count, duration and reconnect behavior from firewall APIs.
internet_policy() { :; }
config_load() { :; }
config_get() {
 local destination="$1" option="$3" value="$4"
 case "$option" in
  enabled) value="$ENABLED";; dry_run) value="$DRY";;
  max_clients) value="$MAX";; max_session) value="$SESSION";;
  interval) value="$INTERVAL";;
  reconnect_cooldown) [ "$COOLDOWN_PRESENT" = 0 ] || value="$COOLDOWN";;
  reconnect_window) [ "$WINDOW_PRESENT" = 0 ] || value="$WINDOW";;
 esac
 eval "$destination=\$value"
}
config_get_bool() { config_get "$@"; }
config_list_foreach() {
 local values= item
 case "$2" in network) values="$NETWORKS";; whitelist) values="$WHITE";; esac
 for item in $values; do "$3" "$item"; done
}
json_load() {
 printf '%s' "$1" > "$FIX/json"
 jp=''
 python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$FIX/json"
}
json_query() { python3 - "$FIX/json" "$jp" "$1" <<'END'
import json, sys
try:
    value = json.load(open(sys.argv[1]))
    for key in sys.argv[2].split('/'):
        if key:
            value = value[int(key)] if isinstance(value, list) else value[key]
    operation = sys.argv[3]
    if operation == 'exists':
        pass
    elif operation == 'keys':
        print(' '.join(map(str, range(len(value)) if isinstance(value, list) else value.keys())))
    else:
        print(value.get(operation, '') if isinstance(value, dict) else '')
except (KeyError, IndexError, TypeError, ValueError):
    sys.exit(1)
END
}
json_select() {
 local previous="$jp"
 if [ "$1" = .. ]; then jp="${jp%/*}"; else jp="$jp/$1"; fi
 json_query exists || { jp="$previous"; return 1; }
}
json_get_keys() { eval "$1=\"\$(json_query keys)\""; }
json_get_var() { eval "$1=\"\$(json_query \"$2\")\""; }
ubus() {
 printf '%s\t%s\t%s\n' "$2" "$3" "${4:-}" >> "$FIX/calls"
 if [ "$2" = network.wireless ]; then cat "$FIX/wireless.json"; return; fi
 if [ "$3" = get_clients ]; then
  [ "$GET_FAIL" = "$2" ] && return 1
  cat "$FIX/$2.json"
 else
  case " $FAIL_APS " in *" $2 "*)
   if [ -z "$FAIL_MAC" ]; then return 1; fi
   case "$4" in *"$FAIL_MAC"*) return 1;; esac
  esac
 fi
}
iw() { cat "$FIX/$2.iw"; }
logger() { printf '%s\n' "$*" >> "$FIX/log"; }
cut() {
 if [ -n "${CLOCK_FILE:-}" ] && [ "$3" = /proc/uptime ]; then
  python3 - "$CLOCK_FILE" <<'END'
from pathlib import Path
import sys
path = Path(sys.argv[1])
values = path.read_text().splitlines()
print(values[0])
path.write_text('\n'.join(values[1:] or values) + '\n')
END
  return
 fi
 if [ -n "${NOW:-}" ] && [ "$3" = /proc/uptime ]; then printf '%s\n' "$NOW"; else command cut "$@"; fi
}
'''


class CooldownWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='wifi-limiter-cooldown-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        (self.path / 'state').mkdir()
        self.harness = self.path / 'worker.sh'
        self.harness.write_text('STATE="$FIX/state"\n' + BODY + '\n' + SHIM + '\ncycle || :\n')
        entries = [
            {'section': f'guest{i}', 'ifname': iface, 'config': {'mode': 'ap'}}
            for i, iface in enumerate(APS)
        ]
        entries.extend([
            {'section': 'admin', 'ifname': 'admin0', 'config': {'mode': 'ap'}},
            {'section': 'duplicate', 'ifname': 'ap0', 'config': {'mode': 'ap'}},
            {'section': 'bad', 'ifname': 'bad iface;echo', 'config': {'mode': 'ap'}},
            {'section': 'station', 'ifname': 'sta0', 'config': {'mode': 'sta'}},
        ])
        (self.path / 'wireless.json').write_text(json.dumps({'radio0': {'interfaces': entries}}))

    def run_worker(self, rows=None, *, cooldown='60', maximum=1, session=0,
                   dry=False, white='', fail_aps='', fail_mac='', get_fail='', enabled=True,
                   window='0', now='', interval=10, clock_sequence=None):
        if rows is None:
            rows = [(A, 'ap0', 100), (B, 'ap1', 10)]
        for name in ('calls', 'log'):
            (self.path / name).write_text('')
        if clock_sequence is not None:
            (self.path / 'clock').write_text(''.join(f'{value}\n' for value in clock_sequence))
        for iface in APS:
            clients = {mac: {} for mac, ap, age in rows if ap == iface}
            (self.path / f'hostapd.{iface}.json').write_text(json.dumps({'clients': clients}))
            (self.path / f'{iface}.iw').write_text(''.join(
                f'Station {mac} (on {iface})\n\tconnected time: {age} seconds\n'
                for mac, ap, age in rows if ap == iface))
        environment = dict(os.environ, FIX=str(self.path), COOLDOWN='' if cooldown is None else str(cooldown),
                           COOLDOWN_PRESENT='0' if cooldown is None else '1',
                           WINDOW='' if window is None else str(window), WINDOW_PRESENT='0' if window is None else '1', NOW=str(now),
                           INTERVAL=str(interval),
                           CLOCK_FILE=str(self.path/'clock') if clock_sequence is not None else '',
                           MAX=str(maximum), SESSION=str(session), DRY=str(int(dry)),
                           ENABLED=str(int(enabled)), WHITE=white, GET_FAIL=get_fail,
                           FAIL_APS=fail_aps, FAIL_MAC=fail_mac,
                           NETWORKS='guest0 guest1 guest2 duplicate bad station')
        subprocess.run(['sh', str(self.harness)], env=environment, check=True,
                       capture_output=True, text=True, timeout=20)
        calls = []
        for line in (self.path / 'calls').read_text().splitlines():
            ap, method, payload = line.split('\t', 2)
            calls.append((ap, method, json.loads(payload) if payload else None))
        return calls, (self.path / 'log').read_text()

    def mutations(self, calls, mac=None):
        return [call for call in calls if call[1] == 'del_client'
                and (mac is None or call[2]['addr'] == mac)]

    def assert_all_ap_bans(self, calls, mac=A, milliseconds=60000):
        changes = self.mutations(calls, mac)
        self.assertEqual(sorted(call[0] for call in changes), [f'hostapd.{ap}' for ap in APS])
        for ap, method, payload in changes:
            self.assertEqual(payload, {'addr': mac, 'reason': 5, 'deauth': True,
                                       'ban_time': milliseconds})

    def test_count_limit_bans_all_selected_aps_after_complete_snapshot(self):
        calls, log = self.run_worker()
        self.assert_all_ap_bans(calls)
        self.assertFalse(self.mutations(calls, B))
        first_change = next(i for i, call in enumerate(calls) if call[1] == 'del_client')
        self.assertEqual([call[0] for call in calls[:first_change] if call[1] == 'get_clients'],
                         [f'hostapd.{ap}' for ap in APS])
        self.assertIn('Reconnect cooldown requested', log)
        self.assertNotIn('admin0', str(calls))
        self.assertNotIn('sta0', str(calls))
        self.assertNotIn('bad iface', str(calls))

    def test_session_limit_and_combined_limits_do_not_repeat_bans(self):
        for maximum in (0, 1):
            with self.subTest(maximum=maximum):
                calls, log = self.run_worker(maximum=maximum, session=90)
                self.assert_all_ap_bans(calls)
                self.assertFalse(self.mutations(calls, B))
                self.assertIn('Session limit: disconnect requested', log)

    def test_zero_and_missing_option_keep_only_associated_disconnect(self):
        for cooldown in ('0', '000', None):
            with self.subTest(cooldown=cooldown):
                calls, log = self.run_worker(cooldown=cooldown)
                changes = self.mutations(calls)
                self.assertEqual(len(changes), 1)
                self.assertEqual(changes[0][0], 'hostapd.ap0')
                self.assertEqual(changes[0][2]['ban_time'], 0)
                self.assertIn('(no ban)', log)

    def test_dry_run_never_changes_any_ap(self):
        calls, log = self.run_worker(dry=True, session=90)
        self.assertFalse(self.mutations(calls))
        self.assertIn('TEST: Session limit', log)
        self.assertIn('TEST: Reconnect cooldown 60s', log)

    def test_whitelist_never_disconnects_or_bans_that_mac(self):
        calls, log = self.run_worker(
            [(A, 'ap0', 100), (B, 'ap1', 50), (C, 'ap2', 10)],
            white=A.upper(), session=90)
        self.assertFalse(self.mutations(calls, A))
        self.assert_all_ap_bans(calls, B)
        self.assertFalse(self.mutations(calls, C))

    def test_extra_ap_failure_warns_without_evicting_another_device(self):
        calls, log = self.run_worker(fail_aps='hostapd.ap2', fail_mac=A)
        self.assert_all_ap_bans(calls)
        self.assertFalse(self.mutations(calls, B))
        self.assertIn(f'WARNING: Reconnect cooldown failed: {A} on ap2', log)
        self.assertIn('another managed AP may accept this device', log)

    def test_all_actual_kicks_fail_so_no_extra_ap_ban_for_that_device(self):
        calls, log = self.run_worker(fail_aps='hostapd.ap0', fail_mac=A)
        failed = self.mutations(calls, A)
        self.assertEqual([call[0] for call in failed], ['hostapd.ap0'])
        self.assertIn('Disconnect failed', log)
        # The failed device remains in the count, so another eligible device
        # must be disconnected. Its extra bans still include the failed AP.
        self.assertEqual(len(self.mutations(calls, B)), 3)

    def test_partial_actual_failure_remains_counted_and_no_retry_this_scan(self):
        rows = [(A, 'ap0', 100), (A, 'ap1', 90), (B, 'ap2', 10)]
        calls, log = self.run_worker(rows, session=90,
                                     fail_aps='hostapd.ap1 hostapd.ap2', fail_mac=A)
        self.assert_all_ap_bans(calls)
        self.assertEqual(len(self.mutations(calls, A)), 3)
        self.assertEqual(len(self.mutations(calls, B)), 3)
        self.assertIn(f'Disconnect failed: {A} on ap1', log)
        self.assertIn(f'WARNING: Reconnect cooldown failed: {A} on ap2', log)
        self.assertFalse((self.path / 'state' / f'ap0.{A}').exists())
        self.assertTrue((self.path / 'state' / f'ap1.{A}').exists())
        self.assertFalse((self.path / 'state' / f'ap2.{B}').exists())

    def test_incomplete_snapshot_does_not_disconnect_or_clean_old_state(self):
        stale = self.path / 'state' / f'ap2.{C}'
        stale.write_text('123\n')
        calls, log = self.run_worker(get_fail='hostapd.ap1')
        self.assertFalse(self.mutations(calls))
        self.assertTrue(stale.exists())
        self.assertIn('skip entire cycle', log)

    def test_no_repeated_ban_on_later_scan_without_an_eviction(self):
        calls, log = self.run_worker()
        self.assert_all_ap_bans(calls)
        calls, log = self.run_worker([(B, 'ap1', 20)])
        self.assertFalse(self.mutations(calls))

    def test_valid_boundaries_and_decimal_leading_zeroes(self):
        for cooldown, milliseconds in [('1', 1000), ('008', 8000),
                                       ('00060', 60000), ('1800', 1800000)]:
            with self.subTest(cooldown=cooldown):
                calls, log = self.run_worker(cooldown=cooldown)
                self.assert_all_ap_bans(calls, milliseconds=milliseconds)

    def test_invalid_cooldown_skips_before_any_ubus_read_or_mutation(self):
        for cooldown in ('1801', '-1', '1.5', '', '2147483648', '60;false'):
            with self.subTest(cooldown=cooldown):
                calls, log = self.run_worker(cooldown=cooldown)
                self.assertFalse(calls)
                self.assertIn('skipping', log)

    def test_disabled_worker_does_not_touch_hostapd(self):
        calls, log = self.run_worker(enabled=False)
        self.assertFalse(calls)


if __name__ == '__main__':
    unittest.main(verbosity=2)

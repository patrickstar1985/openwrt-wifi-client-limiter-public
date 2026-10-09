#!/usr/bin/env python3
"""Exercise runtime ACL checks without reading or changing host WiFi state."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
ADAPTER = (ROOT / 'files/usr/share/wifi-client-limiter/station-adapter.sh').read_text()

SHIM = r'''
uname() { printf '%s\n' "$MACHINE"; }
uci() { [ "$NETWORK_FAIL" != 1 ] || return 1; printf '%s\n' "$NETWORKS"; }
logger() { printf '%s\n' "$*" >> "$FIX/log"; }
station_ap_records() {
 [ "$RECORDS_FAIL" != 1 ] || return 1
 cat "$FIX/aps"
}
'''

QUERY = r'''#!/bin/sh
printf '%s\n' "$1" >> "$FIX/queries"
case "$1" in
 ra0) value="$POLICY0";; rax0) value="$POLICY1";; ra1) value="$POLICY_ADMIN";; *) exit 1;;
esac
[ "$value" != fail ] || exit 1
printf '%s\n' "$value"
'''


class RuntimeMacPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wifi-limiter-acl-policy-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.helper = self.path / 'query'
        self.helper.write_text(QUERY)
        self.helper.chmod(0o755)
        (self.path / 'aps').write_text('ra0 guest mtk\nrax0 guest5 mtk\nra1 admin mtk\nwlan0 native hostapd\n')

    def run_policy(self, success=True, **overrides):
        for name in ('queries', 'log'):
            (self.path / name).write_text('')
        source = ADAPTER.replace('/usr/share/wifi-client-limiter/mtk-acl-query-aarch64', str(self.helper))
        harness = self.path / 'run.sh'
        harness.write_text(source + '\n' + SHIM + '\nstation_mtk_policy_safe\n')
        env = dict(os.environ, FIX=str(self.path), MACHINE='aarch64', NETWORKS='guest guest5 native',
                   POLICY0='0', POLICY1='2', POLICY_ADMIN='1', RECORDS_FAIL='0', NETWORK_FAIL='0')
        env.update({key: str(value) for key, value in overrides.items()})
        result = subprocess.run(['sh', str(harness)], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode == 0, success, result.stderr)
        self.assertEqual(result.stdout, '')
        return (self.path / 'log').read_text()

    def test_disabled_and_denylist_policies_preserve_their_settings(self):
        self.run_policy()
        self.assertEqual((self.path / 'queries').read_text(), 'ra0\nrax0\n')

    def test_runtime_allowlist_stops_before_enforcement(self):
        log = self.run_policy(False, POLICY0=1)
        self.assertIn('can bypass reconnect protection during WPS', log)
        self.assertEqual((self.path / 'queries').read_text(), 'ra0\n')

    def test_other_band_allowlist_is_also_rejected(self):
        self.assertIn('rax0', self.run_policy(False, POLICY1=1))

    def test_unselected_operator_allowlist_is_not_queried(self):
        self.run_policy()
        self.assertNotIn('ra1', (self.path / 'queries').read_text())

    def test_failed_query_cannot_be_mistaken_for_policy_zero(self):
        self.assertIn('Cannot read', self.run_policy(False, POLICY0='fail'))

    def test_unknown_and_multiline_responses_are_rejected(self):
        for value in ('', '3', '-1', '00', '0\n1', '0 extra'):
            with self.subTest(value=value):
                self.assertIn('Unrecognized', self.run_policy(False, POLICY0=value))

    def test_missing_helper_is_truthfully_unavailable(self):
        self.helper.unlink()
        self.assertIn('unavailable', self.run_policy(False))
        self.assertEqual((self.path / 'queries').read_text(), '')

    def test_other_architecture_never_executes_the_native_helper(self):
        self.run_policy(False, MACHINE='x86_64')
        self.assertEqual((self.path / 'queries').read_text(), '')

    def test_failed_ap_resolution_stops_the_query(self):
        self.assertIn('Cannot resolve', self.run_policy(False, RECORDS_FAIL=1))
        self.assertEqual((self.path / 'queries').read_text(), '')

    def test_failed_selection_read_cannot_skip_selected_ap_checks(self):
        self.assertIn('Cannot read the selected WiFi', self.run_policy(False, NETWORK_FAIL=1))
        self.assertEqual((self.path / 'queries').read_text(), '')

    def test_no_active_selected_ap_does_not_query_operator_devices(self):
        self.run_policy(NETWORKS='offline')
        self.assertEqual((self.path / 'queries').read_text(), '')


if __name__ == '__main__':
    unittest.main(verbosity=2)

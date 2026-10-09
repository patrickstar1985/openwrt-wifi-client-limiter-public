#!/usr/bin/env python3
"""Multi-scan tests for fixed readmission memory and returner-first capacity."""
from pathlib import Path
import runpy
import unittest


ROOT = Path(__file__).resolve().parent
FIXTURE = runpy.run_path(str(ROOT / 'test-cooldown.py'))
BASE = FIXTURE['CooldownWorkerTests']
A, B, C = (FIXTURE[key] for key in ('A', 'B', 'C'))
D = '00:00:00:00:00:04'


class ReadmissionTests(unittest.TestCase):
    setUp = BASE.setUp
    run_worker = BASE.run_worker
    mutations = BASE.mutations
    assert_all_ap_bans = BASE.assert_all_ap_bans

    def seed(self, records):
        (self.path / 'state/recent').write_text(''.join(
            f'{mac} {evicted} {floor} {expires}\n' for mac, evicted, floor, expires in records))

    def records(self):
        file = self.path / 'state/recent'
        return {line.split()[0]: tuple(map(int, line.split()[1:]))
                for line in file.read_text().splitlines()} if file.exists() else {}

    def test_real_eviction_records_fixed_deadlines(self):
        calls, log = self.run_worker(window=300, cooldown=60, now=1000)
        self.assert_all_ap_bans(calls)
        self.assertEqual(self.records(), {A: (1000, 1060, 1300)})

    def test_new_count_victim_without_cooldown_gets_immediate_short_all_ap_ban(self):
        calls, log = self.run_worker(window=300, cooldown=0, now=1000)
        self.assert_all_ap_bans(calls, milliseconds=20000)
        self.assertEqual(self.records(), {A: (1000, 1000, 1300)})

    def test_slow_initial_ap_calls_share_one_deadline_and_do_not_restart_cooldown(self):
        calls, log = self.run_worker(window=300, cooldown=60,
                                     clock_sequence=[1000, 1000, 1000, 1000, 1007, 1008])
        self.assertEqual([call[2]['ban_time'] for call in self.mutations(calls, A)],
                         [60000, 53000, 52000])
        self.assertEqual(self.records(), {A: (1000, 1060, 1300)})

    def test_expired_initial_short_lease_is_not_restarted_on_extra_aps(self):
        calls, log = self.run_worker(window=5, cooldown=0,
                                     clock_sequence=[1000, 1000, 1000, 1000, 1007, 1008])
        self.assertEqual([call[2]['ban_time'] for call in self.mutations(calls, A)], [5000])
        self.assertEqual(self.records(), {A: (1000, 1000, 1005)})

    def test_minimum_pause_crossing_during_snapshot_accepts_free_slot(self):
        self.seed([(A, 1000, 1060, 1300)])
        calls, log = self.run_worker([(A, 'ap0', 1), (B, 'ap1', 100)],
                                     maximum=2, window=300,
                                     clock_sequence=[1059, 1059, 1060, 1060])
        self.assertFalse(self.mutations(calls))
        self.assertEqual(self.records(), {})

    def test_returner_full_rejected_before_existing_guest_without_new_deadline(self):
        self.seed([(A, 1000, 1000, 1300)])
        calls, log = self.run_worker([(A, 'ap0', 1), (B, 'ap1', 100)],
                                     window=300, now=1005)
        self.assert_all_ap_bans(calls, milliseconds=20000)
        self.assertFalse(self.mutations(calls, B))
        self.assertEqual(self.records(), {A: (1000, 1000, 1300)})

    def test_exact_limit_accepts_returner_that_used_a_free_slot(self):
        self.seed([(A, 1000, 1000, 1300)])
        calls, log = self.run_worker([(A, 'ap0', 1), (B, 'ap1', 100)],
                                     maximum=2, window=300, now=1005)
        self.assertFalse(self.mutations(calls))
        self.assertEqual(self.records(), {})
        self.assertIn('Readmission accepted', log)

    def test_two_free_slots_admit_only_two_of_three_returners(self):
        self.seed([(mac, 1000, 1000, 1300) for mac in (A, B, C)])
        calls, log = self.run_worker([(D, 'ap0', 100), (A, 'ap0', 1),
                                     (B, 'ap1', 1), (C, 'ap2', 1)],
                                     maximum=3, window=300, now=1005)
        # Registry order is deterministic: eviction time, then MAC.
        kept = sorted((A, B, C))[:2]
        rejected = sorted((A, B, C))[2]
        self.assert_all_ap_bans(calls, rejected, milliseconds=20000)
        self.assertFalse(self.mutations(calls, D))
        for mac in kept:
            self.assertFalse(self.mutations(calls, mac))
        self.assertEqual(self.records(), {rejected: (1000, 1000, 1300)})

    def test_failed_returner_disconnect_never_evicts_existing_guest(self):
        self.seed([(A, 1000, 1000, 1300)])
        calls, log = self.run_worker([(A, 'ap0', 1), (B, 'ap1', 100)],
                                     window=300, now=1005,
                                     fail_aps='hostapd.ap0', fail_mac=A)
        self.assertEqual(len(self.mutations(calls, A)), 1)
        self.assertFalse(self.mutations(calls, B))
        self.assertIn('ordinary capacity evictions skipped', log)
        self.assertIn('guest limit may remain exceeded', log)
        self.assertEqual(self.records(), {A: (1000, 1000, 1300)})

    def test_retry_uses_remaining_minimum_pause_even_when_capacity_is_free(self):
        self.seed([(A, 1000, 1060, 1300)])
        for now, milliseconds in ((1005, 55000), (1015, 45000)):
            calls, log = self.run_worker([(A, 'ap0', 1)], maximum=2,
                                         window=900, cooldown=1800, now=now)
            self.assert_all_ap_bans(calls, milliseconds=milliseconds)
            self.assertEqual(self.records(), {A: (1000, 1060, 1300)})

    def test_failed_old_association_does_not_look_like_successful_readmission(self):
        self.seed([(A, 1000, 1000, 1300)])
        calls, log = self.run_worker([(A, 'ap0', 100)], maximum=2,
                                     window=300, now=1005)
        self.assert_all_ap_bans(calls, milliseconds=20000)
        self.assertEqual(self.records(), {A: (1000, 1000, 1300)})

    def test_absent_full_short_refresh_then_free_slot_lets_bans_expire(self):
        self.seed([(A, 1000, 1000, 1300)])
        calls, log = self.run_worker([(B, 'ap1', 100)], window=300, now=1005)
        self.assert_all_ap_bans(calls, milliseconds=20000)
        calls, log = self.run_worker([], window=300, now=1015)
        self.assertFalse(self.mutations(calls))
        self.assertEqual(self.records(), {A: (1000, 1000, 1300)})
        # No proactive ban_time=0 operation can disconnect a racing rejoin.
        self.assertNotIn('Readmission accepted', log)

    def test_short_refresh_capped_by_sixty_seconds_and_window_end(self):
        self.seed([(A, 1000, 1000, 1300)])
        calls, log = self.run_worker([(B, 'ap1', 100)], window=300, now=1005,
                                     interval=3600)
        self.assert_all_ap_bans(calls, milliseconds=60000)
        calls, log = self.run_worker([(B, 'ap1', 100)], window=300, now=1297)
        self.assert_all_ap_bans(calls, milliseconds=3000)

    def test_window_expiry_forgets_record_without_shortening_longer_cooldown(self):
        self.seed([(A, 1000, 1100, 1010)])
        calls, log = self.run_worker([(B, 'ap1', 100)], window=10, now=1005)
        self.assertFalse(self.mutations(calls))
        calls, log = self.run_worker([(B, 'ap1', 100)], window=10, now=1010)
        self.assertFalse(self.mutations(calls))
        self.assertEqual(self.records(), {})

    def test_window_zero_and_whitelist_clear_memory_without_touching_ap_bans(self):
        for setting in ('disabled', 'whitelist'):
            with self.subTest(setting=setting):
                self.seed([(A, 1000, 1060, 1300)])
                calls, log = self.run_worker([(B, 'ap1', 100)], now=1005,
                                             window=0 if setting == 'disabled' else 300,
                                             white=A.upper() if setting == 'whitelist' else '')
                self.assertFalse(self.mutations(calls))
                self.assertEqual(self.records(), {})

    def test_dry_run_never_changes_registry_or_ap_state(self):
        self.seed([(A, 1000, 1000, 1300)])
        original = (self.path / 'state/recent').read_bytes()
        calls, log = self.run_worker([(A, 'ap0', 1), (B, 'ap1', 100)],
                                     window=300, now=1005, dry=True)
        self.assertFalse(self.mutations(calls))
        self.assertEqual((self.path / 'state/recent').read_bytes(), original)
        calls, log = self.run_worker(window=300, now=1005, dry=True)
        self.assertFalse(self.mutations(calls))
        self.assertEqual((self.path / 'state/recent').read_bytes(), original)

    def test_failed_snapshot_preserves_registry_without_refresh(self):
        self.seed([(A, 1000, 1000, 1300)])
        original = (self.path / 'state/recent').read_bytes()
        calls, log = self.run_worker([(B, 'ap1', 100)], window=300, now=1005,
                                     get_fail='hostapd.ap2')
        self.assertFalse(self.mutations(calls))
        self.assertEqual((self.path / 'state/recent').read_bytes(), original)

    def test_unlimited_capacity_admits_returner_after_minimum_pause(self):
        self.seed([(A, 1000, 1000, 1300)])
        calls, log = self.run_worker([(A, 'ap0', 1), (B, 'ap1', 100)],
                                     maximum=0, window=300, now=1005)
        self.assertFalse(self.mutations(calls))
        self.assertEqual(self.records(), {})

    def test_invalid_registry_entries_are_removed_without_using_their_values(self):
        (self.path / 'state/recent').write_text(
            'invalid-mac 1000 1000 1300\n' + f'{A} bad 1000 1300\n' +
            f'{B} 2000 2000 2300\n' + f'{C} 1000 999 1300\n')
        calls, log = self.run_worker([], window=300, now=1005)
        self.assertFalse(self.mutations(calls))
        self.assertEqual(self.records(), {})

    def test_window_validation_leading_zeroes_and_legacy_missing_value(self):
        for window in ('86401', '-1', '1.5', '', '2147483648'):
            calls, log = self.run_worker(window=window, now=1000)
            self.assertFalse(calls)
            self.assertIn('skipping', log)
        for window, expiry in (('000300', 1300), ('86400', 87400)):
            (self.path / 'state/recent').unlink(missing_ok=True)
            calls, log = self.run_worker(window=window, cooldown=0, now=1000)
            self.assertEqual(self.records(), {A: (1000, 1000, expiry)})
        calls, log = self.run_worker(window=None, now=1000)
        self.assertEqual(self.records(), {})


if __name__ == '__main__':
    unittest.main(defaultTest='ReadmissionTests', verbosity=2)

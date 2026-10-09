#!/usr/bin/env python3
"""Exercise the matched GL MTK controls without touching host kernel/service state."""
import json
import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
HELPER = ROOT / 'files/usr/share/wifi-client-limiter/vendor-acceleration.sh'
MODULES = ('shortcut_fe', 'shortcut_fe_ipv6', 'shortcut_fe_cm', 'shortcut_fe_drv', 'fast_classifier')
HNAT_HASHES = ('19751ae1fd69e351b26900f983ece10ae99f6f8a6dc07d22112d0c9dbbc6c65f',
               'd1b74a335d444ee735d2f0b332cdb9422035c0d0ea14cb3ef29d8229d4a4f756')

MOCK = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ['INTERNET_VENDOR_ROOT'])
path = root / 'mock.json'
state = json.loads(path.read_text())
command = Path(sys.argv[0]).name
args = sys.argv[1:]
code = 0
def fail(name):
    return bool(state.get('fail', {}).get(name))
def loaded(name):
    return (root / 'sys/module' / name).is_dir()
def unload(name):
    directory = root / 'sys/module' / name
    if directory.is_dir(): directory.rmdir()
def config():
    result = dict(state['config'])
    for key, value in state.get('pending', {}).items():
        if value is None: result.pop(key, None)
        else: result[key] = value
    return result
if command == 'uci':
    args = [arg for arg in args if arg not in ('-q', '-X')]
    operation, key = args[:2]
    if fail(operation + ':' + key.split('=')[0]): code = 1
    elif operation == 'get':
        value = config().get(key)
        if value is None: code = 1
        else: print(value)
    elif operation == 'changes':
        for key, value in state.get('pending', {}).items(): print(key + '=' + repr(value))
    elif operation == 'show':
        for key, value in config().items():
            if key.startswith(args[1] + '.'): print(key + "='" + value + "'")
    elif operation == 'set':
        key, value = key.split('=', 1)
        state['pending'][key] = value
        state['events'].append('set ' + key + '=' + value)
    elif operation == 'delete':
        if key not in config(): code = 1
        else: state['pending'][key] = None
        state['events'].append('delete ' + key)
    elif operation == 'commit':
        state['config'] = config()
        state['pending'] = {}
        state['events'].append('commit ' + key)
    elif operation == 'revert':
        state['pending'].pop(key, None)
        state['events'].append('revert ' + key)
    else: code = 2
elif command == 'dmesg':
    if fail('dmesg'): code = 1
    else: print(state['dmesg'], end='')
elif command == 'cat':
    target = args[0]
    if target.endswith('/hnat/hook_toggle'):
        if fail('hook_read'): code = 1
        elif fail('hook_stdout'): print(state['hook'])
        elif not fail('hook_no_message'):
            status = 'enabled' if state['hook'] else 'disabled'
            if fail('hook_unknown'): status = 'unknown'
            message = '[%d.000] value=%d, hook is %s now!\n' % (state['reads'], state['hook'], status)
            state['reads'] += 1
            if fail('ring_wrap'): state['dmesg'] = message
            else:
                state['dmesg'] += message
                if state.get('ring_lines'):
                    state['dmesg'] = ''.join(state['dmesg'].splitlines(keepends=True)[-state['ring_lines']:])
    elif target.endswith('/hnat/hnat_entry'):
        if fail('entry_read'): code = 1
        elif not fail('entry_empty'):
            names = ['Invalid', 'Unbind', 'BIND', 'FIN']
            for ppe in state.get('ppes', [0, 1]):
                print('============================')
                print('PPE_ID = %d' % ppe)
                if not fail('entry_no_summary'):
                    count = state['flows'][ppe] if state['selector'] == 2 else 0
                    print('Total State = %s cnt = %d' % (names[state['selector']], count))
    else:
        try: print(Path(target).read_text(), end='')
        except OSError: code = 1
elif command == 'vendor-control':
    target, value = args
    state['events'].append('control ' + Path(target).name + ' ' + value)
    if fail('write:' + value): code = 1
    elif target.endswith('/hook_toggle'):
        if not fail('hook_stuck'): state['hook'] = int(value)
    elif value.startswith('1 '): state['selector'] = int(value.split()[1])
    elif not fail('flush_stuck'):
        ppe = 0 if value == '3 -1' else 1
        state['flows'][ppe] = 0
elif command == 'rmmod':
    state['events'].append('rmmod ' + args[0])
    if fail('rmmod:' + args[0]): code = 1
    elif not fail('module_stuck'): unload(args[0])
elif command == 'insmod':
    name = Path(args[0]).stem.replace('-', '_')
    state['events'].append('insmod ' + name)
    if fail('insmod:' + name): code = 1
    else:
        (root / 'sys/module' / name).mkdir(exist_ok=True)
        if name == 'mtkhnat':
            state['hook'] = 1  # Actual module probe enables hooks.
            state['selector'] = 0
            state['flows'] = [0, 0]
            directory = root / 'sys/kernel/debug/hnat'
            directory.mkdir(exist_ok=True)
            for control in ('hook_toggle', 'hnat_entry'): (directory / control).touch()
elif command == 'sha256sum':
    if fail('module_hash'): code = 1
    else: print(state.get('hnat_hash', '19751ae1fd69e351b26900f983ece10ae99f6f8a6dc07d22112d0c9dbbc6c65f') + '  ' + args[0])
elif command == 'uname': print('5.4.211')
elif command == 'shortcut-fe':
    operation = args[0]
    if fail('service:' + operation): code = 2
    elif operation == 'enabled': code = 0 if state['service_enabled'] else 1
    elif operation == 'stop':
        state['events'].append('service stop')
        for name in ('shortcut_fe_drv', 'shortcut_fe_cm', 'fast_classifier'): unload(name)
        code = 1  # Exact legacy service can end with a false condition.
    elif operation in ('enable', 'disable'):
        state['events'].append('service ' + operation)
        state['service_enabled'] = operation == 'enable'
    else: code = 2
else: code = 2
path.write_text(json.dumps(state))
sys.exit(code)
'''


class VendorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wcl-vendor-')
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        mock = self.bin / 'mock'
        mock.write_text(MOCK)
        mock.chmod(0o755)
        for name in ('uci', 'dmesg', 'cat', 'rmmod', 'insmod', 'uname', 'sha256sum', 'vendor-control'):
            (self.bin / name).symlink_to(mock)
        for directory in ('etc/config', 'etc/init.d', 'sys/kernel/debug/hnat', 'sys/module', 'lib/modules/5.4.211'):
            (self.root / directory).mkdir(parents=True)
        (self.root / 'etc/config/mtkhnat').touch()
        (self.root / 'etc/init.d/shortcut-fe').symlink_to(mock)
        for name in ('hook_toggle', 'hnat_entry'):
            (self.root / 'sys/kernel/debug/hnat' / name).touch()
        for name in MODULES:
            (self.root / 'lib/modules/5.4.211' / (name.replace('_', '-') + '.ko')).touch()
        (self.root / 'lib/modules/5.4.211/mtkhnat.ko').touch()
        self.state_path = self.root / 'mock.json'
        self.write({'config': {'mtkhnat.global': 'mtkhnat', 'mtkhnat.global.enable': '1', 'mtkhnat.global.actype': '0', 'network.wan.proto': 'dhcp'}, 'pending': {}, 'hook': 1, 'selector': 0, 'service_enabled': True, 'flows': [4, 2], 'dmesg': '[0.000] boot\n', 'reads': 1, 'events': [], 'fail': {}})
        for name in ('mtkhnat', 'shortcut_fe', 'shortcut_fe_ipv6', 'shortcut_fe_cm'):
            (self.root / 'sys/module' / name).mkdir()
        self.backup_dir = self.root / 'etc/wifi-client-limiter'
        self.backup = self.backup_dir / 'vendor-acceleration-backup'
        self.marker = self.backup_dir / 'vendor-acceleration-reload-required'

    def tearDown(self):
        self.temp.cleanup()

    def read(self):
        return json.loads(self.state_path.read_text())

    def write(self, state):
        self.state_path.write_text(json.dumps(state))

    def edit(self, **values):
        state = self.read()
        state.update(values)
        self.write(state)

    def unload_hnat(self):
        for name in ('mtkhnat', 'mtk_warp'):
            directory = self.root / 'sys/module' / name
            if directory.exists(): directory.rmdir()
        for control in ('hook_toggle', 'hnat_entry'):
            (self.root / 'sys/kernel/debug/hnat' / control).unlink(missing_ok=True)
        self.edit(hook=0, selector=2, flows=[0, 0])

    def test_initial_audited_hnat_absence_enforces_and_preserves_unloaded_runtime(self):
        for signature in HNAT_HASHES:
            with self.subTest(signature=signature):
                self.edit(hnat_hash=signature)
                self.unload_hnat()
                self.call('internet_vendor_disable')
                self.call('internet_vendor_inactive')
                self.assertFalse((self.root / 'sys/module/mtkhnat').exists())
                self.assertEqual(self.read()['config']['mtkhnat.global.enable'], '0')
                self.assertTrue(all(not (self.root / 'sys/module' / name).exists() for name in MODULES))
                self.assertFalse(any(event.startswith('control ') for event in self.read()['events']))
                self.call('internet_vendor_restore')
                self.assertEqual(self.read()['config']['mtkhnat.global.enable'], '1')
                self.assertFalse((self.root / 'sys/module/mtkhnat').exists())
                self.assertFalse(self.backup.exists())
                self.edit(events=[])

    def test_reboot_after_active_backup_restores_enabled_hnat_and_saved_selector(self):
        self.edit(selector=3)
        self.call('internet_vendor_disable')
        snapshot = self.backup.read_text()
        self.unload_hnat()
        self.call('internet_vendor_disable')
        self.assertEqual(self.backup.read_text(), snapshot)
        self.call('internet_vendor_inactive')
        self.call('internet_vendor_restore')
        self.assertTrue((self.root / 'sys/module/mtkhnat').is_dir())
        self.assertEqual((self.read()['hook'], self.read()['selector']), (1, 3))
        self.assertIn('insmod mtkhnat', self.read()['events'])
        self.assertFalse(self.backup.exists())

    def test_saved_disabled_hnat_retains_absence_after_reboot_without_selector_load(self):
        self.edit(hook=0, selector=3)
        self.call('internet_vendor_disable')
        self.unload_hnat()
        self.call('internet_vendor_restore')
        self.assertFalse((self.root / 'sys/module/mtkhnat').exists())
        self.assertNotIn('insmod mtkhnat', self.read()['events'])
        self.assertFalse(self.backup.exists())

    def test_unknown_hnat_signature_and_inconsistent_absence_never_prove_safe(self):
        self.unload_hnat()
        self.edit(hnat_hash='0' * 64)
        self.assertIn('vendor_controls_unknown', self.call('internet_vendor_disable', False).stdout)
        self.assertEqual(self.read()['events'], [])
        self.edit(hnat_hash=HNAT_HASHES[1])
        (self.root / 'sys/module/mtk_warp').mkdir()
        self.call('internet_vendor_inactive', False)
        self.call('internet_vendor_disable', False)
        (self.root / 'sys/module/mtk_warp').rmdir()
        (self.root / 'sys/module/mtkhnat').mkdir()
        self.call('internet_vendor_disable', False)
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.read()['events'], [])
        (self.root / 'sys/module/mtkhnat').rmdir()
        (self.root / 'sys/kernel/debug/hnat/hook_toggle').touch()
        self.call('internet_vendor_disable', False)

    def test_missing_kernel_module_inventory_is_not_proof_of_hnat_absence(self):
        self.unload_hnat()
        for directory in (self.root / 'sys/module').iterdir(): directory.rmdir()
        (self.root / 'sys/module').rmdir()
        self.call('internet_vendor_disable', False)
        self.call('internet_vendor_inactive', False)
        self.assertEqual(self.read()['events'], [])
        self.assertFalse(self.backup.exists())

    def test_reappearing_active_hnat_fails_proof_then_is_disabled_and_flushed(self):
        self.unload_hnat()
        self.call('internet_vendor_disable')
        self.call('insmod "$INTERNET_VENDOR_ROOT/lib/modules/5.4.211/mtkhnat.ko"')
        self.edit(flows=[3, 2])
        self.assertIn('vendor_hnat_active', self.call('internet_vendor_inactive', False).stdout)
        self.call('internet_vendor_disable')
        self.assertEqual((self.read()['hook'], self.read()['flows']), (0, [0, 0]))
        self.call('internet_vendor_inactive')

    def test_absent_hnat_restore_load_failure_keeps_persistent_recovery(self):
        self.call('internet_vendor_disable')
        self.unload_hnat()
        self.edit(fail={'insmod:mtkhnat': True})
        self.assertIn('vendor_module_restore_failed', self.call('internet_vendor_restore', False).stdout)
        self.assertTrue(self.backup.exists())
        self.assertEqual(self.marker.read_text(), 'restore-hnat-selector\n')
        self.edit(fail={})
        self.call('internet_vendor_restore')
        self.assertFalse(self.backup.exists())

    def test_reloaded_hnat_selector_read_failure_restores_saved_selector_on_retry(self):
        self.edit(selector=3)
        self.call('internet_vendor_disable')
        self.unload_hnat()
        self.edit(fail={'entry_read': True})
        self.call('internet_vendor_restore', False)
        self.assertEqual((self.read()['hook'], self.read()['selector']), (1, 0))
        self.assertEqual(self.marker.read_text(), 'restore-hnat-selector\n')
        self.assertTrue(self.backup.exists())
        self.edit(fail={})
        self.call('internet_vendor_restore')
        self.assertEqual((self.read()['hook'], self.read()['selector']), (1, 3))
        self.assertFalse(self.backup.exists())
        self.assertFalse(self.marker.exists())

    def test_distinct_admin_selector_after_reload_failure_is_preserved_on_retry(self):
        self.edit(selector=3)
        self.call('internet_vendor_disable')
        self.unload_hnat()
        self.edit(fail={'entry_read': True})
        self.call('internet_vendor_restore', False)
        self.edit(fail={}, selector=1)
        self.call('internet_vendor_restore')
        self.assertEqual(self.read()['selector'], 1)
        self.assertFalse(self.backup.exists())
        self.assertFalse(self.marker.exists())

    def test_reasserting_policy_after_reload_failure_retains_original_selector_intent(self):
        self.edit(selector=3)
        self.call('internet_vendor_disable')
        self.unload_hnat()
        self.edit(fail={'entry_read': True})
        self.call('internet_vendor_restore', False)
        self.edit(fail={})
        self.call('internet_vendor_disable')
        self.assertIn('runtime selector 3\n', self.backup.read_text())
        self.call('internet_vendor_restore')
        self.assertEqual(self.read()['selector'], 3)

    def call(self, function, ok=True):
        # Only writes are substituted; real kernel readback parsing, sequencing,
        # backups, config transaction, module proof and service logic run intact.
        script = '. "$HELPER"\ninternet_vendor_control_write() { vendor-control "$1" "$2"; }\n' + function + '\nresult=$?\nprintf "result=%s error=%s\\n" "$result" "$INTERNET_VENDOR_ERROR"\nexit "$result"\n'
        env = dict(os.environ, HELPER=str(HELPER), INTERNET_VENDOR_ROOT=str(self.root), INTERNET_BACKUP_DIR=str(self.backup_dir), PATH=f'{self.bin}:{os.environ["PATH"]}')
        result = subprocess.run(['/bin/sh', '-c', script], env=env, capture_output=True, text=True)
        if ok: self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else: self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_disable_flushes_all_caches_and_blocks_hotplug_boot_reenable(self):
        self.call('internet_vendor_disable')
        state = self.read()
        self.assertEqual(state['config']['mtkhnat.global.enable'], '0')
        self.assertEqual(state['config']['mtkhnat.global.actype'], '1')
        self.assertEqual((state['hook'], state['selector'], state['flows'], state['service_enabled']), (0, 2, [0, 0], False))
        self.assertTrue(all(not (self.root / 'sys/module' / name).exists() for name in MODULES))
        self.assertTrue((self.root / 'sys/module/mtkhnat').is_dir())
        self.assertEqual(self.backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.backup_dir.stat().st_mode & 0o777, 0o700)
        self.assertFalse(self.marker.exists())
        self.call('internet_vendor_inactive')
        events = self.read()['events']
        self.call('internet_vendor_disable')
        self.assertEqual(self.read()['events'], events, 'Repeated scans must not commit or flush again')

    def test_initially_disabled_hook_still_flushes_bound_entries(self):
        self.edit(hook=0)
        self.call('internet_vendor_disable')
        self.assertEqual(self.read()['flows'], [0, 0])

    def test_restores_exact_options_modules_hook_selector_and_boot_state(self):
        initial = self.read()
        self.call('internet_vendor_disable')
        self.call('internet_vendor_restore')
        state = self.read()
        self.assertEqual(state['config'], initial['config'])
        self.assertEqual((state['hook'], state['selector'], state['service_enabled']), (1, 0, True))
        self.assertTrue(all((self.root / 'sys/module' / name).exists() for name in ('shortcut_fe', 'shortcut_fe_ipv6', 'shortcut_fe_cm')))
        self.assertTrue(all(not (self.root / 'sys/module' / name).exists() for name in ('shortcut_fe_drv', 'fast_classifier')))
        self.assertFalse(self.backup.exists())

    def test_absent_options_are_deleted_on_restore(self):
        state = self.read()
        for key in ('mtkhnat.global.enable', 'mtkhnat.global.actype'): del state['config'][key]
        self.write(state)
        self.call('internet_vendor_disable')
        self.call('internet_vendor_restore')
        self.assertEqual(self.read()['config'], state['config'])

    def test_pending_changes_abort_without_mutation_or_backup(self):
        self.edit(pending={'mtkhnat.other.foo': 'user edit'})
        result = self.call('internet_vendor_disable', False)
        self.assertIn('vendor_pending_changes', result.stdout)
        self.call('internet_vendor_inactive', False)
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.read()['events'], [])

    def test_full_rotating_ring_supports_enforcement_polling_and_restoration(self):
        # The original readback compared the whole old log as a prefix. A
        # normal full ring evicts that prefix on each read-induced printk.
        initial = self.read()
        self.edit(ring_lines=16, reads=100,
                  dmesg=''.join('[%d.000] unrelated kernel event\n' % i for i in range(16)))
        self.call('internet_vendor_disable')
        snapshot = self.backup.read_bytes()
        events = self.read()['events']
        for _ in range(4):
            self.call('internet_vendor_inactive')
            self.call('internet_vendor_disable')
        self.assertEqual(self.backup.read_bytes(), snapshot)
        self.assertEqual(self.read()['events'], events)
        self.assertEqual(self.read()['hook'], 0)
        self.call('internet_vendor_restore')
        restored = self.read()
        self.assertEqual(restored['config'], initial['config'])
        self.assertEqual(restored['hook'], initial['hook'])
        self.assertEqual(restored['selector'], initial['selector'])
        self.assertEqual(restored['service_enabled'], initial['service_enabled'])
        self.assertFalse(self.backup.exists())
        self.assertFalse(self.marker.exists())

    def test_vpn_hotplug_topology_is_rejected_even_by_readonly_proof(self):
        for proto in ('l2tp', 'pptp'):
            with self.subTest(proto=proto):
                state = self.read(); state['config']['network.vpn.proto'] = proto; self.write(state)
                result = self.call('internet_vendor_disable', False)
                self.assertIn('vendor_vpn_hotplug_unsupported', result.stdout)
                self.call('internet_vendor_inactive', False)
                self.assertFalse(self.backup.exists())
                self.assertEqual(self.read()['events'], [])

    def test_fresh_matched_printk_required_not_empty_or_plain_stdout(self):
        for failure in ('hook_no_message', 'hook_unknown', 'hook_stdout', 'ring_wrap', 'dmesg'):
            with self.subTest(failure=failure):
                self.edit(fail={failure: True})
                self.call('internet_vendor_disable', False)
                self.assertFalse(self.backup.exists())
                self.assertEqual(self.read()['events'], [])

    def test_missing_unknown_or_active_ppe_dump_never_claims_inactive(self):
        self.call('internet_vendor_disable')
        for changes in ({'fail': {'entry_empty': True}}, {'fail': {'entry_no_summary': True}}, {'fail': {}, 'ppes': [1]}, {'ppes': [0, 0]}, {'ppes': [0, 1], 'flows': [0, 1]}):
            with self.subTest(changes=changes):
                self.edit(**changes)
                self.call('internet_vendor_inactive', False)

    def test_disable_failure_keeps_recovery_and_retries(self):
        self.edit(fail={'rmmod:shortcut_fe_ipv6': True})
        self.call('internet_vendor_disable', False)
        self.assertTrue(self.backup.exists())
        self.assertEqual(self.marker.read_text(), 'disable\n')
        self.edit(fail={})
        self.call('internet_vendor_disable')
        self.call('internet_vendor_restore')
        self.assertEqual(self.read()['config']['mtkhnat.global.enable'], '1')

    def test_flush_hook_or_module_readback_failure_keeps_retry_marker(self):
        for failure in ('flush_stuck', 'hook_stuck', 'module_stuck'):
            with self.subTest(failure=failure):
                self.edit(fail={failure: True}, flows=[4, 2], hook=1)
                for name in ('shortcut_fe', 'shortcut_fe_ipv6'):
                    (self.root / 'sys/module' / name).mkdir(exist_ok=True)
                self.call('internet_vendor_disable', False)
                self.assertTrue(self.backup.exists())
                self.assertTrue(self.marker.exists())
                self.call('internet_vendor_inactive', False)
        self.edit(fail={})
        self.call('internet_vendor_disable')
        self.assertFalse(self.marker.exists())

    def test_manual_service_reenable_is_adopted_and_unknown_status_fails(self):
        self.edit(service_enabled=False)
        self.call('internet_vendor_disable')
        self.edit(service_enabled=True)
        self.call('internet_vendor_disable')
        self.assertFalse(self.read()['service_enabled'])
        self.call('internet_vendor_restore')
        self.assertTrue(self.read()['service_enabled'])
        self.edit(fail={'service:enabled': True})
        self.call('internet_vendor_disable', False)
        self.assertFalse(self.backup.exists())

    def test_config_failure_reverts_only_owned_staging(self):
        self.edit(fail={'set:mtkhnat.global.actype': True})
        self.call('internet_vendor_disable', False)
        self.assertEqual(self.read()['pending'], {})
        self.assertEqual(self.read()['config']['mtkhnat.global.enable'], '1')
        self.assertTrue(self.backup.exists())
        self.assertTrue(self.marker.exists())
        self.edit(fail={})
        self.call('internet_vendor_disable')
        self.edit(fail={'set:mtkhnat.global.actype': True})
        self.call('internet_vendor_restore', False)
        self.assertEqual(self.read()['pending'], {})
        self.assertEqual(self.read()['config']['mtkhnat.global.enable'], '0')

    def test_reboot_restore_retry_preserves_saved_snapshot(self):
        self.call('internet_vendor_disable')
        self.edit(fail={'insmod:shortcut_fe_ipv6': True})
        self.call('internet_vendor_restore', False)
        self.assertEqual(self.marker.read_text(), 'restore\n')
        self.assertTrue(self.backup.exists())
        self.edit(fail={})
        # A new sourced shell models retry after process restart/reboot.
        self.call('internet_vendor_restore')
        self.assertEqual(self.read()['hook'], 1)
        self.assertFalse(self.backup.exists())

    def test_manual_nonforced_settings_refresh_intent_before_reasserting(self):
        state = self.read(); state['config']['mtkhnat.global.enable'] = '0'; self.write(state)
        self.call('internet_vendor_disable')
        state = self.read(); state['config']['mtkhnat.global.enable'] = '1'; state['config']['mtkhnat.global.actype'] = '2'; state['hook'] = 1; self.write(state)
        self.call('internet_vendor_disable')
        self.call('internet_vendor_restore')
        self.assertEqual(self.read()['config']['mtkhnat.global.enable'], '1')
        self.assertEqual(self.read()['config']['mtkhnat.global.actype'], '2')
        self.assertEqual(self.read()['hook'], 1)

    def test_manual_differing_cleanup_config_and_runtime_are_preserved(self):
        self.call('internet_vendor_disable')
        state = self.read(); state['config']['mtkhnat.global.actype'] = '2'; state['hook'] = 1; self.write(state)
        self.call('internet_vendor_restore')
        self.assertEqual(self.read()['config']['mtkhnat.global.actype'], '2')
        self.assertEqual(self.read()['hook'], 1)
        self.assertFalse((self.root / 'sys/module/shortcut_fe').exists())

    def test_invalid_original_option_never_publishes_bad_backup(self):
        state = self.read(); state['config']['mtkhnat.global.actype'] = '99'; self.write(state)
        self.call('internet_vendor_disable', False)
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.read()['events'], [])

    def test_section_change_or_corrupt_backup_retains_recovery(self):
        self.call('internet_vendor_disable')
        state = self.read(); state['config']['mtkhnat.global'] = 'replacement'; self.write(state)
        result = self.call('internet_vendor_restore', False)
        self.assertIn('vendor_section_changed', result.stdout)
        self.assertTrue(self.backup.exists())
        state['config']['mtkhnat.global'] = 'mtkhnat'; self.write(state)
        self.backup.write_text('bad\n')
        self.call('internet_vendor_disable', False)
        self.assertTrue(self.backup.exists())

    def test_unknown_acceleration_fails_native_router_hooks_are_noop(self):
        (self.root / 'sys/module/ecm').mkdir()
        result = self.call('internet_vendor_disable', False)
        self.assertIn('vendor_acceleration_unknown', result.stdout)
        for directory in (self.root / 'sys/module').iterdir(): directory.rmdir()
        (self.root / 'etc/config/mtkhnat').unlink()
        for file in (self.root / 'sys/kernel/debug/hnat').iterdir(): file.unlink()
        (self.root / 'sys/kernel/debug/hnat').rmdir()
        self.call('internet_vendor_disable')
        self.call('internet_vendor_inactive')
        self.call('internet_vendor_restore')
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.read()['events'], [])


class CombinedCleanupTests(unittest.TestCase):
    def test_complete_policy_with_audited_absence_and_reboot_restoration(self):
        spec = importlib.util.spec_from_file_location('internet_vendor_fixture', ROOT / 'test-internet.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for initially_absent in (True, False):
            with self.subTest(initially_absent=initially_absent):
                vendor = VendorTests()
                vendor.setUp()
                try:
                    with tempfile.TemporaryDirectory(prefix='wcl-vendor-full-policy-') as directory:
                        harness = module.InternetHarness(directory)
                        dispatcher = harness.path / 'vendor-bin'
                        dispatcher.mkdir()
                        uci = dispatcher / 'uci'
                        uci.write_text('#!/bin/sh\ncase "$*" in\n'
                                       ' *mtkhnat*|*"show network"*) exec ' + shlex.quote(str(vendor.bin / 'uci')) + ' "$@";;\n'
                                       ' *) exec ' + shlex.quote(str(harness.path / 'bin/uci')) + ' "$@";;\nesac\n')
                        uci.chmod(0o755)
                        (harness.path / 'vendor-noop.sh').write_text(
                            'INTERNET_VENDOR_ROOT=' + shlex.quote(str(vendor.root)) + '\nexport INTERNET_VENDOR_ROOT\n'
                            'PATH=' + shlex.quote(str(dispatcher) + ':' + str(vendor.bin)) + ':"$PATH"\nexport PATH\n'
                            '. ' + shlex.quote(str(HELPER)) + '\n'
                            'internet_vendor_control_write() { vendor-control "$1" "$2"; }\n')
                        if initially_absent: vendor.unload_hnat()
                        harness.run()
                        self.assertEqual(harness.status['internet_state'], 'blocked')
                        self.assertTrue(harness.status['active'])
                        self.assertIn('ether saddr', harness.rules)
                        self.assertIn('ip6 daddr', harness.rules)
                        self.assertEqual(vendor.read()['config']['mtkhnat.global.enable'], '0')
                        self.assertTrue(all(not (vendor.root / 'sys/module' / name).exists() for name in MODULES))
                        vendor.unload_hnat()  # Boot with forced persistent enable=0.
                        harness.run()
                        self.assertEqual(harness.status['internet_state'], 'blocked')
                        harness.run(action='cleanup')
                        self.assertEqual(harness.status['internet_state'], 'inactive')
                        self.assertEqual(vendor.read()['config']['mtkhnat.global.enable'], '1')
                        self.assertEqual((vendor.root / 'sys/module/mtkhnat').exists(), not initially_absent)
                        if not initially_absent:
                            self.assertEqual((vendor.read()['hook'], vendor.read()['selector']), (1, 0))
                        self.assertFalse((harness.path / 'backup/vendor-acceleration-backup').exists())
                finally:
                    vendor.tearDown()

    def test_vendor_restore_failure_retains_vendor_recovery_after_native_restore(self):
        spec = importlib.util.spec_from_file_location('internet_fixture', ROOT / 'test-internet.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix='wcl-mixed-cleanup-') as directory:
            harness = module.InternetHarness(directory)
            (harness.path / 'vendor-noop.sh').write_text('''
internet_vendor_disable() { printf 'fixture backup\\n' > "$INTERNET_BACKUP_DIR/vendor-acceleration-backup"; }
internet_vendor_inactive() { :; }
internet_vendor_restore() {
 [ ! -f "$FIX/vendor-restore-fails" ] || return 1
 rm -f "$INTERNET_BACKUP_DIR/vendor-acceleration-backup"
}
''')
            harness.run()
            native = harness.path / 'backup/offload-backup'
            vendor = harness.path / 'backup/vendor-acceleration-backup'
            self.assertTrue(native.exists())
            self.assertTrue(vendor.exists())
            failure = harness.path / 'vendor-restore-fails'
            failure.touch()
            harness.run(action='cleanup', expect_ok=False)
            self.assertFalse(native.exists())
            self.assertTrue(vendor.exists())
            self.assertEqual(harness.status['internet_state'], 'error')
            self.assertEqual(harness.status['error'], 'vendor_restore_failed')
            failure.unlink()
            harness.run(action='cleanup')
            self.assertFalse(vendor.exists())
            self.assertEqual(harness.status['internet_state'], 'inactive')


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""Release gate for the current IPK on the audited GL native runtime.

Real opkg installs/queries/removes the IPK in an isolated offline root. Its
custom prerm and init cleanup bodies run under GL BusyBox ash with hardware
APIs and absolute router paths mapped to the existing restoration fixture.
The offline opkg wrapper is a test adapter, not a production uninstall script.
No real router, kernel firewall, radio, or HNAT hardware is exercised here.
"""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
NAME = 'luci-app-wifi-client-limiter'
VERSION = re.search(r'^PKG_VERSION:=(.+)$', (ROOT / 'Makefile').read_text(), re.M).group(1)
IPK = Path(os.environ.get('WCL_TEST_IPK', ROOT / f'packages/{VERSION}/luci-app-wifi-client-limiter_{VERSION}-1_all.ipk'))


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def members(archive):
    with tarfile.open(fileobj=io.BytesIO(archive)) as package:
        return {item.name.removeprefix('./'): package.extractfile(item).read()
                for item in package.getmembers() if item.isfile()}


class NativeReleaseLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recovery = module('release_recovery_fixture', 'tests/fixtures/native_package_lifecycle.py')
        cls.recovery.NativeOpkgRuntime.setUpClass()
        cls.recovery.NativePackageFixture.setUpClass()
        cls.reader = module('release_reader_fixture', 'test-vendor-hook-reader.py')
        cls.reader.NativeHookReaderTests.setUpClass()

    def test_installed_fixed_reader_and_normal_removal_restore_owned_settings(self):
        self.assertTrue(IPK.is_file(), 'Build the release IPK before running this gate')
        with tarfile.open(IPK) as package:
            payload = members(package.extractfile('./data.tar.gz').read())
            controls = members(package.extractfile('./control.tar.gz').read())
        self.assertIn(f'Version: {VERSION}-1\n'.encode(), controls['control'])
        self.assertEqual(payload['usr/share/wifi-client-limiter/version'], (VERSION + '\n').encode())

        fixture = self.recovery.NativePackageFixture()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root = fixture.path / 'package-root'
        info = root / 'usr/lib/opkg/info'
        info.mkdir(parents=True)
        (root / 'var/lock').mkdir(parents=True)
        dependencies = re.search(rb'^Depends: (.*)$', controls['control'], re.M).group(1).decode().split(', ')
        records = []
        for dependency in dependencies:
            dependency = dependency.split(' ')[0]
            records.append(f'Package: {dependency}\nVersion: 1\nStatus: install user installed\nArchitecture: all\n')
            (info / (dependency + '.list')).touch()
        status = root / 'usr/lib/opkg/status'
        status.write_text('\n'.join(records) + '\n')
        config = root / 'opkg.conf'
        config.write_text('dest root /\nlists_dir ext /var/opkg-lists\narch all 100\noption check_signature 0\n')
        fixture.program('update_plugins_db', '#!/bin/sh\nexit 0\n')
        for utility in ('gzip', 'gunzip', 'tar'):
            executable = shutil.which(utility)
            self.assertIsNotNone(executable)
            fixture.program(utility, '#!/bin/sh\nexec ' + shlex.quote(executable) + ' "$@"\n')
        stock = self.recovery.NativeOpkgRuntime.stock
        command = [str(fixture.qemu), '-L', str(stock), str(stock / 'bin/opkg'), '-f', str(config), '-o', str(root)]

        def opkg(*arguments):
            return subprocess.run(command + list(arguments), env=fixture.env, text=True,
                                  capture_output=True, timeout=40)

        installed = opkg('install', str(IPK))
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        current = opkg('status', NAME)
        self.assertEqual(current.returncode, 0)
        self.assertEqual(current.stderr, '')
        self.assertIn(f'Version: {VERSION}-1\n', current.stdout)
        self.assertIn('Status: install user installed\n', current.stdout)
        share = root / 'usr/share/wifi-client-limiter'
        helper = share / 'vendor-acceleration.sh'
        self.assertEqual(helper.read_bytes(), (ROOT / 'files/usr/share/wifi-client-limiter/vendor-acceleration.sh').read_bytes())
        self.assertEqual(hashlib.sha256(helper.read_bytes()).digest(), hashlib.sha256(payload['usr/share/wifi-client-limiter/vendor-acceleration.sh']).digest())

        # Execute the actual installed reader, unchanged, with both 128/256 KiB
        # logs and enabled/disabled fresh readbacks. Each call has a 4s timeout.
        reader = self.reader.NativeHookReaderTests()
        reader.setUp()
        self.addCleanup(reader.doCleanups)
        reader.env['TEST_HELPER'] = str(helper)
        reader.test_large_ring_and_many_new_messages_finish_with_fresh_state()
        reader.test_full_ring_rotation_keeps_fresh_readback()
        reader.test_new_unrelated_message_cannot_reuse_stale_conflicting_hooks()
        reader.test_partial_output_from_failed_parser_provider_is_unknown()

        # Reuse the mapped production init/package-hooks bodies, but source
        # helpers installed from this IPK. Only hardware writes are simulated;
        # the fixed reader is never replaced or monkey-patched.
        fixture.payload = payload
        for variable, filename in (
            ('WCL_UNINSTALL_POLICY', 'internet-policy.sh'),
            ('WCL_UNINSTALL_VENDOR', 'vendor-acceleration.sh'),
            ('INTERNET_VENDOR_HELPER', 'vendor-acceleration.sh'),
            ('WCL_UNINSTALL_STATION', 'station-adapter.sh'),
            ('WCL_UNINSTALL_CHECKSUM', 'posix-cksum.sh'),
            ('INTERNET_CHECKSUM_HELPER', 'posix-cksum.sh'),
        ):
            fixture.env[variable] = str(share / filename)
        fixture.prepare_package_hooks()
        (fixture.bin / 'internet-fixture.native').write_text(
            '. "$WCL_UNINSTALL_POLICY"\n'
            'internet_vendor_load || exit 1\n'
            'internet_vendor_control_write() { vendor-control "$1" "$2"; }\n'
            'internet_cleanup\n')
        kernel = fixture.vendor.read()
        kernel.update(ring_lines=64, reads=64,
                      dmesg=''.join('[%d.000] bounded lifecycle log\n' % index for index in range(64)))
        fixture.vendor.write(kernel)

        # The opkg offline root skips hooks normally. This test-only adapter
        # enables the actual custom prerm body under native ash while mapping
        # its one absolute package-hooks entry point into our private fixture.
        # --force-postinstall enables offline script execution, not force-removal.
        hook_entry = fixture.bin / 'release-package-hooks'
        fixture.program(hook_entry.name, '#!/bin/sh\nexec ' + shlex.join(fixture.shell) + ' ' + shlex.quote(str(fixture.path / 'package-hooks-fixture')) + ' "$@"\n')
        custom_prerm = fixture.path / 'release-prerm-pkg'
        custom_prerm.write_text(controls['prerm-pkg'].decode().replace('/usr/share/wifi-client-limiter/package-hooks', str(hook_entry)))
        prerm = info / (NAME + '.prerm')
        prerm.write_text('#!/bin/sh\nunset IPKG_INSTROOT\nprintf "called\\n" >> ' + shlex.quote(str(fixture.path / 'release-prerm-called')) + '\nexec ' + shlex.join(fixture.shell) + ' ' + shlex.quote(str(custom_prerm)) + ' "$@"\n')
        prerm.chmod(0o755)
        postrm_body = controls['postrm'].decode()
        for original, destination in (
            ('/etc/config/wifi-client-limiter', fixture.path / 'config/wifi-client-limiter'),
            ('/tmp/luci-', fixture.path / 'luci-'),
            ('/tmp/wifi-client-limiter', fixture.path / 'state'),
            ('/etc/init.d/rpcd', fixture.path / 'no-rpcd'),
        ):
            postrm_body = postrm_body.replace(original, str(destination))
        mapped_postrm = fixture.path / 'release-postrm'
        mapped_postrm.write_text(postrm_body)
        postrm = info / (NAME + '.postrm')
        postrm.write_text('#!/bin/sh\nunset IPKG_INSTROOT\nexec ' + shlex.join(fixture.shell) + ' ' + shlex.quote(str(mapped_postrm)) + ' "$@"\n')
        postrm.chmod(0o755)

        removed = opkg('--force-postinstall', 'remove', NAME)
        self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
        self.assertEqual((fixture.path / 'release-prerm-called').read_text(), 'called\n')
        self.assertFalse(helper.exists())
        self.assertFalse((root / 'www/luci-static/resources/view/wifi-client-limiter.js').exists())
        remaining = opkg('list-installed', NAME)
        self.assertEqual((remaining.returncode, remaining.stdout, remaining.stderr), (0, '', ''))
        self.assertNotIn('Package: ' + NAME + '\n', status.read_text())
        self.assertEqual(status.read_text().count('Package: '), len(dependencies))
        self.assertFalse((fixture.path / 'owned-table').exists())
        backup = fixture.path / 'backup'
        self.assertTrue(not backup.exists() or not any(backup.iterdir()))
        restored = fixture.vendor.read()
        self.assertEqual((restored['hook'], restored['selector'], restored['service_enabled']), (1, 0, True))
        self.assertLessEqual(len(restored['dmesg'].splitlines()), 64)
        self.assertTrue((fixture.path / 'sys/module/xt_FLOWOFFLOAD').is_dir())
        self.assertTrue((fixture.path / 'config/wifi-client-limiter.backup').is_file())
        for option in ('flow_offloading', 'flow_offloading_hw'):
            result = fixture.run_shell('uci -q get firewall.main.' + option, check=True)
            self.assertEqual(result.stdout.strip(), '1')

    def test_failed_prerm_retains_installed_record_then_normal_remove_clears_it(self):
        name='luci-app-wifi-client-limiter'
        ipk=IPK
        with tarfile.open(ipk) as package:data=package.extractfile('./control.tar.gz').read()
        with tarfile.open(fileobj=io.BytesIO(data)) as package:control=package.extractfile('./control').read().decode()
        dependencies=re.search(r'^Depends: (.*)$',control,re.M).group(1).split(', ')
        with tempfile.TemporaryDirectory(prefix='wcl-native-opkg-') as directory:
            path=Path(directory);info=path/'usr/lib/opkg/info';info.mkdir(parents=True)
            (path/'var/lock').mkdir(parents=True);(path/'bin').mkdir()
            # Offline dependencies are metadata-only fixtures. The limiter IPK
            # is installed, recorded, queried and removed by the real binary.
            records=[]
            for dependency in dependencies:
                dependency=dependency.split(' ')[0]
                records.append(f'Package: {dependency}\nVersion: 1\nStatus: install ok installed\nArchitecture: all\n')
                (info/(dependency+'.list')).touch()
            status=path/'usr/lib/opkg/status';status.write_text('\n'.join(records)+'\n')
            config=path/'opkg.conf';config.write_text('dest root /\nlists_dir ext /var/opkg-lists\narch all 100\noption check_signature 0\n')
            updater=path/'bin/update_plugins_db';updater.write_text('#!/bin/sh\nexit 0\n');updater.chmod(0o755)
            for utility in ('gzip','gunzip','tar'):
                executable=shutil.which(utility)
                self.assertIsNotNone(executable,'package extraction utility must be available')
                (path/'bin'/utility).symlink_to(executable)
            command=[str(self.recovery.NativeOpkgRuntime.qemu),'-L',str(self.recovery.NativeOpkgRuntime.stock),str(self.recovery.NativeOpkgRuntime.stock/'bin/opkg'),'-f',str(config),'-o',str(path)]
            environment=dict(os.environ,PATH=str(path/'bin'))
            def opkg(*arguments):
                return subprocess.run(command+list(arguments),env=environment,text=True,capture_output=True,timeout=30)
            installed=opkg('install',str(ipk))
            self.assertEqual(installed.returncode,0,installed.stdout+installed.stderr)
            initial=opkg('status',name)
            self.assertIn('Status: install user installed',initial.stdout)
            self.assertEqual(opkg('list-installed',name).stdout.strip(),name+' - ' + VERSION + '-1')
            helper=path/'usr/share/wifi-client-limiter/internet-policy.sh'
            helper_before=helper.read_bytes()
            ui=path/'www/luci-static/resources/view/wifi-client-limiter.js'
            ui_before=ui.read_bytes()
            # --force-postinstall makes offline opkg execute scripts; it is a
            # fixture switch, never a removal bypass. These two controlled
            # scripts touch only this temp root and contain no router commands.
            prerm=info/(name+'.prerm')
            prerm.write_text('#!/bin/sh\nprintf "called\\n" >> '+shlex.quote(str(path/'prerm-called'))+'\nexit 1\n');prerm.chmod(0o755)
            postrm=info/(name+'.postrm');postrm.write_text('#!/bin/sh\nexit 0\n');postrm.chmod(0o755)
            failed=opkg('--force-postinstall','remove',name)
            self.assertEqual(failed.returncode,255,failed.stdout+failed.stderr)
            self.assertIn('prerm script returned status 1',failed.stderr)
            self.assertEqual((path/'prerm-called').read_text(),'called\n')
            record=opkg('status',name)
            self.assertEqual(record.returncode,0)
            self.assertIn(f'Version: {VERSION}-1\n',record.stdout)
            self.assertIn('Status: deinstall user installed\n',record.stdout)
            self.assertIn('Status: deinstall user installed\n',status.read_text())
            self.assertEqual(opkg('list-installed',name).stdout.strip(),name+' - ' + VERSION + '-1')
            self.assertEqual(helper.read_bytes(),helper_before)
            self.assertEqual(ui.read_bytes(),ui_before)
            # Same normal removal operation succeeds once prerm succeeds.
            prerm.write_text('#!/bin/sh\nexit 0\n')
            removed=opkg('--force-postinstall','remove',name)
            self.assertEqual(removed.returncode,0,removed.stdout+removed.stderr)
            self.assertEqual(opkg('status',name).stdout,'')
            self.assertEqual(opkg('list-installed',name).stdout,'')
            self.assertFalse(helper.exists());self.assertFalse(ui.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)

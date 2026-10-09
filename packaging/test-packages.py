#!/usr/bin/env python3
"""Build native archives and check payload, ownership, defaults and APK signing."""
import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def run(*args, env=None):
    return subprocess.run(args, check=True, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def tar_members(data):
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        return {m.name.removeprefix('./'): (m, archive.extractfile(m).read() if m.isfile() else None) for m in archive.getmembers()}


class PackageTests(unittest.TestCase):
    apk_tool = None

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='wifi-client-limiter-package-test-')
        cls.work = Path(cls.temp.name)
        cls.ipk_out = cls.work / 'ipk'
        run('sh', str(ROOT / 'scripts/build-ipk.sh'), str(cls.ipk_out))
        cls.ipk = next(cls.ipk_out.glob('*.ipk'))
        cls.outer = tar_members(cls.ipk.read_bytes())
        cls.control = tar_members(cls.outer['control.tar.gz'][1])
        cls.payload = tar_members(cls.outer['data.tar.gz'][1])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_native_ipk_container(self):
        self.assertEqual(set(self.outer), {'debian-binary', 'control.tar.gz', 'data.tar.gz'})
        self.assertEqual(self.outer['debian-binary'][1], b'2.0\n')
        metadata = self.control['control'][1].decode()
        self.assertIn('Package: luci-app-wifi-client-limiter\n', metadata)
        self.assertIn('Version: 0.5.10-1\n', metadata)
        self.assertIn('Architecture: all\n', metadata)
        dependencies = set(re.search(r'^Depends: (.+)$', metadata, re.M)[1].split(', '))
        self.assertTrue({'luci-base', 'rpcd', 'iw', 'iwinfo', 'wireless-tools', 'jshn', 'jsonfilter', 'ubus', 'uci', 'procd', 'base-files', 'nftables-json', 'kmod-nft-netdev'} <= dependencies)
        self.assertTrue({'firewall', 'firewall4'}.isdisjoint(dependencies))

    def test_sdk_ipk_validator_rejects_changed_missing_or_nonexecutable_helper(self):
        validator = str(ROOT / 'packaging/verify-sdk-helper.py')
        run('python3', validator, str(self.ipk))
        helper = 'usr/share/wifi-client-limiter/mtk-acl-query-aarch64'
        for mutation in ('changed', 'truncated', 'missing', 'mode', 'symlink', 'duplicate'):
            with self.subTest(mutation=mutation):
                payload = io.BytesIO()
                with tarfile.open(fileobj=payload, mode='w:gz') as archive:
                    for relative, (original_info, original_data) in self.payload.items():
                        info, data = copy.copy(original_info), original_data
                        if relative == helper:
                            if mutation == 'missing': continue
                            if mutation == 'changed': data = data[:-1] + bytes([data[-1] ^ 1])
                            if mutation == 'truncated': data = data[:-64]
                            if mutation == 'mode': info.mode = 0o644
                            if mutation == 'symlink':
                                info.type, info.linkname, data = tarfile.SYMTYPE, 'other-query-helper', None
                            info.size = len(data) if data is not None else 0
                            if mutation == 'duplicate': archive.addfile(info, io.BytesIO(data))
                        archive.addfile(info, io.BytesIO(data) if data is not None else None)
                package = self.work / f'sdk-invalid-{mutation}.ipk'
                with tarfile.open(package, mode='w:gz') as archive:
                    for name, (original_info, original_data) in self.outer.items():
                        info = copy.copy(original_info)
                        data = payload.getvalue() if name == 'data.tar.gz' else original_data
                        info.size = len(data)
                        archive.addfile(info, io.BytesIO(data))
                result = subprocess.run(['python3', validator, str(package)], cwd=ROOT,
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, mutation)
                self.assertIn('SDK helper verification failed', result.stderr)

    def test_complete_runtime_payload_and_root_ownership(self):
        for source in (ROOT / 'files').rglob('*'):
            if source.is_file():
                relative = source.relative_to(ROOT / 'files').as_posix()
                self.assertIn(relative, self.payload)
                if relative == 'usr/share/luci/menu.d/luci-app-wifi-client-limiter.json':
                    expected = json.loads(source.read_bytes())
                    digest = hashlib.sha256((ROOT / 'files/www/luci-static/resources/view/wifi-client-limiter.js').read_bytes()).hexdigest()
                    expected['admin/services/wifi-client-limiter']['action']['path'] = 'wifi-client-limiter-' + digest
                    self.assertEqual(json.loads(self.payload[relative][1]), expected)
                else:
                    self.assertEqual(self.payload[relative][1], source.read_bytes(), relative)
        catalogs = [name for name in self.payload if name.endswith('.lmo')]
        self.assertEqual(len(catalogs), 9)
        for info, data in self.payload.values():
            self.assertEqual((info.uid, info.gid), (0, 0), info.name)
            if info.isfile():
                if info.name.removeprefix('./') == 'usr/share/wifi-client-limiter/mtk-acl-query-aarch64':
                    # Optional query-only native adapter for the two audited
                    # AArch64 GL drivers; other architectures use hostapd.
                    self.assertEqual(data[:6], b'\x7fELF\x02\x01')
                    self.assertEqual(int.from_bytes(data[18:20], 'little'), 183)
                else:
                    self.assertFalse(data.startswith(b'\x7fELF'), info.name)
        for filename in ('usr/sbin/wifi-client-limiter', 'etc/init.d/wifi-client-limiter', 'usr/share/wifi-client-limiter/package-hooks', 'usr/share/wifi-client-limiter/internet-policy.sh', 'usr/share/wifi-client-limiter/firewall-reload.sh', 'usr/share/wifi-client-limiter/station-adapter.sh', 'usr/share/wifi-client-limiter/vendor-acceleration.sh', 'usr/libexec/rpcd/wifi-client-limiter', 'etc/uci-defaults/95-wifi-client-limiter'):
            self.assertEqual(self.payload[filename][0].mode & 0o777, 0o755)
        self.assertEqual(self.payload['usr/share/wifi-client-limiter/mtk-acl-query-aarch64'][0].mode & 0o777, 0o755)

    def test_menu_loads_complete_content_addressed_view(self):
        canonical = self.payload['www/luci-static/resources/view/wifi-client-limiter.js'][1]
        digest = hashlib.sha256(canonical).hexdigest()
        menu = json.loads(self.payload['usr/share/luci/menu.d/luci-app-wifi-client-limiter.json'][1])
        action = menu['admin/services/wifi-client-limiter']['action']
        self.assertEqual(action, {'type': 'view', 'path': 'wifi-client-limiter-' + digest})
        alias = 'www/luci-static/resources/view/' + action['path'] + '.js'
        self.assertEqual(self.payload[alias][1], canonical)
        self.assertEqual(self.payload[alias][0].mode & 0o777, 0o644)
        self.assertIn(b"_('Protect device')", self.payload[alias][1])

    def test_view_revision_changes_url_even_with_identical_timestamps(self):
        # An independent package upgrade must not reuse the firmware's cached
        # resource URL. Reproducible builds deliberately keep file times equal.
        module_paths = []
        for version in ('previous UI', 'updated UI'):
            stage = self.work / ('view-' + version.replace(' ', '-'))
            view = stage / 'www/luci-static/resources/view/wifi-client-limiter.js'
            menu = stage / 'usr/share/luci/menu.d/luci-app-wifi-client-limiter.json'
            view.parent.mkdir(parents=True)
            menu.parent.mkdir(parents=True)
            view.write_text(version)
            shutil.copyfile(ROOT / 'files/usr/share/luci/menu.d/luci-app-wifi-client-limiter.json', menu)
            os.utime(view, (1767225600, 1767225600))
            run('sh', str(ROOT / 'packaging/stage-view.sh'), str(stage))
            module = json.loads(menu.read_text())['admin/services/wifi-client-limiter']['action']['path']
            module_paths.append(module)
            self.assertEqual((view.parent / (module + '.js')).read_text(), version)
            self.assertEqual(view.read_text(), version)
            self.assertEqual(int(view.stat().st_mtime), 1767225600)
        self.assertNotEqual(*module_paths)

    def test_sdk_install_recipe_produces_same_menu_and_view(self):
        # Execute the actual SDK install macro with host copy commands. This
        # checks the SDK entry point without claiming a target SDK compilation.
        sdk = self.work / 'sdk-install-fixture'
        sdk.mkdir()
        (sdk / 'rules.mk').touch()
        (sdk / 'package.mk').touch()
        stage = sdk / 'stage'
        makefile = sdk / 'Makefile'
        makefile.write_text('''CP := cp -R
INSTALL_DIR := mkdir -p
INSTALL_DATA := cp -f
include ''' + str(ROOT / 'Makefile') + '''
install-test:
\tmkdir -p ''' + str(stage) + '''
\t$(call Package/luci-app-wifi-client-limiter/install,'''+str(stage)+''')
''')
        run('make', '--no-print-directory', '-f', str(makefile),
            'TOPDIR=' + str(sdk), 'INCLUDE_DIR=' + str(sdk), 'install-test')
        menu_path = 'usr/share/luci/menu.d/luci-app-wifi-client-limiter.json'
        self.assertEqual((stage / menu_path).read_bytes(), self.payload[menu_path][1])
        action = json.loads((stage / menu_path).read_bytes())['admin/services/wifi-client-limiter']['action']
        alias = 'www/luci-static/resources/view/' + action['path'] + '.js'
        self.assertEqual((stage / alias).read_bytes(), self.payload[alias][1])

    def test_file_uninstaller_removes_only_valid_plugin_view_aliases(self):
        stage = self.work / 'uninstall-views'
        stage.mkdir()
        owned = stage / ('wifi-client-limiter-' + 'a' * 64 + '.js')
        unrelated = [stage / name for name in (
            'wifi-client-limiter-custom.js', 'wifi-client-limiter-' + 'a' * 63 + '.js',
            'wifi-client-limiter-' + 'g' * 64 + '.js', 'other-' + 'a' * 64 + '.js')]
        for file in [owned, *unrelated]:
            file.write_text('fixture')
        source = (ROOT / 'uninstall.sh').read_text()
        start = source.index('for view_file in ')
        end = source.index('\ndone\n', start) + len('\ndone\n')
        cleanup = source[start:end].replace('/www/luci-static/resources/view', str(stage))
        run('sh', '-c', cleanup)
        self.assertFalse(owned.exists())
        self.assertTrue(all(file.exists() for file in unrelated))

    def test_conffile_and_safe_defaults(self):
        self.assertEqual(self.control['conffiles'][1], b'/etc/config/wifi-client-limiter\n')
        config = self.payload['etc/config/wifi-client-limiter'][1].decode()
        for option, value in [('enabled', '0'), ('dry_run', '1'), ('guest_internet', '1'), ('max_clients', '25'), ('max_session', '0'), ('interval', '10'), ('reconnect_cooldown', '0'), ('reconnect_window', '300')]:
            self.assertRegex(config, rf"option\s+{option}\s+'{value}'")
        self.assertIsNone(re.search(r'^\s*list\s+network\s+', config, re.M))

    def test_package_hook_syntax_and_offline_guard(self):
        for filename in ('preinst', 'postinst', 'postinst-pkg', 'prerm', 'prerm-pkg', 'postrm'):
            script = self.work / filename
            script.write_bytes(self.control[filename][1])
            run('sh', '-n', str(script))
            if filename not in ('postinst', 'prerm'):
                env = dict(os.environ, IPKG_INSTROOT=str(self.work / 'offline-image'))
                run('sh', str(script), env=env)
        self.assertIn(b'default_postinst', self.control['postinst'][1])
        self.assertIn(b'default_prerm', self.control['prerm'][1])
        self.assertIn(b'backup', self.control['postrm'][1])

    def test_preinstall_requires_native_firewall_without_forcing_dependencies(self):
        with tempfile.TemporaryDirectory(prefix='wifi-client-limiter-preflight-') as directory:
            router = Path(directory)
            hook = router / 'preinst'
            source = self.control['preinst'][1].decode()
            for old, new in {
                '/etc/init.d/wifi-client-limiter': str(router / 'absent-service'),
                '/usr/share/wifi-client-limiter/internet-policy.sh': str(router / 'absent-policy'),
                '/usr/share/wifi-client-limiter/station-adapter.sh': str(router / 'absent-station'),
            }.items():
                source = source.replace(old, new)
            hook.write_text(source)
            # Deliberately omit nft here: a package manager can install its
            # declared dependencies after invoking the pre-install script.
            env = dict(os.environ, IPKG_INSTROOT='', PATH=str(router))
            missing = subprocess.run(['/bin/sh', str(hook)], env=env, capture_output=True)
            self.assertEqual(missing.returncode, 1)
            self.assertIn(b'firewall3 or firewall4', missing.stderr)
            offline = subprocess.run(['/bin/sh', str(hook)], env=dict(env, IPKG_INSTROOT=str(router / 'offline')), capture_output=True)
            self.assertEqual(offline.returncode, 0)
            for backend in ('fw3', 'fw4'):
                with self.subTest(backend=backend):
                    executable = router / backend
                    executable.write_text('#!/bin/sh\nexit 0\n')
                    executable.chmod(0o755)
                    result = subprocess.run(['/bin/sh', str(hook)], env=env, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    executable.unlink()

    def test_cleanup_before_upgrade_and_removal(self):
        # Exercise the lifecycle with disposable substitutes for existing router
        # service/helper paths. No package hook can touch the host filesystem.
        with tempfile.TemporaryDirectory(prefix='wifi-client-limiter-lifecycle-') as directory:
            router = Path(directory)
            service = router / 'service'
            helper = router / 'internet-policy.sh'
            station = router / 'station-adapter.sh'
            config = router / 'config'
            log = router / 'lifecycle.log'
            service.write_text('#!/bin/sh\nprintf "service %s\\n" "$1" >> "$LIFECYCLE_LOG"\nexit "${STOP_RESULT:-0}"\n')
            helper.write_text('#!/bin/sh\nprintf "policy %s\\n" "$1" >> "$LIFECYCLE_LOG"\nexit "${CLEANUP_RESULT:-0}"\n')
            station.write_text('#!/bin/sh\nprintf "station %s\\n" "$1" >> "$LIFECYCLE_LOG"\nexit "${STATION_CLEANUP_RESULT:-0}"\n')
            service.chmod(0o755)
            helper.chmod(0o755)
            station.chmod(0o755)
            (router / 'fw3').write_text('#!/bin/sh\nexit 0\n')
            (router / 'fw3').chmod(0o755)
            config.write_text('user configuration\n')
            replacements = {
                '/etc/init.d/wifi-client-limiter': str(service),
                '/usr/share/wifi-client-limiter/internet-policy.sh': str(helper),
                '/usr/share/wifi-client-limiter/station-adapter.sh': str(station),
                '/etc/config/wifi-client-limiter': str(config),
            }
            scripts = {
                'upgrade': self.control['preinst'][1].decode(),
                'remove': self.payload['usr/share/wifi-client-limiter/package-hooks'][1].decode(),
            }
            for kind, original in scripts.items():
                script = router / f'{kind}.sh'
                for old, new in replacements.items():
                    original = original.replace(old, new)
                script.write_text(original)
                args = ['sh', str(script)] + (['prerm'] if kind == 'remove' else [])
                env = dict(os.environ, LIFECYCLE_LOG=str(log), IPKG_INSTROOT='')
                env = dict(env, PATH=f'{router}:{os.environ["PATH"]}')
                with self.subTest(kind=kind, state='online'):
                    log.unlink(missing_ok=True)
                    subprocess.run(args, check=True, env=env, capture_output=True)
                    self.assertEqual(log.read_text().splitlines(), ['service stop', 'policy cleanup', 'station cleanup'])
                    self.assertEqual(config.read_text(), 'user configuration\n')
                    if kind == 'remove':
                        self.assertEqual(config.with_name(config.name + '.backup').read_bytes(), config.read_bytes())
                with self.subTest(kind=kind, state='offline'):
                    log.unlink()
                    subprocess.run(args, check=True, env=dict(env, IPKG_INSTROOT=str(router / 'image')), capture_output=True)
                    self.assertFalse(log.exists())
                with self.subTest(kind=kind, state='cleanup_failed'):
                    result = subprocess.run(args, env=dict(env, CLEANUP_RESULT='7'), capture_output=True)
                    self.assertEqual(result.returncode, 7)
                    self.assertEqual(config.read_text(), 'user configuration\n')
                with self.subTest(kind=kind, state='stop_failed'):
                    log.unlink()
                    result = subprocess.run(args, env=dict(env, STOP_RESULT='8'), capture_output=True)
                    self.assertEqual(result.returncode, 8)
                    self.assertEqual(log.read_text().splitlines(), ['service stop'])
                with self.subTest(kind=kind, state='station_cleanup_failed'):
                    log.unlink()
                    result = subprocess.run(args, env=dict(env, STATION_CLEANUP_RESULT='9'), capture_output=True)
                    self.assertEqual(result.returncode, 9)
                    self.assertEqual(config.read_text(), 'user configuration\n')
                    self.assertEqual(log.read_text().splitlines(), ['service stop', 'policy cleanup', 'station cleanup'])

    def test_firewall_include_ownership_and_pending_changes(self):
        with tempfile.TemporaryDirectory(prefix='wifi-client-limiter-include-') as directory:
            router = Path(directory)
            firewall = router / 'firewall'
            firewall.touch()
            state = router / 'uci.json'
            binary = router / 'uci'
            binary.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
path = Path(os.environ['UCI_TEST_STATE'])
state = json.loads(path.read_text())
args = [arg for arg in sys.argv[1:] if arg != '-q']
command, key = args[:2]
code = 0
if command == 'get':
    value = state['config'].get(key)
    if value is None: code = 1
    else: print(value)
elif command == 'changes':
    print(state.get('pending', ''), end='')
elif command == 'set':
    name, value = key.split('=', 1)
    state['config'][name] = value
elif command == 'delete':
    state['config'] = {name: value for name, value in state['config'].items() if name != key and not name.startswith(key + '.')}
elif command == 'commit':
    state['commits'] += 1
elif command == 'revert':
    pass
else: code = 2
path.write_text(json.dumps(state))
sys.exit(code)
''')
            binary.chmod(0o755)
            hook = router / 'package-hooks'
            source = self.payload['usr/share/wifi-client-limiter/package-hooks'][1].decode()
            for old, new in {
                '/etc/config/firewall': str(firewall),
                '/etc/config/wifi-client-limiter': str(router / 'limiter-config'),
                '/etc/init.d/wifi-client-limiter': str(router / 'absent-service'),
                '/usr/share/wifi-client-limiter/internet-policy.sh': str(router / 'absent-policy'),
                '/usr/share/wifi-client-limiter/station-adapter.sh': str(router / 'absent-station'),
            }.items():
                source = source.replace(old, new)
            hook.write_text(source)
            env = dict(os.environ, IPKG_INSTROOT='', UCI_TEST_STATE=str(state), PATH=f'{router}:{os.environ["PATH"]}')
            owned = {
                'firewall.wifi_client_limiter': 'include',
                'firewall.wifi_client_limiter.type': 'script',
                'firewall.wifi_client_limiter.path': '/usr/share/wifi-client-limiter/firewall-reload.sh',
                'firewall.wifi_client_limiter.fw4_compatible': '1',
                'firewall.wifi_client_limiter.reload': '1',
                'firewall.wifi_client_limiter.enabled': '1',
            }
            def execute(action, config, pending='', expected=0, extra=()):
                state.write_text(json.dumps({'config': config, 'pending': pending, 'commits': 0}))
                result = subprocess.run(['sh', str(hook), action, *extra], env=env, capture_output=True)
                self.assertEqual(result.returncode, expected, result.stderr)
                return json.loads(state.read_text())
            registered = execute('firewall-register', {})
            self.assertEqual(registered['config'], owned)
            self.assertEqual(registered['commits'], 1)
            previous = dict(owned)
            previous.pop('firewall.wifi_client_limiter.reload')
            migrated = execute('firewall-register', previous)
            self.assertEqual(migrated['config'], owned)
            self.assertEqual(migrated['commits'], 1)
            pending = "firewall.other.enabled='0'\n"
            blocked = execute('firewall-register', {}, pending, expected=1)
            self.assertEqual(blocked['config'], {})
            self.assertEqual(blocked['commits'], 0)
            existing = execute('firewall-register', owned, pending)
            self.assertEqual(existing['config'], owned)
            self.assertEqual(existing['commits'], 0)
            foreign = dict(owned, **{'firewall.wifi_client_limiter.path': '/etc/user-firewall.sh'})
            collision = execute('firewall-register', foreign, expected=1)
            self.assertEqual(collision['config'], foreign)
            self.assertEqual(collision['commits'], 0)
            untouched = execute('firewall-remove', foreign)
            self.assertEqual(untouched['config'], foreign)
            rejected = execute('firewall-remove', owned, pending, expected=1)
            self.assertEqual(rejected['config'], owned)
            self.assertEqual(rejected['commits'], 0)
            retained = execute('prerm', owned, extra=('upgrade',))
            self.assertEqual(retained['config'], owned)
            self.assertEqual(retained['commits'], 0)
            removed = execute('firewall-remove', owned)
            self.assertEqual(removed['config'], {})
            self.assertEqual(removed['commits'], 1)

    def test_ipk_reproducibility(self):
        second = self.work / 'ipk-second'
        run('sh', str(ROOT / 'scripts/build-ipk.sh'), str(second))
        self.assertEqual(hashlib.sha256(self.ipk.read_bytes()).digest(), hashlib.sha256(next(second.glob('*.ipk')).read_bytes()).digest())

    def test_native_apk_install_and_configuration_upgrade(self):
        if not self.apk_tool:
            self.skipTest('Native APK host tool not supplied')
        metadata = dict(re.findall(r'^([^:\n]+): (.+)$', self.control['control'][1].decode(), re.M))
        package_name = metadata['Package']
        version, release = metadata['Version'].rsplit('-', 1)
        upgrade_version = f'{version}-r{int(release) + 1}'
        dependencies = metadata['Depends'].replace(', ', ' ')
        env = dict(os.environ)
        env.pop('APK_SIGNING_KEY', None)
        env.pop('APK_PUBLIC_KEY', None)

        # Every mutating APK command targets a disposable /tmp root and disables
        # scripts. Fixture providers avoid needing real OpenWrt dependencies.
        with tempfile.TemporaryDirectory(prefix='wifi-client-limiter-native-apk-', dir='/tmp') as directory:
            work = Path(directory)
            router_root = work / 'router-root'
            router_root.mkdir()
            fixture_root = work / 'fixture-root'
            fixture_root.mkdir()
            fixture = work / 'fixture-router-dependencies-1-r1.apk'
            run(self.apk_tool, 'mkpkg', '--info', 'name:fixture-router-dependencies',
                '--info', 'version:1-r1', '--info', 'arch:noarch', '--info', 'license:MIT',
                '--info', 'description:Isolated package test dependencies',
                '--info', f'provides:{dependencies}', '--files', str(fixture_root), '--output', str(fixture))

            out = work / 'packages'
            run('sh', str(ROOT / 'scripts/build-apk-package.sh'), '--apk-tool', self.apk_tool, str(out), env=env)
            package = next(out.glob('*.apk'))
            native = (self.apk_tool, '--root', str(router_root), '--no-network', '--no-scripts', '--allow-untrusted')
            usermode = ()
            if os.geteuid() != 0:
                # No script execution is needed, so unprivileged test hosts do
                # not need a user/mount namespace or root-owned extracted files.
                native += ('--force-no-chroot',)
                usermode = ('--usermode',)
            run(*native, 'add', '--initdb', *usermode, str(fixture), str(package))
            for relative, (info, data) in self.payload.items():
                if info.isfile():
                    installed = router_root / relative
                    self.assertTrue(installed.is_file(), relative)
                    self.assertEqual(installed.read_bytes(), data, relative)
                    self.assertEqual(installed.stat().st_mode & 0o777, info.mode & 0o777, relative)
            database = router_root / 'lib/apk/db/installed'
            self.assertIn(f'P:{package_name}\nV:{version}-r{release}\nA:noarch\n', database.read_text())
            config = router_root / 'etc/config/wifi-client-limiter'
            defaults = config.read_bytes()
            changed, count = re.subn(rb"(option\s+max_clients\s+)'25'", rb"\g<1>'42'", defaults)
            self.assertEqual(count, 1)
            user_config = changed + b'\n# Keep this user configuration through upgrades.\n'
            config.write_bytes(user_config)

            stage = work / 'upgrade-root'
            hooks = work / 'upgrade-hooks'
            run('python3', str(ROOT / 'packaging/stage-package.py'), str(stage), 'apk', '--scripts-directory', str(hooks))
            new_defaults, count = re.subn(rb"(option\s+max_clients\s+)'25'", rb"\g<1>'30'", defaults)
            self.assertEqual(count, 1)
            (stage / 'etc/config/wifi-client-limiter').write_bytes(new_defaults)
            checksum = hashlib.sha256(new_defaults).hexdigest()
            (stage / f'lib/apk/packages/{package_name}.conffiles_static').write_text(
                f'/etc/config/wifi-client-limiter {checksum}\n')
            upgrade = work / f'{package_name}-{upgrade_version}.apk'
            run(self.apk_tool, 'mkpkg', '--info', f'name:{package_name}', '--info', f'version:{upgrade_version}',
                '--info', 'arch:noarch', '--info', 'license:MIT', '--info', 'description:Local test upgrade',
                '--info', f'depends:{dependencies}', '--files', str(stage), '--output', str(upgrade))
            run(*native, 'add', '--upgrade', *usermode, str(upgrade))
            self.assertEqual(config.read_bytes(), user_config)
            self.assertEqual(config.with_name(config.name + '.apk-new').read_bytes(), new_defaults)
            self.assertIn(f'P:{package_name}\nV:{upgrade_version}\nA:noarch\n', database.read_text())

    def test_native_apk_unsigned_and_signed(self):
        if not self.apk_tool:
            self.skipTest('Native APK host tool not supplied')
        out = self.work / 'apk'
        run('sh', str(ROOT / 'scripts/build-apk-package.sh'), '--apk-tool', self.apk_tool, str(out))
        package = next(out.glob('*.apk'))
        self.assertTrue((out / 'UNSIGNED-APK.txt').is_file())
        run(self.apk_tool, '--allow-untrusted', 'verify', str(package))
        run('python3', str(ROOT / 'packaging/verify-sdk-helper.py'), str(package), '--apk-tool', self.apk_tool)
        dump = run(self.apk_tool, '--allow-untrusted', 'adbdump', str(package)).stdout
        self.assertIn('arch: noarch', dump)
        self.assertIn('version: 0.5.10-r1', dump)
        self.assertIn('name: wifi-client-limiter', dump)
        self.assertIn('post-upgrade:', dump)
        self.assertIn('conffiles_static', dump)

        key = self.work / 'test-only.pem'
        public = self.work / 'test-only.pem.pub'
        run('openssl', 'ecparam', '-name', 'prime256v1', '-genkey', '-noout', '-out', str(key))
        run('openssl', 'pkey', '-in', str(key), '-pubout', '-out', str(public))
        env = dict(os.environ, APK_SIGNING_KEY=str(key), APK_PUBLIC_KEY=str(public))
        signed = self.work / 'apk-signed'
        run('sh', str(ROOT / 'scripts/build-apk-package.sh'), '--apk-tool', self.apk_tool, str(signed), env=env)
        signed_package = next(signed.glob('*.apk'))
        self.assertFalse((signed / 'UNSIGNED-APK.txt').exists())
        self.assertEqual((signed / 'wifi-client-limiter.pem.pub').read_bytes(), public.read_bytes())
        run(self.apk_tool, '--keys-dir', str(signed), 'verify', str(signed_package))
        run('python3', str(ROOT / 'packaging/verify-sdk-helper.py'), str(signed_package), '--apk-tool', self.apk_tool)
        self.assertGreater(signed_package.stat().st_size, package.stat().st_size - 500)

    def test_sdk_apk_validator_rejects_changed_missing_or_nonexecutable_helper(self):
        if not self.apk_tool:
            self.skipTest('Native APK host tool not supplied')
        helper = 'usr/share/wifi-client-limiter/mtk-acl-query-aarch64'
        for mutation in ('changed', 'missing', 'mode'):
            with self.subTest(mutation=mutation):
                stage = self.work / f'sdk-apk-stage-{mutation}'
                target = stage / helper
                target.parent.mkdir(parents=True)
                if mutation != 'missing':
                    data = (ROOT / 'files' / helper).read_bytes()
                    target.write_bytes(data[:-64] if mutation == 'changed' else data)
                    target.chmod(0o644 if mutation == 'mode' else 0o755)
                package = self.work / f'sdk-invalid-{mutation}.apk'
                run(self.apk_tool, 'mkpkg', '--info', 'name:fixture-sdk-helper',
                    '--info', 'version:1-r1', '--info', 'arch:noarch', '--info', 'license:MIT',
                    '--info', 'description:Local payload-validation fixture',
                    '--files', str(stage), '--output', str(package))
                result = subprocess.run(['python3', str(ROOT / 'packaging/verify-sdk-helper.py'),
                                         str(package), '--apk-tool', self.apk_tool], cwd=ROOT,
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, mutation)
                self.assertIn('SDK helper verification failed', result.stderr)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apk-tool', help='Path to an apk-tools 3 host executable')
    args = parser.parse_args()
    if args.apk_tool:
        PackageTests.apk_tool = str(Path(args.apk_tool).resolve())
    unittest.main(argv=['test-packages.py'], verbosity=2)

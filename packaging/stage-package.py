#!/usr/bin/env python3
"""Stage runtime files and OpenWrt lifecycle metadata for native packages."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def metadata():
    values = {}
    for line in (ROOT / 'packaging/package.env').read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        name, value = line.split('=', 1)
        values[name] = shlex.split(value)[0]
    return values


def write(path, text, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755 if executable else 0o644)


def script(body):
    return '#!/bin/sh\n# SPDX-License-Identifier: MIT\n' + body + '\n'


def stage(destination, kind, scripts_dir):
    m = metadata()
    name = m['PACKAGE_NAME']
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / 'files', destination, dirs_exist_ok=True)
    shutil.copytree(ROOT / 'packaging/root', destination, dirs_exist_ok=True)
    subprocess.run(['sh', str(ROOT / 'packaging/stage-view.sh'), str(destination)], check=True)
    share = destination / 'usr/share/wifi-client-limiter'
    shutil.copy2(ROOT / 'LICENSE', share / 'LICENSE')
    write(share / 'version', m['PACKAGE_VERSION'] + '\n')
    for file in destination.rglob('*'):
        if file.is_dir():
            file.chmod(0o755)
        elif file.is_file():
            rel = file.relative_to(destination).as_posix()
            executable = rel.startswith(('etc/init.d/', 'etc/uci-defaults/', 'usr/sbin/', 'usr/bin/', 'usr/libexec/rpcd/')) or rel.endswith(('/package-hooks', '/internet-policy.sh', '/firewall-reload.sh', '/station-adapter.sh', '/vendor-acceleration.sh', '/mtk-acl-query-aarch64'))
            file.chmod(0o755 if executable else 0o644)

    scripts_dir.mkdir(parents=True, exist_ok=True)
    preinst = script('''[ -n "${IPKG_INSTROOT:-}" ] && exit 0
if ! command -v fw3 >/dev/null 2>&1 && ! command -v fw4 >/dev/null 2>&1; then
    echo 'WiFi Client Limiter requires the existing OpenWrt firewall3 or firewall4 backend.' >&2
    exit 1
fi
if [ -x /etc/init.d/wifi-client-limiter ]; then
    /etc/init.d/wifi-client-limiter stop || exit $?
fi
if [ -x /usr/share/wifi-client-limiter/internet-policy.sh ]; then
    /usr/share/wifi-client-limiter/internet-policy.sh cleanup || exit $?
fi
if [ -x /usr/share/wifi-client-limiter/station-adapter.sh ]; then
    /usr/share/wifi-client-limiter/station-adapter.sh cleanup || exit $?
fi
exit 0''')
    postinst_pkg = script('''[ -n "${IPKG_INSTROOT:-}" ] && exit 0
/usr/share/wifi-client-limiter/package-hooks postinst
exit $?''')
    prerm_pkg = script('''[ -n "${IPKG_INSTROOT:-}" ] && exit 0
/usr/share/wifi-client-limiter/package-hooks prerm "$@"
exit $?''')
    postrm = script('''[ -n "${IPKG_INSTROOT:-}" ] && exit 0
if [ ! -f /etc/config/wifi-client-limiter ] && [ -f /etc/config/wifi-client-limiter.backup ]; then
    cp -p /etc/config/wifi-client-limiter.backup /etc/config/wifi-client-limiter
fi
rm -f /tmp/luci-indexcache /tmp/luci-indexcache.*
rm -rf /tmp/luci-modulecache /tmp/wifi-client-limiter
if [ -x /etc/init.d/rpcd ]; then /etc/init.d/rpcd restart || true; fi
exit 0''')
    write(scripts_dir / 'preinst', preinst, True)
    write(scripts_dir / 'postrm', postrm, True)

    if kind == 'ipk':
        deps = ', '.join(m['PACKAGE_DEPENDS'].split())
        write(scripts_dir / 'control', '\n'.join([
            f'Package: {name}',
            f"Version: {m['PACKAGE_VERSION']}-{m['PACKAGE_RELEASE']}",
            'Architecture: all',
            f"Maintainer: {m['PACKAGE_MAINTAINER']}",
            'License: MIT',
            'Section: luci',
            f"Source: package/{name}",
            f"URL: {m['PACKAGE_URL']}",
            f'Depends: {deps}',
            'Installed-Size: 0',
            f"Description: {m['PACKAGE_DESCRIPTION']}",
            '',
        ]))
        write(scripts_dir / 'conffiles', '/etc/config/wifi-client-limiter\n')
        write(scripts_dir / 'postinst-pkg', postinst_pkg, True)
        write(scripts_dir / 'prerm-pkg', prerm_pkg, True)
        for hook, function in [('postinst', 'default_postinst'), ('prerm', 'default_prerm')]:
            guard = '[ "${IPKG_NO_SCRIPT:-}" = "1" ] && exit 0\n' if hook == 'postinst' else ''
            write(scripts_dir / hook, script(guard + '''[ -s "${IPKG_INSTROOT:-}/lib/functions.sh" ] || exit 0
. "${IPKG_INSTROOT:-}/lib/functions.sh"
''' + function + ' "$0" "$@"'), True)
    else:
        package_files = destination / 'lib/apk/packages'
        package_files.mkdir(parents=True, exist_ok=True)
        files = sorted('/' + f.relative_to(destination).as_posix() for f in destination.rglob('*') if f.is_file() or f.is_symlink())
        write(package_files / (name + '.list'), '\n'.join(files) + '\n')
        config = destination / 'etc/config/wifi-client-limiter'
        write(package_files / (name + '.conffiles'), '/etc/config/wifi-client-limiter\n')
        write(package_files / (name + '.conffiles_static'), '/etc/config/wifi-client-limiter ' + hashlib.sha256(config.read_bytes()).hexdigest() + '\n')
        write(scripts_dir / 'post-install', script('''[ "${IPKG_NO_SCRIPT:-}" = "1" ] && exit 0
[ -s "${IPKG_INSTROOT:-}/lib/functions.sh" ] || exit 0
. "${IPKG_INSTROOT:-}/lib/functions.sh"
export root="${IPKG_INSTROOT:-}"
export pkgname="''' + name + '''"
add_group_and_user
default_postinst
''' + postinst_pkg.split('\n', 2)[2]), True)
        write(scripts_dir / 'post-upgrade', script('export PKG_UPGRADE=1\n' + (scripts_dir / 'post-install').read_text().split('\n', 2)[2]), True)
        write(scripts_dir / 'pre-upgrade', script('export PKG_UPGRADE=1\n' + preinst.split('\n', 2)[2]), True)
        write(scripts_dir / 'pre-deinstall', script('''[ -s "${IPKG_INSTROOT:-}/lib/functions.sh" ] || exit 0
. "${IPKG_INSTROOT:-}/lib/functions.sh"
export root="${IPKG_INSTROOT:-}"
export pkgname="''' + name + '''"
default_prerm
''' + prerm_pkg.split('\n', 2)[2]), True)

    epoch = int(os.environ.get('SOURCE_DATE_EPOCH', '1767225600'))
    for base in (destination, scripts_dir):
        for item in base.rglob('*'):
            os.utime(item, (epoch, epoch), follow_symlinks=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('kind', choices=('ipk', 'apk'))
    parser.add_argument('--scripts-directory', type=Path)
    args = parser.parse_args()
    scripts_path = args.scripts_directory or args.destination / 'CONTROL'
    if args.kind == 'apk' and args.scripts_directory is None:
        parser.error('APK scripts must be stored outside the package payload')
    stage(args.destination, args.kind, scripts_path)

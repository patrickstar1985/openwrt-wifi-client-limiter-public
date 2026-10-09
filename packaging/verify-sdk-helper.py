#!/usr/bin/env python3
"""Verify the actual SDK archive retains the audited optional helper bytes."""
# SPDX-License-Identifier: MIT
import argparse
import io
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HELPER = 'usr/share/wifi-client-limiter/mtk-acl-query-aarch64'


def verify_helper(package, expected, apk_tool=None):
    original = expected.read_bytes()
    if package.suffix == '.ipk':
        # OpenWrt's pinned ipkg-build produces a gzip tar container with a
        # data.tar.gz member. Inspect files directly without running hooks.
        with tarfile.open(package, mode='r:gz') as outer:
            payloads = [member for member in outer.getmembers()
                        if member.name.removeprefix('./') == 'data.tar.gz']
            if len(payloads) != 1 or not payloads[0].isfile():
                raise ValueError('IPK must contain exactly one regular data.tar.gz')
            contents = outer.extractfile(payloads[0]).read()
        with tarfile.open(fileobj=io.BytesIO(contents), mode='r:gz') as payload:
            helpers = [member for member in payload.getmembers()
                       if member.name.removeprefix('./') == HELPER]
            if len(helpers) != 1 or not helpers[0].isfile():
                raise ValueError('IPK must contain exactly one regular MTK query helper')
            data, mode = payload.extractfile(helpers[0]).read(), helpers[0].mode
    elif package.suffix == '.apk':
        if apk_tool is None:
            raise ValueError('APK verification requires the SDK host apk tool')
        # Signature integrity is verified separately by build-sdk-package.sh.
        # Native extract reads the payload; it installs nothing and runs no
        # package scripts. --allow-untrusted supports unsigned development APKs.
        with tempfile.TemporaryDirectory(prefix='wcl-sdk-helper-') as directory:
            subprocess.run([str(apk_tool), '--no-network', '--allow-untrusted',
                            'extract', '--no-chown', '--destination', directory,
                            str(package.resolve())], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            helper = Path(directory) / HELPER
            if helper.is_symlink() or not helper.is_file():
                raise ValueError('APK must contain a regular MTK query helper')
            data, mode = helper.read_bytes(), helper.stat().st_mode
    else:
        raise ValueError('Expected an IPK or APK package')
    if data != original:
        raise ValueError('SDK package changed the audited MTK query helper bytes')
    if stat.S_IMODE(mode) != 0o755:
        raise ValueError('SDK package changed the MTK query helper executable mode')
    return len(data)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--apk-tool', type=Path)
    arguments = parser.parse_args()
    try:
        size = verify_helper(arguments.package, ROOT / 'files' / HELPER, arguments.apk_tool)
    except (ValueError, OSError, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'SDK helper verification failed: {error}\n')
    print(f'Verified {arguments.package.name}: MTK query helper retains all {size} audited bytes and mode0755')

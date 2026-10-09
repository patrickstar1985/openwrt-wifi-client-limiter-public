#!/usr/bin/env python3
"""Reproducibly build the fixed Linux AArch64 query helper with GNU binutils."""
# SPDX-License-Identifier: MIT
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
DEFAULT_OUTPUT = HERE.parent / 'files/usr/share/wifi-client-limiter/mtk-acl-query-aarch64'


def validate_binary(binary):
    data = binary.read_bytes()
    if len(data) < 64:
        raise ValueError('Truncated ELF header')
    header = struct.unpack_from('<16sHHIQQQIHHHHHH', data)
    if header[0][:7] != b'\x7fELF\x02\x01\x01' or header[1:4] != (2, 183, 1):
        raise ValueError('Expected a little-endian ELF64 AArch64 executable')
    if header[9] != 56 or header[10] != 2:
        raise ValueError('Expected exactly a load segment and a GNU stack header')
    programs = [struct.unpack_from('<IIQQQQQQ', data, header[5] + i * 56)
                for i in range(header[10])]
    load, stack = programs
    if load[0:2] != (1, 5) or stack[0:2] != (0x6474E551, 6):
        raise ValueError('Expected read/execute code and non-executable stack')
    if not load[3] <= header[4] < load[3] + load[5]:
        raise ValueError('Entry point lies outside the file-backed code segment')
    if load[2] + load[5] > len(data) or load[5] != load[6]:
        raise ValueError('Invalid load segment extent')
    return data


def build(output, prefix):
    commands = {name: prefix + name for name in ('as', 'ld', 'objcopy')}
    for command in commands.values():
        if shutil.which(command) is None:
            raise SystemExit(f'Missing GNU AArch64 binutils command: {command}')
    environment = dict(os.environ, LC_ALL='C', SOURCE_DATE_EPOCH='1767225600')
    with tempfile.TemporaryDirectory(prefix='mtk-acl-build-') as directory:
        work = Path(directory)
        obj, raw, stripped = (work / name for name in ('query.o', 'query.elf', 'query.stripped'))
        subprocess.run([commands['as'], '-march=armv8-a', '-o', str(obj),
                        str(HERE / 'mtk-acl-query-aarch64.S')], check=True, env=environment)
        subprocess.run([commands['ld'], '-nostdlib', '-static', '--build-id=none',
                        '-z', 'noexecstack', '-T', str(HERE / 'mtk-acl-query-aarch64.ld'),
                        '-o', str(raw), str(obj)], check=True, env=environment)
        subprocess.run([commands['objcopy'], '--strip-all', str(raw), str(stripped)],
                       check=True, env=environment)
        data = validate_binary(stripped)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        output.chmod(0o755)
    return hashlib.sha256(data).hexdigest()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--binutils-prefix', default=os.environ.get('CROSS_COMPILE', 'aarch64-linux-gnu-'))
    args = parser.parse_args()
    digest = build(args.output.resolve(), args.binutils_prefix)
    print(f'{digest}  {args.output}')

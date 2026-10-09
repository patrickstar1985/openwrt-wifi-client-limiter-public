#!/usr/bin/env python3
"""Verify the real ELF, reproducibility and bounded AArch64 syscall behavior."""
# SPDX-License-Identifier: MIT
import argparse
import importlib.util
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('acl_builder', HERE / 'build-mtk-acl-query.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--binary', type=Path, default=builder.DEFAULT_OUTPUT)
parser.add_argument('--binutils-prefix', help='Also verify two clean builds against the shipped binary')
parser.add_argument('--qemu', help='Also run actual Linux argument and query-failure checks')
parser.add_argument('--require-emulation', action='store_true')
options, unittest_args = parser.parse_known_args()

try:
    import unicorn
    from unicorn import arm64_const as registers
except ImportError:
    unicorn = None
if options.require_emulation and unicorn is None:
    parser.error('Full instruction tests require the host Python package unicorn')


class Execution:
    """Run the actual unmodified ELF; mock only the Linux SVC boundary."""

    def __init__(self, arguments, *, fd=3, ioctl_result=0, length=1048,
                 policy=0, count=0, response=True, close_result=0, write_result=2):
        binary = builder.validate_binary(options.binary)
        header = struct.unpack_from('<16sHHIQQQIHHHHHH', binary)
        load = struct.unpack_from('<IIQQQQQQ', binary, header[5])
        self.calls, self.output, self.status = [], bytearray(), None
        machine = unicorn.Uc(unicorn.UC_ARCH_ARM64, unicorn.UC_MODE_ARM)
        self.machine = machine
        start = load[3] & ~4095
        size = (load[3] + load[6] - start + 4095) & ~4095
        machine.mem_map(start, size)
        machine.mem_write(load[3], binary[load[2]:load[2] + load[5]])
        machine.mem_protect(start, size, unicorn.UC_PROT_READ | unicorn.UC_PROT_EXEC)
        machine.mem_map(0x10000000, 0x20000, unicorn.UC_PROT_READ | unicorn.UC_PROT_WRITE)
        stack, cursor = 0x10010000, 0x10011000
        pointers = []
        for argument in ['mtk-acl-query-aarch64'] + list(arguments):
            data = argument.encode('utf-8') + b'\0'
            pointers.append(cursor)
            machine.mem_write(cursor, data)
            cursor += len(data)
        words = [len(pointers)] + pointers + [0, 0, 0, 0]
        machine.mem_write(stack, struct.pack('<' + 'Q' * len(words), *words))
        machine.reg_write(registers.UC_ARM64_REG_SP, stack)

        def read(index):
            return machine.reg_read(getattr(registers, f'UC_ARM64_REG_X{index}'))

        def result(value):
            machine.reg_write(registers.UC_ARM64_REG_X0, value & ((1 << 64) - 1))

        def syscall(_machine, interrupt, _context):
            if interrupt != 2:
                raise AssertionError(f'Unexpected interrupt {interrupt}')
            number = read(8)
            self.calls.append(number)
            if number == 198:
                assert (read(0), read(1), read(2)) == (2, 2, 0), 'Socket API must be fixed'
                result(fd)
            elif number == 29:
                assert fd >= 0 and read(0) == fd, 'Query must use acquired descriptor'
                assert read(1) == 0x8BE1, 'Only the audited query ioctl is permitted'
                request = read(2)
                raw = bytes(machine.mem_read(request, 32))
                name = raw[:16].split(b'\0', 1)[0]
                assert name == arguments[0].encode(), 'Query must select requested AP'
                assert raw[len(name):16] == b'\0' * (16 - len(name)), 'ifname tail must be initialized'
                assert raw[28:32] == b'\0' * 4, 'iwreq padding must be initialized'
                pointer, buffer_length, flags = struct.unpack_from('<QHH', raw, 16)
                assert buffer_length == 1048 and flags == 0x1942, 'Query OID and extent must be fixed'
                assert flags & 0x8000 == 0, 'Setter bit must never be used'
                before = bytes(machine.mem_read(pointer, 1048))
                assert before[:16] == b'\xff' * 16, 'Absent response must not look like policy0'
                assert before[16:] == b'\0' * 1032, 'Remaining response buffer must be initialized'
                if response:
                    machine.mem_write(pointer, struct.pack('<Q', policy))
                    if response != 'policy_only':
                        machine.mem_write(pointer + 8, struct.pack('<Q', count))
                    machine.mem_write(request + 24, struct.pack('<H', length))
                result(ioctl_result)
            elif number == 57:
                assert fd >= 0 and read(0) == fd, 'Close must use acquired descriptor'
                assert self.calls.count(57) == 1, 'Close must not be retried after EINTR'
                result(close_result)
            elif number == 64:
                assert self.calls[-2] == 57, 'Socket must be closed before output'
                assert (read(0), read(2)) == (1, 2), 'Output is exactly two bytes on stdout'
                data = bytes(machine.mem_read(read(1), 2))
                assert data in (b'0\n', b'1\n', b'2\n'), 'Output must contain only current policy'
                if write_result > 0:
                    self.output.extend(data[:write_result])
                result(write_result)
            elif number == 93:
                self.status = read(0)
                machine.emu_stop()
            else:
                raise AssertionError(f'Unexpected syscall {number}: no other APIs are allowed')

        machine.hook_add(unicorn.UC_HOOK_INTR, syscall)
        machine.emu_start(header[4], load[3] + load[5], timeout=1000000, count=20000)
        if self.status is None:
            raise AssertionError('Program failed to terminate within bounded instructions')


class BinaryTests(unittest.TestCase):
    def test_static_read_execute_elf_and_nonexecutable_stack(self):
        binary = builder.validate_binary(options.binary)
        self.assertLess(len(binary), 4096, 'This bounded helper should fit in one page')

    @unittest.skipUnless(options.binutils_prefix, 'Cross-binutils not requested')
    def test_two_clean_builds_match_shipped_binary(self):
        with tempfile.TemporaryDirectory(prefix='acl-reproduce-') as directory:
            first, second = (Path(directory) / name for name in ('first', 'second'))
            builder.build(first, options.binutils_prefix)
            builder.build(second, options.binutils_prefix)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first.read_bytes(), options.binary.read_bytes())

    @unittest.skipUnless(options.qemu, 'QEMU Linux checks not requested')
    def test_qemu_argument_validation_and_actual_missing_driver_query(self):
        for arguments in ([], ['ra0', 'extra'], ['ra16'], ['rax16'], ['ra00'], ['wlan0']):
            with self.subTest(arguments=arguments):
                done = subprocess.run([options.qemu, str(options.binary), *arguments],
                                      capture_output=True, timeout=5)
                self.assertEqual(done.returncode, 2)
                self.assertEqual(done.stdout, b'')
        done = subprocess.run([options.qemu, str(options.binary), 'ra0'],
                              capture_output=True, timeout=5)
        self.assertEqual(done.returncode, 4, 'Non-MTK host must reject the fixed driver query')
        self.assertEqual(done.stdout, b'')


@unittest.skipUnless(unicorn is not None, 'Install host Python package unicorn for instruction tests')
class InstructionTests(unittest.TestCase):
    def assert_failure(self, status, **scenario):
        executed = Execution(['ra0'], **scenario)
        self.assertEqual(executed.status, status)
        self.assertEqual(executed.output, b'')
        self.assertEqual(executed.calls, [198, 29, 57, 93])

    def test_every_permitted_interface_and_policy(self):
        for prefix in ('ra', 'rax'):
            for number in range(16):
                for policy in range(3):
                    with self.subTest(interface=prefix + str(number), policy=policy):
                        executed = Execution([prefix + str(number)], policy=policy, count=128)
                        self.assertEqual(executed.status, 0)
                        self.assertEqual(executed.output, f'{policy}\n'.encode())
                        self.assertEqual(executed.calls, [198, 29, 57, 64, 93])

    def test_invalid_arguments_never_open_a_socket(self):
        invalid = ['', 'r', 'ra', 'rax', 'rx0', 'RA0', 'ra00', 'ra01', 'rax01',
                   'ra16', 'rax16', 'ra99', 'ra-1', 'ra+1', 'ra1a', 'rax1a',
                   'ra1\n', 'wlan0', 'ra1' + '0' * 1024, 'ra0;BlAdd=ff:ff:ff:ff:ff:ff']
        for arguments in [[], ['ra0', 'extra']] + [[name] for name in invalid]:
            with self.subTest(arguments=arguments):
                executed = Execution(arguments)
                self.assertEqual((executed.status, executed.output, executed.calls), (2, b'', [93]))

    def test_descriptor_zero_is_valid_and_closed(self):
        executed = Execution(['rax15'], fd=0, policy=2)
        self.assertEqual((executed.status, executed.output), (0, b'2\n'))
        self.assertEqual(executed.calls, [198, 29, 57, 64, 93])

    def test_socket_errors_never_query_or_close(self):
        for error in (-4, -24, -1):
            with self.subTest(error=error):
                executed = Execution(['ra0'], fd=error)
                self.assertEqual((executed.status, executed.output, executed.calls),
                                 (3, b'', [198, 93]))

    def test_ioctl_errors_close_descriptor_without_output(self):
        for result in (-1, -4, -19, -22, 1):
            with self.subTest(result=result):
                self.assert_failure(4, ioctl_result=result)

    def test_wrong_response_length_fails(self):
        for length in (0, 1047, 1049, 65535):
            with self.subTest(length=length):
                self.assert_failure(5, length=length)

    def test_policy_is_checked_as_full_unsigned_64bit_value(self):
        for policy in (3, (1 << 32) + 1, (1 << 64) - 1):
            with self.subTest(policy=policy):
                self.assert_failure(5, policy=policy)

    def test_entry_count_is_checked_as_full_unsigned_64bit_value(self):
        for count in (129, 1 << 32, (1 << 64) - 1):
            with self.subTest(count=count):
                self.assert_failure(5, count=count)

    def test_absent_or_partial_response_cannot_look_valid(self):
        self.assert_failure(5, response=False)
        self.assert_failure(5, response='policy_only')

    def test_close_errors_fail_before_output(self):
        for result in (-4, -9, 1):
            with self.subTest(result=result):
                self.assert_failure(6, close_result=result)
        self.assert_failure(4, ioctl_result=-22, close_result=-9)

    def test_output_errors_and_short_writes_cannot_report_success(self):
        for result in (0, 1, -4, -32):
            with self.subTest(result=result):
                executed = Execution(['ra0'], write_result=result)
                self.assertEqual(executed.status, 7)
                self.assertEqual(executed.calls, [198, 29, 57, 64, 93])
                self.assertEqual(executed.output, b'0' if result == 1 else b'')


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *unittest_args], verbosity=2)

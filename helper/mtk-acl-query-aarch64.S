/* SPDX-License-Identifier: MIT
 * Linux AArch64, freestanding read-only query for the audited GL MTK profiles.
 * No libc, dynamic loader, general ioctl interface, or driver mutations.
 * Usage: mtk-acl-query-aarch64 ra0..ra15|rax0..rax15
 * Success: exactly one AccessPolicy digit (0, 1, or 2) followed by LF.
 * Failure: exit 2=argument, 3=socket, 4=query, 5=response, 6=close, 7=write.
 * Ignore stdout on nonzero exit (a short write can emit an incomplete result).
 * The caller verifies the exact driver profile.
 */

.equ SYS_IOCTL, 29
.equ SYS_CLOSE, 57
.equ SYS_WRITE, 64
.equ SYS_EXIT, 93
.equ SYS_SOCKET, 198
.equ ACL_QUERY_IOCTL, 0x8be1
.equ ACL_QUERY_OID, 0x1942
.equ ACL_BUFFER_SIZE, 0x418

.section .text
.global _start
.type _start, %function
_start:
    ldr x0, [sp]                    // argc, Linux process-entry stack
    cmp x0, #2
    b.ne bad_argument
    ldr x1, [sp, #16]                // argv[1]
    cbz x1, bad_argument
    ldrb w2, [x1]
    cmp w2, #'r'
    b.ne bad_argument
    ldrb w2, [x1, #1]
    cmp w2, #'a'
    b.ne bad_argument
    add x3, x1, #2
    ldrb w2, [x3]
    cmp w2, #'x'
    b.ne first_digit
    add x3, x3, #1
    ldrb w2, [x3]
first_digit:
    sub w4, w2, #'0'
    cmp w4, #9
    b.hi bad_argument
    ldrb w5, [x3, #1]
    cbz w5, valid_argument           // ra0..ra9 or rax0..rax9
    cmp w2, #'1'                     // only two-digit suffixes 10..15
    b.ne bad_argument
    sub w4, w5, #'0'
    cmp w4, #5
    b.hi bad_argument
    ldrb w5, [x3, #2]
    cbnz w5, bad_argument

valid_argument:
    sub sp, sp, #0x500               // iwreq32 + buffer1048 + output2
    stp xzr, xzr, [sp]
    stp xzr, xzr, [sp, #16]
    mov x2, sp
copy_interface:
    ldrb w3, [x1], #1
    strb w3, [x2], #1
    cbnz w3, copy_interface           // validated maximum five characters
    add x4, sp, #32
    str x4, [sp, #16]                // iwreq.u.data.pointer (LP64)
    mov w5, #ACL_BUFFER_SIZE
    strh w5, [sp, #24]               // iwreq.u.data.length
    mov w5, #ACL_QUERY_OID
    strh w5, [sp, #26]               // query, bit15 clear: never SET
    mov x5, #0
    mov x6, #ACL_BUFFER_SIZE
clear_buffer:
    strb wzr, [x4, x5]
    add x5, x5, #1
    cmp x5, x6
    b.lo clear_buffer
    mov x5, #-1
    stp x5, x5, [sp, #32]            // no-response success cannot look valid

    mov w0, #2                      // AF_INET
    mov w1, #2                      // SOCK_DGRAM
    mov w2, #0
    mov x8, #SYS_SOCKET
    svc #0
    tbnz x0, #63, socket_failure
    mov x19, x0                     // acquired descriptor
    mov x1, #ACL_QUERY_IOCTL
    mov x2, sp
    mov x8, #SYS_IOCTL
    svc #0
    cbnz x0, query_failure           // this audited query returns exactly zero

    ldrh w0, [sp, #24]
    cmp w0, #ACL_BUFFER_SIZE
    b.ne response_failure
    ldr x21, [sp, #32]               // uint64 AccessPolicy
    cmp x21, #2
    b.hi response_failure
    ldr x0, [sp, #40]                // uint64 ACL entry count
    cmp x0, #128
    b.hi response_failure
    mov w22, #0
    b close_descriptor
query_failure:
    mov w22, #4
    b close_descriptor
response_failure:
    mov w22, #5
close_descriptor:
    mov x0, x19
    mov x8, #SYS_CLOSE
    svc #0
    cbnz w22, exit_with_status       // retain earlier query/response failure
    cbnz x0, close_failure

    add x1, sp, #0x480
    add w2, w21, #'0'
    strb w2, [x1]
    mov w2, #10
    strb w2, [x1, #1]
    mov w0, #1                      // stdout
    mov w2, #2
    mov x8, #SYS_WRITE
    svc #0
    cmp x0, #2
    b.ne write_failure              // includes short write and EINTR
    b exit_with_status
close_failure:
    mov w22, #6
    b exit_with_status
write_failure:
    mov w22, #7
    b exit_with_status
socket_failure:
    mov w22, #3
    b exit_with_status
bad_argument:
    mov w22, #2
exit_with_status:
    mov w0, w22
    mov x8, #SYS_EXIT
    svc #0
    brk #0                          // exit must not return
.size _start, . - _start

.section .note.GNU-stack,"",%progbits

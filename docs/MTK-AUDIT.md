# GL-X3000 MTK profile audit

Version 0.5.10 supports the following exact Linux 5.4.211 AArch64 modules. These
hashes identify inspected binaries; matching a model or kernel version alone
does not establish compatibility.

| Profile | Module | SHA256 |
| --- | --- | --- |
| Earlier audited GL.iNet feed | `mt_wifi.ko` | `ed66a1502e819e4cc8424e50ce9c982905dabd1053bdfa7c47a7ec571652cb77` |
| Stock 4.8.3 release 3, build 902 | `mt_wifi.ko` | `0b23162074e8ce928d86ae8438297de173b71f7a1f90398f0ad7f63b88c5a7e3` |
| Earlier audited GL.iNet feed | `mtkhnat.ko` | `19751ae1fd69e351b26900f983ece10ae99f6f8a6dc07d22112d0c9dbbc6c65f` |
| Stock 4.8.3 release 3, build 902 | `mtkhnat.ko` | `d1b74a335d444ee735d2f0b332cdb9422035c0d0ea14cb3ef29d8229d4a4f756` |

The new modules were extracted from GL.iNet's [official firmware image](https://fw.gl-inet.com/firmware/x3000/release/x3000-4.8.3_release3-902-1106-1762433696.bin),
listed in its [GL-X3000 catalog](https://firmware-api.gl-inet.com/cloud-api/model/info?model=x3000)
and [download center](https://dl.gl-inet.com/router/x3000/). The downloaded image
matched the catalog entry's SHA256:
`877500360e81c65c8cb422ab7bcd9f3af14087f454da60434e93568c2e6f0e89`.

## Station control and runtime guard

Relocation-aware comparison verified the private `DisConnectSta`, `BlAdd` and
`BlDel` dispatch and relevant authentication/association gates. Add and delete
retain per-BSS ownership, duplicate handling and idempotent release. Adding an
absent station can mutate the list before reporting failure; allocation failures
also require fresh kernel diagnostics. The adapter therefore journals before
mutation and verifies the primary disconnect. Neither driver provides blacklist
expiry, so the managed service retains responsibility for deadlines and cleanup.

Both drivers contain a WPS exception with ordinary driver allowlist policy
`AccessPolicy=1`. The adapter rejects that policy, permits disabled filtering
(`0`) and ordinary denylists (`2`), and leaves ACL/WPS settings unchanged. The
limiter's protected-MAC list is separate.

The live getter uses ioctl `0x8be1`, query flags `0x1942` with bit 15 clear, and
a `0x418`-byte buffer. Policy and count are the first two 64-bit unsigned fields;
the helper requires the exact returned length, policy `0`–`2` and count at most
128. Query errors stop startup or the affected operation. Cached UCI/DAT values
are not substitutes for this runtime check.

The project-owned static helper is 808 bytes, with SHA256
`2d5aaf37acd655d1a3cdc1f9d6af368dc9e0f5ad19dffcfeb41f24861dec1d50`.
It has no libc, interpreter or ACL/WPS mutation operation. Fourteen helper tests passed,
including actual-instruction Unicorn tests with syscall mocks, two reproducible
builds and QEMU argument/query-error checks. Successful vendor queries still
require the actual driver. [Build and test instructions](../packaging/README.md#optional-mtk-query-helper).

## Acceleration and validation scope

The audited HNAT disable/remove paths invalidate flows, flush and disable PPE
caches, stop engines and free forwarding tables. Stock boot can unload HNAT when
acceleration is disabled. The adapter accepts absence only with an audited
installed binary, neither `mtkhnat` nor `mtk_warp` loaded, no live HNAT controls,
and all SFE disabled.
Restoring a saved enabled hook reloads only verified HNAT and restores its
selector; failures retain recovery data. A saved disabled hook keeps HNAT absent.

Binary matching and software tests do not measure physical radio or WAN behavior.
Validate disconnection/readmission, local services, external
IPv4/IPv6 blocking and protected-device access on the actual router, including
after reboot. Vendor binaries and user device identifiers are not distributed
in this repository.

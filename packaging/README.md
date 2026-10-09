# Native OpenWrt packages

The project includes an OpenWrt SDK recipe and standalone builders. The package
name is `luci-app-wifi-client-limiter`, currently version `0.5.10`, release `1`.
It contains the runtime shell scripts, LuCI JavaScript views, menu and ACL files,
safe default configuration, compiled translations and package lifecycle hooks.

| Router package manager | File | Architecture |
| --- | --- | --- |
| `opkg`, matched GL-X3000 stock MTK profile | `luci-app-wifi-client-limiter_0.5.10-1_all.ipk` | `all` |
| `opkg`, upstream OpenWrt 22.03 through 24.10 | `luci-app-wifi-client-limiter_0.5.10-1_all.ipk` | `all` |
| `apk`, upstream OpenWrt 25.12 | `luci-app-wifi-client-limiter-0.5.10-r1.apk` (signed) | `noarch` |

The file format follows the actual firmware's package manager. Vendor firmware
may differ from upstream version numbering. Package metadata remains `all` for
IPK and `noarch` for APK. The shell, LuCI and translation runtime is portable;
the package also contains one optional static Linux AArch64 executable,
`/usr/share/wifi-client-limiter/mtk-acl-query-aarch64`, used only for the two exact
GL-X3000 MTK driver profiles. The hostapd backend on other architectures never
executes this helper. These metadata values do not guarantee that a firmware
has the required LuCI and AP control APIs.

The current release files are in [`packages/0.5.10/`](../packages/0.5.10/):
the IPK, signed APKv3, public signing key and SHA-256 checksums. Version 0.5.10
is the first public release. Use the package matching the router's package
manager. The APK was built with the official OpenWrt 25.12.4 SDK and signed
with the project key. See the
[English installation guide](../docs/INSTALL.md) or
[deutsche Installationsanleitung](../docs/INSTALL.de.md) before using a package.

## Dependencies and lifecycle

The package declares `luci-base`, `rpcd`, `iw`, `iwinfo`, `wireless-tools`, `jshn`, `jsonfilter`, `ubus`, `uci`, `procd`,
`base-files`, `nftables-json` and `kmod-nft-netdev`, plus the normal OpenWrt `libc` dependency. The firmware's existing firewall3 or firewall4 is checked at installation and runtime; neither is force-installed. `jshn` supplies
`/usr/share/libubox/jshn.sh` and pulls the matching libubox dependencies through
the firmware's package manager. There is no hardcoded libubox ABI version.
No hostapd or wpad variant is force-installed, because these variants can
conflict with the router's existing wireless stack. The existing local APs use
hostapd `get_clients` and `del_client` through ubus or the supported MTK vendor
adapter. MTK station discovery resolves netifd APs with radio type `mtk` and uses
the vendor-compatible `iwinfo IFACE assoclist`. It also supplies the protected-device
picker. MTK association age uses the RAM-based first-observed fallback. Client limits can
apply to any selected WiFi AP network; a separate guest SSID is optional.
Permanent devices on managed networks should be protected by MAC address.

MTK control is limited to the stock GL-X3000 profile and the two exact `mt_wifi.ko`
SHA256 values documented in the [firmware compatibility guide](../docs/INSTALL.md#firmware-compatibility).
These profiles cover the driver shipped in stock 4.8.3 release 3, build 902
and the earlier matched feed driver.
The stock configurator must be present; unknown driver binaries and active
MultiAP/EasyMesh steering fail preflight. This adapter must exclusively own the
driver's private `BlAdd`/`BlDel` blacklist. Ordinary `AccessPolicy` MAC ACLs are
separate and unchanged. Runtime policies `0` (disabled) and `2` (denylist) are
supported; `1` (driver allowlist) is rejected because its WPS path can bypass
private reconnect bans. This does not restrict the limiter's protected-device
list. The helper reads only the current policy and cannot mutate ACL or WPS
settings. Failed or invalid runtime queries prevent startup or the affected
operation. This is not blanket support for OpenWrt 21.02 vendor
firmware. A rejected driver prevents the service from starting, leaving Internet,
count, duration and reconnect enforcement inactive. Browser refresh only reloads
the interface. Guest Internet blocking, connected-device discovery and direct
device protection have been tested on the GL-X3000. Hardware coverage is limited
to this tested configuration; validate the intended policy on the target router.

The settings form supports direct protection from a connected-device row and a
final wizard choice of test or live mode. A row action preserves other form
edits and requires **Save & Apply**; the wizard retains the current mode by
default and uses test mode for fresh setup. The package's LuCI menu loads a
view named with its content hash, so changed interface contents use a new URL.
The canonical source view remains available for development.

A managed mixture of MTK and third-party USB/mac80211 hostapd APs is unsupported;
the stock GL profile covers built-in MTK APs.

Internet control retains the firmware's existing firewall. Both firewall4 and
firewall3 use the plugin's own `netdev wifi_client_limiter` table with per-AP
ingress chains, including the matched GL-X3000 stock OpenWrt 21.02/Linux
5.4.211 profile. Firewall4's native inet table remains in place. The package
declares `kmod-nft-netdev` for this backend with either firewall. This does not
widen the supported firmware or driver profiles.

Kernel modules must come from the actual firmware's configured feeds and match
its full kernel ABI. An `all` package architecture does not make a foreign kmod
compatible. Do not change vendor feeds to upstream feeds, force dependencies or
replace firewall3 to install this package. The SDKs package the portable runtime
and the same prebuilt optional AArch64 query helper. Kernel dependencies are
installed from the target firmware's own feeds and are not shipped inside the
limiter package.

`/etc/config/wifi-client-limiter` is declared as a conffile. OpenWrt's standard
`default_postinst` and `default_prerm` wrappers manage service startup and
shutdown. Existing configuration is preserved on updates. Before removal, a
backup is written to `/etc/config/wifi-client-limiter.backup`; the removal hook
restores the configuration if the package manager removed an unchanged file.

Installation enables the service at boot, but the limiter starts disabled, in
test mode and without any selected WiFi networks. The package never changes
guest limits or resets settings already configured by the user. The first-run
setup assistant is part of the LuCI runtime payload. Language registration
uses an `uci-defaults` script for offline images and an installation hook for
live installs. It does not change the user's selected LuCI language.

The settings view includes a live connected-device table backed by read-only
RPC queries. It refreshes every ten seconds and after Save & Apply, with a
manual Refresh status button. It shows association, selected-network scope,
MAC protection and the current plugin Internet policy using committed settings.
The UI distinguishes inactive, unavailable or failed enforcement from a
confirmed restriction. Startup diagnostics are available even when the worker
cannot begin enforcement. These queries do not probe Internet reachability or
grant permissions through other firewall rules. A phone's mobile data must be
disabled for a router Internet-block test.

`guest_internet` defaults to `1`, also when absent from an existing configuration.
At `0`, with enabled enforcement and test mode off, unprotected guests are
restricted to the connected local address prefixes of logical networks attached
to the selected APs. Rules attach to the actual AP ingress ports, cover IPv4 and
IPv6, and also cover new arrivals before a client-list scan. Protected MACs bypass
this plugin's filter but do not bypass other firewall rules. DHCP, ARP, IPv6
link-local/neighbor discovery and local mDNS remain subject to the existing
firewall policy. Other routed LANs and remote routes are not added automatically.
Local proxy or VPN services may relay traffic beyond the LAN. This is a local
destination policy, not a content filter or MAC authentication.

Unprotected local-only guests use untagged WLAN traffic. Guest-originated VLAN
frames, including QinQ, are dropped in the netdev chain before IP filtering
with either firewall. Protected MACs bypass this plugin rule as well.

Enforcing local-only access saves the original native `flow_offloading` and
`flow_offloading_hw` option states in `/etc/wifi-client-limiter/offload-backup`,
forces both to `0`, and reloads the existing firewall backend to remove accelerated paths. This
persistent backup and any reload-retry marker are registered for sysupgrade.
Cleanup removes only the plugin's table and restores options still at its forced
values. Different administrator edits are preserved; a same-value administrator
edit cannot be distinguished. Pending firewall UCI changes prevent the plugin
from committing unrelated edits. Cleanup or reload failure preserves recovery
state and must not be reported as successful Internet enforcement.

Firewall3's `xt_FLOWOFFLOAD` has cached module-global paths that outlive removal
of its iptables jumps. The helper verifies no live IPv4/IPv6 `FLOWOFFLOAD` jumps,
records original module presence in `native-flowoffload-backup`, unloads the
module and verifies absence before reporting a block. Cleanup restores native
firewall intent and originally present module state. Inspection/module-control
tools are checked before policy changes; errors preserve recovery state.
The module backup is also registered for sysupgrade. This operation does not
flush conntrack or restart WLANs.

For the matched MTK vendor profile, the acceleration adapter also suspends HNAT
and SFE. It forces `mtkhnat.global.enable=0` and `actype=1`, disables the SFE
boot service, unloads remaining known SFE modules, disables the active HNAT hook
and clears both PPE caches. A fresh matched driver report and per-PPE bound-flow
counts must prove inactivity; empty or unrecognized diagnostics are not proof.
An unloaded HNAT state after stock reboot is accepted only for one of the two
audited installed HNAT binaries, with neither HNAT nor WARP loaded, both live
HNAT controls absent and every SFE engine disabled. Missing debug controls
alone are not sufficient. Cleanup reloads verified HNAT only to restore a saved
enabled hook, then restores its saved diagnostic selector. A saved disabled hook
keeps the module absent. Recovery failures retain the backup and persistent
retry marker.
Configured PPTP/L2TP and unknown acceleration controls fail enforcement rather
than producing a confirmed block. Suspension affects routing throughput across
the router, including protected devices and unselected networks.

Original vendor option absence/values, runtime controls, loaded module and
boot-service states are saved atomically in root-only
`/etc/wifi-client-limiter/vendor-acceleration-backup`. That backup and the
vendor retry marker are registered for sysupgrade. Cleanup restores owned
configuration/runtime state and originally present modules; distinct later
configuration intent is preserved. A manual change to the same forced value
cannot be distinguished. Pending `mtkhnat` changes are refused. Failed cleanup
keeps the recovery files and helpers for retry.

Installation registers the named `firewall.wifi_client_limiter` script include
pointing to `/usr/share/wifi-client-limiter/firewall-reload.sh`, with
`reload=1` for firewall3 and `fw4_compatible=1` for firewall4. It refreshes rules from current AP names after a firewall
reload; cached references to removed AP ports are not inserted into the main
batch. The callback does not recursively reload the firewall. Package removal
deletes only the exact owned include; upgrades retain it. Apply or discard
pending firewall edits before installation or removal. A section-name conflict
is preserved and causes registration to fail. Restoration also refuses an
ambiguous or unidentifiable saved defaults section rather than writing to an
unrelated section.

Service stop, package upgrade/removal and the file uninstaller invoke Internet
policy cleanup. Failed cleanup aborts the package lifecycle action rather than
deleting the remaining recovery helper. Existing limiter configuration is
retained. Enabling test mode or allowing guest Internet access also removes this
plugin's active restriction and restores its owned offload settings. Existing
hostapd reconnect bans remain independent and expire naturally.

The package includes `reconnect_window`, measured in whole seconds from `0` to `86400`. Fresh installations set it to `300`; an older configuration without the option uses `0`. `reconnect_cooldown` remains the separate minimum pause, `0`–`1800` whole seconds with default `0`. For example, cooldown `60` and window `300` request a one-minute minimum pause, followed by readmission only into free global guest slots during the remainder of a fixed five-minute window. Returning remembered MACs cannot cause another guest's count eviction. Accepting a visible returner into a free slot removes its entry and restores ordinary rules even before its window expires. Expired windows also restore ordinary rules. With `max_clients=0`, no guest-capacity restriction is added. A window shorter than the minimum pause does not cancel an accepted minimum-pause ban.

The worker records each evicted MAC in RAM under `/tmp/wifi-client-limiter`, with fixed `not_before` and expiry deadlines using the option values at eviction. Repeated attempts do not extend them. Normal service restarts retain this history; router reboots clear it. A hostapd/AP restart clears native bans without clearing the history, so the next active scan can request protection again. At the next successful enforcement scan, a protected MAC is removed from the history and `reconnect_window=0` clears the history. Accepted hostapd bans expire naturally, including after lifecycle cleanup. Newly protected MTK devices have their owned blacklist entries released. Changing the window alone to 0 does not shorten a recorded minimum pause. The list has a technical limit of 4096 remembered MACs. When full, it logs a warning and cannot store another eviction, while ordinary cooldown and limit enforcement continue.

The matched MTK driver implements per-AP association denial with no native TTL.
The adapter journals owned leases before writes in
`/tmp/wifi-client-limiter/station-bans`; the worker removes them at their recorded
deadlines and shortens its sleep to the next deadline. Disabling, test mode,
new protection, AP deselection and service stop/removal actively release owned
MTK entries. Failed release retains the journal and aborts lifecycle cleanup.
Do not delete the journal or share the private blacklist. A reboot clears both
RAM and native driver lists. Enforcing MTK `--once` is refused before mutations
because the procd worker must manage these deadlines; test-mode one-shots remain
available. Owned leases are reapplied after WiFi reload without extending their
deadlines.

With an active window, a new count-limit eviction immediately requests `max(cooldown, min(2 × interval, 60 seconds, window))` as its initial AP ban. This supplies a short capacity pause even when cooldown is 0, without altering the fixed `not_before` deadline based on the configured cooldown. A duration-limit eviction with free slots requests only that minimum pause.

AP ban requests cover selected local APs in the current scan and are best effort. During the minimum pause, requests use its remaining time. Afterward, at full guest capacity, absent remembered MACs can receive short bans with lifetime `min(2 × interval, 60 seconds, remaining window)`. At free capacity the worker stops refreshing them. Hostapd expires its bans; the MTK worker releases its owned entries at their recorded deadlines. This can add up to `20` seconds of waiting at the default interval of `10`, or up to `60` seconds at a longer interval, plus detection/query time. No guest slot is reserved. If a required visible-returner disconnect fails, ordinary count eviction stops for that scan and the error is logged.

The package still implements a soft polling limit. Brief associations, simultaneous arrivals, query delay and failed AP requests can produce temporary over-capacity. A successful ubus response confirms request acceptance rather than wireless success, and a MAC change can bypass returning-device recognition. Hardware and live LuCI/rpcd verification remain necessary.

## Standalone IPK build

Requirements: Linux, Python 3, Bash, GNU tar, gzip and standard shell utilities.
No cross compiler or root access is required. From the project root:

```sh
sh scripts/build-ipk.sh dist
python3 packaging/test-packages.py
```

This uses the unmodified official OpenWrt `ipkg-build` in `packaging/vendor/`.
Its upstream license and provenance are included there. Archive ownership is
normalized to root without changing ownership on the host. `SOURCE_DATE_EPOCH`
can set the package timestamps; the default is `1767225600` for reproducibility.
The output directory receives the `.ipk` and `SHA256SUMS`.

## Standalone APK v3 build

Use OpenWrt's native host `apk` tool from a 25.12 SDK, or a compatible apk-tools
3 host build. An older APKv2-only tool cannot build this package. Requirements
also include Python 3 and `fakeroot` for unprivileged builds, so package files
are owned by root. OpenSSL is required when deriving a public signing key.

```sh
sh scripts/build-apk-package.sh --apk-tool /path/to/apk dist
python3 packaging/test-packages.py --apk-tool /path/to/apk
```

The builder uses native `apk mkpkg`; it does not rename a tarball or IPK.
Without a signing key it writes `UNSIGNED-APK.txt` and verifies package integrity
with the explicit `--allow-untrusted` option. That build-time check does not
change the router's trust settings.

## Optional MTK query helper

The project-owned MIT helper is a freestanding Linux AArch64 executable with
no libc or dynamic-loader dependency and no vendor binary code. Its single
driver request is the audited read-only MAC-policy query. Accepted interface
names are `ra0` through `ra15` and `rax0` through `rax15`. A successful query
prints exactly `0`, `1` or `2`, followed by a newline. It provides no ACL or WPS
write operation. The MTK adapter verifies the supported driver identity before
invoking it; the hostapd backend does not use it.

Source and reproducible build files are
[`helper/mtk-acl-query-aarch64.S`](../helper/mtk-acl-query-aarch64.S),
[`helper/mtk-acl-query-aarch64.ld`](../helper/mtk-acl-query-aarch64.ld) and
[`helper/build-mtk-acl-query.py`](../helper/build-mtk-acl-query.py).
On a development host with Python 3 and GNU AArch64 binutils
(`aarch64-linux-gnu-as`, `aarch64-linux-gnu-ld` and
`aarch64-linux-gnu-objcopy`), rebuild the shipped helper with:

```sh
python3 helper/build-mtk-acl-query.py
```

Use `--binutils-prefix` for a different GNU cross-tool prefix. The builder checks
the ELF architecture, load segment and non-executable stack and prints the
output SHA256. Package builds copy this checked-in helper rather than compiling
it for the SDK host's architecture.

[`helper/test-mtk-acl-query.py`](../helper/test-mtk-acl-query.py) validates the
ELF and can execute its actual instructions in Unicorn 2.x with syscall mocks.
With GNU cross-binutils it also compares two clean builds with the shipped
binary. To require emulation on a host with the `unicorn` Python package:

```sh
python3 helper/test-mtk-acl-query.py --require-emulation --binutils-prefix aarch64-linux-gnu-
```

Passing `--qemu /path/to/qemu-aarch64` additionally checks invalid arguments and
failed real Linux queries. These tests verify the helper and its error handling;
they do not validate physical WiFi behavior on a GL-X3000.

## SDK build and CI

Download an official SDK for a supported release on a Linux build host, verify
its SHA256 checksum, and extract it outside the project checkout. The current
CI matrix uses the following pinned x86/64 SDKs. The resulting package metadata
is `all` or `noarch`; both package formats contain the portable scripts and
catalogs plus the same optional AArch64 query helper for the exact MTK profiles.
The SDK host architecture does not select or replace that helper. The package
contains no x86 executable.

| SDK release | Archive | SHA256 |
| --- | --- | --- |
| 24.10.6 | `openwrt-sdk-24.10.6-x86-64_gcc-13.3.0_musl.Linux-x86_64.tar.zst` | `9e398ea7efc098e4a986f97efff595e32d08c615fe356bcb3d885d7ad3a39ac0` |
| 25.12.4 | `openwrt-sdk-25.12.4-x86-64_gcc-14.3.0_musl.Linux-x86_64.tar.zst` | `28e004c1be4d215d19c1f12a6aa4c8d8f80689549eb707d0ff5a71f16fa8d05f` |

Official download indexes:

- <https://downloads.openwrt.org/releases/24.10.6/targets/x86/64/>
- <https://downloads.openwrt.org/releases/25.12.4/targets/x86/64/>

Pass the extracted SDK directory and output directory to the build script:

```sh
sh scripts/build-sdk-package.sh /path/to/extracted-sdk /path/to/output
```

The script copies this package into the SDK, uses its existing package metadata,
and disables the SDK's default selection of all packages and kernel modules.
It selects the limiter package,
runs `make defconfig`, and builds the selected package target with `NO_DEPS=1`.
This is appropriate here because the package copies scripts, translation
catalogs and an already built optional AArch64 helper; the SDK supplies the
packaging tools.
Runtime dependencies remain in the package metadata and are resolved on the
router; they are not rebuilt from source. It does not request a full firmware
build. The selected SDK decides whether the result is an IPK or APK. The SDK
directory must not already contain `package/luci-app-wifi-client-limiter` from
an earlier attempt. Remove that copied project directory or use a fresh SDK
before another build. `BUILD_JOBS` defaults to `2`.

This package does not update or install feeds during the build. The pinned SDKs
already contain the required dependency metadata. SDK artifacts and successful
package integrity checks do not substitute for router hardware testing.

CI verifies the checksums of the checked-in release downloads. The APK job
verifies the release APK with the published key and runs the native APK
installation, configuration-preservation and signing tests with the SDK host
`apk` tool. Signing the release download is separate from optional signing of
new CI build artifacts.

CI also runs `sudo python3 test-internet-nft.py --require` with nftables,
iproute2 and Linux namespace tools. It renders the production Internet policy
and exercises real IPv4/IPv6 packets inside isolated test networks, including
local access, protected/unselected devices and flow offload. Missing kernel
capabilities fail CI through `--require`; they can be skipped on a restricted
local development host. These Linux tests do not validate a router's WiFi driver
or vendor hardware acceleration.

## APK signing and public-key trust

Keep the publisher's private key outside the repository. Use the same key for
future releases so routers can continue to verify the publisher. If a new key
is generated, distribute and verify its public key as a separate trust change.
Only the public key belongs in the package download directory.

To create a publisher key on a development computer, for example:

```sh
openssl ecparam -name prime256v1 -genkey -noout -out /secure/path/wifi-client-limiter.pem
chmod 600 /secure/path/wifi-client-limiter.pem
openssl pkey -in /secure/path/wifi-client-limiter.pem -pubout -out /secure/path/wifi-client-limiter.pem.pub
```

Set these variables for either APK builder:

```sh
APK_SIGNING_KEY=/secure/path/wifi-client-limiter.pem \
APK_PUBLIC_KEY=/secure/path/wifi-client-limiter.pem.pub \
sh scripts/build-apk-package.sh --apk-tool /path/to/apk dist
```

Use the same variables with `scripts/build-sdk-package.sh` to sign SDK output.
The standalone builder signs during `mkpkg`; the SDK builder passes a temporary
APK configuration containing the signing-key path to its native `mkpkg`.
The SDK's default package-index signing is disabled so it does not introduce
an unrelated SDK publisher key. Both builders copy the public key as
`wifi-client-limiter.pem.pub` and verify the package against that key.

For CI, set the private PEM contents as the `APK_SIGNING_KEY` GitHub Actions
secret. The workflow materializes it only as a temporary file and derives the
public key. No private key is stored in Git, released artifacts or packages.
If the secret is absent, the APK artifact is explicitly marked unsigned.

Before installing a signed package, verify the public-key fingerprint through
a trusted channel, then copy that public key to `/etc/apk/keys/` on the router.
This grants trust to that publisher, not just one package. Verify the package
and install it with `apk add`. APKv3 matches key identities; its public-key
filename is not significant. For the signed release APK:

```sh
scp wifi-client-limiter.pem.pub luci-app-wifi-client-limiter-0.5.10-r1.apk root@192.168.1.1:/tmp/
ssh root@192.168.1.1
mkdir -p /etc/apk/keys
cp /tmp/wifi-client-limiter.pem.pub /etc/apk/keys/
apk add /tmp/luci-app-wifi-client-limiter-0.5.10-r1.apk
```

Use the actual router address. Download the APK and public key from
[`packages/0.5.10/`](../packages/0.5.10/) and verify their SHA-256 checksums before
copying them. The SHA-256 of the published PEM public-key file is
`6978695cc6605763c12d82a4c7443f816b1775149ad9828a71b32f12ebfc3c81`.

For a deliberately unsigned development package, use an explicit per-command
`apk --allow-untrusted add /tmp/package.apk` after reviewing its source and
checksum. Do not disable signature validation in global APK configuration.

## Official format and lifecycle references

- <https://github.com/openwrt/openwrt/blob/openwrt-24.10/scripts/ipkg-build>
- <https://github.com/openwrt/openwrt/blob/openwrt-25.12/include/package-pack.mk>
- <https://github.com/openwrt/openwrt/blob/openwrt-25.12/package/base-files/files/lib/functions.sh>
- <https://github.com/openwrt/openwrt/blob/openwrt-25.12/package/libs/libubox/Makefile>
- <https://github.com/alpinelinux/apk-tools/blob/master/doc/apk-mkpkg.8.scd>
- <https://github.com/alpinelinux/apk-tools/blob/master/doc/apk-keys.5.scd>
- <https://github.com/openwrt/firewall4/blob/master/root/sbin/fw4>
- <https://github.com/openwrt/firewall4/blob/master/root/usr/share/firewall4/main.uc>
- <https://github.com/openwrt/openwrt/blob/openwrt-25.12/package/kernel/linux/modules/netfilter.mk>
- <https://docs.kernel.org/networking/nf_flowtable.html>

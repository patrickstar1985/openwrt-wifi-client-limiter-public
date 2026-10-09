# Installation and configuration

[Deutsche Anleitung](INSTALL.de.md) · [Project overview](../README.md) · [Package build instructions](../packaging/README.md)

**Version 0.5.10.** Install the LuCI application, select the WiFi networks to manage and choose guest limits, protected devices and Internet access. See the [release notes](CHANGELOG.md) for the first public release.

## Choose the right package

| Firmware | Package manager | Package |
| --- | --- | --- |
| GL-X3000 stock MTK profile described below | `opkg` | IPK 0.5.10-1 |
| Upstream OpenWrt 22.03 through 24.10 with the required AP APIs | `opkg` | IPK 0.5.10-1 |
| Upstream OpenWrt 25.12 with the required AP APIs | `apk` | Signed APKv3 0.5.10-r1 |

Check the package manager actually used by your firmware. Vendor firmware can differ from upstream OpenWrt. IPK and APK formats are not interchangeable; the APK requires an APKv3-compatible package manager.

| Download | Purpose |
| --- | --- |
| [luci-app-wifi-client-limiter_0.5.10-1_all.ipk](../packages/0.5.10/luci-app-wifi-client-limiter_0.5.10-1_all.ipk) | Install on a compatible `opkg` router. |
| [luci-app-wifi-client-limiter-0.5.10-r1.apk](../packages/0.5.10/luci-app-wifi-client-limiter-0.5.10-r1.apk) | Install on a compatible `apk` router. |
| [wifi-client-limiter.pem.pub](../packages/0.5.10/wifi-client-limiter.pem.pub) | Publisher public key for verifying the APK signature. |
| [SHA256SUMS](../packages/0.5.10/SHA256SUMS) | SHA256 checksums for the release downloads. |

Download the required files from this project's release directory. A source-code ZIP is for building or file installation, not for uploading as a package in LuCI.

### Verify downloads

Compare the SHA256 of the package and, for APK installation, the public-key file with the entries in `SHA256SUMS` from the trusted project source. For example, on Linux:

```sh
sha256sum luci-app-wifi-client-limiter-0.5.10-r1.apk wifi-client-limiter.pem.pub
```

On Windows, use PowerShell `Get-FileHash -Algorithm SHA256 <filename>` for each downloaded file. For IPK installation, check the `.ipk` file instead. A checksum verifies that files match the release; the public key also needs to come from a source you trust.

## Requirements

- OpenWrt with modern JavaScript LuCI, rpcd, `procd`, UCI and `ubus`.
- The firmware's existing firewall3 or firewall4, nftables and kernel modules from its matching package feeds.
- `/lib/functions.sh` and `/usr/share/libubox/jshn.sh`.
- `iw`, `iwinfo`, `wireless-tools` and usual OpenWrt shell utilities from the firmware's matching feeds.
- Local AP control through hostapd ubus (`get_clients` and `del_client`) or the supported MTK vendor adapter, with vendor-compatible `iwinfo` association discovery.
- For APK installation or the file-install fallback, root SSH access and SCP or SFTP transfer.

Package dependencies must be available for your firmware. The limiter does not replace your wireless stack or require replacing firewall3 with firewall4. A vendor firmware based on OpenWrt is not automatically compatible.

Apply or discard pending firewall changes in LuCI before installation or removal. Installation registers the plugin's own firewall script include to refresh live AP rules after a firewall reload; removal deletes that include only when it is still owned by this plugin. A conflicting `firewall.wifi_client_limiter` section is preserved and installation reports the conflict.

## Firmware compatibility

| Existing firewall and kernel | Internet-rule backend | Required firmware packages |
| --- | --- | --- |
| Firewall4 with Linux 5.10 or later | nftables netdev ingress on the selected AP ports | `nftables-json`, `kmod-nft-netdev` and matching dependencies. Firewall4 remains installed. |
| Firewall3 with an older compatible kernel, including Linux 5.4 | nftables netdev ingress on the selected AP ports | `nftables-json`, `kmod-nft-netdev` and their dependencies from the firmware's matching feeds. Firewall3 remains installed. |

The package declares `nftables-json` and `kmod-nft-netdev` for both paths and uses the existing firewall backend. Its own table uses netdev ingress with either firewall; the native firewall4 inet table remains unchanged. Package metadata `all` or `noarch` does not make firmware dependencies interchangeable. The portable runtime includes one optional static AArch64 query helper for the two exact GL-X3000 MTK profiles below; the hostapd backend on other architectures never executes it.

The GL-X3000 stock firmware uses an OpenWrt 21.02 base and, for the supported stock profile, Linux 5.4.211. Its kernel modules must come from the configured GL.iNet feeds for that exact firmware and full kernel ABI. Do not install kernel modules from upstream OpenWrt 22.03/23.05/24.10, change feeds to obtain them or use `--force-depends`. A matching version number without a matching ABI is insufficient. Resolve a missing dependency with packages supplied for your firmware, or stop installation until a matching package is available.

Its MTK WiFi driver is identified by the wireless radio type `mtk`. The plugin resolves current AP names through netifd and reads associations with `iwinfo IFACE assoclist`, including devices offered by the protected-device picker. A missing `hostapd.*` object alone is therefore not an error on this path. Keep the vendor's WiFi components. Since the vendor path does not supply a usable actual association age, the limiter uses its RAM-based first-observed age; short reconnects between scans can go unnoticed.

MTK control requires the stock GL profile with `/usr/sbin/mtk-wifi-configurator` and one of these exact SHA256 values for `/lib/modules/$(uname -r)/mt_wifi.ko`:

| Driver source | Driver SHA256 |
| --- | --- |
| GL.iNet GL-X3000 feed driver, March 2023 | `ed66a1502e819e4cc8424e50ce9c982905dabd1053bdfa7c47a7ec571652cb77` |
| GL-X3000 stock firmware 4.8.3 release 3, build 902; `kmod-mt_wifi` version `5.4.211+TEST-16` | `0b23162074e8ce928d86ae8438297de173b71f7a1f90398f0ad7f63b88c5a7e3` |

The exact binary identity determines support; a matching router model, kernel version or package version alone does not.

The [MTK profile audit](MTK-AUDIT.md) records the source provenance, WiFi/HNAT fingerprints, runtime-query proof and remaining hardware checks.

The second driver was obtained from GL.iNet's [official GL-X3000 download center](https://dl.gl-inet.com/router/x3000/) and [4.8.3 release 3, build 902 image](https://fw.gl-inet.com/firmware/x3000/release/x3000-4.8.3_release3-902-1106-1762433696.bin). The image SHA256 is `877500360e81c65c8cb422ab7bcd9f3af14087f454da60434e93568c2e6f0e89`. These links identify the inspected driver source. Install the appropriate limiter package above and retain the router's firmware and matching dependencies.

To read the installed driver's hash without changing the router:

```sh
sha256sum /lib/modules/$(uname -r)/mt_wifi.ko
```

The limiter must be the exclusive user of the driver's private `BlAdd`/`BlDel` reconnect blacklist. Do not combine it with active MultiAP/EasyMesh steering or another tool's manual/private blacklist entries. Ordinary driver MAC ACLs are separate and the plugin does not change them. Their current runtime policy must be readable and meet these requirements:

| Driver `AccessPolicy` | Compatibility |
| --- | --- |
| `0`: ordinary MAC filtering disabled | Supported. |
| `1`: ordinary MAC allowlist | Rejected because the driver's WPS path can bypass private reconnect bans. |
| `2`: ordinary MAC denylist | Supported; the existing denylist is retained. |

This is the router driver's MAC ACL, not the limiter's **Protected MAC addresses** list. Protecting permanent devices in the limiter remains supported. The plugin's static AArch64 helper `/usr/share/wifi-client-limiter/mtk-acl-query-aarch64` reads only the current policy; it cannot change ACL or WPS settings. An unavailable, failed or invalid query prevents startup or the affected operation instead of trusting a cached configuration. No additional UI opt-in is needed for a matched stock profile. Unknown driver signatures, missing stock controls, unreadable kernel diagnostics or conflicting steering fail preflight; this is not general compatibility with every OpenWrt 21.02 or MTK vendor build.

A driver preflight failure prevents the service from starting, so Internet restrictions, client and duration limits, and reconnect protection remain inactive. The live interface reports the startup error; the system log includes `Unsupported MTK WiFi driver for reconnect protection` and the rejected hash. Ctrl+F5 loads the current interface and diagnostics but cannot fix a rejected driver. Do not remove the driver check or force support for an unknown binary.

Guest Internet blocking, device discovery and device protection have been tested on a GL-X3000. Validate client limits, reconnect behavior and Internet policy on the target firmware; these tests do not establish compatibility with other driver binaries.

Selecting a mixture of MTK APs and third-party USB/mac80211 hostapd APs is unsupported; this stock profile covers the built-in MTK APs.

### GL-X3000 installation path

1. Verify the supported driver profile and upload the IPK through LuCI using the instructions below. Retain stock WiFi, firewall3 and the firmware's package feeds. Resolve dependencies only through those feeds; an unsupported profile or missing package must be resolved before use.
2. Open **Services → WiFi Client Limiter** and follow the setup assistant. Select the desired APs, protect the operator and permanent devices, set the limits and uncheck **Allow guests to access the Internet** when guests should use only local services.
3. Choose **Test mode** to review proposed actions or **Live mode** to apply the selected policy at the wizard's final review. If you started in test mode, switch it off in the settings when ready. Wait for a scan and inspect the Internet status and connected-device list. Verify local services, blocked external IPv4/IPv6 and protected-device access using the checks below. Disable a test phone's mobile data, or enable airplane mode and turn WiFi back on, to test the router's connection. Use the managed service for MTK enforcement; it owns the driver's pause deadlines.

For a stock configuration that passes these checks, no manual driver blacklist, ACL or acceleration changes are required. Profile checks and recovery are handled by the adapters. An existing driver MAC allowlist must be reviewed in the router's own WiFi settings before using this MTK profile; the plugin does not replace it automatically. This procedure still requires validation on the actual router.

## Install an IPK through LuCI

This is the simplest route on an `opkg` router:

1. Download the `.ipk` package to your computer and [verify its checksum](#verify-downloads).
2. Sign in to the router's LuCI interface.
3. Open **System → Software** and choose **Upload Package…**. The wording can vary by LuCI version.
4. Select the `.ipk` file, upload it and complete installation.
5. Reload LuCI or sign in again, then open **Services → WiFi Client Limiter** from the menu. Check that the connected-device table includes **Protection**.

LuCI needs the Software page for this route. Resolve any dependency errors using packages from your firmware's own feeds. An SSH alternative is:

```sh
scp luci-app-wifi-client-limiter_0.5.10-1_all.ipk root@192.168.1.1:/tmp/
ssh root@192.168.1.1
opkg install /tmp/luci-app-wifi-client-limiter_0.5.10-1_all.ipk
```

Replace the example router address and package filename if your build differs.

## Install an APK on OpenWrt 25.12

Use the signed APKv3 package and its publisher public key on a compatible `apk` router. Download both files and [verify their checksums](#verify-downloads) before copying the key into the router's trusted-key directory. Installing this key trusts packages signed by this publisher, including future releases.

The SHA-256 of the downloaded PEM public-key file is:

```text
6978695cc6605763c12d82a4c7443f816b1775149ad9828a71b32f12ebfc3c81
```

From the folder containing the downloads on your computer:

```sh
scp wifi-client-limiter.pem.pub luci-app-wifi-client-limiter-0.5.10-r1.apk root@192.168.1.1:/tmp/
ssh root@192.168.1.1
```

Then on the router:

```sh
mkdir -p /etc/apk/keys
cp /tmp/wifi-client-limiter.pem.pub /etc/apk/keys/wifi-client-limiter.pem.pub
chmod 644 /etc/apk/keys/wifi-client-limiter.pem.pub
apk add /tmp/luci-app-wifi-client-limiter-0.5.10-r1.apk
```

Replace the example router address with your own. If your SCP client reports that SFTP is unavailable, use its legacy SCP mode, for example `scp -O`, or an appropriate transfer client. APK verifies the signature against the installed key. Resolve signature or dependency errors before continuing; use dependencies from the firmware's own feeds.

Reload LuCI or sign in again, then open **Services → WiFi Client Limiter**. Follow the setup assistant below. These instructions use SSH because package upload and key management differ between LuCI versions.

## Initial setup assistant

Open **Services → WiFi Client Limiter**, or **Dienste → WLAN Client Limiter** in German.

The assistant opens automatically when setup has not been completed and no managed networks have been selected. For an existing setup, choose **Start setup wizard → Protect important devices** to review device exceptions, or use **Protect device** directly in a connected-device row. The wizard carries over existing limits, protected MAC addresses and operating mode. Its final review lets you choose **Test mode** or **Live mode**; fresh setups default to test mode.

1. Select the WiFi AP networks on which client limits should apply. Networks on both 2.4 GHz and 5 GHz can share one limit. Existing home or office networks can be used; a separate guest SSID is optional. Protect permanent devices in the next step.
2. Select permanent connected devices to protect. The assistant shows device names when the router knows them; otherwise it shows MAC addresses and WiFi network labels. Examples include administrator phones, local servers and IoT devices that use a managed network. You can also enter protected MAC addresses manually, including devices that are currently disconnected.
3. Set the maximum guest count, optional connection duration and check interval. Leave **Allow guests to access the Internet** checked for normal access, or uncheck it for local-only guests. For example, set **Reconnect cooldown (seconds)** to 60 and **Reconnect protection window (seconds)** to 300. The first value adds a one-minute minimum pause. During the remainder of the five-minute protection window, evicted devices are readmitted only when global guest slots are free. After 300 seconds, normal admission rules apply again. Cooldown defaults to 0. The protection window defaults to 300 seconds.
4. Review the summary and choose **Operating mode**: **Test mode** logs proposed actions without applying limits or a new Internet restriction; **Live mode** applies the configured limits and policy. Choose **Save setup and apply** to enable the limiter in your selected mode. Reopening an existing setup defaults to its current mode.
5. If you chose test mode, review the logs and turn off **Test mode** in the regular settings when ready, then **Save & Apply**. In live mode, check the status and selected devices. For local-only access, follow the IPv4/IPv6 and protected-device checks below.

A fresh installation starts disabled, with test mode on, guest Internet access allowed, a guest limit of 25, duration limit off, minimum reconnect pause off, a 300-second reconnect protection window, a 10-second check interval and no managed networks or protected MACs. Completing setup enables the mode selected at the final review.

Opening, advancing or cancelling the assistant does not change UCI settings. Only the final save applies them. If LuCI has other pending changes, apply or revert those first; the assistant blocks saving until they are resolved.

The guest count excludes protected devices. Use the MAC address actually presented to the selected network, including a phone's private WiFi address where applicable. If a private address changes, update the protection rule.

## Connected devices and their Internet policy

The settings page includes a **Connected devices** table with **Device**, **MAC address**, **WiFi network**, **Internet policy** and **Protection** columns. It refreshes automatically every ten seconds and after **Save & Apply**. Choose **Refresh status** for an immediate update. Devices on unselected networks are shown too, so the list can explain why a connected phone is outside the limiter's scope.

Choose **Protect device** in a row to add that MAC to **Protected MAC addresses** in the current form. **Protection pending** means it has been added but not applied. Other pending form edits and the current mode are preserved. Choose **Save & Apply** to activate the exception with the rest of your changes. **Already protected** identifies a device already covered by the saved protected list. The live Internet-policy column continues to show the applied state until settings are applied; the button does not grant access immediately.

The table describes this plugin's policy rather than measuring a successful Internet connection. Protected devices and unselected networks keep their existing firewall permissions. A local-only guest is shown as restricted only when the plugin confirms active enforcement for its managed network. Inactive or unavailable enforcement must not be treated as a confirmed block. Unsaved settings do not change the live policy. A query warning means that the list may be incomplete, not that missing devices are disconnected.

Check the current private WiFi MAC on a phone before adding it to or removing it from the protected list. For an Internet-block test, the test phone must be unprotected and connected to a selected network. Keep a separate operator device protected.

## Verify the behavior

The regular settings remain available in LuCI. `0` disables the count or duration limit. `1800` seconds means 30 minutes. **Reconnect cooldown (seconds)**, UCI `reconnect_cooldown`, accepts 0 to 1800 whole seconds. **Reconnect protection window (seconds)**, UCI `reconnect_window`, accepts 0 to 86400 whole seconds. A value of 0 disables the corresponding pause or window. Cooldown 0 can still require free guest slots when the protection window is enabled. The check interval accepts whole seconds from 2 to 3600. With no WiFi networks selected, the limiter takes no action.

Read the log through SSH, or use LuCI's system log:

```sh
logread -e wifi-client-limiter
```

Test-mode decisions start with `TEST:`. At or below the guest limit, with the duration limit disabled or not reached, there may be no limiter log entries.

The count is global across selected local APs. A MAC appearing on multiple selected APs is counted once. Protected MACs are neither counted nor disconnected. At a limit of 25, exactly 25 causes no count eviction. A new 26th guest causes the oldest eligible guest to be selected at the next scan. Remembered returning devices are checked first and can be removed without displacing another guest. Multiple excess guests can be removed in one scan.

Settings are read again each scan. The network selection stores UCI `wifi-iface` section identifiers and resolves their current interface names through `network.wireless status`. If you delete and recreate a wireless section, select the resulting network again in the limiter.

## Guest Internet access

**Allow guests to access the Internet**, UCI `guest_internet`, is a checkbox in both settings and the assistant. Its default is on (`1`). Unchecking it (`0`) restricts unprotected guests to local services only when the limiter is enabled and test mode is off. A count or duration limit need not be enabled to use this option. Test mode does not apply a new Internet restriction; entering it from an active restriction removes the plugin's rules and restores its owned offload settings.

Local-only access keeps the device connected to WiFi so it can use local services. A successful WiFi association alone therefore does not show that the Internet restriction failed. Count and duration limits control disconnection separately.

Rules use the current physical ingress ports of the selected APs. An unselected SSID sharing the same bridge is unaffected. Once installed, the rules cover new guests immediately, without waiting for a client-list scan, and cover both IPv4 and IPv6. Protected MACs bypass this plugin's rules. They remain subject to the router's existing firewall policy, as do ordinary guests when the checkbox is on. This option does not grant access that another firewall rule denies. Use the MAC actually presented to this WiFi network; a MAC exception is not authentication.

On both firewall paths, unprotected local-only guests use ordinary untagged WLAN traffic. Guest-originated VLAN-tagged Ethernet frames, including QinQ, are dropped before IP filtering to prevent a tagged-frame bypass. Protected MACs bypass this additional plugin rule too.

Allowed local destinations come from the connected address prefixes of the logical networks attached to the selected APs. DHCP, ARP, IPv6 link-local addresses, neighbor discovery and local mDNS remain available under the existing firewall policy. Other routed LANs, remote routes and the Internet are not automatically allowed. Check where your local gallery or service is hosted before enabling local-only access. For example, a gallery or file server on the selected LAN remains reachable. A proxy or VPN hosted inside an allowed LAN can relay traffic outside it, so this is not a content filter or a complete isolation policy.

An empty optional IPv6 `local-address` object reported by netifd is skipped during prefix collection. Valid IPv4 can still be used. Invalid partial address data remains an error, and a delegated `/60` is never allowed wholesale as local access. At least one usable local prefix is required to apply the policy.

Use a normal routed OpenWrt gateway with its existing firewall3 or firewall4. The matched GL-X3000 MTK acceleration controls are supported by the adapter described below. Unrecognized vendor acceleration controls cannot be confirmed safe and produce an error. A bridged upstream topology can have different packet paths and requires separate verification.

### Offload and saved settings

Software and hardware flow offload can bypass firewall rules after a connection has been accelerated. While enforcing local-only access, the plugin sets the native firewall defaults `flow_offloading` and `flow_offloading_hw` to `0` and reloads the existing firewall3 or firewall4 backend. This can reduce routing throughput. It changes neither WLAN settings nor other firewall policies.

On firewall3, removing `FLOWOFFLOAD` rules does not by itself clear the module's cached paths. The plugin checks both live IPv4 and IPv6 filter rules, saves the original presence of `xt_FLOWOFFLOAD`, then unloads that module and verifies it is absent. Cleanup restores its saved presence when needed. Required inspection and module-control tools are checked before enforcement; a missing tool or failed unload is an error, not a confirmed block. It does not flush conntrack or restart WLANs.

On the matched GL-X3000 MTK profile, the adapter also sets `mtkhnat.global.enable=0` and `actype=1`, disables the SFE boot service, unloads remaining known SFE modules, disables the live HNAT hook and clears both hardware PPE caches. It checks the actual hook and per-PPE bound-flow counts before reporting enforcement. Stock firmware may unload HNAT after a reboot with acceleration disabled. This state is accepted only with an audited installed HNAT binary, no loaded HNAT or WARP module, no live HNAT controls, and all SFE acceleration disabled. Missing controls alone do not confirm a block. These controls apply to acceleration across the router, so protected devices and unselected networks can also have lower routing throughput.

When cleanup must restore a previously enabled HNAT hook after the module was unloaded, it reloads only the verified HNAT binary and restores the saved hook and diagnostic selector. A saved disabled hook retains the absent-module state. Recovery failures keep the backup and persistent retry marker so cleanup can be retried.

Configured PPTP or L2TP is unsupported on this vendor path because the stock hotplug behavior can re-enable HNAT independently. Unknown controls, conflicting acceleration modules or unverifiable state produce an error instead of a confirmed block. Disabling native flow-offload options alone is insufficient for this vendor firmware.

Original option values, including whether an option was absent, are stored in the root-only file `/etc/wifi-client-limiter/offload-backup`. Firewall3 module state is saved separately in `/etc/wifi-client-limiter/native-flowoffload-backup`. MTK configuration, hook, cache-selector, SFE module and boot-service state are saved in `/etc/wifi-client-limiter/vendor-acceleration-backup`. These backups and needed retry markers survive reboot. Do not delete recovery files while enforcement or recovery is pending.

Cleanup on allowing guest Internet access, disabling the limiter, entering test mode, stopping the service or removing the package removes this plugin's nftables table and restores saved options that still equal its forced value. Different administrator values are preserved. A deliberate administrator change to the same `0` value cannot be distinguished from the plugin's value. Resolve pending firewall UCI changes in LuCI before activating or restoring the policy; the plugin refuses to commit unrelated pending firewall edits. If a saved defaults section cannot be identified safely after administrator changes, restoration stops and keeps its recovery data. A failed cleanup retains recovery state and is reported as an error.

The vendor adapter also restores its saved option absence, runtime controls, originally loaded SFE modules and service enablement. Distinct later configuration changes are remembered before acceleration is suspended again; unchanged forced values retain the saved intent. Resolve pending `mtkhnat` changes too. A same-value manual change remains indistinguishable, and a failed restore retains the vendor backup for retry.

After a reboot or WiFi change, the active worker resolves the current AP ports and rebuilds the policy. During boot or reconfiguration, wait for the live status and verify the client behavior before relying on the restriction.

### Verify local-only access

1. Protect the operator's device and every selected-network device that needs ordinary Internet access. Confirm their private WiFi MACs where applicable.
2. Uncheck **Allow guests to access the Internet** in the assistant or settings and review the selected networks and protected devices. The wizard's final review offers test or live mode.
3. Apply **Live mode** in the wizard, or turn test mode off and choose **Save & Apply** in the settings. Wait for a worker scan and inspect **Internet policy status** and the test device's row. Both must show the applied restriction. If enforcement is inactive, failed or unavailable, resolve the displayed diagnostic before relying on a block.
4. On a test phone, disable mobile data. Alternatively, enable airplane mode, turn WiFi back on and join the selected network. Then, from an unprotected device on each selected AP, verify DHCP, router access and the local gallery or service. Test an external IPv4 destination and an external IPv6 destination where IPv6 is available; both should be blocked. Use a fresh external request rather than a cached page.
5. From a protected device, verify ordinary Internet access under the existing firewall policy. Verify an unselected SSID is unaffected.
6. Re-enable the checkbox and apply. Verify normal guest Internet access and restored offload settings. Repeat the selected-AP checks after WiFi reconfiguration or firmware changes.

For diagnosis:

```sh
logread -e wifi-client-limiter
nft list tables
uci changes firewall
```

The plugin's table is `netdev wifi_client_limiter` with either firewall. Inspect its rules with `nft list table netdev wifi_client_limiter`. It exists only while a restriction has been applied. Its presence alone does not confirm offload recovery or correct service reachability. Consult the live status and logs. Changing settings is read at the next scan, with additional time for network queries and rule application.

## Reconnect protection and limits

A limit-based eviction records the MAC and fixed deadlines for its minimum pause and protection window. Retries never extend these deadlines. While the device is remembered, it may rejoin only after its pause has ended and a global guest slot is free. Protected devices do not occupy guest slots. Returning devices cannot trigger the eviction of another guest. Accepting a visible returner into a free slot removes its entry, so ordinary guest rules resume even before the window ends. Window expiry also restores ordinary rules. With the guest count limit disabled (`max_clients=0`), the window imposes no guest-capacity restriction.

Example with `max_clients=25`, `reconnect_cooldown=60` and `reconnect_window=300`:

| Time after eviction | Returning device |
| --- | --- |
| First 60 seconds | The minimum pause blocks automatic and manual attempts. |
| After 60 seconds, before 300 seconds | Can use a free slot. At 25 occupied guest slots, it remains subject to protection. |
| From 300 seconds onward | Follows ordinary guest rules again. |

AP requests are best effort, and enforcement is based on scans. A remembered device already visible at a full guest count is handled before ordinary count eviction. If its required disconnect fails, the worker logs that failure and stops further ordinary count eviction in that scan. It does not let a returning device trigger a different guest's eviction.

With an active protection window, a new count-limit eviction immediately requests a ban of `max(cooldown, min(2 × check interval, 60 seconds, window))`. Cooldown 0 can therefore still create a short capacity pause on a full guest network. The stored minimum-pause deadline continues to use the configured cooldown. A duration-limit eviction with free slots requests only that minimum pause.

While all guest slots are full after the minimum pause, the worker renews short bans for absent remembered MACs on selected APs included in the scan. The lifetime is `min(2 × check interval, 60 seconds, remaining protection window)`. With a free slot, it stops renewing. Hostapd expires its bans naturally; the MTK adapter removes its owned entries at their recorded deadlines. A short ban can therefore delay readmission by up to 20 seconds at the default 10-second interval, or up to 60 seconds at a longer interval, plus detection/query time. This does not reserve a slot; another arrival may occupy it first.

Minimum-pause protection uses the original deadline, so a retry requests only its remaining time. If the protection window is shorter than the minimum pause, window expiry does not cancel a native pause ban. Requests can fail on an individual AP, for example allowing a brief return on another WiFi band. Failures are logged. A successful ubus reply confirms request acceptance; verify actual disconnection and readmission on your hardware.

### Stored MACs and existing AP bans

- The remembered list is in RAM under `/tmp/wifi-client-limiter`. Normal service restarts retain it; router reboots clear it.
- Restarting hostapd or an AP clears its native bans, while the remembered list remains. With the limiter active, the next scan can request protection again.
- At the next successful enforcement scan, a newly protected MAC is removed from the list and setting `reconnect_window` to 0 clears it. Hostapd bans expire naturally. Newly protected MTK devices have their owned entries released; setting the window alone to 0 does not shorten a recorded minimum pause.
- Changing positive settings affects future evictions. Entries already remembered retain their original pause and window deadlines.
- The RAM list holds at most 4096 MACs. A full list prevents remembering another eviction and produces a warning. Ordinary cooldown and limit enforcement continue.
- On hostapd APs, disabling the limiter, enabling test mode or stopping the service does not actively clear accepted native bans. They expire naturally. Test mode sends no new disconnects or bans.

### MTK native pause and cleanup

For the matched stock MTK profile, the adapter uses the per-AP private blacklist to deny association, including across selected bands after a verified associated-client disconnect. It does not replace the ordinary MAC ACL or add a second pause setting. The same cooldown and readmission options apply.

The driver has no blacklist TTL, so the plugin journals its own leases in `/tmp/wifi-client-limiter/station-bans` before writing them. The active worker releases expired entries, shortening its sleep to the next deadline; query time can add a small delay. Disabling the limiter, entering test mode, newly protecting a MAC, deselecting an AP or stopping/removing the service actively releases owned MTK entries. A failed release keeps the journal for retry and can abort installation or removal. Do not delete this journal manually or share the private blacklist with another tool. A router reboot clears the RAM journal and native driver lists.

Use the procd-managed service for enforcement. An enforcing `wifi-client-limiter --once` run on MTK is rejected before mutations because it would leave driver entries without a deadline manager. A test-mode one-shot is allowed; the read-only client RPC below does not enforce limits.

### Polling and device recognition

The limiter remains a soft limit. Brief associations and over-capacity periods can occur between scans, during simultaneous attempts or when requests fail. It cannot distinguish manual from automatic attempts. Changing a MAC address can avoid recognition as a returning device and also invalidate a protection rule.

The AP/driver hard limit, such as `maxassoc`, needs room above the guest limit for protected devices and arrivals. Guests rejected before association are invisible to the limiter. The installer and worker do not change AP hard limits, and only local selected APs are managed.

Connection age comes from `iw station dump` where supported. Otherwise the worker tracks first observation in RAM. Short disconnects between scans can go unnoticed in that fallback. Equal ages are resolved by MAC order. A successful disconnect resets the tracked connection-duration session. The duration limit measures WiFi connection age, including idle time; enforcement is delayed by the scan interval plus query time. A failed client query skips the scan rather than using a partial client list.

## Languages

Choose the language in LuCI's global **Language and Style** settings, usually under **System → System**. The plugin follows that setting.

| Language | PO source directory | LuCI runtime code |
| --- | --- | --- |
| English | `en` | `en` |
| German | `de` | `de` |
| Simplified Chinese | `zh_Hans` | `zh-cn` |
| Russian | `ru` | `ru` |
| French | `fr` | `fr` |
| Spanish | `es` | `es` |
| Polish | `pl` | `pl` |
| Italian | `it` | `it` |
| Brazilian Portuguese | `pt_BR` | `pt-br` |
| Turkish | `tr` | `tr` |

English is the source language and fallback. Nine precompiled `.lmo` catalogs are included, so the router needs no Python or translation compiler. General LuCI buttons come from LuCI itself and require its matching base language package. Installer output and worker logs are not localized.

## Removal

If you previously used the file installer, run `sh uninstall.sh` from that source folder before installing the native package. It preserves the limiter configuration. Then install the package and retain the existing settings if prompted. This avoids leaving the same installation under two different maintenance methods.

Use LuCI's Software page to remove an installed IPK, or run the matching package-manager command on the router:

```sh
opkg remove luci-app-wifi-client-limiter
```

On an APK router:

```sh
apk del luci-app-wifi-client-limiter
```

Use the package manager corresponding to the installed package. Do not use `uninstall.sh` to remove a package-manager installation. Export or copy `/etc/config/wifi-client-limiter` first if you want to retain it regardless of your package manager's removal behavior.

A firmware upgrade can require reinstalling the package and dependencies. Keep a copy of the package and your settings; an upgrade that discards settings also discards the limiter configuration. When retaining settings, preserve the registered offload recovery files together with the firewall configuration and reinstall the limiter before relying on Internet control. The backup must remain available until its saved settings have been restored successfully.

## File-install fallback

For development or a compatible router without a suitable package, use the file installer. Download the source ZIP, extract it and rename the project root to `wifi-client-limiter`. It must contain `install.sh` and the complete `files/` directory.

Install the prerequisites first from the firmware's own feeds, including `nftables-json` and `kmod-nft-netdev`. The file installer does not resolve missing kernel modules or change feeds. Retain the existing firewall3 or firewall4 installation.

From your computer:

```sh
scp -r wifi-client-limiter root@192.168.1.1:/tmp/
ssh root@192.168.1.1
```

On the router:

```sh
cd /tmp/wifi-client-limiter
sh install.sh
```

The installer checks essential commands, copies the files, enables and starts the service, restarts rpcd and clears LuCI caches. It does not install dependencies. Reload LuCI afterward. An existing limiter configuration is retained and copied to `/etc/config/wifi-client-limiter.backup`.

To remove a file installation, run `sh uninstall.sh` from the project folder. It retains the limiter configuration and its backup. Do not mix a file installation with a package-managed installation.

The file installer registers the limiter configuration in `/etc/sysupgrade.conf`. This does not preserve manually installed program or LuCI files. Reinstall them after a firmware upgrade that retains settings. Keep a project copy on your computer because the router's `/tmp` is cleared on reboot.

If SCP uses SFTP and the router lacks an SFTP subsystem, use an appropriate transfer client or your client's legacy SCP option, such as `scp -O -r` for a folder.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Package format is rejected | Check the firmware's actual package manager and download the matching format. |
| Package installation reports missing dependencies | Use your firmware's own feeds and check compatibility. |
| Installation or removal stops with an error | Keep the complete output and any saved firewall or acceleration state. Resolve the reported package, dependency or cleanup error before retrying. Do not force removal or delete active state journals. |
| Log reports `Unsupported MTK WiFi driver for reconnect protection` | Compare the rejected hash with the supported profiles above. An unknown driver prevents service startup and leaves all enforcement inactive; do not bypass the check. |
| MTK startup or an operation rejects the driver's MAC policy | Ordinary driver allowlist policy `1` is unsupported because of its WPS bypass. Use a selected network with ordinary filtering disabled (`0`) or a denylist (`2`). Review this in the router's own WiFi settings. The limiter's protected-device list is separate and remains supported. A failed runtime query must be resolved using the displayed error and log before enforcement can be confirmed. |
| A phone stays connected to WiFi with guest Internet disabled | This is expected: local-only access preserves WiFi and local services. Check external IPv4/IPv6 reachability and the active Internet policy. Count and duration limits control disconnection separately. |
| Internet policy is inactive, failed or unavailable despite requesting local-only access | Read the displayed diagnostic and system log. Check that settings were applied, the limiter is enabled, test mode is off and the service is running. Check firmware compatibility, supported acceleration state and pending firewall or `mtkhnat` changes. An inactive policy does not block guests. |
| Local-only guests cannot reach the gallery or another LAN | The service must be inside a connected prefix of a selected AP's logical network and allowed by the existing firewall. Other routed LANs are not automatically included. |
| A guest can still reach external services | On a phone, disable mobile data, or enable airplane mode and turn WiFi back on. Check its row, selected AP, current private MAC, protection and live policy status. Test both IP families. Local proxy/VPN services and vendor acceleration require separate review. |
| Connected-device list is unavailable or incomplete | Check the list's diagnostic, WiFi backend and supported client query. Unavailable data does not mean an empty network or confirmed Internet blocking. |
| A row says Protection pending, but Internet policy is unchanged | The MAC was added to the form only. Choose **Save & Apply** to apply it with your other pending changes, then refresh the status. |
| The Protection column or final wizard mode choice is missing | Reload LuCI and open the limiter from its menu; sign out and back in if needed. Check that installation completed and compare a private browser window. The packaged view uses a URL derived from its contents. A browser refresh cannot correct a runtime policy error. |
| Acceleration remains disabled after cleanup | Check logs and the persistent recovery files, resolve pending firewall or `mtkhnat` edits and retry service cleanup. Keep the backups until recovery succeeds. |
| Menu does not appear | Reload or sign in to LuCI again and check installation completed. |
| No APs are offered | Check that the desired wireless sections use AP mode. STA/client sections are not managed. |
| No device is disconnected | Check activation, test mode, selected networks, protected MACs and whether a limit is actually exceeded. |
| A hostapd object cannot be read | On the hostapd path, run `ubus list 'hostapd.*'` and check AP status and ubus support. A missing hostapd object is expected for the MTK vendor path. |
| MTK connected devices are unavailable | Check the radio's `mtk` type and the vendor-compatible `iwinfo IFACE assoclist`. Retain the vendor WiFi stack and use its matching packages. |
| Oldest-device order is approximate | The driver may lack age information, causing the RAM-based fallback to be used. |
| A phone reconnects immediately | Check both reconnect options. With cooldown 0 and protection window 0 this is allowed. Otherwise check expiry times, MAC changes and logged AP request failures; brief associations can occur between scans. |
| A returning device waits despite a free slot | A minimum pause or a previous short capacity ban may still be running. Hostapd expires it; the MTK worker releases its owned entry at the deadline. Waiting can add up to 20 seconds at the default interval or up to 60 at a longer interval, plus query time. |
| A hostapd device remains blocked after disabling or protecting it | Accepted hostapd bans expire naturally. Changing the window or cooldown alone does not cancel a recorded minimum pause. |
| An MTK device remains blocked after disabling or protecting it | Owned MTK entries should be released. Check cleanup errors and keep the lease journal for retry. Changing the window alone to 0 does not shorten a recorded minimum pause. |
| Plugin text is translated but general LuCI buttons are English | Install LuCI's matching base language package. |

For either WiFi backend, the plugin exposes a read-only client query:

```sh
ubus call wifi-client-limiter clients
ubus call wifi-client-limiter status
logread -e wifi-client-limiter
```

On the hostapd path, query an object that actually exists on your router:

```sh
ubus list 'hostapd.*'
ubus call hostapd.phy0-ap0 get_clients
```

`hostapd.phy0-ap0` is an example. Replace it with an object from the first command.

For an MTK AP, use its current kernel interface name:

```sh
ubus call network.wireless status
iwinfo ra0 assoclist
```

`ra0` is an example. Use the AP interface reported by netifd; another band or SSID can have a different name.

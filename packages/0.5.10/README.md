# WiFi Client Limiter 0.5.10

First public release for compatible OpenWrt routers. Manage WiFi guest limits,
protect permanent devices, configure reconnect pauses and optionally restrict
guests to local services. The LuCI setup assistant offers test and live modes.

## Downloads

| File | Purpose |
| --- | --- |
| [luci-app-wifi-client-limiter_0.5.10-1_all.ipk](luci-app-wifi-client-limiter_0.5.10-1_all.ipk) | Installation on compatible routers using `opkg`, including the supported GL-X3000 profile and upstream OpenWrt 22.03 through 24.10. |
| [luci-app-wifi-client-limiter-0.5.10-r1.apk](luci-app-wifi-client-limiter-0.5.10-r1.apk) | Signed APKv3 for compatible routers using `apk`, including OpenWrt 25.12. |
| [wifi-client-limiter.pem.pub](wifi-client-limiter.pem.pub) | Public key for verifying the APK signature. |
| [SHA256SUMS](SHA256SUMS) | SHA-256 checksums of both packages and the public key. |

Select the format used by your router. Vendor firmware may use a different
package manager than its OpenWrt version suggests. Dependencies, kernel modules
and AP control interfaces must match the installed firmware. See the
[compatibility requirements](../../docs/INSTALL.md#firmware-compatibility).

## Installation

- **IPK:** In LuCI, open **System → Software → Upload Package…**, select the IPK
  and install it. Resolve dependencies through the firmware's own feeds.
- **APK:** Download the APK and public key. Verify the checksums, install the
  public key in `/etc/apk/keys/`, then install the package with `apk add`.
  Follow the [English guide](../../docs/INSTALL.md) or
  [deutsche Anleitung](../../docs/INSTALL.de.md) for the exact commands.

SHA-256 of the downloaded public PEM key file:

```text
6978695cc6605763c12d82a4c7443f816b1775149ad9828a71b32f12ebfc3c81
```

When all three files and `SHA256SUMS` are in the same directory, verify them with
`sha256sum --check SHA256SUMS` before installation. Use the checksum and key from
the trusted project source.

## Initial setup

1. Reload LuCI and open **Services → WiFi Client Limiter**.
2. Select the WiFi networks to manage, protect permanent devices and configure
   guest limits and Internet access.
3. Choose **Test mode** or **Live mode** on the assistant's final screen and
   save the setup. Fresh installations default to test mode.

To protect a device later, choose **Protect device** in its row, then
**Save & Apply**. Check the live Internet policy status after applying settings.
Disable mobile data on a test phone when checking the router's restrictions.

Documentation: [English](../../docs/INSTALL.md) ·
[Deutsch](../../docs/INSTALL.de.md) · [Release notes](../../docs/CHANGELOG.md).

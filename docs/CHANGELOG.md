# Release notes

## 0.5.10. First public release

### Guest management

- Shared guest limit across selected local WiFi APs, including both bands.
- Optional connection-duration limit and oldest-guest replacement when the count is exceeded.
- Protected MAC addresses excluded from counts, disconnects and the plugin's Internet restrictions.
- Configurable reconnect cooldown and protection window for returning devices.
- Optional local-only access for guests, with IPv4 and IPv6 rules.

### LuCI interface

- Setup assistant for networks, protected devices, limits and test/live operation.
- Connected-device list with network membership, Internet-policy status and a **Protect device** action.
- Direct protection preserves pending form edits and applies with **Save & Apply**.
- English, German, Simplified Chinese, Russian, French, Spanish, Polish, Italian, Brazilian Portuguese and Turkish.
- Interface files loaded through URLs derived from their contents to avoid stale cached views.

### Installation and compatibility

- IPK package for compatible `opkg` firmware.
- Signed APKv3 package for OpenWrt 25.12, with publisher public key and SHA256 checksums.
- Native UCI configuration, procd service, package lifecycle hooks and file-install fallback.
- Hostapd ubus backend and a GL-X3000 MTK backend restricted to the documented driver fingerprints.

Guest Internet blocking, device discovery and device protection have been tested on a GL-X3000. The interface has been checked with LuCI 21.02 and 24.10. Automated checks cover configuration, UI behavior, package structure and simulated enforcement; native packet checks use isolated Linux networks. These checks do not establish every feature on every router. Validate client limits, reconnect behavior and Internet policy on the target firmware.

See the [English](INSTALL.md) or [German](INSTALL.de.md) guide for installation, setup, compatibility and troubleshooting. Release files and checksums are in [packages/0.5.10](../packages/0.5.10/).

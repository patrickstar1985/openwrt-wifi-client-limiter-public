# OpenWrt ipkg-build

`ipkg-build` is an unmodified copy of the official OpenWrt 24.10 package builder:

- Source: <https://github.com/openwrt/openwrt/blob/openwrt-24.10/scripts/ipkg-build>
- Git blob: `6abcc588322dca15ad4070af1aea7b4a4e022c42`
- Upstream copyright and author notices are retained in the script.
- This upstream file is covered by OpenWrt's GPL version 2 license. The original
  OpenWrt `COPYING` is included here. The project's own files remain MIT licensed.

OpenWrt's `.ipk` output is a gzip-compressed tar archive containing
`debian-binary`, `data.tar.gz` and `control.tar.gz`. It is not an arbitrary ZIP
or an APK renamed to `.ipk`.

`root-tar` is a project-owned MIT wrapper around GNU tar. It normalizes archive
owners to root, allowing reproducible unprivileged package builds.

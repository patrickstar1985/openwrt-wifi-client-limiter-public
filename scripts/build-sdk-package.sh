#!/bin/sh
# SPDX-License-Identifier: MIT
# Caller downloads and verifies an official OpenWrt SDK before invoking this.
set -eu
[ "$#" = 2 ] || { echo 'Usage: build-sdk-package.sh <sdk-directory> <output-directory>' >&2; exit 1; }
task_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
sdk="$(CDPATH= cd -- "$1" && pwd)"
mkdir -p "$2"
output="$(CDPATH= cd -- "$2" && pwd)"
[ -f "$sdk/rules.mk" ] && [ -x "$sdk/scripts/feeds" ] || { echo 'Not an OpenWrt SDK directory' >&2; exit 1; }
package_dir="$sdk/package/luci-app-wifi-client-limiter"
if [ -e "$package_dir" ]; then
    echo "Remove the previous project package directory before building: $package_dir" >&2
    exit 1
fi
mkdir -p "$package_dir"
cp "$task_root/Makefile" "$task_root/LICENSE" "$package_dir/"
cp -R "$task_root/files" "$task_root/packaging" "$package_dir/"
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1767225600}"
signing_config=''
if [ -n "${APK_SIGNING_KEY:-}" ]; then
    [ -f "$APK_SIGNING_KEY" ] || { echo 'APK_SIGNING_KEY does not exist' >&2; exit 1; }
    signing_config="$(mktemp "${TMPDIR:-/tmp}/wifi-client-limiter-apk-config.XXXXXX")"
    chmod 600 "$signing_config"
    printf 'sign-key %s\n' "$APK_SIGNING_KEY" > "$signing_config"
    export APK_CONFIG="$signing_config"
fi
trap '[ -z "$signing_config" ] || rm -f "$signing_config"' EXIT INT TERM
cd "$sdk"
# The pinned SDKs include dependency metadata in Config-build.in. Installing
# duplicate core feed definitions would cause Kconfig dependency cycles.
[ -f Config-build.in ] || { echo 'SDK package metadata is missing' >&2; exit 1; }
# SDK Config.in enables ALL by default. A fresh selection also removes package
# choices left by an earlier broad defconfig, while SDK target defaults remain.
cat > .config <<'CONFIG'
# CONFIG_ALL is not set
# CONFIG_ALL_NONSHARED is not set
# CONFIG_ALL_KMODS is not set
# CONFIG_SIGNED_PACKAGES is not set
CONFIG_PACKAGE_luci-app-wifi-client-limiter=m
CONFIG
make defconfig
# This package copies shell/JavaScript, LMO catalogs and the already audited
# optional AArch64 helper. The recipe skips SDK stripping to retain its bytes.
# The SDK already supplies packaging tools. Skip compiling runtime packages
# such as base-files (which would rebuild kernel modules); keep their metadata.
make "package/luci-app-wifi-client-limiter/compile" NO_DEPS=1 V=s -j"${BUILD_JOBS:-2}"
find bin -type f \( -name 'luci-app-wifi-client-limiter_*.ipk' -o -name 'luci-app-wifi-client-limiter-*.apk' \) -exec cp '{}' "$output/" \;
found=0
for package in "$output"/luci-app-wifi-client-limiter*.ipk "$output"/luci-app-wifi-client-limiter*.apk; do
    [ -f "$package" ] || continue
    found=1
    case "$package" in
        *.ipk)
            python3 "$task_root/packaging/verify-sdk-helper.py" "$package"
            ;;
        *.apk)
            apk_tool="$sdk/staging_dir/host/bin/apk"
            [ -x "$apk_tool" ] || { echo 'SDK host apk tool missing' >&2; exit 1; }
            if [ -n "${APK_SIGNING_KEY:-}" ]; then
                if [ -n "${APK_PUBLIC_KEY:-}" ]; then
                    cp "$APK_PUBLIC_KEY" "$output/wifi-client-limiter.pem.pub"
                else
                    openssl pkey -in "$APK_SIGNING_KEY" -pubout -out "$output/wifi-client-limiter.pem.pub"
                fi
                keydir="$(mktemp -d "${TMPDIR:-/tmp}/wifi-client-limiter-sdk-keys.XXXXXX")"
                cp "$output/wifi-client-limiter.pem.pub" "$keydir/"
                "$apk_tool" --keys-dir "$keydir" verify "$package"
                rm -rf "$keydir"
                rm -f "$output/UNSIGNED-APK.txt"
            else
                printf '%s\n' 'This APK is UNSIGNED. See packaging/README.md for explicit installation or publisher signing. Do not disable signature checking globally.' > "$output/UNSIGNED-APK.txt"
                "$apk_tool" --allow-untrusted verify "$package"
            fi
            python3 "$task_root/packaging/verify-sdk-helper.py" "$package" --apk-tool "$apk_tool"
            ;;
    esac
done
[ "$found" = 1 ] || { echo 'SDK did not produce the expected package' >&2; exit 1; }
cd "$output"
find . -maxdepth 1 -type f \( -name '*.ipk' -o -name '*.apk' -o -name '*.pub' \) -print | sort | xargs sha256sum > SHA256SUMS

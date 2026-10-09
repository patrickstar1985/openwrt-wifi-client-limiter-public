#!/bin/sh
# SPDX-License-Identifier: MIT
set -eu
task_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
[ "${1:-}" = --apk-tool ] && [ "$#" -ge 2 ] || {
    echo 'Usage: build-apk-package.sh --apk-tool /path/to/apk [output-directory]' >&2
    exit 1
}
apk_tool="$2"
shift 2
[ -x "$apk_tool" ] || { echo 'apk tool is not executable' >&2; exit 1; }
apk_tool="$(realpath "$apk_tool")"
output="${1:-$task_root/dist}"
mkdir -p "$output"
output="$(CDPATH= cd -- "$output" && pwd)"
. "$task_root/packaging/package.env"
stage="$(mktemp -d "${TMPDIR:-/tmp}/wifi-client-limiter-apk.XXXXXX")"
trap 'rm -rf "$stage"' EXIT INT TERM
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1767225600}"
python3 "$task_root/packaging/stage-package.py" "$stage/package" apk --scripts-directory "$stage/hooks"
package="$output/$PACKAGE_NAME-$PACKAGE_VERSION-r$PACKAGE_RELEASE.apk"
set --
if [ -n "${APK_SIGNING_KEY:-}" ]; then
    [ -f "$APK_SIGNING_KEY" ] || { echo 'APK_SIGNING_KEY does not exist' >&2; exit 1; }
    set -- --sign-key "$APK_SIGNING_KEY"
fi
if [ "$(id -u)" != 0 ]; then
    command -v fakeroot >/dev/null || { echo 'An unprivileged APK build requires fakeroot to store root ownership' >&2; exit 1; }
    set -- "$apk_tool" mkpkg "$@"
    apk_tool_builder=fakeroot
else
    set -- mkpkg "$@"
    apk_tool_builder="$apk_tool"
fi
"$apk_tool_builder" "$@" \
    --info "name:$PACKAGE_NAME" \
    --info "version:$PACKAGE_VERSION-r$PACKAGE_RELEASE" \
    --info 'arch:noarch' \
    --info 'license:MIT' \
    --info "origin:package/$PACKAGE_NAME" \
    --info "url:$PACKAGE_URL" \
    --info "maintainer:$PACKAGE_MAINTAINER" \
    --info "description:$PACKAGE_DESCRIPTION" \
    --info "depends:$PACKAGE_DEPENDS" \
    --script "pre-install:$stage/hooks/preinst" \
    --script "pre-upgrade:$stage/hooks/pre-upgrade" \
    --script "post-install:$stage/hooks/post-install" \
    --script "post-upgrade:$stage/hooks/post-upgrade" \
    --script "pre-deinstall:$stage/hooks/pre-deinstall" \
    --script "post-deinstall:$stage/hooks/postrm" \
    --files "$stage/package" --output "$package"
if [ -n "${APK_SIGNING_KEY:-}" ]; then
    if [ -n "${APK_PUBLIC_KEY:-}" ]; then
        cp "$APK_PUBLIC_KEY" "$output/wifi-client-limiter.pem.pub"
    else
        openssl pkey -in "$APK_SIGNING_KEY" -pubout -out "$output/wifi-client-limiter.pem.pub"
    fi
    mkdir -p "$stage/keys"
    cp "$output/wifi-client-limiter.pem.pub" "$stage/keys/"
    "$apk_tool" --keys-dir "$stage/keys" verify "$package"
    rm -f "$output/UNSIGNED-APK.txt"
else
    printf '%s\n' 'This APK is UNSIGNED. Review the publisher and checksum before an explicit per-command --allow-untrusted installation. Do not disable signature checking globally.' > "$output/UNSIGNED-APK.txt"
    "$apk_tool" --allow-untrusted verify "$package"
fi
cd "$output"
find . -maxdepth 1 -type f \( -name '*.ipk' -o -name '*.apk' -o -name '*.pub' \) -print | sort | xargs sha256sum > SHA256SUMS

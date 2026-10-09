#!/bin/sh
# SPDX-License-Identifier: MIT
# Give each UI revision its own URL. LuCI's resource version belongs to the
# firmware and does not change when this package is upgraded independently.
set -eu
[ "$#" = 1 ] || { echo 'Usage: stage-view.sh <staged-root>' >&2; exit 1; }
view_root=${1%/}
view_dir="$view_root/www/luci-static/resources/view"
view_menu="$view_root/usr/share/luci/menu.d/luci-app-wifi-client-limiter.json"
view_source="$view_dir/wifi-client-limiter.js"
[ -f "$view_source" ] && [ -f "$view_menu" ] || { echo 'WiFi Client Limiter UI staging files are missing.' >&2; exit 1; }
view_hash=$(sha256sum "$view_source")
view_hash=${view_hash%% *}
case "$view_hash" in ''|*[!0-9a-f]*) echo 'Invalid UI SHA-256 digest.' >&2; exit 1 ;; esac
[ "${#view_hash}" = 64 ] || { echo 'Invalid UI SHA-256 digest length.' >&2; exit 1; }
[ "$(grep -Ec '^[[:space:]]*"path":[[:space:]]*"wifi-client-limiter"[[:space:]]*$' "$view_menu")" = 1 ] || {
    echo 'Expected exactly one canonical WiFi Client Limiter menu view.' >&2
    exit 1
}
cp "$view_source" "$view_dir/wifi-client-limiter-$view_hash.js"
chmod 644 "$view_dir/wifi-client-limiter-$view_hash.js"
sed "s/\"path\":[[:space:]]*\"wifi-client-limiter\"/\"path\": \"wifi-client-limiter-$view_hash\"/" "$view_menu" > "$view_menu.new"
chmod 644 "$view_menu.new"
mv "$view_menu.new" "$view_menu"

#!/bin/sh
set -eu
[ "$(id -u)" = 0 ] || exit 1
if [ -x /etc/init.d/wifi-client-limiter ]; then
 /etc/init.d/wifi-client-limiter stop
 /etc/init.d/wifi-client-limiter disable
fi
if [ -x /usr/share/wifi-client-limiter/internet-policy.sh ]; then
 /usr/share/wifi-client-limiter/internet-policy.sh cleanup
fi
if [ -x /usr/share/wifi-client-limiter/station-adapter.sh ]; then
 /usr/share/wifi-client-limiter/station-adapter.sh cleanup
fi
if [ -x /usr/share/wifi-client-limiter/package-hooks ]; then
 /usr/share/wifi-client-limiter/package-hooks firewall-remove
fi
rm -f /usr/sbin/wifi-client-limiter /etc/init.d/wifi-client-limiter
rm -f /usr/libexec/rpcd/wifi-client-limiter
rm -f /usr/share/wifi-client-limiter/internet-policy.sh
rm -f /usr/share/wifi-client-limiter/firewall-reload.sh
rm -f /usr/share/wifi-client-limiter/station-adapter.sh
rm -f /usr/share/wifi-client-limiter/mtk-acl-query-aarch64
rm -f /usr/share/wifi-client-limiter/vendor-acceleration.sh
rm -f /usr/share/wifi-client-limiter/package-hooks
rm -f /usr/share/nftables.d/ruleset-post/90-wifi-client-limiter.nft
rmdir /etc/wifi-client-limiter 2>/dev/null || true
rm -f /www/luci-static/resources/view/wifi-client-limiter.js
for view_file in /www/luci-static/resources/view/wifi-client-limiter-*.js; do
 view_hash=${view_file##*/wifi-client-limiter-}
 view_hash=${view_hash%.js}
 case "$view_hash" in ''|*[!0-9a-f]*) continue ;; esac
 [ "${#view_hash}" = 64 ] || continue
 rm -f "$view_file"
done
rm -f /usr/lib/lua/luci/i18n/wifi-client-limiter.*.lmo
rm -f /usr/share/wifi-client-limiter/languages
rmdir /usr/share/wifi-client-limiter 2>/dev/null || true
rm -f /usr/share/luci/menu.d/luci-app-wifi-client-limiter.json /usr/share/rpcd/acl.d/luci-app-wifi-client-limiter.json
rm -rf /tmp/wifi-client-limiter /tmp/luci-modulecache
rm -f /tmp/luci-indexcache /tmp/luci-indexcache.*
/etc/init.d/rpcd restart
echo 'Entfernt. Konfiguration bleibt für eine spätere Installation erhalten.'

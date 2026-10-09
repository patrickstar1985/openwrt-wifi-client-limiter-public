#!/bin/sh
set -eu
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo 'Als root ausführen'; exit 1; }
[ -f /etc/openwrt_release ] || { echo 'Nur auf OpenWrt installieren'; exit 1; }
for c in ubus uci iw iwinfo iwpriv jsonfilter awk sort logger nft sha256sum; do command -v "$c" >/dev/null || { echo "Fehlt: $c"; exit 1; }; done
if ! command -v fw3 >/dev/null 2>&1 && ! command -v fw4 >/dev/null 2>&1; then
 echo 'Vorhandenes OpenWrt firewall3 oder firewall4 wird benötigt.' >&2
 exit 1
fi
[ -f /lib/functions.sh ] && [ -f /usr/share/libubox/jshn.sh ] || { echo 'libubox/jshn fehlt'; exit 1; }
[ -d /www/luci-static/resources ] || { echo 'Modernes LuCI fehlt. Zuerst luci installieren.'; exit 1; }
ui_stage=$(mktemp -d /tmp/wifi-client-limiter-ui.XXXXXX)
trap 'rm -rf "$ui_stage"' 0
trap 'exit 1' HUP INT TERM
mkdir -p "$ui_stage/www/luci-static/resources/view" "$ui_stage/usr/share/luci/menu.d"
cp files/www/luci-static/resources/view/wifi-client-limiter.js "$ui_stage/www/luci-static/resources/view/"
cp files/usr/share/luci/menu.d/luci-app-wifi-client-limiter.json "$ui_stage/usr/share/luci/menu.d/"
sh packaging/stage-view.sh "$ui_stage"
. files/usr/share/wifi-client-limiter/upgrade-recovery.sh
UPGRADE_CHECKSUM_FILE="$(pwd)/files/usr/share/wifi-client-limiter/posix-cksum.sh"
trap 'upgrade_recovery_finish; rm -rf "$ui_stage"' 0
trap 'exit 1' HUP INT TERM
upgrade_recovery_prepare
mkdir -p /etc/config
if [ -f /etc/config/wifi-client-limiter ]; then
 cp /etc/config/wifi-client-limiter /etc/config/wifi-client-limiter.backup
else
 cp files/etc/config/wifi-client-limiter /etc/config/wifi-client-limiter
fi
for dir in usr www; do cp -R "files/$dir/." "/$dir/"; done
cp -R "$ui_stage/." /
cp files/etc/init.d/wifi-client-limiter /etc/init.d/wifi-client-limiter
chmod 755 /usr/sbin/wifi-client-limiter /etc/init.d/wifi-client-limiter
chmod 755 /usr/share/wifi-client-limiter/internet-policy.sh /usr/libexec/rpcd/wifi-client-limiter
chmod 755 /usr/share/wifi-client-limiter/firewall-reload.sh
chmod 755 /usr/share/wifi-client-limiter/station-adapter.sh /usr/share/wifi-client-limiter/vendor-acceleration.sh
chmod 755 /usr/share/wifi-client-limiter/mtk-acl-query-aarch64
cp packaging/root/usr/share/wifi-client-limiter/package-hooks /usr/share/wifi-client-limiter/package-hooks
chmod 755 /usr/share/wifi-client-limiter/package-hooks
/usr/share/wifi-client-limiter/package-hooks firewall-register
/etc/init.d/wifi-client-limiter enable
/etc/init.d/wifi-client-limiter start
# Offer shipped translations without changing the user's chosen LuCI language.
languages_changed=0
while IFS='|' read -r code label; do
 [ -n "$code" ] || continue
 if [ -z "$(uci -q get "luci.languages.$code" || true)" ]; then
  uci set "luci.languages.$code=$label"
  languages_changed=1
 fi
done < files/usr/share/wifi-client-limiter/languages
[ "$languages_changed" = 0 ] || uci commit luci
/etc/init.d/rpcd restart
rm -f /tmp/luci-indexcache /tmp/luci-indexcache.*
rm -rf /tmp/luci-modulecache
for config_file in /etc/config/wifi-client-limiter /etc/wifi-client-limiter/offload-backup /etc/wifi-client-limiter/offload-reload-required /etc/wifi-client-limiter/native-flowoffload-backup /etc/wifi-client-limiter/vendor-acceleration-backup /etc/wifi-client-limiter/vendor-acceleration-reload-required; do
 if ! grep -Fxq "$config_file" /etc/sysupgrade.conf 2>/dev/null; then echo "$config_file" >> /etc/sysupgrade.conf; fi
done
echo 'Installiert: LuCI > Dienste > WLAN Client Limiter. Zuerst WLANs und Ausnahmen setzen, im Testmodus prüfen.'

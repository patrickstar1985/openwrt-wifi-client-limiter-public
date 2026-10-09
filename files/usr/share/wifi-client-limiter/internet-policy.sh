#!/bin/sh
# SPDX-License-Identifier: MIT
# The worker sources this helper. The executable cleanup command is also used
# by package removal and service stop, including when no worker is running.
INTERNET_STATE="${INTERNET_STATE:-${STATE:-/tmp/wifi-client-limiter}}"
INTERNET_BACKUP_DIR="${INTERNET_BACKUP_DIR:-/etc/wifi-client-limiter}"
INTERNET_TABLE=wifi_client_limiter
INTERNET_VENDOR_HELPER="${INTERNET_VENDOR_HELPER:-/usr/share/wifi-client-limiter/vendor-acceleration.sh}"
INTERNET_MODULE_ROOT="${INTERNET_MODULE_ROOT:-/sys/module}"
INTERNET_CHECKSUM_HELPER="${INTERNET_CHECKSUM_HELPER:-/usr/share/wifi-client-limiter/posix-cksum.sh}"

internet_checksum_load() {
 [ "${INTERNET_CHECKSUM_LOADED:-0}" = 1 ] && return 0
 [ -r "$INTERNET_CHECKSUM_HELPER" ] || return 1
 . "$INTERNET_CHECKSUM_HELPER" || return 1
 command -v internet_posix_cksum >/dev/null 2>&1 &&
 command -v internet_checksum_record >/dev/null 2>&1 || return 1
 INTERNET_CHECKSUM_LOADED=1
}

internet_vendor_load() {
 [ "${INTERNET_VENDOR_LOADED:-0}" = 1 ] && return 0
 [ -r "$INTERNET_VENDOR_HELPER" ] || return 1
 . "$INTERNET_VENDOR_HELPER" || return 1
 command -v internet_vendor_disable >/dev/null 2>&1 &&
 command -v internet_vendor_inactive >/dev/null 2>&1 &&
 command -v internet_vendor_restore >/dev/null 2>&1 || return 1
 INTERNET_VENDOR_LOADED=1
}
internet_vendor_status_error() {
 local fallback="$1" code="${INTERNET_VENDOR_ERROR:-}"
 # The adapter supplies fixed diagnostics. Keep arbitrary output out of the
 # hand-built status JSON and log, while retaining its actionable reason.
 case "$code" in
  vendor_controls_unknown|vendor_tool_missing|vendor_acceleration_unknown|vendor_network_unknown|vendor_vpn_hotplug_unsupported|vendor_config_unknown|vendor_pending_changes|vendor_config_active|vendor_runtime_unknown|vendor_hnat_active|vendor_flows_active_or_unknown|vendor_sfe_active|vendor_service_unknown|vendor_sfe_boot_active|vendor_backup_failed|vendor_backup_invalid|vendor_section_changed|vendor_config_write_failed|vendor_disable_failed|vendor_config_restore_failed|vendor_module_restore_failed|vendor_hook_restore_failed) ;;
  *) code="$fallback";;
 esac
 internet_status error "$code"
}

internet_backend_detect() {
 if command -v fw4 >/dev/null 2>&1; then
  INTERNET_BACKEND=fw4; INTERNET_FAMILY=netdev
 elif command -v fw3 >/dev/null 2>&1; then
  INTERNET_BACKEND=fw3; INTERNET_FAMILY=netdev
 else INTERNET_BACKEND=unknown; INTERNET_FAMILY=unknown; return 1; fi
 command -v nft >/dev/null 2>&1 || return 1
 if [ "$INTERNET_BACKEND" = fw3 ]; then
  command -v iptables-save >/dev/null 2>&1 && command -v ip6tables-save >/dev/null 2>&1 || return 1
  [ -d "$INTERNET_MODULE_ROOT" ] && [ -r "$INTERNET_MODULE_ROOT" ] && [ -x "$INTERNET_MODULE_ROOT" ] || return 1
  internet_native_module_tools || return 1
 fi
}
internet_native_reload() {
 internet_backend_detect || return 1
 "$INTERNET_BACKEND" reload >/dev/null 2>&1
}
internet_native_module_present() { [ -d "$INTERNET_MODULE_ROOT/xt_FLOWOFFLOAD" ]; }
internet_native_module_tools() {
 command -v rmmod >/dev/null 2>&1 || return 1
 if command -v modprobe >/dev/null 2>&1; then INTERNET_MODULE_LOADER=modprobe
 elif command -v insmod >/dev/null 2>&1; then INTERNET_MODULE_LOADER=insmod
 else return 1; fi
}
internet_native_module_prepare() {
 local backup="$INTERNET_BACKUP_DIR/native-flowoffload-backup" value=0
 [ "$INTERNET_BACKEND" = fw3 ] || return 0
 internet_native_module_tools || return 1
 if [ -f "$backup" ]; then
  value="$(cat "$backup")" || return 1
  case "$value" in 0|1) return 0;; *) return 1;; esac
 fi
 mkdir -p "$INTERNET_BACKUP_DIR" || return 1
 chmod 700 "$INTERNET_BACKUP_DIR"
 internet_native_module_present && value=1
 printf '%s\n' "$value" > "$backup-next" || return 1
 chmod 600 "$backup-next"
 mv "$backup-next" "$backup"
}
internet_native_module_suspend() {
 [ "$INTERNET_BACKEND" = fw3 ] || return 0
 # Removing the target rule does not destroy OpenWrt5.4's module-global
 # flowtable. Unloading it flushes existing native hardware flows as well.
 # Never unload while any native target rule remains in either IP family.
 internet_native_rules_inactive || return 1
 if internet_native_module_present; then rmmod xt_FLOWOFFLOAD >/dev/null 2>&1 || return 1; fi
 ! internet_native_module_present
}
internet_native_module_restore() {
 local backup="$INTERNET_BACKUP_DIR/native-flowoffload-backup" value
 [ -f "$backup" ] || return 0
 value="$(cat "$backup")" || return 1
 case "$value" in 0|1) ;; *) return 1;; esac
 if [ "$value" = 1 ] && ! internet_native_module_present; then
  internet_native_module_tools || return 1
  "$INTERNET_MODULE_LOADER" xt_FLOWOFFLOAD >/dev/null 2>&1 || return 1
  internet_native_module_present || return 1
 fi
 # Originally absent modules may now be required by newly restored or edited
 # offload settings; do not unload them during restoration.
 rm -f "$backup"
}

internet_lock() {
 local owner observed= attempts=0 current
 [ "${INTERNET_LOCKED:-0}" != 1 ] || return 0
 mkdir -p "$INTERNET_STATE"
 while ! mkdir "$INTERNET_STATE/policy-lock" 2>/dev/null; do
  owner=; read -r owner 2>/dev/null < "$INTERNET_STATE/policy-lock/pid" || :
  case "$owner" in ''|*[!0-9]*)
   if [ "$observed" != "$owner" ]; then observed="$owner"; attempts=0; fi
   if [ "$attempts" -ge 5 ]; then
    current=; read -r current 2>/dev/null < "$INTERNET_STATE/policy-lock/pid" || :
    if [ "$current" = "$owner" ]; then
     rm -f "$INTERNET_STATE/policy-lock/pid"
     rmdir "$INTERNET_STATE/policy-lock" 2>/dev/null || return 1
     attempts=0; continue
    fi
   fi;; *)
   if ! kill -0 "$owner" 2>/dev/null; then
    rm -f "$INTERNET_STATE/policy-lock/pid"
    rmdir "$INTERNET_STATE/policy-lock" 2>/dev/null || return 1
    continue
   fi;;
  esac
  attempts=$((attempts+1)); [ "$attempts" -le 5 ] || return 1
  sleep 1
 done
 printf '%s\n' "$$" > "$INTERNET_STATE/policy-lock/pid"
 INTERNET_LOCKED=1
}
internet_unlock() {
 rm -f "$INTERNET_STATE/policy-lock/pid"
 rmdir "$INTERNET_STATE/policy-lock" 2>/dev/null || :
 INTERNET_LOCKED=0
}

internet_valid_name() {
 case "$1" in ''|*[!a-zA-Z0-9_.-]*) return 1;; esac
 [ "${#1}" -le 15 ]
}
internet_valid_network() {
 case "$1" in ''|*[!a-zA-Z0-9_]*) return 1;; esac
 [ "${#1}" -le 64 ]
}
internet_valid_mac() { printf '%s\n' "$1" | grep -Eq '^([0-9a-f]{2}:){5}[0-9a-f]{2}$'; }
internet_table_exists() {
 case "${INTERNET_FAMILY:-}" in inet|netdev) ;; *) internet_backend_detect || return 1;; esac
 nft list table "$INTERNET_FAMILY" "$INTERNET_TABLE" >/dev/null 2>&1
}
internet_owned_table_exists() {
 nft list table inet "$INTERNET_TABLE" >/dev/null 2>&1 || nft list table netdev "$INTERNET_TABLE" >/dev/null 2>&1
}
internet_delete_owned_tables() {
 local family failed=0
 for family in inet netdev; do
  if nft list table "$family" "$INTERNET_TABLE" >/dev/null 2>&1; then
   nft delete table "$family" "$INTERNET_TABLE" >/dev/null 2>&1 || failed=1
  fi
 done
 [ "$failed" = 0 ]
}
internet_status() {
 local state="$1" error="${2:-}" active=false offload=false iface separator=
 [ "$state" != blocked ] || active=true
 [ "$state" != error ] || { internet_owned_table_exists && active=true; }
 if [ -f "$INTERNET_BACKUP_DIR/offload-backup" ] || [ -f "$INTERNET_BACKUP_DIR/native-flowoffload-backup" ] || [ -f "$INTERNET_BACKUP_DIR/vendor-acceleration-backup" ]; then offload=true; fi
 mkdir -p "$INTERNET_STATE"
 {
  case "${INTERNET_BACKEND:-}:${INTERNET_FAMILY:-}" in fw4:netdev|fw3:netdev) ;; *) internet_backend_detect || :;; esac
  printf '{"internet_state":"%s","active":%s,"error":"%s","offload_disabled":%s,"firewall_backend":"%s","nft_family":"%s","interfaces":[' "$state" "$active" "$error" "$offload" "${INTERNET_BACKEND:-unknown}" "${INTERNET_FAMILY:-unknown}"
  for iface in ${internet_interfaces:-}; do
   internet_valid_name "$iface" || continue
   printf '%s"%s"' "$separator" "$iface"; separator=,
  done
  printf ']}\n'
 } > "$INTERNET_STATE/internet-status-next"
 if [ "$state" = error ] && ! cmp -s "$INTERNET_STATE/internet-status-next" "$INTERNET_STATE/internet-status.json"; then
  logger -t wifi-client-limiter "Guest Internet policy error: $error"
 fi
 mv "$INTERNET_STATE/internet-status-next" "$INTERNET_STATE/internet-status.json"
}
# Only prefixes derived from local interface addresses are eligible. Never use
# route tables, default routes, or the whole delegated IPv6 prefix.
internet_prefix() {
 local family="$1" address="$2" mask="$3"
 case "$mask" in ''|*[!0-9]*) return 1;; esac
 case "$family" in
  4)
   [ "$mask" -ge 1 ] && [ "$mask" -le 32 ] || return 1
   printf '%s\n' "$address" | awk -F. -v mask="$mask" '
    NF != 4 {exit 1}
    {for(i=1;i<=4;i++) if($i !~ /^[0-9]+$/ || $i>255) exit 1
     for(i=1;i<=4;i++) {n=mask-(i-1)*8; if(n<0)n=0; if(n>8)n=8; step=2^(8-n); part[i]=int($i/step)*step}
     printf "%d.%d.%d.%d/%d\n",part[1],part[2],part[3],part[4],mask}'
   ;;
  6)
   [ "$mask" -ge 1 ] && [ "$mask" -le 128 ] || return 1
   # A final nft syntax check also validates IPv6 compression and prefix bits.
   case "$address" in ''|*[!a-fA-F0-9:]*|*:::*) return 1;; esac
   case "$address" in *:*) ;; *) return 1;; esac
   [ "${#address}" -le 39 ] || return 1
   printf '%s/%s\n' "$address" "$mask"
   ;;
  *) return 1;;
 esac
}
internet_address_array() {
 local array="$1" family="$2" keys key address mask prefix
 if json_select "$array"; then
  json_get_keys keys
  for key in $keys; do
   json_select "$key" || continue
   json_get_var address address; json_get_var mask mask
   prefix="$(internet_prefix "$family" "$address" "$mask")" || { json_select ..; json_select ..; return 1; }
   printf '%s %s\n' "$family" "$prefix" >> "$INTERNET_STATE/internet-prefixes-next"
   json_select ..
  done
  json_select ..
 fi
}
internet_network_prefixes() {
 local logical="$1" data keys key address mask prefix before type local_keys address_type mask_type
 before="$(wc -l < "$INTERNET_STATE/internet-prefixes-next")"
 data="$(ubus call "network.interface.$logical" status 2>/dev/null)" || return 1
 json_load "$data" || return 1
 internet_address_array ipv4-address 4 || return 1
 internet_address_array ipv6-address 6 || return 1
 if json_select ipv6-prefix-assignment; then
  json_get_keys keys
  for key in $keys; do
   json_select "$key" || continue
   json_get_type type local-address
   if [ -n "$type" ]; then
    [ "$type" = object ] || return 1
    json_select local-address || return 1
    # netifd may report an assignment before a local IPv6 address exists.
    # An empty optional object contributes no prefix; never substitute its
    # enclosing delegated prefix, which can cover other networks.
    json_get_keys local_keys || return 1
    if [ -n "$local_keys" ]; then
     json_get_type address_type address; json_get_type mask_type mask
     [ "$address_type" = string ] && [ "$mask_type" = int ] || return 1
     json_get_var address address; json_get_var mask mask
     prefix="$(internet_prefix 6 "$address" "$mask")" || return 1
     printf '6 %s\n' "$prefix" >> "$INTERNET_STATE/internet-prefixes-next"
    fi
    json_select ..
   fi
   json_select ..
  done
  json_select ..
 fi
 [ "$(wc -l < "$INTERNET_STATE/internet-prefixes-next")" -gt "$before" ]
}
internet_collect() {
 local data radios radio entries entry section iface mode selected logical attached
 internet_interfaces=; selected=
 : > "$INTERNET_STATE/internet-prefixes-next"
 data="$(ubus call network.wireless status 2>/dev/null)" || return 1
 json_load "$data" || return 1
 json_get_keys radios
 for radio in $radios; do
  json_select "$radio" || continue
  if json_select interfaces; then
   json_get_keys entries
   for entry in $entries; do
    json_select "$entry" || continue
    json_get_var section section; json_get_var iface ifname
    mode=; if json_select config; then json_get_var mode mode; json_select ..; fi
    case " $networks " in *" $section "*)
     if [ "$mode" = ap ] && internet_valid_name "$iface" && internet_valid_network "$section"; then
      internet_interfaces="$internet_interfaces $iface"; selected="$selected $section"
     fi;;
    esac
    json_select ..
   done
   json_select ..
  fi
  json_select ..
 done
 internet_interfaces="$(printf '%s\n' $internet_interfaces | sort -u)"
 [ -n "$internet_interfaces" ] || return 0
 # The wireless UCI network list defines the local prefixes of selected APs.
 # Looking only at these logical networks also works for bridge-backed APs.
 config_load wireless
 for section in $selected; do
  config_get attached "$section" network
  [ -n "$attached" ] || return 1
  for logical in $attached; do
   internet_valid_network "$logical" || return 1
   internet_network_prefixes "$logical" || return 1
  done
 done
 sort -u "$INTERNET_STATE/internet-prefixes-next" > "$INTERNET_STATE/internet-prefixes-sorted"
 mv "$INTERNET_STATE/internet-prefixes-sorted" "$INTERNET_STATE/internet-prefixes-next"
}
internet_render() {
 local iface mac family prefix chain=0
 {
  # add is idempotent. These three commands replace this table atomically even
  # after a firewall flush, without flushing or changing any other table.
  printf 'add table %s %s\ndelete table %s %s\ntable %s %s {\n' "$INTERNET_FAMILY" "$INTERNET_TABLE" "$INTERNET_FAMILY" "$INTERNET_TABLE" "$INTERNET_FAMILY" "$INTERNET_TABLE"
  for iface in $internet_interfaces; do
   chain=$((chain+1))
   printf ' chain ap_%s {\n  type filter hook ingress device "%s" priority -500; policy accept;\n' "$chain" "$iface"
   for mac in $white; do
    internet_valid_mac "$mac" || continue
    printf '  ether saddr %s return\n' "$mac"
   done
   # A tagged frame must not escape a netdev IP match by changing its outer
   # EtherType. Guest APs accept ordinary untagged client traffic; protected
   # devices retain their complete bypass, including their VLAN traffic.
   printf '  ether type { 0x8100, 0x88a8, 0x9100 } counter drop\n'
   while read -r family prefix; do
    case "$family" in
     4) printf '  ip daddr %s return\n' "$prefix";;
     6) printf '  ip6 daddr %s return\n' "$prefix";;
    esac
   done < "$INTERNET_STATE/internet-prefixes-next"
   # ARP and ordinary non-IP traffic pass naturally. DHCP and local
   # discovery/control traffic keep DHCP, neighbour discovery and mDNS usable.
   printf '  ip daddr 255.255.255.255 udp sport 68 udp dport 67 return\n'
   printf '  ip daddr 224.0.0.0/24 return\n'
   printf '  ip6 daddr { fe80::/10, ff01::/16, ff02::/16 } return\n'
   printf '  ether type { 0x0800, 0x86dd } counter drop\n'
   printf ' }\n'
  done
  printf '}\n'
 } > "$INTERNET_STATE/internet-policy-next"
}
internet_offload_option() {
 local section="$1" option="$2" value
 value="$(uci -q get "firewall.$section.$option")" || value=-
 case "$value" in -|0|1|true|false|yes|no|on|off) printf '%s\n' "$value";; *) return 1;; esac
}
internet_defaults_sections() {
 local snapshot
 snapshot="$(uci -q -X show firewall)" || return 1
 printf '%s\n' "$snapshot" | sed -n 's/^firewall\.\([a-zA-Z0-9_]*\)=defaults$/\1/p'
}
internet_defaults_fingerprint() {
 local section="$1" snapshot normalized sorted fingerprint
 internet_checksum_load || return 1
 snapshot="$(uci -q -X show "firewall.$section")" || return 1
 normalized="$(printf '%s\n' "$snapshot" | awk -v prefix="firewall.$section" '
  index($0,prefix)==1 {line=substr($0,length(prefix)+1)
   if(line !~ /^\.(flow_offloading|flow_offloading_hw)=/) print line}')" || return 1
 [ -n "$normalized" ] || return 1
 # Restore the final newline stripped by each capture, preserving legacy
 # fingerprints while checking every provider's status before parsing.
 sorted="$(printf '%s\n' "$normalized" | sort)" || return 1
 [ -n "$sorted" ] || return 1
 fingerprint="$(printf '%s\n' "$sorted" | internet_posix_cksum)" || return 1
 fingerprint="$(internet_checksum_record "$fingerprint" colon)" || return 1
 printf '%s\n' "$fingerprint"
}
internet_defaults_resolve() {
 local original="$1" fingerprint="$2" section matched= count=0 actual sections
 internet_checksum_load || return 1
 internet_checksum_record "$fingerprint" colon >/dev/null || return 1
 # Anonymous cfg IDs include option values, so they change after a UCI commit.
 # Match the untouched defaults fields instead. A unique match is required,
 # even when a positional or old generated name could still be looked up.
 sections="$(internet_defaults_sections)" || return 1
 for section in $sections; do
  actual="$(internet_defaults_fingerprint "$section")" || return 1
  if [ "$actual" = "$fingerprint" ]; then matched="$section"; count=$((count+1)); fi
 done
 [ "$count" = 1 ] || return 1
 printf '%s\n' "$matched"
}
internet_uci_rollback() {
 local key
 if [ -f "$INTERNET_STATE/internet-uci-edits" ]; then
  while read -r key; do uci -q revert "firewall.$key" >/dev/null 2>&1 || :; done < "$INTERNET_STATE/internet-uci-edits"
 fi
 rm -f "$INTERNET_STATE/internet-uci-edits"
 return 1
}
internet_offload_disable() {
 local section original fingerprint option value sections changed=0 backup="$INTERNET_BACKUP_DIR/offload-backup" reload="$INTERNET_BACKUP_DIR/offload-reload-required"
 # Refuse to commit unrelated pending firewall edits made in another LuCI tab.
 [ -z "$(uci -q changes firewall)" ] || return 1
 internet_backend_detect && internet_native_module_prepare || return 1
 if [ ! -f "$backup" ]; then
  mkdir -p "$INTERNET_BACKUP_DIR" || return 1
  chmod 700 "$INTERNET_BACKUP_DIR"
  : > "$backup-next" || return 1
  sections="$(internet_defaults_sections)"
  [ -n "$sections" ] || { rm -f "$backup-next"; return 1; }
  for section in $sections; do
   fingerprint="$(internet_defaults_fingerprint "$section")" || { rm -f "$backup-next"; return 1; }
   internet_defaults_resolve "$section" "$fingerprint" >/dev/null || { rm -f "$backup-next"; return 1; }
   for option in flow_offloading flow_offloading_hw; do
    value="$(internet_offload_option "$section" "$option")" || { rm -f "$backup-next"; return 1; }
    printf '%s %s %s %s\n' "$section" "$fingerprint" "$option" "$value" >> "$backup-next"
   done
  done
  chmod 600 "$backup-next"
  mv "$backup-next" "$backup" || return 1
  # Reload even when UCI already says0: an older live flowtable may still be
  # present after somebody saved defaults without reloading the firewall.
  printf 'disable\n' > "$reload" || return 1
 fi
 internet_offload_validate "$backup" strict || return 1
 internet_offload_update_intent "$backup" || return 1
 : > "$INTERNET_STATE/internet-uci-edits" || return 1
 while read -r original fingerprint option value; do
  section="$(internet_defaults_resolve "$original" "$fingerprint")" || { internet_uci_rollback; return 1; }
  case "$option" in flow_offloading|flow_offloading_hw) ;; *) return 1;; esac
  [ "$(uci -q get "firewall.$section")" = defaults ] || return 1
  if [ "$(uci -q get "firewall.$section.$option")" != 0 ]; then
   printf '%s.%s\n' "$section" "$option" >> "$INTERNET_STATE/internet-uci-edits"
   uci -q set "firewall.$section.$option=0" || { internet_uci_rollback; return 1; }; changed=1
  fi
 done < "$backup"
 if [ "$changed" = 1 ]; then
  printf 'disable\n' > "$reload" || { internet_uci_rollback; return 1; }
  uci -q commit firewall || { internet_uci_rollback; return 1; }
 fi
 rm -f "$INTERNET_STATE/internet-uci-edits"
 if [ -f "$reload" ]; then
  internet_native_reload || return 1
  internet_native_rules_inactive && internet_native_module_suspend && internet_offload_inactive || return 1
  rm -f "$reload"
 fi
 # Do not let a stale or manually recreated native flowtable masquerade as a
 # safe policy merely because the saved UCI options still say0.
 if ! internet_offload_inactive; then
  printf 'disable\n' > "$reload" || return 1
  internet_native_reload && internet_native_rules_inactive && internet_native_module_suspend && internet_offload_inactive || return 1
  rm -f "$reload"
 fi
}
internet_offload_update_intent() {
 local backup="$1" section original fingerprint option value current changed=0
 : > "$backup-next" || return 1
 while read -r original fingerprint option value; do
  section="$(internet_defaults_resolve "$original" "$fingerprint")" || { rm -f "$backup-next"; return 1; }
  current="$(internet_offload_option "$section" "$option")" || { rm -f "$backup-next"; return 1; }
  # A deliberate later edit to an owned option is suspended while blocking is
  # needed, but becomes the value restored afterwards. An unchanged forced0
  # retains the previous saved intent (same-value manual0 is indistinguishable).
  if [ "$current" != 0 ] && [ "$current" != "$value" ]; then value="$current"; changed=1; fi
  printf '%s %s %s %s\n' "$original" "$fingerprint" "$option" "$value" >> "$backup-next"
 done < "$backup"
 if [ "$changed" = 1 ]; then
  chmod 600 "$backup-next"
  mv "$backup-next" "$backup" || return 1
  logger -t wifi-client-limiter 'Updated saved flow offload settings; offload remains suspended while guest Internet is blocked'
 else rm -f "$backup-next"; fi
}
internet_offload_inactive() {
 internet_native_rules_inactive || return 1
 [ "$INTERNET_BACKEND" != fw3 ] || ! internet_native_module_present
}
internet_native_rules_inactive() {
 local live family
 internet_backend_detect || return 1
 if [ "$INTERNET_BACKEND" = fw4 ]; then
  nft list table inet fw4 >/dev/null 2>&1 || return 1
  ! nft list flowtable inet fw4 ft >/dev/null 2>&1
 else
  for family in 4 6; do
   live="$(internet_fw3_live "$family")" || return 1
   internet_flowoffload_present "$live" && return 1
  done
  return 0
 fi
}
internet_flowoffload_present() {
 printf '%s\n' "$1" | grep -Eq -- '(^|[[:space:]])(-j|--jump)[[:space:]]+FLOWOFFLOAD([[:space:]]|$)'
}
internet_flowoffload_hardware() {
 printf '%s\n' "$1" | grep -E -- '(^|[[:space:]])(-j|--jump)[[:space:]]+FLOWOFFLOAD([[:space:]]|$)' |
 grep -Eq -- '(^|[[:space:]])--hw([[:space:]]|$)'
}
internet_fw3_live() {
 local data
 case "$1" in
  4) data="$(iptables-save -t filter 2>/dev/null)" || return 1;;
  6) data="$(ip6tables-save -t filter 2>/dev/null)" || return 1;;
  *) return 1;;
 esac
 # A successful empty dump is not evidence that the native firewall is live.
 printf '%s\n' "$data" | grep -q '^\*filter$' &&
 printf '%s\n' "$data" | grep -q '^:FORWARD ' || return 1
 printf '%s\n' "$data"
}
internet_offload_restored() {
 local expected live family ipv6_disabled
 internet_backend_detect || return 1
 if [ "$INTERNET_BACKEND" = fw4 ]; then
  nft list table inet fw4 >/dev/null 2>&1 || return 1
  expected="$(fw4 print 2>/dev/null)" || return 1
  # Enabled offload need not yield a flowtable when no eligible uplink exists.
  # Compare with fw4's generated intent rather than assuming1 always means ft.
  if printf '%s\n' "$expected" | grep -Eq '^[[:space:]]*flowtable[[:space:]]+ft[[:space:]]*\{'; then
   nft list flowtable inet fw4 ft >/dev/null 2>&1
  else ! nft list flowtable inet fw4 ft >/dev/null 2>&1; fi
 else
  # Do not call this from a firewall include: fw3 print takes fw3's own lock.
  # fw3 uses the first defaults section and accepts exactly these true values.
  # Its IPv6-only print returns1 without output when IPv6 rules are disabled.
  # Read that explicit intent instead of treating an ambiguous print failure
  # as proof. A missing/unreadable option still requires a successful print.
  ipv6_disabled="$(uci -q get 'firewall.@defaults[0].disable_ipv6')" || ipv6_disabled=
  for family in 4 6; do
   expected=
   case "$family:$ipv6_disabled" in
    6:1|6:true|6:yes) ;;
    *) expected="$(fw3 "-$family" print 2>/dev/null)" || return 1;;
   esac
   live="$(internet_fw3_live "$family")" || return 1
   if internet_flowoffload_present "$expected"; then
    internet_flowoffload_present "$live" || return 1
    if internet_flowoffload_hardware "$expected"; then internet_flowoffload_hardware "$live" || return 1
    elif internet_flowoffload_hardware "$live"; then return 1; fi
   elif internet_flowoffload_present "$live"; then return 1; fi
  done
  return 0
 fi
}
internet_offload_restore() {
 local section original fingerprint option value current changed=0 backup="$INTERNET_BACKUP_DIR/offload-backup" reload="$INTERNET_BACKUP_DIR/offload-reload-required"
 [ -f "$backup" ] || return 0
 [ -z "$(uci -q changes firewall)" ] || return 1
 internet_offload_validate "$backup" || return 1
 : > "$INTERNET_STATE/internet-uci-edits" || return 1
 while read -r original fingerprint option value; do
  section="$(internet_defaults_resolve "$original" "$fingerprint")" || { internet_uci_rollback; return 1; }
  case "$option" in flow_offloading|flow_offloading_hw) ;; *) return 1;; esac
  case "$value" in -|0|1|true|false|yes|no|on|off) ;; *) return 1;; esac
  current="$(uci -q get "firewall.$section.$option")" || current=-
  # Restore only options still at the value set by this plugin. Respect edits
  # made after it was stopped or an option/section removed by the administrator.
  [ "$current" = 0 ] || continue
  if [ "$value" = 0 ]; then continue; fi
  printf '%s.%s\n' "$section" "$option" >> "$INTERNET_STATE/internet-uci-edits"
  if [ "$value" = - ]; then uci -q delete "firewall.$section.$option" || { internet_uci_rollback; return 1; }
  elif [ "$value" != 0 ]; then uci -q set "firewall.$section.$option=$value" || { internet_uci_rollback; return 1; }
  else continue; fi
  changed=1
 done < "$backup"
 if [ "$changed" = 1 ]; then
  printf 'restore\n' > "$reload" || { internet_uci_rollback; return 1; }
  uci -q commit firewall || { internet_uci_rollback; return 1; }
 fi
 rm -f "$INTERNET_STATE/internet-uci-edits"
 if [ -f "$reload" ]; then
  internet_native_reload || return 1
  internet_offload_restored || return 1
  rm -f "$reload"
 fi
 rm -f "$backup"
 rmdir "$INTERNET_BACKUP_DIR" 2>/dev/null || :
}
internet_offload_validate() {
 local section original fingerprint option value extra found=0
 while read -r original fingerprint option value extra; do
  internet_valid_network "$original" && [ -z "$extra" ] || return 1
  case "$fingerprint" in ''|*[!0-9:]*) return 1;; esac
  section="$(internet_defaults_resolve "$original" "$fingerprint")" || return 1
  case "$option" in flow_offloading|flow_offloading_hw) ;; *) return 1;; esac
  case "$value" in -|0|1|true|false|yes|no|on|off) ;; *) return 1;; esac
  if [ "${2:-}" = strict ]; then
   [ "$(uci -q get "firewall.$section")" = defaults ] || return 1
   internet_offload_option "$section" "$option" >/dev/null || return 1
  fi
  found=1
 done < "$1"
 [ "$found" = 1 ]
}
internet_cleanup_locked() {
 local failed=0
 # Stop/removal is authoritative even while saved settings still request a
 # block. The restoration reload must not cause the callback to recreate it.
 rm -f "$INTERNET_STATE/internet-policy-active"
 rm -f "$INTERNET_STATE/firewall/policy.nft"
 internet_delete_owned_tables || failed=1
 [ "$failed" = 0 ] || { internet_status error cleanup_failed; return 1; }
 if [ -f "$INTERNET_BACKUP_DIR/vendor-acceleration-backup" ] && ! internet_vendor_load; then
  internet_status error adapter_missing; return 1
 fi
 internet_offload_restore || { internet_status error offload_restore_failed; return 1; }
 internet_native_module_restore || { internet_status error native_module_restore_failed; return 1; }
 if internet_vendor_load; then
  INTERNET_VENDOR_ERROR=
  internet_vendor_restore || { internet_vendor_status_error vendor_restore_failed; return 1; }
 fi
 rm -f "$INTERNET_STATE/internet-policy-applied"
 internet_interfaces=
 internet_status inactive
}
internet_apply_rules() {
 local next="$INTERNET_STATE/internet-policy-next" old_family
 internet_backend_detect || { internet_status error firewall_backend_required; return 1; }
 internet_collect || { internet_status error network_status_failed; return 1; }
 if [ -z "$internet_interfaces" ]; then
  internet_delete_owned_tables || { internet_status error cleanup_failed; return 1; }
  rm -f "$INTERNET_STATE/internet-policy-applied"
  return 0
 fi
 internet_render
 if ! cmp -s "$next" "$INTERNET_STATE/internet-policy-applied" || ! internet_table_exists; then
  nft -c -f "$next" >/dev/null 2>&1 || { internet_status error rule_validation_failed; return 1; }
  nft -f "$next" >/dev/null 2>&1 || { internet_status error rule_apply_failed; return 1; }
  cp "$next" "$INTERNET_STATE/internet-policy-applied" || { internet_status error cache_write_failed; return 1; }
 fi
 # Replace an older inet ingress policy only after the new netdev policy is
 # installed, so a failed transaction retains the last block during upgrade.
 old_family=inet
 if nft list table "$old_family" "$INTERNET_TABLE" >/dev/null 2>&1; then
  nft delete table "$old_family" "$INTERNET_TABLE" >/dev/null 2>&1 || { internet_status error cleanup_failed; return 1; }
 fi
}
internet_cleanup() {
 local held="${INTERNET_LOCKED:-0}" result
 internet_lock || { internet_status error policy_busy; return 1; }
 internet_cleanup_locked; result=$?
 [ "$held" = 1 ] || internet_unlock
 return "$result"
}
internet_policy_locked() {
 mkdir -p "$INTERNET_STATE"
 # A fresh simulation never changes nftables or firewall UCI. Entering it from
 # a live policy first removes this plugin's rules and restores owned settings.
 if [ "$dry" = 1 ]; then
  if [ -f "$INTERNET_STATE/internet-policy-applied" ] || [ -f "$INTERNET_BACKUP_DIR/offload-backup" ] || [ -f "$INTERNET_BACKUP_DIR/native-flowoffload-backup" ] || [ -f "$INTERNET_BACKUP_DIR/vendor-acceleration-backup" ] || internet_owned_table_exists; then
   internet_cleanup || return 1
  fi
  printf '%s\n' "${guest_internet:-1}|${networks:-}|${white:-}" > "$INTERNET_STATE/internet-test-next"
  if ! cmp -s "$INTERNET_STATE/internet-test-next" "$INTERNET_STATE/internet-test-policy"; then
   if [ "$guest_internet" = 0 ]; then
    logger -t wifi-client-limiter "TEST: Guests would have local access only on selected WiFi sections: ${networks:-none}. Protected MACs: ${white:-none}"
   else logger -t wifi-client-limiter "TEST: Guest Internet access would remain allowed on selected WiFi sections: ${networks:-none}"; fi
   mv "$INTERNET_STATE/internet-test-next" "$INTERNET_STATE/internet-test-policy"
  fi
  internet_status test; return 0
 fi
 if [ "$enabled" != 1 ] || [ "$guest_internet" != 0 ] || [ -z "$networks" ]; then
  internet_cleanup || return 1
  if [ "$enabled" = 1 ] && [ "$guest_internet" != 0 ]; then internet_status allowed; fi
  return 0
 fi
 internet_vendor_load || { internet_status error adapter_missing; return 1; }
 internet_apply_rules || return 1
 rm -f "$INTERNET_STATE/internet-test-policy" "$INTERNET_STATE/internet-test-next"
 if [ -z "$internet_interfaces" ]; then internet_cleanup; return; fi
 : > "$INTERNET_STATE/internet-policy-active" || { internet_status error cache_write_failed; return 1; }
 internet_offload_disable || { internet_status error offload_disable_failed; return 1; }
 INTERNET_VENDOR_ERROR=
 internet_vendor_disable && internet_vendor_inactive || { internet_vendor_status_error vendor_disable_failed; return 1; }
 internet_status blocked
}
internet_policy() {
 local held="${INTERNET_LOCKED:-0}" result
 internet_lock || { internet_status error policy_busy; return 1; }
 internet_policy_locked; result=$?
 [ "$held" = 1 ] || internet_unlock
 return "$result"
}
internet_add_network() { networks="$networks $1"; }
internet_add_white() { white="$white $(printf '%s\n' "$1" | tr 'A-F' 'a-f')"; }
internet_firewall_refresh() {
 local result=0
 [ -f "$INTERNET_STATE/internet-policy-active" ] || return 0
 # Never wait for the worker inside the native firewall include callback.
 # its offload reload invokes this callback while its policy lock is held.
 mkdir -p "$INTERNET_STATE"
 mkdir "$INTERNET_STATE/policy-lock" 2>/dev/null || return 0
 printf '%s\n' "$$" > "$INTERNET_STATE/policy-lock/pid"
 INTERNET_LOCKED=1
 config_load wifi-client-limiter
 config_get_bool enabled main enabled 0
 config_get_bool dry main dry_run 1
 config_get_bool guest_internet main guest_internet 1
 networks=; white=
 config_list_foreach main network internet_add_network
 config_list_foreach main whitelist internet_add_white
 if [ "$enabled" = 1 ] && [ "$dry" = 0 ] && [ "$guest_internet" = 0 ] && [ -n "$networks" ]; then
  internet_apply_rules || result=1
  if [ "$result" = 0 ]; then
   if [ -z "$internet_interfaces" ]; then internet_status inactive
   elif ! internet_offload_inactive; then internet_status error offload_disable_failed
   elif ! internet_vendor_load; then internet_status error adapter_missing
   else
    INTERNET_VENDOR_ERROR=
    if internet_vendor_inactive; then internet_status blocked
    else internet_vendor_status_error vendor_disable_failed; fi
   fi
  fi
 fi
 internet_unlock
 return "$result"
}

if [ "${0##*/}" = internet-policy.sh ]; then
 umask 077
 case "${1:-}" in
  cleanup|--cleanup) internet_cleanup;;
  firewall-reload)
   . /lib/functions.sh
   . /usr/share/libubox/jshn.sh
   internet_firewall_refresh;;
  *) exit 2;;
 esac
fi

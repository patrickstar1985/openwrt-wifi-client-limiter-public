#!/bin/sh
# SPDX-License-Identifier: MIT
# Keep client discovery independent of the AP controller. The MTK vendor driver
# exports associations through iwinfo, while mac80211 uses hostapd's ubus API.
station_valid_name() { case "$1" in ''|*[!a-zA-Z0-9_.-]*) return 1;; esac; }
station_valid_iface() { station_valid_name "$1" && [ "${#1}" -le 15 ]; }
station_valid_mac() { printf '%s\n' "$1" | grep -Eq '^([0-9a-f]{2}:){5}[0-9a-f]{2}$'; }

# Output: current kernel interface, stable wifi-iface section, radio driver.
# Only live AP interfaces reported by netifd are eligible, including operator
# APs outside the limiter's selected networks for the protected-device picker.
station_ap_records() {
 local data radios radio entries entry section iface mode driver _json_no_warning=1
 data="$(ubus call network.wireless status 2>/dev/null)" || return 1
 json_load "$data" || return 1
 json_get_keys radios
 for radio in $radios; do
  station_valid_name "$radio" || continue
  json_select "$radio" || continue
  driver=
  if json_select config; then json_get_var driver type; json_select ..; fi
  [ -n "$driver" ] || driver="$(uci -q get "wireless.$radio.type" 2>/dev/null)"
  case "$driver" in mtk) ;; *) driver=hostapd;; esac
  if json_select interfaces; then
   json_get_keys entries
   for entry in $entries; do
    json_select "$entry" || continue
    json_get_var section section; json_get_var iface ifname
    mode=
    if json_select config; then json_get_var mode mode; json_select ..; fi
    if [ "$mode" = ap ] && station_valid_name "$section" && station_valid_iface "$iface"; then
     printf '%s %s %s\n' "$iface" "$section" "$driver"
    fi
    json_select ..
   done
   json_select ..
  fi
  json_select ..
 done
}

# Emit lowercase MACs, one per line. Failure is distinct from an empty AP.
station_clients() {
 local iface="$1" driver="${2:-hostapd}" data keys key candidate mac macs
 station_valid_iface "$iface" || return 1
 if [ "$driver" = mtk ]; then
  data="$(iwinfo "$iface" assoclist 2>/dev/null)" || return 1
  # iwinfo's CLI can print a failed association ioctl while still exiting 0.
  # Never mistake that response for an empty AP and discard association ages.
  case "$data" in *'No information available'*|*'not supported'*) return 1;; esac
  macs="$(printf '%s\n' "$data" | awk '
   /^[[:xdigit:]][[:xdigit:]]:/ {print tolower($1)}
  ' | while read -r mac; do station_valid_mac "$mac" && printf '%s\n' "$mac"; done)"
  if [ -z "$macs" ]; then
   case "$data" in *'No station connected'*) ;; *) return 1;; esac
  fi
  [ -z "$macs" ] || printf '%s\n' "$macs" | sort -u
 else
  command -v jsonfilter >/dev/null 2>&1 || return 1
  data="$(ubus call "hostapd.$iface" get_clients 2>/dev/null)" || return 1
  json_load "$data" && json_select clients || return 1
  json_get_keys keys
  for key in $keys; do
   mac="$(printf '%s' "$key" | tr 'A-F' 'a-f')"
   # jshn exposes sanitized object keys (aa_bb_...) for MAC-address keys.
   # Its C decoder discards original names before shell metadata is built.
   # Validate a fixed MAC candidate, then prove its exact original colon key
   # exists in the raw response; other separators also sanitize to '_'.
   if ! station_valid_mac "$mac"; then
    printf '%s\n' "$mac" | grep -Eq '^([0-9a-f]{2}_){5}[0-9a-f]{2}$' || continue
    candidate="$(printf '%s' "$key" | tr '_' ':')"
    jsonfilter -s "$data" -e "@.clients['$candidate']" >/dev/null 2>&1 || continue
    mac="$(printf '%s' "$candidate" | tr 'A-F' 'a-f')"
   fi
   station_valid_mac "$mac" && printf '%s\n' "$mac"
  done
 fi
 return 0
}

# This read-only command is the only RPC entry point. It accepts no device or
# shell command supplied by a web user, and never invokes driver mutations.
station_clients_json() {
 local records iface section driver mac macs clients= comma= unavailable=false
 records="$(station_ap_records)" || { printf '%s\n' '{"clients":[],"unavailable":true}'; return 0; }
 while read -r iface section driver; do
  [ -n "$iface" ] || continue
  if macs="$(station_clients "$iface" "$driver")"; then
   for mac in $macs; do
    clients="$clients$comma{\"mac\":\"$mac\",\"ifname\":\"$iface\",\"section\":\"$section\"}"
    comma=,
   done
  else unavailable=true; fi
 done <<EOF
$records
EOF
 printf '{"clients":[%s],"unavailable":%s}\n' "$clients" "$unavailable"
}

station_mtk_log() { logger -t wifi-client-limiter "$*"; }
# Use a subshell so jshn's state cannot corrupt a pending netifd/procd decode.
station_service_status() (
 local state="$1" error="${2:-}" detail="${3:-}" pid=0 directory="${STATE:-/tmp/wifi-client-limiter}"
 case "$state" in running|error) pid="$$";; starting|stopped) ;; *) return 1;; esac
 [ "${STATION_PREFLIGHT_ONLY:-0}" != 1 ] || pid=0
 umask 077; mkdir -p "$directory" || return 1
 json_init
 json_add_string service_state "$state"
 json_add_string error "$error"
 json_add_string detail "$detail"
 json_add_int pid "$pid"
 json_dump > "$directory/service-status.json.tmp-$$" &&
  mv "$directory/service-status.json.tmp-$$" "$directory/service-status.json"
)
station_preflight_error() {
 STATION_PREFLIGHT_ERROR="$1"; STATION_PREFLIGHT_DETAIL="$2"
 station_mtk_log "$2"
 return 1
}
station_mtk_uint() { case "$1" in ''|*[!0-9]*) return 1;; esac; [ "$1" -le 2147483647 ] 2>/dev/null; }
station_mtk_decimal() { local value="$1"; while [ "${value#0}" != "$value" ]; do value="${value#0}"; done; printf '%s\n' "${value:-0}"; }
station_mtk_clock() { cut -d. -f1 /proc/uptime; }

# This private blacklist has no TTL or robust read API in the stock driver.
# Restrict it to the researched, byte-identical GL drivers and a stock profile
# where this plugin is its sole consumer. Ordinary AccessPolicy/ACL lists are
# separate and are never changed. Unknown vendor builds fail before mutation.
station_mtk_known_driver() {
 local kernel module hash
 kernel="$(uname -r)"; station_valid_name "$kernel" || return 1
 module="/lib/modules/$kernel/mt_wifi.ko"
 [ -r "$module" ] || { station_preflight_error mtk_driver_missing "MTK WiFi module cannot be read: $module"; return 1; }
 hash="$(sha256sum "$module" 2>/dev/null)"; hash="${hash%% *}"
 case "$hash" in
  # Original GL package feed and GL-X3000 4.8.3 release 3, respectively.
  ed66a1502e819e4cc8424e50ce9c982905dabd1053bdfa7c47a7ec571652cb77|\
  0b23162074e8ce928d86ae8438297de173b71f7a1f90398f0ad7f63b88c5a7e3) printf '%s\n' "$hash";;
  *) station_preflight_error mtk_driver_unsupported "Unsupported MTK WiFi driver for reconnect protection: $hash"; return 1;;
 esac
}
station_mtk_stock_profile() {
 local path process
 [ -x /usr/sbin/mtk-wifi-configurator ] || { station_preflight_error mtk_profile_unsupported 'MTK reconnect protection requires the supported stock GL WiFi profile'; return 1; }
 for path in /proc/[0-9]*/comm; do
  [ -r "$path" ] || continue
  read -r process < "$path"
  case "$process" in mapd|1905d|easymeshd|p1905_manager|map_controller|map_agent)
   station_preflight_error mtk_steering_active 'MTK reconnect protection cannot share the private driver blacklist with MultiAP steering'; return 1;;
  esac
 done
 station_mtk_policy_safe
}
station_mtk_access_policy() {
 /usr/share/wifi-client-limiter/mtk-acl-query-aarch64 "$1"
}
station_mtk_policy_safe() {
 local records selected iface section driver policy
 # Both audited AArch64 drivers skip their private blacklist during a WPS
 # session if the ordinary MAC ACL uses allowlist policy1. Query the actual
 # runtime policy; UCI/DAT files cannot prove a later ioctl did not change it.
 [ "$(uname -m)" = aarch64 ] && [ -x /usr/share/wifi-client-limiter/mtk-acl-query-aarch64 ] || {
  station_preflight_error mtk_tools_missing 'MTK runtime ACL query helper is unavailable'; return 1;
 }
 records="$(station_ap_records)" || {
  station_preflight_error mtk_profile_unsupported 'Cannot resolve MTK access points for the runtime ACL check'; return 1;
 }
 selected="$(uci -q get wifi-client-limiter.main.network 2>/dev/null)" || {
  station_preflight_error mtk_profile_unsupported 'Cannot read the selected WiFi networks for the MTK ACL check'; return 1;
 }
 while read -r iface section driver; do
  [ "$driver" = mtk ] || continue
  case " $selected " in *" $section "*) ;; *) continue;; esac
  policy="$(station_mtk_access_policy "$iface")" || {
   station_preflight_error mtk_profile_unsupported "Cannot read the current MTK MAC ACL policy on $iface"; return 1;
  }
  case "$policy" in
   0|2) ;;
   1) station_preflight_error mtk_profile_unsupported "MTK MAC allowlist on $iface can bypass reconnect protection during WPS; protected limiter MACs are a separate setting"; return 1;;
   *) station_preflight_error mtk_profile_unsupported "Unrecognized MTK MAC ACL policy on $iface"; return 1;;
  esac
 done <<EOF
$records
EOF
 return 0
}
station_selected_mtk() {
 local selected section device type
 selected="$(uci -q get wifi-client-limiter.main.network 2>/dev/null)"
 for section in $selected; do
  station_valid_name "$section" || return 2
  [ "$(uci -q get "wireless.$section.mode" 2>/dev/null)" = ap ] || continue
  device="$(uci -q get "wireless.$section.device" 2>/dev/null)"
  station_valid_name "$device" || return 2
  type="$(uci -q get "wireless.$device.type" 2>/dev/null)"
  [ "$type" != mtk ] || return 0
 done
 return 1
}
station_preflight() {
 local result
 STATION_PREFLIGHT_ERROR=; STATION_PREFLIGHT_DETAIL=
 station_selected_mtk; result=$?
 [ "$result" != 1 ] || return 0
 if [ "$result" != 0 ]; then
  station_preflight_error network_selection_invalid 'Selected WiFi sections cannot be resolved safely' || :
 elif ! command -v iwinfo >/dev/null 2>&1 || ! command -v iwpriv >/dev/null 2>&1 || ! command -v dmesg >/dev/null 2>&1; then
  station_preflight_error mtk_tools_missing 'MTK WiFi support requires iwinfo, wireless-tools and kernel log access' || :
 elif ! dmesg >/dev/null 2>&1; then
  station_preflight_error kernel_log_unavailable 'MTK reconnect protection requires read access to the kernel log' || :
 elif station_mtk_known_driver >/dev/null && station_mtk_stock_profile; then return 0
 fi
 if [ "${1:-}" = worker ]; then
  station_service_status error "${STATION_PREFLIGHT_ERROR:-station_preflight_failed}" "${STATION_PREFLIGHT_DETAIL:-WiFi driver preflight failed; check the system log}" || :
 else
  STATION_PREFLIGHT_ONLY=1 station_service_status error "${STATION_PREFLIGHT_ERROR:-station_preflight_failed}" "${STATION_PREFLIGHT_DETAIL:-WiFi driver preflight failed; check the system log}" || :
 fi
 return 1
}
station_mtk_lock() {
 local owner tries=0
 STATE="${STATE:-/tmp/wifi-client-limiter}"
 umask 077; mkdir -p "$STATE" || return 1
 while ! mkdir "$STATE/station-policy-lock" 2>/dev/null; do
  owner=
  [ ! -r "$STATE/station-policy-lock/pid" ] || read -r owner < "$STATE/station-policy-lock/pid"
  [ "$owner" != "$$" ] || return 0
  if station_mtk_uint "$owner" && ! kill -0 "$owner" 2>/dev/null; then
   rm -f "$STATE/station-policy-lock/pid"; rmdir "$STATE/station-policy-lock" 2>/dev/null || :
  else
   tries=$((tries+1))
   if [ "$tries" -gt 5 ]; then
    if ! station_mtk_uint "$owner"; then
     rm -f "$STATE/station-policy-lock/pid"; rmdir "$STATE/station-policy-lock" 2>/dev/null || return 1
     continue
    fi
    station_mtk_log 'MTK reconnect policy is busy'; return 1
   fi
   sleep 1
  fi
 done
 if ! printf '%s\n' "$$" > "$STATE/station-policy-lock/pid"; then
  rm -f "$STATE/station-policy-lock/pid"; rmdir "$STATE/station-policy-lock" 2>/dev/null
  return 1
 fi
}
station_mtk_unlock() {
 local owner
 [ -r "$STATE/station-policy-lock/pid" ] || return 0
 read -r owner < "$STATE/station-policy-lock/pid"
 [ "$owner" != "$$" ] || { rm -f "$STATE/station-policy-lock/pid"; rmdir "$STATE/station-policy-lock" 2>/dev/null; }
 return 0
}
station_mtk_associated() {
 local macs
 macs="$(station_clients "$1" mtk)" || return 2
 printf '%s\n' "$macs" | grep -Fxq "$2"
}
station_mtk_record() {
 local iface="$1" mac="$2" deadline="$3" hash="$4" previous
 [ -f "$STATE/station-bans" ] || { : > "$STATE/station-bans" || return 1; }
 previous="$(awk -v i="$iface" -v m="$mac" '$1==i && $2==m {print $3;exit}' "$STATE/station-bans")"
 if station_mtk_uint "$previous" && [ "$previous" -gt "$deadline" ]; then deadline="$previous"; fi
 awk -v i="$iface" -v m="$mac" '!($1==i && $2==m)' "$STATE/station-bans" > "$STATE/station-bans-new-$$" || { rm -f "$STATE/station-bans-new-$$"; return 1; }
 printf '%s %s %s %s\n' "$iface" "$mac" "$deadline" "$hash" >> "$STATE/station-bans-new-$$" || { rm -f "$STATE/station-bans-new-$$"; return 1; }
 mv "$STATE/station-bans-new-$$" "$STATE/station-bans"
}
station_mtk_oom_stamp() {
 local data
 data="$(dmesg 2>/dev/null)" || return 1
 # The exact driver's allocator can return after an allocation failure while
 # its caller still reports success. Its assertion is unconditional. Read the
 # kernel log without changing global debug settings, and compare fresh stamps.
 printf '%s\n' "$data" | grep -F 'assert pBlEntry != NULLfailed' | tail -n 1
}
station_mtk_issue_add() {
 local iface="$1" mac="$2" associated="$3" before after result error
 before="$(station_mtk_oom_stamp)" || { station_mtk_log 'Cannot inspect MTK blacklist allocation failures'; return 1; }
 error="$(LC_ALL=C iwpriv "$iface" set "BlAdd=$mac" 2>&1)"; result=$?
 after="$(station_mtk_oom_stamp)" || { station_mtk_log 'Cannot verify MTK blacklist allocation'; return 1; }
 [ "$before" = "$after" ] || { station_mtk_log "MTK blacklist allocation failed: $mac on $iface"; return 1; }
 if [ "$result" = 0 ]; then return 0; fi
 if [ "$associated" = 0 ] && [ "$result" = 255 ]; then
  case "$error" in *'Invalid argument'*) return 0;; esac
 fi
 station_mtk_log "MTK private blacklist request failed: $mac on $iface (exit $result)"
 return 1
}
station_mtk_add_locked() {
 local iface="$1" mac="$2" seconds="$3" hash="$4" associated="$5" proof
 if [ "$associated" = 0 ]; then
  proof=
  [ ! -r "$STATE/station-driver-verified" ] || read -r proof < "$STATE/station-driver-verified"
  [ "$proof" = "$hash" ] || { station_mtk_log 'MTK cross-AP ban requires a verified associated-client disconnect first'; return 1; }
 fi
 # Journal before issuing an operation that can add an entry and then return
 # EINVAL for an absent station. Failed or interrupted attempts remain owned
 # and are included in expiry and stop cleanup.
 station_mtk_record "$iface" "$mac" "$(( $(station_mtk_clock) + seconds ))" "$hash" || return 1
 station_mtk_issue_add "$iface" "$mac" "$associated"
}
station_mtk_confirm_gone() {
 local iface="$1" mac="$2" result tries=0
 while :; do
  station_mtk_associated "$iface" "$mac"; result=$?
  [ "$result" != 1 ] || return 0
  [ "$result" != 2 ] || return 1
  tries=$((tries+1)); [ "$tries" -lt 3 ] || return 1
  sleep 1
 done
}
station_mtk_kick() {
 local iface="$1" mac="$2" seconds="$3" hash result
 station_valid_iface "$iface" && station_valid_mac "$mac" && station_mtk_uint "$seconds" && [ "$seconds" -le 1800 ] || return 1
 seconds="$(station_mtk_decimal "$seconds")"
 if [ "$seconds" = 0 ]; then
  iwpriv "$iface" set "DisConnectSta=$mac" >/dev/null 2>&1 && station_mtk_confirm_gone "$iface" "$mac"
  return
 fi
 hash="$(station_mtk_known_driver)" && station_mtk_stock_profile || return 1
 station_mtk_lock || return 1
 station_mtk_associated "$iface" "$mac"; result=$?
 if [ "$result" = 0 ] && station_mtk_add_locked "$iface" "$mac" "$seconds" "$hash" 1 && station_mtk_confirm_gone "$iface" "$mac"; then
  if ! printf '%s\n' "$hash" > "$STATE/station-driver-verified"; then
   station_mtk_unlock; return 1
  fi
  station_mtk_unlock; return 0
 fi
 station_mtk_unlock; return 1
}
station_mtk_ban() {
 local iface="$1" mac="$2" seconds="$3" hash result associated=0
 station_valid_iface "$iface" && station_valid_mac "$mac" && station_mtk_uint "$seconds" && [ "$seconds" -gt 0 ] && [ "$seconds" -le 1800 ] || return 1
 seconds="$(station_mtk_decimal "$seconds")"
 hash="$(station_mtk_known_driver)" && station_mtk_stock_profile || return 1
 station_mtk_lock || return 1
 station_mtk_associated "$iface" "$mac"; result=$?
 if [ "$result" = 2 ]; then station_mtk_unlock; return 1; fi
 [ "$result" != 0 ] || associated=1
 if station_mtk_add_locked "$iface" "$mac" "$seconds" "$hash" "$associated"; then
  if [ "$associated" = 0 ] || station_mtk_confirm_gone "$iface" "$mac"; then
   station_mtk_unlock; return 0
  fi
 fi
 station_mtk_unlock; return 1
}
station_mtk_prune() {
 local clear="${1:-0}" now iface mac deadline hash rest known failed=0 io_failed=0 keep remove proof
 STATE="${STATE:-/tmp/wifi-client-limiter}"
 [ -s "$STATE/station-bans" ] || return 0
 known="$(station_mtk_known_driver)" || return 1
 station_mtk_lock || return 1
 now="$(station_mtk_clock)"
 : > "$STATE/station-bans-new-$$" || { station_mtk_unlock; return 1; }
 while read -r iface mac deadline hash rest; do
  keep=1; remove=0
  if station_valid_iface "$iface" && station_valid_mac "$mac" && station_mtk_uint "$deadline" && [ "$hash" = "$known" ] && [ -z "$rest" ]; then
   deadline="$(station_mtk_decimal "$deadline")"
   [ "$clear" != 1 ] && [ "$deadline" -gt "$now" ] || remove=1
   if [ "$clear" = configured ]; then
    case " ${white:-} " in *" $mac "*) remove=1;; esac
    case " ${interfaces:-} " in *" $iface "*) ;; *) remove=1;; esac
   fi
   if [ "$remove" = 1 ]; then
    if iwpriv "$iface" set "BlDel=$mac" >/dev/null 2>&1; then keep=0
    else station_mtk_log "MTK reconnect cleanup failed: $mac on $iface"; failed=1; fi
   elif [ "$clear" = configured ]; then
    # A wireless reload can clear the driver's RAM list. Reapply owned active
    # leases without restarting their fixed deadlines, before scanning clients.
    proof=
    [ ! -r "$STATE/station-driver-verified" ] || read -r proof < "$STATE/station-driver-verified"
    if [ "$proof" = "$known" ] && station_mtk_stock_profile && station_mtk_issue_add "$iface" "$mac" 0 && station_mtk_confirm_gone "$iface" "$mac"; then :
    else station_mtk_log "MTK reconnect lease could not be refreshed: $mac on $iface"; failed=1; fi
   fi
  else station_mtk_log 'Invalid MTK reconnect journal; cleanup requires attention'; failed=1; fi
  if [ "$keep" != 0 ] && ! printf '%s %s %s %s%s\n' "$iface" "$mac" "$deadline" "$hash" "${rest:+ $rest}" >> "$STATE/station-bans-new-$$"; then io_failed=1; break; fi
 done < "$STATE/station-bans"
 if [ "$io_failed" = 1 ]; then
  rm -f "$STATE/station-bans-new-$$"; failed=1
  station_mtk_log 'MTK reconnect journal could not be updated; retaining original ownership records'
 elif ! mv "$STATE/station-bans-new-$$" "$STATE/station-bans"; then failed=1; fi
 station_mtk_unlock
 [ "$failed" = 0 ]
}
station_mtk_cleanup() {
 station_mtk_prune 1 || return 1
 rm -f "${STATE:-/tmp/wifi-client-limiter}/station-driver-verified"
}
station_mtk_sleep() {
 local wait="$1" iface mac deadline hash rest seconds now
 station_mtk_uint "$wait" || wait=10
 [ -s "$STATE/station-bans" ] || { printf '%s\n' "$wait"; return; }
 now="$(station_mtk_clock)"
 while read -r iface mac deadline hash rest; do
  station_mtk_uint "$deadline" || continue
  deadline="$(station_mtk_decimal "$deadline")"
  seconds=$((deadline-now)); [ "$seconds" -ge 1 ] || seconds=1
  [ "$seconds" -ge "$wait" ] || wait="$seconds"
 done < "$STATE/station-bans"
 printf '%s\n' "$wait"
}

if [ "${0##*/}" = station-adapter.sh ]; then
 [ "$#" = 1 ] || exit 1
 . /usr/share/libubox/jshn.sh
 case "$1" in
  clients) station_clients_json;;
  preflight) station_preflight;;
  cleanup) station_mtk_cleanup;;
  *) exit 1;;
 esac
fi

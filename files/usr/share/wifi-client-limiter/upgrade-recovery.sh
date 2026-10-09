#!/bin/sh
# SPDX-License-Identifier: MIT
# External preparation for an upgrade. opkg invokes the OLD prerm before the
# new preinst, so recovery cannot be deferred to the new package payload.
UPGRADE_POLICY_FILE="${UPGRADE_POLICY_FILE:-/usr/share/wifi-client-limiter/internet-policy.sh}"
UPGRADE_SERVICE_FILE="${UPGRADE_SERVICE_FILE:-/etc/init.d/wifi-client-limiter}"
UPGRADE_STATION_FILE="${UPGRADE_STATION_FILE:-/usr/share/wifi-client-limiter/station-adapter.sh}"
UPGRADE_FIREWALL_FILE="${UPGRADE_FIREWALL_FILE:-/etc/config/firewall}"
UPGRADE_PROC_ROOT="${UPGRADE_PROC_ROOT:-/proc}"

upgrade_recovery_error() { printf 'Upgrade stopped: %s\n' "$1" >&2; return 1; }
upgrade_recovery_begin() {
 [ -z "${UPGRADE_COMPAT_DIR:-}" ] || return 0
 [ -r "${UPGRADE_CHECKSUM_FILE:-}" ] || { upgrade_recovery_error 'checksum helper missing'; return 1; }
 WCL_RECOVERY_SYSTEM_PATH="$PATH"; export WCL_RECOVERY_SYSTEM_PATH
 UPGRADE_COMPAT_DIR="$(mktemp -d /tmp/wifi-limiter-upgrade.XXXXXX)" || return 1
 chmod 700 "$UPGRADE_COMPAT_DIR" || return 1
 cp "$UPGRADE_CHECKSUM_FILE" "$UPGRADE_COMPAT_DIR/posix-cksum.sh" || return 1
 cat > "$UPGRADE_COMPAT_DIR/cksum" <<'COMPAT'
#!/bin/sh
[ "$#" = 0 ] || exit 1
PATH="${WCL_RECOVERY_SYSTEM_PATH:?}"; export PATH
. "${0%/*}/posix-cksum.sh" || exit 1
value="$(internet_posix_cksum)" || exit 1
printf '%s %s\n' "${value%:*}" "${value#*:}"
COMPAT
 chmod 700 "$UPGRADE_COMPAT_DIR/cksum" || return 1
 PATH="$UPGRADE_COMPAT_DIR:$PATH"; export PATH
 [ "$(printf '' | cksum)" = '4294967295 0' ] || { upgrade_recovery_error 'checksum compatibility self-test failed'; return 1; }
}
upgrade_recovery_finish() {
 if [ "${UPGRADE_LOCK_HELD:-0}" = 1 ]; then
  internet_unlock; UPGRADE_LOCK_HELD=0
 fi
 if [ -n "${UPGRADE_CANDIDATE:-}" ]; then rm -f "$UPGRADE_CANDIDATE"; fi
 if [ -n "${UPGRADE_COMPAT_DIR:-}" ]; then
  PATH="$WCL_RECOVERY_SYSTEM_PATH"; export PATH
  rm -rf "$UPGRADE_COMPAT_DIR"; UPGRADE_COMPAT_DIR=
 fi
}
upgrade_recovery_fingerprint() {
 local original="$1" snapshot normalized sorted checksum
 snapshot="$(uci -q -X show "firewall.$original")" || return 1
 normalized="$(printf '%s\n' "$snapshot" | awk -v prefix="firewall.$original" '
  index($0,prefix)==1 {line=substr($0,length(prefix)+1)
   if(line !~ /^\.(flow_offloading|flow_offloading_hw)=/) print line}')" || return 1
 [ -n "$normalized" ] || return 1
 sorted="$(printf '%s\n' "$normalized" | sort)" || return 1
 [ -n "$sorted" ] || return 1
 checksum="$(printf '%s\n' "$sorted" | cksum)" || return 1
 . "$UPGRADE_COMPAT_DIR/posix-cksum.sh" || return 1
 checksum="$(internet_checksum_record "$checksum" fields)" || return 1
 printf '%s\n' "$checksum"
}
upgrade_recovery_archive() {
 local source="$1" destination="$2"
 [ -f "$source" ] && [ ! -L "$source" ] || return 1
 if [ -e "$destination" ] || [ -L "$destination" ]; then
  [ -f "$destination" ] && [ ! -L "$destination" ] && cmp -s "$source" "$destination"
 else
  cp -p "$source" "$destination" && chmod 600 "$destination"
 fi
}
upgrade_recovery_repair_locked() {
 local backup="$INTERNET_BACKUP_DIR/offload-backup" records original option value extra
 local pending sections fingerprint policy_hash archive
 [ -f "$backup" ] || return 0
 [ ! -L "$backup" ] || { upgrade_recovery_error 'backup is a symbolic link'; return 1; }
 # Valid legacy journals are left unchanged and cleaned by their own helper.
 if internet_offload_validate "$backup" strict; then return 0; fi
 # Only the observed 0.5.4 failure is recoverable here. That exact helper
 # rejects these rows before writing either flow-offloading UCI option.
 policy_hash="$(sha256sum "$UPGRADE_POLICY_FILE")" || return 1
 [ "${policy_hash%% *}" = c57b5d7e59245517bf593159c496441b64b3ca926682968e3904e2293d1d0674 ] || {
  upgrade_recovery_error 'unrecognized installed helper; backup preserved'; return 1;
 }
 records="$(awk '
  NF {if(NF!=3 || $1!~/^[a-zA-Z0-9_]+$/ || length($1)>64 || $3!="-" ||
         ($2!="flow_offloading" && $2!="flow_offloading_hw")){bad=1;exit 1}
      if(count && original!=$1){bad=1;exit 1};original=$1
      if(seen[$2]++){bad=1;exit 1};count++}
  END {if(bad || count!=2 || !seen["flow_offloading"] || !seen["flow_offloading_hw"])exit 1
       print original}' "$backup")" || { upgrade_recovery_error 'unrecognized backup records; backup preserved'; return 1; }
 original="$records"
 [ "$(cat "$INTERNET_BACKUP_DIR/offload-reload-required" 2>/dev/null)" = disable ] || {
  upgrade_recovery_error 'unexpected reload marker; backup preserved'; return 1;
 }
 [ ! -e "$INTERNET_BACKUP_DIR/vendor-acceleration-backup" ] &&
 [ ! -e "$INTERNET_STATE/internet-uci-edits" ] || {
  upgrade_recovery_error 'additional recovery state exists; backup preserved'; return 1;
 }
 case "$(cat "$INTERNET_BACKUP_DIR/native-flowoffload-backup" 2>/dev/null)" in
  0|1) ;; *) upgrade_recovery_error 'invalid native module backup'; return 1;;
 esac
 pending="$(uci -q changes firewall)" || return 1
 [ -z "$pending" ] || { upgrade_recovery_error 'save or discard pending firewall changes first'; return 1; }
 sections="$(uci -q -X show firewall)" || return 1
 sections="$(printf '%s\n' "$sections" | sed -n 's/^firewall\.\([a-zA-Z0-9_]*\)=defaults$/\1/p')" || return 1
 [ "$sections" = "$original" ] && [ "$(uci -q get "firewall.$original")" = defaults ] || {
  upgrade_recovery_error 'original defaults section changed; backup preserved'; return 1;
 }
 for option in flow_offloading flow_offloading_hw; do
  if value="$(uci -q get "firewall.$original.$option" 2>/dev/null)"; then
   upgrade_recovery_error 'original offload settings changed; backup preserved'; return 1;
  fi
 done
 # Successful section reads and no saved UCI deltas make these the committed
 # values too. Snapshot/fingerprint success is required independently.
 fingerprint="$(upgrade_recovery_fingerprint "$original")" || return 1
 [ "$(internet_defaults_resolve "$original" "$fingerprint")" = "$original" ] || return 1
 archive="$INTERNET_BACKUP_DIR/cksum-recovery-0.5.4"
 [ ! -L "$archive" ] || return 1
 mkdir -p "$archive" && chmod 700 "$archive" || return 1
 upgrade_recovery_archive "$backup" "$archive/offload-backup.original" || return 1
 # Preserve the first configuration privately, never replace it on a retry.
 if [ ! -e "$archive/firewall.original" ]; then
  upgrade_recovery_archive "$UPGRADE_FIREWALL_FILE" "$archive/firewall.original" || return 1
 elif [ ! -f "$archive/firewall.original" ] || [ -L "$archive/firewall.original" ]; then return 1; fi
 UPGRADE_CANDIDATE="$(mktemp "$INTERNET_BACKUP_DIR/.offload-recovery.XXXXXX")" || return 1
 chmod 600 "$UPGRADE_CANDIDATE" || return 1
 printf '%s %s flow_offloading -\n%s %s flow_offloading_hw -\n' \
  "$original" "$fingerprint" "$original" "$fingerprint" > "$UPGRADE_CANDIDATE" || return 1
 internet_offload_validate "$UPGRADE_CANDIDATE" strict || return 1
 cmp -s "$backup" "$archive/offload-backup.original" || return 1
 # UCI edits are not serialized by this policy lock. Recheck immediately
 # before publishing the journal so a new administrator0 is never restored
 # as though it had been forced by the limiter.
 pending="$(uci -q changes firewall)" || return 1
 [ -z "$pending" ] || return 1
 sections="$(uci -q -X show firewall)" || return 1
 sections="$(printf '%s\n' "$sections" | sed -n 's/^firewall\.\([a-zA-Z0-9_]*\)=defaults$/\1/p')" || return 1
 [ "$sections" = "$original" ] || return 1
 for option in flow_offloading flow_offloading_hw; do
  if uci -q get "firewall.$original.$option" >/dev/null 2>&1; then return 1; fi
 done
 [ "$(upgrade_recovery_fingerprint "$original")" = "$fingerprint" ] || return 1
 mv "$UPGRADE_CANDIDATE" "$backup" || return 1
 UPGRADE_CANDIDATE=
 printf 'Recovered missing-checksum journal. Original preserved at %s\n' "$archive/offload-backup.original"
}
upgrade_recovery_repair() {
 local result
 internet_lock || { upgrade_recovery_error 'policy is busy'; return 1; }
 UPGRADE_LOCK_HELD=1
 upgrade_recovery_repair_locked; result=$?
 internet_unlock; UPGRADE_LOCK_HELD=0
 return "$result"
}
upgrade_recovery_worker_present() {
 local path data first second third
 for path in "$UPGRADE_PROC_ROOT"/[0-9]*/cmdline; do
  if [ ! -r "$path" ]; then
   [ ! -d "${path%/cmdline}" ] || return 2
   continue
  fi
  data="$(tr '\000' '\n' < "$path" 2>/dev/null)" || { [ ! -e "$path" ] && continue; return 2; }
  first="$(printf '%s\n' "$data" | sed -n '1p')"
  second="$(printf '%s\n' "$data" | sed -n '2p')"
  third="$(printf '%s\n' "$data" | sed -n '3p')"
  case "$first:$second:$third" in
   /usr/sbin/wifi-client-limiter:*|/bin/sh:/usr/sbin/wifi-client-limiter:*|/bin/ash:/usr/sbin/wifi-client-limiter:*|sh:/usr/sbin/wifi-client-limiter:*|ash:/usr/sbin/wifi-client-limiter:*|/bin/busybox:sh:/usr/sbin/wifi-client-limiter|/bin/busybox:ash:/usr/sbin/wifi-client-limiter|busybox:sh:/usr/sbin/wifi-client-limiter|busybox:ash:/usr/sbin/wifi-client-limiter) return 0;;
  esac
 done
 return 1
}
upgrade_recovery_wait_stopped() {
 local attempt=0 result service registered kind
 [ -d "$UPGRADE_PROC_ROOT" ] && [ -r "$UPGRADE_PROC_ROOT" ] && [ -x "$UPGRADE_PROC_ROOT" ] || return 1
 while [ "$attempt" -le 40 ]; do
  service="$(ubus -t 5 call service list '{"name":"wifi-client-limiter"}')" || return 1
  kind="$(jsonfilter -s "$service" -t '@' 2>/dev/null)" || return 1
  [ "$kind" = object ] || return 1
  if jsonfilter -s "$service" -e '@["wifi-client-limiter"]' >/dev/null 2>&1; then registered=1
  else registered=0; fi
  if upgrade_recovery_worker_present; then result=0; else result=$?; fi
  [ "$result" != 2 ] || return 1
  if [ "$result" = 1 ] && [ "$registered" = 0 ]; then return 0; fi
  attempt=$((attempt+1)); [ "$attempt" -le 40 ] && sleep 1
 done
 upgrade_recovery_error 'old worker did not stop; upgrade was not started'
 return 1
}
upgrade_recovery_prepare() {
 local stopped=0 path
 upgrade_recovery_begin || return 1
 if [ ! -r "$UPGRADE_POLICY_FILE" ]; then
  if [ -e "$UPGRADE_SERVICE_FILE" ] || [ -L "$UPGRADE_SERVICE_FILE" ]; then
   upgrade_recovery_error 'installed service has no readable cleanup helper'; return 1
  fi
  for path in offload-backup offload-reload-required native-flowoffload-backup vendor-acceleration-backup vendor-acceleration-reload-required; do
   if [ -e "${INTERNET_BACKUP_DIR:-/etc/wifi-client-limiter}/$path" ] || [ -L "${INTERNET_BACKUP_DIR:-/etc/wifi-client-limiter}/$path" ]; then
    upgrade_recovery_error 'recovery state has no readable cleanup helper'; return 1
   fi
  done
  if command -v nft >/dev/null 2>&1 && {
   nft list table netdev wifi_client_limiter >/dev/null 2>&1 || nft list table inet wifi_client_limiter >/dev/null 2>&1;
  }; then
   upgrade_recovery_error 'owned firewall policy has no readable cleanup helper'; return 1
  fi
 fi
 if [ -r "$UPGRADE_POLICY_FILE" ]; then
  . "$UPGRADE_POLICY_FILE" || return 1
  upgrade_recovery_repair || return 1
 fi
 if [ -x "$UPGRADE_SERVICE_FILE" ]; then
  "$UPGRADE_SERVICE_FILE" stop || stopped=1
 fi
 upgrade_recovery_wait_stopped || return 1
 if [ -r "$UPGRADE_POLICY_FILE" ]; then
  # A final old cycle can recreate its broken journal while procd terminates
  # asynchronously. Repair again only after proving the worker has exited.
  upgrade_recovery_repair || return 1
  if [ "$stopped" = 1 ] && [ -x "$UPGRADE_SERVICE_FILE" ]; then
   "$UPGRADE_SERVICE_FILE" stop || return 1
  fi
  "$UPGRADE_POLICY_FILE" cleanup || return 1
 fi
 [ ! -x "$UPGRADE_STATION_FILE" ] || "$UPGRADE_STATION_FILE" cleanup || return 1
 if [ -r "$UPGRADE_POLICY_FILE" ]; then
  internet_owned_table_exists && { upgrade_recovery_error 'owned firewall policy remains'; return 1; }
  for path in offload-backup offload-reload-required native-flowoffload-backup vendor-acceleration-backup vendor-acceleration-reload-required; do
   [ ! -e "$INTERNET_BACKUP_DIR/$path" ] || { upgrade_recovery_error 'cleanup did not finish'; return 1; }
  done
 fi
 printf 'Upgrade preparation complete. Original settings preserved.\n'
}

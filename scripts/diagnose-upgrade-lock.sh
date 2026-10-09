#!/bin/sh
# SPDX-License-Identifier: MIT
# Read-only, focused upgrade-lock report. No runtime helpers are sourced.
# From a computer: ssh root@192.168.8.1 "sh -s" < diagnose-upgrade-lock.sh > upgrade-lock-report.txt
# Fixture paths may be overridden; production defaults inspect only this plugin.
DIAG_PROC_ROOT="${DIAG_PROC_ROOT:-/proc}"
DIAG_STATE="${DIAG_STATE:-/tmp/wifi-client-limiter}"
DIAG_BACKUP="${DIAG_BACKUP:-/etc/wifi-client-limiter}"
DIAG_HELPERS="${DIAG_HELPERS:-/usr/share/wifi-client-limiter}"
DIAG_CONFIG="${DIAG_CONFIG:-/etc/config/wifi-client-limiter}"
DIAG_PACKAGE_STATUS="${DIAG_PACKAGE_STATUS:-/usr/lib/opkg/status}"

diag_number() { case "$1" in ''|*[!0-9]*) return 1;; esac; [ "${#1}" -le 20 ]; }
diag_token() { case "$1" in *[!a-zA-Z0-9_.-]*) return 1;; esac; [ "${#1}" -le 128 ]; }
diag_meta() {
 local label="$1" path="$2" size hash
 if [ -L "$path" ]; then printf '%s_type=symlink_not_read\n' "$label"; return; fi
 if [ -d "$path" ]; then printf '%s_type=directory\n' "$label"; return; fi
 if [ ! -e "$path" ]; then printf '%s_type=absent\n' "$label"; return; fi
 if [ ! -f "$path" ]; then printf '%s_type=nonregular_not_read\n' "$label"; return; fi
 printf '%s_type=regular\n' "$label"
 size="$(wc -c < "$path" 2>/dev/null)"; size="$(printf '%s' "$size" | tr -d ' \t\n')"
 if diag_number "$size"; then printf '%s_bytes=%s\n' "$label" "$size"; else printf '%s_size_status=unavailable\n' "$label"; fi
 hash="$(sha256sum "$path" 2>/dev/null)"; hash="${hash%% *}"
 case "$hash" in ''|*[!0-9a-f]*) printf '%s_sha256_status=unavailable\n' "$label";;
  *) if [ "${#hash}" = 64 ]; then printf '%s_sha256=%s\n' "$label" "$hash"; else printf '%s_sha256_status=unavailable\n' "$label"; fi;; esac
}
diag_package() {
 if [ ! -f "$DIAG_PACKAGE_STATUS" ] || [ -L "$DIAG_PACKAGE_STATUS" ]; then printf 'package_status=unavailable\n'; return; fi
 awk '
 BEGIN {RS="";FS="\n"}
 {found=0;version="";status="";for(i=1;i<=NF;i++) {
   if($i=="Package: luci-app-wifi-client-limiter")found=1
   if($i~/^Version: /)version=substr($i,10)
   if($i~/^Status: /)status=substr($i,9)
  }
  if(found){count++;if(version~/^[0-9][a-zA-Z0-9.+~_-]*$/ && length(version)<80)print "package_version=" version;else print "package_version=invalid"
   if(status~/^[a-zA-Z]+ [a-zA-Z]+ [a-zA-Z]+$/)print "package_status=" status;else print "package_status=invalid"}
 }
 END {print "package_records=" count+0}' "$DIAG_PACKAGE_STATUS" 2>/dev/null
}
diag_config() {
 if [ ! -f "$DIAG_CONFIG" ] || [ -L "$DIAG_CONFIG" ]; then printf 'saved_config_status=unavailable\n'; return; fi
 # Decode only three committed flags. Do not invoke UCI, source configuration,
 # or print values from any other option (including protected MAC addresses).
 awk '
 function unquote(v) {gsub(/^[\047\"]|[\047\"]$/,"",v);return v}
 $1=="config" {main=($2=="limiter" && unquote($3)=="main");if(main)count++;next}
 main && $1=="option" && ($2=="enabled" || $2=="dry_run" || $2=="guest_internet") {
  v=unquote($3);if(v~/^(0|1|true|false|yes|no|on|off)$/)value[$2]=v;else value[$2]="invalid"
 }
 END {print "saved_config_main_sections=" count+0;split("enabled dry_run guest_internet",keys," ")
  for(i=1;i<=3;i++)print "saved_" keys[i] "=" (count!=1?"unknown":(keys[i] in value?value[keys[i]]:"absent"))
 }' "$DIAG_CONFIG" 2>/dev/null
}
diag_json_value() {
 local label="$1" data="$2" expression="$3" kind="$4" value status
 value="$(jsonfilter -s "$data" -e "$expression" 2>/dev/null)"; status=$?
 if [ "$status" != 0 ]; then printf '%s_status=unavailable\n' "$label"; return; fi
 case "$kind" in
  number) diag_number "$value" || { printf '%s_status=invalid\n' "$label"; return; };;
  boolean) case "$value" in true|false) ;; *) printf '%s_status=invalid\n' "$label"; return;; esac;;
  service_state) case "$value" in starting|running|error|stopped|unknown) ;; *) printf '%s_status=redacted\n' "$label"; return;; esac;;
  internet_state) case "$value" in inactive|allowed|blocked|test|error) ;; *) printf '%s_status=redacted\n' "$label"; return;; esac;;
  backend) case "$value" in ''|fw3|fw4) ;; *) printf '%s_status=redacted\n' "$label"; return;; esac;;
  family) case "$value" in ''|inet|netdev) ;; *) printf '%s_status=redacted\n' "$label"; return;; esac;;
  error) case "$value" in ''|nft_missing|firewall_backend_required|helper_missing|station_preflight_failed|configuration_invalid|worker_lock_busy|cleanup_failed|mtk_driver_missing|mtk_driver_unsupported|mtk_profile_unsupported|mtk_steering_active|mtk_tools_missing|network_selection_invalid|kernel_log_unavailable|adapter_missing|cache_write_failed|native_module_restore_failed|network_status_failed|offload_disable_failed|offload_restore_failed|policy_busy|rule_apply_failed|rule_validation_failed|vendor_controls_unknown|vendor_tool_missing|vendor_acceleration_unknown|vendor_network_unknown|vendor_vpn_hotplug_unsupported|vendor_config_unknown|vendor_pending_changes|vendor_config_active|vendor_runtime_unknown|vendor_hnat_active|vendor_flows_active_or_unknown|vendor_sfe_active|vendor_service_unknown|vendor_sfe_boot_active|vendor_backup_failed|vendor_backup_invalid|vendor_section_changed|vendor_config_write_failed|vendor_disable_failed|vendor_config_restore_failed|vendor_module_restore_failed|vendor_hook_restore_failed) ;; *) printf '%s_status=redacted\n' "$label"; return;; esac;;
 esac
 printf '%s=%s\n' "$label" "$value"
}
diag_status() {
 local data key pid
 DIAG_SERVICE_PID=
 for key in service-status internet-status; do
  diag_meta "$key" "$DIAG_STATE/$key.json"
  [ -f "$DIAG_STATE/$key.json" ] && [ ! -L "$DIAG_STATE/$key.json" ] || continue
  data="$(cat "$DIAG_STATE/$key.json" 2>/dev/null)" || continue
  if [ "$key" = service-status ]; then
   diag_json_value service_state "$data" '@.service_state' service_state
   diag_json_value service_error "$data" '@.error' error
   diag_json_value service_pid "$data" '@.pid' number
   pid="$(jsonfilter -s "$data" -e '@.pid' 2>/dev/null)"
   if diag_number "$pid" && [ "$pid" != 0 ]; then DIAG_SERVICE_PID="$pid"; fi
   # Detail may contain arbitrary driver text. Its file hash is captured above.
  else
   diag_json_value internet_internet_state "$data" '@.internet_state' internet_state
   diag_json_value internet_error "$data" '@.error' error
   diag_json_value internet_firewall_backend "$data" '@.firewall_backend' backend
   diag_json_value internet_nft_family "$data" '@.nft_family' family
   for pid in active offload_disabled; do diag_json_value "internet_$pid" "$data" "@.$pid" boolean; done
  fi
 done
}
diag_procd() {
 local data status values value n=0
 DIAG_PROCD_PIDS=
 data="$(ubus -t 5 call service list '{"name":"wifi-client-limiter"}' 2>/dev/null)"; status=$?
 printf 'procd_list_status=%s\n' "$status"
 [ "$status" = 0 ] || return
 values="$(jsonfilter -s "$data" -e '@["wifi-client-limiter"].instances.*.pid' 2>/dev/null)"
 for value in $values; do
  n=$((n+1)); [ "$n" -le 128 ] || break
  if diag_number "$value"; then printf 'procd_instance_%s_pid=%s\n' "$n" "$value"; DIAG_PROCD_PIDS="$DIAG_PROCD_PIDS $value"
  else printf 'procd_instance_%s_pid_status=invalid\n' "$n"; fi
 done
 values="$(jsonfilter -s "$data" -e '@["wifi-client-limiter"].instances.*.running' 2>/dev/null)"; n=0
 for value in $values; do
  n=$((n+1)); [ "$n" -le 128 ] || break
  case "$value" in true|false) printf 'procd_instance_%s_running=%s\n' "$n" "$value";; *) printf 'procd_instance_%s_running_status=invalid\n' "$n";; esac
 done
}
diag_locks() {
 local value
 DIAG_OWNER_PID=
 if [ -L "$DIAG_STATE/policy-lock" ]; then printf 'policy_lock_status=symlink_not_read\n'
 elif [ -d "$DIAG_STATE/policy-lock" ]; then
  printf 'policy_lock_status=present\n'
  if [ -f "$DIAG_STATE/policy-lock/pid" ] && [ ! -L "$DIAG_STATE/policy-lock/pid" ]; then
   value="$(cat "$DIAG_STATE/policy-lock/pid" 2>/dev/null)"
   if diag_number "$value" && [ "$value" != 0 ]; then DIAG_OWNER_PID="$value"; printf 'policy_lock_owner_pid=%s\n' "$value"
   else printf 'policy_lock_owner_status=invalid_or_unreadable\n'; fi
  else printf 'policy_lock_owner_status=missing_or_nonregular\n'; fi
 else printf 'policy_lock_status=absent\n'; fi
 if [ -d "$DIAG_STATE/lock" ]; then printf 'worker_lock_present=1\n'; else printf 'worker_lock_present=0\n'; fi
 if [ -d "$DIAG_STATE/station-policy-lock" ]; then printf 'station_policy_lock_present=1\n'; else printf 'station_policy_lock_present=0\n'; fi
}
diag_process_rows() {
 local path pid record wchan identity
 for path in "$DIAG_PROC_ROOT"/[0-9]*; do
  [ -d "$path" ] || continue
  pid="${path##*/}"; diag_number "$pid" || continue
  record="$(awk '
   {line=$0;sub(/^[0-9]+ \(.*\) /,"",line);n=split(line,a," ")
    if(n>=20 && a[1]~/^[RSDZTtWXxKIP]$/ && a[2]~/^[0-9]+$/ && a[20]~/^[0-9]+$/)print a[2] "\t" a[1] "\t" a[20]
   }' "$path/stat" 2>/dev/null)"; [ -n "$record" ] || continue
  wchan="$(cat "$path/wchan" 2>/dev/null)"
  if ! diag_token "$wchan" || [ -z "$wchan" ]; then wchan=unavailable; fi
  identity="$(tr '\000' '\n' < "$path/cmdline" 2>/dev/null | awk '
   {arg[NR]=$0}
   function base(v) {sub(/^.*\//,"",v);return v}
   END {
    first=base(arg[1]);role="unknown";label="arguments_omitted";script="";scriptindex=0
    if(first=="fw3" || first=="fw4" || first=="opkg"){role=first;label=first}
    if(first=="wifi-client-limiter"){role="worker";label="wifi-client-limiter"}
    if(first~/^(cat|awk|sed|grep|dmesg|sort|cksum|sha256sum|ubus|jsonfilter|flock|sleep)$/)label=first
    if(first=="sh" || first=="ash") {scriptindex=2;script=arg[scriptindex];label="shell arguments_omitted"}
    if(first=="busybox" && (arg[2]=="sh" || arg[2]=="ash")){scriptindex=3;script=arg[scriptindex];label="busybox shell arguments_omitted"}
    if(script=="/etc/rc.common")script=arg[++scriptindex]
    if(script=="/usr/sbin/wifi-client-limiter"){role="worker";label="shell wifi-client-limiter"}
    if(script=="/etc/init.d/wifi-client-limiter"){role="limiter_service";label="shell limiter-service"}
    if(script=="/usr/share/wifi-client-limiter/internet-policy.sh"){role="limiter_cleanup";label="shell internet-policy"}
    if(script=="/usr/share/wifi-client-limiter/upgrade-recovery.sh"){role="limiter_recovery";label="shell upgrade-recovery"}
    if(script=="/usr/share/wifi-client-limiter/package-hooks" || script~/^\/usr\/lib\/opkg\/info\/luci-app-wifi-client-limiter\.(preinst|postinst|prerm|postrm)$/){role="limiter_package_hook";label="shell limiter-package-hook"}
    for(i=2;i<=NR;i++) {
     if(arg[i]~/^(--daemon|--once|--cleanup|cleanup|--refresh|start|stop|reload|restart|print|install|upgrade|remove|configure|prerm|postinst|-4|-6)$/)label=label " " arg[i]
     if(first=="cat" && (arg[i]=="/sys/kernel/debug/hnat/hook_toggle" || arg[i]=="/sys/kernel/debug/hnat/hnat_entry"))label=label " " arg[i]
    }
    print role "\t" label
   }')"
  printf '%s\t%s\t%s\t%s\n' "$pid" "$record" "$wchan" "$identity"
 done
}
diag_processes() {
 diag_process_rows | awk -F '\t' -v roots="$DIAG_OWNER_PID $DIAG_SERVICE_PID $DIAG_PROCD_PIDS" '
  {pid=$1;parent[pid]=$2;state[pid]=$3;start[pid]=$4;wait[pid]=$5;role[pid]=$6;args[pid]=$7;order[++n]=pid
   if($6!="unknown")selected[pid]=1}
  END {split(roots,seed," ");for(i in seed)if(seed[i]~/^[0-9]+$/ && seed[i]!="0")selected[seed[i]]=1
   for(pass=0;pass<n;pass++){changed=0;for(pid in parent)if(selected[parent[pid]] && !selected[pid]){selected[pid]=1;changed=1};if(!changed)break}
   print "processes_scanned=" n+0
   for(i=1;i<=n;i++){pid=order[i];if(!selected[pid])continue;seen[pid]=1
    print "process pid=" pid " ppid=" parent[pid] " state=" state[pid] " starttime_ticks=" start[pid] " wchan=" wait[pid] " role=" role[pid] " argv=" args[pid]}
   for(pid in selected)if(selected[pid] && !seen[pid])print "process pid=" pid " identity_status=unavailable_or_exited"
  }'
}
diag_recovery() {
 local name
 for name in offload-backup native-flowoffload-backup vendor-acceleration-backup offload-reload-required vendor-acceleration-reload-required; do diag_meta "backup_$name" "$DIAG_BACKUP/$name"; done
 diag_meta recovery_archive "$DIAG_BACKUP/cksum-recovery-0.5.4"
 diag_meta recovery_original_offload "$DIAG_BACKUP/cksum-recovery-0.5.4/offload-backup.original"
 diag_meta recovery_original_firewall "$DIAG_BACKUP/cksum-recovery-0.5.4/firewall.original"
 for name in station-bans recent internet-uci-edits internet-policy-applied internet-policy-active; do diag_meta "runtime_$name" "$DIAG_STATE/$name"; done
 for name in internet-policy.sh posix-cksum.sh upgrade-recovery.sh package-hooks; do diag_meta "helper_$name" "$DIAG_HELPERS/$name"; done
}
diag_snapshot() {
 local number="$1" uptime
 printf '\n[snapshot %s]\n' "$number"
 uptime="$(cat "$DIAG_PROC_ROOT/uptime" 2>/dev/null)"; uptime="${uptime%% *}"
 case "$uptime" in ''|*[!0-9.]*) printf 'uptime_seconds=unavailable\n';; *) printf 'uptime_seconds=%s\n' "$uptime";; esac
 diag_package; diag_config; diag_status; diag_procd; diag_locks; diag_processes; diag_recovery
}
printf '%s\n' 'WiFi Client Limiter upgrade-lock diagnostics, revision 20261008-01' 'Read-only. Process arguments are allowlisted; private configuration and MAC contents are omitted.'
if ! command -v jsonfilter >/dev/null 2>&1; then printf 'jsonfilter_status=unavailable\n'; fi
diag_snapshot 1
printf '\n%s\n' 'snapshot_interval_seconds=6'
sleep 6
diag_snapshot 2
printf '\n%s\n' 'report_complete=1'

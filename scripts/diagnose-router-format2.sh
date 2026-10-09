#!/bin/sh
# SPDX-License-Identifier: MIT
# Inspection report: no configuration changes, service restarts or direct
# driver-control writes. Ordinary kernel queries can have implicit side effects.
# Run: sh /tmp/diagnose-router-format2.sh 2>&1 | tee /tmp/wifi-client-limiter-diagnostics.txt

diag_name() {
 case "$1" in ''|*[!a-zA-Z0-9_.-]*) return 1;; esac
 [ "${#1}" -le 128 ]
}
diag_field() {
 local label="$1" data="$2" expression="$3" result status
 result="$(jsonfilter -s "$data" -e "$expression" 2>/dev/null)"; status=$?
 printf '%s_status=%s\n' "$label" "$status"
 [ "$status" != 0 ] || printf '%s=%s\n' "$label" "$result"
}
diag_add_networks() {
 local name
 for name in $1; do
  diag_name "$name" || { printf 'logical_network_name_invalid=1\n'; continue; }
  case " $diag_networks " in *" $name "*) ;; *) diag_networks="$diag_networks $name";; esac
 done
}
diag_boolean() {
 local label="$1" value="$2" status="$3"
 printf '%s_read_status=%s\n' "$label" "$status"
 [ "$status" = 0 ] || return 0
 case "$value" in -|0|1|true|false|yes|no|on|off) printf '%s=%s\n' "$label" "$value";;
  *) printf '%s_value_status=invalid\n' "$label";;
 esac
}
diag_scalar_file() {
 local label="$1" path="$2" kind="$3" value status
 if [ ! -e "$path" ]; then printf '%s_present=0\n' "$label"; return; fi
 printf '%s_present=1\n' "$label"
 value="$(cat "$path" 2>/dev/null)"; status=$?
 printf '%s_read_status=%s\n' "$label" "$status"
 [ "$status" = 0 ] || return 0
 case "$kind:$value" in
  boolean:0|boolean:1|marker:disable|marker:restore) printf '%s=%s\n' "$label" "$value";;
  integer:*) case "$value" in ''|*[!0-9]*) printf '%s_value_status=invalid\n' "$label";;
    *) printf '%s=%s\n' "$label" "$value";; esac;;
  *) printf '%s_value_status=invalid\n' "$label";;
 esac
}
diag_committed_defaults() {
 # Decode UCI as data: UCI's ordinary reads also include saved deltas, even
 # with a different -P/-t path. Never source a router configuration file.
 awk '
 function tokens(line,out, i,c,q,n,v,started) {
  n=0;v="";q="";started=0
  for(i=1;i<=length(line);i++) {
   c=substr(line,i,1)
   if(q!="") {if(c==q)q="";else if(c=="\\" && q=="\""){i++;v=v substr(line,i,1)}else v=v c;started=1;continue}
   if(c=="#")break
   if(c=="\"" || c==sprintf("%c",39)){q=c;started=1;continue}
   if(c=="\\"){i++;if(i>length(line))return -1;v=v substr(line,i,1);started=1;continue}
   if(c~/[[:space:]]/){if(started){out[++n]=v;v="";started=0};continue}
   v=v c;started=1
  }
  if(q!="")return -1;if(started)out[++n]=v;return n
 }
 {delete t;n=tokens($0,t);if(n<0){bad=1;next};if(!n)next
  if(t[1]=="config"){main=(t[2]=="defaults" && (n==2 || n==3));if(main)count++;next}
  if(main && (t[2]=="flow_offloading" || t[2]=="flow_offloading_hw" || t[2]=="disable_ipv6")) {
   if(t[1]!="option" || n!=3){value[count,t[2]]="invalid";next}
   value[count,t[2]]=(t[3]~/^(-|0|1|true|false|yes|no|on|off)$/)?t[3]:"invalid"
  }
 }
 END {if(bad){print "committed_defaults_parse_status=1";exit 1}
  print "committed_defaults_parse_status=0";print "committed_defaults_count=" count+0
  for(i=1;i<=count;i++) {
   split("flow_offloading flow_offloading_hw disable_ipv6",keys," ")
   for(j=1;j<=3;j++)print "committed_defaults_" i "_" keys[j] "=" ((i SUBSEP keys[j] in value)?value[i,keys[j]]:"-")
  }
 }' /etc/config/firewall 2>/dev/null
}
diag_table_inventory() {
 local label="$1" path="$2" data status
 data="$(cat "$path" 2>/dev/null)"; status=$?
 printf '%s_read_status=%s\n' "$label" "$status"
 [ "$status" = 0 ] || return 0
 printf '%s\n' "$data" | awk -v label="$label" '
  NF {if(NF==1 && $1~/^(filter|nat|mangle|raw|security)$/)seen[$1]=1;else unknown++}
  END {split("filter nat mangle raw security",names," ");for(i=1;i<=5;i++)print label "_" names[i] "=" (seen[names[i]]?1:0);print label "_other_entries=" unknown+0}'
}
diag_firewall_summary() {
 local label="$1" data="$2" status="$3"
 printf '%s_status=%s\n' "$label" "$status"
 [ "$status" = 0 ] || return 0
 printf '%s\n' "$data" | awk -v label="$label" '
  /^\*[a-zA-Z0-9_]+$/ {inside=($0=="*filter");if(inside)tables++;next}
  /^COMMIT$/ {if(inside)commits++;inside=0;next}
  /(^|[[:space:]])(-j|--jump)[[:space:]]+FLOWOFFLOAD([[:space:]]|$)/ {
   total++;hardware=($0~/(^|[[:space:]])--hw([[:space:]]|$)/);total_hw+=hardware
   if(inside){flows++;flows_hw+=hardware}
  }
  inside && /^:FORWARD / {
   forwards++
   if(NF==3 && $2~/^(ACCEPT|DROP|-)$/ && $3~/^\[[0-9]+:[0-9]+\]$/) {policy=$2;counter=$3}else invalid++
  }
  END {
   print label "_filter_headers=" tables+0;print label "_forward_chains=" forwards+0;print label "_filter_commits=" commits+0
   print label "_filter_shape_valid=" ((tables==1 && forwards==1 && commits==1 && !inside && !invalid)?1:0)
   if(forwards==1 && !invalid){print label "_forward_policy=" policy;print label "_forward_counters=" counter}
   print label "_filter_flowoffload_rules=" flows+0;print label "_filter_hardware_flowoffload_rules=" flows_hw+0
   print label "_all_tables_flowoffload_rules=" total+0;print label "_all_tables_hardware_flowoffload_rules=" total_hw+0
  }'
}

printf '%s\n' 'WiFi Client Limiter read-only diagnostics, format 2'
printf '\n%s\n' '[installed package]'
if command -v opkg >/dev/null 2>&1; then
 diag_package="$(opkg status luci-app-wifi-client-limiter 2>/dev/null)"; diag_status=$?
 printf 'opkg_status=%s\n' "$diag_status"
 diag_version="$(printf '%s\n' "$diag_package" | awk '/^Version: / {print substr($0,10)}')"
 if [ -n "$diag_version" ]; then printf 'version_status=0\nversion=%s\n' "$diag_version"
 else printf 'version_status=1\n'; fi
elif command -v apk >/dev/null 2>&1; then
 diag_package="$(apk info -v luci-app-wifi-client-limiter 2>/dev/null)"; diag_status=$?
 printf 'apk_info_status=%s\n' "$diag_status"
 printf '%s\n' "$diag_package"
else printf 'package_manager_status=127\n'; fi

if ! command -v jsonfilter >/dev/null 2>&1; then
 printf 'jsonfilter_status=127\n'; exit 1
fi
printf '\n%s\n' '[worker lifecycle]'
diag_service="$(cat /tmp/wifi-client-limiter/service-status.json 2>/dev/null)"; diag_status=$?
printf 'service_status_read_status=%s\n' "$diag_status"
if [ "$diag_status" = 0 ]; then
 for diag_key in service_state error detail pid; do
  diag_field "service_$diag_key" "$diag_service" "@.$diag_key"
 done
 diag_pid="$(jsonfilter -s "$diag_service" -e '@.pid' 2>/dev/null)"; diag_status=$?
 if [ "$diag_status" = 0 ] && diag_name "$diag_pid"; then
  case "$diag_pid" in 0|*[!0-9]*) printf 'worker_cmdline_status=not_a_worker_pid\n';;
   *)
    diag_cmd="$(tr '\000' '\n' 2>/dev/null < "/proc/$diag_pid/cmdline")"; diag_status=$?
    printf 'worker_cmdline_read_status=%s\n' "$diag_status"
    if [ "$diag_status" = 0 ]; then
     diag_first="$(printf '%s\n' "$diag_cmd" | sed -n '1p')"
     diag_second="$(printf '%s\n' "$diag_cmd" | sed -n '2p')"
     diag_third="$(printf '%s\n' "$diag_cmd" | sed -n '3p')"
     # A stale PID can identify an unrelated command with private arguments.
     # Print only a recognized interpreter and the fixed limiter script path.
     diag_recognized=0
     case "$diag_first" in
      /usr/sbin/wifi-client-limiter)
       printf 'worker_argv0=/usr/sbin/wifi-client-limiter\n'; diag_recognized=1;;
      /bin/sh|/bin/ash|sh|ash)
       if [ "$diag_second" = /usr/sbin/wifi-client-limiter ]; then
        printf 'worker_argv0=%s\nworker_argv1=/usr/sbin/wifi-client-limiter\n' "$diag_first"
        diag_recognized=1
       fi;;
      /bin/busybox|busybox)
       case "$diag_second" in sh|ash)
        if [ "$diag_third" = /usr/sbin/wifi-client-limiter ]; then
         printf 'worker_argv0=%s\nworker_argv1=%s\nworker_argv2=/usr/sbin/wifi-client-limiter\n' "$diag_first" "$diag_second"
         diag_recognized=1
        fi;;
       esac;;
     esac
     [ "$diag_recognized" = 1 ] || printf 'worker_cmdline_status=unrecognized_process_arguments_omitted\n'
    fi;;
  esac
 else printf 'worker_cmdline_status=invalid_or_missing_pid\n'; fi
fi

printf '\n%s\n' '[procd limiter instances]'
diag_procd="$(ubus -t 5 call service list '{"name":"wifi-client-limiter"}' 2>/dev/null)"; diag_status=$?
printf 'procd_ubus_status=%s\n' "$diag_status"
if [ "$diag_status" = 0 ]; then
 diag_field procd_instance_running "$diag_procd" '@["wifi-client-limiter"].instances.*.running'
 diag_field procd_instance_pid "$diag_procd" '@["wifi-client-limiter"].instances.*.pid'
fi

printf '\n%s\n' '[limiter status RPC]'
diag_recorded="$(cat /tmp/wifi-client-limiter/internet-status.json 2>/dev/null)"; diag_status=$?
printf 'recorded_internet_status_read_status=%s\n' "$diag_status"
if [ "$diag_status" = 0 ]; then
 for diag_key in internet_state active error offload_disabled firewall_backend nft_family; do
  diag_field "recorded_$diag_key" "$diag_recorded" "@.$diag_key"
 done
fi
# The fw3 RPC proof uses save -t filter, which can implicitly load a missing
# table. Checking the recorded error first would still race a worker update.
if command -v fw3 >/dev/null 2>&1; then
 printf 'limiter_ubus_status=skipped_fw3_table_query_may_load_modules\n'
elif command -v fw4 >/dev/null 2>&1; then
 diag_limiter="$(ubus -t 5 call wifi-client-limiter status '{}' 2>/dev/null)"; diag_status=$?
 printf 'limiter_ubus_status=%s\n' "$diag_status"
 if [ "$diag_status" = 0 ]; then
  for diag_key in internet_state active firewall_backend nft_family reason error enabled dry_run guest_internet service_running service_state service_error service_detail; do
   diag_field "limiter_$diag_key" "$diag_limiter" "@.$diag_key"
  done
 fi
else printf 'limiter_ubus_status=skipped_unknown_firewall_backend\n'; fi

printf '\n%s\n' '[native acceleration configuration]'
diag_committed_defaults; diag_status=$?
printf 'committed_defaults_read_status=%s\n' "$diag_status"
diag_firewall="$(uci -q -X show firewall 2>/dev/null)"; diag_status=$?
printf 'effective_firewall_uci_status=%s\n' "$diag_status"
if [ "$diag_status" = 0 ]; then
 diag_defaults="$(printf '%s\n' "$diag_firewall" | awk '/^firewall\.[a-zA-Z0-9_]+=defaults$/ {sub(/^firewall\./,"");sub(/=defaults$/,"");print}')"
 diag_count=0
 for diag_section in $diag_defaults; do
  diag_count=$((diag_count+1))
  if [ "${#diag_section}" -le 64 ]; then
   printf 'effective_defaults_%s_original=%s\n' "$diag_count" "$diag_section"
  else printf 'effective_defaults_%s_original_status=invalid\n' "$diag_count"; fi
  for diag_key in flow_offloading flow_offloading_hw disable_ipv6; do
   diag_value="$(uci -q get "firewall.$diag_section.$diag_key" 2>/dev/null)"; diag_status=$?
   diag_boolean "effective_defaults_${diag_count}_$diag_key" "$diag_value" "$diag_status"
  done
  diag_snapshot="$(uci -q -X show "firewall.$diag_section" 2>/dev/null)"; diag_status=$?
  printf 'effective_defaults_%s_snapshot_status=%s\n' "$diag_count" "$diag_status"
  if [ "$diag_status" = 0 ]; then
   diag_checksum="$(
    if (set -o pipefail) 2>/dev/null; then set -o pipefail; fi
    printf '%s\n' "$diag_snapshot" | awk -v prefix="firewall.$diag_section" '
     index($0,prefix)==1 {line=substr($0,length(prefix)+1)
      if(line !~ /^\.(flow_offloading|flow_offloading_hw)=/) print line}' |
     sort | cksum 2>/dev/null
   )" 2>/dev/null; diag_status=$?
   printf 'effective_defaults_%s_fingerprint_status=%s\n' "$diag_count" "$diag_status"
   if [ "$diag_status" = 0 ]; then
    printf '%s\n' "$diag_checksum" | awk -v label="effective_defaults_${diag_count}_fingerprint" '
     NF==2 && $1~/^[0-9]+$/ && $2~/^[0-9]+$/ {print label "=" $1 ":" $2;valid=1}
     END {if(!valid)print label "_value_status=invalid"}'
   fi
  fi
 done
 printf 'effective_defaults_count=%s\n' "$diag_count"
fi
diag_pending="$(uci -q changes firewall 2>/dev/null)"; diag_status=$?
printf 'firewall_pending_uci_status=%s\n' "$diag_status"
if [ "$diag_status" = 0 ]; then
 printf '%s\n' "$diag_pending" | awk '
  NF {lines++}
  /^-?firewall\.([a-zA-Z0-9_]+|@defaults\[[0-9]+\])\.(flow_offloading|flow_offloading_hw|disable_ipv6)(=|$)/ {
   line=$0;position=index(line,"=");key=(position?substr(line,1,position-1):line);sub(/^.*\./,"",key)
   if(position){value=substr(line,position+1);quote=sprintf("%c",39);if(substr(value,1,1)==quote && substr(value,length(value),1)==quote)value=substr(value,2,length(value)-2)
    else if(substr(value,1,1)=="\"" && substr(value,length(value),1)=="\"")value=substr(value,2,length(value)-2)
    if(value!~/^(-|0|1|true|false|yes|no|on|off)$/)value="invalid"
   }else value="deleted"
   print "pending_acceleration_" ++changes "_option=" key;print "pending_acceleration_" changes "_value=" value
  }
  END {print "firewall_pending_present=" (lines?1:0);print "firewall_pending_lines=" lines+0;print "pending_acceleration_changes=" changes+0}'
fi

printf '\n%s\n' '[owned native acceleration recovery state]'
diag_backup=/etc/wifi-client-limiter/offload-backup
if [ -e "$diag_backup" ]; then
 printf 'offload_backup_present=1\n'
 diag_backup_data="$(cat "$diag_backup" 2>/dev/null)"; diag_status=$?
 printf 'offload_backup_read_status=%s\n' "$diag_status"
 if [ "$diag_status" = 0 ]; then
  printf '%s\n' "$diag_backup_data" | awk '
   NF {records++
    if(NF==3 && $1~/^[a-zA-Z0-9_]+$/ && length($1)<=64 && $2~/^(flow_offloading|flow_offloading_hw)$/ && $3~/^(-|0|1)$/) {
     invalid++;legacy++;print "offload_backup_record_" records "_legacy_missing_fingerprint=1"
     print "offload_backup_record_" records "_original=" $1;print "offload_backup_record_" records "_option=" $2;print "offload_backup_record_" records "_value=" $3;next
    }
    if(NF!=4 || $1!~/^[a-zA-Z0-9_]+$/ || length($1)>64 || $2!~/^[0-9]+:[0-9]+$/ || $3!~/^(flow_offloading|flow_offloading_hw)$/ || $4!~/^(-|0|1|true|false|yes|no|on|off)$/){invalid++;next}
    print "offload_backup_record_" records "_original=" $1;print "offload_backup_record_" records "_fingerprint=" $2
    print "offload_backup_record_" records "_option=" $3;print "offload_backup_record_" records "_value=" $4}
   END {print "offload_backup_records=" records+0;print "offload_backup_invalid_records=" invalid+0;print "offload_backup_legacy_missing_fingerprint_records=" legacy+0;print "offload_backup_format_valid=" ((records && !invalid)?1:0)}'
 fi
else printf 'offload_backup_present=0\n'; fi
diag_scalar_file native_flowoffload_backup /etc/wifi-client-limiter/native-flowoffload-backup boolean
diag_scalar_file offload_reload_required /etc/wifi-client-limiter/offload-reload-required marker
for diag_path in /etc/wifi-client-limiter/vendor-acceleration-backup /tmp/wifi-client-limiter/internet-uci-edits; do
 case "$diag_path" in */vendor-acceleration-backup) diag_label=vendor_acceleration_backup;; *) diag_label=owned_uci_edits;; esac
 if [ -e "$diag_path" ]; then printf '%s_present=1\n' "$diag_label"; else printf '%s_present=0\n' "$diag_label"; fi
done

printf '\n%s\n' '[native flow offload module and IPv6 state]'
for diag_tool in fw3 iptables-save ip6tables-save rmmod modprobe insmod awk sort cksum; do
 if command -v "$diag_tool" >/dev/null 2>&1; then printf 'tool_%s_available=1\n' "$diag_tool"
 else printf 'tool_%s_available=0\n' "$diag_tool"; fi
done
if [ -d /sys/module ] && [ -r /sys/module ] && [ -x /sys/module ]; then
 printf 'module_inventory_visible=1\n'
 for diag_module in ip_tables iptable_filter ip6_tables ip6table_filter; do
  if [ -d "/sys/module/$diag_module" ]; then printf 'module_%s_present=1\n' "$diag_module"
  else printf 'module_%s_present=0\n' "$diag_module"; fi
 done
 if [ -d /sys/module/xt_FLOWOFFLOAD ]; then
  printf 'xt_FLOWOFFLOAD_present=1\n'
  diag_scalar_file xt_FLOWOFFLOAD_refcnt /sys/module/xt_FLOWOFFLOAD/refcnt integer
  if [ -d /sys/module/xt_FLOWOFFLOAD/holders ] && [ -r /sys/module/xt_FLOWOFFLOAD/holders ] && [ -x /sys/module/xt_FLOWOFFLOAD/holders ]; then
   diag_count=0
   for diag_holder in /sys/module/xt_FLOWOFFLOAD/holders/*; do
    [ -e "$diag_holder" ] || [ -L "$diag_holder" ] || continue
    diag_count=$((diag_count+1))
   done
   printf 'xt_FLOWOFFLOAD_holders_status=0\nxt_FLOWOFFLOAD_holders_count=%s\n' "$diag_count"
  else printf 'xt_FLOWOFFLOAD_holders_status=unavailable\n'; fi
 else printf 'xt_FLOWOFFLOAD_present=0\n'; fi
else printf 'module_inventory_visible=0\nxt_FLOWOFFLOAD_present=unknown\n'; fi
diag_scalar_file ipv6_disable_all /proc/sys/net/ipv6/conf/all/disable_ipv6 integer
diag_table_inventory ipv4_registered_tables /proc/net/ip_tables_names
diag_table_inventory ipv6_registered_tables /proc/net/ip6_tables_names

printf '\n%s\n' '[native firewall summaries; rule bodies and errors omitted]'
# fw3 print opens table handles and can load missing tables. Avoid changing
# the failure under investigation. Unfiltered save visits registered tables;
# -M provides an additional bound against a userspace module-loader fallback.
printf 'fw3_print_status=skipped_to_avoid_implicit_module_loading\n'
diag_dump="$(iptables-save -M /bin/false 2>/dev/null)"; diag_status=$?
diag_firewall_summary iptables_save "$diag_dump" "$diag_status"
diag_dump="$(ip6tables-save -M /bin/false 2>/dev/null)"; diag_status=$?
diag_firewall_summary ip6tables_save "$diag_dump" "$diag_status"

printf '\n%s\n' '[selected WiFi sections]'
diag_selected="$(uci -q get wifi-client-limiter.main.network 2>/dev/null)"; diag_status=$?
printf 'selected_networks_uci_status=%s\n' "$diag_status"
diag_sections=; diag_networks=; diag_selected_aps=
for diag_section in $diag_selected; do
 diag_name "$diag_section" || { printf 'selected_section_name_invalid=1\n'; continue; }
 diag_sections="$diag_sections $diag_section"
 printf 'selected_section=%s\n' "$diag_section"
 for diag_key in mode network; do
  diag_value="$(uci -q get "wireless.$diag_section.$diag_key" 2>/dev/null)"; diag_status=$?
  printf 'uci_%s_%s_status=%s\n' "$diag_section" "$diag_key" "$diag_status"
  [ "$diag_status" != 0 ] || printf 'uci_%s_%s=%s\n' "$diag_section" "$diag_key" "$diag_value"
  [ "$diag_key" != network ] || diag_add_networks "$diag_value"
  if [ "$diag_key" = mode ] && [ "$diag_status" = 0 ] && [ "$diag_value" = ap ]; then
   diag_selected_aps="$diag_selected_aps $diag_section"
  fi
 done
done

printf '\n%s\n' '[wireless runtime shape; SSIDs and keys omitted]'
diag_wireless="$(ubus -t 5 call network.wireless status '{}' 2>/dev/null)"; diag_status=$?
printf 'wireless_ubus_status=%s\n' "$diag_status"
diag_live_aps=
if [ "$diag_status" = 0 ]; then
 diag_field radio_types "$diag_wireless" '@.*.config.type'
 diag_entries="$(jsonfilter -s "$diag_wireless" -e '@.*.interfaces[*].section' 2>/dev/null)"; diag_status=$?
 printf 'wireless_sections_status=%s\n' "$diag_status"
 for diag_section in $diag_entries; do
  diag_name "$diag_section" || { printf 'wireless_section_name_invalid=1\n'; continue; }
  printf 'runtime_section=%s\n' "$diag_section"
  diag_expression="@.*.interfaces[@.section='$diag_section']"
  diag_field "runtime_${diag_section}_ifname" "$diag_wireless" "$diag_expression.ifname"
  diag_field "runtime_${diag_section}_mode" "$diag_wireless" "$diag_expression.config.mode"
  diag_field "runtime_${diag_section}_network" "$diag_wireless" "$diag_expression.config.network"
  case " $diag_sections " in *" $diag_section "*) ;; *) continue;; esac
  diag_attachment="$(jsonfilter -s "$diag_wireless" -e "$diag_expression.config.network" 2>/dev/null)"; diag_status=$?
  if [ "$diag_status" = 0 ]; then
   case "$diag_attachment" in
    '['*) diag_attachment="$(jsonfilter -s "$diag_attachment" -e '@[*]' 2>/dev/null)";;
   esac
   diag_add_networks "$diag_attachment"
  fi
  diag_mode="$(jsonfilter -s "$diag_wireless" -e "$diag_expression.config.mode" 2>/dev/null)"
  if [ -z "$diag_mode" ]; then
   case " $diag_selected_aps " in *" $diag_section "*) diag_mode=ap;; esac
  fi
  [ "$diag_mode" = ap ] || continue
  diag_ifnames="$(jsonfilter -s "$diag_wireless" -e "$diag_expression.ifname" 2>/dev/null)"
  for diag_ifname in $diag_ifnames; do
   diag_name "$diag_ifname" && [ "${#diag_ifname}" -le 15 ] || { printf 'live_ap_ifname_invalid=1\n'; continue; }
   case " $diag_live_aps " in *" $diag_ifname "*) ;; *) diag_live_aps="$diag_live_aps $diag_ifname";; esac
  done
 done
fi

printf '\n%s\n' '[selected logical interface statuses]'
for diag_network in $diag_networks; do
 printf 'logical_network=%s\n' "$diag_network"
 diag_interface="$(ubus -t 5 call "network.interface.$diag_network" status '{}' 2>/dev/null)"; diag_status=$?
 printf 'logical_ubus_status=%s\n' "$diag_status"
 [ "$diag_status" = 0 ] || continue
 for diag_key in up l3_device ipv4-address ipv6-address ipv6-prefix-assignment; do
  diag_field "$diag_key" "$diag_interface" "@[\"$diag_key\"]"
 done
done

printf '\n%s\n' '[selected live AP associations]'
for diag_ifname in $diag_live_aps; do
 printf 'ap_ifname=%s\n' "$diag_ifname"
 diag_associations="$(iwinfo "$diag_ifname" assoclist 2>/dev/null)"; diag_status=$?
 printf 'iwinfo_assoclist_status=%s\n' "$diag_status"
 [ "$diag_status" != 0 ] || printf '%s\n' "$diag_associations"
done
printf '\n%s\n' 'End of read-only diagnostics.'

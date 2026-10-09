#!/bin/sh
# SPDX-License-Identifier: MIT
# GL.iNet MTK controls verified against the vendor's kernel 5.4.211 packages.
# Sourced by the Internet policy and RPC helpers; no native-firewall dependency.
INTERNET_VENDOR_ROOT="${INTERNET_VENDOR_ROOT:-}"
INTERNET_BACKUP_DIR="${INTERNET_BACKUP_DIR:-/etc/wifi-client-limiter}"

internet_vendor_path() { printf '%s%s\n' "$INTERNET_VENDOR_ROOT" "$1"; }
internet_vendor_error() { INTERNET_VENDOR_ERROR="$1"; return 1; }
internet_vendor_modules() { printf '%s\n' shortcut_fe shortcut_fe_ipv6 shortcut_fe_cm shortcut_fe_drv fast_classifier; }
internet_vendor_hnat_module() {
 local kernel module hash
 kernel="$(uname -r)" || return 1
 case "$kernel" in ''|*[!a-zA-Z0-9_.+-]*|*..*) return 1;; esac
 module="$(internet_vendor_path "/lib/modules/$kernel/mtkhnat.ko")"
 [ -r "$module" ] || return 1
 hash="$(sha256sum "$module" 2>/dev/null)" || return 1
 hash="${hash%% *}"
 case "$hash" in
  19751ae1fd69e351b26900f983ece10ae99f6f8a6dc07d22112d0c9dbbc6c65f|d1b74a335d444ee735d2f0b332cdb9422035c0d0ea14cb3ef29d8229d4a4f756) printf '%s\n' "$module";;
  *) return 1;;
 esac
}
internet_vendor_hnat_absent() {
 # Both audited module removal paths stop every PPE, invalidate its entries,
 # flush and disable the caches, clear IPv4/IPv6 routing and free DMA tables.
 # WARP depends on HNAT and cannot independently create a binding without it.
 [ -d "$(internet_vendor_path /sys/module)" ] &&
 [ -r "$(internet_vendor_path /sys/module)" ] &&
 [ -x "$(internet_vendor_path /sys/module)" ] &&
 [ ! -d "$(internet_vendor_path /sys/module/mtkhnat)" ] &&
 [ ! -d "$(internet_vendor_path /sys/module/mtk_warp)" ] &&
 [ ! -e "$(internet_vendor_path /sys/kernel/debug/hnat/hook_toggle)" ] &&
 [ ! -e "$(internet_vendor_path /sys/kernel/debug/hnat/hnat_entry)" ] &&
 internet_vendor_hnat_module >/dev/null
}
internet_vendor_present() {
 local directory name
 [ -f "$INTERNET_BACKUP_DIR/vendor-acceleration-backup" ] && return 0
 [ -f "$(internet_vendor_path /etc/config/mtkhnat)" ] && return 0
 [ -d "$(internet_vendor_path /sys/kernel/debug/hnat)" ] && return 0
 for directory in "$(internet_vendor_path /sys/module)"/*; do
  [ -d "$directory" ] || continue
  name="${directory##*/}"
  case "$name" in mtkhnat|hnat|shortcut_*|sfe_*|fast_classifier|ecm|qca_nss*|nss*|natflow|fast_nat|fast_forward*) return 0;; esac
 done
 return 1
}
internet_vendor_known() {
 local directory name tool protocols
 [ -f "$(internet_vendor_path /etc/config/mtkhnat)" ] || { internet_vendor_error vendor_controls_unknown; return 1; }
 for tool in uci dmesg rmmod insmod cat awk sed tail uname sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || { internet_vendor_error vendor_tool_missing; return 1; }
 done
 internet_vendor_hnat_module >/dev/null || { internet_vendor_error vendor_controls_unknown; return 1; }
 if ! internet_vendor_hnat_absent; then
  [ -d "$(internet_vendor_path /sys/module/mtkhnat)" ] &&
  [ -r "$(internet_vendor_path /sys/kernel/debug/hnat/hook_toggle)" ] &&
  [ -w "$(internet_vendor_path /sys/kernel/debug/hnat/hook_toggle)" ] &&
  [ -r "$(internet_vendor_path /sys/kernel/debug/hnat/hnat_entry)" ] &&
  [ -w "$(internet_vendor_path /sys/kernel/debug/hnat/hnat_entry)" ] || { internet_vendor_error vendor_controls_unknown; return 1; }
 fi
 for directory in "$(internet_vendor_path /sys/module)"/*; do
  [ -d "$directory" ] || continue
  name="${directory##*/}"
  case "$name" in mtkhnat|shortcut_fe|shortcut_fe_ipv6|shortcut_fe_cm|shortcut_fe_drv|fast_classifier) ;;
   hnat|shortcut_*|sfe_*|ecm|qca_nss*|nss*|natflow|fast_nat|fast_forward*) internet_vendor_error vendor_acceleration_unknown; return 1;;
  esac
 done
 # This stock hotplug hook ignores both acceleration settings and enables HNAT
 # on a PPTP/L2TP disconnect. Refuse that topology instead of editing its file.
 protocols="$(uci -q -X show network)" || { internet_vendor_error vendor_network_unknown; return 1; }
 if printf '%s\n' "$protocols" | awk -F= '/^network\..*\.proto=/ {gsub(/\047/, "", $2); if($2=="l2tp" || $2=="pptp") found=1} END {exit !found}'; then
  internet_vendor_error vendor_vpn_hotplug_unsupported; return 1
 fi
}
internet_vendor_no_pending() {
 local pending
 pending="$(uci -q changes mtkhnat)" || { internet_vendor_error vendor_config_unknown; return 1; }
 [ -z "$pending" ] || { internet_vendor_error vendor_pending_changes; return 1; }
}
internet_vendor_option() {
 local value
 value="$(uci -q get "mtkhnat.global.$1")" || value=-
 case "$1:$value" in enable:-|enable:0|enable:1|actype:-|actype:0|actype:1|actype:2) printf '%s\n' "$value";; *) return 1;; esac
}
internet_vendor_hook_read() {
 local before after output delta anchor state LC_ALL=C
 export LC_ALL
 if internet_vendor_hnat_absent; then printf '0\n'; return 0; fi
 # The matched driver emits no seq_file output. Reading causes a printk of the
 # current value. Require a fresh message without clearing anybody else's log.
 before="$(dmesg 2>/dev/null)" || return 1
 output="$(cat "$(internet_vendor_path /sys/kernel/debug/hnat/hook_toggle)" 2>/dev/null)" || return 1
 [ -z "$output" ] || return 1
 after="$(dmesg 2>/dev/null)" || return 1
 # BusyBox ash scans every possible prefix for ${after#"$before"}. On a
 # large kernel log that becomes quadratic work while the policy lock is held.
 case "$after" in
  "$before"?*)
   # Exact prefix plus C locale makes this a byte offset.
   delta="$(printf '%s' "$after" | tail -c "+$(( ${#before} + 1 ))")" || return 1
   ;;
  *)
   # A full kernel ring normally drops old records for every new printk.
   # Preserve the last old record as a boundary, accepting it only once in
   # each snapshot. Never treat an old hook message as fresh after rotation.
   anchor="$(printf '%s\n' "$before" | tail -n 1)" || return 1
   [ -n "$anchor" ] || return 1
   printf '%s\n' "$before" | WCL_VENDOR_HOOK_ANCHOR="$anchor" awk '
    $0==ENVIRON["WCL_VENDOR_HOOK_ANCHOR"] {matches++}
    END {exit matches!=1}' || return 1
   delta="$(printf '%s\n' "$after" | WCL_VENDOR_HOOK_ANCHOR="$anchor" awk '
    $0==ENVIRON["WCL_VENDOR_HOOK_ANCHOR"] {matches++; next}
    matches {print}
    END {exit matches!=1}')" || return 1
   ;;
 esac
 state="$(printf '%s\n' "$delta" | awk '
  /value=/ && /hook/ {
   value=$0; sub(/^.*value=/, "", value)
   if(value!="0, hook is disabled now!" && value!="1, hook is enabled now!") {bad=1; next}
   current=substr(value, 1, 1)
   if(seen && current!=state) bad=1
   seen=1; state=current
  }
  END {if(!seen || bad) exit 1; print state}')" || return 1
 case "$state" in 0|1) printf '%s\n' "$state";; *) return 1;; esac
}
internet_vendor_hook_write() {
 case "$1" in 0|1) ;; *) return 1;; esac
 if internet_vendor_hnat_absent; then [ "$1" = 0 ]; return; fi
 internet_vendor_control_write "$(internet_vendor_path /sys/kernel/debug/hnat/hook_toggle)" "$1"
}
internet_vendor_control_write() { printf '%s\n' "$2" > "$1"; }
internet_vendor_entries_read() {
 local output
 if internet_vendor_hnat_absent; then printf '2\n'; return 0; fi
 output="$(cat "$(internet_vendor_path /sys/kernel/debug/hnat/hnat_entry)" 2>/dev/null)" || return 1
 # Every actual PPE must supply its own valid summary. Never accept an empty
 # dump, a different debug selector, duplicate PPEs or a nonzero bound count.
 printf '%s\n' "$output" | awk -v empty="${1:-0}" '
  /^PPE_ID = / {if(waiting || $3 !~ /^[01]$/ || seen[$3]++) bad=1; waiting=1; ids++; next}
  /^Total State = / {
   if(!waiting || NF!=7 || $5!="cnt" || $6!="=" || $7!~/^[0-9]+$/) bad=1
   value=-1; if($4=="Invalid") value=0; if($4=="Unbind") value=1; if($4=="BIND") value=2; if($4=="FIN") value=3
   if(value<0 || (summaries && previous!=value)) bad=1
   if(empty==1 && (value!=2 || $7!=0)) bad=1
   previous=value; summaries++; waiting=0
  }
  END {if(bad || waiting || ids<1 || summaries!=ids || !seen[0]) exit 1; print previous}'
}
internet_vendor_selector_write() {
 case "$1" in 0|1|2|3) ;; *) return 1;; esac
 if internet_vendor_hnat_absent; then [ "$1" = 2 ]; return; fi
 internet_vendor_control_write "$(internet_vendor_path /sys/kernel/debug/hnat/hnat_entry)" "1 $1"
}
internet_vendor_flush() {
 local entry="$(internet_vendor_path /sys/kernel/debug/hnat/hnat_entry)"
 internet_vendor_hnat_absent && return 0
 # Documented -1 clears all entries and refreshes the hardware cache. Verify
 # every actual PPE afterward, since an absent PPE's write can still succeed.
 internet_vendor_control_write "$entry" '3 -1' && internet_vendor_control_write "$entry" '5 -1' || return 1
 internet_vendor_selector_write 2 && internet_vendor_entries_read 1 >/dev/null
}
internet_vendor_module_loaded() { [ -d "$(internet_vendor_path "/sys/module/$1")" ]; }
internet_vendor_service() { printf '%s\n' "$(internet_vendor_path /etc/init.d/shortcut-fe)"; }
internet_vendor_service_enabled() {
 local service="$(internet_vendor_service)" result
 [ -x "$service" ] || { printf '%s\n' -; return 0; }
 "$service" enabled >/dev/null 2>&1; result=$?
 case "$result" in 0) printf '1\n';; 1) printf '0\n';; *) return 1;; esac
}
internet_vendor_backup_validate() {
 local backup="${1:-$INTERNET_BACKUP_DIR/vendor-acceleration-backup}"
 [ -f "$backup" ] || return 1
 awk '
  NR==1 {if($0!="WCL_VENDOR_ACCELERATION_V1") bad=1; next}
  {if(NF!=3 || seen[$1 " " $2]++) bad=1}
  $1=="option" {if($2=="enable" && $3~/^(-|0|1)$/) enable++; else if($2=="actype" && $3~/^(-|0|1|2)$/) actype++; else bad=1; next}
  $1=="runtime" {if($2=="hook" && $3~/^[01]$/) hook++; else if($2=="selector" && $3~/^[0-3]$/) selector++; else if($2=="service" && $3~/^(-|0|1)$/) service++; else if($2=="section" && $3~/^[a-zA-Z0-9_]+$/) section++; else bad=1; next}
  $1=="module" {if($2!~/^(shortcut_fe|shortcut_fe_ipv6|shortcut_fe_cm|shortcut_fe_drv|fast_classifier)$/ || $3!~/^[01]$/) bad=1; modules++; next}
  {bad=1}
  END {exit bad || enable!=1 || actype!=1 || hook!=1 || selector!=1 || service!=1 || section!=1 || modules!=5}' "$backup"
}
internet_vendor_saved() { awk -v kind="$1" -v name="$2" '$1==kind && $2==name {print $3}' "$INTERNET_BACKUP_DIR/vendor-acceleration-backup"; }
internet_vendor_marker() {
 local marker="$INTERNET_BACKUP_DIR/vendor-acceleration-reload-required"
 umask 077
 printf '%s\n' "$1" > "$marker-next" && chmod 600 "$marker-next" && mv "$marker-next" "$marker"
}
internet_vendor_backup_create() {
 local value section hook selector service enabled=- module enable actype backup="$INTERNET_BACKUP_DIR/vendor-acceleration-backup"
 section="$(uci -q get mtkhnat.global)" || return 1
 case "$section" in ''|*[!a-zA-Z0-9_]*) return 1;; esac
 enable="$(internet_vendor_option enable)" && actype="$(internet_vendor_option actype)" || return 1
 hook="$(internet_vendor_hook_read)" && selector="$(internet_vendor_entries_read)" || return 1
 service="$(internet_vendor_service)"
 enabled="$(internet_vendor_service_enabled)" || return 1
 if [ "$enabled" = - ]; then
  for module in $(internet_vendor_modules); do internet_vendor_module_loaded "$module" && return 1; done
 fi
 mkdir -p "$INTERNET_BACKUP_DIR" && chmod 700 "$INTERNET_BACKUP_DIR" || return 1
 umask 077
 {
  printf 'WCL_VENDOR_ACCELERATION_V1\n'
  printf 'option enable %s\noption actype %s\n' "$enable" "$actype"
  printf 'runtime section %s\nruntime hook %s\nruntime selector %s\nruntime service %s\n' "$section" "$hook" "$selector" "$enabled"
  for module in $(internet_vendor_modules); do
   if internet_vendor_module_loaded "$module"; then value=1; else value=0; fi
   printf 'module %s %s\n' "$module" "$value"
  done
 } > "$backup-next" || { rm -f "$backup-next"; return 1; }
 internet_vendor_backup_validate "$backup-next" && chmod 600 "$backup-next" && mv "$backup-next" "$backup"
}
internet_vendor_adopt_settings() {
 local backup="$INTERNET_BACKUP_DIR/vendor-acceleration-backup" enable actype original_enable original_actype hook selector service module loaded="" selector_owned=0
 enable="$(internet_vendor_option enable)" && actype="$(internet_vendor_option actype)" || return 1
 hook="$(internet_vendor_hook_read)" && selector="$(internet_vendor_entries_read)" && service="$(internet_vendor_service_enabled)" || return 1
 if [ "$selector" = 0 ] && [ -f "$INTERNET_BACKUP_DIR/vendor-acceleration-reload-required" ] &&
  [ "$(cat "$INTERNET_BACKUP_DIR/vendor-acceleration-reload-required")" = restore-hnat-selector ]; then selector_owned=1; fi
 for module in $(internet_vendor_modules); do
  internet_vendor_module_loaded "$module" && loaded="$loaded $module"
 done
 original_enable="$(internet_vendor_saved option enable)"; original_actype="$(internet_vendor_saved option actype)"
 [ "$enable" = 0 ] || original_enable="$enable"
 [ "$actype" = 1 ] || original_actype="$actype"
 umask 077
 awk -v enable="$original_enable" -v actype="$original_actype" -v hook="$hook" -v selector="$selector" -v selector_owned="$selector_owned" -v service="$service" -v loaded="$loaded " '
  $1=="option" && $2=="enable" {$3=enable}
  $1=="option" && $2=="actype" {$3=actype}
  $1=="runtime" && $2=="hook" && hook==1 {$3=1}
  $1=="runtime" && $2=="selector" && selector!=2 && !selector_owned {$3=selector}
  $1=="runtime" && $2=="service" && service==1 {$3=1}
  $1=="module" && index(loaded, " " $2 " ") {$3=1}
  {print}' "$backup" > "$backup-next" && internet_vendor_backup_validate "$backup-next" &&
 chmod 600 "$backup-next" && mv "$backup-next" "$backup"
}
internet_vendor_revert_options() {
 uci -q revert mtkhnat.global.enable
 uci -q revert mtkhnat.global.actype
}
internet_vendor_sfe_disable() {
 local service="$(internet_vendor_service)" module
 if [ -x "$service" ]; then
  # Its last false conditional returns1 when a classifier is absent. Actual
  # module disappearance, rather than this legacy exit status, is authoritative.
  "$service" stop >/dev/null 2>&1 || :
  "$service" disable >/dev/null 2>&1 || return 1
  [ "$(internet_vendor_service_enabled)" = 0 ] || return 1
 fi
 for module in shortcut_fe_drv shortcut_fe_cm fast_classifier shortcut_fe_ipv6 shortcut_fe; do
  internet_vendor_module_loaded "$module" || continue
  rmmod "$module" >/dev/null 2>&1 || return 1
  internet_vendor_module_loaded "$module" && return 1
 done
 return 0
}
internet_vendor_inactive() {
 local module hook service
 INTERNET_VENDOR_ERROR=
 internet_vendor_present || return 0
 internet_vendor_known && internet_vendor_no_pending || return 1
 [ "$(uci -q get mtkhnat.global.enable)" = 0 ] &&
 [ "$(uci -q get mtkhnat.global.actype)" = 1 ] || { internet_vendor_error vendor_config_active; return 1; }
 hook="$(internet_vendor_hook_read)" || { internet_vendor_error vendor_runtime_unknown; return 1; }
 [ "$hook" = 0 ] || { internet_vendor_error vendor_hnat_active; return 1; }
 internet_vendor_entries_read 1 >/dev/null || { internet_vendor_error vendor_flows_active_or_unknown; return 1; }
 for module in $(internet_vendor_modules); do
  internet_vendor_module_loaded "$module" && { internet_vendor_error vendor_sfe_active; return 1; }
 done
 service="$(internet_vendor_service)"
 if [ -x "$service" ]; then
  service="$(internet_vendor_service_enabled)" || { internet_vendor_error vendor_service_unknown; return 1; }
  [ "$service" = 0 ] || { internet_vendor_error vendor_sfe_boot_active; return 1; }
 fi
 return 0
}
internet_vendor_disable() {
 local backup="$INTERNET_BACKUP_DIR/vendor-acceleration-backup"
 INTERNET_VENDOR_ERROR=
 internet_vendor_present || return 0
 internet_vendor_known && internet_vendor_no_pending || return 1
 [ -f "$backup" ] || internet_vendor_backup_create || { internet_vendor_error vendor_backup_failed; return 1; }
 internet_vendor_backup_validate || { internet_vendor_error vendor_backup_invalid; return 1; }
 [ "$(uci -q get mtkhnat.global)" = "$(internet_vendor_saved runtime section)" ] || { internet_vendor_error vendor_section_changed; return 1; }
 if internet_vendor_inactive; then
  rm -f "$INTERNET_BACKUP_DIR/vendor-acceleration-reload-required"
  INTERNET_VENDOR_ERROR=
  return 0
 fi
 internet_vendor_adopt_settings || { internet_vendor_error vendor_backup_failed; return 1; }
 internet_vendor_marker disable || return 1
 if ! { uci -q set mtkhnat.global.enable=0 && uci -q set mtkhnat.global.actype=1 && uci -q commit mtkhnat; }; then
  # Only these two deltas were staged after an empty-changes check.
  internet_vendor_revert_options
  internet_vendor_error vendor_config_write_failed; return 1
 fi
 internet_vendor_sfe_disable && internet_vendor_hook_write 0 && internet_vendor_flush || { internet_vendor_error vendor_disable_failed; return 1; }
 internet_vendor_inactive || return 1
 INTERNET_VENDOR_ERROR=
 rm -f "$INTERNET_BACKUP_DIR/vendor-acceleration-reload-required"
}
internet_vendor_module_restore() {
 local module="$1" filename kernel
 internet_vendor_module_loaded "$module" && return 0
 case "$module" in shortcut_fe) filename=shortcut-fe.ko;; shortcut_fe_ipv6) filename=shortcut-fe-ipv6.ko;; shortcut_fe_cm) filename=shortcut-fe-cm.ko;; shortcut_fe_drv) filename=shortcut-fe-drv.ko;; fast_classifier) filename=fast-classifier.ko;; *) return 1;; esac
 kernel="$(uname -r)" || return 1
 case "$kernel" in ''|*[!a-zA-Z0-9_.+-]*|*..*) return 1;; esac
 filename="$(internet_vendor_path "/lib/modules/$kernel/$filename")"
 [ -f "$filename" ] && insmod "$filename" >/dev/null 2>&1 && internet_vendor_module_loaded "$module"
}
internet_vendor_restore() {
 local backup="$INTERNET_BACKUP_DIR/vendor-acceleration-backup" option forced saved current changed=0 module hook selector service enabled restore_runtime=1 hnat_reloaded=0
 INTERNET_VENDOR_ERROR=
 [ -f "$backup" ] || return 0
 internet_vendor_known && internet_vendor_no_pending && internet_vendor_backup_validate || return 1
 [ "$(uci -q get mtkhnat.global)" = "$(internet_vendor_saved runtime section)" ] || { internet_vendor_error vendor_section_changed; return 1; }
 # A prior reload may have succeeded before its selector readback failed.
 # Retain ownership of the module probe's default selector0 across retries.
 if [ -f "$INTERNET_BACKUP_DIR/vendor-acceleration-reload-required" ] &&
  [ "$(cat "$INTERNET_BACKUP_DIR/vendor-acceleration-reload-required")" = restore-hnat-selector ]; then
  hnat_reloaded=1
  internet_vendor_marker restore-hnat-selector || return 1
 else internet_vendor_marker restore || return 1; fi
 for option in enable actype; do
  case "$option" in enable) forced=0;; actype) forced=1;; esac
  current="$(internet_vendor_option "$option")" || return 1
  [ "$current" = "$forced" ] || continue
  saved="$(internet_vendor_saved option "$option")"
  if [ "$saved" = - ]; then uci -q delete "mtkhnat.global.$option"
  else uci -q set "mtkhnat.global.$option=$saved"; fi
  if [ "$?" != 0 ]; then
   internet_vendor_revert_options
   internet_vendor_error vendor_config_restore_failed; return 1
  fi
  changed=1
 done
 if [ "$changed" = 1 ] && ! uci -q commit mtkhnat; then
  internet_vendor_revert_options
  internet_vendor_error vendor_config_restore_failed; return 1
 fi
 for option in enable actype; do
  current="$(internet_vendor_option "$option")" || return 1
  [ "$current" = "$(internet_vendor_saved option "$option")" ] || restore_runtime=0
 done
 # Preserve already loaded administrator modules; reload only modules present
 # in the original snapshot. Unloading the cores flushed both IP flow caches.
 if [ "$restore_runtime" = 1 ]; then
 for module in shortcut_fe shortcut_fe_ipv6 shortcut_fe_cm shortcut_fe_drv fast_classifier; do
  [ "$(internet_vendor_saved module "$module")" = 1 ] || continue
  internet_vendor_module_restore "$module" || { internet_vendor_error vendor_module_restore_failed; return 1; }
 done
 hook="$(internet_vendor_hook_read)" || { internet_vendor_error vendor_runtime_unknown; return 1; }
 if [ "$hook" = 0 ]; then
  saved="$(internet_vendor_saved runtime hook)"
  if [ "$saved" = 1 ] && internet_vendor_hnat_absent; then
   # Stock boot can unload HNAT while enable=0. Its probe enables the hooks,
   # so initialize only to restore a saved enabled runtime, never to verify
   # a disabled policy. The exact module and its live controls are checked.
   internet_vendor_marker restore-hnat-selector || return 1
   module="$(internet_vendor_hnat_module)" && insmod "$module" >/dev/null 2>&1 &&
    [ -d "$(internet_vendor_path /sys/module/mtkhnat)" ] && internet_vendor_known || {
     internet_vendor_error vendor_module_restore_failed; return 1;
    }
   hnat_reloaded=1
  fi
  internet_vendor_hook_write "$saved" && [ "$(internet_vendor_hook_read)" = "$saved" ] || { internet_vendor_error vendor_hook_restore_failed; return 1; }
 fi
 selector="$(internet_vendor_entries_read)" || return 1
 # An unloaded engine has no diagnostic selector to restore. A saved hook0
 # retains that unloaded runtime; do not load a hook-enabled engine for a
 # purely diagnostic setting.
 if { [ "$selector" = 2 ] || { [ "$hnat_reloaded" = 1 ] && [ "$selector" = 0 ]; }; } && ! internet_vendor_hnat_absent; then
  internet_vendor_selector_write "$(internet_vendor_saved runtime selector)" || return 1
 fi
 internet_vendor_marker restore || return 1
 service="$(internet_vendor_service)"; enabled="$(internet_vendor_saved runtime service)"
 if [ "$enabled" = 1 ]; then
  [ -x "$service" ] || return 1
  "$service" enabled >/dev/null 2>&1 || "$service" enable >/dev/null 2>&1 || return 1
  [ "$(internet_vendor_service_enabled)" = 1 ] || return 1
 fi
 fi
 rm -f "$backup" "$INTERNET_BACKUP_DIR/vendor-acceleration-reload-required"
 rmdir "$INTERNET_BACKUP_DIR" 2>/dev/null || :
}

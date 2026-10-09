#!/usr/bin/env python3
"""Integration simulations for the actual shell worker, without OpenWrt."""
import tempfile, subprocess, json
from pathlib import Path
root=Path(__file__).parent
src=(root/'files/usr/sbin/wifi-client-limiter').read_text()
for f in [root/'install.sh',root/'uninstall.sh',root/'files/etc/init.d/wifi-client-limiter',root/'files/usr/sbin/wifi-client-limiter',root/'files/usr/share/wifi-client-limiter/internet-policy.sh',root/'files/usr/share/wifi-client-limiter/posix-cksum.sh',root/'files/usr/share/wifi-client-limiter/upgrade-recovery.sh',root/'files/usr/share/wifi-client-limiter/station-adapter.sh',root/'files/usr/share/wifi-client-limiter/vendor-acceleration.sh',root/'files/usr/libexec/rpcd/wifi-client-limiter']:
 subprocess.run(['sh','-n',str(f)],check=True)
subprocess.run(['node','--check',str(root/'files/www/luci-static/resources/view/wifi-client-limiter.js')],check=True)
for f in (root/'files/usr/share').rglob('*.json'): json.loads(f.read_text())
subprocess.run(['node',str(root/'test-i18n.js')],check=True)
subprocess.run(['python3',str(root/'test-catalogs.py')],check=True)
subprocess.run(['node',str(root/'test-wizard.js')],check=True)
with tempfile.TemporaryDirectory() as td:
 p=Path(td); (p/'state').mkdir()
 body=src[src.index('log()'):src.index('# procd runs')]
 shim=r'''
# This suite isolates existing client eviction. Internet policy has its own tests.
internet_policy() { :; }
json_load() { printf '%s' "$1" > "$FIX/json"; jp=''; }
json_select() {
 if [ "$1" = .. ]; then jp="${jp%/*}"; else jp="$jp/$1"; fi
}
json_query() { python3 - "$FIX/json" "$jp" "$1" <<'END'
import sys,json
v=json.load(open(sys.argv[1]))
for k in sys.argv[2].split('/'):
 if k:v=v[int(k)] if isinstance(v,list) else v[k]
if sys.argv[3]=='keys':print(' '.join(map(str,range(len(v)) if isinstance(v,list) else v.keys())))
else:print(v.get(sys.argv[3],'') if isinstance(v,dict) else '')
END
}
json_get_keys() { eval "$1=\"\$(json_query keys)\""; }
json_get_var() { eval "$1=\"\$(json_query \"$2\")\""; }
load_settings() { enabled=1; dry="$DRY"; max="$MAX"; session="$SESSION"; interval=10; cooldown=0; cooldown_ms=0; window=0; networks=guest; white="$WHITE"; }
resolve_interfaces() { echo ap0; echo ap1; }
ubus() {
 if [ "$3" = get_clients ]; then
  [ "$FAIL" = "$2" ] && return 1
  cat "$FIX/$2.json"
 else echo "$2 $3 $4" >> "$FIX/kicks"; fi
}
iw() { cat "$FIX/$2.iw"; }
logger() { echo "$*" >> "$FIX/log"; }
'''
 harness=p/'run.sh';harness.write_text('STATE="$FIX/state"\n'+body+'\n'+shim+'\ncycle\n')
 def run(rows,max=2,session=0,white='',dry=0,fail=''):
  for file in ['kicks','log']:(p/file).write_text('')
  for iface in ['ap0','ap1']:
   clients={m:{} for m,i,a in rows if i==iface}
   (p/f'hostapd.{iface}.json').write_text(json.dumps({'clients':clients}))
   (p/f'{iface}.iw').write_text(''.join(f'Station {m} (on {iface})\n\tconnected time: {a} seconds\n' for m,i,a in rows if i==iface))
  import os
  env=dict(os.environ,FIX=td,MAX=str(max),SESSION=str(session),WHITE=white,DRY=str(dry),FAIL=fail)
  subprocess.run(['sh',str(harness)],check=True,env=env)
  return (p/'kicks').read_text(),(p/'log').read_text()
 a='00:00:00:00:00:01';b='00:00:00:00:00:02';c='00:00:00:00:00:03';d='00:00:00:00:00:04'
 rows=[(a,'ap0',100),(b,'ap0',80),(c,'ap1',50),(d,'ap1',10)]
 k,l=run(rows);assert a in k and b in k and c not in k and d not in k
 k,l=run(rows,max=4);assert not k
 k,l=run(rows,max=2,white=a);assert a not in k and b in k and c not in k
 k,l=run(rows,max=0,session=90);assert a in k and b not in k
 k,l=run(rows,max=2,session=90);assert a in k and b in k and c not in k
 k,l=run(rows,dry=1);assert not k and 'TEST:' in l
 k,l=run(rows,fail='hostapd.ap1');assert not k
 k,l=run([(a,'ap0',100),(a,'ap1',90),(b,'ap0',20)],max=1);assert k.count('del_client')==2 and b not in k
 assert '"ban_time":0' in k
 print('PASS: syntax/JSON and 8 worker integration simulations')
subprocess.run(['python3', str(root/'test-cooldown.py')], check=True)
subprocess.run(['python3', str(root/'test-readmission.py')], check=True)
subprocess.run(['python3', str(root/'test-posix-cksum.py')], check=True)
subprocess.run(['python3', str(root/'test-fw3-restoration.py')], check=True)
subprocess.run(['python3', str(root/'test-upgrade-recovery.py')], check=True)
subprocess.run(['python3', str(root/'test-internet.py')], check=True)
subprocess.run(['python3', str(root/'test-vendor-acceleration.py')], check=True)
subprocess.run(['python3', str(root/'test-vendor-hook-reader.py')], check=True)
subprocess.run(['python3', str(root/'test-package-lifecycle.py')], check=True)
subprocess.run(['python3', str(root/'test-mtk-driver.py')], check=True)
subprocess.run(['python3', str(root/'test-jshn-discovery.py')], check=True)
subprocess.run(['python3', str(root/'test-rpc-client-discovery.py')], check=True)
subprocess.run(['python3', str(root/'test-internet-jshn.py')], check=True)
subprocess.run(['python3', str(root/'test-mtk-policy.py')], check=True)
subprocess.run(['python3', str(root/'helper/test-mtk-acl-query.py')], check=True)

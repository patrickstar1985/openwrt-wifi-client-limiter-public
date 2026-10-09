#!/usr/bin/env python3
"""Focused read-only/private-data tests; optionally use exact router ash/tools.

python3 test-diagnose-upgrade-lock.py --firmware-root /tmp/gl483-discovery --qemu /tmp/gl-acl-qemu/usr/bin/qemu-aarch64-static
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--firmware-root', type=Path)
parser.add_argument('--qemu')
options, remaining = parser.parse_known_args()
if bool(options.firmware_root) != bool(options.qemu):
    parser.error('Supply both --firmware-root and --qemu')

API = r'''import json,os,pathlib,sys
root=pathlib.Path(os.environ['DIAG_FIXTURE']); name=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]
with (root/'calls').open('a') as output: output.write(json.dumps([name,*args])+'\n')
if name=='ubus' and args==['-t','5','call','service','list','{"name":"wifi-client-limiter"}']:
    print((root/'procd.json').read_text());sys.exit(int(os.environ.get('UBUS_STATUS','0')))
if name=='sleep' and args==['6']:
    mode=os.environ.get('CHANGE_OWNER','')
    if mode=='exited': (root/'state/policy-lock/pid').write_text('999\n')
    if mode=='reused':
        (root/'proc/101/cmdline').write_bytes(b'/bin/sh\0-c\0NEVER_PRINT_REUSED_PAYLOAD\0')
        text=(root/'proc/101/stat').read_text().rsplit(') ',1)
        fields=text[1].split();fields[0]='Z';fields[19]='9999'
        (root/'proc/101/stat').write_text(text[0]+') '+' '.join(fields)+'\n')
    sys.exit(0)
(root/'UNEXPECTED_COMMAND').write_text(json.dumps([name,*args]));sys.exit(99)
'''

JSONFILTER = r'''import json,sys
args=sys.argv[1:]
if len(args)!=4 or args[0]!='-s' or args[2]!='-e':sys.exit(1)
try:data=json.loads(args[1])
except ValueError:sys.exit(1)
expression=args[3]
if expression.startswith('@.'):
    values=[data.get(expression[2:])]
elif expression in ('@["wifi-client-limiter"].instances.*.pid','@["wifi-client-limiter"].instances.*.running'):
    key=expression.rsplit('.',1)[1]
    values=[item.get(key) for item in data.get('wifi-client-limiter',{}).get('instances',{}).values()]
else:sys.exit(1)
for value in values:
    if value is None:continue
    if isinstance(value,bool):print('true' if value else 'false')
    elif isinstance(value,(str,int)):print(value)
    else:sys.exit(1)
'''


class UpgradeLockDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wcl-lock-diag-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        for directory in ('bin','state/policy-lock','state/lock','backup/cksum-recovery-0.5.4','helpers','proc'):
            (self.path/directory).mkdir(parents=True,exist_ok=True)
        self.shell = [shutil.which('sh')]
        native = None
        if options.firmware_root:
            stock=options.firmware_root.resolve()
            native=[str(Path(options.qemu).resolve()),'-L',str(stock),str(stock/'bin/busybox')]
            self.shell=native+['ash']
        for app in ('awk','tr','cat','wc','sha256sum'):
            command=native+[app] if native else [shutil.which(app)]
            self.assertTrue(command[-1])
            self.program(app,'#!/bin/sh\nexec '+shlex.join(command)+' "$@"\n')
        if native:
            command=native[:-1]+[str(options.firmware_root.resolve()/'usr/bin/jsonfilter')]
            self.program('jsonfilter','#!/bin/sh\nexec '+shlex.join(command)+' "$@"\n')
        else:
            self.program('jsonfilter','#!'+sys.executable+'\n'+JSONFILTER)
        for name in ('ubus','sleep','opkg','uci','fw3','fw4','nft','cp','mv','rm','mkdir','rmdir','kill','logger'):
            self.program(name,'#!'+sys.executable+'\n'+API)
        self.write('calls','')
        self.write('package-status','Package: unrelated\nVersion: NEVER_PRINT_OTHER_VERSION\nStatus: install ok installed\n\nPackage: luci-app-wifi-client-limiter\nVersion: 0.5.5-1\nStatus: install ok installed\n\n')
        self.write('config',"config limiter 'main'\n option enabled '1'\n option dry_run '0'\n option guest_internet '0'\n list whitelist '02:00:00:00:00:AB'\n option secret 'NEVER_PRINT_CONFIG'\n")
        self.write('state/policy-lock/pid','101\n')
        self.write('state/service-status.json',json.dumps({'service_state':'running','pid':101,'error':'','detail':'NEVER_PRINT_DRIVER_DETAIL'}))
        self.write('state/internet-status.json',json.dumps({'internet_state':'error','error':'policy_busy','active':False,'offload_disabled':True,'firewall_backend':'fw3','nft_family':'netdev','secret':'NEVER_PRINT_STATUS'}))
        self.write('procd.json',json.dumps({'wifi-client-limiter':{'instances':{'worker':{'pid':101,'running':True,'command':['NEVER_PRINT_UBUS_ARG'],'env':{'PRIVATE':'NEVER_PRINT_ENV'}}}},'unrelated':{'instances':{'other':{'pid':880,'running':True}}}}))
        self.write('proc/uptime','1200.20 600.00\n')
        self.process(101,1,'S',1000,'do_wait',['/bin/sh','/usr/sbin/wifi-client-limiter','--daemon','NEVER_PRINT_WORKER_ARG'])
        self.process(102,101,'S',1001,'do_wait',['/bin/sh','/usr/share/wifi-client-limiter/internet-policy.sh','--cleanup'])
        self.process(103,102,'D',1002,'mutex_lock',['/sbin/fw3','reload','NEVER_PRINT_FW3_ARG'])
        self.process(104,103,'S',1003,'do_wait',['/bin/sh','-c','NEVER_PRINT_SHELL_PAYLOAD'])
        self.process(120,1,'S',900,'do_wait',['/bin/opkg','install','/tmp/NEVER_PRINT_IPK_PATH'])
        self.process(130,1,'S',800,'poll',['/bin/sh','-c','NEVER_PRINT_OTHER_COMMAND'])
        self.process(105,103,'S',1004,'seq_read',['/bin/cat','/sys/kernel/debug/hnat/hook_toggle','NEVER_PRINT_CAT_ARG'])
        self.process(106,102,'S',1005,'do_wait',['/bin/busybox','ash','/etc/rc.common','/etc/init.d/wifi-client-limiter','cleanup','NEVER_PRINT_SERVICE_ARG'])
        for file in ('backup/offload-backup','backup/native-flowoffload-backup','backup/cksum-recovery-0.5.4/offload-backup.original','backup/cksum-recovery-0.5.4/firewall.original','state/station-bans','state/recent','state/internet-uci-edits'):
            self.write(file,'NEVER_PRINT_JOURNAL 02:00:00:00:00:AB\n')
        for file in ('internet-policy.sh','posix-cksum.sh','upgrade-recovery.sh','package-hooks'):
            self.write('helpers/'+file,'NEVER_SOURCE_RUNTIME_HELPER\n')
        self.write('backup/vendor-acceleration-reload-required','NEVER_PRINT_VENDOR_MARKER\n')

    def program(self,name,text):
        path=self.path/'bin'/name;path.write_text(text);path.chmod(0o755)

    def write(self,name,text):
        (self.path/name).write_text(text)

    def process(self,pid,ppid,state,start,wchan,args):
        path=self.path/'proc'/str(pid);path.mkdir(exist_ok=True)
        fields=[state,str(ppid)]+['0']*18;fields[19]=str(start)
        (path/'stat').write_text(f'{pid} (private name ) allowed parentheses) '+ ' '.join(fields)+'\n')
        (path/'cmdline').write_bytes(b'\0'.join(arg.encode() for arg in args)+b'\0')
        (path/'wchan').write_text(wchan+'\n')

    def run_report(self,**extra):
        before={p.relative_to(self.path):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.path.rglob('*') if p.is_file() and p.name!='calls'}
        env=dict(os.environ,PATH=str(self.path/'bin'),DIAG_FIXTURE=str(self.path),DIAG_PROC_ROOT=str(self.path/'proc'),DIAG_STATE=str(self.path/'state'),DIAG_BACKUP=str(self.path/'backup'),DIAG_HELPERS=str(self.path/'helpers'),DIAG_CONFIG=str(self.path/'config'),DIAG_PACKAGE_STATUS=str(self.path/'package-status'),**extra)
        done=subprocess.run(self.shell+[str(ROOT/'scripts/diagnose-upgrade-lock.sh')],env=env,text=True,capture_output=True,timeout=20)
        self.assertEqual(done.returncode,0,done.stderr)
        self.assertEqual(done.stderr,'')
        self.assertFalse((self.path/'UNEXPECTED_COMMAND').exists(),done.stdout)
        self.assertNotIn('NEVER_',done.stdout)
        self.assertNotIn('02:00:00:00:00:AB',done.stdout)
        if not extra.get('CHANGE_OWNER'):
            after={p.relative_to(self.path):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.path.rglob('*') if p.is_file() and p.name!='calls'}
            self.assertEqual(before,after,'diagnosis must not modify inspected files')
        calls=[json.loads(line) for line in (self.path/'calls').read_text().splitlines()]
        self.assertEqual(calls,[['ubus','-t','5','call','service','list','{"name":"wifi-client-limiter"}'],['sleep','6'],['ubus','-t','5','call','service','list','{"name":"wifi-client-limiter"}']])
        return done.stdout

    def test_version_flags_status_owner_and_descendants_without_sensitive_data(self):
        output=self.run_report()
        for expected in ('package_version=0.5.5-1','package_status=install ok installed','saved_enabled=1','saved_dry_run=0','saved_guest_internet=0','internet_error=policy_busy','policy_lock_owner_pid=101','process pid=103 ppid=102 state=D starttime_ticks=1002 wchan=mutex_lock role=fw3 argv=fw3 reload','process pid=104 ppid=103','process pid=120','argv=cat /sys/kernel/debug/hnat/hook_toggle','role=limiter_service argv=shell limiter-service cleanup','backup_vendor-acceleration-reload-required_type=regular','report_complete=1'):
            self.assertIn(expected,output)
        self.assertNotIn('process pid=130',output)
        self.assertNotIn('880',output)
        self.assertEqual(output.count('[snapshot '),2)
        self.assertIn(hashlib.sha256(b'NEVER_SOURCE_RUNTIME_HELPER\n').hexdigest(),output)

    def test_exit_between_snapshots_is_explicit(self):
        output=self.run_report(CHANGE_OWNER='exited')
        self.assertIn('policy_lock_owner_pid=101',output.split('[snapshot 2]')[0])
        self.assertIn('policy_lock_owner_pid=999',output.split('[snapshot 2]')[1])
        self.assertIn('process pid=999 identity_status=unavailable_or_exited',output)

    def test_pid_reuse_and_zombie_state_are_not_hidden(self):
        output=self.run_report(CHANGE_OWNER='reused')
        second=output.split('[snapshot 2]')[1]
        self.assertIn('process pid=101 ppid=1 state=Z starttime_ticks=9999',second)
        self.assertIn('role=unknown argv=shell arguments_omitted',second)

    def test_invalid_owner_and_wchan_are_not_echoed(self):
        self.write('state/policy-lock/pid','NEVER_PRINT_OWNER; $(touch file)\n')
        self.write('proc/101/wchan','NEVER_PRINT_WCHAN; $(secret)\n')
        output=self.run_report()
        self.assertIn('policy_lock_owner_status=invalid_or_unreadable',output)
        self.assertIn('wchan=unavailable',output)

    def test_absent_owner_and_failed_procd_query_do_not_hide_other_processes(self):
        (self.path/'state/policy-lock/pid').unlink()
        output=self.run_report(UBUS_STATUS='5')
        self.assertIn('policy_lock_owner_status=missing_or_nonregular',output)
        self.assertIn('procd_list_status=5',output)
        self.assertIn('process pid=103',output)

    def test_symlink_journals_are_not_read(self):
        path=self.path/'state/station-bans';path.unlink();path.symlink_to(self.path/'config')
        output=self.run_report()
        self.assertIn('runtime_station-bans_type=symlink_not_read',output)

    def test_unexpected_status_fields_are_redacted(self):
        self.write('state/internet-status.json',json.dumps({'internet_state':'NEVER_PRINT_STATE','error':'NEVER_PRINT_ERROR','firewall_backend':'NEVER_PRINT_BACKEND','nft_family':'NEVER_PRINT_FAMILY'}))
        output=self.run_report()
        for field in ('internet_internet_state','internet_error','internet_firewall_backend','internet_nft_family'):
            self.assertIn(field+'_status=redacted',output)


if __name__=='__main__':
    unittest.main(argv=[sys.argv[0],*remaining],verbosity=2)

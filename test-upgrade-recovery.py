#!/usr/bin/env python3
"""Test the current upgrade-recovery library against its exact legacy input.

The hash-pinned policy fixture is retained only because the shipped runtime
recognizes those exact bytes before repairing its legacy ownership journal.
Native integration uses WCL_STOCK_ROOT/WCL_STOCK_QEMU when available. Only
hardware, procd, delays and router paths are simulated. The harness calls the
current library directly and never invokes a package manager or old updater.
"""
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
OLD_SHA = 'c57b5d7e59245517bf593159c496441b64b3ca926682968e3904e2293d1d0674'
MOCK = r'''import json, os, pathlib, shutil, subprocess, sys
p=pathlib.Path(os.environ['FIX']); name=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]; options=json.loads((p/'options.json').read_text())
with (p/'events').open('a') as out: out.write(name+' '+ ' '.join(args)+'\n')
if name=='fw3':
    if options.get('cleanup_fail'): sys.exit(1)
    if 'print' in args: print('*filter\n:FORWARD ACCEPT [0:0]\nCOMMIT')
elif name in ('iptables-save','ip6tables-save'):
    print('*filter\n:FORWARD ACCEPT [0:0]\nCOMMIT')
elif name=='nft':
    present=p/'owned-table'
    if args[:2]==['list','table']:
        sys.exit(0 if args[2]=='netdev' and present.exists() else 1)
    if args[:2]==['delete','table']: present.unlink(missing_ok=True)
elif name in ('rmmod','modprobe','insmod'):
    module=p/'modules/xt_FLOWOFFLOAD'
    if name=='rmmod': shutil.rmtree(module,ignore_errors=True)
    else: module.mkdir(exist_ok=True)
elif name=='service':
    count=int((p/'stops').read_text())+1; (p/'stops').write_text(str(count))
    if options.get('stop_fail'): sys.exit(1)
    result=subprocess.run([str(p/'internet-policy.sh'),'cleanup']).returncode
    sys.exit(result)
elif name=='ubus':
    count=int((p/'polls').read_text())+1; (p/'polls').write_text(str(count))
    if options.get('late_cycle') and count==1:
        result=subprocess.run(json.loads((p/'native-shell.json').read_text())+['-c',
            '. "$UPGRADE_POLICY_FILE"; internet_offload_disable'],
            env=dict(os.environ,PATH=str(p/'bin')),capture_output=True)
        if result.returncode!=1 or b'cksum: not found' not in result.stderr:
            print(result.stderr.decode(),file=sys.stderr);sys.exit(1)
        with (p/'events').open('a') as out: out.write('late-cycle\n')
        print('{"wifi-client-limiter":{"instances":{"main":{"running":true}}}}')
    elif options.get('late_cycle'):
        shutil.rmtree(p/'proc/55555',ignore_errors=True)
        with (p/'events').open('a') as out: out.write('worker-stopped\n')
        print('{}')
    elif options.get('registered'): print('{"wifi-client-limiter":{"instances":{"main":{"running":false}}}}')
    elif options.get('malformed_service'): print(options['malformed_service'])
    else: print('{}')
elif name in ('logger','sleep'): pass
else: sys.exit(2)
'''


class UpgradeRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stock=os.environ.get('WCL_STOCK_ROOT','/tmp/gl483-discovery')
        qemu=os.environ.get('WCL_STOCK_QEMU','/tmp/gl-acl-qemu/usr/bin/qemu-aarch64-static')
        cls.stock=Path(stock); cls.qemu=Path(qemu)
        required=(cls.qemu,cls.stock/'bin/busybox',cls.stock/'sbin/uci',cls.stock/'usr/bin/jsonfilter')
        if not all(path.is_file() for path in required):
            raise unittest.SkipTest('Audited stock GL native tools are unavailable')
        cls.old=(ROOT/'tests/fixtures/legacy-internet-policy.sh').read_bytes()
        if hashlib.sha256(cls.old).hexdigest()!=OLD_SHA: raise AssertionError('Historical helper bytes changed')

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='wcl-upgrade-e2e-');self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name);self.bin=self.path/'bin';self.bin.mkdir()
        for directory in ('config','delta','backup','state','modules/xt_FLOWOFFLOAD','proc'):
            (self.path/directory).mkdir(parents=True)
        prefix=[str(self.qemu),'-L',str(self.stock)]
        self.shell=prefix+[str(self.stock/'bin/busybox'),'ash']
        (self.path/'native-shell.json').write_text(json.dumps(self.shell))
        for app in ('awk','sed','sort','tr','grep','mktemp','chmod','cp','cat','rm','cmp','mkdir','mv','rmdir','wc','sha256sum','id'):
            self.program(app,'#!/bin/sh\nexec '+shlex.join(prefix+[str(self.stock/'bin/busybox'),app])+' "$@"\n')
        self.program('hexdump','#!/bin/sh\nexec '+shlex.join(prefix+[str(self.stock/'bin/busybox'),'hexdump'])+' "$@"\n')
        self.program('uci','#!/bin/sh\nexec '+shlex.join(prefix+[str(self.stock/'sbin/uci'),'-c',str(self.path/'config'),'-t',str(self.path/'delta')])+' "$@"\n')
        self.program('jsonfilter','#!/bin/sh\nexec '+shlex.join(prefix+[str(self.stock/'usr/bin/jsonfilter')])+' "$@"\n')
        for app in ('fw3','iptables-save','ip6tables-save','nft','rmmod','modprobe','insmod','ubus','logger','sleep'):
            self.program(app,'#!'+sys.executable+'\n'+MOCK)
        self.program('service','#!'+sys.executable+'\n'+MOCK)
        self.env=dict(os.environ,PATH=str(self.bin),FIX=str(self.path),
            INTERNET_STATE=str(self.path/'state'),INTERNET_BACKUP_DIR=str(self.path/'backup'),
            INTERNET_MODULE_ROOT=str(self.path/'modules'),INTERNET_VENDOR_HELPER=str(self.path/'vendor.sh'),
            UPGRADE_POLICY_FILE=str(self.path/'internet-policy.sh'),UPGRADE_SERVICE_FILE=str(self.bin/'service'),
            UPGRADE_STATION_FILE=str(self.path/'missing-station'),UPGRADE_FIREWALL_FILE=str(self.path/'config/firewall'),
            UPGRADE_PROC_ROOT=str(self.path/'proc'))
        (self.path/'config/firewall').write_text("config defaults\n option input 'ACCEPT'\n option output 'ACCEPT'\n option forward 'REJECT'\n")
        self.section=self.uci('-q','-X','show','firewall').stdout.decode().splitlines()[0].split('.')[1].split('=')[0]
        self.backup=self.path/'backup/offload-backup'
        self.original=f'{self.section}  flow_offloading -\n{self.section}  flow_offloading_hw -\n'.encode()
        self.backup.write_bytes(self.original)
        (self.path/'backup/offload-reload-required').write_text('disable\n')
        (self.path/'backup/native-flowoffload-backup').write_text('1\n')
        (self.path/'internet-policy.sh').write_bytes(self.old);(self.path/'internet-policy.sh').chmod(0o755)
        (self.path/'vendor.sh').write_text('internet_vendor_disable() { return 0; }\ninternet_vendor_inactive() { return 0; }\ninternet_vendor_restore() { return 0; }\n')
        for name in ('stops','polls'): (self.path/name).write_text('0')
        (self.path/'options.json').write_text('{}');(self.path/'events').write_text('')
        (self.path/'owned-table').touch();(self.path/'openwrt_release').touch()
        self.script=self.path/'prepare.sh'
        self.script.write_text(
            'set -u\numask 077\n'
            + 'UPGRADE_CHECKSUM_FILE=' + shlex.quote(str(ROOT/'files/usr/share/wifi-client-limiter/posix-cksum.sh')) + '\n'
            + '. ' + shlex.quote(str(ROOT/'files/usr/share/wifi-client-limiter/upgrade-recovery.sh')) + '\n'
            + "trap 'upgrade_recovery_finish' 0\n"
            + 'upgrade_recovery_prepare || exit 1\n'
            + 'printf "ready\\n" > ' + shlex.quote(str(self.path/'prepared')) + '\n')

    def program(self,name,source):
        path=self.bin/name;path.write_text(source);path.chmod(0o755)

    def uci(self,*args):
        return subprocess.run([str(self.bin/'uci'),*args],env=self.env,capture_output=True,check=True)

    def options(self,**values): (self.path/'options.json').write_text(json.dumps(values))

    def worker(self):
        path=self.path/'proc/55555';path.mkdir();(path/'cmdline').write_bytes(b'/bin/sh\0/usr/sbin/wifi-client-limiter\0')

    def run_upgrade(self):
        self.firewall_before=(self.path/'config/firewall').read_bytes()
        return subprocess.run(self.shell+[str(self.script)],env=self.env,
                              capture_output=True,timeout=50)

    def assert_stopped(self,result):
        self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse((self.path/'prepared').exists())
        self.assertEqual((self.path/'config/firewall').read_bytes(),self.firewall_before)

    def assert_success(self,result):
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((self.path/'prepared').read_text(),'ready\n')
        archive=self.path/'backup/cksum-recovery-0.5.4'
        self.assertEqual((archive/'offload-backup.original').read_bytes(),self.original)
        self.assertEqual((archive/'firewall.original').read_bytes(),self.firewall_before)
        self.assertEqual((self.path/'config/firewall').read_bytes(),self.firewall_before)
        self.assertFalse(self.backup.exists())
        self.assertFalse((self.path/'owned-table').exists())
        self.assertTrue((self.path/'modules/xt_FLOWOFFLOAD').is_dir())
        self.assertEqual((archive/'offload-backup.original').stat().st_mode&0o777,0o600)
        self.assertEqual((archive/'firewall.original').stat().st_mode&0o777,0o600)

    def test_observed_missing_options_recover_then_normal_cleanup(self):
        self.assert_success(self.run_upgrade())
        self.assertGreaterEqual(int((self.path/'stops').read_text()),1)

    def test_late_old_cycle_is_repaired_only_after_worker_quiescence(self):
        self.options(late_cycle=True);self.worker()
        self.assert_success(self.run_upgrade())
        events=(self.path/'events').read_text()
        self.assertLess(events.index('late-cycle'),events.index('worker-stopped'))

    def test_changed_flags_and_pending_deltas_are_preserved(self):
        self.uci('set',f'firewall.{self.section}.flow_offloading=0')
        self.uci('commit','firewall')
        result=self.run_upgrade();self.assert_stopped(result)
        self.assertEqual(self.backup.read_bytes(),self.original)

    def test_pending_firewall_changes_refuse_recovery(self):
        self.uci('set',f'firewall.{self.section}.input=REJECT')
        pending=self.uci('-q','changes','firewall').stdout
        self.assertTrue(pending)
        self.assert_stopped(self.run_upgrade())
        self.assertEqual(self.uci('-q','changes','firewall').stdout,pending)

    def test_unrecognized_journal_rows_are_preserved(self):
        for rows in (self.original+b'other flow_offloading -\n',
                     self.original+self.original.splitlines(keepends=True)[0],
                     self.original.replace(b'flow_offloading -',b'flow_offloading 0',1)):
            with self.subTest(rows=rows):
                self.backup.write_bytes(rows);self.assert_stopped(self.run_upgrade())
                self.assertEqual(self.backup.read_bytes(),rows)

    def test_changed_section_refuses_recovery(self):
        self.backup.write_bytes(self.original.replace(self.section.encode(),b'other'))
        self.assert_stopped(self.run_upgrade())

    def test_wrong_marker_and_vendor_journal_refuse_recovery(self):
        (self.path/'backup/offload-reload-required').write_text('restore\n')
        self.assert_stopped(self.run_upgrade())
        (self.path/'backup/offload-reload-required').write_text('disable\n')
        (self.path/'backup/vendor-acceleration-backup').write_text('preserved\n')
        self.assert_stopped(self.run_upgrade())

    def test_unrecognized_old_helper_refuses_recovery(self):
        (self.path/'internet-policy.sh').write_bytes(self.old+b'\n')
        self.assert_stopped(self.run_upgrade())

    def test_checksum_failure_refuses_upgrade(self):
        self.program('awk','#!/bin/sh\nexit 7\n')
        self.assert_stopped(self.run_upgrade())
        self.assertEqual(self.backup.read_bytes(),self.original)

    def test_stop_or_cleanup_failure_refuses_readiness(self):
        for options in ({'stop_fail':True},{'cleanup_fail':True}):
            with self.subTest(options=options):
                self.options(**options);self.assert_stopped(self.run_upgrade())

    def test_orphan_worker_and_registered_procd_service_refuse_upgrade(self):
        self.worker();result=self.run_upgrade();self.assert_stopped(result)
        self.assertIn(b'old worker did not stop',result.stderr)
        self.assertFalse(self.backup.exists())
        shutil.rmtree(self.path/'proc/55555');self.options(registered=True)
        result=self.run_upgrade();self.assert_stopped(result)
        self.assertIn(b'old worker did not stop',result.stderr)

    def test_nonobject_service_replies_refuse_upgrade(self):
        for reply in ('[]','true','"service"','null'):
            with self.subTest(reply=reply):
                self.options(malformed_service=reply)
                self.assert_stopped(self.run_upgrade())
                self.assertGreater(int((self.path/'polls').read_text()),0)
                self.assertFalse(self.backup.exists())

    def test_missing_old_helper_with_existing_state_refuses_upgrade(self):
        (self.path/'internet-policy.sh').unlink()
        self.assert_stopped(self.run_upgrade())
        self.assertEqual(self.backup.read_bytes(),self.original)
        (self.bin/'service').unlink()
        self.assert_stopped(self.run_upgrade())
        for path in (self.path/'backup').iterdir(): path.unlink()
        self.assert_stopped(self.run_upgrade())

    def test_missing_helper_clean_fresh_install_is_allowed(self):
        (self.path/'internet-policy.sh').unlink();(self.bin/'service').unlink()
        for path in (self.path/'backup').iterdir(): path.unlink()
        (self.path/'owned-table').unlink()
        result=self.run_upgrade()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((self.path/'prepared').read_text(),'ready\n')
        self.assertEqual((self.path/'config/firewall').read_bytes(),self.firewall_before)


if __name__=='__main__': unittest.main()

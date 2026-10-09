#!/usr/bin/env python3
"""Native GL fixtures for the current package lifecycle. Never targets a router.

Only hardware APIs and absolute entry-point paths are mapped. Production
cleanup bodies, vendor state reader, native BusyBox, UCI and opkg stay intact.
"""
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
VERSION = re.search(r"^PKG_VERSION:=(.+)$", (ROOT / "Makefile").read_text(), re.M).group(1)
IPK = Path(os.environ.get("WCL_TEST_IPK", ROOT / f"packages/{VERSION}/luci-app-wifi-client-limiter_{VERSION}-1_all.ipk"))

def load_module(name, filename):
    spec=importlib.util.spec_from_file_location(name,ROOT/filename)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


PLATFORM = r'''import json,os,pathlib,subprocess,sys
p=pathlib.Path(os.environ['UNINSTALL_FIX']);name=pathlib.Path(sys.argv[0]).name;args=sys.argv[1:]
options=json.loads((p/'options.json').read_text())
with (p/'events').open('a') as output:output.write(json.dumps([name,*args])+'\n')
def uci(key):
    return subprocess.run([str(p/'bin/uci'),'-q','get',key],capture_output=True,text=True).stdout.strip()
if name=='fw3':
    if options.get('cleanup_fail'):sys.exit(1)
    enabled=uci('firewall.main.flow_offloading')=='1';hardware=uci('firewall.main.flow_offloading_hw')=='1'
    if 'print' in args:
        print('*filter\n:FORWARD ACCEPT [0:0]')
        if enabled:print('-A FORWARD -j FLOWOFFLOAD'+(' --hw' if hardware else ''))
        print('COMMIT')
    elif 'reload' not in args:sys.exit(99)
elif name in ('iptables-save','ip6tables-save'):
    print('*filter\n:FORWARD ACCEPT [0:0]')
    if uci('firewall.main.flow_offloading')=='1':print('-A FORWARD -j FLOWOFFLOAD'+(' --hw' if uci('firewall.main.flow_offloading_hw')=='1' else ''))
    print('COMMIT')
elif name=='nft':
    if args[:2]==['list','table']:sys.exit(0 if args[2]=='netdev' and (p/'owned-table').exists() else 1)
    if args[:2]==['delete','table']:(p/'owned-table').unlink(missing_ok=True)
    else:sys.exit(99)
elif name=='modprobe':(p/'sys/module/xt_FLOWOFFLOAD').mkdir(exist_ok=True)
elif name=='iwpriv':sys.exit(1 if options.get('station_cleanup_fail') else 0)
elif name=='logger':pass
else:sys.exit(99)
'''


class NativePackageFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stock=Path(os.environ.get('WCL_STOCK_ROOT','/tmp/gl483-discovery'))
        cls.qemu=Path(os.environ.get('WCL_STOCK_QEMU','/tmp/gl-acl-qemu/usr/bin/qemu-aarch64-static'))
        if not all(p.is_file() for p in (cls.qemu,cls.stock/'bin/busybox',cls.stock/'sbin/uci',cls.stock/'usr/bin/jsonfilter')):
            raise unittest.SkipTest('Exact GL native runtime is unavailable')
        cls.vendor_module=load_module('uninstall_vendor_fixture','test-vendor-acceleration.py')
        with tarfile.open(IPK) as package:
            data=package.extractfile('./data.tar.gz').read()
        with tarfile.open(fileobj=io.BytesIO(data)) as package:
            cls.payload={member.name.lstrip('./'):package.extractfile(member).read() for member in package.getmembers() if member.isfile()}

    def setUp(self):
        self.vendor=self.vendor_module.VendorTests();self.vendor.setUp();self.addCleanup(self.vendor.tearDown)
        self.path=self.vendor.root;self.bin=self.path/'bin'
        (self.bin/'python3').symlink_to(sys.executable)
        for name in ('config','delta','state','backup','helpers'):(self.path/name).mkdir()
        self.prefix=[str(self.qemu),'-L',str(self.stock)]
        self.shell=self.prefix+[str(self.stock/'bin/busybox'),'ash']
        for name in ('awk','sed','sort','tr','grep','mktemp','chmod','cp','cat','rm','cmp','mkdir','mv','rmdir','wc','tail','id','sleep','date','find','tar'):
            if name=='cat':continue
            self.program(name,'#!/bin/sh\nexec '+shlex.join(self.prefix+[str(self.stock/'bin/busybox'),name])+' "$@"\n')
        self.program('cat', '#!' + sys.executable + '\n' + self.vendor_module.MOCK.split('\n', 1)[1])
        self.program('uci','#!/bin/sh\nexec '+shlex.join(self.prefix+[str(self.stock/'sbin/uci'),'-c',str(self.path/'config'),'-t',str(self.path/'delta')])+' "$@"\n')
        self.program('jsonfilter','#!/bin/sh\nexec '+shlex.join(self.prefix+[str(self.stock/'usr/bin/jsonfilter')])+' "$@"\n')
        self.program('hexdump','#!/bin/sh\nexec '+shlex.join(self.prefix+[str(self.stock/'bin/busybox'),'hexdump'])+' "$@"\n')
        sha=self.prefix+[str(self.stock/'bin/busybox'),'sha256sum']
        self.program('sha256sum','#!'+sys.executable+'\nimport subprocess,sys\nif len(sys.argv)>1 and sys.argv[-1].endswith("mtkhnat.ko"):print("19751ae1fd69e351b26900f983ece10ae99f6f8a6dc07d22112d0c9dbbc6c65f  "+sys.argv[-1])\nelse:sys.exit(subprocess.call('+repr(sha)+'+sys.argv[1:]))\n')
        for name in ('fw3','iptables-save','ip6tables-save','nft','modprobe','iwpriv','logger'):
            self.program(name,'#!'+sys.executable+'\n'+PLATFORM)
        for name in ('internet-policy.sh','vendor-acceleration.sh','station-adapter.sh','posix-cksum.sh','package-hooks'):
            file=self.path/'helpers'/name;file.write_bytes(self.payload['usr/share/wifi-client-limiter/'+name]);file.chmod(0o755)
        (self.path/'config/firewall').write_text("config defaults 'main'\n option input 'ACCEPT'\n option output 'ACCEPT'\n option forward 'REJECT'\n option flow_offloading '0'\n option flow_offloading_hw '0'\n option disable_ipv6 '0'\n")
        (self.path/'config/mtkhnat').write_text("config mtkhnat 'global'\n option enable '0'\n option actype '1'\n")
        (self.path/'config/network').write_text("config interface 'wan'\n option proto 'dhcp'\n")
        (self.path/'config/wifi-client-limiter').write_text("config limiter 'main'\n option enabled '1'\n option dry_run '0'\n option guest_internet '0'\n")
        (self.path/'owned-table').touch()
        (self.path/'options.json').write_text('{}');(self.path/'events').write_text('')
        (self.path/'state/service-status.json').write_text('{"service_state":"running","pid":0}')
        for name in self.vendor_module.MODULES:
            shutil.rmtree(self.path/'sys/module'/name,ignore_errors=True)
        state=self.vendor.read();state.update(hook=0,selector=2,service_enabled=False,flows=[0,0],dmesg='[0.000] boot\n'*8192)
        self.vendor.write(state)
        backup='WCL_VENDOR_ACCELERATION_V1\noption enable 1\noption actype 0\nruntime section mtkhnat\nruntime hook 1\nruntime selector 0\nruntime service 1\n'
        backup+=''.join('module '+name+' '+('1' if name in ('shortcut_fe','shortcut_fe_ipv6','shortcut_fe_cm') else '0')+'\n' for name in self.vendor_module.MODULES)
        (self.path/'backup/vendor-acceleration-backup').write_text(backup)
        (self.path/'backup/native-flowoffload-backup').write_text('1\n')
        self.env=dict(os.environ,PATH=str(self.bin),UNINSTALL_FIX=str(self.path),
            STATE=str(self.path/'state'),INTERNET_STATE=str(self.path/'state'),
            INTERNET_BACKUP_DIR=str(self.path/'backup'),INTERNET_MODULE_ROOT=str(self.path/'sys/module'),
            INTERNET_VENDOR_ROOT=str(self.path),INTERNET_VENDOR_HELPER=str(self.path/'helpers/vendor-acceleration.sh'),
            INTERNET_CHECKSUM_HELPER=str(self.path/'helpers/posix-cksum.sh'),
            WCL_UNINSTALL_POLICY=str(self.path/'helpers/internet-policy.sh'),
            WCL_UNINSTALL_STATION=str(self.path/'helpers/station-adapter.sh'))
        self.prepare_package_hooks()
        fingerprint=self.run_shell('. "$WCL_UNINSTALL_POLICY"; internet_defaults_fingerprint main',check=True).stdout.strip()
        (self.path/'backup/offload-backup').write_text(f'main {fingerprint} flow_offloading 1\nmain {fingerprint} flow_offloading_hw 1\n')

    def program(self,name,body):
        file=self.bin/name;file.unlink(missing_ok=True);file.write_text(body);file.chmod(0o755)

    def prepare_package_hooks(self):
        """Map router entry-point paths only; published cleanup bodies stay intact."""
        shell='#!'+str(Path('/bin/sh'))+'\nexec '+shlex.join(self.shell)+' "$0.native" "$@"\n'
        for name,body in {
            'internet-fixture':'. "$WCL_UNINSTALL_POLICY"\ninternet_cleanup\n',
            'station-fixture':'. '+shlex.quote(str(ROOT/'tests/fixtures/libubox-20210516-jshn.sh'))+'\n. "$WCL_UNINSTALL_STATION"\nstation_mtk_known_driver() { printf "%s\\n" ed66a1502e819e4cc8424e50ce9c982905dabd1053bdfa7c47a7ec571652cb77; }\nstation_mtk_cleanup\n',
        }.items():
            self.program(name,shell);(self.bin/(name+'.native')).write_text(body)
        init=self.payload['etc/init.d/wifi-client-limiter'].decode()
        init=init.replace('/usr/share/wifi-client-limiter/internet-policy.sh',str(self.bin/'internet-fixture')).replace('/usr/share/wifi-client-limiter/station-adapter.sh',str(self.bin/'station-fixture')).replace('directory=/tmp/wifi-client-limiter','directory='+str(self.path/'state'))
        self.program('init-fixture',shell);(self.bin/'init-fixture.native').write_text(init+'\n[ "$1" = stop ] || exit 1\nservice_stopped\n')
        hook=self.payload['usr/share/wifi-client-limiter/package-hooks'].decode()
        for original,replacement in {
            '/etc/init.d/wifi-client-limiter':self.bin/'init-fixture',
            '/usr/share/wifi-client-limiter/internet-policy.sh':self.bin/'internet-fixture',
            '/usr/share/wifi-client-limiter/station-adapter.sh':self.bin/'station-fixture',
            '/etc/config/wifi-client-limiter':self.path/'config/wifi-client-limiter',
        }.items():hook=hook.replace(original,str(replacement))
        (self.path/'package-hooks-fixture').write_text(hook)

    def run_shell(self,body,check=False):
        return subprocess.run(self.shell+['-c',body],env=self.env,text=True,capture_output=True,check=check,timeout=20)



class NativeOpkgRuntime(unittest.TestCase):
    """Audited GL package-manager binary for an isolated temporary root."""
    @classmethod
    def setUpClass(cls):
        cls.stock=Path(os.environ.get('WCL_STOCK_OPKG_ROOT','/tmp/gl-opkg-native'))
        cls.qemu=Path(os.environ.get('WCL_STOCK_QEMU','/tmp/gl-acl-qemu/usr/bin/qemu-aarch64-static'))
        if not cls.qemu.is_file() or not (cls.stock/'bin/opkg').is_file():
            raise unittest.SkipTest('Exact GL native opkg runtime is unavailable')
        import hashlib
        actual=hashlib.sha256((cls.stock/'bin/opkg').read_bytes()).hexdigest()
        if actual!='4541db950a9f4045cd910d95a8ee6a45e9bd2bb7d588879ffbf6a1de286e31e6':
            raise unittest.SkipTest('Available opkg differs from audited GL 4.8.3 binary')

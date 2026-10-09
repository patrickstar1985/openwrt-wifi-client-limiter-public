#!/usr/bin/env python3
"""Test rendered production nft rules against real packets in Linux namespaces.

No host interfaces, sysctls or firewall rules are changed. The outer process
first creates an isolated router namespace. Peers also have isolated namespaces.
Use --require in CI: missing tools, capabilities or kernel features then fail
instead of being treated as a local environment skip.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
PORT = 41414
SERVER = r'''
import os, selectors, socket, sys, threading
kind, ready = sys.argv[1:]
sel = selectors.DefaultSelector()
for family in (socket.AF_INET, socket.AF_INET6):
    sock = socket.socket(family, socket.SOCK_DGRAM)
    if family == socket.AF_INET6: sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    sock.bind(('0.0.0.0' if family == socket.AF_INET else '::', 41414))
    sel.register(sock, selectors.EVENT_READ)
if kind == 'router':
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(('0.0.0.0', 67)); sel.register(sock, selectors.EVENT_READ)
    for family in (socket.AF_INET, socket.AF_INET6):
        sock = socket.socket(family, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET:
            sock.bind(('', 5353))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                            socket.inet_aton('224.0.0.251')+socket.inet_aton('192.168.50.1'))
        else:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            sock.bind(('::', 5353))
            import struct
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP,
                            socket.inet_pton(socket.AF_INET6,'ff02::fb')+struct.pack('@I',socket.if_nametoindex('br-lan')))
        sel.register(sock, selectors.EVENT_READ)
open(ready,'w').write('ready')
while True:
    for key, mask in sel.select():
        sock = key.fileobj
        data, peer = sock.recvfrom(4096)
        sock.sendto(data, peer)
'''
PROBE = r'''
import socket, sys
family = socket.AF_INET6 if sys.argv[1] == '6' else socket.AF_INET
address, mode = sys.argv[2:4]
sock = socket.socket(family, socket.SOCK_DGRAM)
sock.settimeout(0.7)
if len(sys.argv)>4 and sys.argv[4]: sock.bind((sys.argv[4],0))
port = 41414
if mode == 'dhcp':
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(('0.0.0.0',68)); port=67
if mode == 'mdns':
    port=5353
    if family == socket.AF_INET:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton('192.168.50.10'))
    else:
        index=socket.if_nametoindex('eth0')
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF,index)
        address=(address,port,0,index)
else: address=(address,port)
if mode == 'mdns' and family == socket.AF_INET: address=(address,port)
payload=b'wifi-limiter-native-packet'
try:
    sock.sendto(payload,address)
    data, peer=sock.recvfrom(4096)
    assert data==payload
except (OSError,AssertionError) as error:
    print(type(error).__name__+': '+str(error),file=sys.stderr)
    sys.exit(1)
'''
FLOW = r'''
import socket, sys
sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
sock.bind(('192.168.50.10',31000)); sock.settimeout(0.7)
print('ready',flush=True)
for line in sys.stdin:
    try:
        sock.sendto(b'flow-offload-probe',('198.18.0.2',41414))
        data,peer=sock.recvfrom(4096)
        print('ok' if data==b'flow-offload-probe' else 'bad',flush=True)
    except OSError: print('blocked',flush=True)
'''


def command(*args, check=True, capture=True, **kwargs):
    result = subprocess.run(list(map(str, args)), check=False, text=True,
                            capture_output=capture, timeout=15, **kwargs)
    if check and result.returncode:
        raise AssertionError(f'command failed: {args}\n{result.stdout}\n{result.stderr}')
    return result


class NativeRouter:
    def __init__(self, path):
        self.path = Path(path)
        self.processes = []
        self.peers = {}

    def peer_command(self, peer, *args, **kwargs):
        return command('nsenter', '-t', self.peers[peer], '-n', *args, **kwargs)

    def peer_process(self, peer, *args, **kwargs):
        process = subprocess.Popen(['nsenter', '-t', str(self.peers[peer]), '-n',
                                    *map(str, args)], **kwargs)
        self.processes.append(process)
        return process

    def new_peer(self, name, router_iface, ipv4, ipv6, mac, *, wan=False):
        process = subprocess.Popen(['unshare', '-n', 'sleep', '300'])
        self.processes.append(process)
        original = os.readlink('/proc/self/ns/net')
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            if process.poll() is not None: raise AssertionError('peer namespace creation failed')
            if os.readlink(f'/proc/{process.pid}/ns/net') != original: break
            time.sleep(0.01)
        else: raise AssertionError('peer namespace was not initialized')
        self.peers[name] = process.pid
        # Static fixture addresses must be ready immediately, including the
        # automatically generated link-local addresses used by IPv6 NDP.
        self.peer_command(name, 'sysctl', '-q', '-w', 'net.ipv6.conf.default.accept_dad=0')
        host = 'peer-'+name
        command('ip', 'link', 'add', router_iface, 'type', 'veth', 'peer', 'name', host)
        command('ip', 'link', 'set', host, 'netns', process.pid)
        self.peer_command(name, 'ip', 'link', 'set', host, 'name', 'eth0')
        self.peer_command(name, 'ip', 'link', 'set', 'eth0', 'address', mac)
        self.peer_command(name, 'ip', 'link', 'set', 'lo', 'up')
        self.peer_command(name, 'ip', 'link', 'set', 'eth0', 'up')
        self.peer_command(name, 'ip', 'address', 'add', ipv4, 'dev', 'eth0')
        self.peer_command(name, 'ip', '-6', 'address', 'add', ipv6, 'dev', 'eth0', 'nodad')
        # IPv6 device routes require the router-side interface to be up.
        # Raise both veth ends before installing WAN and peer gateway routes.
        command('ip', 'link', 'set', router_iface, 'up')
        if wan:
            command('ip', 'address', 'add', '198.18.0.1/24', 'dev', router_iface)
            command('ip', '-6', 'address', 'add', '2001:db8:99::1/64', 'dev', router_iface, 'nodad')
            self.peer_command(name, 'ip', 'route', 'add', '192.168.50.0/24', 'via', '198.18.0.1')
            self.peer_command(name, 'ip', '-6', 'route', 'add', 'fd42:50::/64', 'via', '2001:db8:99::1')
            self.peer_command(name, 'ip', '-6', 'address', 'add', 'fd99::2/64', 'dev', 'eth0', 'nodad')
            self.peer_command(name, 'ip', '-6', 'address', 'add', '2001:db8:999::2/64', 'dev', 'eth0', 'nodad')
            command('ip', '-6', 'route', 'add', 'fd99::/64', 'dev', router_iface)
            command('ip', '-6', 'route', 'add', '2001:db8:999::/64', 'dev', router_iface)
        else:
            command('ip', 'link', 'set', router_iface, 'master', 'br-lan')
            self.peer_command(name, 'ip', 'route', 'add', 'default', 'via', '192.168.50.1')
            self.peer_command(name, 'ip', '-6', 'route', 'add', 'default', 'via', 'fd42:50::1')

    def add_vlan_path(self):
        # Both single and double tags have usable WAN routes before policy
        # activation. A drop therefore proves filtering rather than bad setup.
        paths = (
            ('77','br-lan','br-lan.77','eth0','eth0.77'),
            ('88','br-lan.77','br-lan.77.88','eth0.77','eth0.77.88'),
        )
        for tag, router_parent, router_iface, peer_parent, peer_iface in paths:
            command('ip', 'link', 'add', 'link', router_parent, 'name', router_iface, 'type', 'vlan', 'id', tag)
            command('ip', 'link', 'set', router_iface, 'up')
            command('ip', 'address', 'add', '192.168.'+tag+'.1/24', 'dev', router_iface)
            command('ip', '-6', 'address', 'add', 'fd42:'+tag+'::1/64', 'dev', router_iface, 'nodad')
            for peer, suffix in (('guest','10'),('protected','11'),('open','12')):
                address4 = '192.168.'+tag+'.'+suffix
                address6 = 'fd42:'+tag+'::'+suffix
                self.peer_command(peer, 'ip', 'link', 'add', 'link', peer_parent, 'name', peer_iface, 'type', 'vlan', 'id', tag)
                self.peer_command(peer, 'ip', 'link', 'set', peer_iface, 'up')
                self.peer_command(peer, 'ip', 'address', 'add', address4+'/24', 'dev', peer_iface)
                self.peer_command(peer, 'ip', '-6', 'address', 'add', address6+'/64', 'dev', peer_iface, 'nodad')
                self.peer_command(peer, 'ip', 'route', 'add', '192.168.'+tag+'.0/24', 'dev', peer_iface, 'table', tag)
                self.peer_command(peer, 'ip', 'route', 'add', 'default', 'via', '192.168.'+tag+'.1', 'table', tag)
                self.peer_command(peer, 'ip', 'rule', 'add', 'from', address4+'/32', 'table', tag)
                self.peer_command(peer, 'ip', '-6', 'route', 'add', 'fd42:'+tag+'::/64', 'dev', peer_iface, 'table', tag)
                self.peer_command(peer, 'ip', '-6', 'route', 'add', 'default', 'via', 'fd42:'+tag+'::1', 'table', tag)
                self.peer_command(peer, 'ip', '-6', 'rule', 'add', 'from', address6+'/128', 'table', tag)
            self.peer_command('wan', 'ip', 'route', 'add', '192.168.'+tag+'.0/24', 'via', '198.18.0.1')
            self.peer_command('wan', 'ip', '-6', 'route', 'add', 'fd42:'+tag+'::/64', 'via', '2001:db8:99::1')

    def start_server(self, peer=None):
        ready = self.path/('ready-'+(peer or 'router'))
        args = ['python3', '-u', '-c', SERVER, 'peer' if peer else 'router', str(ready)]
        if peer: process = self.peer_process(peer, *args)
        else:
            process = subprocess.Popen(args)
            self.processes.append(process)
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            if process.poll() is not None: raise AssertionError(f'{peer} UDP server exited')
            if ready.exists(): return
            time.sleep(0.01)
        raise AssertionError(f'{peer} UDP server did not become ready')

    def probe(self, peer, destination, *, family=4, mode='echo', allowed=True):
        source = ''
        if mode in ('vlan','qinq'):
            suffix = {'guest':'10','protected':'11','open':'12'}[peer]
            tag = '88' if mode == 'qinq' else '77'
            source = ('fd42:'+tag+'::' if family == 6 else '192.168.'+tag+'.')+suffix
        result = self.peer_command(peer, 'python3', '-c', PROBE,
                                   '6' if family == 6 else '4', destination, mode, source, check=False)
        if (result.returncode == 0) != allowed:
            diagnostics = []
            for description, args in (('addresses', ('ip','-6','address','show')),
                                      ('routes', ('ip','-6','route','show','table','all')),
                                      ('neighbours', ('ip','-6','neigh','show'))):
                snapshot = self.peer_command(peer, *args, check=False)
                diagnostics.append(description+':\n'+snapshot.stdout+snapshot.stderr)
            snapshot = command('ip','-6','neigh','show', check=False)
            diagnostics.append('router IPv6 neighbours:\n'+snapshot.stdout+snapshot.stderr)
            raise AssertionError(f'{peer} IPv{family} {destination} {mode}: '
                                 f'expected {"pass" if allowed else "drop"}\n{result.stderr}\n'+ '\n'.join(diagnostics))

    def close(self):
        for process in reversed(self.processes):
            process.terminate()
        for process in reversed(self.processes):
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()


def render_production_policy(path, backend, *, ipv4_only=False):
    spec = importlib.util.spec_from_file_location('internet_fixtures', ROOT/'test-internet.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    Path(path).mkdir()
    harness = module.InternetHarness(path)
    harness.write('wireless.json', {'radio0': {'interfaces': [
        {'section': 'guest', 'ifname': 'wlan-guest', 'config': {'mode': 'ap'}},
        {'section': 'guest2', 'ifname': 'wlan-protect', 'config': {'mode': 'ap'}},
        {'section': 'admin', 'ifname': 'wlan-open', 'config': {'mode': 'ap'}},
    ]}})
    harness.write('config.json', {'guest': {'network': 'lan'}, 'guest2': {'network': 'lan'}})
    # IPv6 host bits are deliberate, matching netifd's interface-address format.
    local_status = {
        'ipv4-address': [{'address': '192.168.50.1', 'mask': 24}],
        'ipv6-address': [{'address': 'fd42:50::1', 'mask': 64}],
        'ipv6-prefix-assignment': [{'address': 'fd42:8::', 'mask': 60, 'local-address': {}},
                                 {'address': '2001:db8::', 'mask': 48,
                                  'local-address': {'address': '2001:db8:50::1234', 'mask': 64}}],
        'route': [{'target': '::', 'mask': 0}, {'target': '0.0.0.0', 'mask': 0}],
    }
    if ipv4_only:
        local_status['ipv6-address'] = []
        local_status['ipv6-prefix-assignment'] = [
            {'address': 'fd42:8::', 'mask': 60, 'local-address': {}}]
    harness.write('lan.json', local_status)
    harness.harness.write_text('INTERNET_STATE="$FIX/state"\nINTERNET_BACKUP_DIR="$FIX/backup"\n. "$HELPER"\n'+module.SHIM+
                              f'\nINTERNET_BACKEND={backend}; INTERNET_FAMILY=netdev\n'
                              'networks="guest guest2"; white="02:00:00:00:00:11"\ninternet_collect && internet_render\n')
    harness.run()
    return harness.path/'state/internet-policy-next'


def verify_live_policy(path, rules, *, expected=True, ipv4_only=False):
    # Exercise the actual read-only verifier against nft's kernel-derived dump.
    # This catches output normalization that synthetic command doubles miss,
    # including IPv6 prefixes with host bits and changing packet counters.
    source = (ROOT/'files/usr/libexec/rpcd/wifi-client-limiter').read_text()
    source = source.split('\ncase "${1:-}" in\n', 1)[0]
    source = source.replace('/tmp/wifi-client-limiter/internet-policy-applied', str(rules))
    probe = path/'verify-live-policy.sh'
    prefixes = "'4 192.168.50.0/24'"
    if not ipv4_only:
        prefixes += " '6 fd42:50::1/64' '6 2001:db8:50::1234/64'"
    probe.write_text(source + "\nrpc_current_prefixes() { printf '%s\\n' " + prefixes + "; }\n" + '''
rpc_ap_records() { printf '%s\\n' 'wlan-guest guest hostapd' 'wlan-protect guest2 hostapd' 'wlan-open admin hostapd'; }
networks='guest guest2'; white='02:00:00:00:00:11'
rpc_rules_verify
result=$?
if [ "$result" != 0 ]; then
 printf '%s\\n' "Unverified: $reason" 'Generated policy:' >&2
 cat "$1" >&2
 printf '%s\\n' 'Kernel policy:' >&2
 nft -s -nn -y list table netdev wifi_client_limiter >&2
 printf '%s\\n' 'Canonical generated policy:' >&2
 rpc_policy_records < "$1" >&2
 printf '%s\\n' 'Canonical kernel policy:' >&2
 nft -s -nn -y list table netdev wifi_client_limiter | rpc_policy_records >&2
fi
exit "$result"
''')
    result = command('sh', probe, rules, check=False)
    assert (result.returncode == 0) == expected, result.stdout+result.stderr


def inside(backend):
    nft_family = "netdev"
    with tempfile.TemporaryDirectory(prefix='wifi-limiter-native-') as directory:
        path = Path(directory)
        router = NativeRouter(path)
        try:
            command('ip', 'link', 'set', 'lo', 'up')
            command('sysctl', '-q', '-w', 'net.ipv6.conf.default.accept_dad=0')
            command('ip', 'link', 'add', 'br-lan', 'type', 'bridge', 'mcast_snooping', '0')
            command('ip', 'link', 'set', 'br-lan', 'up')
            command('ip', 'address', 'add', '192.168.50.1/24', 'dev', 'br-lan')
            command('ip', '-6', 'address', 'add', 'fd42:50::1/64', 'dev', 'br-lan', 'nodad')
            command('sysctl', '-q', '-w', 'net.ipv4.ip_forward=1')
            command('sysctl', '-q', '-w', 'net.ipv6.conf.all.forwarding=1')
            router.new_peer('guest', 'wlan-guest', '192.168.50.10/24', 'fd42:50::10/64', '02:00:00:00:00:10')
            router.new_peer('protected', 'wlan-protect', '192.168.50.11/24', 'fd42:50::11/64', '02:00:00:00:00:11')
            router.new_peer('open', 'wlan-open', '192.168.50.12/24', 'fd42:50::12/64', '02:00:00:00:00:12')
            router.new_peer('gallery', 'gallery0', '192.168.50.20/24', 'fd42:50::20/64', '02:00:00:00:00:20')
            router.new_peer('wan', 'wan0', '198.18.0.2/24', '2001:db8:99::2/64', '02:00:00:00:00:99', wan=True)
            router.add_vlan_path()
            router.start_server('wan'); router.start_server('gallery'); router.start_server()
            for peer in ('guest','protected','open'):
                for mode in ('vlan','qinq'):
                    router.probe(peer, '198.18.0.2', mode=mode)
                    router.probe(peer, '2001:db8:99::2', family=6, mode=mode)
            command('nft', '-f', '-', input='''table inet fw4 {
 flowtable ft { hook ingress priority 0; devices = { wlan-guest, br-lan, wan0 }; }
 chain forward { type filter hook forward priority 0; policy accept;
  ct state established,related flow add @ft
 }
}
''')
            # Establish a real flowtable-eligible UDP connection before enabling.
            flow = router.peer_process('guest', 'python3', '-u', '-c', FLOW,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            assert flow.stdout.readline().strip() == 'ready'
            def flow_probe():
                flow.stdin.write('probe\n'); flow.stdin.flush()
                return flow.stdout.readline().strip()
            for attempt in range(30):
                assert flow_probe() == 'ok', 'Internet baseline must pass'
                connection = command('conntrack', '-L', '-p', 'udp', '-o', 'extended', check=False)
                assert connection.returncode == 0, connection.stderr
                if any('sport=31000' in line and 'OFFLOAD' in line
                       for line in connection.stdout.splitlines()): break
                time.sleep(0.02)
            else:
                raise AssertionError('Baseline UDP connection was not software offloaded:\n'+connection.stdout)
            rules = render_production_policy(path/'fixtures', backend)
            command('nft', '-c', '-f', rules)
            command('nft', '-f', rules)
            verify_live_policy(path, rules)
            # Keep the baseline flowtable present deliberately. The policy's
            # earlier AP ingress hook must defeat even an already offloaded flow.
            assert flow_probe() == 'blocked', 'A pre-existing offloaded flow bypassed the policy'
            for peer in ('protected', 'open'):
                router.probe(peer, '198.18.0.2')
                router.probe(peer, '2001:db8:99::2', family=6)
                for mode in ('vlan','qinq'):
                    router.probe(peer, '198.18.0.2', mode=mode)
                    router.probe(peer, '2001:db8:99::2', family=6, mode=mode)
            for mode in ('vlan','qinq'):
                router.probe('guest', '198.18.0.2', mode=mode, allowed=False)
                router.probe('guest', '2001:db8:99::2', family=6, mode=mode, allowed=False)
            for address, family in (('198.18.0.2',4), ('2001:db8:99::2',6),
                                    ('fd99::2',6), ('2001:db8:999::2',6)):
                router.probe('guest', address, family=family, allowed=False)
            # Force fresh ARP/IPv6 neighbour discovery, then transfer local data.
            router.peer_command('guest', 'ip', 'neigh', 'flush', 'dev', 'eth0')
            router.peer_command('guest', 'ip', '-6', 'neigh', 'flush', 'dev', 'eth0')
            router.probe('guest', '192.168.50.20')
            router.probe('guest', 'fd42:50::20', family=6)
            router.probe('guest', '192.168.50.1')
            router.probe('guest', 'fd42:50::1', family=6)
            router.probe('guest', '255.255.255.255', mode='dhcp')
            router.probe('guest', '224.0.0.251', mode='mdns')
            router.probe('guest', 'ff02::fb', family=6, mode='mdns')
            # Drops are real packet events rather than string-only rule assertions.
            table = command('nft', '-j', 'list', 'table', nft_family, 'wifi_client_limiter')
            counts = [item['rule']['expr'] for item in json.loads(table.stdout)['nftables'] if 'rule' in item]
            assert any(any(expr.get('counter',{}).get('packets',0)>0 for expr in rule) for rule in counts)
            verify_live_policy(path, rules)
            command('nft', 'add', 'rule', nft_family, 'wifi_client_limiter', 'ap_1', 'counter', 'accept')
            verify_live_policy(path, rules, expected=False)
            # Rule replacement is atomic, repeatable and leaves the native table intact.
            command('nft', '-f', rules)
            verify_live_policy(path, rules)
            command('nft', 'list', 'table', 'inet', 'fw4')
            # Reproduce the reported netifd shape: valid local IPv4, no local
            # IPv6 address and an empty optional allocation in a /60 pool.
            # The rule must still block external packets in both families.
            ipv4_rules = render_production_policy(path/'ipv4-fixtures', backend, ipv4_only=True)
            assert 'fd42:8::' not in ipv4_rules.read_text()
            command('nft', '-c', '-f', ipv4_rules)
            command('nft', '-f', ipv4_rules)
            verify_live_policy(path, ipv4_rules, ipv4_only=True)
            assert flow_probe() == 'blocked'
            router.probe('guest', '192.168.50.20')
            router.probe('guest', '192.168.50.1')
            router.probe('guest', '198.18.0.2', allowed=False)
            router.probe('guest', '2001:db8:99::2', family=6, allowed=False)
            for peer in ('protected', 'open'):
                router.probe(peer, '198.18.0.2')
                router.probe(peer, '2001:db8:99::2', family=6)
            command('nft', 'list', 'table', 'inet', 'fw4')
            command('nft', 'delete', 'table', nft_family, 'wifi_client_limiter')
            assert flow_probe() == 'ok', 'Removing owned policy did not restore Internet'
            for mode in ('vlan','qinq'):
                router.probe('guest', '198.18.0.2', mode=mode)
                router.probe('guest', '2001:db8:99::2', family=6, mode=mode)
            command('nft', 'list', 'table', 'inet', 'fw4')
            print(f'PASS: {backend}/{nft_family}, real IPv4/IPv6 packets, shared bridge, MAC exemptions, local services, '
                  'VLAN/QinQ bypass prevention, empty optional IPv6 allocation, atomic rules and a pre-existing software-offloaded connection')
        finally:
            router.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require', action='store_true')
    parser.add_argument('--backend', choices=('fw4','fw3'), help=argparse.SUPPRESS)
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inside:
        inside(args.backend); return
    missing = [tool for tool in ('nft','ip','unshare','nsenter','sysctl','conntrack') if not shutil.which(tool)]
    problem = 'missing tools: '+', '.join(missing) if missing else ''
    if not problem and os.geteuid() != 0: problem = 'root is required for isolated router namespaces'
    if not problem:
        probe = command('unshare', '-n', 'true', check=False)
        if probe.returncode: problem = 'network namespaces are unavailable: '+probe.stderr.strip()
    if problem:
        print(('FAIL' if args.require else 'SKIP')+': native nft packet tests. '+problem,
              file=sys.stderr if args.require else sys.stdout)
        sys.exit(1 if args.require else 0)
    for backend in (args.backend,) if args.backend else ('fw4','fw3'):
        result = subprocess.run(['unshare','-n',sys.executable,str(Path(__file__).resolve()),
                                 '--inside','--backend',backend], timeout=120)
        if result.returncode: sys.exit(result.returncode)


if __name__ == '__main__':
    main()

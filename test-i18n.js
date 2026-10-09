'use strict';
// Execute the real LuCI view against every shipped translation and a missing language.
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const {makeHarness} = require('./test-luci-harness');
const root = __dirname;
const script = fs.readFileSync(path.join(root, 'files/www/luci-static/resources/view/wifi-client-limiter.js'), 'utf8');
function catalog(lang) {
 const result = new Map(); let id;
 for (const line of fs.readFileSync(path.join(root, 'po', lang, 'wifi-client-limiter.po'), 'utf8').split('\n')) {
  if (line.startsWith('msgid ')) id = JSON.parse(line.slice(6));
  if (line.startsWith('msgstr ') && id) {
   const value = JSON.parse(line.slice(7));
   assert(value.length > 0, 'Empty translation in '+lang+': '+id);
   assert(!result.has(id), 'Duplicate key in '+lang);
   result.set(id,value);
  }
 }
 return result;
}
function findNode(node, predicate) {
 if (predicate(node)) return node;
 for (const child of node.children || []) {
  if (!child || typeof child !== 'object') continue;
  const found = findNode(child, predicate);
  if (found) return found;
 }
 return null;
}
const entries = catalog('en');
const languages = ['en','de','zh_Hans','ru','fr','es','pl','it','pt_BR','tr'];
const definitions = JSON.parse(fs.readFileSync(path.join(root, 'languages.json'), 'utf8'));
assert.deepEqual(definitions.map(l=>l.po),languages);
for (const definition of definitions) {
 if (definition.po !== 'en') assert(fs.statSync(path.join(root, 'files/usr/lib/lua/luci/i18n/wifi-client-limiter.'+definition.runtime+'.lmo')).size>0);
}
assert.equal(definitions.find(l=>l.po==='zh_Hans').runtime, 'zh-cn');
assert.equal(definitions.find(l=>l.po==='pt_BR').runtime, 'pt-br');
const registry = fs.readFileSync(path.join(root,'files/usr/share/wifi-client-limiter/languages'),'utf8').trim().split('\n');
assert.deepEqual(registry, definitions.filter(l=>l.po!=='en').map(l=>l.runtime.replace(/-/g,'_')+'|'+l.label));
const menu = JSON.parse(fs.readFileSync(path.join(root, 'files/usr/share/luci/menu.d/luci-app-wifi-client-limiter.json'), 'utf8'));
const title = menu['admin/services/wifi-client-limiter'].title;
assert(entries.has(title), 'Menu title must have a translation');
// Catalog coverage is checked against every literal translation call, including
// error/fallback paths that do not appear during one successful wizard run.
const sourceKeys = new Set(Array.from(script.matchAll(/_\(('(?:\\.|[^'\\])*')\)/g),
 match => new Function('return ' + match[1])()));
assert.deepEqual(Array.from(sourceKeys).sort(), Array.from(entries.keys()).sort());
async function main() {
for (const lang of languages.concat(['nl'])) {
 const selected = lang === 'nl' ? entries : catalog(lang);
 assert.deepEqual(Array.from(selected.keys()), Array.from(entries.keys()), 'Catalog completeness: '+lang);
 const translate = key => {
  assert(entries.has(key), 'Missing translation: ' + key);
  return selected.get(key);
 };
 const h = makeHarness({translate,stationReply:{clients:[],unavailable:false}});
 await h.instance.load(); await h.instance.render();
 assert(h.instance.root.text().includes(translate('Setup wizard')));
 assert(h.instance.root.text().includes(translate('Select WiFi networks to manage')));
 const apOption=h.instance.wizardMap.options.find(o=>o.name==='network');
 assert.equal(apOption.values[0][0],'guest');
 assert.equal(apOption.values[0][1].text(),'Guest WiFi (radio0, guest)');
 assert(!apOption.values.some(v=>v[0]==='uplink'));
 h.setField('network',['guest']); await h.instance.nextStep();
 assert(h.instance.root.text().includes(translate('Protect important devices')));
 assert(h.instance.root.text().includes(translate('No connected devices were found. Add protected MAC addresses manually if needed.')));
 h.setField('manual_whitelist',['aa:bb:cc:dd:ee:01']);await h.instance.nextStep();
 assert.equal(h.instance.wizardMap.options.find(o=>o.name==='max_clients').datatype,'and(uinteger,range(0,4096))');
 assert.equal(h.instance.wizardMap.options.find(o=>o.name==='reconnect_cooldown').datatype,'and(uinteger,range(0,1800))');
 assert(h.instance.root.text().includes(translate('Reconnect cooldown (seconds)')));
 assert(h.instance.root.text().includes(translate('0 = no pause. After a disconnect, block automatic and manual reconnections on all selected WiFi networks for this duration.')));
 h.setField('reconnect_cooldown','60');
 assert.equal(h.instance.wizardMap.options.find(o=>o.name==='reconnect_window').datatype,'and(uinteger,range(0,86400))');
 assert(h.instance.root.text().includes(translate('Reconnect protection window (seconds)')));
 assert(h.instance.root.text().includes(translate('0 = disabled. For this long after a disconnect, returning devices may rejoin only if guest slots are free and the reconnect cooldown has ended.')));
 h.setField('reconnect_window','300');
 assert(h.instance.root.text().includes(translate('Allow guests to access the Internet')));
 h.setField('guest_internet','0');
 await h.instance.nextStep();
 assert.equal(h.instance.draft.reconnect_cooldown,'60');assert(h.instance.root.text().includes(translate('Reconnect cooldown (seconds)')+'60'));
 assert.equal(h.instance.draft.reconnect_window,'300');assert(h.instance.root.text().includes(translate('Reconnect protection window (seconds)')+'300'));
 assert.equal(h.instance.draft.guest_internet,'0');assert(h.instance.root.text().includes(translate('Allow guests to access the Internet')+translate('Blocked')));
 assert(h.instance.root.text().includes(translate('Save setup and apply')));
 assert(h.instance.root.text().includes('Guest WiFi (radio0, guest)'));
 assert(h.instance.root.text().includes(translate('Operating mode')));
 assert(h.instance.root.text().includes(translate('Test mode only logs actions. Live mode enforces client limits, reconnect pauses and the selected Internet policy.')));
 const operatingMode = h.instance.wizardMap.options.find(o=>o.name==='dry_run');
 assert.deepEqual(operatingMode.values,[['1',translate('Test mode')],['0',translate('Live mode')]]);
 assert.equal(operatingMode.validate('wizard','invalid'),translate('Choose whether to test the settings or enforce them after applying.'));
 await h.instance.cancelWizard();
 const settings=h.instance.settingsMap;
 assert.equal(settings.options.find(o=>o.name==='whitelist').datatype,'macaddr');
 assert.equal(settings.options.find(o=>o.name==='reconnect_cooldown').formvalue(),'0');assert(h.instance.root.text().includes(translate('Reconnect cooldown (seconds)')));
 assert.equal(settings.options.find(o=>o.name==='reconnect_window').formvalue(),'0');assert(h.instance.root.text().includes(translate('Reconnect protection window (seconds)')));
 assert.equal(settings.options.find(o=>o.name==='guest_internet').formvalue(),'1');assert(h.instance.root.text().includes(translate('Internet policy status')));assert(h.instance.root.text().includes(translate('The Internet policy is inactive.')));
 assert(h.instance.root.text().includes(translate('Settings')));
 assert(h.instance.root.text().includes(translate('Start setup wizard')));
 if(h.instance.liveRequest)await h.instance.liveRequest;
 assert(h.instance.root.text().includes(translate('Connected devices')));
 assert(h.instance.root.text().includes(translate('Refresh status')));
 assert(h.instance.connectedDevicesNode.text().includes(translate('No connected WiFi devices were found.')));
 for(const permission of ['allowed','blocked','exempt','unmanaged','test','not_enforced','unknown']) {
  const label=h.instance.devicePermissionText(permission);
  assert(selected.has(Array.from(selected.keys()).find(key=>translate(key)===label)),'Translated device permission: '+permission);
 }
 h.instance.internetStatus={internet_state:'error',reason:'service_error',service_state:'error',service_error:'mtk_driver_unsupported',service_detail:'driver SHA256: 1234'};
 h.instance.renderInternetStatus();
 assert(h.instance.internetStatusNode.text().includes(translate('The limiter service reported an error. Internet restrictions could not be verified.')));
 assert(h.instance.internetStatusNode.text().includes(translate('This MTK WiFi driver is not supported.')));
 assert(h.instance.internetStatusNode.text().includes(translate('Diagnostic details')+': driver SHA256: 1234'));
 assert.deepEqual(h.state.mutations,[]);
 const devices = makeHarness({translate,main:{setup_complete:'1',whitelist:['aa:bb:cc:dd:ee:02']},stationReply:{
  internet_state:'blocked',active:true,unavailable:false,clients:[
   {mac:'aa:bb:cc:dd:ee:01',ifname:'ap0',section:'guest',hostname:'Guest phone',internet_permission:'blocked'},
   {mac:'aa:bb:cc:dd:ee:02',ifname:'ap0',section:'guest',hostname:'Operator phone',internet_permission:'exempt'}
  ]
 }});
 await devices.instance.load();await devices.instance.render();
 if(devices.instance.liveRequest)await devices.instance.liveRequest;
 assert(devices.instance.connectedDevicesNode.text().includes(translate('Protection')));
 assert(devices.instance.connectedDevicesNode.text().includes(translate('Already protected')));
 const protectButton=findNode(devices.instance.connectedDevicesNode,node=>node.tag==='button'&&node.text()===translate('Protect device'));
 assert(protectButton,'Translated protection action: '+lang);
 await protectButton.attrs.click();
 assert(devices.instance.connectedDevicesNode.text().includes(translate('Protection pending')));
 assert(devices.state.notifications.includes(translate('Device added to protected MAC addresses. Save & Apply to activate the exception.')));
 assert.deepEqual(devices.state.mutations,[],'Protection action must remain an unsaved form change');
 assert.equal(devices.state.applies.length,0);
 const whitelistOption=devices.instance.settingsMap.options.find(o=>o.name==='whitelist');
 whitelistOption.getUIElement=()=>null;
 devices.instance.protectConnectedDevice('aa:bb:cc:dd:ee:03',devices.instance.liveGeneration);
 assert(devices.state.notifications.includes(translate('Unable to update protected MAC addresses.')));
 assert.equal(translate(title),selected.get(title));
}
console.log('PASS: 10 complete catalogs, translated settings, live devices and wizard, SSID preservation, native language aliases and English fallback');
}
main().catch(error=>{console.error(error);process.exit(1);});

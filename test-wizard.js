'use strict';
const assert=require('assert');
const {makeHarness,Node,source}=require('./test-luci-harness');
const config='wifi-client-limiter';
const a='aa:bb:cc:dd:ee:01',b='aa:bb:cc:dd:ee:02',c='aa:bb:cc:dd:ee:03';
async function open(options={}) { const h=makeHarness(options);await h.instance.load();await h.instance.render();if(h.instance.liveRequest)await h.instance.liveRequest;return h; }
function assertLiteral(node,text) {
 assert(node.text().includes(text));
 function visit(n) {if(!(n instanceof Node))return;assert(!n.scalarHTML || !n.text().includes(text),'Untrusted text entered a native scalar HTML argument');n.children.forEach(visit);}
 visit(node);
}
function button(h,label) {
 function find(node){if(!(node instanceof Node))return null;if(node.tag==='button'&&node.text()===label)return node;for(const child of node.children){const found=find(child);if(found)return found;}return null;}
 const found=find(h.instance.root);assert(found,'Missing native button: '+label);return found;
}
async function main() {
 let h=await open();assert(h.instance.wizard);assert.equal(h.instance.wizardStep,0);assert.deepEqual(h.state.mutations,[]);
 assert.equal(h.instance.draft.reconnect_cooldown,'0');assert(!('reconnect_cooldown' in h.state.configs[config].main));
 assert.equal(h.instance.draft.reconnect_window,'0');assert(!('reconnect_window' in h.state.configs[config].main));
 assert.equal(h.instance.draft.guest_internet,'1');assert(!('guest_internet' in h.state.configs[config].main));
 assert.equal(h.instance.draft.dry_run,'1');
 assert(!button(h,'Next').disabled);assert(button(h,'Back').disabled);assert(!button(h,'Cancel').disabled);
 await h.instance.nextStep();assert.equal(h.instance.wizardStep,0);assert.equal(h.state.saves,0);assert(h.state.notifications.at(-1).includes('Select at least'));
 h.setField('network',['guest']);await h.instance.nextStep();assert.equal(h.instance.wizardStep,1);assert.deepEqual(h.state.mutations,[]);
 assert(!button(h,'Next').disabled);assert(!button(h,'Back').disabled);
 h.setField('manual_whitelist',[a]);await h.instance.nextStep();h.setField('max_clients','12');h.setField('reconnect_cooldown','60');h.setField('reconnect_window','300');h.setField('guest_internet','0');await h.instance.nextStep();
 const expectedTypes={max_clients:'and(uinteger,range(0,4096))',max_session:'and(uinteger,range(0,604800))',reconnect_cooldown:'and(uinteger,range(0,1800))',reconnect_window:'and(uinteger,range(0,86400))',interval:'and(uinteger,range(2,3600))'};
 for(const map of h.state.maps)for(const option of map.options)if(expectedTypes[option.name])assert.equal(option.datatype,expectedTypes[option.name]);
 assert.equal(h.instance.wizardStep,3);assert.equal(h.instance.draft.max_clients,'12');assert.equal(h.instance.draft.reconnect_cooldown,'60');assert(h.instance.root.text().includes('Reconnect cooldown (seconds)60'));assert.equal(h.state.saves,0);
 assert.equal(h.instance.draft.reconnect_window,'300');assert(h.instance.root.text().includes('Reconnect protection window (seconds)300'));
 assert.equal(h.instance.draft.guest_internet,'0');assert(h.instance.root.text().includes('Allow guests to access the InternetBlocked'));
 assert.equal(h.instance.wizardMap.options.find(o=>o.name==='dry_run').type,h.form.ListValue);
 assert.equal(h.instance.wizardMap.options.find(o=>o.name==='dry_run').formvalue(),'1');
 await h.instance.previousStep();assert.equal(h.instance.wizardMap.options.find(o=>o.name==='guest_internet').formvalue(),'0');await h.instance.nextStep();
 await h.instance.saveWizard();assert.equal(h.state.saves,1);assert.deepEqual(h.state.applies,[true]);assert.equal(h.state.init,1);
 assert(h.state.mutations.every(row=>row[0]===config));assert.equal(h.state.configs[config].main.enabled,'1');assert.equal(h.state.configs[config].main.dry_run,'1');
 assert.equal(h.state.configs[config].main.setup_complete,'1');assert.equal(h.state.configs[config].main.reconnect_cooldown,'60');assert.deepEqual(h.state.configs[config].main.whitelist,[a]);
 assert.equal(h.state.configs[config].main.reconnect_window,'300');
 assert.equal(h.state.configs[config].main.guest_internet,'0');
 h=await open();h.instance.draft.network=['guest'];await h.instance.showWizardStep(3);await h.instance.saveWizard();assert.equal(h.state.saves,1);assert.deepEqual(h.state.applies,[true]);assert.equal(h.state.configs[config].main.reconnect_cooldown,'0');assert.equal(h.state.configs[config].main.reconnect_window,'0');assert(!('whitelist' in h.state.configs[config].main));assert(!h.state.mutations.some(row=>Array.isArray(row[3])&&!row[3].length));
 h=await open({main:{network:['guest'],whitelist:[a],max_clients:'40',max_session:'1800',reconnect_cooldown:'60',reconnect_window:'300',interval:'15',dry_run:'0',guest_internet:'1'}});
 assert(!h.instance.wizard);assert(!button(h,'Start setup wizard').disabled);h.setField('reconnect_cooldown','120');h.setField('reconnect_window','900');h.setField('guest_internet','0');await h.instance.startWizard();assert.equal(h.instance.draft.max_clients,'40');assert.equal(h.instance.draft.max_session,'1800');assert.equal(h.instance.draft.reconnect_cooldown,'120');assert.equal(h.instance.draft.reconnect_window,'900');assert.equal(h.instance.draft.guest_internet,'0');
 assert.equal(h.instance.draft.dry_run,'0','Existing live mode must be the wizard default');
 await h.instance.cancelWizard();assert(!h.instance.wizard);assert.deepEqual(h.state.mutations,[]);assert.equal(h.state.configs[config].main.dry_run,'0');assert.equal(h.state.configs[config].main.reconnect_cooldown,'60');assert.equal(h.state.configs[config].main.reconnect_window,'300');
 assert.equal(h.state.configs[config].main.guest_internet,'1');
 // The last step collects an explicit mode on Back and Save, retaining the
 // current (including unsaved) mode instead of forcing all setups into test.
 for(const storedMode of ['0','1']) {
  h=await open({main:{network:['guest'],dry_run:storedMode}});await h.instance.startWizard();
  await h.instance.showWizardStep(3);assert.equal(h.instance.wizardMap.options.find(o=>o.name==='dry_run').formvalue(),storedMode);
  const chosenMode=storedMode==='0'?'1':'0';h.setField('dry_run',chosenMode);
  await h.instance.previousStep();assert.equal(h.instance.draft.dry_run,chosenMode);
  await h.instance.nextStep();assert.equal(h.instance.wizardMap.options.find(o=>o.name==='dry_run').formvalue(),chosenMode);
  await h.instance.saveWizard();assert.equal(h.state.configs[config].main.dry_run,chosenMode);assert.deepEqual(h.state.applies,[true]);
 }
 h=await open({main:{network:['guest'],dry_run:'1'}});h.setField('dry_run','0');await h.instance.startWizard();assert.equal(h.instance.draft.dry_run,'0');
 await h.instance.showWizardStep(3);await h.instance.saveWizard();assert.equal(h.state.configs[config].main.dry_run,'0');
 h=await open({main:{network:['guest'],dry_run:'0'}});await h.instance.startWizard();await h.instance.showWizardStep(3);await h.instance.saveWizard();assert.equal(h.state.configs[config].main.dry_run,'0','Saving an existing live setup must keep it live');
 h=await open({main:{network:['guest'],dry_run:'invalid'}});await h.instance.startWizard();assert.equal(h.instance.draft.dry_run,'1');
 await h.instance.showWizardStep(3);h.setField('dry_run','invalid');await h.instance.saveWizard();assert.equal(h.state.saves,0);assert.deepEqual(h.state.mutations,[]);
 h.setField('dry_run','0');await h.instance.cancelWizard();assert.equal(h.state.saves,0);assert.deepEqual(h.state.mutations,[]);
 // Cancellation before asynchronous preflight completion must not apply a
 // stale wizard draft. Once saving starts, navigation is blocked until done.
 let releaseCheck;const pendingCheck=new Promise(resolve=>releaseCheck=resolve);
 h=await open({changesHandler:()=>pendingCheck});h.instance.draft.network=['guest'];await h.instance.showWizardStep(3);h.setField('dry_run','0');
 const cancelledSave=h.instance.saveWizard();await Promise.resolve();await Promise.resolve();await h.instance.cancelWizard();releaseCheck({});await cancelledSave;
 assert(!h.instance.wizard);assert.equal(h.state.saves,0);assert.deepEqual(h.state.mutations,[]);assert.deepEqual(h.state.applies,[]);
 let releaseSave;const pendingSave=new Promise(resolve=>releaseSave=resolve);
 h=await open({saveHandler:()=>pendingSave});h.instance.draft.network=['guest'];await h.instance.showWizardStep(3);h.setField('dry_run','0');
 const activeSave=h.instance.saveWizard();for(let tick=0;tick<10&&!h.instance.wizardSaving;tick++)await Promise.resolve();
 assert(h.instance.wizardSaving);assert(button(h,'Cancel').disabled);assert(button(h,'Back').disabled);
 const duplicateSave=h.instance.saveWizard();assert.strictEqual(duplicateSave,activeSave);await h.instance.cancelWizard();await h.instance.previousStep();assert(h.instance.wizard);assert.equal(h.instance.wizardStep,3);
 releaseSave([config]);await activeSave;assert.equal(h.state.saves,1);assert.deepEqual(h.state.applies,[true]);assert(!h.instance.wizardSaving);assert(!button(h,'Back').disabled);
 const retryOptions={applyFailure:true};h=await open(retryOptions);h.instance.draft.network=['guest'];await h.instance.showWizardStep(3);h.setField('dry_run','0');await h.instance.saveWizard();
 assert.equal(h.state.configs[config].main.dry_run,'0');assert(!h.instance.wizardSaving);retryOptions.applyFailure=false;h.setField('dry_run','1');await h.instance.saveWizard();
 assert.equal(h.state.configs[config].main.dry_run,'1');assert.equal(h.state.saves,2);assert.equal(h.state.applies.length,2);
 h=await open({main:{network:['guest']}});const cooldown=h.instance.settingsMap.options.find(o=>o.name==='reconnect_cooldown');assert.equal(cooldown.default,'0');assert.equal(cooldown.formvalue(),'0');assert.equal(cooldown.datatype,expectedTypes.reconnect_cooldown);await h.instance.startWizard();assert.equal(h.instance.draft.reconnect_cooldown,'0');await h.instance.cancelWizard();assert(!('reconnect_cooldown' in h.state.configs[config].main));assert.deepEqual(h.state.mutations,[]);
 const protection=h.instance.settingsMap.options.find(o=>o.name==='reconnect_window');assert.equal(protection.default,'0');assert.equal(protection.formvalue(),'0');assert.equal(protection.datatype,expectedTypes.reconnect_window);await h.instance.startWizard();assert.equal(h.instance.draft.reconnect_window,'0');await h.instance.cancelWizard();assert(!('reconnect_window' in h.state.configs[config].main));assert.deepEqual(h.state.mutations,[]);
 h=await open({main:{network:['guest'],reconnect_window:'300'}});assert.equal(h.instance.settingsMap.options.find(o=>o.name==='reconnect_window').formvalue(),'300');await h.instance.startWizard();assert.equal(h.instance.draft.reconnect_window,'300');assert.deepEqual(h.state.mutations,[]);
 h=await open({main:{setup_complete:'1'}});assert(!h.instance.wizard);
 const legacyInternet=h.instance.settingsMap.options.find(o=>o.name==='guest_internet');assert.equal(legacyInternet.default,'1');assert.equal(legacyInternet.formvalue(),'1');await h.instance.startWizard();assert.equal(h.instance.draft.guest_internet,'1');await h.instance.cancelWizard();assert(!('guest_internet' in h.state.configs[config].main));assert.deepEqual(h.state.mutations,[]);
 h=await open({main:{network:['guest'],guest_internet:'0'}});assert.equal(h.instance.settingsMap.options.find(o=>o.name==='guest_internet').formvalue(),'0');h.setField('guest_internet','1');await h.instance.handleSaveApply(null,'0');assert.equal(h.state.configs[config].main.guest_internet,'1');assert.equal(h.state.saves,1);assert.deepEqual(h.state.applies,[true]);
 // Status describes the actual backend result, even when configuration or unsaved values disagree.
 const policyMessages={allowed:'Guests may access the Internet under the existing firewall policy.',blocked:'Internet access is blocked for guests. Protected devices keep their existing Internet access.',test:'Test mode. The Internet policy is logged without applying it.',inactive:'The Internet policy is inactive.',error:'The Internet policy could not be applied. Check the system log.'};
 for(const [internet_state,message] of Object.entries(policyMessages)){
  h=await open({main:{network:['guest'],guest_internet:internet_state==='blocked'?'1':'0'},internetStatus:{internet_state,error:'<img src=x onerror=alert(1)>'}});
  assert(h.instance.root.text().includes(message));assert(!h.instance.root.text().includes('<img'));
  assert(h.state.rpc.some(call=>call[0]===config&&call[1]==='status'));assert.deepEqual(h.state.mutations,[]);
 }
 for(const options of [{rpcFailure:[config]},{internetStatus:null},{internetStatus:{internet_state:'unexpected'}}]){
  h=await open(Object.assign({main:{network:['guest']}},options));assert(h.instance.root.text().includes('Internet policy status is unavailable.'));assert(h.instance.settingsMap);
 }
 h=await open({rpcFailure:true});h.setField('network',['guest']);await h.instance.nextStep();assert(h.instance.discovery.unavailable);
 assert(h.instance.root.text().includes('Device discovery is unavailable'));h.setField('manual_whitelist',[a]);await h.instance.nextStep();assert.deepEqual(h.instance.draft.whitelist,[a]);
 h=await open({hints:{[a.toUpperCase()]:{name:'Network server'}},clients:{'hostapd.ap0':{clients:{[a.toUpperCase()]:{assoc:true},[b]:{assoc:false},invalid:{assoc:true}}},'hostapd.ap1':{clients:{[a]:{assoc:true},[c]:{assoc:true}}}}});
 h.setField('network',['guest']);await h.instance.nextStep();assert.equal(h.instance.discovery.devices.length,2);
 assert(h.instance.discovery.devices.find(d=>d.mac===a).label.startsWith('Network server · '+a));assert(h.instance.discovery.devices.find(d=>d.mac===a).label.includes('Administration'));assert(!h.state.rpc.some(call=>call[0].includes('sta0')));
 h.setField('selected_devices',[a]);h.setField('manual_whitelist',[c.toUpperCase(),a]);await h.instance.nextStep();assert.deepEqual(h.instance.draft.whitelist,[a,c]);
 await h.instance.previousStep();assert.deepEqual(h.instance.wizardMap.data.get('json','wizard','selected_devices'),[a,c]);
 h=await open({rpcFailure:['luci-rpc'],clients:{'hostapd.ap0':{clients:{[a]:{assoc:true}}}}});h.setField('network',['guest']);await h.instance.nextStep();
 assert(!h.instance.discovery.unavailable);assert(h.instance.discovery.devices[0].label.startsWith(a+' · '));
 // The read-only adapter also discovers vendor stations on unselected admin APs.
 h=await open({rpcFailure:['network.wireless','hostapd.ap0','hostapd.ap1'],hints:{[a.toUpperCase()]:{name:'Operator laptop'}},stationReply:{unavailable:false,clients:[
  {mac:a.toUpperCase(),ifname:'ra0',section:'guest'},
  {mac:a,ifname:'ra0',section:'guest'},
  {mac:a,ifname:'rax0',section:'admin'},
  {mac:c,ifname:'rax0',section:'admin'},
  {mac:b,ifname:'apcli0',section:'uplink'},
  {mac:b,ifname:'ra1',section:'unknown'},
  {mac:b,ifname:'ra0',section:'guest',assoc:false},
  {mac:'invalid',ifname:'ra0',section:'guest'},
  {mac:b,ifname:'ra0;exec bad',section:'guest'},
  {mac:b,ifname:12,section:'guest'}
 ]}});
 h.setField('network',['guest']);await h.instance.nextStep();assert.equal(h.instance.discovery.devices.length,2);assert(!h.instance.discovery.unavailable);
 assert.equal(h.instance.discovery.devices.find(d=>d.mac===a).label,'Operator laptop · '+a+' · Guest WiFi (ra0), Administration (rax0)');
 assert(!h.state.rpc.some(call=>call[0]==='network.wireless'||call[0].startsWith('hostapd.')));
 h.setField('selected_devices',[a,c]);await h.instance.nextStep();assert.deepEqual(h.instance.draft.whitelist,[a,c]);assert.deepEqual(h.state.mutations,[]);
 h=await open({stationReply:{unavailable:true,clients:[{mac:a,ifname:'ra0',section:'guest'}]}});h.setField('network',['guest']);await h.instance.nextStep();assert(h.instance.discovery.unavailable);assert.equal(h.instance.discovery.devices.length,1);assert(h.instance.root.text().includes('Device discovery is unavailable'));
 h=await open({stationReply:{clients:{}},clients:{'hostapd.ap0':{clients:{[a]:{assoc:true}}}}});h.setField('network',['guest']);await h.instance.nextStep();assert(!h.instance.discovery.unavailable);assert.equal(h.instance.discovery.devices.length,1);assert(h.state.rpc.some(call=>call[0]==='hostapd.ap0'));
 let releaseStations;const delayedStations=new Promise(resolve=>releaseStations=resolve);
 h=await open({rpcHandlers:{'wifi-client-limiter.clients':()=>delayedStations}});h.setField('network',['guest']);
 const advancingStations=h.instance.nextStep();await Promise.resolve();await h.instance.cancelWizard();releaseStations({unavailable:false,clients:[{mac:a,ifname:'ra0',section:'guest'}]});await advancingStations;
 assert(!h.instance.wizard);assert.deepEqual(h.instance.discovery.devices,[]);assert.equal(h.state.notifications.length,0);assert.deepEqual(h.state.mutations,[]);
 let release;const delayed=new Promise(resolve=>release=resolve);h=await open({rpcHandlers:{'network.wireless':()=>delayed}});h.setField('network',['guest']);
 const advancing=h.instance.nextStep();await Promise.resolve();await Promise.resolve();await h.instance.cancelWizard();release({radio0:{interfaces:[{section:'guest',ifname:'ap0'}]}});await advancing;
 assert(!h.instance.wizard);assert(h.instance.root.text().includes('Start setup wizard'));assert.equal(h.state.notifications.length,0);assert.deepEqual(h.state.mutations,[]);
 h=await open();h.setField('network',['guest']);await h.instance.nextStep();h.setField('manual_whitelist',['invalid']);await h.instance.nextStep();assert.equal(h.instance.wizardStep,1);assert.equal(h.state.saves,0);
 h.setField('manual_whitelist',[]);await h.instance.nextStep();h.setField('max_clients','-1');await h.instance.nextStep();assert.equal(h.instance.wizardStep,2);assert.equal(h.state.saves,0);
 h.setField('max_clients','25.5');await h.instance.nextStep();assert.equal(h.instance.wizardStep,2);h.setField('max_clients','25');h.setField('interval','2.5');await h.instance.nextStep();assert.equal(h.instance.wizardStep,2);assert.equal(h.state.saves,0);
 h.setField('interval','10');for(const value of ['-1','0.5','1801','']){h.setField('reconnect_cooldown',value);await h.instance.nextStep();assert.equal(h.instance.wizardStep,2);assert.equal(h.state.saves,0);assert.deepEqual(h.state.mutations,[]);}
 h.setField('reconnect_cooldown','1800');await h.instance.nextStep();assert.equal(h.instance.wizardStep,3);assert.equal(h.instance.draft.reconnect_cooldown,'1800');assert.equal(h.state.saves,0);
 await h.instance.previousStep();for(const value of ['-1','0.5','86401','']){h.setField('reconnect_window',value);await h.instance.nextStep();assert.equal(h.instance.wizardStep,2);assert.equal(h.state.saves,0);assert.deepEqual(h.state.mutations,[]);}
 h.setField('reconnect_window','86400');await h.instance.nextStep();assert.equal(h.instance.wizardStep,3);assert.equal(h.instance.draft.reconnect_window,'86400');assert.equal(h.state.saves,0);
 h=await open({pending:{wireless:[['set','guest','ssid','Changed']]}});h.instance.draft.network=['guest'];await h.instance.showWizardStep(3);await h.instance.saveWizard();
 assert.equal(h.state.saves,0);assert.deepEqual(h.state.mutations,[]);assert(h.state.notifications.at(-1).includes('Pending changes in other settings'));
 h=await open({main:{network:['guest']},pending:{[config]:[['set','main','max_clients','8']]}});await h.instance.startWizard();assert(!h.instance.wizard);assert(h.state.notifications.at(-1).includes('Pending changes to this limiter'));
 h=await open({saveFailure:true});h.instance.draft.network=['guest'];await h.instance.showWizardStep(3);await h.instance.saveWizard();assert.equal(h.state.applies.length,0);assert(h.state.notifications.at(-1).includes('Unable to save setup'));
 h=await open({applyFailure:true});h.instance.draft.network=['guest'];await h.instance.showWizardStep(3);await h.instance.saveWizard();assert(h.instance.wizardSaved);assert.equal(h.state.configs[config].main.dry_run,'1');assert(h.state.notifications.at(-1).includes('Unable to save setup'));assert(!h.instance.root.text().includes('No changes have been saved yet'));
 h=await open({readonly:true});assert(h.instance.readonly);assert(button(h,'Next').disabled);h.instance.draft.network=['guest'];h.instance.draft.reconnect_cooldown='60';h.instance.draft.reconnect_window='300';await h.instance.saveWizard();assert.equal(h.state.saves,0);assert.deepEqual(h.state.mutations,[]);
 h=await open({readonly:true,main:{network:['guest'],guest_internet:'1'}});assert(h.instance.settingsMap.readonly);assert(button(h,'Start setup wizard').disabled);h.setField('guest_internet','0');await h.instance.handleSaveApply(null,'0');assert.equal(h.state.configs[config].main.guest_internet,'1');assert.equal(h.state.saves,0);assert.deepEqual(h.state.mutations,[]);
 h=await open({wireless:[]});assert(button(h,'Next').disabled);assert.equal(h.instance.draft.reconnect_cooldown,'0');assert.equal(h.instance.draft.reconnect_window,'0');assert.deepEqual(h.state.mutations,[]);
 const hostile='<img src=x onerror=alert(1)>';h=await open({wireless:[{'.name':'guest',mode:'ap',ssid:hostile,device:'radio0'}],hints:{[a]:{name:hostile}},clients:{'hostapd.ap0':{clients:{[a]:{assoc:true}}}}});
 assertLiteral(h.instance.wizardMap.options.find(o=>o.name==='network').values[0][1],hostile);
 h.setField('network',['guest']);await h.instance.nextStep();assertLiteral(h.instance.wizardMap.options.find(o=>o.name==='selected_devices').values[0][1],hostile);await h.instance.nextStep();await h.instance.nextStep();
 assertLiteral(h.instance.root,hostile);h.instance.showError(new Error(hostile));assertLiteral(h.state.notificationNodes.at(-1),hostile);assert(!source.includes('innerHTML'));assert(!source.includes('del_client'));
 // The live list reports the server's committed, verified policy, not local form drafts.
 const permissions={allowed:'Allowed under existing firewall policy',blocked:'Blocked (local network only)',exempt:'Protected device (existing firewall policy)',unmanaged:'Not managed by this limiter',test:'Test mode (not enforced)',not_enforced:'Not enforced',unknown:'Unknown'};
 const rows=Object.keys(permissions).map((permission,index)=>({mac:'02:00:00:00:00:'+String(index+1).padStart(2,'0'),ifname:'ra0',section:'guest',ssid:'Guest WiFi',hostname:'Phone '+index,internet_permission:permission}));
 let liveReply={clients:rows,unavailable:false,internet_state:'blocked',active:true};
 h=await open({main:{network:['guest'],enabled:'1',dry_run:'0',guest_internet:'1'},rpcHandlers:{'wifi-client-limiter.clients':()=>Promise.resolve(liveReply)}});
 assert(h.instance.root.text().includes('Connected devices'));assert.equal(h.state.pollers.length,1);assert.equal(h.state.pollers[0].interval,10);
 for(const label of Object.values(permissions))assert(h.instance.connectedDevicesNode.text().includes(label));
 assert(h.instance.internetStatusNode.text().includes(policyMessages.blocked));
 assert(!button(h,'Refresh status').disabled);assert.deepEqual(h.state.mutations,[]);
 const statusCalls=h.state.rpc.filter(call=>call[0]===config&&call[1]==='status').length;
 const originalSettings=h.instance.settingsMap;h.setField('guest_internet','0');h.setField('max_clients','31');
 liveReply={clients:[{mac:a,ifname:'ra0',section:'guest',ssid:hostile,hostname:hostile,internet_permission:'allowed'}],unavailable:false,internet_state:'allowed',active:false};
 await h.runPoll();assert.strictEqual(h.instance.settingsMap,originalSettings);assert.equal(h.instance.settingsMap.options.find(o=>o.name==='max_clients').formvalue(),'31');
 assert(h.instance.internetStatusNode.text().includes(policyMessages.allowed));assertLiteral(h.instance.connectedDevicesNode,hostile);
 assert.equal(h.state.rpc.filter(call=>call[0]===config&&call[1]==='status').length,statusCalls,'New client snapshots should need no duplicate status audit');assert.deepEqual(h.state.mutations,[]);
 await h.instance.handleSaveApply(null,'0');assert.equal(h.state.configs[config].main.guest_internet,'0');assert(h.instance.internetStatusNode.text().includes(policyMessages.allowed),'Save/apply must refresh the observed result, not assume draft enforcement');
 liveReply={clients:[],unavailable:false,internet_state:'inactive',active:false};await h.instance.refreshConnectedDevices();assert(h.instance.connectedDevicesNode.text().includes('No connected WiFi devices were found.'));assert(!h.instance.connectedDevicesNode.text().includes('unavailable'));
 liveReply={clients:[],unavailable:true,internet_state:'error',active:false};await h.instance.refreshConnectedDevices();assert(h.instance.connectedDevicesNode.text().includes('Connected device information is unavailable.'));assert(!h.instance.connectedDevicesNode.text().includes('No connected'));
 liveReply={clients:[rows[0]],unavailable:true,internet_state:'error',active:false};await h.instance.refreshConnectedDevices();assert(h.instance.connectedDevicesNode.text().includes('The list may be incomplete.'));assert(h.instance.connectedDevicesNode.text().includes('Phone 0'));
 liveReply={clients:[rows[1]],unavailable:false,internet_state:'error',active:false};await h.instance.refreshConnectedDevices();assert(!h.instance.connectedDevicesNode.text().includes('Blocked (local network only)'));assert(h.instance.connectedDevicesNode.text().includes('Unknown'));
 liveReply.internet_state='blocked';await h.instance.refreshConnectedDevices();assert(!h.instance.connectedDevicesNode.text().includes('Blocked (local network only)'));assert(h.instance.internetStatusNode.text().includes('The Internet rules could not be verified.'));assert(!h.instance.internetStatusNode.text().includes(policyMessages.blocked));
 liveReply={clients:[{mac:'invalid',ifname:'ra0',section:'guest'}],unavailable:false};await h.instance.refreshConnectedDevices();assert(h.instance.connectedDevicesNode.text().includes('Connected device information is unavailable.'));assert(!h.instance.connectedDevicesNode.text().includes('No connected'));
 liveReply={clients:[Object.assign({},rows[0],{internet_permission:'not_enforced'})],unavailable:false,internet_state:'inactive',active:false,enabled:true,dry_run:false,guest_internet:false,reason:'service_stopped',service_state:'stopped',service_running:false};
 await h.instance.refreshConnectedDevices();assert(h.instance.internetStatusNode.text().includes('The limiter service is not running. Internet restrictions could not be verified.'));assert(!h.instance.internetStatusNode.text().includes(policyMessages.inactive));assert(h.instance.connectedDevicesNode.text().includes('Not enforced'));
 liveReply.service_state='starting';await h.instance.refreshConnectedDevices();assert(h.instance.internetStatusNode.text().includes('The limiter service is starting. Internet restrictions have not been verified yet.'));
 liveReply.reason='service_error';liveReply.internet_state='error';liveReply.service_state='error';liveReply.service_error='mtk_driver_unsupported';liveReply.service_detail=hostile;
 await h.instance.refreshConnectedDevices();assert(h.instance.internetStatusNode.text().includes('This MTK WiFi driver is not supported.'));assert(h.instance.internetStatusNode.text().includes('Diagnostic code: mtk_driver_unsupported'));assertLiteral(h.instance.internetStatusNode,hostile);
 liveReply.service_error='';liveReply.service_detail='';liveReply.reason='policy_error';liveReply.error='rule_validation_failed';await h.instance.refreshConnectedDevices();assert(h.instance.internetStatusNode.text().includes('Diagnostic code: rule_validation_failed'));assert(h.instance.internetStatusNode.text().includes('The Internet rules could not be verified.'));
 liveReply.error='offload_active';await h.instance.refreshConnectedDevices();assert(h.instance.internetStatusNode.text().includes('Traffic acceleration could not be verified as disabled. Internet restrictions cannot be verified.'));
 for(const code of ['nft_missing','firewall_backend_required','helper_missing','mtk_driver_missing','mtk_driver_unsupported','mtk_profile_unsupported','mtk_steering_active','mtk_tools_missing','kernel_log_unavailable','network_selection_invalid','configuration_invalid','config_invalid','station_preflight_failed','worker_lock_busy','cleanup_failed','cleanup_pending','policy_error','rules_missing','rules_unverified','config_mismatch','vendor_acceleration_active','offload_active','network_status_failed','no_active_networks','no_networks'])assert(h.instance.internetDiagnosticText(code),'Missing human-readable diagnostic: '+code);
 h=await open({readonly:true,main:{network:['guest']},stationReply:{clients:[{mac:a,ifname:'ra0',section:'guest',internet_permission:'exempt'}],unavailable:false,internet_state:'allowed',active:false}});
 assert(!button(h,'Refresh status').disabled);await button(h,'Refresh status').attrs.click();assert.deepEqual(h.state.mutations,[]);assert(h.instance.connectedDevicesNode.text().includes('Protected device'));
 // Direct protection edits only the existing live DynamicList widget. Valid
 // pending entries retain their spelling so native LuCI's add-field value is
 // not accidentally duplicated by its case-sensitive getValue implementation.
 const protectReply={clients:[{mac:b.toUpperCase(),ifname:'ra0',section:'guest',hostname:'New phone',internet_permission:'blocked',protected:false}],unavailable:false,internet_state:'blocked',active:true};
 h=await open({main:{network:['guest'],enabled:'1',dry_run:'0',whitelist:[a]},stationReply:protectReply});
 h.setField('whitelist',[a.toUpperCase(),c]);h.setField('max_clients','43');h.setField('guest_internet','0');
 const currentMap=h.instance.settingsMap,protectButton=button(h,'Protect device');await protectButton.attrs.click();
 const whitelist=()=>h.instance.settingsMap.lookupOption('whitelist','main')[0].formvalue('main');
 assert.deepEqual(whitelist(),[a.toUpperCase(),c,b]);assert.strictEqual(h.instance.settingsMap,currentMap);
 assert.equal(currentMap.lookupOption('max_clients','main')[0].formvalue(),'43');assert.equal(currentMap.lookupOption('guest_internet','main')[0].formvalue(),'0');
 assert.equal(currentMap.lookupOption('dry_run','main')[0].formvalue(),'0');assert.deepEqual(h.state.mutations,[]);assert.equal(h.state.saves,0);assert.deepEqual(h.state.applies,[]);
 assert(h.instance.connectedDevicesNode.text().includes('Protection pending'));assert(h.instance.connectedDevicesNode.text().includes('Blocked (local network only)'));
 assert(h.state.notifications.at(-1).includes('Save & Apply to activate the exception.'));
 await protectButton.attrs.click();await h.instance.protectConnectedDevice(b.toUpperCase(),h.instance.liveGeneration);await h.runPoll();
 assert.equal(h.state.widgetUpdates.length,1);assert.deepEqual(whitelist(),[a.toUpperCase(),c,b]);assert(h.instance.connectedDevicesNode.text().includes('Protection pending'));
 // LuCI Save can stage UCI deltas before Apply. Backend observations, rather
 // than uci.get's saved draft, decide whether protection is already active.
 h.state.configs[config].main.whitelist=[a,b,c];await h.runPoll();assert(h.instance.connectedDevicesNode.text().includes('Protection pending'));assert(!h.instance.connectedDevicesNode.text().includes('Already protected'));
 h.instance.renderConnectedDevices(Object.assign({},protectReply,{clients:[Object.assign({},protectReply.clients[0],{protected:true,internet_permission:'exempt'})]}));
 assert(h.instance.connectedDevicesNode.text().includes('Already protected'));assert(h.instance.connectedDevicesNode.text().includes('Protected device (existing firewall policy)'));
 await h.instance.protectConnectedDevice('not-a-mac',h.instance.liveGeneration);assert.equal(h.state.widgetUpdates.length,1);
 const oldProtectGeneration=h.instance.liveGeneration;await h.instance.startWizard();await protectButton.attrs.click();await h.instance.protectConnectedDevice('02:00:00:00:00:99',oldProtectGeneration);
 assert.equal(h.state.widgetUpdates.length,1);await h.instance.cancelWizard();await h.instance.protectConnectedDevice('02:00:00:00:00:99',oldProtectGeneration);assert.equal(h.state.widgetUpdates.length,1);
 h=await open({readonly:true,main:{network:['guest']},stationReply:protectReply});assert(button(h,'Protect device').disabled);await button(h,'Protect device').attrs.click();await h.instance.protectConnectedDevice(b,h.instance.liveGeneration);
 assert.deepEqual(h.state.widgetUpdates,[]);assert.deepEqual(h.state.mutations,[]);
 h=await open({main:{network:['guest'],whitelist:[b.toUpperCase()]},stationReply:protectReply});assert(h.instance.connectedDevicesNode.text().includes('Protection pending'));
 await h.instance.protectConnectedDevice(b,h.instance.liveGeneration);assert.deepEqual(h.state.widgetUpdates,[]);
 h=await open({main:{network:['guest']},stationReply:protectReply});h.instance.settingsMap.lookupOption('whitelist','main')[0].getUIElement=()=>null;await button(h,'Protect device').attrs.click();
 assert(h.state.notifications.at(-1).includes('Unable to update protected MAC addresses.'));assert.deepEqual(h.state.widgetUpdates,[]);assert.deepEqual(h.state.mutations,[]);
 // Switching to the wizard stops polling and discards a late policy callback.
 let releaseLive,liveCalls=0;const delayedLive=new Promise(resolve=>releaseLive=resolve);
 const allowedSnapshot={clients:[Object.assign({},rows[0],{internet_permission:'allowed'})],unavailable:false,internet_state:'allowed',active:false};
 h=await open({main:{network:['guest']},rpcHandlers:{'wifi-client-limiter.clients':()=>++liveCalls===2?delayedLive:Promise.resolve(allowedSnapshot)}});
 const previousDevicesNode=h.instance.connectedDevicesNode,refreshing=h.runPoll();await Promise.resolve();assert(button(h,'Refresh status').disabled);
 await h.instance.startWizard();assert(h.instance.wizard);assert.equal(h.state.pollers.length,0);
 releaseLive({clients:[rows[1]],unavailable:false,internet_state:'blocked',active:true});await refreshing;assert(h.instance.wizard);assert(!h.instance.root.text().includes('Connected devices'));assert(!previousDevicesNode.text().includes('Blocked (local network only)'));
 await h.instance.cancelWizard();if(h.instance.liveRequest)await h.instance.liveRequest;assert.equal(h.state.pollers.length,1);assert(h.instance.connectedDevicesNode.text().includes('Allowed under existing firewall policy'));assert.deepEqual(h.state.mutations,[]);
 const acl=require('./files/usr/share/rpcd/acl.d/luci-app-wifi-client-limiter.json')['luci-app-wifi-client-limiter'];
 assert.deepEqual(acl.read.ubus,{'network.wireless':['status'],'hostapd.*':['get_clients'],'luci-rpc':['getHostHints'],'wifi-client-limiter':['status','clients']});assert.deepEqual(acl.write,{uci:[config]});
 console.log('PASS: wizard draft/cancel, selection/discovery/manual fallback, validation, limiter-only saving/apply, pending changes, failures, readonly, safe text and live policy/device snapshots');
}
main().catch(error=>{console.error(error);process.exit(1);});

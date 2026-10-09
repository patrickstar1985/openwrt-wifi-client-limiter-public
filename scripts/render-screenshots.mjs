#!/usr/bin/env node
// Render the production view with real upstream LuCI form/ui widgets.
// Only remote router state is replaced with deterministic example data.
// Usage: node scripts/render-screenshots.mjs [en|de] (default: en).
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';

const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const language=process.argv[2] || 'en';
if(!['en','de'].includes(language))throw new Error('Screenshot language must be en or de');
const output=process.env.LUCI_SCREENSHOT_OUTPUT || path.join(root,'docs/screenshots');
const branch=process.env.LUCI_SCREENSHOT_BRANCH || 'openwrt-24.10';
if(!/^openwrt-\d{2}\.\d{2}$/.test(branch))throw new Error('Unexpected LuCI branch');
const assets=process.env.LUCI_SCREENSHOT_ASSETS || path.join(os.tmpdir(),branch==='openwrt-24.10'?'luci-screenshots-assets':'luci-screenshots-assets-'+branch);
const upstream='https://raw.githubusercontent.com/openwrt/luci/'+branch+'/';
const files={
 'resources/cbi.js':'modules/luci-base/htdocs/luci-static/resources/cbi.js',
 'resources/luci.js':'modules/luci-base/htdocs/luci-static/resources/luci.js',
 'resources/form.js':'modules/luci-base/htdocs/luci-static/resources/form.js',
 'resources/ui.js':'modules/luci-base/htdocs/luci-static/resources/ui.js',
 'resources/validation.js':'modules/luci-base/htdocs/luci-static/resources/validation.js',
 'bootstrap/cascade.css':'themes/luci-theme-bootstrap/htdocs/luci-static/bootstrap/cascade.css',
 'bootstrap/mobile.css':'themes/luci-theme-bootstrap/htdocs/luci-static/bootstrap/mobile.css',
 ...(language==='de'?{'de/base.po':'modules/luci-base/po/de/base.po'}:{})
};
fs.mkdirSync(output,{recursive:true});fs.mkdirSync(assets,{recursive:true});
for(const [destination,source] of Object.entries(files)) {
 const target=path.join(assets,destination);
 if(fs.existsSync(target))continue;
 fs.mkdirSync(path.dirname(target),{recursive:true});
 const downloaded=spawnSync('python3',['-c','import sys,urllib.request;open(sys.argv[2],"wb").write(urllib.request.urlopen(sys.argv[1],timeout=40).read())',upstream+source,target],{encoding:'utf8'});
 if(downloaded.status!==0)throw new Error('Cannot fetch '+source+': '+downloaded.stderr);
}
function parsePo(file) {
 const entries={},lines=fs.readFileSync(file,'utf8').split('\n');
 let id='',value='',context='',field=null;
 function flush(){if(id&&value)entries[(context?context+'\u0001':'')+id]=value;id='';value='';context='';field=null;}
 for(const line of lines) {
  if(line.startsWith('msgctxt ')){flush();context=JSON.parse(line.slice(8));field='context';}
  else if(line.startsWith('msgid ')){if(id||value)flush();id=JSON.parse(line.slice(6));field='id';}
  else if(line.startsWith('msgstr ')){value=JSON.parse(line.slice(7));field='value';}
  else if(line.startsWith('msgstr[0] ')){value=JSON.parse(line.slice(10));field='value';}
  else if(line.startsWith('msgid_plural ')||/^msgstr\[\d+\]/.test(line))field=null;
  else if(line.startsWith('"')){const extra=JSON.parse(line);if(field==='id')id+=extra;else if(field==='value')value+=extra;else if(field==='context')context+=extra;}
  else if(!line.trim())flush();
 }
 flush();return entries;
}
const translations=Object.assign(language==='de'?parsePo(path.join(assets,'de/base.po')):{},
 parsePo(path.join(root,'po',language,'wifi-client-limiter.po')));
const labels=Object.fromEntries(['Start setup wizard','Select WiFi networks to manage','Next','Back','Dismiss','Protect device','Protection pending','Blocked (local network only)'].map(key=>[key,translations[key] || key]));
const candidates=['files/www/luci-static/resources/view/wifi-client-limiter/settings.js','files/www/luci-static/resources/view/wifi-client-limiter.js'];
const viewFile=candidates.map(p=>path.join(root,p)).find(p=>fs.existsSync(p));
if(!viewFile)throw new Error('Production LuCI view not found');
const sources=Object.fromEntries(Object.keys(files).filter(p=>p.endsWith('.js')).map(p=>[path.basename(p),fs.readFileSync(path.join(assets,p),'utf8')]));
sources.view=fs.readFileSync(viewFile,'utf8');
const require=createRequire(import.meta.url);
let playwright;
try{playwright=require('playwright');}catch(error){
 const location=process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES;
 if(!location)throw new Error('Install Playwright locally: npm install --no-save playwright');
 playwright=require(path.join(location,'playwright'));
}
const browser=await playwright.chromium.launch({headless:true,
 executablePath:process.env.SCREENSHOT_CHROMIUM || undefined,
 args:['--no-sandbox','--disable-dev-shm-usage'].concat(JSON.parse(process.env.SCREENSHOT_CHROMIUM_ARGS || '[]'))});
try {
 const page=await browser.newPage({viewport:{width:1200,height:1024},deviceScaleFactor:1,locale:language==='de'?'de-DE':'en-US',colorScheme:'light'});
 const dismissNotifications=async()=>{
  const dismiss=page.getByRole('button',{name:labels.Dismiss,exact:true});
  while(await dismiss.count()){
   const before=await dismiss.count();
   await dismiss.first().click();
   await page.waitForFunction(({label,before})=>Array.from(document.querySelectorAll('button')).filter(button=>button.textContent.trim()===label).length<before,{label:labels.Dismiss,before});
  }
 };
 const errors=[];page.on('pageerror',error=>{errors.push(error.message);console.error('LuCI browser error: '+error.stack);});
 await page.setContent('<!doctype html><html lang="'+language+'"><head><meta charset="utf-8"><title>WiFi Client Limiter</title></head><body><div class="container"><main id="maincontent"><div id="view"></div></main></div></body></html>');
 await page.addStyleTag({content:fs.readFileSync(path.join(assets,'bootstrap/cascade.css'),'utf8')});
 await page.addStyleTag({content:fs.readFileSync(path.join(assets,'bootstrap/mobile.css'),'utf8')});
 await page.addScriptTag({content:sources['cbi.js']});
 // Expose the native loader's module cache so fixtures can supply only RPC/UCI.
 await page.addScriptTag({content:sources['luci.js'].replace('window.XHR = XHR;','window.__screenshotClasses = classes;\nwindow.XHR = XHR;')});
 await page.evaluate(({sources,translations})=>{
  window.L=Object.create(window.LuCI.prototype);
  Object.assign(L.env,{resource:'/luci-static/resources',media:'/luci-static/bootstrap',scriptname:'/cgi-bin/luci',requestpath:['admin','services','wifi-client-limiter'],nodespec:{satisfied:true,readonly:false}});
  L.loaded=true;
  window.TR={};for(const [key,value] of Object.entries(translations))TR[sfh(trimws(key))]=value;
  const clone=value=>JSON.parse(JSON.stringify(value));
  const config='wifi-client-limiter';
  const records={
   [config]:{main:{'.name':'main','.type':'limiter','.index':0,enabled:'1',dry_run:'0',guest_internet:'0',setup_complete:'1',max_clients:'25',max_session:'1800',reconnect_cooldown:'60',reconnect_window:'300',interval:'10',network:['guest'],whitelist:['02:00:00:00:00:10','02:00:00:00:00:20']}},
   wireless:{guest:{'.name':'guest','.type':'wifi-iface','.index':0,mode:'ap',ssid:'Guest WiFi',device:'radio0'},admin:{'.name':'admin','.type':'wifi-iface','.index':1,mode:'ap',ssid:'Admin WiFi',device:'radio1'}}
  };
  window.__fixtureWrites=[];
  const uci={load:()=>Promise.resolve(),loadPackage:()=>Promise.resolve(),get:(c,s,k)=>k==null?records[c]?.[s]:records[c]?.[s]?.[k],
   sections:(c,type,fn)=>{const list=Object.values(records[c]||{}).filter(s=>!type||s['.type']===type);if(fn)list.forEach(fn);return list;},
   changes:()=>Promise.resolve({}),resolveSID:(c,s)=>s,
   set:(...args)=>{__fixtureWrites.push(args);throw new Error('Screenshot fixture is read-only');},
   unset:(...args)=>{__fixtureWrites.push(args);throw new Error('Screenshot fixture is read-only');},save:()=>Promise.reject(new Error('Screenshot fixture cannot save'))};
  const status={radio0:{interfaces:[{section:'guest',ifname:'wlan0',config:{mode:'ap'}}]},radio1:{interfaces:[{section:'admin',ifname:'wlan1',config:{mode:'ap'}}]}};
  const hints={'02:00:00:00:00:10':{name:'Administration laptop'},'02:00:00:00:00:20':{name:'Network server'},'02:00:00:00:00:30':{name:'Guest smartphone'}};
  const clients={'hostapd.wlan0':{clients:{'02:00:00:00:00:20':{assoc:true},'02:00:00:00:00:30':{assoc:true}}},'hostapd.wlan1':{clients:{'02:00:00:00:00:10':{assoc:true}}}};
  const stations={unavailable:false,internet_state:'blocked',active:true,service_running:true,error:'',clients:[
   {mac:'02:00:00:00:00:10',ifname:'wlan1',section:'admin',hostname:'Administration laptop',ssid:'Admin WiFi',protected:true,internet_permission:'unmanaged'},
   {mac:'02:00:00:00:00:20',ifname:'wlan0',section:'guest',hostname:'Network server',ssid:'Guest WiFi',protected:true,internet_permission:'exempt'},
   {mac:'02:00:00:00:00:30',ifname:'wlan0',section:'guest',hostname:'Guest smartphone',ssid:'Guest WiFi',protected:false,internet_permission:'blocked'}
  ]};
  window.__fixtureCalls=[];
  const rpc={declare:description=>(()=>{
   __fixtureCalls.push([description.object,description.method]);
   return Promise.resolve(description.object==='session'?true:clone(description.object==='network.wireless'?status:description.object==='luci-rpc'?hints:description.object==='wifi-client-limiter'?(description.method==='clients'?stations:{internet_state:'blocked',active:true,service_running:true,error:''}):clients[description.object]||{}));
  })};
  const baseclass=L.Class,dom=L.dom,poll=L.Poll;
  const modules={baseclass,dom,poll,rpc,uci,view:L.view,request:{},session:{},fs:{}};
  L.require=name=>modules[name]?Promise.resolve(modules[name]):Promise.reject(new Error('Unexpected module: '+name));
  function load(source,names){return new Function(...names,source).apply({},names.map(name=>modules[name]));}
  modules.validation=new(load(sources['validation.js'],['baseclass']))();
  const UI=load(sources['ui.js'],['validation','baseclass','request','session','poll','dom','rpc','uci','fs']);
  modules.ui=new UI();L.ui=modules.ui;__screenshotClasses.ui=modules.ui;
  const Form=load(sources['form.js'],['ui','uci','rpc','dom','baseclass']);modules.form=new Form();__screenshotClasses.form=modules.form;
  const View=load(sources.view,['view','form','uci','ui','dom','rpc','poll'].map(name=>name));
  window.__limiterView=new View();
 },{sources,translations});
 await page.waitForSelector('#view .cbi-map');
 await page.waitForFunction(label=>document.querySelector('#view').textContent.includes(label),labels['Start setup wizard']);
 await page.evaluate(()=>document.dispatchEvent(new Event('DOMContentLoaded')));
 const start=page.getByRole('button',{name:labels['Start setup wizard'],exact:true});
 await start.waitFor();
 if(!await start.isEnabled())throw new Error('Native Start setup wizard button is disabled');
 if(await page.locator('[id="widget.cbid.wifi-client-limiter.main.reconnect_cooldown"]').inputValue()!=='60')throw new Error('Native settings cooldown fixture is missing');
 if(await page.locator('[id="widget.cbid.wifi-client-limiter.main.reconnect_window"]').inputValue()!=='300')throw new Error('Native settings protection window fixture is missing');
 if(await page.locator('[id="cbid.wifi-client-limiter.main.guest_internet"] input[type="checkbox"]').isChecked())throw new Error('Native settings Internet policy fixture is missing');
 await page.waitForFunction(()=>document.querySelector('#view').textContent.includes('Guest smartphone')).catch(async error=>{
  throw new Error(error.message+'; '+JSON.stringify(await page.evaluate(()=>({text:document.querySelector('#view').textContent,calls:__fixtureCalls,live:__limiterView.liveGeneration,wizard:__limiterView.wizard})))+'; '+errors.join('; '));
 });
 if(await page.locator('#view table').count()<1)throw new Error('Native connected-device table is missing');
 await page.locator('#maincontent').screenshot({path:path.join(output,'settings-'+language+'.png'),animations:'disabled'});

 // Exercise the actual DynamicList widget. A row action must only stage the
 // exception, keep other unsaved fields and retain the observed live policy.
 const maxClients=page.locator('[id="widget.cbid.wifi-client-limiter.main.max_clients"]');
 await maxClients.fill('19');
 const guestRow=page.locator('#view table tr').filter({hasText:'Guest smartphone'});
 await guestRow.getByRole('button',{name:labels['Protect device'],exact:true}).click();
 const protectedValues=()=>page.evaluate(()=>__limiterView.settingsMap.lookupOption('whitelist','main')[0].formvalue('main'));
 let values=await protectedValues();
 if(values.length!==3 || !values.includes('02:00:00:00:00:30'))throw new Error('Native row action did not preserve and extend protected MAC addresses');
 if(await maxClients.inputValue()!=='19')throw new Error('Native row action discarded another unsaved setting');
 if(!await guestRow.getByText(labels['Protection pending'],{exact:true}).count())throw new Error('Native row action does not show pending protection');
 if(!await guestRow.getByText(labels['Blocked (local network only)'],{exact:true}).count())throw new Error('Native row action falsely changed the observed live Internet policy');
 await page.evaluate(async()=>{
  __limiterView.protectConnectedDevice('02:00:00:00:00:30',__limiterView.liveGeneration);
  await __limiterView.refreshConnectedDevices();
 });
 values=await protectedValues();
 if(values.length!==3 || new Set(values).size!==3)throw new Error('Native row action created duplicate addresses after repeat or polling');
 if(!await guestRow.getByText(labels['Protection pending'],{exact:true}).count())throw new Error('Native status polling lost pending protection');
 if(await page.evaluate(()=>__fixtureWrites.length))throw new Error('Native row action wrote router state before Save & Apply');

 // Return through the ordinary cancel path before recording wizard examples.
 await start.click();
 await page.waitForFunction(()=>window.__limiterView.wizardStep===0);
 await page.evaluate(()=>__limiterView.cancelWizard());
 await page.waitForFunction(()=>!__limiterView.wizard && document.querySelector('#view').textContent.includes('Guest smartphone'));
 if(await maxClients.inputValue()!=='25' || (await protectedValues()).length!==2)throw new Error('Native cancel did not restore the clean fixture');
 await dismissNotifications();
 await start.click();
 await page.waitForFunction(label=>window.__limiterView.wizardStep===0&&document.querySelector('#view').textContent.includes(label),labels['Select WiFi networks to manage']);
 const next=page.getByRole('button',{name:labels.Next,exact:true});
 const back=page.getByRole('button',{name:labels.Back,exact:true});
 if(!await next.isEnabled() || !await back.isDisabled())throw new Error('Unexpected native wizard button state at first step');
 await next.click();
 await page.waitForFunction(()=>window.__limiterView.wizardStep===1&&document.getElementById('cbid.json.wizard.selected_devices'));
 if(!await next.isEnabled() || !await back.isEnabled())throw new Error('Native wizard navigation buttons are disabled');
 await page.locator('[id="cbid.json.wizard.selected_devices"] span.open').click();
 await page.locator('#maincontent').screenshot({path:path.join(output,'wizard-devices-'+language+'.png'),animations:'disabled'});
 await page.locator('[id="cbid.json.wizard.selected_devices"] span.open').click();
 await next.click();
 await page.waitForFunction(()=>window.__limiterView.wizardStep===2&&document.getElementById('widget.cbid.json.wizard.reconnect_cooldown'));
 const cooldown=page.locator('[id="widget.cbid.json.wizard.reconnect_cooldown"]');
 const protection=page.locator('[id="widget.cbid.json.wizard.reconnect_window"]');
 const guestInternet=page.locator('[id="cbid.json.wizard.guest_internet"] input[type="checkbox"]');
 if(await cooldown.inputValue()!=='60')throw new Error('Native wizard lost existing cooldown');
 if(await protection.inputValue()!=='300')throw new Error('Native wizard lost existing protection window');
 if(await guestInternet.isChecked())throw new Error('Native wizard lost disabled guest Internet choice');
 await guestInternet.check();await back.click();
 await page.waitForFunction(()=>window.__limiterView.wizardStep===1);await next.click();
 await page.waitForFunction(()=>window.__limiterView.wizardStep===2);
 if(!await guestInternet.isChecked())throw new Error('Native wizard lost changed Internet choice on Back');
 await guestInternet.uncheck();
 await cooldown.fill('0.5');await next.click();
 if(await page.evaluate(()=>__limiterView.wizardStep)!==2)throw new Error('Native form accepted a fractional cooldown');
 await cooldown.fill('60');
 for(const invalid of ['0.5','86401']){
  await protection.fill(invalid);await next.click();
  if(await page.evaluate(()=>__limiterView.wizardStep)!==2)throw new Error('Native form accepted an invalid protection window: '+invalid);
 }
 await protection.fill('300');await next.click();
 await page.waitForFunction(()=>window.__limiterView.wizardStep===3);
 if(await page.evaluate(()=>__limiterView.draft.reconnect_cooldown)!=='60')throw new Error('Native wizard review lost cooldown');
 if(await page.evaluate(()=>__limiterView.draft.reconnect_window)!=='300')throw new Error('Native wizard review lost protection window');
 if(await page.evaluate(()=>__limiterView.draft.guest_internet)!=='0')throw new Error('Native wizard review lost Internet choice');
 const mode=page.locator('[id="widget.cbid.json.wizard.dry_run"]');
 if(await mode.inputValue()!=='0')throw new Error('Native wizard reset existing live mode');
 await mode.selectOption('1');await back.click();
 await page.waitForFunction(()=>window.__limiterView.wizardStep===2);await next.click();
 await page.waitForFunction(()=>window.__limiterView.wizardStep===3);
 if(await mode.inputValue()!=='1')throw new Error('Native wizard lost selected test mode on Back');
 await mode.selectOption('0');
 await dismissNotifications();
 await page.locator('#maincontent').screenshot({path:path.join(output,'wizard-review-'+language+'.png'),animations:'disabled'});
 await page.evaluate(async()=>{
  L.env.nodespec.readonly=true;
  __limiterView.readonly=true;
  await __limiterView.cancelWizard();
  await __limiterView.handleSaveApply(null,'0');
 });
 if(!await page.locator('[id="cbid.wifi-client-limiter.main.guest_internet"] input[type="checkbox"]').isDisabled())throw new Error('Native Internet choice can be changed without write permissions');
 if(!await page.getByRole('button',{name:labels['Start setup wizard'],exact:true}).isDisabled())throw new Error('Native setup can be started without write permissions');
 await page.waitForFunction(()=>document.querySelector('#view').textContent.includes('Guest smartphone'));
 if(!await guestRow.getByRole('button',{name:labels['Protect device'],exact:true}).isDisabled())throw new Error('Native row action can be used without write permissions');
 if(!await page.evaluate(()=>__fixtureCalls.some(call=>call[0]==='wifi-client-limiter'&&call[1]==='clients')))throw new Error('Native chooser did not use shared station discovery');
 if(await page.evaluate(()=>__fixtureCalls.some(call=>call[0].startsWith('hostapd.'))))throw new Error('Native chooser bypassed shared station discovery');
 if(errors.length)throw new Error('Browser errors: '+errors.join('; '));
 if(await page.evaluate(()=>__fixtureWrites.length))throw new Error('Screenshot navigation changed router state');
 const hash=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
 const manifest={caption:'Screenshots rendered from the current LuCI view using example router data. Hardware integration still needs testing.',
  language,theme:'Official LuCI Bootstrap',luciBranch:branch,view:path.relative(root,viewFile),viewSha256:hash(viewFile),
  fixtureNetworks:['Guest WiFi','Admin WiFi'],fixtureDevices:['Administration laptop','Network server','Guest smartphone'],
  fixtureReconnectCooldownSeconds:60,defaultReconnectCooldownSeconds:0,
  fixtureReconnectProtectionWindowSeconds:300,freshConfigReconnectProtectionWindowSeconds:300,missingOptionReconnectProtectionWindowSeconds:0,
  fixtureGuestInternet:false,missingOptionGuestInternet:true,fixtureInternetPolicyStatus:'blocked',fixtureLiveDevicePermissions:['unmanaged','exempt','blocked'],
  fixtureStationDiscoveryRpc:'wifi-client-limiter.clients',
  fixtureOperatingMode:'live',checkedOperatingModeRoundTrip:['live','test','live'],
  checkedDirectProtection:{writesRouterState:false,preservesUnsavedLimit:true,persistsAcrossPolling:true,deduplicates:true,requiresApply:true},
  screenshots:Object.fromEntries(['settings','wizard-devices','wizard-review'].map(kind=>{
   const name=kind+'-'+language+'.png',file=path.join(output,name),png=fs.readFileSync(file);
   return [name,{sha256:hash(file),width:png.readUInt32BE(16),height:png.readUInt32BE(20)}];
  })),
  sourceAssets:Object.fromEntries(Object.entries(files).map(([asset,source])=>[asset,{url:upstream+source,sha256:hash(path.join(assets,asset))}]))};
 const provenance=JSON.stringify(manifest,null,2)+'\n';
 fs.writeFileSync(path.join(output,'provenance-'+language+'.json'),provenance);
 if(language==='en')fs.writeFileSync(path.join(output,'provenance.json'),provenance);
 console.log('Rendered genuine '+branch+' LuCI widgets with example data: '+output+' ({settings,wizard-devices,wizard-review}-'+language+'.png)');
} finally {await browser.close();}

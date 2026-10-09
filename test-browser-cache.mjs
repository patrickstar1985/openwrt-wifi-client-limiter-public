#!/usr/bin/env node
// Real LuCI 21.02 module-loader/cache regression. Router RPC and UCI data are
// fixtures; L.require(), XMLHttpRequest, browser HTTP cache and form widgets
// are real. Requires cached upstream LuCI assets and Playwright/Chromium.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';

const root=path.dirname(fileURLToPath(import.meta.url));
const assets=process.env.LUCI_CACHE_TEST_ASSETS || '/tmp/luci-screenshots-assets-openwrt-21.02';
const require=createRequire(import.meta.url);
let playwright;
try { playwright=require('playwright'); }
catch { playwright=require(path.join(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES || '', 'playwright')); }
const current=fs.readFileSync(path.join(root,'files/www/luci-static/resources/view/wifi-client-limiter.js'),'utf8');
const digest=crypto.createHash('sha256').update(current).digest('hex');
// A tiny synthetic prior interface suffices to exercise the real module
// loader's stale-URL behavior; no obsolete release package is required.
const legacy=fs.readFileSync(path.join(root,'tests/fixtures/legacy-four-column-view.js'),'utf8');
const oldModule='view.wifi-client-limiter';
// Obtain the new module name from the actual shared packaging helper, rather
// than duplicating the package's naming decision in this browser test.
let freshModule;
const stage=fs.mkdtempSync(path.join(os.tmpdir(),'wcl-cache-test-'));
try {
 for(const relative of ['www/luci-static/resources/view/wifi-client-limiter.js',
  'usr/share/luci/menu.d/luci-app-wifi-client-limiter.json']) {
  fs.mkdirSync(path.dirname(path.join(stage,relative)),{recursive:true});
  fs.copyFileSync(path.join(root,'files',relative),path.join(stage,relative));
 }
 execFileSync('sh',[path.join(root,'packaging/stage-view.sh'),stage]);
 const menu=JSON.parse(fs.readFileSync(path.join(stage,'usr/share/luci/menu.d/luci-app-wifi-client-limiter.json'),'utf8'));
 const viewPath=menu['admin/services/wifi-client-limiter'].action.path;
 assert.equal(viewPath,'wifi-client-limiter-'+digest);
 assert.equal(fs.readFileSync(path.join(stage,'www/luci-static/resources/view',viewPath+'.js'),'utf8'),current);
 freshModule='view.'+viewPath;
} finally {fs.rmSync(stage,{recursive:true,force:true});}
const fixedMtime='Thu, 01 Jan 2026 00:00:00 GMT';
const requests=[];
let installed=legacy;

function fixture() {
 window.L=Object.create(window.LuCI.prototype);
 Object.assign(L.env,{base_url:'/resources',resource:'/resources',media:'/bootstrap',
  resource_version:'git-22.245.77575-63bfee6',scriptname:'/cgi-bin/luci',
  requestpath:['admin','services','wifi-client-limiter'],nodespec:{satisfied:true,readonly:false}});
 L.loaded=true;
 window.TR={};
 const records={
  'wifi-client-limiter':{main:{'.name':'main','.type':'limiter','.index':0,
   enabled:'1',dry_run:'0',guest_internet:'0',setup_complete:'1',max_clients:'25',
   max_session:'0',reconnect_cooldown:'0',reconnect_window:'300',interval:'10',
   network:['guest'],whitelist:['02:00:00:00:00:10']}},
  wireless:{guest:{'.name':'guest','.type':'wifi-iface','.index':0,mode:'ap',ssid:'Guest WiFi',device:'radio0'}}
 };
 window.__fixtureWrites=[];
 const write=(...args)=>{__fixtureWrites.push(args);throw new Error('No writes allowed before Save & Apply');};
 const uci={load:()=>Promise.resolve(),loadPackage:()=>Promise.resolve(),
  get:(c,s,k)=>k==null?records[c]?.[s]:records[c]?.[s]?.[k],
  sections:(c,type,fn)=>{const list=Object.values(records[c]||{}).filter(s=>!type||s['.type']===type);if(fn)list.forEach(fn);return list;},
  changes:()=>Promise.resolve({}),resolveSID:(c,s)=>s,set:write,unset:write,save:write};
 const status={internet_state:'blocked',active:true,service_running:true,error:''};
 const clients={...status,unavailable:false,clients:[{
  mac:'02:00:00:00:00:30',ifname:'wlan0',section:'guest',hostname:'Guest smartphone',
  ssid:'Guest WiFi',protected:false,internet_permission:'blocked'
 }]};
 const rpc={declare:description=>(()=>Promise.resolve(description.object==='session'?true:
  description.object==='wifi-client-limiter'?(description.method==='clients'?clients:status):
  description.object==='network.wireless'?{radio0:{interfaces:[{section:'guest',ifname:'wlan0',config:{mode:'ap'}}]}}:{}))};
 Object.assign(window.__cacheTestClasses,{uci,rpc,fs:{}});
 const name=new URL(location.href).searchParams.get('module');
 // Use upstream loader for ui, form, validation AND the production view.
 window.__boot=L.require('ui').then(ui=>{
  L.ui=ui;
  return L.require(name);
 }).then(view=>{window.__limiterView=view;return view;});
 __boot.catch(error=>{window.__bootError=error.stack;console.error(error);});
}

const cbi=fs.readFileSync(path.join(assets,'resources/cbi.js'),'utf8');
const luci=fs.readFileSync(path.join(assets,'resources/luci.js'),'utf8')
 .replace('window.XHR = XHR;','window.__cacheTestClasses = classes;\nwindow.XHR = XHR;');
const server=http.createServer((request,response)=>{
 const url=new URL(request.url,'http://localhost');
 const item={url:request.url,validator:request.headers['if-modified-since'] || '',status:200};
 requests.push(item);
 if(url.pathname==='/') {
  response.writeHead(200,{'Content-Type':'text/html','Cache-Control':'no-store'});
  response.end('<!doctype html><html><head><meta charset="utf-8"></head><body><main id="maincontent"><div id="view"></div></main>' +
   '<script src="/resources/cbi.js"></script><script src="/resources/luci.js"></script><script>('+fixture.toString()+')()</script></body></html>');
  return;
 }
 let source;
 if(url.pathname==='/resources/cbi.js')source=cbi;
 else if(url.pathname==='/resources/luci.js')source=luci;
 else if(url.pathname==='/resources/view/wifi-client-limiter.js')source=installed;
 else if(url.pathname==='/resources/'+freshModule.replaceAll('.','/')+'.js')source=current;
 else if(/^\/resources\/(ui|form|validation)\.js$/.test(url.pathname))
  source=fs.readFileSync(path.join(assets,url.pathname.slice(1)),'utf8');
 if(source===undefined){item.status=404;response.writeHead(404);response.end();return;}
 // Model stock uhttpd's observed headers: no explicit Cache-Control, an old
 // reproducible build mtime, and a validator that changes with the payload.
 // A real revalidation would receive the new bytes. Heuristic freshness can
 // avoid that request entirely for XMLHttpRequest made by LuCI's loader.
 const tag='\"'+crypto.createHash('sha256').update(source).digest('hex')+'\"';
 const headers={'Content-Type':'text/javascript','Last-Modified':fixedMtime,'ETag':tag};
 if(request.headers['if-none-match']===tag){item.status=304;response.writeHead(304,headers);response.end();}
 else {response.writeHead(200,headers);response.end(source);}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const launchFile=process.env.LUCI_CACHE_TEST_LAUNCH || '/tmp/wcl-screenshot-browser/launch.json';
const launch=fs.existsSync(launchFile)?JSON.parse(fs.readFileSync(launchFile,'utf8')):{};
const browser=await playwright.chromium.launch({headless:true,...launch,
 ...(process.env.SCREENSHOT_CHROMIUM?{executablePath:process.env.SCREENSHOT_CHROMIUM}:{}),
 args:[...(launch.args || []),'--no-sandbox','--disable-dev-shm-usage']});
try {
 const context=await browser.newContext();
 const page=await context.newPage();
 const errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 const base='http://127.0.0.1:'+server.address().port;
 async function ready(){
  await page.waitForFunction(()=>!!window.__limiterView || !!window.__bootError);
  assert.equal(await page.evaluate(()=>window.__bootError),undefined);
  await page.getByText('Guest smartphone',{exact:true}).waitFor();
 }
 await page.goto(base+'/?module='+oldModule);await ready();
 assert.equal(await page.locator('table th').count(),4,'synthetic baseline must have four columns');
 assert.equal(await page.getByRole('button',{name:'Protect device',exact:true}).count(),0);
 installed=current;
 await page.reload();await ready();
 assert.equal(await page.locator('table th').count(),4,'ordinary reload of unchanged URL should reproduce stale view');
 assert.equal(requests.filter(r=>r.url.startsWith('/resources/view/wifi-client-limiter.js?')).length,1,
  'browser heuristic cache must reuse old view without a new request');
 await page.goto(base+'/?module='+freshModule);await ready();
 assert.equal(await page.locator('table th').count(),5,'unique production module must load fifth column');
 const maxClients=page.locator('[id="widget.cbid.wifi-client-limiter.main.max_clients"]');
 await maxClients.fill('19');
 await page.getByRole('button',{name:'Protect device',exact:true}).click();
 const values=await page.evaluate(()=>__limiterView.settingsMap.lookupOption('whitelist','main')[0].formvalue('main'));
 assert.deepEqual(values,['02:00:00:00:00:10','02:00:00:00:00:30']);
 assert.equal(await maxClients.inputValue(),'19');
 assert.equal(await page.getByText('Protection pending',{exact:true}).count(),1);
 assert.equal(await page.getByText('Blocked (local network only)',{exact:true}).count(),1);
 assert.equal(await page.evaluate(()=>__fixtureWrites.length),0);
 await page.evaluate(()=>__limiterView.refreshConnectedDevices());
 assert.equal(await page.getByText('Protection pending',{exact:true}).count(),1);
 await page.getByRole('button',{name:'Start setup wizard',exact:true}).click();
 await page.waitForFunction(()=>__limiterView.wizardStep===0);
 for(const step of [1,2,3]) {
  await page.getByRole('button',{name:'Next',exact:true}).click();
  await page.waitForFunction(expected=>__limiterView.wizardStep===expected,step);
 }
 const mode=page.locator('[id="widget.cbid.json.wizard.dry_run"]');
 assert.equal(await mode.inputValue(),'0','new wizard must preserve existing live mode');
 await mode.selectOption('1');
 await page.getByRole('button',{name:'Back',exact:true}).click();
 await page.waitForFunction(()=>__limiterView.wizardStep===2);
 await page.getByRole('button',{name:'Next',exact:true}).click();
 await page.waitForFunction(()=>__limiterView.wizardStep===3);
 assert.equal(await mode.inputValue(),'1','new wizard must retain explicit test-mode choice');
 assert.equal(await page.evaluate(()=>__fixtureWrites.length),0);
 assert.equal(errors.length,0,errors.join('\n'));
 const freshRequests=requests.filter(r=>r.url.startsWith('/resources/'+freshModule.replaceAll('.','/')+'.js?'));
 assert.equal(freshRequests.length,1);
 assert.equal(freshRequests[0].status,200);
 console.log(JSON.stringify({result:'PASS',luciBranch:'openwrt-21.02',coreResourceVersion:'git-22.245.77575-63bfee6',
  baseline:'synthetic four-column view',reproducedStaleColumns:4,freshColumns:5,viewSha256:digest,
  oldViewResponses:requests.filter(r=>r.url.startsWith('/resources/view/wifi-client-limiter.js?')).map(r=>r.status),
  freshViewResponses:freshRequests.map(r=>r.status),protectDeviceStagesMac:true,preservesOtherPendingFields:true,
  preservesLiveMode:true,retainsExplicitTestMode:true,writesBeforeApply:0,usesNativeModuleLoader:true},null,2));
 await context.close();
} finally {await browser.close();await new Promise(resolve=>server.close(resolve));}

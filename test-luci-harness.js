'use strict';
// Small API doubles for the real LuCI view. These tests do not emulate a router.
const fs = require('fs');
const path = require('path');
const source = fs.readFileSync(path.join(__dirname,
 'files/www/luci-static/resources/view/wifi-client-limiter.js'), 'utf8');
const clone = value => value == null ? value : JSON.parse(JSON.stringify(value));
class Node {
 constructor(tag,attrs,children) {
  this.tag=tag;this.attrs={};
  // Native dom.attr omits only null/undefined. disabled="false" still disables HTML buttons.
  for(const [name,value] of Object.entries(attrs||{}))if(value!=null)this.attrs[name]=typeof value==='function'?value:String(value);
  this.children=[];this.scalarHTML=typeof children==='string';this.append(children);
 }
 get disabled() {return Object.hasOwn(this.attrs,'disabled');}
 set disabled(value) {if(value)this.attrs.disabled='';else delete this.attrs.disabled;}
 append(value) {
  // Native LuCI E() accepts one flat children array. Nested arrays are coerced
  // to text rather than recursively flattened into DOM nodes.
  if (Array.isArray(value)) value.forEach(v=>this.children.push(v instanceof Node?v:String(v==null?'':v)));
  else if(value!=null) this.children.push(value instanceof Node ? value : String(value));
 }
 appendChild(value) { this.append(value); return value; }
 text() { return this.children.map(v=>v instanceof Node?v.text():v).join(''); }
}
function E(tag, attrs, children) {
 if (arguments.length===2) { children=attrs; attrs={}; }
 return new Node(tag,attrs,children);
}
function makeHarness(opts={}) {
 const config='wifi-client-limiter';
 const state={configs:{[config]:{main:Object.assign({'.type':'limiter',enabled:'0',dry_run:'1',max_clients:'25',max_session:'0',interval:'10'},clone(opts.main||{}))},wireless:{}},
  mutations:[], saves:0, applies:[], init:0, pending:clone(opts.pending||{}), notifications:[], notificationNodes:[], rpc:[], maps:[], translations:new Set(), pollers:[], widgetUpdates:[]};
 const wireless=opts.wireless || [{'.name':'guest',mode:'ap',ssid:'Guest WiFi',device:'radio0'},
  {'.name':'admin',mode:'ap',ssid:'Administration',device:'radio1'},
  {'.name':'uplink',mode:'sta',ssid:'Internet',device:'radio1'}];
 wireless.forEach(w=>state.configs.wireless[w['.name']]=clone(w));
 const uci={
  load:()=>Promise.resolve(),
  get:(conf,section,key)=>key==null?state.configs[conf][section]:state.configs[conf][section]?.[key],
  sections:(conf,type,fn)=>Object.values(state.configs[conf]).forEach(fn),
  changes:()=>opts.changesHandler?opts.changesHandler(state):Promise.resolve(clone(state.pending)),
  set:(conf,section,key,value)=>{ state.mutations.push([conf,section,key,clone(value)]); state.configs[conf][section][key]=clone(value); },
  unset:(conf,section,key)=>{state.mutations.push([conf,section,key,null]);delete state.configs[conf][section][key];},
  add:(conf,type,section)=>{state.mutations.push([conf,section,'.type',type]);state.configs[conf][section]={'.type':type};return section;},
  save:()=>{state.saves++;if(state.mutations.some(row=>Array.isArray(row[3])&&!row[3].length))return Promise.reject(new Error('rpcd rejects empty arrays'));if(opts.saveFailure)return Promise.reject(new Error('Save failed'));state.pending[config]=[['set','main','dry_run',state.configs[config].main.dry_run]];return opts.saveHandler?opts.saveHandler(state):Promise.resolve([config]);}
 };
 class Data {
  constructor(data) { this.values=clone(data); }
  get(conf,id,key) { return key==null?this.values[id]:this.values[id]?.[key]; }
  set(conf,id,key,value) { this.values[id][key]=clone(value); }
 }
 class Map {
  constructor(conf,title,help) {this.config=conf;this.title=title;this.help=help;this.options=[];this.data=uci;state.maps.push(this);}
  section(type,id,kind,title) {
   const section={id,kind,title,option:(type,name,label,help)=>{
    const option={name,label,help,type,values:[],value:function(k,v){this.values.push([k,v]);},
     formvalue:()=>{
      if(option.input!==undefined)return option.input;
      const stored=this.data.get(this.config,id,name);
      return stored==null?option.default:stored;
     }};
    option.getUIElement=()=>({setValue:value=>{option.input=clone(value);state.widgetUpdates.push([name,clone(value)]);}});
    this.options.push(option);return option;
   }};this.sectionInfo=section;return section;
  }
  lookupOption(key) {const value=this.options.find(o=>o.name===key);return value?[value,this.sectionInfo.id]:null;}
  parse() {
   try {
    const id=this.sectionInfo.id;
    for(const o of this.options) {
     let value=o.formvalue(id);
     if(value==null && o.default!=null)value=o.default;
     if(o.datatype?.includes('range(')) {
      const [min,max]=o.datatype.match(/\d+/g).map(Number);
      if(!Number.isFinite(Number(value)) || value==='' ||
       (o.datatype.includes('uinteger') && !/^\d+$/.test(String(value))) ||
       Number(value)<min || Number(value)>max)throw new Error('Invalid '+o.name);
     }
     if(o.datatype==='macaddr' && (Array.isArray(value)?value:[value]).filter(Boolean).some(m=>!/^([0-9a-f]{2}:){5}[0-9a-f]{2}$/i.test(m)))throw new Error('Invalid MAC');
     if(o.validate) { const valid=o.validate(id,value);if(valid!==true)throw new Error(valid); }
     this.data.set(this.config,id,o.name,value);
    }
    return Promise.resolve();
   } catch(error) {return Promise.reject(error);}
  }
  save() {return this.parse().then(()=>uci.save());}
  render() {return Promise.resolve(E('section',{},[this.title,this.help,this.sectionInfo?.title].concat(
   this.options.map(o=>E('label',{},[o.label,o.help].concat(o.values.map(v=>v[1])))))));}
 }
 class JSONMap extends Map {
  constructor(data,title,help) {super('json',title,help);this.data=new Data(data);}
 }
 const form={Map,JSONMap,NamedSection:'named',Flag:'flag',Value:'value',ListValue:'list',MultiValue:'multi',DynamicList:'dynamic'};
 const ui={
  addNotification:(title,node)=>{state.notifications.push(node.text());state.notificationNodes.push(node);},
  createHandlerFn:(instance,name,...args)=>(()=>instance[name](...args)),
  changes:{init:()=>{state.init++;return Promise.resolve();},apply:checked=>{state.applies.push(checked);if(opts.applyFailure)throw new Error('Apply startup failed');}}
 };
 const status=opts.status||{radio0:{interfaces:[{section:'guest',ifname:'ap0',config:{mode:'ap'}}]},
  radio1:{interfaces:[{section:'admin',ifname:'ap1',config:{mode:'ap'}},{section:'uplink',ifname:'sta0',config:{mode:'sta'}}]}};
 const rpc={declare:declaration=>()=>{
  state.rpc.push([declaration.object,declaration.method]);
  if(opts.rpcHandlers?.[declaration.object+'.'+declaration.method])return opts.rpcHandlers[declaration.object+'.'+declaration.method]();
  if(opts.rpcHandlers?.[declaration.object])return opts.rpcHandlers[declaration.object]();
  if(opts.rpcFailure===true || (opts.rpcFailure||[]).includes(declaration.object))return Promise.reject(new Error('RPC unavailable'));
  if(declaration.object==='network.wireless')return Promise.resolve(clone(status));
  if(declaration.object==='luci-rpc')return Promise.resolve(clone(opts.hints||{}));
  if(declaration.object==='wifi-client-limiter'){
   if(declaration.method==='clients')return opts.stationReply===undefined?Promise.reject(new Error('Legacy installation has no clients method')):Promise.resolve(clone(opts.stationReply));
   return Promise.resolve(clone(opts.internetStatus===undefined?{internet_state:'inactive'}:opts.internetStatus));
  }
  return Promise.resolve(clone((opts.clients||{})[declaration.object]||{clients:{}}));
 }};
 const L={hasViewPermission:()=>!opts.readonly};
 const dom={content:(node,children)=>{node.children=[];node.append(children);}};
 const poll={add:(callback,interval)=>{state.pollers.push({callback,interval});},remove:callback=>{state.pollers=state.pollers.filter(item=>item.callback!==callback);}};
 const translate=key=>{state.translations.add(key);return opts.translate?opts.translate(key):key;};
 const view={extend:instance=>{
  instance.super=function(method,args){
   if(method==='addFooter')return E('footer',{},'Native LuCI footer');
   if(method==='handleSaveApply')return this.settingsMap.save().then(()=>ui.changes.init()).then(()=>ui.changes.apply(args[1]==='0'));
   throw new Error('Unsupported native method: '+method);
  };return instance;
 }};
 const instance=new Function('view','form','uci','ui','dom','rpc','poll','E','L','_',source)(view,form,uci,ui,dom,rpc,poll,E,L,translate);
 return {instance,state,form,uci,source,runPoll:()=>Promise.all(state.pollers.map(item=>item.callback())),setField:(name,value)=>{
  const map=instance.wizardMap||instance.settingsMap,option=map.options.find(o=>o.name===name);
  if(!option)throw new Error('No field '+name);option.input=value;
 }};
}
module.exports={makeHarness,Node,E,source};

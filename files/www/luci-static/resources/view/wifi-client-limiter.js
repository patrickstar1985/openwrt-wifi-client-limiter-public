'use strict';
'require view';
'require form';
'require uci';
'require ui';
'require dom';
'require rpc';
'require poll';

var configName = 'wifi-client-limiter';
var callWirelessStatus = rpc.declare({
 object: 'network.wireless', method: 'status', expect: { '': {} }, reject: true
});
var callHostHints = rpc.declare({
 object: 'luci-rpc', method: 'getHostHints', expect: { '': {} }, reject: true
});
var callLimiterStatus = rpc.declare({
 object: 'wifi-client-limiter', method: 'status', expect: { '': {} }, reject: true
});
var callLimiterClients = rpc.declare({
 object: 'wifi-client-limiter', method: 'clients', expect: { '': {} }, reject: true
});

function list(value) {
 if (Array.isArray(value)) return value.slice();
 if (typeof value === 'string') return value.trim() ? value.trim().split(/\s+/) : [];
 return value ? [value] : [];
}

function macList(values) {
 return list(values).map(function(value) { return String(value).toLowerCase(); })
  .filter(function(value, index, all) { return all.indexOf(value) === index; });
}

function accessPoints() {
 var result = [];
 uci.sections('wireless', 'wifi-iface', function(w) {
  if (w.mode === 'ap') result.push({
   id: w['.name'], ssid: w.ssid || w['.name'],
   label: (w.ssid || w['.name']) + ' (' + (w.device || '') + ', ' + w['.name'] + ')'
  });
 });
 return result;
}

// Older installations expose hostapd directly. The shared read-only endpoint
// also supports vendor station backends without exposing driver commands.
function legacyClients(aps) {
 return callWirelessStatus().then(function(status) {
  var interfaces = [], known = Object.create(null), clients = [], failed = false;
  aps.forEach(function(ap) { known[ap.id] = ap; });
  Object.keys(status || {}).forEach(function(radio) {
   list(status[radio] && status[radio].interfaces).forEach(function(entry) {
    if (!entry || !known[entry.section] || !/^[A-Za-z0-9_.-]{1,15}$/.test(entry.ifname || '')) return;
    if (entry.config && entry.config.mode && entry.config.mode !== 'ap') return;
    if (!interfaces.some(function(item) { return item.ifname === entry.ifname; }))
     interfaces.push({ ifname: entry.ifname, section: entry.section });
   });
  });
  return Promise.all(interfaces.map(function(iface) {
   return rpc.declare({ object: 'hostapd.' + iface.ifname, method: 'get_clients',
    expect: { '': {} }, reject: true })().then(function(reply) {
     if (!reply.clients || typeof reply.clients !== 'object' || Array.isArray(reply.clients)) { failed = true; return; }
     Object.keys(reply.clients).forEach(function(mac) {
      if (reply.clients[mac] && reply.clients[mac].assoc === false) return;
      clients.push({ mac: mac, ifname: iface.ifname, section: iface.section });
     });
    }).catch(function() { failed = true; });
  })).then(function() { return { clients: clients, unavailable: failed }; });
 });
}

function addNetworks(section, aps, required) {
 var option = section.option(form.MultiValue, 'network', _('WiFi networks to manage'),
  required ? _('Choose the WiFi networks on which client limits should apply. The wizard does not change WiFi configuration.') :
   _('Select the WiFi networks on which client limits should apply. Protected devices are excluded. No action is taken if no network is selected.'));
 // Native dropdowns accept nodes; scalar strings may be parsed as HTML.
 aps.forEach(function(ap) { option.value(ap.id, E('span', {}, [ap.label])); });
 if (required) {
  option.rmempty = false;
  option.validate = function(sectionId, value) {
   var chosen = list(value);
   return chosen.length && chosen.every(function(id) {
    return aps.some(function(ap) { return ap.id === id; });
   }) ? true : _('Select at least one access point.');
  };
 }
 return option;
}

function addLimits(section) {
 var o = section.option(form.Flag, 'guest_internet', _('Allow guests to access the Internet'),
  _('When disabled, guests can reach the local WiFi network only. Protected devices keep their existing Internet access. Test mode logs the policy without applying it.'));
 o.default = '1'; o.rmempty = false;
 o = section.option(form.Value, 'max_clients', _('Maximum number of guests'),
  _('0 = unlimited. Disconnect only when the limit is exceeded.'));
 o.datatype = 'and(uinteger,range(0,4096))'; o.rmempty = false;
 o = section.option(form.Value, 'max_session', _('Maximum connection duration (seconds)'),
  _('0 = unlimited. 1800 = 30 minutes.'));
 o.datatype = 'and(uinteger,range(0,604800))'; o.rmempty = false;
 o = section.option(form.Value, 'reconnect_cooldown', _('Reconnect cooldown (seconds)'),
  _('0 = no pause. After a disconnect, block automatic and manual reconnections on all selected WiFi networks for this duration.'));
 o.default = '0'; o.datatype = 'and(uinteger,range(0,1800))'; o.rmempty = false;
 o = section.option(form.Value, 'reconnect_window', _('Reconnect protection window (seconds)'),
  _('0 = disabled. For this long after a disconnect, returning devices may rejoin only if guest slots are free and the reconnect cooldown has ended.'));
 o.default = '0'; o.datatype = 'and(uinteger,range(0,86400))'; o.rmempty = false;
 o = section.option(form.Value, 'interval', _('Check interval (seconds)'));
 o.datatype = 'and(uinteger,range(2,3600))'; o.rmempty = false;
}

return view.extend({
 load: function() {
  return Promise.all([uci.load(['wireless', configName]), callLimiterStatus().catch(function() { return null; })])
   .then(function(results) { this.internetStatus = results[1]; }.bind(this));
 },

 render: function() {
  this.root = E('div');
  this.aps = accessPoints();
  this.readonly = !L.hasViewPermission();
  this.wizardGeneration = 1;
  this.discovery = { devices: [], unavailable: false };
  if (uci.get(configName, 'main', 'setup_complete') !== '1' &&
      !list(uci.get(configName, 'main', 'network')).length) {
   this.draft = this.readDraft();
   this.wizard = true;
   return this.showWizardStep(0).then(function() { return this.root; }.bind(this));
  }
  return this.showSettings().then(function() { return this.root; }.bind(this));
 },

 // Each mode places its own footer inside root, avoiding stale Save buttons.
 addFooter: function() { return E('div'); },

 showSettings: function() {
  this.stopLiveStatus();
  var generation = this.liveGeneration;
  this.wizardGeneration++;
  this.wizard = false;
  this.wizardMap = null;
  var m = new form.Map(configName, _('WiFi Client Limiter'),
   _('Global limit across selected WiFi networks. Protected devices are excluded from client limits and Internet restrictions. A reconnect cooldown can delay new connections after a disconnect.'));
  m.readonly = this.readonly;
  var s = m.section(form.NamedSection, 'main', 'limiter', _('Settings')), o;
  o = s.option(form.Flag, 'enabled', _('Enabled')); o.rmempty = false;
  o = s.option(form.Flag, 'dry_run', _('Test mode'), _('Log actions only. Do not disconnect devices or change Internet access.'));
  o.default = '1'; o.rmempty = false;
  addLimits(s);
  addNetworks(s, this.aps, false);
  o = s.option(form.DynamicList, 'whitelist', _('Protected MAC addresses'),
   _('Add administrator devices, servers and other permanent devices here. Check for private MAC addresses.'));
  o.datatype = 'macaddr';
  this.settingsMap = m;
  return m.render().then(function(node) {
   if (this.wizard || generation !== this.liveGeneration) return;
   this.internetStatusNode = E('div', { 'class': 'wifi-client-limiter-internet-status' });
   this.connectedDevicesNode = E('div');
   this.connectedRefreshButton = E('button', { 'class': 'btn', 'type': 'button',
    'click': ui.createHandlerFn(this, 'refreshConnectedDevices') }, [_('Refresh status')]);
   dom.content(this.root, [node, this.internetStatusNode, E('p', {}, E('button', {
    'class': 'btn cbi-button-action', 'type': 'button', 'disabled': this.readonly || null,
    'click': ui.createHandlerFn(this, 'startWizard')
   }, [_('Start setup wizard')])), E('div', { 'class': 'cbi-section' }, [
    E('h3', {}, [_('Connected devices')]),
    E('p', {}, [_('Device names may be unavailable. Permissions describe the limiter policy, not whether the Internet is reachable. Unsaved settings are not included.')]),
    E('p', {}, [this.connectedRefreshButton]), this.connectedDevicesNode
   ]), this.super('addFooter', [])]);
   this.renderInternetStatus();
   // Render immediately so a slow read-only request cannot delay cancelling the wizard.
   this.livePoll = function() {
    if (!this.wizard && generation === this.liveGeneration) return this.refreshConnectedDevices();
    return Promise.resolve();
   }.bind(this);
   poll.add(this.livePoll, 10);
   this.refreshConnectedDevices();
  }.bind(this));
 },

 stopLiveStatus: function() {
  this.liveGeneration = (this.liveGeneration || 0) + 1;
  if (this.livePoll) poll.remove(this.livePoll);
  this.livePoll = null;
  this.liveRequest = null;
 },

 refreshConnectedDevices: function() {
  if (this.wizard || !this.connectedDevicesNode) return Promise.resolve();
  if (this.liveRequest) return this.liveRequest;
  var generation = this.liveGeneration;
  var button = this.connectedRefreshButton;
  button.disabled = true;
  var request = callLimiterClients().catch(function() { return null; }).then(function(reply) {
   if (reply && ['allowed', 'blocked', 'test', 'inactive', 'error'].indexOf(reply.internet_state) !== -1)
    return [reply, reply];
   // Legacy endpoints do not include policy. Query status only for that fallback.
   return callLimiterStatus().catch(function() { return null; }).then(function(status) { return [reply, status]; });
  }).then(function(results) {
   if (this.wizard || generation !== this.liveGeneration) return;
   var reply = results[0];
   // The clients snapshot includes the policy that was verified for its rows.
   this.internetStatus = results[1];
   this.renderInternetStatus();
   this.renderConnectedDevices(reply);
  }.bind(this)).then(function() {
   if (generation === this.liveGeneration && this.liveRequest === request) {
    this.liveRequest = null;
    button.disabled = false;
   }
  }.bind(this));
  this.liveRequest = request;
  return request;
 },

 renderInternetStatus: function() {
  var status = this.internetStatus || {}, code = status.service_error || status.error || status.reason;
  var reason = this.internetDiagnosticText(code) || this.internetDiagnosticText(status.reason);
  var warning = status.internet_state === 'error' || status.service_state === 'error' ||
   status.reason === 'service_stopped' || status.reason === 'service_error' || !!reason;
  var content = [E('p', { 'class': warning ? 'alert-message warning' : null }, [
   E('strong', {}, [_('Internet policy status') + ': ']), this.internetStatusText()
  ])];
  if (reason) content.push(E('p', { 'class': 'alert-message warning' }, [reason]));
  if (warning && typeof code === 'string' && /^[a-z0-9_]{1,64}$/.test(code))
   content.push(E('p', {}, [E('strong', {}, [_('Diagnostic code') + ': ']), code]));
  if (warning && typeof status.service_detail === 'string' && status.service_detail)
   content.push(E('p', {}, [E('strong', {}, [_('Diagnostic details') + ': ']), status.service_detail.slice(0, 2048)]));
  dom.content(this.internetStatusNode, content);
 },

 internetDiagnosticText: function(code) {
  switch (code) {
   case 'nft_missing': return _('nftables is missing.');
   case 'firewall_backend_required': return _('The native OpenWrt firewall backend is unavailable.');
   case 'helper_missing': return _('A required limiter component is missing.');
   case 'mtk_driver_missing': return _('The MTK WiFi driver could not be read.');
   case 'mtk_driver_unsupported': return _('This MTK WiFi driver is not supported.');
   case 'mtk_profile_unsupported': return _('This MTK WiFi profile is not supported.');
   case 'mtk_steering_active': return _('MultiAP steering conflicts with reconnect protection.');
   case 'mtk_tools_missing':
   case 'kernel_log_unavailable': return _('Required MTK WiFi tools or kernel log access are unavailable.');
   case 'network_selection_invalid':
   case 'configuration_invalid':
   case 'config_invalid': return _('The saved limiter configuration is invalid.');
   case 'station_preflight_failed': return _('The WiFi driver check failed.');
   case 'worker_lock_busy': return _('Another limiter worker is already running.');
   case 'cleanup_failed':
   case 'cleanup_pending': return _('Previous limiter rules could not be fully removed.');
   case 'policy_error':
   case 'rules_missing':
   case 'rules_unverified': return _('The Internet rules could not be verified.');
   case 'config_mismatch': return _('The active Internet rules do not match the saved settings.');
   case 'vendor_acceleration_active':
   case 'offload_active': return _('Traffic acceleration could not be verified as disabled. Internet restrictions cannot be verified.');
   case 'network_status_failed': return _('WiFi network status is unavailable.');
   case 'no_active_networks': return _('None of the selected WiFi networks are active.');
   case 'no_networks': return _('No WiFi network is selected.');
   default: return '';
  }
 },

 renderConnectedDevices: function(reply) {
  this.connectedReply = reply;
  var available = reply && Array.isArray(reply.clients) && typeof reply.unavailable === 'boolean';
  var known = Object.create(null), seen = Object.create(null), clients = [], invalid = false;
  var generation = this.liveGeneration;
  var option = this.settingsMap && this.settingsMap.lookupOption('whitelist', 'main');
  var pending = macList(option && option[0] ? option[0].formvalue('main') : []);
  var saved = macList(uci.get(configName, 'main', 'whitelist'));
  this.aps.forEach(function(ap) { known[ap.id] = ap; });
  if (available) reply.clients.forEach(function(client) {
   if (!client || typeof client.mac !== 'string' || typeof client.ifname !== 'string' ||
       typeof client.section !== 'string' || !client.section ||
       !/^[A-Za-z0-9_.-]{1,15}$/.test(client.ifname)) { invalid = true; return; }
   if (client.assoc === false) return;
   var mac = client.mac.toLowerCase();
   if (!/^([0-9a-f]{2}:){5}[0-9a-f]{2}$/.test(mac)) { invalid = true; return; }
   var key = mac + '/' + client.ifname + '/' + client.section;
   if (seen[key]) return;
   seen[key] = true;
   clients.push({
    mac: mac,
    protected: typeof client.protected === 'boolean' ? client.protected : null,
    hostname: typeof client.hostname === 'string' && client.hostname.trim() ? client.hostname : _('Unknown device'),
    wifi: (typeof client.ssid === 'string' && client.ssid ? client.ssid :
     (known[client.section] ? known[client.section].ssid : client.section)) + ' (' + client.ifname + ')',
    permission: this.devicePermissionText(client.internet_permission === 'blocked' &&
     (reply.internet_state !== 'blocked' || reply.active !== true) ? 'unknown' : client.internet_permission)
   });
  }, this);
  clients.sort(function(a, b) { return a.mac.localeCompare(b.mac) || a.wifi.localeCompare(b.wifi); });
  var content = [];
  if (!available || reply.unavailable || invalid) content.push(E('p', { 'class': 'alert-message warning' }, [
   clients.length ? _('Some WiFi devices could not be queried. The list may be incomplete.') : _('Connected device information is unavailable.')
  ]));
  if (!clients.length && available && !reply.unavailable && !invalid)
   content.push(E('p', {}, [_('No connected WiFi devices were found.')]));
  if (clients.length) content.push(E('table', { 'class': 'table' }, [
   E('tr', { 'class': 'tr table-titles' }, [_('Device'), _('MAC address'), _('WiFi network'), _('Internet policy'), _('Protection')].map(function(label) {
    return E('th', { 'class': 'th' }, [label]);
   }))
  ].concat(clients.map(function(client) {
    var action = pending.indexOf(client.mac) !== -1 ? E('span', {}, [
     (client.protected === true || (client.protected === null && saved.indexOf(client.mac) !== -1)) ?
      _('Already protected') : _('Protection pending')
    ]) : E('button', { 'class': 'btn', 'type': 'button', 'disabled': this.readonly || null,
     'click': ui.createHandlerFn(this, 'protectConnectedDevice', client.mac, generation)
    }, [_('Protect device')]);
    return E('tr', { 'class': 'tr' }, [client.hostname, client.mac, client.wifi, client.permission].map(function(value) {
     return E('td', { 'class': 'td' }, [value]);
    }).concat([E('td', { 'class': 'td' }, [action])]));
   }, this))));
  dom.content(this.connectedDevicesNode, content);
 },

 protectConnectedDevice: function(mac, generation) {
  if (this.readonly || this.wizard || generation !== this.liveGeneration || !this.settingsMap ||
      typeof mac !== 'string' || !/^([0-9a-f]{2}:){5}[0-9a-f]{2}$/i.test(mac)) return;
  mac = mac.toLowerCase();
  try {
   var found = this.settingsMap.lookupOption('whitelist', 'main');
   var option = found && found[0], widget = option && option.getUIElement('main');
   if (!widget || typeof widget.setValue !== 'function' || this.settingsMap.readonly || option.readonly)
    throw new Error(_('Unable to update protected MAC addresses.'));
   // Preserve all current widget values, including a valid address still in
   // its add field. Native setValue() emits LuCI's change events but does not
   // clear that field, so preserve spelling to avoid duplicating its value.
   var current = list(option.formvalue('main'));
   if (macList(current).indexOf(mac) !== -1) return;
   widget.setValue(current.concat([mac]));
   this.renderConnectedDevices(this.connectedReply);
   ui.addNotification(null, E('p', {}, [
    _('Device added to protected MAC addresses. Save & Apply to activate the exception.')
   ]));
  }
  catch (error) { this.showError(error); }
 },

 devicePermissionText: function(permission) {
  switch (permission) {
   case 'allowed': return _('Allowed under existing firewall policy');
   case 'blocked': return _('Blocked (local network only)');
   case 'exempt': return _('Protected device (existing firewall policy)');
   case 'unmanaged': return _('Not managed by this limiter');
   case 'test': return _('Test mode (not enforced)');
   case 'not_enforced': return _('Not enforced');
   default: return _('Unknown');
  }
 },

 internetStatusText: function() {
  // Show the backend's observed policy, independently of unsaved form values.
  var status = this.internetStatus || {};
  if (status.reason === 'service_stopped')
   return status.service_state === 'starting' ? _('The limiter service is starting. Internet restrictions have not been verified yet.') :
    _('The limiter service is not running. Internet restrictions could not be verified.');
  if (status.reason === 'service_error') return _('The limiter service reported an error. Internet restrictions could not be verified.');
  switch (status.internet_state) {
   case 'allowed': return _('Guests may access the Internet under the existing firewall policy.');
   case 'blocked': return status.active === false ? _('The Internet rules could not be verified.') :
    _('Internet access is blocked for guests. Protected devices keep their existing Internet access.');
   case 'test': return _('Test mode. The Internet policy is logged without applying it.');
   case 'inactive': return _('The Internet policy is inactive.');
   case 'error': return _('The Internet policy could not be applied. Check the system log.');
   default: return _('Internet policy status is unavailable.');
  }
 },

 readDraft: function() {
  var result = {}, defaults = { dry_run: '1', guest_internet: '1', max_clients: '25', max_session: '0', reconnect_cooldown: '0', reconnect_window: '0', interval: '10' };
  ['network', 'whitelist', 'dry_run', 'guest_internet', 'max_clients', 'max_session', 'reconnect_cooldown', 'reconnect_window', 'interval'].forEach(function(key) {
   var value = uci.get(configName, 'main', key);
   // Copy unsaved form values without parsing or mutating the UCI map.
   var option = this.settingsMap && this.settingsMap.lookupOption(key, 'main');
   if (option && option[0]) value = option[0].formvalue('main');
   result[key] = key === 'network' ? list(value) :
    (key === 'whitelist' ? macList(value) : String(value == null ? defaults[key] : value));
  }, this);
  result.dry_run = result.dry_run === '0' ? '0' : '1';
  return result;
 },

 checkPending: function(includeLimiter) {
  return uci.changes().then(function(changes) {
   var names = Object.keys(changes || {}).filter(function(name) { return list(changes[name]).length; });
   if (names.some(function(name) { return name !== configName; }))
    throw new Error(_('Pending changes in other settings were found. Apply or discard them before saving this setup.'));
   if (includeLimiter && names.indexOf(configName) !== -1)
    throw new Error(_('Pending changes to this limiter were found. Apply or discard them before starting the wizard.'));
  });
 },

 startWizard: function() {
  if (this.wizardSaving || this.readonly) return Promise.resolve();
  var generation = ++this.wizardGeneration;
  return uci.changes().then(function(changes) {
   if (generation !== this.wizardGeneration) return;
   if (list((changes || {})[configName]).length)
    throw new Error(_('Pending changes to this limiter were found. Apply or discard them before starting the wizard.'));
   this.draft = this.readDraft();
   this.stopLiveStatus();
   this.wizard = true;
   this.wizardSaved = false;
   return this.showWizardStep(0);
  }.bind(this)).catch(function(error) {
   if (generation === this.wizardGeneration) this.showError(error);
  }.bind(this));
 },

 discoverDevices: function() {
  var generation = this.wizardGeneration;
  var stations = callLimiterClients().catch(function() { return null; }).then(function(reply) {
   return reply && Array.isArray(reply.clients) && typeof reply.unavailable === 'boolean' ? reply : legacyClients(this.aps);
  }.bind(this));
  return Promise.all([stations, callHostHints().catch(function() { return {}; })]).then(function(results) {
   var reply = results[0], hints = results[1];
   var known = Object.create(null), devices = Object.create(null), names = Object.create(null);
   Object.keys(hints || {}).forEach(function(address) {
    var hint = hints[address];
    if (hint && typeof hint.name === 'string' && hint.name.trim()) names[address.toLowerCase()] = hint.name;
   });
   this.aps.forEach(function(ap) { known[ap.id] = ap; });
   reply.clients.forEach(function(client) {
    if (!client || typeof client.mac !== 'string' || typeof client.section !== 'string' || typeof client.ifname !== 'string' ||
        !known[client.section] || !/^[A-Za-z0-9_.-]{1,15}$/.test(client.ifname || '') || client.assoc === false) return;
    var mac = client.mac.toLowerCase();
    if (!/^([0-9a-f]{2}:){5}[0-9a-f]{2}$/.test(mac)) return;
    if (!devices[mac]) devices[mac] = { mac: mac, networks: [] };
    var label = known[client.section].ssid + ' (' + client.ifname + ')';
    if (devices[mac].networks.indexOf(label) === -1) devices[mac].networks.push(label);
   });
   var discovery = { unavailable: reply.unavailable, devices: Object.keys(devices).sort().map(function(mac) {
    return { mac: mac, label: (names[mac] ? names[mac] + ' · ' : '') + mac + ' · ' + devices[mac].networks.join(', ') };
   }) };
   if (this.isCurrentWizard(generation)) this.discovery = discovery;
   return discovery;
  }.bind(this)).catch(function() {
   var discovery = { devices: [], unavailable: true };
   if (this.isCurrentWizard(generation)) this.discovery = discovery;
   return discovery;
  }.bind(this));
 },

 showWizardStep: function(step) {
  if (!this.wizard) return Promise.resolve();
  var generation = this.wizardGeneration;
  this.wizardStep = step;
  this.wizardMap = null;
  var titles = [_('Select WiFi networks to manage'), _('Protect important devices'), _('Limits'), _('Review setup')];
  var descriptions = [
   _('Choose the WiFi networks on which client limits should apply. The wizard does not change WiFi configuration.'),
   _('Select the connected WiFi devices that must never be disconnected. You can also enter MAC addresses manually.'),
   _('Global limit across selected WiFi networks. Protected devices are excluded from client limits and Internet restrictions. A reconnect cooldown can delay new connections after a disconnect.'),
   _('Choose whether to test the settings or enforce them after applying.')
  ];
  var content = E('div'), m, s, o;
  var header = E('div', {}, [E('h2', {}, [_('Setup wizard')]),
   E('p', {}, [titles.map(function(title, index) { return (index + 1) + '. ' + title; }).join(' › ')]),
   E('h3', {}, [titles[step]]), E('p', {}, [descriptions[step]])]);
  if (step < 3) {
   var data = Object.assign({}, this.draft);
   if (step === 1) {
    var found = this.discovery.devices.map(function(device) { return device.mac; });
    data.selected_devices = this.draft.whitelist.filter(function(mac) { return found.indexOf(mac) !== -1; });
    data.manual_whitelist = this.draft.whitelist.filter(function(mac) { return found.indexOf(mac) === -1; });
   }
   m = new form.JSONMap({ wizard: data });
   m.readonly = this.readonly;
   s = m.section(form.NamedSection, 'wizard', 'wizard');
   if (step === 0) {
    addNetworks(s, this.aps, true);
    if (!this.aps.length) content.appendChild(E('p', { 'class': 'alert-message' },
     [_('No access points are configured. Create a WiFi network in Network / Wireless first.')]));
   }
   else if (step === 1) {
    if (this.discovery.devices.length) {
     o = s.option(form.MultiValue, 'selected_devices', _('Connected WiFi devices'));
     this.discovery.devices.forEach(function(device) { o.value(device.mac, E('span', {}, [device.label])); });
    }
    if (this.discovery.unavailable || !this.discovery.devices.length)
     content.appendChild(E('p', { 'class': 'alert-message' }, [this.discovery.unavailable ?
      _('Device discovery is unavailable. Add protected MAC addresses manually.') :
      _('No connected devices were found. Add protected MAC addresses manually if needed.')]));
    o = s.option(form.DynamicList, 'manual_whitelist', _('Manual MAC addresses'),
     _('Add administrator devices, servers and other permanent devices here. Check for private MAC addresses.'));
    o.datatype = 'macaddr';
    content.appendChild(E('p', {}, E('button', { 'class': 'btn', 'type': 'button',
     'click': ui.createHandlerFn(this, 'refreshDevices') }, [_('Refresh devices')])));
   }
   else addLimits(s);
   this.wizardMap = m;
  }
  else {
   var selected = this.draft.network.map(function(id) {
    var ap = this.aps.filter(function(item) { return item.id === id; })[0];
    return ap ? ap.label : id;
   }, this);
   var rows = [
    [_('WiFi networks to manage'), selected.join(', ')],
    [_('Protected MAC addresses'), this.draft.whitelist.join(', ') || '0'],
    [_('Allow guests to access the Internet'), this.draft.guest_internet === '0' ? _('Blocked') : _('Allowed')],
    [_('Maximum number of guests'), this.draft.max_clients],
    [_('Maximum connection duration (seconds)'), this.draft.max_session],
    [_('Reconnect cooldown (seconds)'), this.draft.reconnect_cooldown],
    [_('Reconnect protection window (seconds)'), this.draft.reconnect_window],
    [_('Check interval (seconds)'), this.draft.interval],
    [_('Enabled'), '1']
   ];
   content.appendChild(E('table', { 'class': 'table' }, rows.map(function(row) {
    return E('tr', { 'class': 'tr' }, [E('th', { 'class': 'th' }, [row[0]]), E('td', { 'class': 'td' }, [row[1]])]);
   })));
   m = new form.JSONMap({ wizard: { dry_run: this.draft.dry_run } });
   m.readonly = this.readonly;
   s = m.section(form.NamedSection, 'wizard', 'wizard');
   o = s.option(form.ListValue, 'dry_run', _('Operating mode'),
    _('Test mode only logs actions. Live mode enforces client limits, reconnect pauses and the selected Internet policy.'));
   o.value('1', _('Test mode')); o.value('0', _('Live mode')); o.rmempty = false;
   o.validate = function(sectionId, value) {
    return value === '0' || value === '1' ? true : _('Choose whether to test the settings or enforce them after applying.');
   };
   this.wizardMap = m;
  }
  var cancelButton = E('button', { 'class': 'btn', 'type': 'button', 'click': ui.createHandlerFn(this, 'cancelWizard') }, [_('Cancel')]);
  var backButton = E('button', { 'class': 'btn', 'type': 'button', 'disabled': step === 0 || null,
    'click': ui.createHandlerFn(this, 'previousStep') }, [_('Back')]),
   saveButton = E('button', { 'class': 'btn cbi-button-positive', 'type': 'button',
    'disabled': this.readonly || (step === 0 && !this.aps.length) || null,
    'click': ui.createHandlerFn(this, step === 3 ? 'saveWizard' : 'nextStep') },
    [step === 3 ? _('Save setup and apply') : _('Next')]);
  this.wizardButtons = [{ node: cancelButton, disabled: false }, { node: backButton, disabled: step === 0 },
   { node: saveButton, disabled: this.readonly || (step === 0 && !this.aps.length) }];
  var nav = E('div', { 'class': 'cbi-page-actions' }, [cancelButton, ' ', backButton, ' ', saveButton]);
  return Promise.resolve(m ? m.render() : null).then(function(node) {
   if (!this.isCurrentWizard(generation)) return;
   if (node) content.appendChild(node);
   this.wizardNote = E('p', {}, [this.wizardSaved ? '' : _('No changes have been saved yet.')]);
   dom.content(this.root, [header, content, this.wizardNote, nav]);
  }.bind(this));
 },

 collectStep: function() {
  var generation = this.wizardGeneration, m = this.wizardMap, step = this.wizardStep;
  if (!m) return Promise.resolve(this.isCurrentWizard(generation));
  return m.parse().then(function() {
   if (!this.isCurrentWizard(generation)) return false;
   var data = m.data;
   if (step === 0) {
    this.draft.network = list(data.get('json', 'wizard', 'network'));
    if (!this.draft.network.length) throw new Error(_('Select at least one access point.'));
   }
   else if (step === 1)
    this.draft.whitelist = macList(list(data.get('json', 'wizard', 'selected_devices'))
     .concat(list(data.get('json', 'wizard', 'manual_whitelist'))));
   else if (step === 2)
    ['guest_internet', 'max_clients', 'max_session', 'reconnect_cooldown', 'reconnect_window', 'interval'].forEach(function(key) {
     this.draft[key] = String(data.get('json', 'wizard', key));
    }, this);
   else if (step === 3) {
    var mode = data.get('json', 'wizard', 'dry_run');
    if (mode !== '0' && mode !== '1') throw new Error(_('Choose whether to test the settings or enforce them after applying.'));
    this.draft.dry_run = mode;
   }
   return true;
  }.bind(this));
 },

 isCurrentWizard: function(generation) {
  return this.wizard && generation === this.wizardGeneration;
 },

 nextStep: function() {
  if (this.wizardSaving) return Promise.resolve();
  var generation = this.wizardGeneration, step = this.wizardStep;
  return this.collectStep().then(function(collected) {
   return collected && step === 0 ? this.discoverDevices() : null;
  }.bind(this)).then(function() {
   if (this.isCurrentWizard(generation)) return this.showWizardStep(step + 1);
  }.bind(this)).catch(function(error) {
   if (this.isCurrentWizard(generation)) this.showError(error);
  }.bind(this));
 },

 previousStep: function() {
  if (this.wizardSaving) return Promise.resolve();
  var generation = this.wizardGeneration, step = this.wizardStep;
  return this.collectStep().then(function(collected) {
   if (collected && this.isCurrentWizard(generation)) return this.showWizardStep(step - 1);
  }.bind(this)).catch(function(error) {
   if (this.isCurrentWizard(generation)) this.showError(error);
  }.bind(this));
 },

 refreshDevices: function() {
  var generation = this.wizardGeneration;
  return this.collectStep().then(function(collected) {
   return collected ? this.discoverDevices() : null;
  }.bind(this)).then(function() {
   if (this.isCurrentWizard(generation)) return this.showWizardStep(1);
  }.bind(this)).catch(function(error) {
   if (this.isCurrentWizard(generation)) this.showError(error);
  }.bind(this));
 },

 cancelWizard: function() {
  if (this.wizardSaving) return Promise.resolve();
  this.draft = null;
  return this.showSettings();
 },

 saveWizard: function() {
  if (this.readonly || !this.wizard || this.wizardStep !== 3) return Promise.resolve();
  if (this.wizardSaveRequest && this.wizardSaveGeneration === this.wizardGeneration) return this.wizardSaveRequest;
  var generation = this.wizardGeneration, map = this.wizardMap;
  var request = this.collectStep().then(function(collected) {
   if (!collected || !this.isCurrentWizard(generation)) return false;
   return this.checkPending(!this.wizardSaved).then(function() {
    return this.isCurrentWizard(generation) && this.wizardMap === map;
   }.bind(this));
  }.bind(this)).then(function(proceed) {
   if (!proceed) return;
   if (!this.draft.network.length || !this.draft.network.every(function(id) {
    return this.aps.some(function(ap) { return ap.id === id; });
   }, this)) throw new Error(_('Select at least one access point.'));
   this.setWizardSaving(true);
   if (!uci.get(configName, 'main')) uci.add(configName, 'limiter', 'main');
   ['network', 'whitelist', 'guest_internet', 'max_clients', 'max_session', 'reconnect_cooldown', 'reconnect_window', 'interval'].forEach(function(key) {
    // rpcd rejects empty UCI list values; an empty protection list is unset.
    if (Array.isArray(this.draft[key]) && !this.draft[key].length) uci.unset(configName, 'main', key);
    else uci.set(configName, 'main', key, this.draft[key]);
   }, this);
   uci.set(configName, 'main', 'enabled', '1');
   uci.set(configName, 'main', 'dry_run', this.draft.dry_run);
   uci.set(configName, 'main', 'setup_complete', '1');
   return uci.save().then(function() {
    this.wizardSaved = true;
    if (this.wizardNote) dom.content(this.wizardNote, []);
    return this.checkPending(false);
   }.bind(this)).then(function() {
    // Native LuCI apply/confirm/reload flow with rollback protection.
    return Promise.resolve(ui.changes.init()).then(function() { return ui.changes.apply(true); });
   });
  }.bind(this)).catch(function(error) {
   if (this.isCurrentWizard(generation)) this.showError(error, _('Unable to save setup.'));
  }.bind(this)).then(function() {
   if (this.isCurrentWizard(generation)) this.setWizardSaving(false);
   if (this.wizardSaveRequest === request) this.wizardSaveRequest = null;
  }.bind(this));
  this.wizardSaveGeneration = generation;
  this.wizardSaveRequest = request;
  return request;
 },

 setWizardSaving: function(saving) {
  this.wizardSaving = saving;
  (this.wizardButtons || []).forEach(function(button) { button.node.disabled = saving || button.disabled; });
 },

 handleSaveApply: function(ev, mode) {
  if (this.readonly) return Promise.resolve();
  return this.checkPending(false).then(function() {
   return this.super('handleSaveApply', [ev, mode]);
  }.bind(this)).then(function() {
   return this.refreshConnectedDevices();
  }.bind(this)).catch(function(error) { this.showError(error); }.bind(this));
 },

 showError: function(error, prefix) {
  ui.addNotification(null, E('p', {}, [(prefix ? prefix + ' ' : '') + (error.message || String(error))]));
 }
});

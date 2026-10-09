'use strict';
'require view';

// Synthetic cache fixture, not a historical release. The real LuCI loader
// caches these four columns under the unchanged old module URL. The test then
// serves the current production view, whose content-addressed URL must bypass
// that cached response and expose its native protection/wizard widgets.
return view.extend({
	render: function() {
		return E('table', { 'class': 'table' }, [
			E('tr', {}, ['Device', 'MAC address', 'WiFi network', 'Internet policy'].map(function(label) {
				return E('th', {}, label);
			})),
			E('tr', {}, ['Guest smartphone', '02:00:00:00:00:30', 'Guest WiFi', 'Blocked'].map(function(label) {
				return E('td', {}, label);
			}))
		]);
	},
	handleSaveApply: null,
	handleSave: null,
	handleReset: null
});

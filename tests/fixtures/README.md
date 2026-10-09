# Test fixtures

These inputs are used only by automated tests and are never installed on a router.

## `legacy-internet-policy.sh`

A frozen policy helper used to exercise ownership-journal recovery. Its SHA-256 is
`c57b5d7e59245517bf593159c496441b64b3ca926682968e3904e2293d1d0674`.
The runtime library `upgrade-recovery.sh` recognizes these exact bytes before
repairing a legacy journal. `test-upgrade-recovery.py` checks the repair and
refusal paths with simulated router paths and hardware.

## `native_package_lifecycle.py`

The shared lifecycle harness loads the package identified by the current
`Makefile`. `test-package-lifecycle.py` installs and removes that IPK with GL's
native `opkg` in a temporary offline root. Lifecycle hooks run against simulated
hardware state. This harness is not an installer for real routers.

## `legacy-four-column-view.js`

A minimal synthetic LuCI view for browser-cache tests. `test-browser-cache.mjs`
loads it through LuCI's real module loader and Chromium's HTTP cache, then serves
the production view. The unchanged URL reproduces cached content; the
content-addressed URL must expose the current protection and wizard controls.
The form widgets are real LuCI widgets; router RPC and UCI data are fixtures.

## `libubox-20210516-jshn.sh`

The upstream jshn shell interface used for compatibility checks against the
native JSON tooling supplied by the supported firmware.

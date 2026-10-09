#!/bin/sh
# SPDX-License-Identifier: MIT
# fw4 calls script includes after its native nftables batch. Resolve current AP
# devices then, so a stale WLAN name can never reject the native firewall batch.
/usr/share/wifi-client-limiter/internet-policy.sh firewall-reload

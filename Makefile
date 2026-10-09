# SPDX-License-Identifier: MIT
# OpenWrt SDK / buildroot package recipe. Use scripts/build-sdk-package.sh.
include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-wifi-client-limiter
PKG_VERSION:=0.5.10
PKG_RELEASE:=1
PKG_LICENSE:=MIT
PKG_LICENSE_FILES:=LICENSE
PKG_MAINTAINER:=Patrick Gaisbauer
PKGARCH:=all

include $(INCLUDE_DIR)/package.mk

# Payload is copied as audited, including an already stripped optional AArch64
# helper. Preserve its bytes regardless of the SDK target or sstrip settings.
RSTRIP:=:

define Package/luci-app-wifi-client-limiter
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=Configurable WiFi limits and guest Internet access
  URL:=https://psphotobox.at/
  DEPENDS:=+luci-base +rpcd +iw +iwinfo +wireless-tools +jshn +jsonfilter +ubus +uci +procd +base-files +nftables-json +kmod-nft-netdev
  PKGARCH:=all
endef

define Package/luci-app-wifi-client-limiter/description
  Global guest WiFi count and connection duration limits with a multilingual
  LuCI interface, protected MAC addresses, guest Internet controls and a
  first-run setup assistant.
  Supports local hostapd access points through ubus and the supported GL.iNet
  MTK driver through iwinfo/iwpriv. Retains the installed firewall3/firewall4.
endef

define Package/luci-app-wifi-client-limiter/conffiles
/etc/config/wifi-client-limiter
endef

define Build/Prepare
	$(INSTALL_DIR) $(PKG_BUILD_DIR)
	$(CP) ./LICENSE $(PKG_BUILD_DIR)/LICENSE
endef

define Build/Configure
endef

define Build/Compile
endef

define Package/luci-app-wifi-client-limiter/install
	$(CP) ./files/. $(1)/
	$(CP) ./packaging/root/. $(1)/
	sh ./packaging/stage-view.sh "$(1)"
	$(INSTALL_DIR) $(1)/usr/share/wifi-client-limiter
	$(INSTALL_DATA) ./LICENSE $(1)/usr/share/wifi-client-limiter/LICENSE
	echo '$(PKG_VERSION)' > $(1)/usr/share/wifi-client-limiter/version
	chmod 0755 $(1)/usr/sbin/wifi-client-limiter $(1)/etc/init.d/wifi-client-limiter
	chmod 0755 $(1)/usr/share/wifi-client-limiter/package-hooks
	chmod 0755 $(1)/usr/share/wifi-client-limiter/internet-policy.sh
	chmod 0755 $(1)/usr/share/wifi-client-limiter/firewall-reload.sh
	chmod 0755 $(1)/usr/share/wifi-client-limiter/station-adapter.sh
	chmod 0755 $(1)/usr/share/wifi-client-limiter/mtk-acl-query-aarch64
	chmod 0755 $(1)/usr/share/wifi-client-limiter/vendor-acceleration.sh
	chmod 0755 $(1)/usr/libexec/rpcd/wifi-client-limiter
	chmod 0755 $(1)/etc/uci-defaults/95-wifi-client-limiter
endef

define Package/luci-app-wifi-client-limiter/preinst
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] && exit 0
if ! command -v fw3 >/dev/null 2>&1 && ! command -v fw4 >/dev/null 2>&1; then
  echo 'WiFi Client Limiter requires the existing OpenWrt firewall3 or firewall4 backend.' >&2
  exit 1
fi
if [ -x /etc/init.d/wifi-client-limiter ]; then
  /etc/init.d/wifi-client-limiter stop || exit $$?
fi
if [ -x /usr/share/wifi-client-limiter/internet-policy.sh ]; then
  /usr/share/wifi-client-limiter/internet-policy.sh cleanup || exit $$?
fi
if [ -x /usr/share/wifi-client-limiter/station-adapter.sh ]; then
  /usr/share/wifi-client-limiter/station-adapter.sh cleanup || exit $$?
fi
exit 0
endef

define Package/luci-app-wifi-client-limiter/postinst
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] && exit 0
/usr/share/wifi-client-limiter/package-hooks postinst
exit $$?
endef

define Package/luci-app-wifi-client-limiter/prerm
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] && exit 0
/usr/share/wifi-client-limiter/package-hooks prerm "$$@"
exit $$?
endef

define Package/luci-app-wifi-client-limiter/postrm
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] && exit 0
if [ ! -f /etc/config/wifi-client-limiter ] && [ -f /etc/config/wifi-client-limiter.backup ]; then
  cp -p /etc/config/wifi-client-limiter.backup /etc/config/wifi-client-limiter
fi
rm -f /tmp/luci-indexcache /tmp/luci-indexcache.*
rm -rf /tmp/luci-modulecache /tmp/wifi-client-limiter
if [ -x /etc/init.d/rpcd ]; then /etc/init.d/rpcd restart || true; fi
exit 0
endef

$(eval $(call BuildPackage,luci-app-wifi-client-limiter))

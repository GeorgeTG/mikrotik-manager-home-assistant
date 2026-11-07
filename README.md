# MikroTik Manager for Home Assistant

<img src="./custom_components/mtm/brand/icon.png" alt="MikroTik Manager icon" width="64" height="64">

See your MikroTik routers in Home Assistant. Connect to your MikroTik Manager server with OAuth to see the devices your account can access, including their health, WireGuard handshake age, and RouterOS version.

Requires **Home Assistant 2026.11.0 or later** and a MikroTik Manager server that exposes OAuth and MCP over HTTPS with a trusted certificate.

<!-- ha-marketplace:exclude-start -->
## Install

[![Open MikroTik Manager in Home Assistant Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=GeorgeTG&repository=mikrotik-manager-home-assistant&category=integration)

Open the badge as a Home Assistant administrator, add the repository, and select **Install**. If prompted, connect GitHub. You can also open **Settings → Marketplace → Add from link** and enter `GeorgeTG/mikrotik-manager-home-assistant`.

For manual installation, download the source archive from the [latest release](https://github.com/GeorgeTG/mikrotik-manager-home-assistant/releases/latest). Copy the complete `custom_components/mtm` directory into your Home Assistant configuration directory, so the manifest is at `/config/custom_components/mtm/manifest.json` on a typical installation. Restart Home Assistant.
<!-- ha-marketplace:exclude-end -->

## Connect your account

1. Open **Settings → Devices & services → Add integration** and choose **MikroTik Manager**.
2. Enter the server's HTTPS root URL, for example `https://mtm.example.com`.
3. Sign in to MikroTik Manager in your browser. Choose **See only** and leave `device_list` enabled on the consent screen.
4. Return to Home Assistant to see your devices.

A read-only MikroTik Manager account is sufficient. It needs `devices.read` for the relevant projects and permission to use the `device_list` MCP tool. MikroTik Manager must also have that tool enabled for the application. Its existing permission rules apply to unassigned devices.

## Device entities

| Entity | What it shows |
| --- | --- |
| State | The device's lifecycle state, such as online, degraded, or offline. |
| Healthy | On when MikroTik Manager reports the device as online. |
| Last handshake age | Seconds since the recorded WireGuard handshake. An absent observation appears as unknown. |
| RouterOS version | The version recorded in MikroTik Manager's inventory. |

The integration refreshes inventory every 60 seconds and discovers newly visible routers automatically. When a device is removed from the account's access, its entities become unavailable after the next successful refresh. Its Home Assistant history and registry entry remain available.

## Authorization and reconnection

The integration uses OAuth authorization code with PKCE. It requests `mtm:read` for read-only access and `offline_access` for automatic refresh. You enter your account credentials on your MikroTik Manager server, and Home Assistant keeps OAuth tokens in its local configuration. Keep installation credentials out of this repository and issue reports.

The refresh authorization expires 30 days after you connect. When Home Assistant asks you to reconnect, sign in and choose **See only** again. Revoking access can prompt an earlier reconnection.

Removing the integration attempts to revoke its authorization. If the server was unreachable during removal, open **Settings → Connected applications** in MikroTik Manager and disconnect Home Assistant there.

## Support and license

Report an integration problem in [GitHub Issues](https://github.com/GeorgeTG/mikrotik-manager-home-assistant/issues) with your Home Assistant version and integration version.

Licensed under either the [MIT License](./LICENSE-MIT) or the [Apache License 2.0](./LICENSE-APACHE), at your option.

"""Constants for the MikroTik Manager integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "mtm"
CONF_SERVER_URL = "server_url"
CONF_CLIENT_ID = "client_id"
PLATFORMS = (Platform.BINARY_SENSOR, Platform.SENSOR)
DEFAULT_SCAN_INTERVAL = timedelta(seconds=60)
OAUTH_SCOPE = "mtm:read offline_access"
REQUEST_TIMEOUT = 20

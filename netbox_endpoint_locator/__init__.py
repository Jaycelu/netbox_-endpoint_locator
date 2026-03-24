from netbox.plugins import PluginConfig


class EndpointLocatorConfig(PluginConfig):
    name = "netbox_endpoint_locator"
    verbose_name = "Endpoint Locator"
    description = "Locate endpoint switch/port via LibreNMS APIs"
    version = "0.2.0"
    author = "Jayce"
    author_email = "admin@example.com"
    base_url = "endpoint-locator"
    min_version = "4.0.0"

    required_settings = ["librenms_url", "librenms_token"]
    default_settings = {
        "verify_ssl": False,
        "timeout": 15,
        "top_level_menu": False,
    }


config = EndpointLocatorConfig

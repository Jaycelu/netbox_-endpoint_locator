from netbox.plugins import PluginMenu, PluginMenuItem
from netbox.choices import ButtonColorChoices


# Use a custom top-level menu so the plugin appears in the left navigation like other
# NetBox plugins (e.g. Diode), instead of only showing under the shared "Plugins" submenu.
menu = PluginMenu(
    label="Endpoint Locator",
    groups=(
        (
            "查找",
            (
                PluginMenuItem(
                    link="plugins:netbox_endpoint_locator:lookup",
                    link_text="Lookup",
                    buttons=(),
                ),
            ),
        ),
    ),
    icon_class="mdi mdi-router",
)

# Keep menu_items for backwards compatibility, but NetBox will prefer the custom `menu`
# when provided.
menu_items = (
    PluginMenuItem(
        link="plugins:netbox_endpoint_locator:lookup",
        link_text="Endpoint Locator",
    ),
)
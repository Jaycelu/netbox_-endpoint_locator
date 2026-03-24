from netbox.plugins import PluginMenu, PluginMenuItem

# PluginMenu is registered as a top-level sidebar section (same pattern as Diode).
# PluginConfig MUST live in the package __init__ (netbox_endpoint_locator/__init__.py)
# so NetBox resolves default path `navigation.menu` correctly.
menu = PluginMenu(
    label="Endpoint Locator",
    groups=(
        (
            "Lookup",
            (
                PluginMenuItem(
                    link="plugins:netbox_endpoint_locator:lookup",
                    link_text="Lookup",
                ),
            ),
        ),
    ),
    icon_class="mdi mdi-router",
)

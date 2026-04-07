import importlib.util
import pathlib
import sys
import types
import unittest
import uuid


def load_views_module():
    module_path = pathlib.Path(__file__).resolve().parents[1] / "netbox_endpoint_locator" / "views.py"
    package_name = "netbox_endpoint_locator"
    module_name = f"{package_name}.views_under_test_{uuid.uuid4().hex}"

    package_module = types.ModuleType(package_name)
    package_module.__path__ = [str(module_path.parent)]
    sys.modules[package_name] = package_module

    django_module = types.ModuleType("django")
    django_shortcuts_module = types.ModuleType("django.shortcuts")
    django_shortcuts_module.render = lambda request, template_name, context: context
    django_views_module = types.ModuleType("django.views")
    django_views_generic_module = types.ModuleType("django.views.generic")

    class View:
        pass

    django_views_generic_module.View = View
    sys.modules["django"] = django_module
    sys.modules["django.shortcuts"] = django_shortcuts_module
    sys.modules["django.views"] = django_views_module
    sys.modules["django.views.generic"] = django_views_generic_module

    class _Manager:
        def filter(self, **kwargs):
            return self

        def first(self):
            return None

    dcim_module = types.ModuleType("dcim")
    dcim_models_module = types.ModuleType("dcim.models")

    class Device:
        objects = _Manager()

    dcim_models_module.Device = Device
    sys.modules["dcim"] = dcim_module
    sys.modules["dcim.models"] = dcim_models_module

    ipam_module = types.ModuleType("ipam")
    ipam_models_module = types.ModuleType("ipam.models")

    class IPAddress:
        objects = _Manager()

    ipam_models_module.IPAddress = IPAddress
    sys.modules["ipam"] = ipam_module
    sys.modules["ipam.models"] = ipam_models_module

    forms_module = types.ModuleType(f"{package_name}.forms")

    class EndpointLookupForm:
        def __init__(self, *args, **kwargs):
            self.cleaned_data = {}

        def is_valid(self):
            return False

    forms_module.EndpointLookupForm = EndpointLookupForm
    sys.modules[f"{package_name}.forms"] = forms_module

    librenms_module = types.ModuleType(f"{package_name}.librenms")
    librenms_module.collect_port_vlan_values = lambda port_info: [
        str(item.get("vlan"))
        for item in (port_info or {}).get("vlans") or []
        if isinstance(item, dict) and item.get("vlan") is not None
    ]
    librenms_module.extract_vlan_from_interface_fields = lambda record: ""
    librenms_module.format_mac_ui = lambda mac: mac
    librenms_module.is_ip = lambda value: True
    librenms_module.lookup_arp_by_mac = lambda mac: []
    librenms_module.lookup_arp_by_ip = lambda ip: []
    librenms_module.lookup_device_links = lambda device: []
    librenms_module.lookup_device_port_stack = lambda device: []
    librenms_module.lookup_device_vlans = lambda device: []
    librenms_module.lookup_fdb_by_mac = lambda mac: []
    librenms_module.lookup_fdb_detail_by_mac = lambda mac: []
    librenms_module.lookup_port_by_id = lambda port_id, with_relations=None: None
    librenms_module.normalize_mac = lambda mac: mac
    librenms_module.parse_ip_from_arp = (
        lambda record: str((record or {}).get("ipv4_address") or (record or {}).get("ip") or (record or {}).get("address") or "")
        if isinstance(record, dict) else ""
    )
    librenms_module.parse_mac_from_arp = (
        lambda records: next(
            (
                str(item.get("mac_address") or item.get("mac") or "")
                for item in records or []
                if isinstance(item, dict) and (item.get("mac_address") or item.get("mac"))
            ),
            None,
        )
    )
    librenms_module.pick_arp_record = lambda records, ip=None: next(
        (item for item in records or [] if isinstance(item, dict)),
        None,
    )
    librenms_module.pick_scored_candidate = lambda candidates: candidates[0] if candidates else None
    librenms_module.resolve_fdb_vlan = lambda *args, **kwargs: ""
    librenms_module.score_fdb_candidate = lambda *args, **kwargs: 0
    sys.modules[f"{package_name}.librenms"] = librenms_module

    topology_module = types.ModuleType(f"{package_name}.topology")
    topology_module.build_port_stack_members = lambda mappings: {}
    topology_module.candidate_id = lambda candidate: str(
        candidate.get("candidate_id") or f"{candidate.get('device_id', '')}:{candidate.get('port_id', '')}"
    )
    topology_module.pick_edge_candidate = lambda canonical, candidates, links, stack: {
        "selected": canonical,
        "graph": {},
        "path": [topology_module.candidate_id(canonical)] if canonical else [],
        "scores": {},
        "candidates": candidates,
    }
    sys.modules[f"{package_name}.topology"] = topology_module

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ResponseNotFoundError(Exception):
    def __init__(self, message="404 Client Error: Not Found for url: http://localhost:8081/api/v0/resources/fdb/00900b6951a6"):
        super().__init__(message)
        self.response = types.SimpleNamespace(status_code=404)


class EndpointLookupArpFallbackTests(unittest.TestCase):
    def setUp(self):
        self.views = load_views_module()
        self.view = self.views.EndpointLookupView()
        self.view._port_cache = {}
        self.view._device_vlan_cache = {}
        self.view._device_link_cache = {}
        self.view._device_port_stack_cache = {}

    def _patch_port_lookup(self):
        self.views.lookup_port_by_id = lambda port_id, with_relations=None: {
            "port_id": str(port_id),
            "device_id": "13",
            "ifName": "XGE1/0/5",
            "ifAlias": "To FW",
            "device": {
                "device_id": "13",
                "hostname": "172.22.38.13",
                "sysName": "SMZY_B1_LAN_6520",
            },
            "vlans": [],
        }

    def test_locate_by_mac_falls_back_to_arp_port_when_fdb_is_not_found(self):
        self._patch_port_lookup()
        self.views.lookup_fdb_by_mac = lambda mac: (_ for _ in ()).throw(ResponseNotFoundError())

        localization = self.view.locate_by_mac(
            "00900b6951a6",
            arp_records=[
                {
                    "device_id": "13",
                    "port_id": "101",
                    "ipv4_address": "172.25.254.251",
                    "mac_address": "00:90:0b:69:51:a6",
                    "ifName": "XGE1/0/5",
                }
            ],
        )

        self.assertEqual(localization["canonical"]["source"], "arp_direct")
        self.assertEqual(localization["canonical"]["interface"], "XGE1/0/5")
        self.assertEqual(localization["canonical"]["device_name"], "SMZY_B1_LAN_6520")
        self.assertEqual(localization["topology"]["path"], ["13:101"])

    def test_locate_by_mac_falls_back_to_arp_port_when_fdb_has_no_candidates(self):
        self._patch_port_lookup()
        self.views.lookup_fdb_by_mac = lambda mac: []
        self.views.lookup_fdb_detail_by_mac = lambda mac: []

        localization = self.view.locate_by_mac(
            "00900b6951a6",
            arp_records=[
                {
                    "device_id": "13",
                    "port_id": "101",
                    "ipv4_address": "172.25.254.251",
                    "mac_address": "00:90:0b:69:51:a6",
                    "ifName": "XGE1/0/5",
                }
            ],
        )

        self.assertEqual(localization["canonical"]["source"], "arp_direct")
        self.assertEqual(localization["canonical"]["port_id"], "101")

    def test_locate_by_mac_reraises_not_found_without_arp_port_context(self):
        self.views.lookup_fdb_by_mac = lambda mac: (_ for _ in ()).throw(ResponseNotFoundError())

        with self.assertRaises(ResponseNotFoundError):
            self.view.locate_by_mac(
                "00900b6951a6",
                arp_records=[{"ipv4_address": "172.25.254.251", "mac_address": "00:90:0b:69:51:a6"}],
            )


if __name__ == "__main__":
    unittest.main()

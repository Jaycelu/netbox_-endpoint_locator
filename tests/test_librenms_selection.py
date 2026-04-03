import importlib.util
import pathlib
import sys
import types
import unittest


def load_module(file_name, module_name):
    django_module = types.ModuleType("django")
    django_conf_module = types.ModuleType("django.conf")
    django_conf_module.settings = types.SimpleNamespace(PLUGINS_CONFIG={})
    requests_module = types.ModuleType("requests")
    requests_module.get = lambda *args, **kwargs: None

    sys.modules.setdefault("django", django_module)
    sys.modules["django.conf"] = django_conf_module
    sys.modules.setdefault("requests", requests_module)

    module_path = pathlib.Path(__file__).resolve().parents[1] / "netbox_endpoint_locator" / file_name
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


librenms = load_module("librenms.py", "librenms_under_test")
topology = load_module("topology.py", "topology_under_test")


class LibreNMSSelectionTests(unittest.TestCase):
    def test_format_mac_readable_adds_colons(self):
        self.assertEqual(librenms.format_mac_readable("9ce89518ffd6"), "9c:e8:95:18:ff:d6")

    def test_format_mac_ui_groups_mac_in_hyphenated_blocks(self):
        self.assertEqual(librenms.format_mac_ui("000c29da9004"), "000c-29da-9004")

    def test_pick_arp_record_prefers_exact_ip_match(self):
        records = [
            {"ipv4_address": "172.22.1.2", "mac_address": "aa:bb:cc:dd:ee:ff"},
            {"ipv4_address": "172.22.254.61", "mac_address": "9c:e8:95:18:ff:d6"},
        ]

        picked = librenms.pick_arp_record(records, "172.22.254.61")

        self.assertEqual(picked["ipv4_address"], "172.22.254.61")

    def test_build_device_vlan_map_uses_internal_vlan_ids(self):
        vlan_records = [
            {"vlan_id": 20000, "vlan_vlan": 997},
            {"vlan_id": 20001, "vlan_vlan": 509},
        ]

        mapping = librenms.build_device_vlan_map(vlan_records)

        self.assertEqual(mapping, {"20000": "997", "20001": "509"})

    def test_resolve_fdb_vlan_maps_internal_id_to_vlan_number(self):
        fdb_record = {"device_id": 10, "port_id": 101, "vlan_id": 20000}
        device_vlans = [{"vlan_id": 20000, "vlan_vlan": 997}]

        vlan = librenms.resolve_fdb_vlan(fdb_record, device_vlans=device_vlans)

        self.assertEqual(vlan, "997")

    def test_resolve_fdb_vlan_falls_back_to_arp_interface_hint(self):
        fdb_record = {"device_id": 10, "port_id": 101, "vlan_id": 20000}
        arp_record = {"ifName": "Vlan-interface997"}

        vlan = librenms.resolve_fdb_vlan(fdb_record, device_vlans=[], arp_record=arp_record)

        self.assertEqual(vlan, "997")

    def test_resolve_fdb_vlan_does_not_guess_access_vlan_when_fdb_id_is_unmapped(self):
        fdb_record = {"device_id": 10, "port_id": 101, "vlan_id": 20000}
        port_info = {"vlans": [{"vlan": 1, "untagged": 1}]}

        vlan = librenms.resolve_fdb_vlan(fdb_record, device_vlans=[], port_info=port_info)

        self.assertEqual(vlan, "")

    def test_extract_terminal_vlan_reads_vlan_interface_name(self):
        vlan = librenms.extract_terminal_vlan({"ifName": "Vlan-interface997"})

        self.assertEqual(vlan, "997")

    def test_extract_vlan_from_interface_fields_ignores_unrelated_vlan_keys(self):
        arp_record = {
            "ifVlan": 109,
            "ifName": "Vlan-interface997",
            "remote_interface": "Vlan-interface997",
        }

        vlan = librenms.extract_vlan_from_interface_fields(arp_record)

        self.assertEqual(vlan, "997")

    def test_records_wrap_single_dict_payloads(self):
        records = librenms._records({"ports_fdb": {"port_id": 202, "vlan_id": 997}})

        self.assertEqual(records, [{"port_id": 202, "vlan_id": 997}])

    def test_records_wrap_vlan_payloads(self):
        records = librenms._records({"vlans": {"vlan_id": 20000, "vlan_vlan": 997}})

        self.assertEqual(records, [{"vlan_id": 20000, "vlan_vlan": 997}])

    def test_lookup_arp_by_mac_uses_readable_mac_path(self):
        seen = {}

        original_get = librenms._get
        try:
            def fake_get(path):
                seen["path"] = path
                return {"arp": []}

            librenms._get = fake_get
            librenms.lookup_arp_by_mac("9ce89518ffd6")
        finally:
            librenms._get = original_get

        self.assertEqual(seen["path"], "/api/v0/resources/ip/arp/9c%3Ae8%3A95%3A18%3Aff%3Ad6")

    def test_lookup_device_vlans_uses_resources_vlan_endpoint(self):
        seen = {}

        original_get = librenms._get
        try:
            def fake_get(path):
                seen["path"] = path
                return {"vlans": []}

            librenms._get = fake_get
            librenms.lookup_device_vlans("10.0.0.1")
        finally:
            librenms._get = original_get

        self.assertEqual(seen["path"], "/api/v0/resources/vlans?hostname=10.0.0.1")

    def test_lookup_port_by_id_fetches_relations_separately(self):
        seen = []

        def fake_get(path):
            seen.append(path)
            if path.endswith("?with=device"):
                return {"port": [{"port_id": 101, "device": {"hostname": "10.0.0.1"}}]}
            if path.endswith("?with=vlans"):
                return {"port": [{"port_id": 101, "vlans": [{"vlan": 997}]}]}
            return {"port": [{"port_id": 101, "ifName": "BA2"}]}

        original_get = librenms._get
        try:
            librenms._get = fake_get
            result = librenms.lookup_port_by_id(101, with_relations=["device", "vlans"])
        finally:
            librenms._get = original_get

        self.assertEqual(
            seen,
            ["/api/v0/ports/101?with=device", "/api/v0/ports/101?with=vlans"],
        )
        self.assertEqual(result["device"]["hostname"], "10.0.0.1")
        self.assertEqual(result["vlans"], [{"vlan": 997}])

    def test_filter_fdb_records_by_mac_matches_normalized_values(self):
        records = [
            {"mac_address": "9c:e8:95:18:ff:d6", "vlan_id": 997},
            {"mac_address": "aa:bb:cc:dd:ee:ff", "vlan_id": 109},
        ]

        matched = librenms.filter_fdb_records_by_mac(records, "9ce89518ffd6")

        self.assertEqual(matched, [{"mac_address": "9c:e8:95:18:ff:d6", "vlan_id": 997}])

    def test_score_fdb_candidate_prefers_exact_arp_port(self):
        same_port = librenms.score_fdb_candidate(
            {"device_id": 10, "port_id": 101},
            preferred_port_ids={"101"},
            preferred_device_ids={"10"},
            preferred_vlans={"997"},
            candidate_vlan="997",
        )
        same_device_only = librenms.score_fdb_candidate(
            {"device_id": 10, "port_id": 202},
            preferred_port_ids={"101"},
            preferred_device_ids={"10"},
            preferred_vlans={"997"},
            candidate_vlan="997",
        )

        self.assertGreater(same_port, same_device_only)

    def test_pick_scored_candidate_uses_score_then_updated_at(self):
        candidates = [
            {"score": 250, "updated_at": "2025-01-01 00:00:00", "port_id": "101"},
            {"score": 250, "updated_at": "2025-03-01 00:00:00", "port_id": "202"},
            {"score": 100, "updated_at": "2025-04-01 00:00:00", "port_id": "303"},
        ]

        picked = librenms.pick_scored_candidate(candidates)

        self.assertEqual(picked["port_id"], "202")


class EdgeLocalizationTests(unittest.TestCase):
    def test_pick_edge_candidate_prefers_downstream_leaf_port(self):
        canonical = {
            "candidate_id": "10:100",
            "device_id": "10",
            "port_id": "100",
            "vlan": "514",
            "interface": "Bridge-Aggregation203",
            "description": "To_B2_1F_02C_11U_H3C-S5130",
            "updated_at": "2026-04-02 10:00:00",
        }
        downstream = {
            "candidate_id": "20:200",
            "device_id": "20",
            "port_id": "200",
            "vlan": "514",
            "interface": "Gi2/0/46",
            "description": "GigabitEthernet2/0/46 Interface",
            "updated_at": "2026-04-02 10:01:00",
        }

        selected = topology.pick_edge_candidate(
            canonical,
            [canonical, downstream],
            links_by_device={
                "10": [{"active": 1, "local_port_id": "101", "remote_device_id": "20"}],
                "20": [],
            },
            stack_members_by_device={"10": {"100": {"101"}}, "20": {}},
        )

        self.assertEqual(selected["selected"]["candidate_id"], "20:200")
        self.assertEqual(selected["path"], ["10:100", "20:200"])

    def test_pick_edge_candidate_keeps_canonical_when_vlan_differs(self):
        canonical = {
            "candidate_id": "10:100",
            "device_id": "10",
            "port_id": "100",
            "vlan": "514",
            "interface": "Bridge-Aggregation203",
            "description": "To_B2_1F_02C_11U_H3C-S5130",
            "updated_at": "2026-04-02 10:00:00",
        }
        downstream_other_vlan = {
            "candidate_id": "20:200",
            "device_id": "20",
            "port_id": "200",
            "vlan": "997",
            "interface": "Gi2/0/46",
            "description": "GigabitEthernet2/0/46 Interface",
            "updated_at": "2026-04-02 10:01:00",
        }

        selected = topology.pick_edge_candidate(
            canonical,
            [canonical, downstream_other_vlan],
            links_by_device={
                "10": [{"active": 1, "local_port_id": "101", "remote_device_id": "20"}],
                "20": [],
            },
            stack_members_by_device={"10": {"100": {"101"}}, "20": {}},
        )

        self.assertEqual(selected["selected"]["candidate_id"], "10:100")

    def test_pick_edge_candidate_keeps_canonical_without_downstream_path(self):
        canonical = {
            "candidate_id": "10:100",
            "device_id": "10",
            "port_id": "100",
            "vlan": "514",
            "interface": "Bridge-Aggregation203",
            "description": "To_B2_1F_02C_11U_H3C-S5130",
            "updated_at": "2026-04-02 10:00:00",
        }
        unrelated = {
            "candidate_id": "30:300",
            "device_id": "30",
            "port_id": "300",
            "vlan": "514",
            "interface": "Gi1/0/24",
            "description": "GigabitEthernet1/0/24 Interface",
            "updated_at": "2026-04-02 10:01:00",
        }

        selected = topology.pick_edge_candidate(
            canonical,
            [canonical, unrelated],
            links_by_device={"10": [], "30": []},
            stack_members_by_device={"10": {}, "30": {}},
        )

        self.assertEqual(selected["selected"]["candidate_id"], "10:100")

    def test_pick_edge_candidate_infers_downstream_from_uplink_description(self):
        canonical = {
            "candidate_id": "13:2024",
            "device_id": "13",
            "device_key": "172.22.38.13",
            "hostname": "172.22.38.13",
            "device_name": "smzy_b1_lan_6520",
            "port_id": "2024",
            "vlan": "523",
            "interface": "Bridge-Aggregation204",
            "description": "To_BGW-B7-03_172.22.254.7",
            "updated_at": "2026-04-02T10:37:25.000000Z",
        }
        downstream = {
            "candidate_id": "12:1639",
            "device_id": "12",
            "device_key": "172.22.254.7",
            "hostname": "172.22.254.7",
            "device_name": "b2_4f_03c_26u_h3c-s5130",
            "port_id": "1639",
            "vlan": "523",
            "interface": "GigabitEthernet1/0/3",
            "description": "GigabitEthernet1/0/3 Interface",
            "updated_at": "2026-04-02T04:36:33.000000Z",
        }

        selected = topology.pick_edge_candidate(
            canonical,
            [canonical, downstream],
            links_by_device={"12": [], "13": []},
            stack_members_by_device={"12": {}, "13": {}},
        )

        self.assertEqual(selected["graph"]["13:2024"], {"12:1639"})
        self.assertEqual(selected["selected"]["candidate_id"], "12:1639")
        self.assertEqual(selected["path"], ["13:2024", "12:1639"])


if __name__ == "__main__":
    unittest.main()

"""Unit tests for choosing scenario inputs (``scenario_inputs.py``).

Catalog entries here are synthetic dictionaries shaped like the data page's
item views and STAC Items; no file is read and no model is called.

Run: python3 -m unittest scripts/test_scenario_inputs.py -v   (from the repository root)
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scenario_inputs as si  # noqa: E402
import workspace_scenarios as ws  # noqa: E402

PLACE = "希腊色萨利平原"


def view(item_id, caps, day, collection="col_sar", *, place=PLACE, legacy=False, audience="user", type_label="SAR"):
    return {
        "id": item_id, "capabilities": caps, "date": day, "collection_id": collection,
        "collection_title": collection, "place": place, "legacy": legacy, "audience": audience,
        "type_label": type_label, "event": None, "self_check_for": None, "usable_in": ["x"],
    }


def entries(*views):
    return [{"view": v, "stac": None} for v in views]


THESSALY = entries(
    view("sar.1", ["sar_vv"], "2023-09-01"),
    view("sar.2", ["sar_vv"], "2023-09-13"),
    view("old.before", ["sar_vv"], "2023-09-01", "legacy", legacy=True),
    view("old.after", ["sar_vv"], "2023-09-13", "legacy", legacy=True),
    view("s2.1", ["optical_green_nir"], "2023-08-31", "col_s2"),
    view("s2.2", ["optical_green_nir"], "2023-09-10", "col_s2"),
    view("wc.1", ["landcover"], "2021-01-01", "col_wc"),
    view("ems.1", ["reference_extent"], "2023-09-07", "col_ems"),
    view("val.1", ["sar_vv"], "2024-10-20", "col_val", place="巴伦西亚"),
)


class Resolve(unittest.TestCase):
    def resolve(self, scenario, content="", attachments=(), place=PLACE):
        return si.resolve(ws.TASK_SCENARIOS[scenario], THESSALY, place=place, content=content,
                          attachments=list(attachments), previous=[])

    def test_pair_prefers_new_uploads_and_orders_by_date(self):
        roles, _, source = self.resolve("water_change")
        self.assertEqual(roles, {"before": "sar.1", "after": "sar.2"})
        self.assertEqual(source, "message")

    def test_landcover_impact_fills_three_slots(self):
        roles, _, _ = self.resolve("landcover_impact")
        self.assertEqual(roles, {"before": "sar.1", "after": "sar.2", "landcover": "wc.1"})

    def test_reference_compare_takes_scene_closest_to_reference(self):
        roles, _, _ = self.resolve("reference_compare")
        self.assertEqual(roles["reference"], "ems.1")
        self.assertIn(roles["scene"], ("sar.1", "sar.2"))  # 9/1 and 9/13 are both 6 days from 9/7
        roles, _, _ = self.resolve("reference_compare", content="9 月 13 日的结果和 EMS 比一比")
        self.assertEqual(roles["scene"], "sar.2")

    def test_optical_uses_date_in_message_else_latest(self):
        self.assertEqual(self.resolve("optical_water", "8 月 31 日的光学")[0], {"scene": "s2.1"})
        self.assertEqual(self.resolve("optical_water")[0], {"scene": "s2.2"})

    def test_attachments_sorted_by_date(self):
        roles, _, source = self.resolve("water_change", attachments=["sar.2", "sar.1"], place=None)
        self.assertEqual(roles, {"before": "sar.1", "after": "sar.2"})
        self.assertEqual(source, "attachment")

    def test_one_attachment_completes_pair_from_its_group(self):
        roles, _, _ = self.resolve("water_change", attachments=["sar.2"], place=None)
        self.assertEqual(roles, {"before": "sar.1", "after": "sar.2"})
        roles, _, _ = self.resolve("water_change", attachments=["sar.1"], place=None)
        self.assertEqual(roles, {"before": "sar.1", "after": "sar.2"})

    def test_place_without_needed_data_leaves_slots_empty(self):
        roles, _, _ = self.resolve("landcover_impact", place="巴伦西亚")
        self.assertNotIn("landcover", roles)

    def test_places_summary_lists_types_and_dates_not_items(self):
        summary = {p["place"]: p for p in si.places(THESSALY)}
        self.assertEqual(summary[PLACE]["data"]["光学"], ["2023-08-31", "2023-09-10"])
        self.assertNotIn("sar.1", str(summary))


class FakeCatalog:
    def __init__(self, items):
        self.items = {i["id"]: i for i in items}

    def get(self, item_id):
        item = self.items.get(item_id)
        return (item, {"id": "c", "title": "组"}) if item else None


def sar_item(item_id, day, unit="1"):
    return {"id": item_id, "properties": {"datetime": f"{day}T00:00:00Z", "workspace:data_type": "sar"},
            "assets": {"data": {"bands": [{"name": "VV", "unit": unit}]}}}


class Check(unittest.TestCase):
    spec = ws.TASK_SCENARIOS["water_change"]

    def test_valid_pair(self):
        catalog = FakeCatalog([sar_item("a", "2023-09-01"), sar_item("b", "2023-09-13")])
        chosen, problem = si.check(self.spec, {"before": "a", "after": "b"}, catalog)
        self.assertIsNone(problem)
        self.assertEqual(si.input_kind(chosen), ("sar_backscatter", "linear"))

    def test_before_must_be_earlier(self):
        catalog = FakeCatalog([sar_item("a", "2023-09-01"), sar_item("b", "2023-09-13")])
        _, problem = si.check(self.spec, {"before": "b", "after": "a"}, catalog)
        self.assertIn("应早于", problem)

    def test_mixed_scales_rejected(self):
        catalog = FakeCatalog([sar_item("a", "2023-09-01"), sar_item("b", "2023-09-13", unit="dB")])
        _, problem = si.check(self.spec, {"before": "a", "after": "b"}, catalog)
        self.assertIn("数值尺度", problem)

    def test_wrong_type_for_slot_rejected(self):
        landcover = {"id": "wc", "properties": {"datetime": "2021-01-01T00:00:00Z", "workspace:data_type": "classification"},
                     "assets": {"data": {"classification:classes": [{"value": 10, "name": "tree_cover"}]}}}
        catalog = FakeCatalog([sar_item("a", "2023-09-01"), landcover])
        _, problem = si.check(self.spec, {"before": "a", "after": "wc"}, catalog)
        self.assertIn("需要", problem)


if __name__ == "__main__":
    unittest.main()

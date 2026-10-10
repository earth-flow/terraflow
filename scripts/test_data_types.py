"""Unit tests for the data type rules and STAC Item layout (``data_types.py``).

All file profiles here are synthetic dictionaries shaped like the output of
``inspect_geodata.py``; no raster or vector file is read.

Run: python3 -m unittest scripts/test_data_types.py -v   (from the repository root)
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import data_catalog  # noqa: E402
import data_types as dt  # noqa: E402

UTM = {"crs": "EPSG:32634", "projected": True, "projection_method": "Transverse Mercator"}
BBOX = [22.0, 39.49, 22.13, 39.59]


def raster(bands, *, resolution=10.0, shape=(100, 100), integer=False, **extra):
    """A synthetic raster profile."""
    profile = {
        "ok": True, "kind": "raster", **UTM, "shape": list(shape),
        "transform": [resolution, 0, 586300, 0, -resolution, 4382310],
        "resolution": [resolution, resolution], "bounds": [586300, 4372190, 596740, 4382310],
        "bbox_wgs84": BBOX, "count": len(bands), "integer": integer, "bands": [],
    }
    for index, band in enumerate(bands, start=1):
        profile["bands"].append({"index": index, "description": None, "nodata": None, **band})
    profile.update(extra)
    return profile


def upload(profile, name="a.tif", fmt="geotiff", n=1, **extra):
    return {"n": n, "original_name": name, "format": fmt, "bytes": 10, "sha256": "ab" * 32, "profile": profile, **extra}


def statuses(result):
    return result["status"], [m["text"] for m in result["messages"] if m["level"] == "error"]


SAR_FIELDS = {"datetime": "2023-09-13", "polarizations": ["VV", "VH"], "frequency_band": "C",
              "orbit_state": "descending", "relative_orbit": 7, "value_scale": "linear",
              "backscatter": "gamma0_terrain"}


class SarRules(unittest.TestCase):
    def profile(self, dtype="float32", negative=0.0):
        stats = {"negative_fraction": negative, "minimum": 0.001, "p2": 0.004, "p98": 0.5}
        return raster([{"dtype": dtype, "nodata": -32768.0, "statistics": stats}] * 2)

    def test_two_polarizations_accepted(self):
        status, errors = statuses(dt.validate("sar", [upload(self.profile())], SAR_FIELDS))
        self.assertEqual((status, errors), ("ok", []))

    def test_polarization_count_must_match_bands(self):
        fields = {**SAR_FIELDS, "polarizations": ["VV"]}
        status, errors = statuses(dt.validate("sar", [upload(self.profile())], fields))
        self.assertEqual(status, "rejected")
        self.assertIn("一一对应", errors[0])

    def test_integer_dn_rejected(self):
        status, errors = statuses(dt.validate("sar", [upload(self.profile("uint16"))], SAR_FIELDS))
        self.assertEqual(status, "rejected")
        self.assertIn("浮点", errors[0])

    def test_negative_linear_values_warn(self):
        result = dt.validate("sar", [upload(self.profile(negative=0.4))], SAR_FIELDS)
        self.assertEqual(result["status"], "warning")

    def test_missing_crs_rejected(self):
        profile = {**self.profile(), "crs": None, "crs_wkt": None}
        status, errors = statuses(dt.validate("sar", [upload(profile)], SAR_FIELDS))
        self.assertEqual(status, "rejected")
        self.assertIn("坐标参考系", errors[0])

    def test_unsupported_format_rejected(self):
        status, errors = statuses(dt.validate("sar", [upload(self.profile(), name="a.jp2", fmt=None)], SAR_FIELDS))
        self.assertEqual(status, "rejected")
        self.assertIn("只接受", errors[0])

    def test_item_layout(self):
        item = dt.build_item("sar", "u.1", {"id": "col_x", "title": "t", "place": "p"}, [upload(self.profile())],
                             SAR_FIELDS, audience="user", created="2026-10-10T00:00:00Z", hrefs={1: "/r/1.tif"})
        p = item["properties"]
        self.assertEqual(p["sar:polarizations"], ["VV", "VH"])
        self.assertEqual(p["sat:relative_orbit"], 7)
        self.assertEqual(p["workspace:data_type"], "sar")
        self.assertEqual(p["workspace:verification"], "declared")
        self.assertEqual([b["name"] for b in item["assets"]["data"]["bands"]], ["VV", "VH"])
        self.assertEqual(item["assets"]["data"]["bands"][0]["nodata"], -32768)
        self.assertEqual(item["assets"]["data"]["file:checksum"], "1220" + "ab" * 32)
        self.assertEqual(item["assets"]["data"]["proj:code"], "EPSG:32634")
        self.assertIn(dt.EXTENSIONS["sar"], item["stac_extensions"])
        # Project fields use only the workspace: prefix; everything else is STAC core or a declared extension.
        prefixes = {key.split(":")[0] for key in p if ":" in key}
        self.assertEqual(prefixes, {"sar", "sat", "workspace"})

    def test_vv_band_makes_item_usable_in_scenarios(self):
        item = dt.build_item("sar", "u.1", {"id": "col_x"}, [upload(self.profile())], SAR_FIELDS,
                             audience="user", created="2026-10-10T00:00:00Z", hrefs={1: "/r/1.tif"})
        scene = data_catalog.item_scene(item, {"id": "col_x", "title": "t"})
        self.assertEqual((scene["input_kind"], scene["band_index"], scene["band_count"]), ("sar_backscatter", 1, 2))
        vh_only = {**SAR_FIELDS, "polarizations": ["VH"]}
        single = raster([{"dtype": "float32", "statistics": {}}])
        item = dt.build_item("sar", "u.2", {"id": "col_x"}, [upload(single)], vh_only,
                             audience="user", created="2026-10-10T00:00:00Z", hrefs={1: "/r/1.tif"})
        self.assertIsNone(data_catalog.item_scene(item, {"id": "col_x", "title": "t"}))


class OpticalRules(unittest.TestCase):
    def files(self, scale="0.0001"):
        stats = {"p2": 1200.0, "p98": 4000.0}
        ten = raster([{"dtype": "uint16", "statistics": stats}] * 4, integer=True)
        twenty = raster([{"dtype": "uint16", "statistics": stats}], resolution=20.0, shape=(50, 50), integer=True)
        scl = raster([{"dtype": "uint8", "statistics": {"distinct": [3, 4, 5, 6, 8]}}], resolution=20.0,
                     shape=(50, 50), integer=True)
        names = ["blue", "green", "red", "nir"]
        return [
            upload(ten, n=1, role="data", bands=[{"common_name": c, "scale": scale, "offset": "-0.1"} for c in names]),
            upload(twenty, n=2, role="data", bands=[{"common_name": "swir16", "scale": scale, "offset": "-0.1"}]),
            upload(scl, n=3, role="cloud-mask"),
        ]

    FIELDS = {"datetime": "2023-09-15", "processing_level": "L2"}

    def test_multi_file_item_accepted(self):
        status, errors = statuses(dt.validate("optical", self.files(), self.FIELDS))
        self.assertEqual((status, errors), ("ok", []))

    def test_integer_reflectance_needs_scale(self):
        status, errors = statuses(dt.validate("optical", self.files(scale=""), self.FIELDS))
        self.assertEqual(status, "rejected")
        self.assertIn("scale", errors[0])

    def test_duplicate_common_name_rejected(self):
        files = self.files()
        files[1]["bands"][0]["common_name"] = "nir"
        status, errors = statuses(dt.validate("optical", files, self.FIELDS))
        self.assertEqual(status, "rejected")
        self.assertIn("重复", errors[-1])

    def test_non_multiple_resolution_rejected(self):
        files = self.files()
        files[1]["profile"]["resolution"] = [15.0, 15.0]
        status, errors = statuses(dt.validate("optical", files, self.FIELDS))
        self.assertEqual(status, "rejected")

    def test_item_has_eo_bands_and_cloud_mask(self):
        item = dt.build_item("optical", "u.1", {"id": "c"}, self.files(), self.FIELDS, audience="user",
                             created="2026-10-10T00:00:00Z", hrefs={1: "/a", 2: "/b", 3: "/c"})
        commons = [b["eo:common_name"] for k in ("data", "data-2") for b in item["assets"][k]["bands"]]
        self.assertEqual(commons, ["blue", "green", "red", "nir", "swir16"])
        self.assertEqual(item["assets"]["data"]["bands"][0]["raster:scale"], 0.0001)
        self.assertIn("cloud", item["assets"]["cloud-mask"]["roles"])
        self.assertEqual(item["properties"]["processing:level"], "L2")
        self.assertIsNone(data_catalog.item_scene(item, {"id": "c", "title": "t"}))


class DemRules(unittest.TestCase):
    FIELDS = {"start_date": "2011-01-01", "end_date": "2015-12-31", "surface": "dsm", "vertical_datum": "EGM2008"}

    def profile(self, minimum=50.0, maximum=600.0):
        return raster([{"dtype": "float32", "statistics": {"minimum": minimum, "maximum": maximum}}],
                      crs="EPSG:4326", projected=False)

    def test_accepted_with_info_about_projection(self):
        result = dt.validate("dem", [upload(self.profile())], self.FIELDS)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(any("投影" in m["text"] for m in result["messages"]))

    def test_implausible_range_warns(self):
        result = dt.validate("dem", [upload(self.profile(maximum=60000))], self.FIELDS)
        self.assertEqual(result["status"], "warning")

    def test_reversed_period_rejected(self):
        fields = {**self.FIELDS, "start_date": "2016-01-01"}
        self.assertEqual(dt.validate("dem", [upload(self.profile())], fields)["status"], "rejected")

    def test_item_uses_time_range(self):
        item = dt.build_item("dem", "u.1", {"id": "c"}, [upload(self.profile())], self.FIELDS, audience="user",
                             created="2026-10-10T00:00:00Z", hrefs={1: "/a"})
        p = item["properties"]
        self.assertIsNone(p["datetime"])
        self.assertEqual((p["start_datetime"], p["end_datetime"]), ("2011-01-01T00:00:00Z", "2015-12-31T23:59:59Z"))
        self.assertEqual(item["assets"]["data"]["bands"][0]["unit"], "m")


class VectorRules(unittest.TestCase):
    def profile(self, **extra):
        base = {"ok": True, "kind": "vector", "crs": "EPSG:4326", "bbox_wgs84": BBOX, "bounds": BBOX,
                "geometry_types": ["MultiPolygon"], "feature_count": 12, "invalid_geometries": 0,
                "columns": [{"name": "notation", "type": "object"}], "layers": ["x"], "layer": "x"}
        return {**base, **extra}

    FIELDS = {"datetime": "2023-09-10", "purpose": "reference", "origin": "manual",
              "description": "Copernicus EMS 洪水范围", "label_property": "notation"}

    def test_reference_accepted(self):
        result = dt.validate("vector", [upload(self.profile(), name="f.geojson", fmt="geojson")], self.FIELDS)
        self.assertEqual(result["status"], "ok")

    def test_shapefile_without_prj_rejected(self):
        profile = {"ok": True, "kind": "vector", "shapefile_missing": [".prj"], "crs": None, "layers": []}
        status, errors = statuses(dt.validate("vector", [upload(profile, name="f.zip", fmt="zip")], self.FIELDS))
        self.assertEqual(status, "rejected")
        self.assertIn(".prj", errors[0])

    def test_unknown_label_column_rejected(self):
        fields = {**self.FIELDS, "label_property": "class"}
        status, _ = statuses(dt.validate("vector", [upload(self.profile(), name="f.geojson", fmt="geojson")], fields))
        self.assertEqual(status, "rejected")

    def test_item_label_fields(self):
        item = dt.build_item("vector", "u.1", {"id": "c"}, [upload(self.profile(), name="f.geojson", fmt="geojson")],
                             self.FIELDS, audience="user", created="2026-10-10T00:00:00Z", hrefs={1: "/a"})
        p = item["properties"]
        self.assertEqual((p["label:type"], p["label:properties"]), ("vector", ["notation"]))
        self.assertEqual(item["assets"]["data"]["type"], "application/geo+json")
        self.assertEqual(item["assets"]["data"]["vector:geometry_types"], ["MultiPolygon"])
        self.assertNotIn("proj:bbox", item["assets"]["data"])


class ClassificationRules(unittest.TestCase):
    def profile(self, distinct, nodata=0.0):
        return raster([{"dtype": "uint8", "nodata": nodata, "statistics": {"distinct": distinct}}], integer=True)

    def fields(self, preset="esa_worldcover", origin="observed"):
        return {"datetime": "2021-01-01", "origin": origin, "classes": dt.CLASS_PRESETS[preset]["classes"]}

    def test_worldcover_accepted(self):
        result = dt.validate("classification", [upload(self.profile([0, 10, 40, 50, 80]))], self.fields())
        self.assertEqual(result["status"], "ok")

    def test_undeclared_value_rejected(self):
        status, errors = statuses(dt.validate("classification", [upload(self.profile([10, 37, 80]))], self.fields()))
        self.assertEqual(status, "rejected")
        self.assertIn("37", errors[0])

    def test_float_raster_rejected(self):
        profile = raster([{"dtype": "float32", "statistics": {}}])
        self.assertEqual(dt.validate("classification", [upload(profile)], self.fields())["status"], "rejected")

    def test_auto_generated_class_names_rejected(self):
        fields = {"datetime": "2021-01-01", "origin": "model_estimate",
                  "classes": [{"value": 10, "name": "class_10"}, {"value": 37, "name": "class_37"}]}
        status, errors = statuses(dt.validate("classification", [upload(self.profile([10, 37], nodata=None))], fields))
        self.assertEqual(status, "rejected")
        self.assertIn("自动生成", errors[0])

    def test_extra_value_still_suggests_preset_so_it_is_flagged(self):
        profile = self.profile([0, 10, 20, 37, 40, 80])
        suggestion = dt.suggest("classification", upload(profile))
        self.assertEqual(suggestion["preset"], "esa_worldcover")
        fields = {"datetime": "2021-01-01", "origin": "model_estimate", "classes": suggestion["fields"]["classes"]}
        status, errors = statuses(dt.validate("classification", [upload(profile)], fields))
        self.assertEqual(status, "rejected")
        self.assertIn("37", errors[0])

    def test_manual_label_needs_description(self):
        result = dt.validate("classification", [upload(self.profile([0, 1], nodata=None))],
                             self.fields("water_binary", "manual"))
        self.assertEqual(result["status"], "rejected")

    def test_water_binary_usable_in_scenarios_worldcover_not(self):
        water = dt.build_item("classification", "u.1", {"id": "c"}, [upload(self.profile([0, 1], nodata=None))],
                              {**self.fields("water_binary", "model_estimate")}, audience="user",
                              created="2026-10-10T00:00:00Z", hrefs={1: "/a"})
        self.assertEqual(data_catalog.item_scene(water, {"id": "c", "title": "t"})["input_kind"], "water_labels")
        cover = dt.build_item("classification", "u.2", {"id": "c"}, [upload(self.profile([10, 80]))], self.fields(),
                              audience="user", created="2026-10-10T00:00:00Z", hrefs={1: "/a"})
        self.assertIsNone(data_catalog.item_scene(cover, {"id": "c", "title": "t"}))
        self.assertTrue(cover["assets"]["data"]["classification:classes"][0]["nodata"])


class Suggestions(unittest.TestCase):
    def test_s2_band_names_from_descriptions(self):
        profile = raster([{"dtype": "uint16", "description": d, "statistics": {}} for d in ("B02", "B03", "B04", "B08")],
                         integer=True)
        out = dt.suggest("optical", upload(profile, name="2023-09-15_s2l2a_10m.tif"))
        self.assertEqual([b["common_name"] for b in out["bands"]], ["blue", "green", "red", "nir"])
        self.assertEqual(out["fields"]["datetime"], "2023-09-15")

    def test_band_name_from_file_name_and_scl_mask(self):
        one = raster([{"dtype": "uint16", "statistics": {}}], integer=True)
        self.assertEqual(dt.suggest("optical", upload(one, name="20230915_B11_20m.tif"))["bands"][0]["common_name"], "swir16")
        scl = raster([{"dtype": "uint8", "statistics": {"distinct": [3, 4, 8]}}], integer=True)
        self.assertEqual(dt.suggest("optical", upload(scl, name="x_SCL_20m.tif"))["role"], "cloud-mask")

    def test_sar_polarizations_and_classification_preset(self):
        sar = raster([{"dtype": "float32", "description": "VV", "statistics": {}},
                      {"dtype": "float32", "description": "VH", "statistics": {}}])
        self.assertEqual(dt.suggest("sar", upload(sar))["fields"]["polarizations"], ["VV", "VH"])
        cover = raster([{"dtype": "uint8", "nodata": 0, "statistics": {"distinct": [0, 10, 80]}}], integer=True)
        self.assertEqual(dt.suggest("classification", upload(cover))["preset"], "esa_worldcover")


class LegacyConversion(unittest.TestCase):
    def test_fixture_scene_keeps_id_and_links_source(self):
        scene = {
            "id": "thessaly_2023_s1.after", "title": "2023-09-13 · Sentinel-1", "collection_id": "thessaly_2023_s1",
            "collection_title": "色萨利", "input_kind": "sar_backscatter", "input_scale": "linear",
            "category": "observed", "source_type": "sentinel1_rtc_gamma0", "sensor": "Sentinel-1 SAR（VV）",
            "acquired": "2023-09-13", "path": "/r/after.tif",
            "source": {"datetime": "2023-09-13T04:32:10Z", "polarization": "VV", "orbit_state": "descending",
                       "relative_orbit": 7, "stac_item_url": "https://example.invalid/item"},
        }
        profile = raster([{"dtype": "float32", "nodata": -32768.0, "statistics": {}}])
        item = data_catalog.legacy_item(scene, profile)
        p = item["properties"]
        self.assertEqual(item["id"], "thessaly_2023_s1.after")
        self.assertEqual(p["workspace:backscatter"], "gamma0_terrain")
        self.assertEqual(p["workspace:verification"], "source_linked")
        self.assertEqual(p["sar:frequency_band"], "C")
        self.assertEqual(item["assets"]["data"]["bands"][0]["unit"], "1")
        self.assertIn("derived_from", [link["rel"] for link in item["links"]])


if __name__ == "__main__":
    unittest.main()

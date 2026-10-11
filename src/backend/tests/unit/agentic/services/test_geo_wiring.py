"""Static wiring checks for Terrabox water flows (synthetic flow dicts, no rasters)."""

from terraflow.agentic.services.flow_run_error_classification import RunErrorKind, classify_run_error
from terraflow.agentic.services.geo_wiring import geo_wiring_errors


def _flow(nodes, edges):
    return {
        "data": {
            "nodes": [{"id": i, "data": {"type": t}} for i, t in nodes],
            "edges": [
                {"data": {"sourceHandle": {"id": s, "name": so}, "targetHandle": {"id": t, "fieldName": tf}}}
                for s, so, t, tf in edges
            ],
        }
    }


NODES = [
    ("pair", "WaterInputSet"),
    ("seg_after", "TerraboxGeoRasterThresholdSegmentation"),
    ("seg_before", "TerraboxGeoRasterThresholdSegmentation"),
    ("mask_after", "ToolResultField"),
    ("mask_before", "ToolResultField"),
    ("evidence", "TerraboxGeoWaterWaterEvidence"),
]
MASKS = [
    ("seg_after", "api_run_model", "mask_after", "result"),
    ("seg_before", "api_run_model", "mask_before", "result"),
]


def test_correct_two_date_flow_has_no_errors():
    edges = [
        ("pair", "after_path", "seg_after", "input_path"),
        ("pair", "before_path", "seg_before", "input_path"),
        *MASKS,
        ("mask_after", "value", "evidence", "after_path"),
        ("mask_before", "value", "evidence", "before_path"),
    ]
    assert geo_wiring_errors(_flow(NODES, edges)) == []


def test_threshold_fed_with_another_mask_is_flagged():
    # N041: the before step read the after step's mask; WaterEvidence then refused (valid_mask_mismatch).
    edges = [
        ("pair", "after_path", "seg_after", "input_path"),
        ("seg_after", "api_run_model", "mask_after", "result"),
        ("mask_after", "value", "seg_before", "input_path"),
    ]
    errors = geo_wiring_errors(_flow(NODES, edges))
    assert len(errors) == 1
    assert errors[0].startswith("seg_before: input_path comes from the mask written by seg_after")


def test_both_dates_from_the_same_scene_are_flagged():
    edges = [("pair", "after_path", "seg_after", "input_path"), ("pair", "after_path", "seg_before", "input_path")]
    assert any("before_path" in e and "after_path" in e for e in geo_wiring_errors(_flow(NODES, edges)))


def test_swapped_evidence_inputs_are_flagged():
    edges = [
        ("pair", "after_path", "seg_after", "input_path"),
        ("pair", "before_path", "seg_before", "input_path"),
        *MASKS,
        ("mask_after", "value", "evidence", "before_path"),
        ("mask_before", "value", "evidence", "after_path"),
    ]
    assert len(geo_wiring_errors(_flow(NODES, edges))) == 2


def test_wiring_errors_are_fixable():
    assert classify_run_error("Geo wiring error: seg_before: input_path ...") is RunErrorKind.FIXABLE

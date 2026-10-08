from pathlib import Path

import pytest
from lfx.components.geo_workflow.raster_pair_gate import RasterPairGateComponent, preflight_outputs
from lfx.components.geo_workflow.tool_result_field import read_field
from lfx.components.geo_workflow.water_input_set import list_input_sets, read_set_metadata
from lfx.schema.data import Data


def _result(outputs: dict) -> list[Data]:
    """Shape of a Terrabox component result: a list holding one Data payload."""
    return [Data(data={"success": True, "outputs": outputs, "metadata": {}})]


def _touch_pair(directory: Path, *, mismatch: bool = False) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    names = ["before.tif", "after.tif"] + (["mismatch.tif"] if mismatch else [])
    for name in names:
        (directory / name).write_bytes(b"II*\x00")


def test_read_field_follows_dicts_and_list_indexes():
    result = _result({"output_path": "/tmp/mask.tif", "before": {"resolution": [10.0, 9.5]}})
    assert read_field(result, "outputs.output_path") == "/tmp/mask.tif"
    assert read_field(result, "outputs.before.resolution.1") == 9.5
    with pytest.raises(ValueError, match="not found"):
        read_field(result, "outputs.missing")


def test_preflight_outputs_unwraps_terrabox_payload():
    outputs = preflight_outputs(_result({"execution_blocked": True, "reason_code": "shape_mismatch"}))
    assert outputs["reason_code"] == "shape_mismatch"
    assert preflight_outputs(None) == {}


@pytest.mark.parametrize(
    ("outputs", "passed", "reason"),
    [
        ({"success": True, "execution_blocked": False, "failed_checks": []}, True, None),
        ({"success": False, "execution_blocked": True, "reason_code": "crs_mismatch"}, False, "crs_mismatch"),
        ({}, False, "preflight_unavailable"),
    ],
)
def test_gate_decision(outputs, passed, reason):
    gate = RasterPairGateComponent()
    gate.set(preflight=_result(outputs) if outputs else [], before_path="a.tif", after_path="b.tif")
    decision = gate._decision()
    assert decision["passed"] is passed
    assert decision["reason_code"] == reason


def test_list_input_sets_finds_fixtures_and_finalized_uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("TERRAFLOW_WATER_RUNTIME_DIR", str(tmp_path))
    _touch_pair(tmp_path / "demo-fixtures", mismatch=True)
    _touch_pair(tmp_path / "demo-fixtures" / "event_a")
    (tmp_path / "demo-fixtures" / "event_a" / "fixture.json").write_text(
        '{"type": "sentinel1_rtc_gamma0", "label": "A"}', encoding="utf-8"
    )
    _touch_pair(tmp_path / "web-uploads" / "upl_pending")
    _touch_pair(tmp_path / "web-uploads" / "upl_ready")
    (tmp_path / "web-uploads" / "upl_ready" / "fixture.json").write_text('{"type": "uploaded"}', encoding="utf-8")
    (tmp_path / "demo-fixtures" / "incomplete").mkdir()

    sets = list_input_sets()
    assert set(sets) == {"demo-fixtures", "event_a", "upload-upl_ready"}
    assert read_set_metadata(sets["event_a"])["input_kind"] == "sar_backscatter"
    assert read_set_metadata(sets["demo-fixtures"])["input_kind"] == "water_labels"


def test_list_input_sets_without_runtime_dir(monkeypatch):
    monkeypatch.delenv("TERRAFLOW_WATER_RUNTIME_DIR", raising=False)
    assert list_input_sets() == {}

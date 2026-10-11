"""Static wiring checks for Terrabox water flows the assistant builds.

The flow-run verification only catches errors that raise. A mis-wired water
flow usually runs: a threshold step fed with another step's mask, or a
two-date comparison whose "before" mask comes from the after scene, produces
a plausible number (or a gate refusal) instead of an exception. These checks
read the wiring alone (no run, no model) and return messages the agent can
act on; the verification loop feeds them back as fixable errors.
"""

from __future__ import annotations

from typing import Any

THRESHOLD = "TerraboxGeoRasterThresholdSegmentation"
EVIDENCE = "TerraboxGeoWaterWaterEvidence"
PAIR_INPUT = "WaterInputSet"
FIELD = "ToolResultField"
DATES = 2  # before and after


def _nodes(flow: dict[str, Any]) -> dict[str, str]:
    """Node id -> component type."""
    out = {}
    for node in (flow.get("data") or {}).get("nodes") or []:
        if isinstance(node, dict) and node.get("id"):
            out[node["id"]] = (node.get("data") or {}).get("type") or ""
    return out


def _inputs(flow: dict[str, Any]) -> dict[tuple[str, str], tuple[str, str]]:
    """(target id, field) -> (source id, source output name)."""
    out = {}
    for edge in (flow.get("data") or {}).get("edges") or []:
        data = (edge or {}).get("data") or {}
        source, target = data.get("sourceHandle") or {}, data.get("targetHandle") or {}
        if source.get("id") and target.get("id") and target.get("fieldName"):
            out[(target["id"], target["fieldName"])] = (source["id"], source.get("name") or "")
    return out


def _origin(node_id: str, field: str, types: dict[str, str], inputs: dict) -> tuple[str, str] | None:
    """The component that really produces a value: skips ToolResultField hops."""
    seen = set()
    link = inputs.get((node_id, field))
    while link and link[0] not in seen:
        seen.add(link[0])
        if types.get(link[0]) != FIELD:
            return link
        link = inputs.get((link[0], "result"))
    return None


def geo_wiring_errors(flow: dict[str, Any]) -> list[str]:
    """Wiring mistakes in a Terrabox water flow; empty when none are found."""
    types, inputs = _nodes(flow), _inputs(flow)
    errors: list[str] = []
    raster_source: dict[str, tuple[str, str] | None] = {}
    for node_id, ctype in types.items():
        if ctype != THRESHOLD:
            continue
        source = _origin(node_id, "input_path", types, inputs)
        raster_source[node_id] = source
        if source and types.get(source[0]) == THRESHOLD:
            errors.append(
                f"{node_id}: input_path comes from the mask written by {source[0]}. Threshold the "
                "original raster instead (SceneInput `path`, or WaterInputSet `before_path` / `after_path`)."
            )

    if any(ctype == PAIR_INPUT for ctype in types.values()):
        pair_outputs = [s[1] for s in raster_source.values() if s and types.get(s[0]) == PAIR_INPUT]
        if len(pair_outputs) >= DATES and len(set(pair_outputs)) == 1:
            errors.append(
                f"Every threshold step reads WaterInputSet `{pair_outputs[0]}`; the two dates need one step on "
                "`before_path` and one on `after_path`."
            )

    for node_id, ctype in types.items():
        if ctype != EVIDENCE:
            continue
        for field, expected in (("before_path", "before_path"), ("after_path", "after_path")):
            mask = _origin(node_id, field, types, inputs)
            if not mask or types.get(mask[0]) != THRESHOLD:
                continue
            source = raster_source.get(mask[0])
            if source and types.get(source[0]) == PAIR_INPUT and source[1] != expected:
                errors.append(
                    f"{node_id}: {field} is the mask of {mask[0]}, which thresholds WaterInputSet `{source[1]}`. "
                    f"Wire {field} to the mask made from `{expected}`."
                )
    return errors

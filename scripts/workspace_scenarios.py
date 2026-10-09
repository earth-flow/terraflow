"""Task scenarios known to the agent workspace.

A scenario declares what it needs, what it produces and how a runner summary
becomes the workspace's generic result: headline, metrics, map layers with
legends, the gate outcome, an optional self-check and the claim boundaries.
The web page renders only that generic result, so adding a scenario does not
require scenario-specific components.

Standard library only; shared by the web adapter and the flow builder.
"""

from __future__ import annotations

from typing import Any, Callable

WATER_LEGEND = [
    {"color": "#2570eb", "label": "水体"},
    {"color": "#484d57", "label": "非水体"},
    {"color": "transparent", "label": "无效像素"},
]
SAR_LEGEND = [{"color": "linear-gradient(90deg,#000,#fff)", "label": "后向散射 −25～0 dB"}]


def _steps(summary: dict[str, Any], chain: str = "success_chain") -> list[dict[str, Any]]:
    return [step for step in (summary.get(chain) or {}).get("steps") or [] if isinstance(step, dict)]


def _outputs(step: dict[str, Any] | None) -> dict[str, Any]:
    outputs = (step or {}).get("outputs")
    return outputs if isinstance(outputs, dict) else {}


def _find(summary: dict[str, Any], slug: str) -> dict[str, Any] | None:
    return next((step for step in _steps(summary) if step.get("tool_slug") == slug), None)


def _km2(value: Any) -> float | None:
    return round(float(value) / 1e6, 2) if isinstance(value, (int, float)) else None


def gate_outcome(summary: dict[str, Any]) -> dict[str, Any]:
    preflight = _outputs(_find(summary, "geo_water.raster_profile_preflight"))
    chain = summary.get("success_chain") or {}
    blocked = chain.get("status") == "blocked_by_gate"
    return {
        "checked": bool(preflight),
        "passed": not blocked and preflight.get("execution_blocked") is False,
        "blocked_tool": chain.get("blocked_tool"),
        "reason_code": chain.get("reason_code") if blocked else preflight.get("reason_code"),
        "failed_checks": chain.get("failed_checks") if blocked else preflight.get("failed_checks") or [],
        "label": "网格检查",
        "description": "前后两期影像的网格、坐标系、范围、分辨率、nodata 和有效区一致后才做成对分析",
    }


def self_check_outcome(summary: dict[str, Any]) -> dict[str, Any] | None:
    chain = summary.get("rejection_chain") or {}
    if chain.get("status") != "deterministic_refusal":
        return None
    return {
        "description": "用故意做成尺寸不一致的样本再做一次网格检查，确认门控会拦截",
        "reason_code": chain.get("reason_code"),
        "failed_checks": chain.get("failed_checks") or [],
    }


def _area_boundary(evidence: dict[str, Any], what: str) -> str:
    text = f"只统计{what}有效的像素；面积按 " + str(evidence.get("crs") or "投影坐标系") + " 的像元面积计算"
    if evidence.get("pixel_area_m2"):
        text += f"（每像元 {evidence.get('pixel_area_m2')} m²）"
    return text + "。"


def _gate_from_measure(summary: dict[str, Any], slug: str, label: str, description: str) -> dict[str, Any]:
    """Gate outcome when the measuring tool itself checks labels and the area grid."""
    step = _find(summary, slug)
    outputs = _outputs(step)
    chain = summary.get("success_chain") or {}
    blocked = chain.get("status") == "blocked_by_gate"
    return {
        "checked": step is not None or blocked,
        "passed": not blocked and step is not None and outputs.get("execution_blocked") is False,
        "blocked_tool": chain.get("blocked_tool"),
        "reason_code": chain.get("reason_code") if blocked else outputs.get("reason_code"),
        "failed_checks": chain.get("failed_checks") if blocked else outputs.get("failed_checks") or [],
        "label": label,
        "description": description,
    }


REASON_TEXT = {
    "unsupported_area_crs": "坐标系不是投影坐标，不能按像元算面积",
    "area_distorting_projection": "投影会随纬度放大面积（如 Web Mercator），不能按像元算面积",
    "unsupported_area_units": "坐标单位不是长度单位",
    "invalid_water_labels": "标签中有 0/1 以外的值",
    "no_valid_pixels": "没有有效像素",
    "unsupported_band_count": "不是单波段栅格",
}


def water_extent_result(summary: dict[str, Any], preview_url: Callable[[str], str]) -> dict[str, Any]:
    fixtures = summary.get("fixtures") or {}
    sar = fixtures.get("input_kind") == "sar_backscatter"
    gate = _gate_from_measure(
        summary,
        "geo_water.water_extent",
        "面积检查",
        "单景水体面积只在投影坐标、长度单位、不随纬度放大面积的网格上计算，标签必须是 0/1",
    )
    extent = _outputs(_find(summary, "geo_water.water_extent"))
    derived = (summary.get("success_chain") or {}).get("derived_labels") or {}
    metrics: list[dict[str, Any]] = []
    if extent.get("extent_answerable"):
        metrics += [
            {"key": "valid_area_m2", "label": "有效面积", "value": _km2(extent.get("valid_area_m2")), "unit": "km²"},
            {"key": "water_area_m2", "label": "水体面积", "value": _km2(extent.get("water_area_m2")), "unit": "km²"},
            {
                "key": "water_fraction",
                "label": "水体占比",
                "value": round(100 * float(extent.get("water_fraction") or 0), 1),
                "unit": "%",
                "note": "水体像元 / 有效像元",
            },
        ]
    if isinstance(derived.get("threshold"), (int, float)):
        metrics.append(
            {
                "key": "threshold",
                "label": "水体阈值",
                "value": round(float(derived["threshold"]), 2),
                "unit": derived.get("units") or "",
                "note": "本景 Otsu",
            }
        )
    layers = [
        {
            "key": "scene",
            "label": "影像" if sar else "水体标签",
            "url": preview_url("scene"),
            "legend": SAR_LEGEND if sar else WATER_LEGEND,
        }
    ]
    if sar and derived:
        layers.append(
            {"key": "scene_mask", "label": "水体（阈值估计）", "url": preview_url("scene_mask"), "legend": WATER_LEGEND}
        )
    by_key = {item["key"]: item["value"] for item in metrics}
    if not gate["passed"]:
        reason = gate["reason_code"] or "未知原因"
        headline = f"没有计算水体面积：{REASON_TEXT.get(reason, reason)}（{reason}）。"
    elif by_key.get("water_area_m2") is not None:
        headline = (
            f"水体面积 {by_key['water_area_m2']} km²，占有效面积 {by_key['valid_area_m2']} km² 的 "
            f"{by_key['water_fraction']}%。"
        )
    else:
        headline = "分析完成，但没有得到水体统计。"
    boundaries = [_area_boundary(extent, "本景")]
    if sar:
        boundaries.append("SAR 水体由后向散射阈值估计，不是参考标注；平滑地表、阴影也可能低于阈值。")
    boundaries.append("单景只反映成像时刻的水面，不能说明变化或淹没。")
    if fixtures.get("type") == "synthetic_engineering_fixture":
        boundaries.append("这是合成测试栅格，结果只用于检查工具链。")
    return {
        "scenario": "water_extent",
        "status": "success" if gate["passed"] else "blocked",
        "headline": headline,
        "metrics": metrics,
        "layers": layers,
        "gate": gate,
        "self_check": None,
        "boundaries": boundaries,
    }


def water_change_result(summary: dict[str, Any], preview_url: Callable[[str], str]) -> dict[str, Any]:
    fixtures = summary.get("fixtures") or {}
    sar = fixtures.get("input_kind") == "sar_backscatter"
    gate = gate_outcome(summary)
    evidence = _outputs(_find(summary, "geo_water.water_evidence"))
    derived = (summary.get("success_chain") or {}).get("derived_labels") or {}
    metrics: list[dict[str, Any]] = []
    if evidence.get("change_answerable"):
        for key, label in (
            ("valid_area_m2", "有效面积"),
            ("before_water_area_m2", "前期水体"),
            ("after_water_area_m2", "后期水体"),
            ("new_water_area_m2", "新增水体"),
            ("receded_water_area_m2", "消退水体"),
        ):
            metrics.append({"key": key, "label": label, "value": _km2(evidence.get(key)), "unit": "km²"})
    if isinstance(derived.get("threshold"), (int, float)):
        metrics.append(
            {
                "key": "threshold",
                "label": "水体阈值",
                "value": round(float(derived["threshold"]), 2),
                "unit": derived.get("units") or "",
                "note": "后期影像 Otsu，前期沿用同一阈值",
            }
        )
    if sar:
        layers = [
            {"key": "before", "label": "前期影像", "url": preview_url("before"), "legend": SAR_LEGEND},
            {"key": "after", "label": "后期影像", "url": preview_url("after"), "legend": SAR_LEGEND},
            {
                "key": "before_mask",
                "label": "前期水体（阈值估计）",
                "url": preview_url("before_mask"),
                "legend": WATER_LEGEND,
            },
            {
                "key": "after_mask",
                "label": "后期水体（阈值估计）",
                "url": preview_url("after_mask"),
                "legend": WATER_LEGEND,
            },
        ]
    else:
        layers = [
            {"key": "before", "label": "前期水体标签", "url": preview_url("before"), "legend": WATER_LEGEND},
            {"key": "after", "label": "后期水体标签", "url": preview_url("after"), "legend": WATER_LEGEND},
        ]
    if not gate["passed"]:
        layers = layers[:2]  # masks are not produced when the gate blocks the pair
    by_key = {item["key"]: item["value"] for item in metrics}
    if not gate["passed"] and gate.get("blocked_tool") == "geo_water.water_evidence":
        reason = gate["reason_code"] or "未知原因"
        headline = f"没有计算水体变化：{REASON_TEXT.get(reason, reason)}（{reason}）。"
    elif not gate["passed"]:
        headline = f"两期影像没有通过网格检查（{gate['reason_code'] or '未知原因'}），没有计算水体变化。"
    elif by_key.get("after_water_area_m2") is not None:
        headline = (
            f"后期水体 {by_key['after_water_area_m2']} km²，相对前期新增 {by_key['new_water_area_m2']} km²、"
            f"消退 {by_key['receded_water_area_m2']} km²。"
        )
    else:
        headline = "分析完成，但没有得到可比较的水体统计。"
    boundaries = [_area_boundary(evidence, "两期都")]
    if sar:
        boundaries.append("SAR 水体由后向散射阈值估计，不是参考标注；平滑地表、阴影也可能低于阈值。")
    boundaries.append("前后水体差异本身不能证明洪水成因。")
    if fixtures.get("type") == "synthetic_engineering_fixture":
        boundaries.append("这是合成测试栅格，结果只用于检查工具链。")
    return {
        "scenario": "water_change",
        "status": "success" if gate["passed"] else "blocked",
        "headline": headline,
        "metrics": metrics,
        "layers": layers,
        "gate": gate,
        "self_check": self_check_outcome(summary),
        "boundaries": boundaries,
    }


TASK_SCENARIOS: dict[str, dict[str, Any]] = {
    "water_change": {
        "id": "water_change",
        "title": "水体变化",
        "summary": "比较同一地区前后两期影像的水体范围，给出前期、后期、新增和消退水体面积。",
        "inputs": [
            {"role": "before", "label": "前期影像"},
            {"role": "after", "label": "后期影像"},
        ],
        "input_kinds": ["sar_backscatter", "water_labels"],
        "requirements": [
            "两期影像在同一网格上（坐标系、范围、分辨率、nodata 一致）",
            "SAR 后向散射（线性或 dB），或 0/1 水体标签",
        ],
        "outputs": ["两期水体图层", "前期、后期、新增、消退水体面积", "网格检查结论与每步证据"],
        "tools": [
            "raster_viewer.inspect_geotiff",
            "geo_water.raster_profile_preflight",
            "geo_raster.threshold_segmentation",
            "geo_raster.raster_diff",
            "geo_raster.raster_stats",
            "geo_water.water_evidence",
            "geo_basic.pixel_area",
        ],
        "limitations": [
            "SAR 水体为阈值估计，没有参考数据时不代表精度",
            "差异不能单独证明洪水成因",
        ],
        "examples": [
            "看看希腊色萨利 2023 年 9 月那次洪水，新增了多少水面？",
            "比较这两期影像的水体变化，并给出证据。",
        ],
        "build_result": water_change_result,
    },
    "water_extent": {
        "id": "water_extent",
        "title": "单景水体提取",
        "summary": "提取一景影像中的水体范围，给出有效面积、水体面积和水体占比。",
        "inputs": [{"role": "scene", "label": "影像"}],
        "input_kinds": ["sar_backscatter", "water_labels"],
        "requirements": [
            "投影坐标系、长度单位，且不是 Web Mercator 这类随纬度放大面积的投影",
            "SAR 后向散射（线性或 dB），或 0/1 水体标签",
        ],
        "outputs": ["水体图层", "有效面积、水体面积、水体占比", "面积检查结论与每步证据"],
        "tools": [
            "raster_viewer.inspect_geotiff",
            "geo_raster.raster_stats",
            "geo_raster.threshold_segmentation",
            "geo_water.water_extent",
        ],
        "limitations": [
            "SAR 水体为阈值估计，没有参考数据时不代表精度",
            "单景不能说明变化或淹没",
        ],
        "examples": [
            "色萨利 9 月 13 日那景影像里有多少水面？",
            "提取这景影像的水体范围。",
        ],
        "build_result": water_extent_result,
    },
}


def scenario_catalog() -> list[dict[str, Any]]:
    """Public description of every scenario (without the result builder)."""
    return [{key: value for key, value in spec.items() if key != "build_result"} for spec in TASK_SCENARIOS.values()]


def build_result(summary: dict[str, Any], preview_url: Callable[[str], str]) -> dict[str, Any]:
    scenario = TASK_SCENARIOS.get(str(summary.get("scenario") or "water_change")) or TASK_SCENARIOS["water_change"]
    return scenario["build_result"](summary, preview_url)

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


# Open water in Sentinel-1 VV gamma0/sigma0 usually lies well below about -15 dB; an Otsu split far
# outside this band more likely separates other surfaces (e.g. bright urban areas vs everything else).
SAR_WATER_THRESHOLD_RANGE_DB = (-26.0, -12.0)


def threshold_caution(derived: dict[str, Any]) -> str | None:
    value = derived.get("threshold")
    if derived.get("units") != "dB" or not isinstance(value, (int, float)):
        return None
    low, high = SAR_WATER_THRESHOLD_RANGE_DB
    if low <= value <= high:
        return None
    return (f"注意：Otsu 阈值 {value:.2f} dB 不在开阔水体常见的 {low:.0f}～{high:.0f} dB 范围内，"
            "直方图可能分开的是城区等高亮地物与其余地表，而不是水与陆地；这次的水体面积可信度低。")


def _with_caution(result: dict[str, Any], derived: dict[str, Any]) -> dict[str, Any]:
    caution = threshold_caution(derived)
    if caution and result.get("status") == "success":
        result["headline"] = result["headline"].rstrip("。") + "（阈值偏离常见范围，见适用边界）。"
        result["boundaries"] = [caution] + result["boundaries"]
    return result


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
    boundaries += _alignment_boundaries(fixtures)
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


CLASS_LEGEND_NOTE = "颜色按类别表"
COMPARE_LEGEND = [
    {"color": "#2570eb", "label": "两者都是水体"},
    {"color": "#f08c2e", "label": "仅本次识别为水体"},
    {"color": "#d6336c", "label": "仅参考范围为水体"},
    {"color": "#d9dde3", "label": "两者都不是水体"},
]
OPTICAL_LEGEND = [{"color": "linear-gradient(90deg,#000,#fff)", "label": "NDWI 低 → 高（2–98% 拉伸）"}]


def _class_legend(classes: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"color": "#" + c["color_hint"], "label": str(c.get("title") or c.get("name"))}
        for c in classes if c.get("color_hint") and not c.get("nodata")
    ]


def _item_preview(fixtures: dict[str, Any], role: str) -> str | None:
    item_id = (fixtures.get("scene_ids") or {}).get(role)
    return f"/api/workspace/items/{item_id}/preview" if item_id else None


def _blocked_headline(gate: dict[str, Any], what: str) -> str:
    reason = gate.get("reason_code") or "未知原因"
    return f"没有{what}：{REASON_TEXT.get(reason, reason)}（{reason}）。"


def _alignment_boundaries(fixtures: dict[str, Any]) -> list[str]:
    out = []
    for record in fixtures.get("alignment") or []:
        if record.get("operation") == "warp":
            out.append(
                f"{record.get('label', '辅助数据')}从 {record.get('source_crs')} 用{'最近邻' if record.get('resampling') == 'nearest' else '双线性'}"
                f"重采样到分析网格 {record.get('target_crs')}（{record.get('target_resolution', [None])[0]} m）。"
            )
        elif record.get("operation") == "rasterize":
            out.append(f"{record.get('label', '矢量')}按像元中心落在面内栅格化到分析网格（{record.get('features')} 个面）。")
        elif record.get("operation") == "common_valid" and record.get("applied"):
            excluded = record.get("excluded_pixels") or {}
            out.append(f"{record.get('label', '共同有效区')}：任一方无效的像元在两者中都按无效处理"
                       f"（分别去掉 {excluded.get('first', 0)}、{excluded.get('second', 0)} 个像元）。")
        elif record.get("operation") == "band" and record.get("scale") is not None:
            out.append(
                f"{record.get('label', '波段')}按登记的换算系数转为反射率（× {record.get('scale')} {record.get('offset'):+}）。"
            )
    return out


def optical_water_result(summary: dict[str, Any], preview_url: Callable[[str], str]) -> dict[str, Any]:
    fixtures = summary.get("fixtures") or {}
    chain = summary.get("success_chain") or {}
    gate = _gate_from_measure(
        summary, "geo_water.water_extent", "面积检查",
        "绿、近红外两波段先做网格检查；水体面积只在投影坐标、长度单位、不随纬度放大面积的网格上计算",
    )
    extent = _outputs(_find(summary, "geo_water.water_extent"))
    metrics: list[dict[str, Any]] = []
    if extent.get("extent_answerable"):
        metrics += [
            {"key": "valid_area_m2", "label": "有效面积", "value": _km2(extent.get("valid_area_m2")), "unit": "km²",
             "note": "扣除云、云影和无数据后"},
            {"key": "water_area_m2", "label": "水体面积", "value": _km2(extent.get("water_area_m2")), "unit": "km²"},
            {"key": "water_fraction", "label": "水体占比", "value": round(100 * float(extent.get("water_fraction") or 0), 1),
             "unit": "%", "note": "水体像元 / 有效像元"},
        ]
        if chain.get("cloud_masked_pixels") is not None and extent.get("pixel_area_m2"):
            metrics.append({"key": "cloud_area_m2", "label": "云和云影扣除",
                            "value": _km2(chain["cloud_masked_pixels"] * extent["pixel_area_m2"]), "unit": "km²",
                            "note": "SCL 类别 3、8、9、10"})
    metrics.append({"key": "threshold", "label": "NDWI 阈值", "value": 0.0, "unit": "",
                    "note": "NDWI > 0 为水体（McFeeters 1996）"})
    layers = []
    if _item_preview(fixtures, "scene"):
        layers.append({"key": "optical", "label": "光学影像（真彩色）", "url": _item_preview(fixtures, "scene"), "legend": []})
    layers += [
        {"key": "ndwi", "label": "NDWI", "url": preview_url("ndwi"), "legend": OPTICAL_LEGEND},
        {"key": "scene_mask", "label": "水体（NDWI 阈值）", "url": preview_url("scene_mask"), "legend": WATER_LEGEND},
    ]
    by_key = {item["key"]: item["value"] for item in metrics}
    if not gate["passed"]:
        headline = _blocked_headline(gate, "计算光学水体面积")
        layers = layers[:1]
    elif by_key.get("water_area_m2") is not None:
        headline = (f"光学水体 {by_key['water_area_m2']} km²，占有效面积 {by_key['valid_area_m2']} km² 的 "
                    f"{by_key['water_fraction']}%（成像 {fixtures.get('scene_date') or '日期未记录'}）。")
    else:
        headline = "分析完成，但没有得到水体统计。"
    boundaries = [_area_boundary(extent, "云外")]
    boundaries += _alignment_boundaries(fixtures)
    boundaries.append("NDWI 固定阈值是常用经验规则，不是参考标注；浑浊水体、建筑阴影、湿润裸土可能误判。")
    boundaries.append("光学只反映成像时刻且受云影响；与 SAR 结果日期不同时不能直接相减。")
    return {"scenario": "optical_water", "status": "success" if gate["passed"] else "blocked", "headline": headline,
            "metrics": metrics, "layers": layers, "gate": gate, "self_check": None, "boundaries": boundaries}


def landcover_impact_result(summary: dict[str, Any], preview_url: Callable[[str], str]) -> dict[str, Any]:
    fixtures = summary.get("fixtures") or {}
    base = water_change_result(summary, preview_url)
    chain = summary.get("success_chain") or {}
    by_class = _outputs(_find(summary, "geo_water.water_by_class"))
    gate = base["gate"]
    if chain.get("blocked_tool") == "geo_water.water_by_class":
        gate = {**gate, "passed": False, "label": "分类检查",
                "description": "土地覆盖栅格与水体掩膜网格一致、类别值都在类别表中后才统计"}
    metrics = [m for m in base["metrics"] if m["key"] in ("valid_area_m2", "new_water_area_m2", "threshold")]
    rows = sorted(by_class.get("classes") or [], key=lambda r: -(r.get("new_water_area_m2") or 0))
    for row in rows[:5]:
        if not row.get("new_water_area_m2"):
            continue
        metrics.append({
            "key": f"new_water_{row['name']}", "label": f"新增水体 · {row.get('title') or row['name']}",
            "value": _km2(row["new_water_area_m2"]), "unit": "km²",
            "note": f"占该类面积 {round(100 * row['new_water_fraction_of_class'], 1)}%，占新增水体 {round(100 * (row.get('share_of_new_water') or 0), 1)}%",
        })
    classes = fixtures.get("classes") or []
    layers = [layer for layer in base["layers"] if layer["key"] in ("after", "after_mask")]
    layers += [
        {"key": "landcover", "label": f"土地覆盖（{fixtures.get('landcover_title') or '分类栅格'}）",
         "url": preview_url("landcover"), "legend": _class_legend(classes)},
        {"key": "new_water_by_class", "label": "新增水体所在类别", "url": preview_url("new_water_by_class"),
         "legend": _class_legend(classes)},
    ]
    if not gate["passed"]:
        headline = base["headline"] if chain.get("blocked_tool") != "geo_water.water_by_class" else _blocked_headline(gate, "按类别统计")
        layers = layers[:1]
    elif rows and rows[0].get("new_water_area_m2"):
        top = rows[0]
        headline = (f"新增水体 {_km2(by_class.get('new_water_area_m2'))} km²，其中{top.get('title') or top['name']} "
                    f"{_km2(top['new_water_area_m2'])} km²（该类 {round(100 * top['new_water_fraction_of_class'], 1)}% 被淹）。")
    else:
        headline = "分析完成，但新增水体为 0 或没有得到分类统计。"
    boundaries = base["boundaries"][:]
    boundaries += [line for line in _alignment_boundaries(fixtures) if line not in boundaries]
    if by_class.get("valid_area_m2") and base["metrics"]:
        boundaries.append(f"按类别统计的有效面积 {_km2(by_class['valid_area_m2'])} km²：土地覆盖为无数据的像元不计入，所以合计可能略小于新增水体总面积。")
    boundaries.append("土地覆盖是分类产品（模型估计），年份与洪水不同时类别可能已变化。")
    return {**base, "scenario": "landcover_impact", "headline": headline, "metrics": metrics, "layers": layers,
            "gate": gate, "self_check": None, "boundaries": boundaries}


def reference_compare_result(summary: dict[str, Any], preview_url: Callable[[str], str]) -> dict[str, Any]:
    fixtures = summary.get("fixtures") or {}
    sar = fixtures.get("input_kind") == "sar_backscatter"
    gate = _gate_from_measure(
        summary, "geo_water.compare_with_reference", "面积检查",
        "识别结果与参考范围在同一网格、都是 0/1，且网格可以按像元算面积后才比较",
    )
    out = _outputs(_find(summary, "geo_water.compare_with_reference"))
    derived = (summary.get("success_chain") or {}).get("derived_labels") or {}
    metrics: list[dict[str, Any]] = []
    if out.get("comparison_answerable"):
        for key, label in (("iou", "IoU（交并比）"), ("f1", "F1"), ("precision", "精确率"), ("recall", "召回率")):
            if out.get(key) is not None:
                metrics.append({"key": key, "label": label, "value": round(100 * float(out[key]), 1), "unit": "%"})
        for key, label in (("predicted_water_area_m2", "本次识别水体"), ("reference_water_area_m2", "参考范围水体"),
                           ("agreement_area_m2", "两者一致"), ("predicted_only_area_m2", "仅本次识别"),
                           ("reference_only_area_m2", "仅参考范围")):
            metrics.append({"key": key, "label": label, "value": _km2(out.get(key)), "unit": "km²"})
    if isinstance(derived.get("threshold"), (int, float)):
        metrics.append({"key": "threshold", "label": "水体阈值", "value": round(float(derived["threshold"]), 2),
                        "unit": derived.get("units") or "", "note": "本景 Otsu"})
    layers = [
        {"key": "scene", "label": "影像", "url": _item_preview(fixtures, "scene") or preview_url("scene"),
         "legend": SAR_LEGEND if sar else WATER_LEGEND},
        {"key": "scene_mask", "label": "本次识别水体", "url": preview_url("scene_mask"), "legend": WATER_LEGEND},
        {"key": "reference", "label": f"参考范围（{fixtures.get('reference_title') or '参考'}）", "url": preview_url("reference"),
         "legend": WATER_LEGEND},
        {"key": "comparison", "label": "对比", "url": preview_url("comparison"), "legend": COMPARE_LEGEND},
    ]
    if not sar:
        layers = [layer for layer in layers if layer["key"] != "scene_mask"]
    by_key = {item["key"]: item["value"] for item in metrics}
    scene_date, reference_date = fixtures.get("scene_date"), fixtures.get("reference_date")
    if not gate["passed"]:
        headline = _blocked_headline(gate, "比较")
        layers = [layer for layer in layers if layer["key"] in ("scene", "reference")]
    elif by_key.get("iou") is not None:
        headline = (f"与参考范围相比 IoU {by_key['iou']}%：本次识别水体 {by_key['predicted_water_area_m2']} km²，"
                    f"参考范围 {by_key['reference_water_area_m2']} km²（影像 {scene_date}，参考 {reference_date}）。")
    else:
        headline = "比较完成，但没有得到一致性指标。"
    boundaries = [_area_boundary(out, "两者都")]
    boundaries += _alignment_boundaries(fixtures)
    if fixtures.get("date_gap_days") is not None:
        boundaries.append(f"影像与参考范围相差 {fixtures['date_gap_days']} 天，期间水面会变化，差异不全是识别误差。")
    boundaries.append("参考范围是制图产品（如 Copernicus EMS），不是实测真值；一致性不等于精度。")
    boundaries.append("洪水参考范围通常不含永久水体（海、湖、河道），识别结果包含它们，会计入“仅本次识别”。")
    if sar:
        boundaries.append("SAR 水体由后向散射阈值估计；城区、植被下的水体通常漏检。")
    return {"scenario": "reference_compare", "status": "success" if gate["passed"] else "blocked", "headline": headline,
            "metrics": metrics, "layers": layers, "gate": gate, "self_check": None, "boundaries": boundaries}


TASK_SCENARIOS: dict[str, dict[str, Any]] = {
    "water_change": {
        "id": "water_change",
        "title": "水体变化",
        "summary": "比较同一地区前后两期影像的水体范围，给出前期、后期、新增和消退水体面积。",
        "inputs": [
            {"role": "before", "label": "前期影像"},
            {"role": "after", "label": "后期影像"},
        ],
        "slots": [
            {"role": "before", "label": "前期影像", "accepts": ["sar_vv", "water_mask"], "pair": "dates"},
            {"role": "after", "label": "后期影像", "accepts": ["sar_vv", "water_mask"], "pair": "dates"},
        ],
        "canvas_template": True,
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
            "2023 年 9 月希腊色萨利发生洪水，洪水后比洪水前多了多少水面？",
            "对比这两期影像，水面有什么变化？请给出依据。",
        ],
        "build_result": water_change_result,
    },
    "water_extent": {
        "id": "water_extent",
        "title": "单景水体提取",
        "summary": "提取一景影像中的水体范围，给出有效面积、水体面积和水体占比。",
        "inputs": [{"role": "scene", "label": "影像"}],
        "slots": [{"role": "scene", "label": "影像", "accepts": ["sar_vv", "water_mask"]}],
        "canvas_template": True,
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
            "色萨利 2023 年 9 月 13 日的雷达影像上，水面有多大？",
            "提取这景影像中的水体范围。",
        ],
        "build_result": water_extent_result,
    },
}


TASK_SCENARIOS["optical_water"] = {
    "id": "optical_water",
    "title": "光学水体提取",
    "summary": "用 Sentinel-2 等多光谱影像计算 NDWI，扣除云和云影后提取水体，给出水体面积和占比。",
    "inputs": [{"role": "scene", "label": "光学影像"}],
    "slots": [{"role": "scene", "label": "光学影像", "accepts": ["optical_green_nir"]}],
    "requirements": ["含 green、nir 波段的地表反射率（登记了换算系数）", "最好附 Sentinel-2 SCL 云掩膜", "投影坐标系"],
    "outputs": ["NDWI 与水体图层", "有效面积、水体面积、占比、云扣除面积", "面积检查结论与每步证据"],
    "tools": ["raster_viewer.inspect_geotiff", "geo_water.raster_profile_preflight", "geo_raster.calculate_index",
              "geo_raster.apply_cloud_mask", "geo_raster.threshold_segmentation", "geo_water.water_extent"],
    "limitations": ["NDWI 固定阈值是经验规则，浑浊水和阴影可能误判", "受云影响；只反映成像时刻"],
    "examples": ["用 9 月 10 日的 Sentinel-2 光学影像，估算色萨利的水面面积。", "用光学影像提取色萨利的水体。"],
    "build_result": optical_water_result,
}
TASK_SCENARIOS["landcover_impact"] = {
    "id": "landcover_impact",
    "title": "淹没区土地覆盖统计",
    "summary": "比较前后两期 SAR 得到新增水体，再叠加土地覆盖，统计新增水体里耕地、建设用地等各类的面积。",
    "inputs": [{"role": "before", "label": "前期影像"}, {"role": "after", "label": "后期影像"},
               {"role": "landcover", "label": "土地覆盖"}],
    "slots": [
        {"role": "before", "label": "前期影像", "accepts": ["sar_vv", "water_mask"], "pair": "dates"},
        {"role": "after", "label": "后期影像", "accepts": ["sar_vv", "water_mask"], "pair": "dates"},
        {"role": "landcover", "label": "土地覆盖", "accepts": ["landcover"]},
    ],
    "requirements": ["两期影像在同一网格", "土地覆盖为整数类别栅格并有类别表（如 ESA WorldCover），系统用最近邻重采样到影像网格"],
    "outputs": ["新增水体与土地覆盖图层", "新增水体总面积及各类别面积、占比", "网格检查、分类检查与每步证据"],
    "tools": ["raster_viewer.inspect_geotiff", "geo_water.raster_profile_preflight", "geo_raster.threshold_segmentation",
              "geo_water.water_evidence", "geo_water.water_by_class"],
    "limitations": ["SAR 水体为阈值估计", "土地覆盖是分类产品，年份可能与洪水不同"],
    "examples": ["这次色萨利洪水淹没了多少耕地？", "新增的水面分别落在哪些土地覆盖类型上？"],
    "build_result": landcover_impact_result,
}
TASK_SCENARIOS["reference_compare"] = {
    "id": "reference_compare",
    "title": "与参考范围对比",
    "summary": "把一景影像识别出的水体与参考水体范围（如 Copernicus EMS 洪水范围）对比，给出 IoU、精确率、召回率和差异图。",
    "inputs": [{"role": "scene", "label": "影像"}, {"role": "reference", "label": "参考范围"}],
    "slots": [
        {"role": "scene", "label": "影像", "accepts": ["sar_vv", "water_mask"]},
        {"role": "reference", "label": "参考范围", "accepts": ["reference_extent"]},
    ],
    "requirements": ["影像为投影坐标系", "参考范围为登记为“参考范围”的矢量，系统按像元中心栅格化到影像网格"],
    "outputs": ["识别水体、参考范围和对比图层", "IoU、F1、精确率、召回率与各部分面积", "面积检查结论与每步证据"],
    "tools": ["raster_viewer.inspect_geotiff", "geo_raster.threshold_segmentation", "geo_water.water_extent",
              "geo_water.compare_with_reference"],
    "limitations": ["参考范围是制图产品，不是真值", "影像与参考日期不同时差异不全是误差"],
    "examples": ["我们识别出的色萨利水体，和 Copernicus EMS 发布的洪水范围相差多少？", "把这景影像的水体和参考范围对比一下。"],
    "build_result": reference_compare_result,
}


def scenario_catalog() -> list[dict[str, Any]]:
    """Public description of every scenario (without the result builder)."""
    return [{key: value for key, value in spec.items() if key != "build_result"} for spec in TASK_SCENARIOS.values()]


def build_result(summary: dict[str, Any], preview_url: Callable[[str], str]) -> dict[str, Any]:
    scenario = TASK_SCENARIOS.get(str(summary.get("scenario") or "water_change")) or TASK_SCENARIOS["water_change"]
    result = scenario["build_result"](summary, preview_url)
    result["canvas"] = bool(scenario.get("canvas_template"))
    return _with_caution(result, (summary.get("success_chain") or {}).get("derived_labels") or {})

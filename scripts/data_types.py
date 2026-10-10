"""Data type specifications for the workspace data catalog.

Every record in the catalog is a STAC 1.1 Item. What differs between data
types (SAR backscatter, multispectral optical, elevation, vector,
classification raster) lives here, one specification per type:

- the file formats a type accepts and the form fields an uploader fills in;
- ``validate``: rules checked against the file profile (from
  ``inspect_geodata.py``) and the filled-in fields, giving ok / warning /
  rejected with reasons;
- ``build_item``: the STAC Item written for an accepted upload.

Fields that STAC has no place for use the ``workspace:`` prefix (data type,
observed or estimated, verification, audience, place, event, claim boundary,
SAR backscatter convention, DEM surface and vertical datum, vector purpose).
Adding a type means adding a specification here; the catalog, its storage,
search and the upload steps stay as they are.

Standard library only. Messages are shown to users and are in Chinese.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

STAC_VERSION = "1.1.0"
EXTENSIONS = {
    "sar": "https://stac-extensions.github.io/sar/v1.3.2/schema.json",
    "sat": "https://stac-extensions.github.io/sat/v1.2.0/schema.json",
    "eo": "https://stac-extensions.github.io/eo/v2.0.0/schema.json",
    "projection": "https://stac-extensions.github.io/projection/v2.1.0/schema.json",
    "raster": "https://stac-extensions.github.io/raster/v2.0.0/schema.json",
    "processing": "https://stac-extensions.github.io/processing/v1.2.0/schema.json",
    "file": "https://stac-extensions.github.io/file/v2.1.0/schema.json",
    "classification": "https://stac-extensions.github.io/classification/v2.0.0/schema.json",
    "label": "https://stac-extensions.github.io/label/v1.0.1/schema.json",
    "vector": "https://stac-extensions.github.io/vector/v0.1.0/schema.json",
    "table": "https://stac-extensions.github.io/table/v1.3.0/schema.json",
}
MEDIA_TYPES = {
    "geotiff": "image/tiff; application=geotiff",
    "geojson": "application/geo+json",
    "gpkg": "application/geopackage+sqlite3",
    "zip": "application/zip",
}
RASTER_SUFFIXES = {".tif": "geotiff", ".tiff": "geotiff"}
VECTOR_SUFFIXES = {".geojson": "geojson", ".json": "geojson", ".gpkg": "gpkg", ".zip": "zip"}
STAC_DATA_TYPES = {
    "int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64",
    "float16", "float32", "float64", "cint16", "cint32", "cfloat32", "cfloat64",
}
MERCATOR_CODES = {"EPSG:3857", "EPSG:900913", "EPSG:3785", "EPSG:3395", "EPSG:54004"}

ORIGINS = {
    "observed": "传感器观测（含裁剪、重投影、定标等确定性处理）",
    "model_estimate": "模型或阈值估计",
    "manual": "人工标注或绘制",
    "synthetic": "合成测试数据",
}
VERIFICATION = {"source_linked": "可链接到上游来源", "declared": "上传者填写，未核实"}
POLARIZATIONS = ["HH", "VV", "HV", "VH"]
FREQUENCY_BANDS = ["P", "L", "S", "C", "X", "Ku", "K", "Ka"]
BACKSCATTER = {
    "gamma0_terrain": "γ⁰（地形校正，RTC）",
    "sigma0_ellipsoid": "σ⁰（椭球面，GRD 定标）",
    "beta0": "β⁰",
    "unknown": "不清楚",
}
COMMON_NAMES = [
    "coastal", "blue", "green", "red", "rededge", "rededge071", "rededge075", "rededge078",
    "nir", "nir08", "nir09", "cirrus", "swir16", "swir22", "pan", "lwir", "lwir11", "lwir12",
]
SURFACES = {"dtm": "地面高程（DTM）", "dsm": "表面高程（DSM，含建筑和植被）", "unknown": "不清楚"}
VERTICAL_DATUMS = {"EGM2008": "EGM2008", "EGM96": "EGM96", "WGS84 ellipsoid": "WGS 84 椭球高", "other": "其他", "unknown": "不清楚"}
VECTOR_PURPOSES = {
    "aoi": "研究区",
    "boundary": "行政区或边界",
    "reference": "参考范围（标注，如洪水范围）",
    "features": "地物（建筑、道路等）",
    "other": "其他",
}

# Class tables an uploader can start from instead of typing them in.
CLASS_PRESETS: dict[str, dict[str, Any]] = {
    "water_binary": {
        "title": "水体 0/1",
        "classes": [
            {"value": 0, "name": "non_water", "title": "非水体", "color_hint": "484d57"},
            {"value": 1, "name": "water", "title": "水体", "color_hint": "2570eb"},
        ],
    },
    "esa_worldcover": {
        "title": "ESA WorldCover（v100/v200）",
        "classes": [
            {"value": 0, "name": "no_data", "title": "无数据", "nodata": True},
            {"value": 10, "name": "tree_cover", "title": "林地", "color_hint": "006400"},
            {"value": 20, "name": "shrubland", "title": "灌木", "color_hint": "ffbb22"},
            {"value": 30, "name": "grassland", "title": "草地", "color_hint": "ffff4c"},
            {"value": 40, "name": "cropland", "title": "耕地", "color_hint": "f096ff"},
            {"value": 50, "name": "built_up", "title": "建设用地", "color_hint": "fa0000"},
            {"value": 60, "name": "bare_sparse_vegetation", "title": "裸地或稀疏植被", "color_hint": "b4b4b4"},
            {"value": 70, "name": "snow_and_ice", "title": "冰雪", "color_hint": "f0f0f0"},
            {"value": 80, "name": "permanent_water_bodies", "title": "永久水体", "color_hint": "0064c8"},
            {"value": 90, "name": "herbaceous_wetland", "title": "草本湿地", "color_hint": "0096a0"},
            {"value": 95, "name": "mangroves", "title": "红树林", "color_hint": "00cf75"},
            {"value": 100, "name": "moss_and_lichen", "title": "苔藓地衣", "color_hint": "fae6a0"},
        ],
    },
    "s2_scl": {
        "title": "Sentinel-2 场景分类（SCL）",
        "classes": [
            {"value": 0, "name": "no_data", "title": "无数据", "nodata": True},
            {"value": 1, "name": "saturated_or_defective", "title": "饱和或坏像元", "color_hint": "ff0000"},
            {"value": 2, "name": "dark_area_pixels", "title": "暗区", "color_hint": "2f2f2f"},
            {"value": 3, "name": "cloud_shadows", "title": "云影", "color_hint": "643200"},
            {"value": 4, "name": "vegetation", "title": "植被", "color_hint": "00a000"},
            {"value": 5, "name": "not_vegetated", "title": "非植被", "color_hint": "ffe65a"},
            {"value": 6, "name": "water", "title": "水体", "color_hint": "0000ff"},
            {"value": 7, "name": "unclassified", "title": "未分类", "color_hint": "808080"},
            {"value": 8, "name": "cloud_medium_probability", "title": "云（中概率）", "color_hint": "c0c0c0"},
            {"value": 9, "name": "cloud_high_probability", "title": "云（高概率）", "color_hint": "ffffff"},
            {"value": 10, "name": "thin_cirrus", "title": "薄卷云", "color_hint": "64c8ff"},
            {"value": 11, "name": "snow", "title": "雪", "color_hint": "ff96ff"},
        ],
    },
}
# SCL values that make an optical pixel unusable as a clear observation.
S2_SCL_UNCLEAR = {0, 1, 3, 8, 9, 10, 11}

_CLASS_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LINEAR_NEGATIVE_LIMIT = 0.01
REFLECTANCE_RANGE = (-0.2, 1.5)
ELEVATION_RANGE = (-500.0, 9000.0)
MAX_FEATURES_WARNING = 100_000
PRESET_MATCH_SHARE = 0.75
_AUTO_CLASS_NAME = re.compile(r"^class_-?\d+$")

# ---------------------------------------------------------------- form fields
# Each field: key, label, kind (text|textarea|date|number|select|multiselect), required, options, help.


def _options(mapping: dict[str, str] | list[str]) -> list[dict[str, str]]:
    if isinstance(mapping, dict):
        return [{"value": k, "label": v} for k, v in mapping.items()]
    return [{"value": v, "label": v} for v in mapping]


COMMON_FIELDS = [
    {"key": "title", "label": "名称", "kind": "text", "required": False, "help": "列表中显示的名称，不填时用日期和类型"},
    {"key": "platform", "label": "平台", "kind": "text", "required": False, "help": "如 sentinel-1a、sentinel-2b、landsat-9"},
    {"key": "source_url", "label": "来源链接", "kind": "text", "required": False, "help": "上游 STAC 条目或产品页面；填写后仍标为“上传者填写，未核实”"},
    {"key": "license", "label": "许可", "kind": "text", "required": False, "help": "SPDX 标识（如 CC-BY-4.0）或 other"},
    {"key": "lineage", "label": "处理说明", "kind": "textarea", "required": False, "help": "裁剪、重采样、格式转换等"},
]

DATA_TYPES: dict[str, dict[str, Any]] = {
    "sar": {
        "id": "sar",
        "label": "SAR 后向散射",
        "summary": "Sentinel-1 GRD、RTC 等已定标的后向散射，每个波段一种极化。",
        "formats": ["GeoTIFF/COG（.tif）"],
        "suffixes": sorted(RASTER_SUFFIXES),
        "file_roles": ["data"],
        "multi_file": False,
        "fields": [
            {"key": "datetime", "label": "成像日期", "kind": "date", "required": True},
            {"key": "polarizations", "label": "极化（按波段顺序）", "kind": "multiselect", "required": True, "options": _options(POLARIZATIONS)},
            {"key": "frequency_band", "label": "频段", "kind": "select", "required": True, "options": _options(FREQUENCY_BANDS), "default": "C"},
            {"key": "orbit_state", "label": "轨道方向", "kind": "select", "required": True, "options": _options({"ascending": "升轨", "descending": "降轨"})},
            {"key": "relative_orbit", "label": "相对轨道号", "kind": "number", "required": False, "help": "变化分析要求同一相对轨道"},
            {"key": "value_scale", "label": "数值尺度", "kind": "select", "required": True, "options": _options({"linear": "线性功率", "db": "dB"}), "default": "linear"},
            {"key": "backscatter", "label": "定标约定", "kind": "select", "required": True, "options": _options(BACKSCATTER)},
        ],
        "notes": "不接受 SLC 复数数据和 SAFE 原始包，需要先处理为 GRD 或 RTC 并导出 GeoTIFF。",
    },
    "optical": {
        "id": "optical",
        "label": "多光谱光学",
        "summary": "Sentinel-2、Landsat 等多光谱反射率；可以是一个多波段文件，也可以每个波段一个文件，可附云掩膜。",
        "formats": ["GeoTIFF/COG（.tif），多波段或每波段一个文件"],
        "suffixes": sorted(RASTER_SUFFIXES),
        "file_roles": ["data", "cloud-mask"],
        "multi_file": True,
        "fields": [
            {"key": "datetime", "label": "成像日期", "kind": "date", "required": True},
            {"key": "processing_level", "label": "处理级别", "kind": "select", "required": True, "options": _options({"L2": "L2 地表反射率", "L1": "L1 大气层顶反射率"})},
            {"key": "cloud_cover", "label": "云量（%）", "kind": "number", "required": False},
        ],
        "band_fields": ["common_name", "scale", "offset"],
        "notes": "第一批只接受研究区裁剪后的文件（单个不超过 64 MB）；JP2 需先转成 GeoTIFF。整数存储的反射率需要填写换算系数（反射率 = 值 × scale + offset）。",
    },
    "dem": {
        "id": "dem",
        "label": "高程（DEM）",
        "summary": "Copernicus DEM、SRTM、LiDAR DTM 等单波段高程，单位米。",
        "formats": ["GeoTIFF/COG（.tif），单波段"],
        "suffixes": sorted(RASTER_SUFFIXES),
        "file_roles": ["data"],
        "multi_file": False,
        "fields": [
            {"key": "start_date", "label": "数据时间段起", "kind": "date", "required": True, "help": "采集时间段；只知道年份时填当年 1 月 1 日"},
            {"key": "end_date", "label": "数据时间段止", "kind": "date", "required": True},
            {"key": "surface", "label": "高程类型", "kind": "select", "required": True, "options": _options(SURFACES)},
            {"key": "vertical_datum", "label": "高程基准", "kind": "select", "required": False, "options": _options(VERTICAL_DATUMS)},
        ],
        "notes": "坡度等计算需要投影坐标系（米）；地理坐标的 DEM 可以登记，分析时会被要求先投影。",
    },
    "vector": {
        "id": "vector",
        "label": "矢量",
        "summary": "研究区、行政边界、参考范围（如洪水范围）、建筑和道路等。",
        "formats": ["GeoJSON（.geojson/.json）", "GeoPackage（.gpkg）", "Shapefile（.zip，含 .shp/.shx/.dbf/.prj）"],
        "suffixes": sorted(VECTOR_SUFFIXES),
        "file_roles": ["data"],
        "multi_file": False,
        "fields": [
            {"key": "datetime", "label": "数据日期", "kind": "date", "required": True, "help": "数据描述的日期，如洪水范围对应的观测日期；边界填发布日期"},
            {"key": "purpose", "label": "用途", "kind": "select", "required": True, "options": _options(VECTOR_PURPOSES)},
            {"key": "origin", "label": "数据性质", "kind": "select", "required": True, "options": _options({k: ORIGINS[k] for k in ("manual", "model_estimate", "observed")}), "default": "manual"},
            {"key": "label_property", "label": "类别属性列（参考范围可选）", "kind": "text", "required": False},
            {"key": "description", "label": "说明（参考范围必填）", "kind": "textarea", "required": False, "help": "如何制作、可用于什么"},
        ],
        "notes": "Shapefile 上传后转换为 GeoPackage 登记，原始 zip 一并保留；缺少 .prj 时坐标系未知，会被拒收。",
    },
    "classification": {
        "id": "classification",
        "label": "分类栅格",
        "summary": "水体掩膜、土地覆盖、模型分割结果、人工参考标注等整数类别栅格。",
        "formats": ["GeoTIFF/COG（.tif），单波段整数"],
        "suffixes": sorted(RASTER_SUFFIXES),
        "file_roles": ["data"],
        "multi_file": False,
        "fields": [
            {"key": "datetime", "label": "数据日期", "kind": "date", "required": True},
            {"key": "origin", "label": "数据性质", "kind": "select", "required": True, "options": _options({k: ORIGINS[k] for k in ("manual", "model_estimate", "observed")})},
            {"key": "description", "label": "说明（人工标注必填）", "kind": "textarea", "required": False},
        ],
        "class_presets": {key: preset["title"] for key, preset in CLASS_PRESETS.items() if key != "s2_scl"},
        "notes": "像元值必须都在类别表中（nodata 值也要在表中标出），否则拒收。",
    },
}


def public_specs() -> list[dict[str, Any]]:
    specs = []
    for spec in DATA_TYPES.values():
        specs.append({**spec, "common_fields": COMMON_FIELDS})
    return specs


# ---------------------------------------------------------------- helpers


def file_format(name: str, data_type: str) -> str | None:
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    table = VECTOR_SUFFIXES if data_type == "vector" else RASTER_SUFFIXES
    return table.get(suffix)


def _iso(day: str, end: bool = False) -> str:
    return f"{day}T23:59:59Z" if end else f"{day}T00:00:00Z"


def valid_day(value: Any) -> bool:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def area_distorting(profile: dict[str, Any]) -> bool:
    method = str(profile.get("projection_method") or "").lower()
    return profile.get("crs") in MERCATOR_CODES or ("mercator" in method and "transverse" not in method)


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Report:
    """Collects messages; the worst level decides the outcome."""

    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def error(self, text: str) -> None:
        self.messages.append({"level": "error", "text": text})

    def warn(self, text: str) -> None:
        self.messages.append({"level": "warning", "text": text})

    def info(self, text: str) -> None:
        self.messages.append({"level": "info", "text": text})

    @property
    def status(self) -> str:
        levels = {m["level"] for m in self.messages}
        return "rejected" if "error" in levels else "warning" if "warning" in levels else "ok"

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "messages": self.messages}


# ---------------------------------------------------------------- validation
# ``files``: [{"n", "original_name", "format", "role", "profile", "bands"?}] for one item.
# ``fields``: the form values for that item.


def validate(data_type: str, files: list[dict[str, Any]], fields: dict[str, Any]) -> dict[str, Any]:
    report = Report()
    spec = DATA_TYPES.get(data_type)
    if spec is None:
        report.error(f"未知的数据类型：{data_type}")
        return report.as_dict()
    if not files:
        report.error("没有文件。")
        return report.as_dict()
    if len(files) > 1 and not spec["multi_file"]:
        report.error(f"{spec['label']}每条记录只能有一个文件。")
    for item in files:
        _check_file(spec, item, report)
    if report.status == "rejected":
        return report.as_dict()
    for field in spec["fields"]:
        if field.get("required") and fields.get(field["key"]) in (None, "", []):
            report.error(f"请填写“{field['label']}”。")
    for key in ("datetime", "start_date", "end_date"):
        if fields.get(key) not in (None, "") and not valid_day(fields.get(key)):
            report.error("日期格式应为 YYYY-MM-DD。")
            break
    if fields.get("license") and not re.fullmatch(r"[A-Za-z0-9.+()\- ]{1,80}", str(fields["license"])):
        report.error("许可应为 SPDX 标识（如 CC-BY-4.0）或 other。")
    CHECKS[data_type](files, fields, report)
    return report.as_dict()


def _check_file(spec: dict[str, Any], item: dict[str, Any], report: Report) -> None:
    name = item.get("original_name") or f"文件 {item.get('n')}"
    profile = item.get("profile") or {}
    if item.get("format") is None:
        report.error(f"{name}：{spec['label']}只接受 {'、'.join(spec['formats'])}。")
        return
    if not profile.get("ok"):
        report.error(f"{name}：无法读取（{profile.get('error') or '未返回检查结果'}）。")
        return
    expected = "vector" if spec["id"] == "vector" else "raster"
    if profile.get("kind") != expected:
        report.error(f"{name}：不是{'矢量' if expected == 'vector' else '栅格'}文件。")
        return
    missing = profile.get("shapefile_missing")
    if missing:
        reason = "，坐标系未知" if ".prj" in missing else ""
        report.error(f"{name}：Shapefile 缺少 {'、'.join(missing)}{reason}。")
        return
    if not profile.get("crs") and not profile.get("crs_wkt"):
        report.error(f"{name}：缺少坐标参考系。")
        return
    bbox = profile.get("bbox_wgs84")
    if not bbox or not (-180 <= bbox[0] <= bbox[2] <= 180 and -90 <= bbox[1] <= bbox[3] <= 90):
        report.error(f"{name}：无法换算出合法的经纬度范围。")


def _bands(profile: dict[str, Any]) -> list[dict[str, Any]]:
    return profile.get("bands") or []


def _check_sar(files: list[dict[str, Any]], fields: dict[str, Any], report: Report) -> None:
    profile = files[0]["profile"]
    bands = _bands(profile)
    pols = fields.get("polarizations") or []
    if any(p not in POLARIZATIONS for p in pols) or len(set(pols)) != len(pols):
        report.error("极化取值应为 HH、VV、HV、VH，且不能重复。")
    elif pols and len(pols) != len(bands):
        report.error(f"文件有 {len(bands)} 个波段，填写了 {len(pols)} 种极化，两者应一一对应。")
    if fields.get("frequency_band") and fields["frequency_band"] not in FREQUENCY_BANDS:
        report.error("频段取值不合法。")
    if fields.get("backscatter") and fields["backscatter"] not in BACKSCATTER:
        report.error("定标约定取值不合法。")
    if fields.get("relative_orbit") not in (None, ""):
        orbit = _number(fields["relative_orbit"])
        if orbit is None or orbit < 1 or orbit != int(orbit):
            report.error("相对轨道号应为正整数。")
    else:
        report.warn("没有相对轨道号：做变化分析时无法确认两期是否同一轨道。")
    for band in bands:
        dtype = str(band.get("dtype"))
        if dtype.startswith("complex") or dtype.startswith("c"):
            report.error("这是复数数据（SLC），需要先处理为 GRD 或 RTC 后向散射。")
            return
        if not dtype.startswith("float"):
            report.error(f"波段 {band['index']} 是 {dtype}：SAR 后向散射应为浮点数，整数 DN 需要先定标。")
            return
    scale = fields.get("value_scale")
    for band in bands:
        stats = band.get("statistics") or {}
        if scale == "linear" and (stats.get("negative_fraction") or 0) > LINEAR_NEGATIVE_LIMIT:
            report.warn(
                f"波段 {band['index']} 有 {stats['negative_fraction']:.0%} 的有效值为负，线性功率不应为负，可能其实是 dB。"
            )
        if scale == "db" and stats.get("minimum") is not None and stats["minimum"] >= 0 and (stats.get("p98") or 0) < 2:
            report.warn(f"波段 {band['index']} 的值都在 0–2 之间且没有负值，更像线性功率而不是 dB。")
    if all(band.get("nodata") is None for band in bands):
        report.warn("文件没有声明 nodata；非有限值仍按无效处理，其他无效值无法识别。")
    if area_distorting(profile):
        report.info("网格是 Mercator 类投影，面积不能按名义像元大小计算，分析时会被拦截。")


def _check_optical(files: list[dict[str, Any]], fields: dict[str, Any], report: Report) -> None:
    data = [f for f in files if f.get("role", "data") == "data"]
    masks = [f for f in files if f.get("role") == "cloud-mask"]
    if not data:
        report.error("至少需要一个反射率数据文件。")
        return
    if len(masks) > 1:
        report.error("每条记录最多一个云掩膜文件。")
    crs = {f["profile"].get("crs") or f["profile"].get("crs_wkt") for f in files}
    if len(crs) > 1:
        report.error("各文件坐标系不同，同一次观测的波段应在同一坐标系下。")
        return
    resolutions = sorted({round(f["profile"]["resolution"][0], 6) for f in files})
    base = resolutions[0]
    if any(abs(r / base - round(r / base)) > 1e-6 for r in resolutions):
        report.error("各文件分辨率不成整数倍（" + "、".join(f"{r:g}" for r in resolutions) + "），不能作为同一次观测。")
    boxes = [f["profile"]["bounds"] for f in files]
    west, south = max(b[0] for b in boxes), max(b[1] for b in boxes)
    east, north = min(b[2] for b in boxes), min(b[3] for b in boxes)
    if west >= east or south >= north:
        report.error("各文件范围互不重叠。")
    names: list[str] = []
    for item in data:
        bands = _bands(item["profile"])
        filled = item.get("bands") or []
        if len(filled) != len(bands):
            report.error(f"{item['original_name']}：有 {len(bands)} 个波段，需要逐个填写标准名。")
            continue
        for band, entry in zip(bands, filled):
            common = entry.get("common_name")
            if common not in COMMON_NAMES:
                report.error(f"{item['original_name']} 波段 {band['index']}：请选择标准波段名（blue、green、red、nir 等）。")
                continue
            names.append(common)
            scale, offset = _number(entry.get("scale")), _number(entry.get("offset"))
            integer = not str(band.get("dtype")).startswith("float")
            if integer and scale is None:
                report.error(
                    f"{item['original_name']} 波段 {band['index']}（{common}）是整数存储，需要填写换算系数 scale（如 0.0001）。"
                )
                continue
            stats = band.get("statistics") or {}
            if stats.get("p2") is not None:
                s, o = (scale if scale is not None else 1.0), (offset or 0.0)
                low, high = stats["p2"] * s + o, stats["p98"] * s + o
                if low < REFLECTANCE_RANGE[0] or high > REFLECTANCE_RANGE[1]:
                    report.warn(
                        f"{common} 换算后 2–98% 的值在 {low:.3f}–{high:.3f}，超出反射率常见范围，换算系数可能不对。"
                    )
    duplicated = sorted({n for n in names if names.count(n) > 1})
    if duplicated:
        report.error("标准波段名重复：" + "、".join(duplicated) + "。")
    for mask in masks:
        bands = _bands(mask["profile"])
        if len(bands) != 1 or str(bands[0].get("dtype")).startswith("float"):
            report.error(f"{mask['original_name']}：云掩膜应为单波段整数栅格（如 Sentinel-2 SCL）。")
            continue
        declared = {c["value"] for c in CLASS_PRESETS["s2_scl"]["classes"]}
        extra = sorted(set((bands[0].get("statistics") or {}).get("distinct") or []) - declared)
        if extra:
            report.warn(f"{mask['original_name']}：出现 SCL 类别表之外的值 {extra}，请确认这是 Sentinel-2 SCL。")
    cloud = _number(fields.get("cloud_cover"))
    if fields.get("cloud_cover") not in (None, "") and (cloud is None or not 0 <= cloud <= 100):
        report.error("云量应在 0–100 之间。")
    if not masks:
        report.info("没有云掩膜：分析时无法扣除云和云影。")


def _check_dem(files: list[dict[str, Any]], fields: dict[str, Any], report: Report) -> None:
    profile = files[0]["profile"]
    bands = _bands(profile)
    if len(bands) != 1:
        report.error(f"DEM 应为单波段，这个文件有 {len(bands)} 个波段。")
        return
    dtype = str(bands[0].get("dtype"))
    if dtype.startswith("uint"):
        report.warn(f"高程用无符号整数（{dtype}）存储，无法表示海平面以下的高程。")
    elif not (dtype.startswith("float") or dtype.startswith("int")):
        report.error(f"高程数据类型 {dtype} 不支持。")
    stats = bands[0].get("statistics") or {}
    if stats.get("minimum") is not None and (
        stats["minimum"] < ELEVATION_RANGE[0] or stats["maximum"] > ELEVATION_RANGE[1]
    ):
        report.warn(f"高程范围 {stats['minimum']:.0f}–{stats['maximum']:.0f} m 超出常见范围，单位或 nodata 可能不对。")
    start, end = fields.get("start_date"), fields.get("end_date")
    if valid_day(start) and valid_day(end) and start > end:
        report.error("时间段的起始日期晚于结束日期。")
    if fields.get("surface") and fields["surface"] not in SURFACES:
        report.error("高程类型取值不合法。")
    if not fields.get("vertical_datum"):
        report.warn("没有填写高程基准：不同基准的高程可能相差几十米，不能直接相减。")
    elif fields["vertical_datum"] not in VERTICAL_DATUMS:
        report.error("高程基准取值不合法。")
    if not profile.get("projected"):
        report.info("DEM 是地理坐标（度）：计算坡度前需要先投影到以米为单位的坐标系。")


def _check_vector(files: list[dict[str, Any]], fields: dict[str, Any], report: Report) -> None:
    profile = files[0]["profile"]
    count = profile.get("feature_count") or 0
    if count == 0:
        report.error("文件中没有要素。")
        return
    if count > MAX_FEATURES_WARNING:
        report.warn(f"要素数 {count} 较多，预览只显示外包框，分析可能较慢。")
    if profile.get("invalid_geometries"):
        report.warn(f"有 {profile['invalid_geometries']} 个无效几何（如自相交）；分析前修复会作为处理步骤记录。")
    layers = profile.get("layers") or []
    if len(layers) > 1:
        report.info(f"GeoPackage 有 {len(layers)} 个图层，只登记第一个图层“{profile.get('layer')}”。")
    purpose = fields.get("purpose")
    if purpose and purpose not in VECTOR_PURPOSES:
        report.error("用途取值不合法。")
    if fields.get("origin") and fields["origin"] not in ORIGINS:
        report.error("数据性质取值不合法。")
    if purpose == "reference" and not str(fields.get("description") or "").strip():
        report.error("参考范围需要填写说明：如何制作、对应哪次观测。")
    prop = str(fields.get("label_property") or "").strip()
    columns = {c["name"] for c in profile.get("columns") or []}
    if prop and prop not in columns:
        report.error(f"属性列“{prop}”不存在；可选：{'、'.join(sorted(columns)) or '无'}。")


def _check_classification(files: list[dict[str, Any]], fields: dict[str, Any], report: Report) -> None:
    profile = files[0]["profile"]
    bands = _bands(profile)
    if len(bands) != 1:
        report.error(f"分类栅格应为单波段，这个文件有 {len(bands)} 个波段。")
        return
    if not profile.get("integer"):
        report.error(f"分类栅格应为整数类型，这个文件是 {bands[0].get('dtype')}。")
        return
    classes = fields.get("classes") or []
    if not classes:
        report.error("请填写类别表（或选择预设）。")
        return
    values: set[int] = set()
    for entry in classes:
        value = entry.get("value")
        if not isinstance(value, int) or isinstance(value, bool):
            report.error("类别值应为整数。")
            return
        if value in values:
            report.error(f"类别值 {value} 重复。")
            return
        values.add(value)
        if not _CLASS_NAME.fullmatch(str(entry.get("name") or "")):
            report.error(f"类别 {value} 的名称只能用字母、数字、- 和 _。")
            return
        if _AUTO_CLASS_NAME.fullmatch(str(entry.get("name"))) and not str(entry.get("title") or "").strip():
            report.error(f"类别 {value} 的名称是按像元值自动生成的，请填写这一类的含义（名称或显示名），或删去不应出现的值。")
            return
        if entry.get("color_hint") and not re.fullmatch(r"[0-9a-fA-F]{6}", str(entry["color_hint"])):
            report.error(f"类别 {value} 的颜色应为 6 位十六进制，如 2570eb。")
            return
    stats = bands[0].get("statistics") or {}
    nodata = bands[0].get("nodata")
    present = set(stats.get("distinct") or [])
    if nodata is not None:
        present.discard(int(nodata))
    extra = sorted(present - values)
    if extra:
        report.error("像元值 " + "、".join(str(v) for v in extra[:20]) + " 不在类别表中。")
    if stats.get("distinct_truncated"):
        report.warn("不同像元值超过 256 个，只检查了前 256 个；这可能不是分类栅格。")
    if not stats.get("distinct_full_resolution", True):
        report.info("栅格较大，类别值按抽样检查，稀有值可能漏检。")
    if nodata is not None and int(nodata) in values:
        flagged = [c for c in classes if c["value"] == int(nodata)]
        if not flagged[0].get("nodata"):
            report.warn(f"文件的 nodata 值 {int(nodata)} 在类别表中，但没有标为 nodata。")
    if fields.get("origin") == "manual" and not str(fields.get("description") or "").strip():
        report.error("人工标注需要填写说明：如何制作、对应哪次观测。")
    if fields.get("origin") and fields["origin"] not in ORIGINS:
        report.error("数据性质取值不合法。")


CHECKS = {
    "sar": _check_sar,
    "optical": _check_optical,
    "dem": _check_dem,
    "vector": _check_vector,
    "classification": _check_classification,
}


# ---------------------------------------------------------------- STAC item


def multihash_sha256(hex_digest: str) -> str:
    return "1220" + hex_digest


def bbox_union(boxes: list[list[float]]) -> list[float]:
    return [
        round(min(b[0] for b in boxes), 6),
        round(min(b[1] for b in boxes), 6),
        round(max(b[2] for b in boxes), 6),
        round(max(b[3] for b in boxes), 6),
    ]


def bbox_polygon(bbox: list[float]) -> dict[str, Any]:
    w, s, e, n = bbox
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def _nodata(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        if value != value:
            return "nan"
        if value in (float("inf"), float("-inf")):
            return "inf" if value > 0 else "-inf"
        if value.is_integer():
            return int(value)
    return value


def projection_fields(profile: dict[str, Any], raster: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"proj:code": profile.get("crs")}
    if not profile.get("crs"):
        out["proj:wkt2"] = profile.get("crs_wkt")
    if raster:
        out["proj:shape"] = profile["shape"]
        out["proj:transform"] = profile["transform"]
    elif profile.get("crs") != "EPSG:4326" and profile.get("bounds"):
        out["proj:bbox"] = [round(v, 6) for v in profile["bounds"]]
    return out


def band_object(band: dict[str, Any], name: str, unit: str | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"name": name}
    dtype = str(band.get("dtype"))
    out["data_type"] = dtype if dtype in STAC_DATA_TYPES else "other"
    nodata = _nodata(band.get("nodata"))
    if nodata is not None:
        out["nodata"] = nodata
    if unit:
        out["unit"] = unit
    out.update(extra or {})
    return out


def class_objects(classes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for entry in classes:
        obj: dict[str, Any] = {"value": int(entry["value"]), "name": str(entry["name"])}
        for key in ("title", "description", "color_hint"):
            if entry.get(key):
                obj[key] = str(entry[key])
        if entry.get("nodata"):
            obj["nodata"] = True
        out.append(obj)
    return out


def build_item(
    data_type: str,
    item_id: str,
    collection: dict[str, Any],
    files: list[dict[str, Any]],
    fields: dict[str, Any],
    *,
    audience: str,
    created: str,
    hrefs: dict[int, str],
) -> dict[str, Any]:
    """The STAC Item for an accepted upload. ``hrefs`` maps file number to the stored file path."""
    extensions = {"projection", "file"}
    props: dict[str, Any] = {}
    if data_type == "dem":
        props["datetime"] = None
        props["start_datetime"] = _iso(fields["start_date"])
        props["end_datetime"] = _iso(fields["end_date"], end=True)
    else:
        props["datetime"] = _iso(fields["datetime"])
    for key in ("title", "platform", "license"):
        if str(fields.get(key) or "").strip():
            props[key] = str(fields[key]).strip()
    props["created"] = props["updated"] = created
    if str(fields.get("lineage") or "").strip():
        props["processing:lineage"] = str(fields["lineage"]).strip()
        extensions.add("processing")
    origin = "observed" if data_type in ("sar", "optical", "dem") else fields.get("origin") or "manual"
    props.update(
        {
            "workspace:data_type": data_type,
            "workspace:origin": origin,
            "workspace:verification": "declared",
            "workspace:audience": audience,
        }
    )
    for key in ("place", "event"):
        if collection.get(key):
            props[f"workspace:{key}"] = collection[key]
    if fields.get("claim_boundary"):
        props["workspace:claim_boundary"] = fields["claim_boundary"]

    assets: dict[str, Any] = {}
    data_files = [f for f in files if f.get("role", "data") == "data"]
    for item in files:
        profile = item["profile"]
        raster = data_type != "vector"
        asset: dict[str, Any] = {
            "href": hrefs[item["n"]],
            "type": MEDIA_TYPES["gpkg" if item.get("converted_to") == "gpkg" else item["format"]],
            "roles": ["data"],
            "title": item.get("original_name"),
            "file:size": item.get("stored_bytes", item.get("bytes")),
            "file:checksum": multihash_sha256(item.get("stored_sha256", item["sha256"])),
            **projection_fields(profile, raster),
        }
        bands = _bands(profile)
        if data_type == "sar":
            unit = "dB" if fields.get("value_scale") == "db" else "1"
            asset["bands"] = [band_object(b, p, unit) for b, p in zip(bands, fields["polarizations"])]
        elif data_type == "optical" and item.get("role", "data") == "data":
            extensions.update({"eo", "raster"})
            entries = item.get("bands") or []
            asset["bands"] = []
            for band, entry in zip(bands, entries):
                extra: dict[str, Any] = {"eo:common_name": entry["common_name"]}
                scale, offset = _number(entry.get("scale")), _number(entry.get("offset"))
                if scale is not None:
                    extra["raster:scale"] = scale
                if offset is not None:
                    extra["raster:offset"] = offset
                name = str(entry.get("name") or band.get("description") or entry["common_name"])
                asset["bands"].append(band_object(band, name, None, extra))
            asset["raster:spatial_resolution"] = profile["resolution"][0]
        elif data_type == "optical":
            extensions.add("classification")
            asset["roles"] = ["data-mask", "cloud"]
            asset["bands"] = [band_object(bands[0], "SCL")]
            asset["classification:classes"] = class_objects(CLASS_PRESETS["s2_scl"]["classes"])
        elif data_type == "dem":
            asset["bands"] = [band_object(bands[0], "elevation", "m")]
        elif data_type == "classification":
            extensions.add("classification")
            asset["bands"] = [band_object(bands[0], "class")]
            asset["classification:classes"] = class_objects(fields["classes"])
        elif data_type == "vector":
            extensions.update({"vector", "table"})
            asset["vector:geometry_types"] = profile.get("geometry_types") or []
            asset["table:row_count"] = profile.get("feature_count")
            asset["table:columns"] = [{"name": c["name"], "type": c["type"]} for c in profile.get("columns") or []]
            if item.get("converted_to") == "gpkg":
                asset["table:primary_geometry"] = "geom"
        key = "data" if item is data_files[0] else ("cloud-mask" if item.get("role") == "cloud-mask" else f"data-{item['n']}")
        assets[key] = asset
        if item.get("source_archive_href"):
            assets["source-archive"] = {
                "href": item["source_archive_href"],
                "type": MEDIA_TYPES["zip"],
                "roles": ["source"],
                "title": f"{item.get('original_name')}（上传的原始 Shapefile）",
                "file:size": item.get("bytes"),
                "file:checksum": multihash_sha256(item["sha256"]),
            }

    if data_type == "sar":
        extensions.update({"sar", "sat"})
        props["sar:polarizations"] = list(fields["polarizations"])
        props["sar:frequency_band"] = fields["frequency_band"]
        props["sat:orbit_state"] = fields["orbit_state"]
        if fields.get("relative_orbit") not in (None, ""):
            props["sat:relative_orbit"] = int(float(fields["relative_orbit"]))
        props["workspace:backscatter"] = fields["backscatter"]
    elif data_type == "optical":
        extensions.add("processing")
        props["processing:level"] = fields["processing_level"]
        if fields.get("cloud_cover") not in (None, ""):
            props["eo:cloud_cover"] = float(fields["cloud_cover"])
    elif data_type == "dem":
        props["workspace:elevation_surface"] = fields["surface"]
        if fields.get("vertical_datum"):
            props["workspace:vertical_datum"] = fields["vertical_datum"]
    elif data_type == "vector":
        props["workspace:purpose"] = fields["purpose"]
        if fields.get("purpose") == "reference":
            extensions.add("label")
            prop = str(fields.get("label_property") or "").strip()
            props["label:type"] = "vector"
            props["label:properties"] = [prop] if prop else []
            props["label:description"] = str(fields.get("description") or "").strip()
            props["label:methods"] = ["manual" if origin == "manual" else "automated"]
            props["label:tasks"] = ["segmentation"]
        elif str(fields.get("description") or "").strip():
            props["description"] = str(fields["description"]).strip()
    elif data_type == "classification":
        if origin == "manual":
            extensions.add("label")
            props["label:type"] = "raster"
            props["label:properties"] = None
            props["label:description"] = str(fields.get("description") or "").strip()
            props["label:methods"] = ["manual"]
            props["label:tasks"] = ["segmentation"]
        elif str(fields.get("description") or "").strip():
            props["description"] = str(fields["description"]).strip()

    bbox = bbox_union([f["profile"]["bbox_wgs84"] for f in files])
    links = [{"rel": "collection", "href": "../collection.json", "type": "application/json"}]
    if str(fields.get("source_url") or "").strip():
        links.append({"rel": "derived_from", "href": str(fields["source_url"]).strip()})
    return {
        "type": "Feature",
        "stac_version": STAC_VERSION,
        "stac_extensions": sorted(EXTENSIONS[e] for e in extensions),
        "id": item_id,
        "collection": collection["id"],
        "bbox": bbox,
        "geometry": bbox_polygon(bbox),
        "properties": props,
        "links": links,
        "assets": assets,
    }


def build_collection(collection: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    boxes = [item["bbox"] for item in items] or [[-180, -90, 180, 90]]
    times = []
    for item in items:
        p = item["properties"]
        times += [t for t in (p.get("datetime"), p.get("start_datetime"), p.get("end_datetime")) if t]
    spec = DATA_TYPES.get(collection.get("data_type") or "", {})
    return {
        "type": "Collection",
        "stac_version": STAC_VERSION,
        "id": collection["id"],
        "title": collection.get("title") or collection["id"],
        "description": collection.get("description") or f"{spec.get('label', '数据')}：{collection.get('title') or collection['id']}",
        "license": collection.get("license") or "other",
        "extent": {
            "spatial": {"bbox": [bbox_union(boxes)]},
            "temporal": {"interval": [[min(times), max(times)] if times else [None, None]]},
        },
        "links": [],
        "workspace:data_type": collection.get("data_type"),
        "workspace:place": collection.get("place") or None,
        "workspace:event": collection.get("event") or None,
    }


def item_date(item: dict[str, Any]) -> str | None:
    p = item.get("properties") or {}
    value = p.get("datetime") or p.get("start_datetime")
    return str(value)[:10] if value else None


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    # Times without an offset (e.g. a bare date) are taken as UTC, like STAC datetimes.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- prefill
# Suggestions from the file itself; the uploader confirms or corrects them.

S2_BANDS = {
    "B01": "coastal", "B02": "blue", "B03": "green", "B04": "red", "B05": "rededge071",
    "B06": "rededge075", "B07": "rededge078", "B08": "nir", "B8A": "nir08", "B09": "nir09",
    "B10": "cirrus", "B11": "swir16", "B12": "swir22",
}
LANDSAT_OLI_BANDS = {
    "SR_B1": "coastal", "SR_B2": "blue", "SR_B3": "green", "SR_B4": "red",
    "SR_B5": "nir08", "SR_B6": "swir16", "SR_B7": "swir22",
}
_NAME_DATE = re.compile(r"(20\d{2}|19\d{2})-?([01]\d)-?([0-3]\d)")


def date_from_name(name: str) -> str | None:
    match = _NAME_DATE.search(name or "")
    if not match:
        return None
    day = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return day if valid_day(day) else None


def _band_label(band: dict[str, Any], name: str) -> str | None:
    """A band name from the file's band description, else from the file name (e.g. *_B11_20m.tif)."""
    description = str(band.get("description") or "").strip().upper()
    if description:
        return description
    tokens = re.split(r"[^A-Za-z0-9]+", name.upper())
    known = [t for t in tokens if t in S2_BANDS]
    return known[0] if len(known) == 1 else None


def suggest(data_type: str, item: dict[str, Any]) -> dict[str, Any]:
    profile = item.get("profile") or {}
    name = str(item.get("original_name") or "")
    out: dict[str, Any] = {"fields": {}}
    day = date_from_name(name)
    if day:
        out["fields"]["datetime"] = day
    bands = _bands(profile)
    if data_type == "sar":
        pols = [str(b.get("description") or "").upper() for b in bands]
        if pols and all(p in POLARIZATIONS for p in pols):
            out["fields"]["polarizations"] = pols
        else:
            found = [p for p in POLARIZATIONS if re.search(rf"(^|[^A-Z]){p}([^A-Z]|$)", name.upper())]
            if len(found) == len(bands):
                out["fields"]["polarizations"] = found
        negative = max(((b.get("statistics") or {}).get("negative_fraction") or 0) for b in bands) if bands else 0
        out["fields"]["value_scale"] = "db" if negative > 0.2 else "linear"
    elif data_type == "optical":
        distinct = set((bands[0].get("statistics") or {}).get("distinct") or []) if len(bands) == 1 else set()
        is_mask = len(bands) == 1 and (
            "SCL" in name.upper() or (profile.get("integer") and distinct and distinct <= set(range(12)))
        )
        out["role"] = "cloud-mask" if is_mask else "data"
        if not is_mask:
            entries = []
            for band in bands:
                label = _band_label(band, name)
                common = S2_BANDS.get(label or "") or LANDSAT_OLI_BANDS.get(label or "")
                entries.append({"name": label or f"band{band['index']}", "common_name": common or ""})
            out["bands"] = entries
    elif data_type == "classification":
        distinct = set((bands[0].get("statistics") or {}).get("distinct") or []) if bands else set()
        nodata = bands[0].get("nodata") if bands else None
        if nodata is not None:
            distinct.discard(int(nodata))
        # The preset that explains most of the values present; values outside it are then flagged
        # by validation instead of being declared automatically.
        best, share = None, 0.0
        for key in ("water_binary", "esa_worldcover"):
            values = {c["value"] for c in CLASS_PRESETS[key]["classes"]}
            covered = len(distinct & values) / len(distinct) if distinct else 0.0
            if covered > share:
                best, share = key, covered
        if best and share >= PRESET_MATCH_SHARE:
            out["preset"] = best
            out["fields"]["classes"] = CLASS_PRESETS[best]["classes"]
        else:
            out["fields"]["classes"] = [{"value": v, "name": f"class_{v}"} for v in sorted(distinct)[:64]]
    elif data_type == "vector":
        out["columns"] = [c["name"] for c in profile.get("columns") or []]
    return out

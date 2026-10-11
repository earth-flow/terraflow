/** Chinese names for tools, components and parameters shown in the three views. */

export const TOOL_CN: Record<string, string> = {
  inspect_geotiff: "读取影像",
  raster_profile_preflight: "网格检查",
  threshold_segmentation: "阈值分割",
  raster_diff: "栅格差值",
  raster_stats: "栅格统计",
  water_evidence: "水体证据",
  water_extent: "水体范围",
  pixel_area: "像元面积",
  calculate_index: "计算指数",
  apply_cloud_mask: "云掩膜",
  water_by_class: "分类统计",
  compare_with_reference: "参考比对",
};

export const COMPONENT_CN: Record<string, string> = {
  SceneInput: "影像",
  WaterInputSet: "成对数据",
  ChatOutput: "输出",
  TerraboxRasterViewerInspectGeotiff: "读取影像",
  TerraboxGeoWaterRasterProfilePreflight: "网格检查",
  TerraboxGeoRasterThresholdSegmentation: "阈值分割",
  TerraboxGeoRasterCalculateIndex: "计算指数",
  TerraboxGeoWaterWaterEvidence: "水体证据",
  TerraboxGeoWaterWaterExtent: "水体范围",
  TerraboxGeoWaterWaterByClass: "分类统计",
  TerraboxGeoWaterCompareWithReference: "参考比对",
  TerraboxGeoRasterApplyCloudMask: "云掩膜",
};

/** Tools whose output is a scientific gate (grid, labels, projected area). */
export const GATE_TOOLS = new Set([
  "raster_profile_preflight",
  "water_evidence",
  "water_extent",
  "compare_with_reference",
  "water_by_class",
  "TerraboxGeoWaterRasterProfilePreflight",
  "TerraboxGeoWaterWaterEvidence",
  "TerraboxGeoWaterWaterExtent",
  "TerraboxGeoWaterCompareWithReference",
  "TerraboxGeoWaterWaterByClass",
]);

export const PARAM_CN: Record<string, string> = {
  scene_id: "数据",
  input_set: "成对数据",
  method: "方法",
  threshold: "阈值",
  direction: "方向",
  input_transform: "变换",
  otsu_range: "Otsu 范围",
  index_name: "指数",
  field_path: "字段",
  output_path: "输出",
};

export const TOOLKIT_CN: Record<string, string> = {
  geo_water: "水体",
  geo_raster: "栅格处理",
  raster_viewer: "影像查看",
  geo_basic: "基础几何",
  geo_statistics: "统计",
  geoanalysis: "空间分析",
  geo_perception: "地物识别",
  disaster_response: "灾害响应",
  earth_sci: "地球科学",
  geopatch: "影像切片",
  osm_gis: "OSM 与 GIS",
  stac_basic: "STAC 查询",
  github: "GitHub",
  bash: "命令行",
  ipython: "Python",
  bing_search: "网页搜索",
  example: "示例",
};

export const GENERIC_TOOLKITS = new Set([
  "github",
  "bash",
  "ipython",
  "bing_search",
  "example",
]);

export function toolName(slug: string) {
  const short = slug.split(".").at(-1) ?? slug;
  return TOOL_CN[short] ?? short;
}

function fmt(value: number, digits = 2) {
  return Number(value.toFixed(digits)).toString();
}

/** The one number worth printing on a node after a run. */
export function keyOutput(
  outputs?: Record<string, unknown>,
): [string, string] | null {
  if (!outputs) return null;
  const n = (key: string) =>
    typeof outputs[key] === "number" ? (outputs[key] as number) : undefined;
  if (outputs.execution_blocked === true)
    return ["拦截", String(outputs.reason_code ?? "门控")];
  if (n("new_water_area_m2") !== undefined)
    return ["新增水体", fmt(n("new_water_area_m2")! / 1e6) + " km²"];
  if (n("water_area_m2") !== undefined)
    return ["水体面积", fmt(n("water_area_m2")! / 1e6) + " km²"];
  if (n("threshold") !== undefined)
    return [
      "阈值",
      fmt(n("threshold")!) + (outputs.input_transform === "db" ? " dB" : ""),
    ];
  if (n("iou") !== undefined) return ["IoU", fmt(n("iou")!, 3)];
  if (outputs.success === true && "execution_blocked" in outputs)
    return ["检查", "通过"];
  if (typeof outputs.crs === "string") return ["坐标系", outputs.crs];
  return null;
}

import { type ReactNode, useEffect, useState } from "react";
import { API, apiJson, formatJson } from "./api";
import type { CatalogItem } from "./types";

type Band = {
  name?: string;
  data_type?: string;
  nodata?: number | string;
  unit?: string;
  "eo:common_name"?: string;
  "raster:scale"?: number;
  "raster:offset"?: number;
};
type ClassObject = {
  value: number;
  name: string;
  title?: string;
  color_hint?: string;
  nodata?: boolean;
};
type Asset = {
  href: string;
  type?: string;
  roles?: string[];
  title?: string;
  "file:size"?: number;
  "file:checksum"?: string;
  "proj:code"?: string | null;
  "proj:shape"?: number[];
  "proj:transform"?: number[];
  bands?: Band[];
  "classification:classes"?: ClassObject[];
  "vector:geometry_types"?: string[];
  "table:row_count"?: number;
  "table:columns"?: { name: string; type?: string }[];
};
type StacItem = {
  id: string;
  bbox: number[];
  properties: Record<string, unknown>;
  links: { rel: string; href: string }[];
  assets: Record<string, Asset>;
};

const ASSET_LABELS: Record<string, string> = {
  data: "数据",
  "cloud-mask": "云掩膜",
  "source-archive": "上传的原始文件",
};
const ORBIT = { ascending: "升轨", descending: "降轨" } as Record<
  string,
  string
>;

function text(value: unknown) {
  return value === undefined || value === null || value === ""
    ? null
    : String(value);
}

function size(bytes?: number) {
  if (!bytes) return null;
  return bytes > 1024 * 1024
    ? (bytes / 1024 / 1024).toFixed(1) + " MB"
    : Math.ceil(bytes / 1024) + " KB";
}

function grid(asset: Asset) {
  const shape = asset["proj:shape"];
  const transform = asset["proj:transform"];
  if (!shape) return asset["proj:code"] ?? null;
  const resolution = transform ? Math.abs(transform[0]) : null;
  const unit = asset["proj:code"] === "EPSG:4326" ? "°" : " m";
  return [
    asset["proj:code"],
    shape[1] + "×" + shape[0],
    resolution
      ? (unit === "°" ? resolution.toPrecision(3) : resolution) + unit
      : null,
  ]
    .filter(Boolean)
    .join("，");
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div className="grid grid-cols-[6.5rem_1fr] gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words text-foreground">{value}</dd>
    </div>
  );
}

export default function ItemDetail({ item }: { item: CatalogItem }) {
  const [stac, setStac] = useState<StacItem | null>(null);
  const [error, setError] = useState("");
  const [raw, setRaw] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setStac(null);
    apiJson<{ item: StacItem }>(API + "/items/" + encodeURIComponent(item.id))
      .then((body) => !cancelled && setStac(body.item))
      .catch(
        (cause) =>
          !cancelled &&
          setError(cause instanceof Error ? cause.message : "读取失败"),
      );
    return () => {
      cancelled = true;
    };
  }, [item.id]);

  if (error)
    return <p className="text-xs text-destructive">无法读取详情：{error}</p>;
  if (!stac) return <p className="text-xs text-muted-foreground">读取中…</p>;
  const p = stac.properties;
  const source = stac.links.find((link) => link.rel === "derived_from");
  const time =
    text(p.datetime)?.replace("T", " ").replace("Z", " UTC") ??
    [text(p.start_datetime)?.slice(0, 10), text(p.end_datetime)?.slice(0, 10)]
      .filter(Boolean)
      .join(" → ");
  const orbit = p["sat:orbit_state"]
    ? (ORBIT[String(p["sat:orbit_state"])] ?? String(p["sat:orbit_state"])) +
      (p["sat:relative_orbit"] ? "，相对轨道 " + p["sat:relative_orbit"] : "")
    : null;

  return (
    <div className="rounded-2xl border border-border bg-card p-4 text-xs leading-5">
      <p className="text-sm font-medium">
        {item.collection_title} · {item.title}
      </p>
      <dl className="mt-3 space-y-1.5">
        <Row label="数据类型" value={item.type_label} />
        <Row
          label="时间"
          value={p["workspace:date_unknown"] ? "未记录" : time}
        />
        <Row label="平台" value={text(p.platform)} />
        <Row
          label="极化"
          value={(p["sar:polarizations"] as string[] | undefined)?.join("、")}
        />
        <Row label="频段" value={text(p["sar:frequency_band"])} />
        <Row label="轨道" value={orbit} />
        <Row label="定标约定" value={text(p["workspace:backscatter"])} />
        <Row label="处理级别" value={text(p["processing:level"])} />
        <Row
          label="云量"
          value={
            p["eo:cloud_cover"] !== undefined ? p["eo:cloud_cover"] + "%" : null
          }
        />
        <Row label="高程类型" value={text(p["workspace:elevation_surface"])} />
        <Row label="高程基准" value={text(p["workspace:vertical_datum"])} />
        <Row label="用途" value={text(p["workspace:purpose"])} />
        <Row label="标注说明" value={text(p["label:description"])} />
        <Row label="数据性质" value={item.origin_label} />
        <Row
          label="来源"
          value={
            <>
              {item.verification_label}
              {source && (
                <>
                  {"："}
                  <a
                    className="break-all text-primary underline"
                    href={source.href}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {source.href}
                  </a>
                </>
              )}
            </>
          }
        />
        <Row label="处理说明" value={text(p["processing:lineage"])} />
        <Row label="许可" value={text(p.license)} />
        <Row
          label="经纬度范围"
          value={stac.bbox.map((v) => v.toFixed(4)).join(", ")}
        />
        <Row label="使用边界" value={text(p["workspace:claim_boundary"])} />
      </dl>

      {Object.entries(stac.assets).map(([key, asset]) => (
        <div key={key} className="mt-3 rounded-xl border border-border p-3">
          <p className="font-medium">
            {ASSET_LABELS[key] ?? "数据（" + key + "）"}
            <span className="ml-2 font-normal text-muted-foreground">
              {[asset.title, size(asset["file:size"])]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </p>
          <dl className="mt-2 space-y-1.5">
            <Row label="格式" value={asset.type} />
            <Row
              label="网格"
              value={asset["proj:shape"] ? grid(asset) : asset["proj:code"]}
            />
            {asset.bands && asset.bands.length > 0 && (
              <Row
                label="波段"
                value={asset.bands
                  .map((band) =>
                    [
                      band.name,
                      band["eo:common_name"] &&
                      band["eo:common_name"] !== band.name
                        ? "（" + band["eo:common_name"] + "）"
                        : "",
                      " ",
                      band.data_type,
                      band.unit ? "，单位 " + band.unit : "",
                      band["raster:scale"] !== undefined
                        ? "，×" + band["raster:scale"]
                        : "",
                      band["raster:offset"] !== undefined
                        ? (band["raster:offset"] >= 0 ? "+" : "") +
                          band["raster:offset"]
                        : "",
                      band.nodata !== undefined
                        ? "，nodata " + band.nodata
                        : "",
                    ].join(""),
                  )
                  .join("；")}
              />
            )}
            <Row
              label="几何类型"
              value={asset["vector:geometry_types"]?.join("、")}
            />
            <Row label="要素数" value={text(asset["table:row_count"])} />
            <Row
              label="属性列"
              value={asset["table:columns"]?.map((c) => c.name).join("、")}
            />
            <Row
              label="校验和"
              value={
                asset["file:checksum"]
                  ? "SHA-256 " + asset["file:checksum"].slice(4, 16) + "…"
                  : null
              }
            />
          </dl>
          {asset["classification:classes"] && (
            <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
              {asset["classification:classes"].map((entry) => (
                <span
                  key={entry.value}
                  className="inline-flex items-center gap-1"
                >
                  <span
                    className="inline-block h-3 w-3 rounded-sm border border-border"
                    style={{
                      background: entry.color_hint
                        ? "#" + entry.color_hint
                        : "transparent",
                    }}
                  />
                  {entry.value} {entry.title ?? entry.name}
                  {entry.nodata ? "（nodata）" : ""}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}

      <button
        type="button"
        className="mt-3 text-primary underline"
        onClick={() => setRaw((value) => !value)}
      >
        {raw ? "收起 STAC 记录" : "查看 STAC 记录（JSON）"}
      </button>
      {raw && (
        <pre className="mt-2 max-h-96 overflow-auto rounded-xl bg-muted/40 p-3 text-[11px] leading-4">
          {formatJson(stac)}
        </pre>
      )}
    </div>
  );
}

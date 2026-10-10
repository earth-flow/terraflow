import { useState } from "react";
import { useCatalog } from "./api";
import ItemDetail from "./ItemDetail";
import type { CatalogItem } from "./types";
import UploadPanel from "./UploadPanel";

const TYPE_FILTERS = [
  { id: "", label: "全部" },
  { id: "sar", label: "SAR" },
  { id: "optical", label: "光学" },
  { id: "dem", label: "DEM" },
  { id: "vector", label: "矢量" },
  { id: "classification", label: "分类栅格" },
];

function ItemCard({ item }: { item: CatalogItem }) {
  const badges = [
    item.type_label,
    item.origin && item.origin !== "observed"
      ? (item.origin_label ?? "").split("（")[0]
      : null,
    item.verification === "declared" && !item.legacy ? "未核实来源" : null,
    item.self_check_for ? "门控自检样本" : null,
  ].filter(Boolean) as string[];
  return (
    <div className="ws-dataset block w-full rounded-2xl border border-border bg-card p-2.5 text-left">
      <div className="ws-thumb aspect-square overflow-hidden rounded-lg">
        <img
          src={item.preview}
          alt={item.title}
          loading="lazy"
          className={
            "h-full w-full [image-rendering:pixelated] " +
            (item.data_type === "vector" ? "object-contain" : "object-cover")
          }
        />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {badges.map((badge) => (
          <span
            key={badge}
            className="rounded-full bg-muted px-2 py-0.5 text-[11px]"
          >
            {badge}
          </span>
        ))}
      </div>
      <p className="mt-1 break-words text-sm font-medium leading-5">
        {item.title}
      </p>
      <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
        {item.facts.filter((fact) => fact !== item.type_label).join(" · ")}
      </p>
    </div>
  );
}

export default function DataPanel({
  onUse,
}: {
  onUse: (itemIds: string[]) => void;
}) {
  const { items, collections, error, loading, refresh } = useCatalog();
  const [showDeveloper, setShowDeveloper] = useState(false);
  const [filter, setFilter] = useState("");
  const [detail, setDetail] = useState<string | null>(null);
  const shown = items.filter(
    (item) =>
      (showDeveloper || item.audience !== "developer") &&
      (!filter || item.data_type === filter),
  );
  const counts = new Map<string, number>();
  for (const item of items)
    if (showDeveloper || item.audience !== "developer")
      counts.set(item.data_type, (counts.get(item.data_type) ?? 0) + 1);
  const groups = collections
    .map((collection) => ({
      collection,
      members: shown.filter((item) => item.collection_id === collection.id),
    }))
    .filter((group) => group.members.length > 0);
  const opened = items.find((item) => item.id === detail);

  return (
    <section className="space-y-4">
      <div className="rounded-2xl border border-border bg-card p-5">
        <h2 className="font-semibold">数据</h2>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          每份数据按 STAC
          条目记录：时间、范围、坐标网格、波段或类别、来源和处理说明。 目前支持
          SAR
          后向散射、多光谱光学、高程（DEM）、矢量和分类栅格五类，同一组只放一类数据。
          现有的两个水体场景能用 SAR（含 VV 波段）和 0/1
          水体分类栅格；其他类型可以上传和查看，分析场景随后接入。
        </p>
        <UploadPanel onUploaded={refresh} />
      </div>
      <div className="flex flex-wrap gap-2">
        {TYPE_FILTERS.map((option) => (
          <button
            key={option.id}
            type="button"
            onClick={() => setFilter(option.id)}
            className={
              "rounded-full border px-3 py-1 text-xs " +
              (filter === option.id
                ? "border-primary bg-primary/10 text-primary"
                : "border-border bg-card")
            }
          >
            {option.label}
            {option.id ? " " + (counts.get(option.id) ?? 0) : ""}
          </button>
        ))}
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {loading && (
        <p className="text-sm text-muted-foreground">读取数据列表…</p>
      )}
      {!loading && groups.length === 0 && !error && (
        <p className="text-sm text-muted-foreground">这一类还没有数据。</p>
      )}
      {groups.map(({ collection, members }) => {
        const usable = members.filter((item) => item.usable_in.length > 0);
        return (
          <div
            key={collection.id}
            className="rounded-2xl border border-border bg-card p-5"
          >
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <h3 className="font-medium">{collection.title}</h3>
              <span className="text-xs text-muted-foreground">
                {[
                  collection.type_label,
                  collection.place,
                  collection.period,
                  members.length + " 份",
                  collection.legacy ? "早期数据（只读转换）" : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
              {usable.length > 1 && (
                <button
                  type="button"
                  className="ml-auto text-xs text-primary underline"
                  onClick={() => onUse(usable.map((item) => item.id))}
                >
                  用这一组对话
                </button>
              )}
            </div>
            <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
              {members.map((item) => (
                <div key={item.id} className="flex min-w-0 flex-col gap-1.5">
                  <ItemCard item={item} />
                  <div className="flex flex-wrap gap-x-3 px-1">
                    {item.usable_in.length > 0 && (
                      <button
                        type="button"
                        className="text-xs text-primary underline"
                        onClick={() => onUse([item.id])}
                      >
                        用这份数据对话
                      </button>
                    )}
                    <button
                      type="button"
                      className="text-xs text-muted-foreground underline"
                      onClick={() =>
                        setDetail(detail === item.id ? null : item.id)
                      }
                    >
                      {detail === item.id ? "收起详情" : "详情"}
                    </button>
                  </div>
                </div>
              ))}
            </div>
            {opened && opened.collection_id === collection.id && (
              <div className="mt-3">
                <ItemDetail item={opened} />
              </div>
            )}
          </div>
        );
      })}
      <label className="flex items-center gap-2 px-1 text-xs text-muted-foreground">
        <input
          type="checkbox"
          checked={showDeveloper}
          onChange={(event) => setShowDeveloper(event.target.checked)}
        />
        显示开发者数据（合成测试栅格、测试上传、门控自检样本）
      </label>
    </section>
  );
}

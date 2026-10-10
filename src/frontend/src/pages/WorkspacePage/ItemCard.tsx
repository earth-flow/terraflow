import type { CatalogItem } from "./types";

export default function ItemCard({
  item,
  selected = false,
  onSelect,
  compact = false,
}: {
  item: CatalogItem;
  selected?: boolean;
  onSelect?: () => void;
  compact?: boolean;
}) {
  const badges = [
    item.type_label,
    item.origin && item.origin !== "observed"
      ? (item.origin_label ?? "").split("（")[0]
      : null,
    item.verification === "declared" && !item.legacy ? "未核实来源" : null,
    item.self_check_for ? "门控自检样本" : null,
  ].filter(Boolean) as string[];
  const body = (
    <>
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
        {selected && (
          <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] text-primary">
            已选
          </span>
        )}
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
      {!compact && (
        <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
          {item.facts.filter((fact) => fact !== item.type_label).join(" · ")}
        </p>
      )}
    </>
  );
  const className =
    "ws-dataset block w-full rounded-2xl border bg-card p-2.5 text-left transition " +
    (selected ? "ws-dataset-selected" : "border-border");
  return onSelect ? (
    <button
      type="button"
      className={className}
      onClick={onSelect}
      aria-pressed={selected}
    >
      {body}
    </button>
  ) : (
    <div className={className}>{body}</div>
  );
}

import { sceneFacts } from "./api";
import type { Scene } from "./types";
import { CATEGORY_LABELS } from "./types";

export default function SceneCard({
  scene,
  selected = false,
  onSelect,
  compact = false,
}: {
  scene: Scene;
  selected?: boolean;
  onSelect?: () => void;
  compact?: boolean;
}) {
  const body = (
    <>
      <div className="ws-thumb aspect-square overflow-hidden rounded-lg">
        {scene.preview ? (
          <img
            src={scene.preview}
            alt={scene.title}
            loading="lazy"
            className="h-full w-full object-cover [image-rendering:pixelated]"
          />
        ) : null}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {scene.audience === "developer" && (
          <span className="rounded-full bg-muted px-2 py-0.5 text-[11px]">
            {CATEGORY_LABELS[scene.category] ?? scene.category}
          </span>
        )}
        {selected && (
          <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] text-primary">
            已选
          </span>
        )}
      </div>
      <p className="mt-1 text-sm font-medium leading-5">
        {scene.acquired ?? scene.title}
      </p>
      {!compact && (
        <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
          {sceneFacts(scene)
            .filter((fact) => fact !== scene.acquired)
            .join(" · ")}
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

import type { FixtureOption } from "./types";
import { CATEGORY_LABELS } from "./types";

function Thumb({ src, caption }: { src?: string; caption: string }) {
  return (
    <figure className="min-w-0 flex-1">
      <div className="ws-thumb aspect-square overflow-hidden rounded-lg">
        {src ? (
          <img
            src={src}
            alt={caption}
            loading="lazy"
            className="h-full w-full object-cover [image-rendering:pixelated]"
          />
        ) : null}
      </div>
      <figcaption className="mt-1 text-center text-[11px] text-muted-foreground">
        {caption}
      </figcaption>
    </figure>
  );
}

export function datasetFacts(fixture: FixtureOption) {
  return [
    fixture.period,
    fixture.sensor,
    fixture.area_km2 ? "约 " + fixture.area_km2.toFixed(0) + " km²" : null,
  ].filter(Boolean) as string[];
}

export default function DatasetCard({
  fixture,
  selected = false,
  onSelect,
  actionLabel,
}: {
  fixture: FixtureOption;
  selected?: boolean;
  onSelect?: () => void;
  actionLabel?: string;
}) {
  const body = (
    <>
      <div className="flex gap-2">
        <Thumb src={fixture.previews?.before} caption="前期" />
        <Thumb src={fixture.previews?.after} caption="后期" />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="rounded-full bg-muted px-2 py-0.5 text-xs">
          {CATEGORY_LABELS[fixture.category] ?? fixture.category}
        </span>
        {selected && (
          <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">
            当前使用
          </span>
        )}
      </div>
      <p className="mt-2 text-sm font-medium leading-5">{fixture.title}</p>
      {fixture.place && (
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          {fixture.place}
        </p>
      )}
      <p className="mt-1 text-xs leading-5 text-muted-foreground">
        {datasetFacts(fixture).join(" · ")}
      </p>
      {actionLabel && (
        <span className="mt-2 inline-block text-xs text-primary underline">
          {actionLabel}
        </span>
      )}
    </>
  );
  const className =
    "ws-dataset block w-full rounded-2xl border bg-card p-3 text-left transition " +
    (selected ? "ws-dataset-selected" : "border-border");
  return onSelect ? (
    <button type="button" className={className} onClick={onSelect}>
      {body}
    </button>
  ) : (
    <div className={className}>{body}</div>
  );
}

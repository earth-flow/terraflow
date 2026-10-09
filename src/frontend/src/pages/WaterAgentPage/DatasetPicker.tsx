import { useState } from "react";
import DatasetCard, { datasetFacts } from "./DatasetCard";
import type { FixtureOption } from "./types";

export function visibleDatasets(
  fixtures: FixtureOption[],
  showDeveloper: boolean,
) {
  return fixtures.filter(
    (fixture) => showDeveloper || fixture.audience !== "developer",
  );
}

export default function DatasetPicker({
  fixtures,
  value,
  disabled,
  onChange,
}: {
  fixtures: FixtureOption[];
  value: string;
  disabled: boolean;
  onChange: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [showDeveloper, setShowDeveloper] = useState(false);
  const selected = fixtures.find((fixture) => fixture.id === value);
  const options = visibleDatasets(
    fixtures,
    showDeveloper || selected?.audience === "developer",
  );

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium">分析数据</span>
        <button
          type="button"
          className="text-xs text-primary underline disabled:opacity-50"
          disabled={disabled}
          onClick={() => setOpen((current) => !current)}
        >
          {open ? "收起" : "更换数据"}
        </button>
      </div>
      {selected ? (
        <div className="mt-2 flex gap-3 rounded-2xl border border-border bg-muted/20 p-3">
          <div className="flex w-36 shrink-0 gap-1.5">
            {(["before", "after"] as const).map((role) => (
              <div
                key={role}
                className="water-agent-thumb aspect-square min-w-0 flex-1 overflow-hidden rounded-md"
              >
                {selected.previews?.[role] && (
                  <img
                    src={selected.previews[role]}
                    alt={role === "before" ? "前期影像" : "后期影像"}
                    className="h-full w-full object-cover [image-rendering:pixelated]"
                  />
                )}
              </div>
            ))}
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium leading-5">{selected.title}</p>
            {selected.place && (
              <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
                {selected.place}
              </p>
            )}
            <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
              {datasetFacts(selected).join(" · ")}
            </p>
          </div>
        </div>
      ) : (
        <p className="mt-2 text-xs text-muted-foreground">
          还没有可用数据，请先在“数据”中上传。
        </p>
      )}
      {open && (
        <div className="mt-3 rounded-2xl border border-border p-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {options.map((fixture) => (
              <DatasetCard
                key={fixture.id}
                fixture={fixture}
                selected={fixture.id === value}
                onSelect={() => {
                  onChange(fixture.id);
                  setOpen(false);
                }}
              />
            ))}
          </div>
          <label className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={showDeveloper}
              onChange={(event) => setShowDeveloper(event.target.checked)}
            />
            显示开发者数据（合成测试栅格、测试上传）
          </label>
        </div>
      )}
    </div>
  );
}

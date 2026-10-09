import { useState } from "react";
import DatasetCard from "./DatasetCard";
import { visibleDatasets } from "./DatasetPicker";
import type { FixtureOption } from "./types";
import { sceneLine } from "./types";
import UploadPanel from "./UploadPanel";

export default function DataPanel({
  fixtures,
  busy,
  onUploaded,
  onUse,
}: {
  fixtures: FixtureOption[];
  busy: boolean;
  onUploaded: (fixtureId: string) => Promise<void>;
  onUse: (fixtureId: string) => void;
}) {
  const [showDeveloper, setShowDeveloper] = useState(false);
  const [detail, setDetail] = useState<string | null>(null);
  const shown = visibleDatasets(fixtures, showDeveloper);
  const opened = fixtures.find((fixture) => fixture.id === detail);

  return (
    <section className="space-y-4">
      <div className="rounded-2xl border border-border bg-card p-5">
        <h2 className="font-semibold">数据</h2>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          每组数据是同一地区的前期、后期两景影像。卫星观测数据记录了来源、成像时间和轨道；上传数据的来源和时间由上传者负责。
        </p>
        <UploadPanel disabled={busy} onUploaded={onUploaded} />
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {shown.map((fixture) => (
          <div key={fixture.id} className="flex flex-col gap-2">
            <DatasetCard fixture={fixture} />
            <div className="flex gap-3 px-1">
              <button
                type="button"
                className="text-xs text-primary underline"
                onClick={() => onUse(fixture.id)}
              >
                用这组数据对话
              </button>
              <button
                type="button"
                className="text-xs text-muted-foreground underline"
                onClick={() =>
                  setDetail(detail === fixture.id ? null : fixture.id)
                }
              >
                {detail === fixture.id ? "收起来源" : "查看来源"}
              </button>
            </div>
          </div>
        ))}
      </div>
      {opened && (
        <div className="rounded-2xl border border-border bg-card p-5 text-xs leading-5 text-muted-foreground">
          <p className="text-sm font-medium text-foreground">{opened.title}</p>
          <p className="mt-2">{opened.description}</p>
          {opened.event && <p>事件：{opened.event}</p>}
          {sceneLine("前期", opened.scenes?.before) && (
            <p>{sceneLine("前期", opened.scenes?.before)}</p>
          )}
          {sceneLine("后期", opened.scenes?.after) && (
            <p>{sceneLine("后期", opened.scenes?.after)}</p>
          )}
          {opened.scenes?.before?.item_id && (
            <p>前期 STAC item：{opened.scenes.before.item_id}</p>
          )}
          {opened.scenes?.after?.item_id && (
            <p>后期 STAC item：{opened.scenes.after.item_id}</p>
          )}
          {opened.grid?.crs && (
            <p>
              网格：{opened.grid.crs}，
              {opened.grid.shape && [...opened.grid.shape].reverse().join("×")}
              {opened.grid.resolution_m
                ? "，" + opened.grid.resolution_m + " m"
                : ""}
            </p>
          )}
          {opened.claim_boundary && <p>使用边界：{opened.claim_boundary}</p>}
        </div>
      )}
      <label className="flex items-center gap-2 px-1 text-xs text-muted-foreground">
        <input
          type="checkbox"
          checked={showDeveloper}
          onChange={(event) => setShowDeveloper(event.target.checked)}
        />
        显示开发者数据（合成测试栅格、测试上传）
      </label>
    </section>
  );
}

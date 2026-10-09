import type { FixtureOption } from "./types";
import { SOURCE_LABELS, sceneLine } from "./types";
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
  return (
    <section className="space-y-4">
      <div className="rounded-2xl border border-border bg-card p-5">
        <h2 className="font-semibold">数据</h2>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          输入组合是一对前后期栅格，可附带一个尺寸不一致的拒绝样本。真实观测带有
          STAC 来源、时相和轨道记录；上传数据的来源和时相由上传者负责。
        </p>
        <UploadPanel disabled={busy} onUploaded={onUploaded} />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {fixtures.map((fixture) => (
          <div
            key={fixture.id}
            className="flex flex-col rounded-2xl border border-border bg-card p-5"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full bg-muted px-2 py-0.5 text-xs">
                {SOURCE_LABELS[fixture.source] ?? fixture.source}
              </span>
              <span className="font-mono text-xs text-muted-foreground">
                {fixture.input_kind}
              </span>
              {fixture.has_mismatch && (
                <span className="rounded-full bg-muted px-2 py-0.5 text-xs">
                  含拒绝样本
                </span>
              )}
            </div>
            <p className="mt-3 font-medium">{fixture.label}</p>
            <div className="mt-2 flex-1 space-y-1 text-xs leading-5 text-muted-foreground">
              <p>{fixture.description}</p>
              {fixture.event && <p>事件：{fixture.event}</p>}
              {sceneLine("前期", fixture.scenes?.before) && (
                <p>{sceneLine("前期", fixture.scenes?.before)}</p>
              )}
              {sceneLine("后期", fixture.scenes?.after) && (
                <p>{sceneLine("后期", fixture.scenes?.after)}</p>
              )}
              {fixture.grid?.crs && (
                <p>
                  网格：{fixture.grid.crs}，
                  {fixture.grid.shape &&
                    [...fixture.grid.shape].reverse().join("×")}
                  {fixture.grid.resolution_m
                    ? "，" + fixture.grid.resolution_m + " m"
                    : ""}
                </p>
              )}
            </div>
            <button
              type="button"
              className="mt-3 self-start text-xs text-primary underline"
              onClick={() => onUse(fixture.id)}
            >
              用这组数据对话
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}

import { useState } from "react";
import { pickableScenes, sceneFacts } from "./api";
import SceneCard from "./SceneCard";
import type { Collection, Scene } from "./types";
import { CATEGORY_LABELS } from "./types";
import UploadPanel from "./UploadPanel";

function SceneDetail({ scene }: { scene: Scene }) {
  const source = scene.source ?? {};
  const grid = scene.grid ?? {};
  return (
    <div className="rounded-2xl border border-border bg-card p-4 text-xs leading-5 text-muted-foreground">
      <p className="text-sm font-medium text-foreground">
        {scene.collection_title} · {scene.title}
      </p>
      <p className="mt-1">{sceneFacts(scene).join(" · ")}</p>
      {scene.event && <p>事件：{scene.event}</p>}
      {source.datetime && (
        <p>
          成像时间：{source.datetime.slice(0, 16).replace("T", " ")} UTC
          {source.platform ? "，" + source.platform : ""}
          {source.polarization ? "，" + source.polarization : ""}
          {source.relative_orbit !== undefined
            ? "，相对轨道 " +
              source.relative_orbit +
              (source.orbit_state ? "（" + source.orbit_state + "）" : "")
            : ""}
        </p>
      )}
      {source.item_id && (
        <p className="break-all">
          STAC item：
          {source.stac_item_url ? (
            <a
              className="text-primary underline"
              href={source.stac_item_url}
              target="_blank"
              rel="noreferrer"
            >
              {source.item_id}
            </a>
          ) : (
            source.item_id
          )}
        </p>
      )}
      {scene.original_name && <p>上传文件：{scene.original_name}</p>}
      {grid.crs && (
        <p>
          网格：{grid.crs}
          {grid.shape ? "，" + [...grid.shape].reverse().join("×") : ""}
          {grid.resolution ? "，" + Math.abs(grid.resolution[0]) + " m" : ""}
        </p>
      )}
      {scene.self_check_for && (
        <p>用途：门控自检，故意与原影像不在同一网格，不用于分析。</p>
      )}
      {scene.claim_boundary && <p>使用边界：{scene.claim_boundary}</p>}
    </div>
  );
}

export default function DataPanel({
  scenes,
  collections,
  onUploaded,
  onUse,
}: {
  scenes: Scene[];
  collections: Collection[];
  onUploaded: () => Promise<void>;
  onUse: (sceneIds: string[]) => void;
}) {
  const [showDeveloper, setShowDeveloper] = useState(false);
  const [detail, setDetail] = useState<string | null>(null);
  // Self-check samples are listed with developer data, next to their scene.
  const shown = scenes.filter(
    (scene) => showDeveloper || scene.audience !== "developer",
  );
  const groups = collections
    .map((collection) => ({
      collection,
      items: shown.filter((scene) => scene.collection_id === collection.id),
    }))
    .filter((group) => group.items.length > 0);
  const opened = scenes.find((scene) => scene.id === detail);

  return (
    <section className="space-y-4">
      <div className="rounded-2xl border border-border bg-card p-5">
        <h2 className="font-semibold">数据</h2>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          数据按“景”管理：每景是一幅单波段影像，记录成像日期、传感器和数据类型，同一事件或同一次上传的几景归为一组。
          分析时由计划指定每景的用途，例如水体变化选两景作前期、后期，单景水体提取选一景。
        </p>
        <UploadPanel onUploaded={onUploaded} />
      </div>
      {groups.map(({ collection, items }) => {
        const usable = pickableScenes(items, showDeveloper);
        return (
          <div
            key={collection.id}
            className="rounded-2xl border border-border bg-card p-5"
          >
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <h3 className="font-medium">{collection.title}</h3>
              <span className="text-xs text-muted-foreground">
                {[
                  collection.place,
                  collection.period,
                  items.length + " 景",
                  CATEGORY_LABELS[collection.category] ?? collection.category,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
              {usable.length > 0 && (
                <button
                  type="button"
                  className="ml-auto text-xs text-primary underline"
                  onClick={() => onUse(usable.map((scene) => scene.id))}
                >
                  用这一组对话
                </button>
              )}
            </div>
            <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
              {items.map((scene) => (
                <div key={scene.id} className="flex flex-col gap-1.5">
                  <SceneCard scene={scene} />
                  <div className="flex flex-wrap gap-x-3 px-1">
                    {!scene.self_check_for && (
                      <button
                        type="button"
                        className="text-xs text-primary underline"
                        onClick={() => onUse([scene.id])}
                      >
                        用这景对话
                      </button>
                    )}
                    <button
                      type="button"
                      className="text-xs text-muted-foreground underline"
                      onClick={() =>
                        setDetail(detail === scene.id ? null : scene.id)
                      }
                    >
                      {detail === scene.id ? "收起来源" : "来源"}
                    </button>
                  </div>
                </div>
              ))}
            </div>
            {opened && opened.collection_id === collection.id && (
              <div className="mt-3">
                <SceneDetail scene={opened} />
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

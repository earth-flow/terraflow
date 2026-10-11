import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCatalog } from "./api";
import type { CatalogItem, ChatMessage, Layer } from "./types";
import { useWorkspace } from "./workspaceStore";

const TYPE_CLASS: Record<string, string> = {
  sar: "sar",
  optical: "opt",
  dem: "dem",
  classification: "cls",
  vector: "vec",
};
const TYPE_SHORT: Record<string, string> = {
  sar: "SAR",
  optical: "光学",
  dem: "DEM",
  classification: "分类",
  vector: "矢量",
};

const isMask = (layer: Layer) =>
  /水体|掩膜|mask/i.test(layer.label + layer.key) && !/影像/.test(layer.label);
const isBefore = (layer: Layer) => /前期|before/i.test(layer.label + layer.key);
const isAfter = (layer: Layer) => /后期|after/i.test(layer.label + layer.key);

function usableItems(items: CatalogItem[]) {
  return items.filter(
    (item) =>
      item.audience !== "developer" && !item.legacy && !item.self_check_for,
  );
}

/** Swipe between the before and after stacks (image plus water mask), or show one stack. */
function Stage({
  layers,
  visible,
  opacity,
  label,
}: {
  layers: Layer[];
  visible: Record<string, boolean>;
  opacity: number;
  label?: string;
}) {
  const [split, setSplit] = useState(50);
  const box = useRef<HTMLDivElement>(null);
  const shown = layers.filter((l) => visible[l.key] !== false);
  const before = shown.filter(isBefore);
  const after = shown.filter(isAfter);
  const swipe = before.length > 0 && after.length > 0;
  const stack = (list: Layer[]) =>
    [...list.filter((l) => !isMask(l)), ...list.filter(isMask)].map((l) => (
      <img
        key={l.key + "|" + l.url}
        src={l.url}
        alt={l.label}
        className={isMask(l) && list.some((x) => !isMask(x)) ? "ws-mask" : ""}
        style={
          isMask(l) && list.some((x) => !isMask(x)) ? { opacity } : undefined
        }
      />
    ));
  function drag(event: React.PointerEvent) {
    const rect = box.current?.getBoundingClientRect();
    if (!rect) return;
    const move = (e: PointerEvent) =>
      setSplit(
        Math.min(95, Math.max(5, ((e.clientX - rect.left) / rect.width) * 100)),
      );
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    event.preventDefault();
  }
  return (
    <div ref={box} className="ws-aoi">
      {swipe ? (
        <>
          <div
            className="ws-side-img"
            style={{ clipPath: `inset(0 ${100 - split}% 0 0)` }}
          >
            {stack(before)}
          </div>
          <div
            className="ws-side-img"
            style={{ clipPath: `inset(0 0 0 ${split}%)` }}
          >
            {stack(after)}
          </div>
          <div
            className="ws-split"
            style={{ left: split + "%" }}
            onPointerDown={drag}
          >
            <i>‹›</i>
          </div>
          <span
            className="ws-slab"
            style={{ right: `calc(${100 - split}% + 12px)` }}
          >
            前期
          </span>
          <span className="ws-slab" style={{ left: `calc(${split}% + 12px)` }}>
            后期
          </span>
        </>
      ) : (
        <div className="ws-side-img">{stack(shown)}</div>
      )}
      {label && (
        <span className="ws-slab" style={{ left: 12, top: swipe ? 44 : 12 }}>
          {label}
        </span>
      )}
    </div>
  );
}

export default function DataStage() {
  const navigate = useNavigate();
  const { items } = useCatalog();
  const { messages, resultFocus, selectedItem, selectItem, requestAttach } =
    useWorkspace();
  const usable = useMemo(() => usableItems(items), [items]);

  const result = useMemo(() => {
    const runs = messages.filter((m) => m.kind === "run" && m.result);
    return (runs.find((m) => m.id === resultFocus) ?? runs.at(-1)) as
      | ChatMessage
      | undefined;
  }, [messages, resultFocus]);
  const [tab, setTab] = useState<"catalog" | "layers">("catalog");
  const [showResult, setShowResult] = useState(true);
  useEffect(() => {
    if (result) {
      setTab("layers");
      setShowResult(true);
    }
  }, [result?.id]);
  const item = usable.find((i) => i.id === selectedItem);
  useEffect(() => {
    if (!selectedItem && usable.length)
      selectItem(
        (
          usable.find(
            (i) =>
              i.data_type === "sar" && /色萨利|Thessaly/.test(i.place ?? ""),
          ) ??
          usable.find((i) => i.data_type === "sar") ??
          usable[0]
        ).id,
      );
  }, [selectedItem, usable, selectItem]);

  const layers = showResult && result?.result ? result.result.layers : [];
  const [visible, setVisible] = useState<Record<string, boolean>>({});
  const [opacity, setOpacity] = useState(0.75);
  const plan = result
    ? messages.find(
        (m) =>
          m.id === (result as { plan_message_id?: string }).plan_message_id,
      )
    : undefined;
  const usedIds = new Set((plan?.plan?.inputs ?? []).map((i) => i.scene_id));

  // The place shown on the observation bar: the result's inputs, else the selected item.
  const place =
    usable.find((i) => usedIds.has(i.id))?.place ??
    item?.place ??
    usable[0]?.place ??
    null;
  const groups = useMemo(() => {
    const byPlace = new Map<string, CatalogItem[]>();
    for (const i of usable)
      byPlace.set(
        i.place ?? "其他",
        (byPlace.get(i.place ?? "其他") ?? []).concat(i),
      );
    return Array.from(byPlace.entries()).sort(([a], [b]) =>
      a === place ? -1 : b === place ? 1 : a.localeCompare(b),
    );
  }, [usable, place]);
  const observations = usable
    .filter(
      (i) =>
        i.place === place &&
        (i.data_type === "sar" || i.data_type === "optical") &&
        i.date,
    )
    .sort((a, b) => (a.date! < b.date! ? -1 : 1));
  const dates = observations.map((o) => Date.parse(o.date!));
  const t0 = Math.min(...dates) - 4 * 864e5;
  const t1 = Math.max(...dates) + 4 * 864e5;
  const x = (d: string) => ((Date.parse(d) - t0) / (t1 - t0)) * 100;

  return (
    <div className="ws-view">
      <section className="wv-stage ws-map">
        {layers.length ? (
          <Stage layers={layers} visible={visible} opacity={opacity} />
        ) : item ? (
          <Stage
            layers={[{ key: "item", label: item.title, url: item.preview }]}
            visible={{}}
            opacity={1}
            label={`预览：${item.collection_title} · ${item.date ?? ""}`}
          />
        ) : (
          <div className="ws-empty">从右侧数据目录选择数据</div>
        )}
        {layers.length > 0 && result?.result && (
          <div className="ws-headline">
            <span
              className={
                (result as { mode?: string }).mode === "assistant"
                  ? "ws-src-ai"
                  : "ws-src-tpl"
              }
            >
              {(result as { mode?: string }).mode === "assistant"
                ? "流程助手"
                : "已验证模板"}
            </span>
            {result.result.headline}
            <button type="button" onClick={() => setShowResult(false)}>
              只看数据
            </button>
          </div>
        )}
        {observations.length > 0 && (
          <div className="ws-obs">
            <div className="hd">
              <b>观测</b>
              <span>
                {place} · {observations.length} 期影像
              </span>
              <span className="lgd">
                <span>
                  <i className="mk used" />
                  {usedIds.size ? "本次使用" : "已选"}
                </span>
                <span>
                  <i className="mk" />
                  SAR
                </span>
                <span>
                  <i className="mk s2" />
                  光学
                </span>
              </span>
            </div>
            <div className="axis">
              <div className="line" />
              {observations.map((o, index) => {
                const used = usedIds.size
                  ? usedIds.has(o.id)
                  : o.id === selectedItem;
                return (
                  <button
                    type="button"
                    key={o.id}
                    className="o"
                    style={{ left: x(o.date!) + "%" }}
                    title={`${o.collection_title} · ${o.date}`}
                    onClick={() => {
                      setShowResult(false);
                      selectItem(o.id);
                    }}
                  >
                    <i
                      className={
                        "mk" +
                        (o.data_type === "optical" ? " s2" : "") +
                        (used ? " used" : "")
                      }
                    />
                    {used && <em>{o.date!.slice(5)}</em>}
                    {(index === 0 ||
                      x(o.date!) - x(observations[index - 1].date!) > 7) && (
                      <span className="dl">{o.date!.slice(5)}</span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </section>
      <aside className="ws-side">
        <div
          className="ws-tabs"
          style={
            {
              "--n": 2,
              "--i": tab === "catalog" ? 0 : 1,
            } as React.CSSProperties
          }
        >
          <button
            type="button"
            className={tab === "catalog" ? "on" : ""}
            onClick={() => setTab("catalog")}
          >
            数据目录
          </button>
          <button
            type="button"
            className={tab === "layers" ? "on" : ""}
            onClick={() => setTab("layers")}
          >
            图层{result ? "" : "（无结果）"}
          </button>
        </div>
        {tab === "catalog" ? (
          <div className="ws-catalog">
            {groups.map(([group, list]) => (
              <details key={group} open={group === place}>
                <summary>
                  {group}
                  <span>{list.length} 份</span>
                </summary>
                {list.map((i) => (
                  <div
                    key={i.id}
                    className={"row" + (i.id === selectedItem ? " sel" : "")}
                  >
                    <button
                      type="button"
                      className="main"
                      onClick={() => {
                        setShowResult(false);
                        selectItem(i.id);
                      }}
                    >
                      <img src={i.preview} alt="" />
                      <span className="nm">
                        {i.collection_title.replace(/^.*? · /, "")}
                        <small>{i.date ?? "—"}</small>
                      </span>
                      <span
                        className={"ty " + (TYPE_CLASS[i.data_type] ?? "vec")}
                      >
                        {TYPE_SHORT[i.data_type] ?? i.type_label}
                      </span>
                    </button>
                    {i.id === selectedItem && (
                      <div className="acts">
                        <button
                          type="button"
                          onClick={() => requestAttach([i.id])}
                        >
                          用于提问
                        </button>
                        <span>{i.facts.slice(1, 3).join(" · ")}</span>
                      </div>
                    )}
                  </div>
                ))}
              </details>
            ))}
            <button
              type="button"
              className="ws-upload"
              onClick={() => navigate("/workspace/data/manage")}
            >
              ＋ 上传与管理数据
            </button>
          </div>
        ) : result?.result ? (
          <div className="ws-layers">
            {result.result.layers.map((l) => (
              <label key={l.key} className="ly">
                <img src={l.url} alt="" />
                <span className="nm">
                  {l.label}
                  <small>{isMask(l) ? "结果" : "输入"}</small>
                </span>
                <input
                  type="checkbox"
                  checked={visible[l.key] !== false}
                  onChange={(e) => {
                    setShowResult(true);
                    setVisible((v) => ({ ...v, [l.key]: e.target.checked }));
                  }}
                />
              </label>
            ))}
            {result.result.layers.some(isMask) &&
              result.result.layers.some((l) => !isMask(l)) && (
                <div className="op">
                  水体透明度
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={Math.round(opacity * 100)}
                    onChange={(e) => setOpacity(Number(e.target.value) / 100)}
                  />
                  {Math.round(opacity * 100)}%
                </div>
              )}
            <p className="hint">拖动地图中间的竖线对比前后两期。</p>
          </div>
        ) : (
          <p className="hint p-3">还没有运行结果。</p>
        )}
      </aside>
    </div>
  );
}

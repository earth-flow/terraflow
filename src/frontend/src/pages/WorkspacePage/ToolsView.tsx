import { useEffect, useMemo, useState } from "react";
import { API, apiJson } from "./api";
import { GENERIC_TOOLKITS, TOOLKIT_CN, toolName } from "./labels";
import { useWorkspace } from "./workspaceStore";

type Toolkit = {
  slug: string;
  name: string;
  status: string;
  description?: string;
};
type Tool = {
  slug: string;
  name: string;
  toolkit: string;
  status: string;
  description?: string;
  scenarios: string[];
};
type Preset = {
  tool?: string;
  preset?: string;
  params?: Record<string, unknown>;
  transform_by_scale?: Record<string, string>;
  otsu_range?: number[];
  units?: string;
  rule?: string;
};
type ToolsBody = {
  toolkits: Toolkit[];
  tools: Tool[];
  presets: Record<string, Record<string, Preset | null>>;
};

const INPUT_CN: Record<string, string> = {
  sar_backscatter: "SAR 后向散射",
  water_labels: "0/1 水体标签",
  optical_reflectance: "光学反射率",
};

export default function ToolsView() {
  const { selectedTool, selectTool, requestPrefill, actions, messages } =
    useWorkspace();
  const [body, setBody] = useState<ToolsBody | null>(null);
  const [kit, setKit] = useState("geo_water");
  const [query, setQuery] = useState("");
  useEffect(() => {
    void apiJson<ToolsBody>(API + "/tools")
      .then(setBody)
      .catch(() => undefined);
  }, []);
  const tools = body?.tools ?? [];
  const kits = useMemo(
    () =>
      (body?.toolkits ?? [])
        .map((k) => ({
          ...k,
          count: tools.filter((t) => t.toolkit === k.slug).length,
          used: tools.filter((t) => t.toolkit === k.slug && t.scenarios.length)
            .length,
        }))
        .sort((a, b) =>
          GENERIC_TOOLKITS.has(a.slug) !== GENERIC_TOOLKITS.has(b.slug)
            ? GENERIC_TOOLKITS.has(a.slug)
              ? 1
              : -1
            : b.used - a.used || b.count - a.count,
        ),
    [body, tools],
  );
  const q = query.trim().toLowerCase();
  const list = q
    ? tools.filter((t) =>
        (t.slug + t.name + (t.description ?? "") + toolName(t.slug))
          .toLowerCase()
          .includes(q),
      )
    : tools.filter((t) => t.toolkit === kit);
  const tool = tools.find((t) => t.slug === selectedTool) ?? list[0];
  const presets = Object.entries(body?.presets ?? {}).flatMap(
    ([input, entry]) =>
      Object.values(entry ?? {})
        .filter((p): p is Preset => Boolean(p && p.tool === tool?.slug))
        .map((p) => [input, p] as const),
  );
  const hasFlow = messages.some((m) => m.kind === "flow");

  return (
    <div className="ws-view">
      <section className="wv-stage ws-panel-stage">
        <div className="ws-tools-h">
          <b>工具</b>
          <span>
            Terrabox {kits.length} 个工具集 · {tools.length} 个工具 ·
            每个工具也是工作流里的一个节点
          </span>
        </div>
        <input
          className="ws-search"
          placeholder="按名称或用途搜索，如“阈值”“NDWI”“面积”"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {!q && (
          <div className="ws-kits">
            {kits.map((k) => (
              <button
                type="button"
                key={k.slug}
                className={
                  "kit" +
                  (k.slug === kit ? " sel" : "") +
                  (GENERIC_TOOLKITS.has(k.slug) ? " generic" : "")
                }
                onClick={() => {
                  setKit(k.slug);
                  selectTool(null);
                }}
                title={k.description}
              >
                <b>{TOOLKIT_CN[k.slug] ?? k.name}</b>
                <span>
                  {k.slug} · {k.count}
                </span>
                {k.used > 0 && <em>场景用到 {k.used}</em>}
              </button>
            ))}
          </div>
        )}
        <div className="ws-tlist">
          <p className="hd2">
            {q ? `搜索“${query}”` : `${TOOLKIT_CN[kit] ?? kit}（${kit}）`}
            <span>{list.length} 个工具</span>
          </p>
          {list.map((t) => (
            <button
              type="button"
              key={t.slug}
              className={"trow" + (t.slug === tool?.slug ? " sel" : "")}
              onClick={() => selectTool(t.slug)}
            >
              <span>
                <b>
                  {toolName(t.slug) !== t.slug.split(".").at(-1)
                    ? toolName(t.slug)
                    : t.name}
                </b>
                <code>{t.slug}</code>
              </span>
              <span className="d">{t.description}</span>
              <span className="sc2">
                {t.scenarios.slice(0, 3).map((s) => (
                  <i key={s}>{s}</i>
                ))}
              </span>
            </button>
          ))}
        </div>
      </section>
      <aside className="ws-side">
        {tool ? (
          <div className="ws-det">
            <p className="dh">
              {toolName(tool.slug) !== tool.slug.split(".").at(-1)
                ? toolName(tool.slug)
                : tool.name}
            </p>
            <code>{tool.slug}</code>
            <p className="txt">{tool.description}</p>
            {presets.map(([input, p]) => (
              <div key={input}>
                <p className="sec">参数预设 · {INPUT_CN[input] ?? input}</p>
                <div className="kvs">
                  {Object.entries(p.params ?? {}).map(([k, v]) => (
                    <div key={k}>
                      {k}
                      <b>{String(v)}</b>
                    </div>
                  ))}
                  {p.otsu_range && (
                    <div>
                      otsu_range<b>[{p.otsu_range.join(", ")}]</b>
                    </div>
                  )}
                  {p.transform_by_scale && (
                    <div>
                      变换
                      <b>
                        {Object.entries(p.transform_by_scale)
                          .map(([a, b]) => `${a}→${b}`)
                          .join("，")}
                      </b>
                    </div>
                  )}
                </div>
                {p.rule && <p className="txt">{p.rule}</p>}
              </div>
            ))}
            <p className="sec">用于已验证场景</p>
            <p className="txt">
              {tool.scenarios.length
                ? tool.scenarios.join("、")
                : "暂未用于已验证场景，可由流程助手在新工作流中使用。"}
            </p>
            <div className="btns">
              <button
                type="button"
                className="p"
                onClick={() =>
                  hasFlow
                    ? actions.send?.(`在当前工作流中加入 ${tool.slug} 工具`, {
                        assistant: true,
                      })
                    : requestPrefill(`搭建一个工作流：用 ${tool.slug} `)
                }
              >
                {hasFlow ? "加入当前工作流" : "用它搭工作流"}
              </button>
              <button
                type="button"
                onClick={() =>
                  requestPrefill(
                    `${toolName(tool.slug)}（${tool.slug}）怎么用？`,
                  )
                }
              >
                在对话中提问
              </button>
            </div>
          </div>
        ) : (
          <p className="hint p-3">正在读取工具…</p>
        )}
      </aside>
    </div>
  );
}

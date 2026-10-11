import { useEffect, useMemo, useState } from "react";
import { API, apiJson } from "./api";
import {
  COMPONENT_CN,
  GATE_TOOLS,
  keyOutput,
  PARAM_CN,
  toolName,
} from "./labels";
import type { ChatMessage, FlowGraph, Scenario, StepEvent } from "./types";
import { type Live, useWorkspace } from "./workspaceStore";

type NodeKind = "input" | "tool" | "gate" | "custom";
type VNode = {
  id: string;
  label: string;
  type: string;
  kind: NodeKind;
  rows: [string, string][];
  /** One mono line under the title (template steps: the tool slug). */
  sub?: string;
  params: Record<string, unknown>;
  state?: StepEvent["state"] | "pending";
  outputs?: Record<string, unknown>;
};
type VEdge = { source: string; target: string; label?: string };

// Top-down layout: one row per dependency depth.
const NODE_W = 210;
const COL_W = 236;
const ROW_GAP = 46;

function short(value: unknown) {
  const text = Array.isArray(value)
    ? "[" + value.join(", ") + "]"
    : String(value);
  const path = text.includes("/") ? text.split("/").at(-1)! : text;
  return path.length > 28 ? path.slice(0, 27) + "…" : path;
}

/** Nodes and edges of an assistant-built flow; field-extraction nodes become edge labels. */
function fromGraph(graph: FlowGraph): { nodes: VNode[]; edges: VEdge[] } {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const hidden = new Set(
    graph.nodes
      .filter((n) => n.type === "ToolResultField" || n.type === "ChatOutput")
      .map((n) => n.id),
  );
  const edges: VEdge[] = [];
  for (const edge of graph.edges) {
    if (hidden.has(edge.target)) continue;
    if (!hidden.has(edge.source)) {
      edges.push({ source: edge.source, target: edge.target });
      continue;
    }
    const field = byId.get(edge.source);
    if (field?.type !== "ToolResultField") continue;
    const into = graph.edges.find((e) => e.target === field.id);
    if (!into || hidden.has(into.source)) continue;
    const path = String(field.params.field_path ?? "");
    edges.push({
      source: into.source,
      target: edge.target,
      label: path.endsWith("threshold")
        ? "阈值"
        : path.endsWith("output_path")
          ? "输出栅格"
          : path,
    });
  }
  // A threshold step fed from the pair's before/after path is that date's step.
  const date = (id: string) => {
    const link = graph.edges.find(
      (e) => e.target === id && byId.get(e.source)?.type === "WaterInputSet",
    );
    return link?.output === "before_path"
      ? " · 前期"
      : link?.output === "after_path"
        ? " · 后期"
        : "";
  };
  for (const edge of edges)
    if (!edge.label && byId.get(edge.source)?.type === "WaterInputSet") {
      const link = graph.edges.find(
        (e) => e.source === edge.source && e.target === edge.target,
      );
      if (link?.output)
        edge.label =
          link.output === "before_path"
            ? "前期"
            : link.output === "after_path"
              ? "后期"
              : "";
    }
  const nodes = graph.nodes
    .filter((n) => !hidden.has(n.id))
    .map((n): VNode => {
      const kind: NodeKind = n.custom
        ? "custom"
        : n.type === "SceneInput" || n.type === "WaterInputSet"
          ? "input"
          : GATE_TOOLS.has(n.type)
            ? "gate"
            : "tool";
      const base = COMPONENT_CN[n.type] ?? n.label;
      return {
        id: n.id,
        type: n.type,
        kind,
        label:
          n.type === "TerraboxGeoRasterThresholdSegmentation"
            ? base + date(n.id)
            : base,
        params: n.params,
        rows: Object.entries(n.params)
          .filter(([key]) => key !== "output_path" && key !== "field_path")
          .slice(0, 3)
          .map(([key, value]) => [PARAM_CN[key] ?? key, short(value)]),
      };
    });
  return { nodes, edges };
}

/** A template plan: its input slots, then the tool chain in execution order. */
function fromPlan(message: ChatMessage): { nodes: VNode[]; edges: VEdge[] } {
  const plan = message.plan!;
  const inputs: VNode[] = (plan.inputs ?? []).map((input) => ({
    id: "in:" + input.role,
    type: "input",
    kind: "input",
    label: input.label,
    params: {},
    rows: [
      ["数据", input.collection_title.replace(/ · .*/, "")],
      ["日期", input.acquired ?? input.scene_title],
    ],
  }));
  const tools: VNode[] = plan.execution_order.map((slug, index) => ({
    id: "t" + index,
    type: slug,
    kind: GATE_TOOLS.has(slug.split(".").at(-1) ?? "") ? "gate" : "tool",
    label: toolName(slug),
    params: {},
    rows: [],
    sub: slug,
  }));
  const edges: VEdge[] = [];
  for (const input of inputs)
    if (tools[0]) edges.push({ source: input.id, target: tools[0].id });
  tools
    .slice(1)
    .forEach((tool, index) =>
      edges.push({ source: tools[index].id, target: tool.id }),
    );
  return { nodes: inputs.concat(tools), edges };
}

function layout(nodes: VNode[], edges: VEdge[]) {
  const depth = new Map<string, number>();
  const visit = (id: string, seen: Set<string>): number => {
    if (depth.has(id)) return depth.get(id)!;
    if (seen.has(id)) return 0;
    seen.add(id);
    const preds = edges
      .filter((e) => e.target === id)
      .map((e) => visit(e.source, seen) + 1);
    const d = preds.length ? Math.max(...preds) : 0;
    depth.set(id, d);
    return d;
  };
  nodes.forEach((n) => visit(n.id, new Set()));
  const rows = new Map<number, VNode[]>();
  nodes.forEach((n) =>
    rows.set(depth.get(n.id)!, (rows.get(depth.get(n.id)!) ?? []).concat(n)),
  );
  const widest = Math.max(1, ...Array.from(rows.values()).map((r) => r.length));
  const width = Math.max(48 + widest * COL_W, 640);
  const pos = new Map<string, { x: number; y: number; h: number }>();
  let top = 12;
  Array.from(rows.keys())
    .sort((a, b) => a - b)
    .forEach((d) => {
      const row = rows.get(d)!;
      row.forEach((n, i) =>
        pos.set(n.id, {
          x:
            width / 2 -
            (row.length * COL_W) / 2 +
            i * COL_W +
            (COL_W - NODE_W) / 2,
          y: top,
          h: nodeHeight(n),
        }),
      );
      top += Math.max(...row.map(nodeHeight)) + ROW_GAP;
    });
  return { pos, width, height: top + 60 };
}

/** Rendered height of a node card (header, optional slug line, parameter rows, footer). */
function nodeHeight(n: VNode) {
  return (
    34 +
    (n.sub ? 16 : 0) +
    n.rows.length * 17 +
    8 +
    (keyOutput(n.outputs) ? 27 : 0)
  );
}

/** The conversation's current workflow: the latest plan or assistant flow, its run and live state. */
function useCurrentFlow(
  messages: ChatMessage[],
  draft: Live | null,
  live: Live | null,
) {
  return useMemo(() => {
    const source = [...messages]
      .reverse()
      .find((m) => m.kind === "flow" || (m.kind === "plan" && m.plan));
    const building = draft && draft.graph ? draft : null;
    const run = source
      ? [...messages]
          .reverse()
          .find(
            (m) =>
              m.kind === "run" &&
              (m as { plan_message_id?: string }).plan_message_id === source.id,
          )
      : undefined;
    const running = live && source && live.planId === source.id ? live : null;
    return { source, building, run, running };
  }, [messages, draft, live]);
}

export default function FlowView() {
  const {
    messages,
    draft,
    live,
    actions,
    selectedNode,
    selectNode,
    setView,
    focusResult,
    requestPrefill,
  } = useWorkspace();
  const { source, building, run, running } = useCurrentFlow(
    messages,
    draft,
    live,
  );
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  useEffect(() => {
    void apiJson<{ scenarios: Scenario[] }>(API + "/scenarios")
      .then((body) => setScenarios(body.scenarios))
      .catch(() => undefined);
  }, []);

  const graphSource =
    building?.graph ?? (source?.kind === "flow" ? source.graph : undefined);
  const { nodes, edges } = useMemo(() => {
    if (graphSource) return fromGraph(graphSource);
    if (source?.kind === "plan") return fromPlan(source);
    return { nodes: [] as VNode[], edges: [] as VEdge[] };
  }, [graphSource, source]);

  // Node state: live steps while running, the stored run afterwards.
  const steps: StepEvent[] = running?.steps ?? [];
  const inline = (run?.result?.steps_inline ?? []) as {
    node_id?: string;
    outputs?: Record<string, unknown>;
    success?: boolean;
    execution_blocked?: boolean;
  }[];
  const viewNodes = nodes.map((n, index) => {
    if (source?.kind === "plan" && n.kind !== "input") {
      const analysis = steps.filter((s) => s.chain !== "self_check");
      const tIndex = Number(n.id.slice(1));
      const step = analysis[tIndex];
      if (step) return { ...n, state: step.state, outputs: step.outputs };
      if (running) return { ...n, state: "pending" as const };
      if (run || source.status === "done")
        return { ...n, state: "done" as const };
      return n;
    }
    const step = steps.find(
      (s) => (s as StepEvent & { node?: string }).node === n.id,
    );
    if (step) return { ...n, state: step.state, outputs: step.outputs };
    const stored = inline.find((s) => s.node_id === n.id);
    if (stored)
      return {
        ...n,
        state: stored.execution_blocked
          ? ("blocked" as const)
          : ("done" as const),
        outputs: stored.outputs,
      };
    if (running) return { ...n, state: "pending" as const };
    return index >= 0 ? n : n;
  });
  const { pos, width, height } = layout(viewNodes, edges);
  const chosen = viewNodes.find((n) => n.id === selectedNode);
  const fromAssistant = Boolean(building || source?.kind === "flow");
  const title = building
    ? "流程助手正在搭建工作流"
    : source?.kind === "plan"
      ? source.plan!.scenario_title + "（已验证模板）"
      : source?.kind === "flow"
        ? "流程助手搭建的工作流"
        : "工作流";

  function runCurrent() {
    if (!source) return;
    if (source.kind === "flow") actions.runFlow?.(source.id);
    else if (source.status === "pending" || !source.status)
      actions.execute?.(source.id);
  }
  const canRun =
    source &&
    !building &&
    !running &&
    (source.kind === "flow" || source.status === "pending" || !source.status);

  return (
    <div className="ws-view">
      <section className="wv-stage ws-canvas">
        <div className="ws-fbar">
          <b className="truncate">{title}</b>
          {source || building ? (
            <span className={fromAssistant ? "ws-src-ai" : "ws-src-tpl"}>
              {fromAssistant ? "流程助手" : "已验证模板"}
            </span>
          ) : null}
          {source?.kind === "flow" &&
            source.checks?.length === 0 &&
            !building && <span className="ws-src-tpl">接线检查通过</span>}
          <div className="ml-auto flex shrink-0 items-center gap-1.5">
            {source?.kind === "flow" && source.flow_id && (
              <a
                className="ws-ghost-btn rounded-lg px-2.5 py-1 text-xs"
                href={"/flow/" + source.flow_id}
                target="_blank"
                rel="noreferrer"
              >
                在画布中编辑
              </a>
            )}
            <button
              type="button"
              className="ws-primary-btn rounded-lg px-3 py-1 text-xs"
              disabled={!canRun}
              onClick={runCurrent}
            >
              ▶ 运行
            </button>
          </div>
        </div>
        {viewNodes.length ? (
          <div className="ws-graph" style={{ width, height }}>
            <svg className="ws-edges" width={width} height={height}>
              {edges.map((e) => {
                const a = pos.get(e.source);
                const b = pos.get(e.target);
                if (!a || !b) return null;
                const x1 = a.x + NODE_W / 2;
                const y1 = a.y + a.h;
                const x2 = b.x + NODE_W / 2;
                const y2 = b.y;
                const active =
                  viewNodes.find((n) => n.id === e.target)?.state === "done";
                return (
                  <path
                    key={e.source + e.target + (e.label ?? "")}
                    className={active ? "on" : building ? "pend" : ""}
                    d={`M${x1},${y1} C${x1},${y1 + 36} ${x2},${y2 - 36} ${x2},${y2}`}
                  />
                );
              })}
            </svg>
            {edges.map((e) => {
              const a = pos.get(e.source);
              const b = pos.get(e.target);
              if (!a || !b || !e.label) return null;
              return (
                <span
                  key={"l" + e.source + e.target + e.label}
                  className="ws-elab"
                  style={{
                    left: (a.x + b.x + NODE_W) / 2,
                    top: (a.y + a.h + b.y) / 2,
                  }}
                >
                  {e.label}
                </span>
              );
            })}
            {viewNodes.map((n) => {
              const p = pos.get(n.id)!;
              const key = keyOutput(n.outputs);
              return (
                <button
                  type="button"
                  key={n.id}
                  className={`ws-node ws-node-${n.kind} ${n.state ? "is-" + n.state : ""} ${selectedNode === n.id ? "is-sel" : ""}`}
                  style={{ left: p.x, top: p.y, width: NODE_W }}
                  onClick={() =>
                    selectNode(selectedNode === n.id ? null : n.id)
                  }
                >
                  <span className="nh">
                    <i>
                      {n.kind === "input"
                        ? "D"
                        : n.kind === "gate"
                          ? "✓"
                          : n.kind === "custom"
                            ? "!"
                            : "T"}
                    </i>
                    <b>{n.label}</b>
                    {n.kind === "gate" && <em>门控</em>}
                    {n.state && n.state !== "pending" && (
                      <span className={"st st-" + n.state}>
                        {n.state === "done"
                          ? "✓"
                          : n.state === "running"
                            ? ""
                            : "!"}
                      </span>
                    )}
                  </span>
                  <span className="nb">
                    {n.sub && <code className="sub">{n.sub}</code>}
                    {n.rows.map(([k, v]) => (
                      <span key={k}>
                        {k}
                        <b>{v}</b>
                      </span>
                    ))}
                  </span>
                  {key && (
                    <span className="nf">
                      {key[0]}
                      <b>{key[1]}</b>
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        ) : draft ? (
          <div className="ws-building">
            <p className="t">
              <span className="ws-spinner" />
              正在生成工作流
            </p>
            <ol>
              {(draft.log ?? [draft.status]).map((line, i, all) => (
                <li
                  key={line.replace(/….*$/, "") + i}
                  className={i === all.length - 1 ? "now" : ""}
                >
                  {line}
                </li>
              ))}
            </ol>
            <p className="hint">节点会在流程助手生成工作流后出现在这里。</p>
          </div>
        ) : (
          <div className="ws-empty">
            <p className="text-base font-semibold">还没有工作流</p>
            <p className="mt-1 text-sm text-muted-foreground">
              在左侧提问：已验证的场景会按模板生成工作流；“搭建一个工作流：……”交给流程助手。也可以从右侧模板开始。
            </p>
          </div>
        )}
        {(building || running || run) && (
          <div className="ws-runbar">
            {building || running ? (
              <>
                <span className="ws-spinner" />
                <span className="truncate">
                  {(building ?? running)!.status || "处理中…"}
                </span>
              </>
            ) : run?.result ? (
              <>
                <span
                  className={
                    run.result.status === "success"
                      ? "ws-ok"
                      : "text-destructive"
                  }
                >
                  {run.result.status === "success" ? "✓ 运行完成" : "已拦截"}
                </span>
                <span className="truncate">{run.result.headline}</span>
                <button
                  type="button"
                  className="ws-primary-btn ml-auto shrink-0 rounded-lg px-3 py-1 text-xs"
                  onClick={() => {
                    focusResult(run.id);
                    setView("data");
                  }}
                >
                  在数据视图查看 →
                </button>
              </>
            ) : null}
          </div>
        )}
      </section>
      <aside className="ws-side">
        {chosen ? (
          <div className="ws-det" key={chosen.id}>
            <p className="dh">{chosen.label}</p>
            <code>{chosen.type}</code>
            {Object.keys(chosen.params).length > 0 && (
              <>
                <p className="sec">参数</p>
                <div className="kvs">
                  {Object.entries(chosen.params).map(([k, v]) => (
                    <div key={k}>
                      {PARAM_CN[k] ?? k}
                      <b title={String(v)}>{short(v)}</b>
                    </div>
                  ))}
                </div>
              </>
            )}
            {edges.some((e) => e.target === chosen.id) && (
              <>
                <p className="sec">输入来自</p>
                <div className="kvs">
                  {edges
                    .filter((e) => e.target === chosen.id)
                    .map((e) => (
                      <div key={e.source + (e.label ?? "")}>
                        {viewNodes.find((n) => n.id === e.source)?.label}
                        <b>{e.label ?? "结果"}</b>
                      </div>
                    ))}
                </div>
              </>
            )}
            {chosen.outputs && (
              <>
                <p className="sec">本次运行</p>
                <div className="kvs">
                  {Object.entries(chosen.outputs)
                    .filter(
                      ([, v]) =>
                        typeof v === "number" ||
                        typeof v === "string" ||
                        typeof v === "boolean",
                    )
                    .slice(0, 10)
                    .map(([k, v]) => (
                      <div key={k}>
                        {k}
                        <b title={String(v)}>
                          {typeof v === "number"
                            ? Number(v.toFixed(4))
                            : short(v)}
                        </b>
                      </div>
                    ))}
                </div>
              </>
            )}
            <div className="btns">
              <button type="button" onClick={() => selectNode(null)}>
                返回
              </button>
              {fromAssistant && (
                <button
                  type="button"
                  onClick={() => requestPrefill(`把“${chosen.label}”的`)}
                >
                  让助手修改
                </button>
              )}
            </div>
          </div>
        ) : (
          <div className="ws-det">
            <p className="dh">工作流</p>
            {source || building ? (
              <>
                <p className="sec">来源</p>
                <p className="txt">
                  {fromAssistant
                    ? `流程助手搭建（${source?.model ?? "qwen3.8-flash"}）。不是已验证模板，运行前做接线检查和试运行；结果只经过 Terrabox 工具自身的检查。`
                    : "已验证模板：按场景规则选择数据和工具顺序，执行时经过科学门控和场景规则校验。"}
                </p>
                {source?.kind === "flow" && (
                  <div className="kvs">
                    <div>
                      节点<b>{viewNodes.length}</b>
                    </div>
                    <div>
                      搭建用时<b>{source.seconds ?? "—"} s</b>
                    </div>
                    <div>
                      自写组件
                      <b>
                        {source.custom_components?.length
                          ? source.custom_components.join("、")
                          : "无"}
                      </b>
                    </div>
                  </div>
                )}
                {source?.checks?.map((note) => (
                  <p
                    key={note}
                    className="ws-warning mt-2 rounded-lg px-2 py-1 text-xs"
                  >
                    运行前检查：{note}
                  </p>
                ))}
                <p className="txt mt-2 text-muted-foreground">
                  点击节点查看参数和运行数值。
                </p>
              </>
            ) : (
              <p className="txt">当前对话还没有工作流。</p>
            )}
            <p className="sec">已验证模板</p>
            <div className="lib">
              {scenarios.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => requestPrefill(s.examples[0] ?? s.title)}
                  title={s.summary}
                >
                  <span className="ws-src-tpl">模板</span>
                  {s.title}
                </button>
              ))}
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}

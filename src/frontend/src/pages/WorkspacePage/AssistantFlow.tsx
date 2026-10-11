import { Button } from "@/components/ui/button";
import type { ChatMessage, FlowGraph } from "./types";

const PARAM_LABELS: Record<string, string> = {
  scene_id: "数据",
  input_set: "成对数据",
  method: "方法",
  threshold: "阈值",
  direction: "方向",
  input_transform: "变换",
  otsu_range: "Otsu 范围",
  index_name: "指数",
  sensor: "传感器",
  field_path: "字段",
  output_path: "输出",
  stat: "统计",
};

/** The workflow the flow assistant built, as nodes in dependency order. */
export function FlowGraphView({ graph }: { graph: FlowGraph }) {
  if (!graph.nodes.length)
    return <p className="text-xs text-muted-foreground">工作流还是空的。</p>;
  const position = new Map(graph.nodes.map((node, index) => [node.id, index]));
  return (
    <ol className="flex flex-wrap items-stretch gap-2">
      {graph.nodes.map((node, index) => {
        const inputs = graph.edges.filter((edge) => edge.target === node.id);
        return (
          <li key={node.id} className="flex min-w-0 items-center gap-2">
            {index > 0 && (
              <span aria-hidden className="text-muted-foreground">
                →
              </span>
            )}
            <div
              className={
                "min-w-0 max-w-[15rem] rounded-xl border px-2.5 py-1.5 text-xs leading-5 " +
                (node.custom
                  ? "ws-flow-node-custom"
                  : node.terrabox
                    ? "ws-flow-node-tool"
                    : "border-border bg-background")
              }
            >
              <p className="font-medium">
                {index + 1}. {node.label}
              </p>
              <p className="truncate text-[11px] text-muted-foreground">
                {node.custom
                  ? "流程助手自写的组件"
                  : node.terrabox
                    ? "Terrabox 工具"
                    : node.type}
              </p>
              {Object.entries(node.params).map(([key, value]) => (
                <p
                  key={key}
                  className="truncate text-[11px]"
                  title={String(value)}
                >
                  {(PARAM_LABELS[key] ?? key) + "：" + String(value)}
                </p>
              ))}
              {inputs.length > 0 && (
                <p className="truncate text-[11px] text-muted-foreground">
                  输入自{" "}
                  {inputs
                    .map((edge) => "#" + ((position.get(edge.source) ?? 0) + 1))
                    .join("、")}
                </p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function FlowCard({
  message,
  busy,
  running,
  onRun,
}: {
  message: ChatMessage;
  busy: boolean;
  running: boolean;
  onRun: () => void;
}) {
  const graph = message.graph ?? { nodes: [], edges: [] };
  const checks = message.checks ?? [];
  return (
    <div className="ws-plan rounded-2xl border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">工作流 · 流程助手</span>
        <span className="rounded-full bg-muted px-2 py-0.5 text-xs">
          {graph.nodes.length} 个节点 · {graph.edges.length} 条连线
        </span>
        <span className="text-xs text-muted-foreground">
          {[
            message.model,
            message.seconds !== undefined ? message.seconds + " s" : null,
            message.usage?.input_tokens
              ? "输入 " +
                Math.round(message.usage.input_tokens / 1000) +
                "k token"
              : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </div>
      <div className="mt-3">
        <FlowGraphView graph={graph} />
      </div>
      {checks.map((note) => (
        <p key={note} className="ws-warning mt-2 rounded-lg px-2 py-1 text-xs">
          运行前检查：{note}
        </p>
      ))}
      {message.error && (
        <p className="mt-2 text-xs text-destructive">{message.error}</p>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button
          type="button"
          disabled={busy || !graph.nodes.length}
          onClick={onRun}
        >
          {running ? "执行中…" : "运行工作流"}
        </Button>
        {message.flow_id && (
          <a
            className="text-xs text-primary underline"
            href={"/flow/" + message.flow_id}
            target="_blank"
            rel="noreferrer"
          >
            在 Canvas 中打开
          </a>
        )}
        <span className="text-xs text-muted-foreground">
          继续用“搭工作流”模式说明要改什么，助手会在这个工作流上修改。
        </span>
      </div>
    </div>
  );
}

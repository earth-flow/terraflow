import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { apiFetch } from "./api";
import type { FixtureOption } from "./types";
import { formatTime } from "./types";

type FlowRow = {
  id: string;
  name: string;
  description?: string;
  updated_at?: string;
};
type IndexEntry = {
  flow_id: string;
  canvas_path: string;
  input_set?: string;
  plan?: string[];
  created_at?: string;
};

// Flows created by the water workflow share this name prefix (see build_water_flow.py).
const FLOW_PREFIX = "水体分析";

function displayTool(slug: string) {
  return slug.split(".").at(-1) ?? slug;
}

export default function FlowsPanel({
  fixtures,
}: {
  fixtures: FixtureOption[];
}) {
  const [flows, setFlows] = useState<FlowRow[]>([]);
  const [index, setIndex] = useState<Record<string, IndexEntry>>({});
  const [setId, setSetId] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const [flowResponse, indexResponse] = await Promise.all([
      apiFetch(
        "/api/v1/flows/?remove_example_flows=true&header_flows=true&get_all=true",
      ),
      apiFetch("/api/water-demo/canvas-flows"),
    ]);
    const flowBody = (await flowResponse.json()) as FlowRow[];
    const indexBody = (await indexResponse.json()) as {
      flows?: Record<string, IndexEntry>;
    };
    if (Array.isArray(flowBody))
      setFlows(flowBody.filter((flow) => flow.name?.startsWith(FLOW_PREFIX)));
    setIndex(indexBody.flows ?? {});
  }, []);

  useEffect(() => {
    void refresh().catch(() => setNote("无法读取 flow 列表。"));
  }, [refresh]);

  useEffect(() => {
    if (!setId && fixtures[0]) setSetId(fixtures[0].id);
  }, [fixtures, setId]);

  const sourceByFlow = new Map(
    Object.entries(index).map(([key, entry]) => [
      entry.flow_id,
      { key, entry },
    ]),
  );

  async function createTemplate() {
    setBusy(true);
    setNote("正在生成模板 flow…");
    try {
      const response = await apiFetch(
        "/api/water-demo/input-sets/" + encodeURIComponent(setId) + "/canvas",
        { method: "POST" },
      );
      const body = (await response.json()) as {
        error?: string;
        status?: string;
      };
      if (!response.ok) throw new Error(body.error || "模板 flow 生成失败。");
      setNote(
        body.status === "exists"
          ? "该输入组合已有模板 flow。"
          : "模板 flow 已生成。",
      );
      await refresh();
    } catch (cause) {
      setNote(cause instanceof Error ? cause.message : "模板 flow 生成失败。");
    } finally {
      setBusy(false);
    }
  }

  async function deleteFlow(flow: FlowRow) {
    if (!window.confirm("删除 flow「" + flow.name + "」？此操作不可撤销。"))
      return;
    const response = await apiFetch("/api/v1/flows/" + flow.id, {
      method: "DELETE",
    });
    if (!response.ok) {
      setNote("删除失败（HTTP " + response.status + "）。");
      return;
    }
    await apiFetch("/api/water-demo/canvas-flows/" + flow.id, {
      method: "DELETE",
    });
    setNote("已删除。");
    await refresh();
  }

  return (
    <section className="space-y-4">
      <div className="rounded-2xl border border-border bg-card p-5">
        <h2 className="font-semibold">Flow</h2>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          每次对话运行都可以生成一个由真实组件组成的 Canvas flow，保存在
          Terraflow 中，可以打开、修改和重跑。也可以直接为某个输入组合生成模板
          flow。
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <select
            aria-label="模板输入组合"
            value={setId}
            onChange={(event) => setSetId(event.target.value)}
            className="rounded-xl border border-input bg-background px-3 py-2 text-sm"
          >
            {fixtures.map((fixture) => (
              <option key={fixture.id} value={fixture.id}>
                {fixture.title}
              </option>
            ))}
          </select>
          <Button
            type="button"
            disabled={busy || !setId}
            onClick={() => void createTemplate()}
          >
            {busy ? "生成中…" : "生成模板 flow"}
          </Button>
          <button
            type="button"
            className="text-xs text-primary underline"
            onClick={() => void refresh()}
          >
            刷新
          </button>
          {note && (
            <span className="text-xs text-muted-foreground">{note}</span>
          )}
        </div>
      </div>

      <div className="rounded-2xl border border-border bg-card p-5">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold">已保存的 flow</h3>
          <span className="text-xs text-muted-foreground">
            {flows.length} 个
          </span>
        </div>
        {flows.length === 0 ? (
          <p className="mt-4 text-sm text-muted-foreground">
            还没有水体分析 flow。
          </p>
        ) : (
          <div className="mt-4 divide-y divide-border">
            {flows.map((flow) => {
              const source = sourceByFlow.get(flow.id);
              const origin = !source
                ? "命令行生成"
                : source.key.startsWith("template:")
                  ? "模板 · " + source.key.slice("template:".length)
                  : "运行 " + source.key;
              return (
                <div
                  key={flow.id}
                  className="flex flex-wrap items-center gap-3 py-3"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{flow.name}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {origin}
                      {flow.updated_at
                        ? " · 更新于 " + formatTime(flow.updated_at)
                        : ""}
                    </p>
                    {source?.entry.plan && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        {source.entry.plan.map(displayTool).join(" → ")}
                      </p>
                    )}
                  </div>
                  <a
                    className="text-xs text-primary underline"
                    href={"/flow/" + flow.id}
                    target="_blank"
                    rel="noreferrer"
                  >
                    在 Canvas 中打开
                  </a>
                  <button
                    type="button"
                    className="text-xs text-destructive underline"
                    onClick={() => void deleteFlow(flow)}
                  >
                    删除
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}

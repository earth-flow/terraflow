import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "./api";
import { formatTime } from "./types";

type ToolRow = {
  slug: string;
  name?: string;
  toolkit?: string;
  status?: string;
  toolkit_status?: string;
  description?: string;
  water_chain?: boolean;
};
type ToolkitRow = {
  slug: string;
  name?: string;
  status?: string;
  description?: string;
  tool_count: number;
  available_count: number;
};
type Catalog = {
  fetched_at?: string;
  toolkits: ToolkitRow[];
  tools: ToolRow[];
  water_chain_tools: string[];
  scenarios: Record<string, { derive_labels?: Record<string, unknown> | null }>;
};

export default function ToolsPanel() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [onlyWater, setOnlyWater] = useState(false);
  const [openToolkit, setOpenToolkit] = useState<string | null>(null);

  useEffect(() => {
    apiFetch("/api/water-demo/tools")
      .then(async (response) => {
        const body = (await response.json()) as Catalog & { error?: string };
        if (!response.ok) throw new Error(body.error || "工具目录读取失败。");
        setCatalog(body);
      })
      .catch((cause) =>
        setError(cause instanceof Error ? cause.message : "工具目录读取失败。"),
      );
  }, []);

  const filtered = useMemo(() => {
    const text = query.trim().toLowerCase();
    return (catalog?.tools ?? []).filter(
      (tool) =>
        (!onlyWater || tool.water_chain) &&
        (!text ||
          tool.slug.toLowerCase().includes(text) ||
          (tool.name ?? "").toLowerCase().includes(text) ||
          (tool.description ?? "").toLowerCase().includes(text)),
    );
  }, [catalog, query, onlyWater]);

  if (error)
    return (
      <section className="rounded-xl border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
        {error}
      </section>
    );
  if (!catalog)
    return <p className="text-sm text-muted-foreground">正在读取工具目录…</p>;

  const available = catalog.tools.filter(
    (tool) => tool.status === "available",
  ).length;
  const searching = Boolean(query.trim()) || onlyWater;

  return (
    <section className="space-y-4">
      <div className="rounded-2xl border border-border bg-card p-5">
        <h2 className="font-semibold">工具</h2>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          工具来自 Terrabox，Terraflow 自动把每个工具生成为 Canvas 组件。Agent
          规划时只从可用工具中选择；科学上敏感的参数（阈值方向、单位、标签值）由场景预设决定。
        </p>
        <div className="mt-4 grid gap-3 md:grid-cols-4">
          {[
            ["工具集", catalog.toolkits.length],
            ["工具", catalog.tools.length],
            ["可用", available],
            ["水体链使用", catalog.water_chain_tools.length],
          ].map(([name, value]) => (
            <div key={name as string} className="rounded-xl bg-muted/50 p-3">
              <p className="text-xs text-muted-foreground">{name}</p>
              <p className="mt-1 text-lg font-semibold">{value}</p>
            </div>
          ))}
        </div>
        <p className="mt-3 text-xs text-muted-foreground">
          读取时间 {formatTime(catalog.fetched_at)} UTC
        </p>
      </div>

      <div className="rounded-2xl border border-border bg-card p-5">
        <h3 className="font-semibold">场景预设</h3>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          {Object.entries(catalog.scenarios).map(([kind, scenario]) => (
            <div key={kind} className="min-w-0 rounded-xl bg-muted/40 p-3">
              <p className="text-sm font-medium">
                {kind === "sar_backscatter" ? "SAR 后向散射" : "0/1 水体标签"}
                <span className="ml-2 font-mono text-xs text-muted-foreground">
                  {kind}
                </span>
              </p>
              <pre className="mt-2 overflow-x-auto text-[11px] leading-5">
                {scenario.derive_labels
                  ? JSON.stringify(scenario.derive_labels, null, 2)
                  : "直接比较两期标签，无需派生步骤"}
              </pre>
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-2xl border border-border bg-card p-5">
        <div className="flex flex-wrap items-center gap-3">
          <input
            aria-label="搜索工具"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索工具名称、slug 或说明"
            className="min-w-[16rem] flex-1 rounded-xl border border-input bg-background px-3 py-2 text-sm"
          />
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={onlyWater}
              onChange={(event) => setOnlyWater(event.target.checked)}
            />
            只看水体链工具
          </label>
        </div>
        {searching ? (
          <ToolList tools={filtered} />
        ) : (
          <div className="mt-4 divide-y divide-border">
            {catalog.toolkits.map((toolkit) => {
              const isOpen = openToolkit === toolkit.slug;
              return (
                <div key={toolkit.slug} className="py-2">
                  <button
                    type="button"
                    className="flex w-full items-center gap-3 text-left"
                    onClick={() => setOpenToolkit(isOpen ? null : toolkit.slug)}
                  >
                    <span className="font-mono text-sm">{toolkit.slug}</span>
                    <span className="text-xs text-muted-foreground">
                      {toolkit.available_count}/{toolkit.tool_count} 可用
                    </span>
                    {toolkit.status !== "active" && (
                      <span className="water-agent-step-blocked rounded-full px-2 py-0.5 text-xs">
                        {toolkit.status}
                      </span>
                    )}
                    <span className="ml-auto text-xs text-primary">
                      {isOpen ? "收起" : "展开"}
                    </span>
                  </button>
                  {isOpen && (
                    <ToolList
                      tools={catalog.tools.filter(
                        (tool) => tool.toolkit === toolkit.slug,
                      )}
                    />
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}

function ToolList({ tools }: { tools: ToolRow[] }) {
  if (tools.length === 0)
    return (
      <p className="mt-4 text-sm text-muted-foreground">没有匹配的工具。</p>
    );
  return (
    <div className="mt-3 space-y-2">
      {tools.map((tool) => (
        <div key={tool.slug} className="rounded-xl bg-muted/30 px-3 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs">{tool.slug}</span>
            <span
              className={
                "rounded-full px-2 py-0.5 text-xs " +
                (tool.status === "available"
                  ? "water-agent-step-success"
                  : "water-agent-step-blocked")
              }
            >
              {tool.status === "available" ? "可用" : tool.status}
            </span>
            {tool.water_chain && (
              <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">
                水体链
              </span>
            )}
          </div>
          {tool.description && (
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {tool.description}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

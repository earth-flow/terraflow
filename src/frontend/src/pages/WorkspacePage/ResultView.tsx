import { useEffect, useState } from "react";
import { API, apiFetch, displayTool, formatJson } from "./api";
import type { GenericResult } from "./types";

type EvidenceRecord = {
  chain: string;
  step?: number;
  tool_slug: string;
  target?: string | null;
  execution_id?: string;
  trace_id?: string;
  manifest_path?: string;
  inputs?: unknown;
  outputs?: unknown;
  manifest?: unknown;
  success?: boolean;
  execution_blocked?: boolean;
  reason_code?: string | null;
};

function EvidenceView({
  runId,
  inline,
}: {
  runId: string;
  inline?: EvidenceRecord[];
}) {
  const [records, setRecords] = useState<EvidenceRecord[] | null>(
    inline ?? null,
  );
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (inline) return;
    void apiFetch(API + "/runs/" + encodeURIComponent(runId) + "/evidence")
      .then(async (response) => {
        const body = (await response.json()) as {
          evidence?: { records: EvidenceRecord[] };
          error?: string;
        };
        if (!response.ok) throw new Error(body.error || "证据无法读取。");
        setRecords(body.evidence?.records ?? []);
      })
      .catch((cause) =>
        setError(cause instanceof Error ? cause.message : "证据无法读取。"),
      );
  }, [runId, inline]);
  if (error) return <p className="text-xs text-destructive">{error}</p>;
  if (!records)
    return <p className="text-xs text-muted-foreground">正在读取证据…</p>;
  return (
    <div className="divide-y divide-border">
      {records.map((record) => {
        const key = record.execution_id ?? record.chain + record.step;
        const isOpen = open === key;
        return (
          <div key={key} className="py-2">
            <button
              type="button"
              className="flex w-full flex-wrap items-center gap-2 text-left"
              onClick={() => setOpen(isOpen ? null : key)}
            >
              <span className="text-xs text-muted-foreground">
                {record.chain === "rejection_chain" ? "门控自检" : "分析"}
              </span>
              <span className="rounded bg-muted px-2 py-0.5 font-mono text-xs">
                {displayTool(record.tool_slug)}
                {record.target ? "（" + record.target + "）" : ""}
              </span>
              <span
                className={
                  "rounded-full px-2 py-0.5 text-xs " +
                  (record.execution_blocked
                    ? "ws-step-blocked"
                    : "ws-step-success")
                }
              >
                {record.execution_blocked
                  ? "拦截：" + (record.reason_code ?? "")
                  : "完成"}
              </span>
              <span className="ml-auto text-xs text-primary">
                {isOpen ? "收起" : "展开"}
              </span>
            </button>
            {isOpen && (
              <div className="ws-evidence mt-2 space-y-2 rounded-xl p-3 text-[11px] leading-5">
                <p className="break-all text-muted-foreground">
                  execution {record.execution_id ?? "—"} · manifest{" "}
                  {record.manifest_path ?? "—"}
                </p>
                <p className="font-medium">输入</p>
                <pre className="overflow-x-auto">
                  {formatJson(record.inputs)}
                </pre>
                <p className="font-medium">输出摘要</p>
                <pre className="overflow-x-auto">
                  {formatJson(record.outputs)}
                </pre>
                {record.manifest ? (
                  <>
                    <p className="font-medium">manifest 内容</p>
                    <pre className="max-h-64 overflow-auto">
                      {formatJson(record.manifest)}
                    </pre>
                  </>
                ) : null}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function formatValue(value: number | string | null) {
  if (value === null || value === undefined) return "—";
  return typeof value === "number" ? value.toLocaleString("zh-CN") : value;
}

export default function ResultView({
  runId,
  result,
}: {
  runId: string;
  result: GenericResult;
}) {
  const [showEvidence, setShowEvidence] = useState(false);
  const [canvasNote, setCanvasNote] = useState("");

  async function openCanvas() {
    if (result.canvas_url) {
      window.open(result.canvas_url, "_blank");
      return;
    }
    const tab = window.open("about:blank", "_blank");
    setCanvasNote("正在生成 flow…");
    try {
      const response = await apiFetch(
        API + "/runs/" + encodeURIComponent(runId) + "/canvas",
        { method: "POST" },
      );
      const body = (await response.json()) as {
        canvas_path?: string;
        error?: string;
      };
      if (!response.ok || !body.canvas_path)
        throw new Error(body.error || "flow 生成失败。");
      if (tab) tab.location.href = body.canvas_path;
      else window.location.assign(body.canvas_path);
      setCanvasNote("");
    } catch (cause) {
      tab?.close();
      setCanvasNote(cause instanceof Error ? cause.message : "flow 生成失败。");
    }
  }

  return (
    <div className="space-y-4">
      <p
        className={
          "text-sm font-medium leading-6 " +
          (result.status === "blocked" ? "text-destructive" : "")
        }
      >
        {result.headline}
      </p>
      {result.metrics.length > 0 && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          {result.metrics.map((metric) => (
            <div
              key={metric.key}
              className="rounded-xl bg-muted/50 p-3"
              title={metric.note}
            >
              <p className="text-xs text-muted-foreground">{metric.label}</p>
              <p className="mt-1 font-medium">
                {formatValue(metric.value)}
                <span className="ml-1 text-xs text-muted-foreground">
                  {metric.unit}
                </span>
              </p>
            </div>
          ))}
        </div>
      )}
      {result.layers.length > 0 && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {result.layers.map((layer) => (
            <figure key={layer.key} className="min-w-0">
              <div className="ws-thumb aspect-square overflow-hidden rounded-xl">
                <img
                  src={layer.url}
                  alt={layer.label}
                  loading="lazy"
                  className="h-full w-full object-cover [image-rendering:pixelated]"
                />
              </div>
              <figcaption className="mt-1 text-center text-xs text-muted-foreground">
                {layer.label}
              </figcaption>
              {layer.legend && (
                <div className="mt-1 flex flex-wrap justify-center gap-x-2 gap-y-0.5">
                  {layer.legend.map((item) => (
                    <span
                      key={item.label}
                      className="flex items-center gap-1 text-[10px] text-muted-foreground"
                    >
                      <span
                        className="inline-block h-2 w-3 rounded-sm border border-border"
                        style={{ background: item.color }}
                      />
                      {item.label}
                    </span>
                  ))}
                </div>
              )}
            </figure>
          ))}
        </div>
      )}
      <div className="flex flex-wrap gap-2 text-xs">
        {result.gate?.checked && (
          <span
            className={
              "rounded-full px-2 py-1 " +
              (result.gate.passed ? "ws-step-success" : "ws-step-blocked")
            }
            title={result.gate.description}
          >
            {result.gate.label ?? "门控检查"}：
            {result.gate.passed
              ? "通过"
              : "拦截（" + (result.gate.reason_code ?? "") + "）"}
          </span>
        )}
        {result.self_check && (
          <span
            className="ws-step-blocked rounded-full px-2 py-1"
            title={result.self_check.description}
          >
            门控自检：按预期拦截（{result.self_check.reason_code}）
          </span>
        )}
      </div>
      {result.boundaries.length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-xs leading-5 text-muted-foreground">
          {result.boundaries.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-3 text-xs">
        {result.canvas !== false && (
          <button
            type="button"
            className="text-primary underline"
            onClick={() => void openCanvas()}
          >
            在 Canvas 中打开
          </button>
        )}
        <button
          type="button"
          className="text-primary underline"
          onClick={() => setShowEvidence((value) => !value)}
        >
          {showEvidence ? "收起证据" : "查看证据"}
        </button>
        <span className="font-mono text-muted-foreground">{runId}</span>
        {canvasNote && (
          <span className="text-muted-foreground">{canvasNote}</span>
        )}
      </div>
      {showEvidence && (
        <EvidenceView
          runId={runId}
          inline={result.steps_inline as EvidenceRecord[] | undefined}
        />
      )}
    </div>
  );
}

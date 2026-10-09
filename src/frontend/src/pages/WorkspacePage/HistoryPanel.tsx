import { useMemo, useState } from "react";
import type { HistoryItem } from "./types";
import { formatTime } from "./types";

type Filter = "all" | "complete" | "blocked" | "error";

function historyLabel(item: HistoryItem) {
  if (item.status === "running" || item.status === "queued")
    return item.stage || "运行中";
  if (item.status === "error") return "执行受阻：" + (item.error || "未知原因");
  return item.prompt || item.run_id || "未命名运行";
}

function outcome(item: HistoryItem) {
  if (item.status === "error") return "失败";
  if (item.status !== "complete") return item.stage || item.status || "—";
  if (item.success_status === "blocked_by_gate") return "门控拦截";
  return "完成";
}

export default function HistoryPanel({
  history,
  onOpen,
  onRefresh,
}: {
  history: HistoryItem[];
  onOpen: (runId: string) => void;
  onRefresh: () => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const rows = useMemo(
    () =>
      history.filter((item) => {
        if (filter === "all") return true;
        if (filter === "error") return item.status === "error";
        if (filter === "blocked")
          return item.success_status === "blocked_by_gate";
        return (
          item.status === "complete" &&
          item.success_status !== "blocked_by_gate"
        );
      }),
    [history, filter],
  );
  return (
    <section className="rounded-2xl border border-border bg-card p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">运行记录</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            来自 runner 写入的运行摘要；当前进程内失败的任务也会列出。
          </p>
        </div>
        <div className="flex items-center gap-3">
          <select
            aria-label="按结果筛选"
            value={filter}
            onChange={(event) => setFilter(event.target.value as Filter)}
            className="rounded-lg border border-input bg-background px-2 py-1 text-xs"
          >
            <option value="all">全部</option>
            <option value="complete">完成</option>
            <option value="blocked">门控拦截</option>
            <option value="error">失败</option>
          </select>
          <button
            type="button"
            className="text-xs text-primary underline"
            onClick={onRefresh}
          >
            刷新
          </button>
        </div>
      </div>
      {rows.length === 0 ? (
        <p className="mt-4 text-sm text-muted-foreground">
          没有符合条件的运行。
        </p>
      ) : (
        <div className="mt-4 divide-y divide-border">
          {rows.map((item) => (
            <div
              key={item.job_id ?? item.run_id}
              className="flex flex-wrap items-center gap-3 py-3"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm">{historyLabel(item)}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {formatTime(item.created_at)} ·{" "}
                  {item.fixture_label || item.fixture_id || "—"} ·{" "}
                  {item.model_used || "等待模型"} · {item.execution_count || 0}{" "}
                  条记录
                </p>
              </div>
              <span className="rounded-full bg-muted px-2 py-1 text-xs">
                {outcome(item)}
              </span>
              {item.status === "complete" && item.run_id && (
                <button
                  type="button"
                  className="text-xs text-primary underline"
                  onClick={() => onOpen(item.run_id!)}
                >
                  打开
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

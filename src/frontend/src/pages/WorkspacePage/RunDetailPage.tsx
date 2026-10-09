import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { API, apiJson, displayTool } from "./api";
import PageFrame from "./PageFrame";
import ResultView from "./ResultView";
import type { GenericResult } from "./types";
import { formatTime } from "./types";

type RunDetail = {
  run_id: string;
  created_at?: string;
  prompt?: string;
  dataset: { title?: string; label?: string };
  model?: string;
  plan?: string[];
  result: GenericResult;
};

export default function RunDetailPage() {
  const { runId = "" } = useParams();
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    void apiJson<RunDetail>(
      API + "/runs/" + encodeURIComponent(runId) + "/result",
    )
      .then(setDetail)
      .catch((cause) =>
        setError(cause instanceof Error ? cause.message : "运行记录无法读取。"),
      );
  }, [runId]);
  return (
    <PageFrame>
      <Link to="/workspace/runs" className="text-xs text-primary underline">
        ← 运行记录
      </Link>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {detail && (
        <div className="rounded-2xl border border-border bg-card p-5">
          <p className="text-sm font-medium">{detail.prompt || "运行详情"}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {formatTime(detail.created_at)} ·{" "}
            {detail.dataset.title || detail.dataset.label} · {detail.model} ·{" "}
            {(detail.plan ?? []).map(displayTool).join(" → ")}
          </p>
          <div className="mt-4">
            <ResultView runId={detail.run_id} result={detail.result} />
          </div>
        </div>
      )}
    </PageFrame>
  );
}

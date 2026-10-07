import "./water-agent.css";
import { type FormEvent, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";

type RunStatus = "idle" | "running" | "complete" | "error";

type Step = {
  step: number;
  tool_slug: string;
  execution_id?: string;
  success: boolean;
  execution_blocked?: boolean;
  reason_code?: string;
  manifest_path?: string;
  trace_id?: string;
  error?: string;
};

type DemoSummary = {
  run_id: string;
  created_at: string;
  model?: {
    model_requested?: string;
    model_used?: string;
    endpoint_model_id?: string;
    fallback_reason?: string | null;
  };
  plan?: {
    status?: string;
    execution_order?: string[];
    reason?: string;
  };
  success_chain?: {
    status?: string;
    fixture_type?: string;
    steps?: Step[];
  };
  rejection_chain?: {
    status?: string;
    reason_code?: string;
    failed_checks?: string[];
    steps?: Step[];
  };
  analytics?: {
    matched_count?: number;
    expected_count?: number;
  };
  fixtures?: {
    type?: string;
    claim_boundary?: string;
  };
};

type ApiResponse = {
  status: string;
  summary?: DemoSummary;
  error?: string;
  cli?: { run_id?: string };
};

const DEFAULT_PROMPT =
  "请分析这组前后栅格，检查水体变化，说明执行了哪些工具，并给出可追溯证据。";

function displayTool(slug: string) {
  return slug.split(".").at(-1) ?? slug;
}

function statusLabel(status: RunStatus) {
  if (status === "running") return "Agent 正在执行";
  if (status === "complete") return "已完成";
  if (status === "error") return "执行受阻";
  return "等待输入";
}

export default function WaterAgentPage() {
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [status, setStatus] = useState<RunStatus>("idle");
  const [result, setResult] = useState<DemoSummary | null>(null);
  const [error, setError] = useState("");

  const successSteps = result?.success_chain?.steps ?? [];
  const rejectionSteps = result?.rejection_chain?.steps ?? [];
  const allSteps = useMemo(
    () => [
      ...successSteps.map((step) => ({ ...step, chain: "成功链" })),
      ...rejectionSteps.map((step) => ({ ...step, chain: "拒绝链" })),
    ],
    [successSteps, rejectionSteps],
  );

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status === "running") return;
    setStatus("running");
    setError("");
    setResult(null);
    try {
      const response = await fetch("/api/water-demo/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt }),
      });
      const body = (await response.json()) as ApiResponse;
      if (!response.ok || body.status !== "complete" || !body.summary) {
        throw new Error(body.error || "水体 Agent 没有返回可展示的运行摘要。");
      }
      setResult(body.summary);
      setStatus("complete");
    } catch (cause) {
      setStatus("error");
      setError(cause instanceof Error ? cause.message : "网页请求失败");
    }
  }

  return (
    <main className="water-agent-page flex h-full min-h-0 flex-1 flex-col overflow-y-auto bg-background px-6 py-8">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
        <header className="water-agent-hero rounded-2xl border border-border bg-card p-6 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
                Terraflow / Agent workspace
              </p>
              <h1 className="text-2xl font-semibold tracking-tight">
                水体变化分析
              </h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
                用自然语言提交任务。网页端调用同一条 Terraflow
                水体工程链路，并把模型规划、工具步骤和证据索引放在同一处查看。
              </p>
            </div>
            <span className="water-agent-status rounded-full border px-3 py-1 text-xs font-medium">
              {statusLabel(status)}
            </span>
          </div>

          <form onSubmit={submit} className="mt-6">
            <label
              htmlFor="water-agent-prompt"
              className="mb-2 block text-sm font-medium"
            >
              任务描述
            </label>
            <textarea
              id="water-agent-prompt"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              rows={3}
              maxLength={1200}
              className="w-full resize-y rounded-xl border border-input bg-background px-4 py-3 text-sm leading-6 outline-none transition focus:ring-2 focus:ring-ring"
              placeholder="例如：检查前后栅格中的水体变化，并说明哪些检查阻止了不一致输入继续执行。"
            />
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
              <p className="text-xs text-muted-foreground">
                当前演示使用可审计的合成工程栅格；结果不会自动解释为洪水因果。
              </p>
              <Button
                type="submit"
                disabled={status === "running" || !prompt.trim()}
              >
                {status === "running" ? "执行中…" : "运行水体 Agent"}
              </Button>
            </div>
          </form>
        </header>

        {error && (
          <section className="rounded-xl border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
            {error}
          </section>
        )}

        {result && (
          <>
            <section className="grid gap-4 md:grid-cols-4">
              <div className="rounded-xl border border-border bg-card p-4">
                <p className="text-xs text-muted-foreground">模型</p>
                <p className="mt-2 font-medium">
                  {result.model?.model_used ?? "未知"}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  endpoint: {result.model?.endpoint_model_id ?? "未知"}
                </p>
              </div>
              <div className="rounded-xl border border-border bg-card p-4">
                <p className="text-xs text-muted-foreground">运行 ID</p>
                <p className="mt-2 break-all font-mono text-xs">
                  {result.run_id}
                </p>
              </div>
              <div className="rounded-xl border border-border bg-card p-4">
                <p className="text-xs text-muted-foreground">规划状态</p>
                <p className="mt-2 font-medium">
                  {result.plan?.status ?? "未知"}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {result.plan?.execution_order?.length ?? 0} 个工具
                </p>
              </div>
              <div className="rounded-xl border border-border bg-card p-4">
                <p className="text-xs text-muted-foreground">可追溯记录</p>
                <p className="mt-2 font-medium">
                  {result.analytics?.matched_count ?? 0} /{" "}
                  {result.analytics?.expected_count ?? 0}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  execution history
                </p>
              </div>
            </section>

            <section className="grid gap-6 lg:grid-cols-[1.4fr_0.9fr]">
              <div className="rounded-2xl border border-border bg-card p-5">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h2 className="font-semibold">Agent 工具执行链</h2>
                    <p className="mt-1 text-xs text-muted-foreground">
                      规划顺序：
                      {result.plan?.execution_order
                        ?.map(displayTool)
                        .join(" → ") || "未返回"}
                    </p>
                  </div>
                  <span className="rounded-full bg-muted px-2.5 py-1 text-xs">
                    {allSteps.length} 条记录
                  </span>
                </div>
                <div className="mt-4 divide-y divide-border">
                  {allSteps.map((step) => (
                    <div
                      key={`${step.chain}-${step.execution_id ?? step.step}`}
                      className="py-3 first:pt-0 last:pb-0"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs text-muted-foreground">
                          {step.chain}
                        </span>
                        <span className="rounded bg-muted px-2 py-0.5 font-mono text-xs">
                          {displayTool(step.tool_slug)}
                        </span>
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs ${step.success && !step.execution_blocked ? "water-agent-step-success" : "water-agent-step-blocked"}`}
                        >
                          {step.execution_blocked
                            ? `已拦截：${step.reason_code ?? "gate"}`
                            : step.success
                              ? "完成"
                              : "失败"}
                        </span>
                      </div>
                      <div className="mt-2 grid gap-1 text-xs text-muted-foreground md:grid-cols-2">
                        <span>execution: {step.execution_id ?? "—"}</span>
                        <span>trace: {step.trace_id ?? "—"}</span>
                        {step.manifest_path && (
                          <span className="md:col-span-2">
                            manifest: {step.manifest_path}
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded-2xl border border-border bg-card p-5">
                <h2 className="font-semibold">Agent 回应</h2>
                <p className="mt-3 text-sm leading-6">
                  {result.plan?.reason ||
                    "模型返回了可执行的工具顺序，页面保留原始执行证据供复核。"}
                </p>
                <div className="water-agent-rejection mt-5 rounded-xl p-4 text-sm leading-6">
                  <p className="font-medium">拒绝链</p>
                  <p className="mt-1 text-muted-foreground">
                    {result.rejection_chain?.reason_code
                      ? `输入不一致时已停止下游调用（${result.rejection_chain.reason_code}）。`
                      : "本次未返回拒绝原因。"}
                  </p>
                </div>
                <div className="mt-4 rounded-xl bg-muted/60 p-4 text-xs leading-5 text-muted-foreground">
                  {result.fixtures?.claim_boundary ||
                    "结果仅表示当前工程输入上的工具执行记录。"}
                </div>
              </div>
            </section>
          </>
        )}

        {!result && !error && (
          <section className="rounded-2xl border border-dashed border-border bg-card/50 px-6 py-12 text-center">
            <p className="text-sm font-medium">
              提交任务后，这里会显示规划和证据记录
            </p>
            <p className="mt-2 text-xs text-muted-foreground">
              先把命令链路搬到网页端，再逐步增加文件选择、任务历史和 Canvas
              节点视图。
            </p>
          </section>
        )}
      </div>
    </main>
  );
}

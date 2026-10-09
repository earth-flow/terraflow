import "./water-agent.css";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { apiFetch } from "./api";
import DataPanel from "./DataPanel";
import DatasetPicker from "./DatasetPicker";
import FlowsPanel from "./FlowsPanel";
import HistoryPanel from "./HistoryPanel";
import ToolsPanel from "./ToolsPanel";
import type { FixtureInfo, FixtureOption, HistoryItem } from "./types";
import { sceneLine } from "./types";

type RunStatus = "idle" | "running" | "complete" | "error";
type Step = {
  step: number;
  tool_slug: string;
  execution_id?: string;
  success: boolean;
  execution_blocked?: boolean;
  reason_code?: string;
  failed_checks?: string[];
  manifest_path?: string;
  manifest_exists?: boolean;
  trace_id?: string;
  target?: "before" | "after";
  inputs?: unknown;
  outputs?: unknown;
  error?: string;
};
type DemoSummary = {
  run_id: string;
  created_at: string;
  request?: { prompt?: string };
  web_request?: { fixture_id?: string; conversation_turns?: number };
  model?: {
    model_requested?: string;
    model_used?: string;
    endpoint_model_id?: string;
    fallback_reason?: string | null;
  };
  plan?: { status?: string; execution_order?: string[]; reason?: string };
  success_chain?: {
    status?: string;
    fixture_type?: string;
    execution_count?: number;
    blocked_tool?: string;
    reason_code?: string;
    failed_checks?: string[];
    derived_labels?: {
      tool?: string;
      preset?: string;
      threshold?: number | null;
      units?: string;
    } | null;
    steps?: Step[];
  };
  rejection_chain?: {
    status?: string;
    reason_code?: string;
    failed_checks?: string[];
    steps?: Step[];
  };
  analytics?: { matched_count?: number; expected_count?: number };
  fixtures?: FixtureInfo & {
    before?: string;
    after?: string;
    mismatch?: string | null;
  };
};
type WaterStats = {
  valid_area_m2?: number;
  before_water_area_m2?: number;
  after_water_area_m2?: number;
  new_water_area_m2?: number;
  receded_water_area_m2?: number;
  pixel_area_m2?: number;
  crs?: string;
};
type EvidenceRecord = Step & {
  chain: "success_chain" | "rejection_chain";
  manifest?: Record<string, unknown>;
};
type Evidence = {
  records: EvidenceRecord[];
  analytics?: {
    endpoint?: string;
    matched_count?: number;
    expected_count?: number;
    records?: unknown;
  };
};
type JobState = {
  job_id: string;
  status: string;
  stage?: string;
  message?: string;
  current_step?: number | null;
  total_steps?: number | null;
  tool?: string | null;
  run_id?: string;
  summary?: DemoSummary;
  error?: string;
};
type ApiResponse = {
  status: string;
  job_id?: string;
  summary?: DemoSummary;
  error?: string;
  evidence?: Evidence;
};
type InputResolution = {
  input_set?: string | null;
  mentioned?: string | null;
  unmatched?: boolean;
  reason?: string;
  model?: string;
};
type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  runId?: string;
};

type Section = "chat" | "flows" | "tools" | "data" | "history";
const SECTIONS: { id: Section; label: string; hint: string }[] = [
  { id: "chat", label: "对话", hint: "提交任务、查看结果与证据" },
  { id: "flows", label: "Flow", hint: "由运行生成、可在 Canvas 打开" },
  { id: "tools", label: "工具", hint: "Terrabox 目录与场景预设" },
  { id: "data", label: "数据", hint: "输入组合与上传" },
  { id: "history", label: "运行记录", hint: "历史运行与失败原因" },
];
const HISTORY_LIMIT = 50;

const DEFAULT_PROMPT =
  "请分析这组前后栅格，检查水体变化，说明执行了哪些工具，并给出可追溯证据。";
const STAGES = [
  { id: "queued", label: "排队" },
  { id: "starting", label: "启动 runner" },
  { id: "discovering", label: "发现工具" },
  { id: "planning", label: "模型规划" },
  { id: "executing_success", label: "执行分析" },
  { id: "executing_rejection", label: "门控自检" },
  { id: "recording", label: "整理证据" },
  { id: "complete", label: "完成" },
];

function displayTool(slug: string) {
  return slug.split(".").at(-1) ?? slug;
}
function statusLabel(status: RunStatus) {
  if (status === "running") return "Agent 正在执行";
  if (status === "complete") return "已完成";
  if (status === "error") return "执行受阻";
  return "等待输入";
}
function stageIndex(stage?: string) {
  const index = STAGES.findIndex((item) => item.id === stage);
  return index < 0 ? 0 : index;
}
function formatJson(value: unknown) {
  return value === undefined || value === null
    ? "—"
    : JSON.stringify(value, null, 2);
}
function km2(value?: number) {
  return value === undefined || value === null
    ? "—"
    : (value / 1e6).toFixed(2) + " km²";
}
function targetLabel(target?: string) {
  if (target === "before") return "（前期）";
  if (target === "after") return "（后期）";
  return "";
}
function waterStats(summary: DemoSummary): WaterStats | null {
  const step = summary.success_chain?.steps?.find(
    (item) => item.tool_slug === "geo_water.water_evidence" && item.success,
  );
  return step && step.outputs && typeof step.outputs === "object"
    ? (step.outputs as WaterStats)
    : null;
}
function assistantReply(summary: DemoSummary) {
  const success = summary.success_chain;
  const reason = summary.plan?.reason?.trim();
  const lines: string[] = [];
  if (success?.status === "blocked_by_gate") {
    lines.push(
      "分析在 " +
        displayTool(success.blocked_tool ?? "gate") +
        " 被门控拦截（" +
        (success.reason_code ?? "未知原因") +
        "），后续工具没有执行。",
    );
  } else {
    lines.push("已执行 " + (success?.steps?.length ?? 0) + " 次工具调用。");
    const stats = waterStats(summary);
    if (stats)
      lines.push(
        "后期水体 " +
          km2(stats.after_water_area_m2) +
          "，相对前期新增 " +
          km2(stats.new_water_area_m2) +
          "。",
      );
  }
  const rejection = summary.rejection_chain;
  if (rejection?.status === "not_run")
    lines.push("这组数据没有自检样本，未运行门控自检。");
  else if (rejection?.reason_code)
    lines.push(
      "门控自检：故意不一致的样本被拦截（" + rejection.reason_code + "）。",
    );
  return (reason ? reason + "\n\n" : "") + lines.join("");
}

export default function WaterAgentPage() {
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [status, setStatus] = useState<RunStatus>("idle");
  const [result, setResult] = useState<DemoSummary | null>(null);
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [error, setError] = useState("");
  const [job, setJob] = useState<JobState | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [fixtures, setFixtures] = useState<FixtureOption[]>([]);
  const [fixtureId, setFixtureId] = useState("demo-fixtures");
  const [expandedStep, setExpandedStep] = useState<string | null>(null);
  const [canvasState, setCanvasState] = useState("");
  const [selfCheck, setSelfCheck] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [pendingChoice, setPendingChoice] = useState<{
    prompt: string;
    resolution: InputResolution;
  } | null>(null);
  const [section, setSection] = useState<Section>("chat");

  async function openCanvas(runId: string) {
    // Open the tab synchronously so popup blockers allow it, then point it at the flow.
    const tab = window.open("about:blank", "_blank");
    setCanvasState("正在生成 Canvas flow…");
    try {
      const response = await apiFetch(
        "/api/water-demo/runs/" + encodeURIComponent(runId) + "/canvas",
        { method: "POST" },
      );
      const body = (await response.json()) as {
        canvas_path?: string;
        error?: string;
      };
      if (!response.ok || !body.canvas_path)
        throw new Error(body.error || "Canvas flow 生成失败。");
      if (tab) tab.location.href = body.canvas_path;
      else window.location.assign(body.canvas_path);
      setCanvasState("");
    } catch (cause) {
      tab?.close();
      setCanvasState(
        cause instanceof Error ? cause.message : "Canvas flow 生成失败。",
      );
    }
  }

  const selectedFixture = fixtures.find((fixture) => fixture.id === fixtureId);
  const stats = result ? waterStats(result) : null;
  const successSteps = result?.success_chain?.steps ?? [];
  const rejectionSteps = result?.rejection_chain?.steps ?? [];
  const allSteps = useMemo(
    () => [
      ...successSteps.map((step) => ({ ...step, chain: "分析" as const })),
      ...rejectionSteps.map((step) => ({
        ...step,
        chain: "门控自检" as const,
      })),
    ],
    [successSteps, rejectionSteps],
  );
  const evidenceByExecution = useMemo(
    () =>
      new Map(
        (evidence?.records ?? []).map((record) => [
          record.execution_id ?? record.chain + "-" + record.step,
          record,
        ]),
      ),
    [evidence],
  );

  async function refreshFixtures(selectId?: string) {
    const response = await apiFetch("/api/water-demo/fixtures");
    const body = (await response.json()) as { fixtures?: FixtureOption[] };
    if (Array.isArray(body.fixtures)) setFixtures(body.fixtures);
    if (selectId) setFixtureId(selectId);
  }

  useEffect(() => {
    void Promise.all([
      apiFetch("/api/water-demo/fixtures").then((response) => response.json()),
      apiFetch("/api/water-demo/history?limit=" + HISTORY_LIMIT).then(
        (response) => response.json(),
      ),
    ])
      .then(([fixtureBody, historyBody]) => {
        if (Array.isArray(fixtureBody.fixtures)) {
          setFixtures(fixtureBody.fixtures);
          const preferred =
            fixtureBody.fixtures.find(
              (item: FixtureOption) => item.audience !== "developer",
            ) ?? fixtureBody.fixtures[0];
          if (preferred?.id) setFixtureId(preferred.id);
        }
        if (Array.isArray(historyBody.runs)) setHistory(historyBody.runs);
      })
      .catch(() =>
        setError("无法读取演示配置和运行历史，请检查网页 adapter 是否启动。"),
      );
  }, []);

  async function refreshHistory() {
    const response = await apiFetch(
      "/api/water-demo/history?limit=" + HISTORY_LIMIT,
    );
    const body = (await response.json()) as { runs?: HistoryItem[] };
    if (Array.isArray(body.runs)) setHistory(body.runs);
  }

  async function loadRun(runId: string) {
    setError("");
    try {
      const runResponse = await apiFetch(
        "/api/water-demo/runs/" + encodeURIComponent(runId),
      );
      const evidenceResponse = await apiFetch(
        "/api/water-demo/runs/" + encodeURIComponent(runId) + "/evidence",
      );
      const runBody = (await runResponse.json()) as ApiResponse;
      const evidenceBody = (await evidenceResponse.json()) as ApiResponse;
      if (!runResponse.ok || !runBody.summary)
        throw new Error(runBody.error || "运行记录无法读取。");
      setResult(runBody.summary);
      setEvidence(evidenceBody.evidence ?? null);
      setStatus("complete");
      setJob(null);
      setExpandedStep(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "运行记录无法读取。");
    }
  }

  async function pollJob(jobId: string) {
    for (;;) {
      const response = await apiFetch(
        "/api/water-demo/jobs/" + encodeURIComponent(jobId),
      );
      const body = (await response.json()) as JobState;
      if (!response.ok) throw new Error(body.error || "任务状态无法读取。");
      setJob(body);
      if (body.status === "complete" && body.summary) {
        const summary = body.summary;
        setResult(summary);
        setStatus("complete");
        const evidenceResponse = await apiFetch(
          "/api/water-demo/runs/" +
            encodeURIComponent(summary.run_id) +
            "/evidence",
        );
        const evidenceBody = (await evidenceResponse.json()) as ApiResponse;
        setEvidence(evidenceBody.evidence ?? null);
        setMessages((current) =>
          current.concat({
            id: summary.run_id + "-assistant",
            role: "assistant",
            runId: summary.run_id,
            content: assistantReply(summary),
          }),
        );
        await refreshHistory();
        return;
      }
      if (body.status === "error")
        throw new Error(body.error || body.message || "水体 Agent 执行受阻。");
      await new Promise((resolve) => window.setTimeout(resolve, 700));
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status === "running" || resolving || !prompt.trim()) return;
    const text = prompt.trim();
    setPendingChoice(null);
    setResolving(true);
    let resolution: InputResolution | null = null;
    try {
      const response = await apiFetch("/api/water-demo/resolve-input", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: text, current: fixtureId }),
      });
      if (response.ok) resolution = (await response.json()) as InputResolution;
    } catch {
      // Identification is advisory; run with the current selection if it fails.
    } finally {
      setResolving(false);
    }
    if (
      resolution &&
      ((resolution.input_set && resolution.input_set !== fixtureId) ||
        resolution.unmatched)
    ) {
      setPendingChoice({ prompt: text, resolution });
      return;
    }
    await startRun(
      text,
      fixtureId,
      resolution && {
        ...resolution,
        decision: resolution.input_set ? "matched_current" : "no_mention",
      },
    );
  }

  async function startRun(
    currentPrompt: string,
    setId: string,
    resolution: (InputResolution & { decision: string }) | null,
  ) {
    setPendingChoice(null);
    setFixtureId(setId);
    setMessages((current) =>
      current.concat({
        id: String(Date.now()) + "-user",
        role: "user",
        content: currentPrompt,
      }),
    );
    setStatus("running");
    setError("");
    setResult(null);
    setEvidence(null);
    setExpandedStep(null);
    setJob(null);
    const runSelfCheck =
      selfCheck &&
      Boolean(fixtures.find((item) => item.id === setId)?.has_mismatch);
    try {
      const response = await apiFetch("/api/water-demo/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: currentPrompt,
          fixture_id: setId,
          messages,
          self_check: runSelfCheck,
          input_resolution: resolution,
        }),
      });
      const body = (await response.json()) as ApiResponse;
      if (!response.ok || body.status !== "queued" || !body.job_id)
        throw new Error(body.error || "水体 Agent 任务没有成功提交。");
      setJob({
        job_id: body.job_id,
        status: "queued",
        stage: "queued",
        message: "任务已提交",
      });
      await pollJob(body.job_id);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "网页请求失败";
      setStatus("error");
      setError(message);
      setMessages((current) =>
        current.concat({
          id: String(Date.now()) + "-assistant-error",
          role: "assistant",
          content: "这次任务没有完成：" + message,
        }),
      );
      await refreshHistory().catch(() => undefined);
    }
  }

  const titleOf = (id?: string | null) =>
    fixtures.find((item) => item.id === id)?.title ?? id ?? "—";

  const progress = job
    ? Math.round(
        ((stageIndex(job.stage) +
          (job.stage?.startsWith("executing_") &&
          job.current_step &&
          job.total_steps
            ? job.current_step / job.total_steps
            : 0)) /
          (STAGES.length - 1)) *
          100,
      )
    : 0;

  return (
    <main className="water-agent-page flex h-full min-h-0 w-full min-w-0 flex-1 flex-col bg-background md:flex-row">
      <nav
        aria-label="工作区分区"
        className="water-agent-nav flex shrink-0 gap-1 overflow-x-auto border-b border-border p-3 md:w-56 md:flex-col md:border-b-0 md:border-r md:p-4"
      >
        <div className="hidden px-2 pb-3 md:block">
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
            Agent Workspace
          </p>
          <p className="mt-1 text-sm font-semibold">Water Change</p>
        </div>
        {SECTIONS.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setSection(item.id)}
            aria-current={section === item.id ? "page" : undefined}
            className={
              "water-agent-nav-item shrink-0 rounded-xl px-3 py-2 text-left " +
              (section === item.id ? "water-agent-nav-item-active" : "")
            }
          >
            <span className="block text-sm font-medium">{item.label}</span>
            <span className="hidden text-xs text-muted-foreground md:block">
              {item.hint}
            </span>
          </button>
        ))}
      </nav>
      <div className="min-h-0 min-w-0 flex-1 overflow-y-auto px-4 py-6 md:px-6 md:py-8">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
          {section === "flows" && <FlowsPanel fixtures={fixtures} />}
          {section === "tools" && <ToolsPanel />}
          {section === "data" && (
            <DataPanel
              fixtures={fixtures}
              busy={status === "running"}
              onUploaded={(id) => refreshFixtures(id)}
              onUse={(id) => {
                setFixtureId(id);
                setSection("chat");
              }}
            />
          )}
          {section === "history" && (
            <HistoryPanel
              history={history}
              onRefresh={() => void refreshHistory()}
              onOpen={(runId) => {
                setSection("chat");
                void loadRun(runId);
              }}
            />
          )}
          {section === "chat" && (
            <>
              <header className="water-agent-hero rounded-2xl border border-border bg-card p-6 shadow-sm">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <p className="mb-2 text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
                      Agent Workspace · Water Change
                    </p>
                    <h1 className="text-2xl font-semibold tracking-tight">
                      水体变化分析
                    </h1>
                    <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
                      用自然语言描述要分析的区域和问题。Agent
                      会核对任务里提到的数据，规划并执行工具，每一步都保留可复查的证据；上一轮对话会作为下一轮的上下文。
                    </p>
                  </div>
                  <span className="water-agent-status rounded-full border px-3 py-1 text-xs font-medium">
                    {statusLabel(status)}
                  </span>
                </div>
                <form onSubmit={submit} className="mt-6">
                  <div className="space-y-4">
                    <div>
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
                        placeholder="例如：看看希腊色萨利 2023 年 9 月那次洪水，新增了多少水面？"
                      />
                    </div>
                    <DatasetPicker
                      fixtures={fixtures}
                      value={fixtureId}
                      disabled={status === "running"}
                      onChange={(id) => {
                        setFixtureId(id);
                        setPendingChoice(null);
                      }}
                    />
                  </div>
                  <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                    <p className="max-w-3xl text-xs leading-5 text-muted-foreground">
                      {selectedFixture?.category === "observed"
                        ? "卫星观测数据。水体由后向散射阈值估计得到，不是参考标注；前后差异本身不能证明洪水成因。"
                        : selectedFixture?.category === "uploaded" ||
                            selectedFixture?.category === "test"
                          ? "上传的数据，来源、时间和标签未经系统核实。"
                          : "合成测试栅格，只用于检查工具链，结果没有实际意义。"}
                    </p>
                    <div className="flex flex-wrap items-center gap-3">
                      {selectedFixture?.has_mismatch && (
                        <label
                          className="flex items-center gap-2 text-xs text-muted-foreground"
                          title="用这组数据附带的、故意做成尺寸不一致的样本再跑一次网格检查，确认门控会拦截"
                        >
                          <input
                            type="checkbox"
                            checked={selfCheck}
                            onChange={(event) =>
                              setSelfCheck(event.target.checked)
                            }
                            disabled={status === "running"}
                          />
                          同时运行门控自检
                        </label>
                      )}
                      <Button
                        type="submit"
                        disabled={
                          status === "running" || resolving || !prompt.trim()
                        }
                      >
                        {resolving
                          ? "正在核对数据…"
                          : status === "running"
                            ? "执行中…"
                            : "运行"}
                      </Button>
                    </div>
                  </div>
                </form>
                {pendingChoice && (
                  <div className="water-agent-rejection mt-5 rounded-xl p-4 text-sm leading-6">
                    {pendingChoice.resolution.input_set ? (
                      <p>
                        任务中提到「{pendingChoice.resolution.mentioned}
                        」，对应数据「
                        {titleOf(pendingChoice.resolution.input_set)}
                        」；当前选择的是「
                        {titleOf(fixtureId)}」。
                      </p>
                    ) : (
                      <p>
                        任务中提到的「{pendingChoice.resolution.mentioned}
                        」不在现有数据中。可以先到“数据”里上传对应影像，或者用当前选择的「
                        {titleOf(fixtureId)}」继续。
                      </p>
                    )}
                    <div className="mt-3 flex flex-wrap gap-2">
                      {pendingChoice.resolution.input_set && (
                        <Button
                          type="button"
                          onClick={() =>
                            void startRun(
                              pendingChoice.prompt,
                              pendingChoice.resolution.input_set!,
                              {
                                ...pendingChoice.resolution,
                                decision: "switched",
                              },
                            )
                          }
                        >
                          改用「{titleOf(pendingChoice.resolution.input_set)}
                          」并运行
                        </Button>
                      )}
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() =>
                          void startRun(pendingChoice.prompt, fixtureId, {
                            ...pendingChoice.resolution,
                            decision: "kept_current",
                          })
                        }
                      >
                        仍用当前数据运行
                      </Button>
                      {!pendingChoice.resolution.input_set && (
                        <Button
                          type="button"
                          variant="outline"
                          onClick={() => {
                            setPendingChoice(null);
                            setSection("data");
                          }}
                        >
                          去上传数据
                        </Button>
                      )}
                      <Button
                        type="button"
                        variant="ghost"
                        onClick={() => setPendingChoice(null)}
                      >
                        取消
                      </Button>
                    </div>
                  </div>
                )}
                {status === "running" && job && (
                  <div className="water-agent-progress mt-5 rounded-xl border border-border bg-muted/30 p-4">
                    <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                      <span className="font-medium">
                        {job.message || "任务处理中"}
                      </span>
                      <span className="font-mono text-xs text-muted-foreground">
                        {progress}%
                      </span>
                    </div>
                    <div className="mt-3 h-2 overflow-hidden rounded-full bg-muted">
                      <div
                        className="water-agent-progress-bar h-full rounded-full"
                        style={{ width: Math.max(5, progress) + "%" }}
                      />
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {STAGES.map((stage) => (
                        <span
                          key={stage.id}
                          className={
                            "water-agent-stage rounded-full px-2.5 py-1 text-xs " +
                            (stageIndex(job.stage) >= stageIndex(stage.id)
                              ? "water-agent-stage-active"
                              : "")
                          }
                        >
                          {stage.label}
                        </span>
                      ))}
                    </div>
                    {job.tool && (
                      <p className="mt-3 text-xs text-muted-foreground">
                        当前工具：
                        <span className="font-mono">
                          {displayTool(job.tool)}
                        </span>
                        {job.current_step && job.total_steps
                          ? "（" +
                            job.current_step +
                            "/" +
                            job.total_steps +
                            "）"
                          : ""}
                      </p>
                    )}
                  </div>
                )}
              </header>

              {messages.length > 0 && (
                <section className="rounded-2xl border border-border bg-card p-5">
                  <div className="flex items-center justify-between gap-3">
                    <h2 className="font-semibold">对话记录</h2>
                    <span className="text-xs text-muted-foreground">
                      {messages.length} 条消息
                    </span>
                  </div>
                  <div className="mt-4 space-y-3">
                    {messages.map((message) => (
                      <div
                        key={message.id}
                        className={
                          "water-agent-message " +
                          (message.role === "user"
                            ? "water-agent-message-user"
                            : "")
                        }
                      >
                        <span className="text-xs font-medium text-muted-foreground">
                          {message.role === "user" ? "你" : "Agent"}
                        </span>
                        <p className="mt-1 whitespace-pre-wrap text-sm leading-6">
                          {message.content}
                        </p>
                        {message.runId && (
                          <button
                            type="button"
                            className="mt-2 text-xs text-primary underline"
                            onClick={() => void loadRun(message.runId!)}
                          >
                            查看这次运行
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                </section>
              )}

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
                      <button
                        type="button"
                        className="mt-2 text-xs text-primary underline"
                        onClick={() => void openCanvas(result.run_id)}
                      >
                        在 Canvas 中打开
                      </button>
                      {canvasState && (
                        <p className="mt-1 text-xs text-muted-foreground">
                          {canvasState}
                        </p>
                      )}
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
                      <p className="text-xs text-muted-foreground">
                        可追溯记录
                      </p>
                      <p className="mt-2 font-medium">
                        {result.analytics?.matched_count ?? 0} /{" "}
                        {result.analytics?.expected_count ?? 0}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        execution history
                      </p>
                    </div>
                  </section>
                  {result.success_chain?.status === "blocked_by_gate" && (
                    <section className="water-agent-rejection rounded-xl p-4 text-sm leading-6">
                      <p className="font-medium">分析在门控处被拦截</p>
                      <p className="mt-1 text-muted-foreground">
                        {displayTool(
                          result.success_chain.blocked_tool ?? "gate",
                        )}{" "}
                        返回 {result.success_chain.reason_code ?? "未知原因"}
                        ，后续工具没有执行。
                        {result.success_chain.failed_checks?.length
                          ? "检查项：" +
                            result.success_chain.failed_checks.join("、")
                          : ""}
                      </p>
                    </section>
                  )}
                  {result.fixtures?.before && (
                    <section className="rounded-2xl border border-border bg-card p-5">
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <h2 className="font-semibold">影像与水体</h2>
                        <p className="text-xs text-muted-foreground">
                          蓝色为水体，灰色为非水体，透明为无效像素
                          {result.fixtures?.input_kind === "sar_backscatter"
                            ? "；水体由阈值估计得到"
                            : ""}
                        </p>
                      </div>
                      <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
                        {(result.fixtures?.input_kind === "sar_backscatter"
                          ? [
                              ["before", "前期影像"],
                              ["after", "后期影像"],
                              ["before_mask", "前期水体"],
                              ["after_mask", "后期水体"],
                            ]
                          : [
                              ["before", "前期水体标签"],
                              ["after", "后期水体标签"],
                            ]
                        ).map(([name, caption]) => (
                          <figure key={name} className="min-w-0">
                            <div className="water-agent-thumb aspect-square overflow-hidden rounded-xl">
                              <img
                                src={
                                  "/api/water-demo/runs/" +
                                  encodeURIComponent(result.run_id) +
                                  "/preview/" +
                                  name
                                }
                                alt={caption}
                                loading="lazy"
                                className="h-full w-full object-cover [image-rendering:pixelated]"
                              />
                            </div>
                            <figcaption className="mt-1 text-center text-xs text-muted-foreground">
                              {caption}
                            </figcaption>
                          </figure>
                        ))}
                      </div>
                    </section>
                  )}
                  {stats && (
                    <section className="rounded-2xl border border-border bg-card p-5">
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <h2 className="font-semibold">水体统计</h2>
                        <p className="text-xs text-muted-foreground">
                          只统计两期都有效的像素；单像素面积{" "}
                          {stats.pixel_area_m2 ?? "—"} m²（{stats.crs ?? "—"}）
                          {typeof result.success_chain?.derived_labels
                            ?.threshold === "number"
                            ? "；水体阈值 " +
                              result.success_chain.derived_labels.threshold.toFixed(
                                2,
                              ) +
                              " " +
                              (result.success_chain.derived_labels.units ??
                                "") +
                              "（预设 " +
                              (result.success_chain.derived_labels.preset ??
                                "—") +
                              "：后期影像 Otsu，前期沿用同一阈值）"
                            : ""}
                        </p>
                      </div>
                      <div className="mt-4 grid gap-3 md:grid-cols-5">
                        {[
                          ["有效面积", stats.valid_area_m2],
                          ["前期水体", stats.before_water_area_m2],
                          ["后期水体", stats.after_water_area_m2],
                          ["新增水体", stats.new_water_area_m2],
                          ["消退水体", stats.receded_water_area_m2],
                        ].map(([name, value]) => (
                          <div
                            key={name as string}
                            className="rounded-xl bg-muted/50 p-3"
                          >
                            <p className="text-xs text-muted-foreground">
                              {name}
                            </p>
                            <p className="mt-1 font-medium">
                              {km2(value as number | undefined)}
                            </p>
                          </div>
                        ))}
                      </div>
                    </section>
                  )}
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
                        {allSteps.map((step) => {
                          const key =
                            step.chain + "-" + (step.execution_id ?? step.step);
                          const detail = evidenceByExecution.get(
                            step.execution_id ?? step.chain + "-" + step.step,
                          );
                          const isOpen = expandedStep === key;
                          return (
                            <div
                              key={key}
                              className="py-3 first:pt-0 last:pb-0"
                            >
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="text-xs text-muted-foreground">
                                  {step.chain}
                                </span>
                                <span className="rounded bg-muted px-2 py-0.5 font-mono text-xs">
                                  {displayTool(step.tool_slug)}
                                  {targetLabel(step.target)}
                                </span>
                                <span
                                  className={
                                    "rounded-full px-2 py-0.5 text-xs " +
                                    (step.success && !step.execution_blocked
                                      ? "water-agent-step-success"
                                      : "water-agent-step-blocked")
                                  }
                                >
                                  {step.execution_blocked
                                    ? "已拦截：" + (step.reason_code ?? "gate")
                                    : step.success
                                      ? "完成"
                                      : "失败"}
                                </span>
                                <button
                                  type="button"
                                  className="water-agent-detail-button ml-auto text-xs underline"
                                  onClick={() =>
                                    setExpandedStep(isOpen ? null : key)
                                  }
                                >
                                  {isOpen ? "收起证据" : "查看证据"}
                                </button>
                              </div>
                              <div className="mt-2 grid gap-1 text-xs text-muted-foreground md:grid-cols-2">
                                <span>
                                  execution: {step.execution_id ?? "—"}
                                </span>
                                <span>trace: {step.trace_id ?? "—"}</span>
                                {step.manifest_path && (
                                  <span className="break-all md:col-span-2">
                                    manifest: {step.manifest_path}
                                  </span>
                                )}
                              </div>
                              {isOpen && (
                                <div className="water-agent-evidence mt-3 space-y-3 rounded-xl p-3">
                                  <div>
                                    <p className="text-xs font-medium">输入</p>
                                    <pre className="mt-1 overflow-x-auto text-[11px] leading-5">
                                      {formatJson(
                                        detail?.inputs ?? step.inputs,
                                      )}
                                    </pre>
                                  </div>
                                  <div>
                                    <p className="text-xs font-medium">
                                      工具输出摘要
                                    </p>
                                    <pre className="mt-1 overflow-x-auto text-[11px] leading-5">
                                      {formatJson(
                                        detail?.outputs ?? step.outputs,
                                      )}
                                    </pre>
                                  </div>
                                  {detail?.manifest && (
                                    <div>
                                      <p className="text-xs font-medium">
                                        manifest 内容
                                      </p>
                                      <pre className="mt-1 max-h-64 overflow-auto text-[11px] leading-5">
                                        {formatJson(detail.manifest)}
                                      </pre>
                                    </div>
                                  )}
                                  {step.failed_checks && (
                                    <p className="text-xs text-muted-foreground">
                                      失败检查：{step.failed_checks.join("、")}
                                    </p>
                                  )}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                    <div className="rounded-2xl border border-border bg-card p-5">
                      <h2 className="font-semibold">Agent 回应</h2>
                      <p className="mt-3 text-sm leading-6">
                        {result.plan?.reason ||
                          "模型返回了可执行的工具顺序，页面保留原始执行证据供复核。"}
                      </p>
                      {result.rejection_chain?.status ===
                        "deterministic_refusal" && (
                        <div className="water-agent-rejection mt-5 rounded-xl p-4 text-sm leading-6">
                          <p className="font-medium">门控自检</p>
                          <p className="mt-1 text-muted-foreground">
                            用故意做成尺寸不一致的样本再做一次网格检查，门控按预期拦截（
                            {result.rejection_chain.reason_code}
                            ），后续工具没有执行。
                          </p>
                          {result.rejection_chain.failed_checks && (
                            <p className="mt-2 text-xs text-muted-foreground">
                              检查项：
                              {result.rejection_chain.failed_checks.join("、")}
                            </p>
                          )}
                        </div>
                      )}
                      <div className="mt-4 space-y-1 rounded-xl bg-muted/60 p-4 text-xs leading-5 text-muted-foreground">
                        <p className="font-medium text-foreground">
                          数据：
                          {result.fixtures?.title ??
                            result.fixtures?.label ??
                            "—"}
                        </p>
                        {result.fixtures?.event && (
                          <p>事件：{result.fixtures.event}</p>
                        )}
                        {sceneLine("前期", result.fixtures?.scenes?.before) && (
                          <p>
                            {sceneLine("前期", result.fixtures?.scenes?.before)}
                          </p>
                        )}
                        {sceneLine("后期", result.fixtures?.scenes?.after) && (
                          <p>
                            {sceneLine("后期", result.fixtures?.scenes?.after)}
                          </p>
                        )}
                        <p>
                          {result.fixtures?.claim_boundary ||
                            "结果仅表示当前工程输入上的工具执行记录。"}
                        </p>
                      </div>
                    </div>
                  </section>
                </>
              )}

              {!result && !error && messages.length === 0 && (
                <section className="rounded-2xl border border-dashed border-border bg-card/50 px-6 py-12 text-center">
                  <p className="text-sm font-medium">
                    提交任务后，这里会显示规划、阶段进度和证据记录
                  </p>
                  <p className="mt-2 text-xs text-muted-foreground">
                    可以继续追问上一轮结果，也可以在“运行记录”中打开之前的运行。
                  </p>
                </section>
              )}
            </>
          )}
        </div>
      </div>
    </main>
  );
}

import {
  type KeyboardEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import ReactMarkdown from "react-markdown";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/button";
import { FlowCard } from "./AssistantFlow";
import {
  API,
  apiFetch,
  apiJson,
  displayTool,
  postJson,
  readEvents,
  useCatalog,
} from "./api";
import ItemPicker from "./ItemPicker";
import ResultView from "./ResultView";
import type {
  CatalogItem,
  ChatMessage,
  Conversation,
  FlowGraph,
  PlanInput,
  Scenario,
  StepEvent,
} from "./types";
import { type Live, routeToAssistant, useWorkspace } from "./workspaceStore";

// Plans written before input slots carry only the runner's input kind.
const LEGACY_CAPABILITY: Record<string, string> = {
  sar_backscatter: "sar_vv",
  water_labels: "water_mask",
  optical_reflectance: "optical_green_nir",
};
const MATCHED_BY: Record<string, string> = {
  attachment: "你添加的数据",
  message: "任务中提到的地点",
  previous_run: "上一次运行用的数据",
  keyword: "任务中要求的测试数据",
};
const TARGET_LABELS: Record<string, string> = {
  before: "前期",
  after: "后期",
  scene: "本景",
};
const MAX_ATTACHMENTS = 4;

function Markdown({ text }: { text: string }) {
  return (
    <div className="ws-markdown text-sm leading-6">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

function Warnings({ items }: { items?: string[] }) {
  if (!items?.length) return null;
  return (
    <p className="ws-warning mt-2 rounded-lg px-2 py-1 text-xs">
      {items.join("；")}（以结果卡片中的数值为准）
    </p>
  );
}

function StepList({ steps }: { steps: StepEvent[] }) {
  if (!steps.length) return null;
  return (
    <ol className="ws-steps mt-3 space-y-1">
      {steps.map((step) => (
        <li
          key={step.id ?? step.tool + step.target}
          className="flex flex-wrap items-center gap-2 text-xs"
        >
          <span
            className={
              "inline-block h-2 w-2 rounded-full " +
              (step.state === "running"
                ? "ws-dot-running"
                : step.state === "done"
                  ? "ws-dot-done"
                  : "ws-dot-blocked")
            }
          />
          <span className="text-muted-foreground">
            {step.chain === "self_check" ? "自检" : "分析"}
          </span>
          <span className="font-mono">
            {displayTool(step.tool)}
            {step.target
              ? "（" + (TARGET_LABELS[step.target] ?? step.target) + "）"
              : ""}
          </span>
          <span className="text-muted-foreground">
            {step.state === "running"
              ? "执行中…"
              : step.state === "done"
                ? Object.entries(step.outputs ?? {})
                    .filter(([key]) => key !== "reason_code")
                    .slice(0, 3)
                    .map(
                      ([key, value]) =>
                        key +
                        "=" +
                        (typeof value === "number"
                          ? Number(value.toFixed(3))
                          : value),
                    )
                    .join("  ")
                : "拦截：" + (step.reason_code ?? step.state)}
          </span>
        </li>
      ))}
    </ol>
  );
}

function PlanInputRow({
  input,
  value,
  options,
  editable,
  onChange,
}: {
  input: PlanInput;
  value: string;
  options: CatalogItem[];
  editable: boolean;
  onChange: (sceneId: string) => void;
}) {
  const chosen = options.find((item) => item.id === value);
  const preview = chosen?.preview ?? input.preview;
  return (
    <div className="flex items-center gap-3 rounded-xl border border-border bg-background p-2">
      <div className="ws-thumb h-14 w-14 shrink-0 overflow-hidden rounded-md">
        {preview && (
          <img
            src={preview}
            alt={input.label}
            className="h-full w-full object-cover [image-rendering:pixelated]"
          />
        )}
      </div>
      <div className="min-w-0 flex-1 text-xs">
        <p className="font-medium">{input.label}</p>
        {editable && options.length > 1 ? (
          <select
            aria-label={"更换" + input.label}
            value={value}
            onChange={(event) => onChange(event.target.value)}
            className="mt-1 w-full rounded-lg border border-input bg-background px-2 py-1"
          >
            {options.map((item) => (
              <option key={item.id} value={item.id}>
                {item.collection_title} · {item.date ?? item.title}
              </option>
            ))}
          </select>
        ) : (
          <p className="mt-0.5 truncate text-muted-foreground">
            {(chosen?.collection_title ?? input.collection_title) +
              " · " +
              (chosen?.date ?? input.acquired ?? input.scene_title)}
          </p>
        )}
      </div>
    </div>
  );
}

function PlanCard({
  message,
  live,
  busy,
  items,
  onExecute,
  onCancel,
}: {
  message: ChatMessage;
  live?: Live;
  busy: boolean;
  items: CatalogItem[];
  onExecute: (selfCheck: boolean, inputs?: Record<string, string>) => void;
  onCancel: () => void;
}) {
  const [selfCheck, setSelfCheck] = useState(false);
  const plan = message.plan!;
  const status = live ? "running" : message.status;
  const [roles, setRoles] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      (plan.inputs ?? []).map((input) => [input.role, input.scene_id]),
    ),
  );
  // Swapping is limited to data that fills the slot the same way: the tool order depends on it.
  const optionsFor = (input: PlanInput) =>
    items.filter(
      (item) =>
        !item.self_check_for &&
        item.capabilities.includes(
          input.capability ?? LEGACY_CAPABILITY[plan.input_kind] ?? "",
        ) &&
        (item.audience === "user" || Object.values(roles).includes(item.id)),
    );
  const chosen = Object.values(roles);
  const duplicate = new Set(chosen).size < chosen.length;
  return (
    <div className="ws-plan rounded-2xl border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">
          计划 · {plan.scenario_title}
        </span>
        <span className="rounded-full bg-muted px-2 py-0.5 text-xs">
          {
            {
              pending: "待确认",
              running: "执行中",
              done: "已完成",
              failed: "失败",
              cancelled: "已取消",
            }[status ?? "pending"]
          }
        </span>
      </div>
      {plan.inputs ? (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {plan.inputs.map((input) => (
            <PlanInputRow
              key={input.role}
              input={input}
              value={roles[input.role]}
              options={optionsFor(input)}
              editable={status === "pending"}
              onChange={(sceneId) =>
                setRoles((current) => ({ ...current, [input.role]: sceneId }))
              }
            />
          ))}
        </div>
      ) : (
        <p className="mt-2 text-xs text-muted-foreground">
          数据：{plan.input_title}（旧版计划，请重新提问以生成可执行的计划）
        </p>
      )}
      {(plan.matched_by || plan.inputs_changed_by_user) && (
        <p className="mt-2 text-xs text-muted-foreground">
          {[
            plan.matched_by
              ? "数据来自" + (MATCHED_BY[plan.matched_by] ?? plan.matched_by)
              : null,
            plan.inputs_changed_by_user ? "执行前由你更换过" : null,
          ]
            .filter(Boolean)
            .join("，")}
        </p>
      )}
      {plan.notes?.map((note) => (
        <p key={note} className="ws-warning mt-2 rounded-lg px-2 py-1 text-xs">
          {note}
        </p>
      ))}
      {duplicate && (
        <p className="mt-2 text-xs text-destructive">
          同一景不能同时作为两个输入。
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-1 text-xs">
        {plan.execution_order.map((slug, index) => (
          <span key={slug} className="flex items-center gap-1">
            {index > 0 && <span className="text-muted-foreground">→</span>}
            <span className="rounded bg-muted px-1.5 py-0.5 font-mono">
              {displayTool(slug)}
            </span>
          </span>
        ))}
      </div>
      {plan.reason && <p className="mt-2 text-sm leading-6">{plan.reason}</p>}
      {status === "pending" && (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button
            type="button"
            disabled={busy || duplicate || !plan.inputs}
            onClick={() => onExecute(selfCheck, roles)}
          >
            执行
          </Button>
          <Button
            type="button"
            variant="ghost"
            disabled={busy}
            onClick={onCancel}
          >
            取消
          </Button>
          {plan.self_check_available && (
            <label
              className="flex items-center gap-2 text-xs text-muted-foreground"
              title="用一份故意与后期影像尺寸不一致的样本再做一次网格检查，确认门控会拦截"
            >
              <input
                type="checkbox"
                checked={selfCheck}
                onChange={(event) => setSelfCheck(event.target.checked)}
              />
              同时运行门控自检
            </label>
          )}
          <span className="text-xs text-muted-foreground">
            规划模型：{plan.model}
          </span>
        </div>
      )}
      {live && (
        <div className="mt-3 border-t border-border pt-3">
          <p className="text-xs text-muted-foreground">{live.status}</p>
          <StepList steps={live.steps} />
          {live.result && (
            <div className="mt-4">
              <ResultView
                runId={live.result.run_id}
                result={live.result.result}
              />
            </div>
          )}
          {live.text && (
            <div className="mt-3">
              <Markdown text={live.text} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function MessageView({
  message,
  live,
  busy,
  items,
  onExecute,
  onCancel,
  onRunFlow,
  onHandoff,
}: {
  message: ChatMessage;
  live?: Live;
  busy: boolean;
  items: CatalogItem[];
  onExecute: (
    id: string,
    selfCheck: boolean,
    inputs?: Record<string, string>,
  ) => void;
  onCancel: (id: string) => void;
  onRunFlow: (id: string) => void;
  onHandoff?: () => void;
}) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="ws-bubble-user max-w-[80%] rounded-2xl px-4 py-2 text-sm leading-6">
          <p className="whitespace-pre-wrap">{message.content}</p>
          {message.attachments?.length ? (
            <p className="mt-1 text-xs opacity-80">
              附加数据：{message.attachments.length} 份
            </p>
          ) : null}
        </div>
      </div>
    );
  }
  if (message.kind === "flow") {
    return (
      <div className="space-y-2">
        {message.content && (
          <div className="max-w-[90%]">
            <Markdown text={message.content} />
          </div>
        )}
        <FlowCard
          message={message}
          busy={busy}
          running={Boolean(live)}
          onRun={() => onRunFlow(message.id)}
        />
        {live && (
          <div className="rounded-2xl border border-border bg-card p-3">
            <p className="text-xs text-muted-foreground">{live.status}</p>
            <StepList steps={live.steps} />
          </div>
        )}
      </div>
    );
  }
  if (message.kind === "plan") {
    return (
      <PlanCard
        message={message}
        live={live}
        busy={busy}
        items={items}
        onExecute={(selfCheck, inputs) =>
          onExecute(message.id, selfCheck, inputs)
        }
        onCancel={() => onCancel(message.id)}
      />
    );
  }
  if (message.kind === "run" && message.result && message.run_id) {
    return (
      <div className="rounded-2xl border border-border bg-card p-4">
        <p className="mb-3 text-xs text-muted-foreground">
          运行结果 · {message.input_title} · {message.model}
        </p>
        <ResultView runId={message.run_id} result={message.result} />
        {message.content && (
          <div className="mt-4 border-t border-border pt-3">
            <p className="mb-1 text-xs text-muted-foreground">Agent 解读</p>
            <Markdown text={message.content} />
            <Warnings items={message.warnings} />
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="max-w-[95%]">
      <Markdown text={message.content ?? ""} />
      <Warnings items={message.warnings} />
      {onHandoff && (
        <button
          type="button"
          className="ws-handoff mt-2 rounded-lg px-2.5 py-1 text-xs"
          onClick={onHandoff}
          title="没有合适的已验证场景时，可以让流程助手搭建一个工作流"
        >
          交给流程助手搭建 →
        </button>
      )}
    </div>
  );
}

export default function ChatPage() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { items, collections } = useCatalog();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [input, setInput] = useState("");
  // The data page opens the chat with ?attach=<scene id>,<scene id>.
  const [attachments, setAttachments] = useState<string[]>(() =>
    (searchParams.get("attach") ?? "")
      .split(",")
      .filter(Boolean)
      .slice(0, MAX_ATTACHMENTS),
  );
  const [picker, setPicker] = useState(false);
  const [draft, setDraft] = useState<Live | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const ws = useWorkspace();
  const { mirror, setView, focusResult, setActions } = ws;
  const [error, setError] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const busy = Boolean(draft || live);

  useEffect(() => {
    mirror({ conversationId, messages, draft, live });
  }, [mirror, conversationId, messages, draft, live]);

  // Requests from the views: data to attach, text to prefill.
  useEffect(() => {
    if (!ws.attachRequest.length) return;
    setAttachments((current) =>
      Array.from(new Set(current.concat(ws.attachRequest))).slice(
        -MAX_ATTACHMENTS,
      ),
    );
    ws.requestAttach([]);
  }, [ws.attachRequest, ws.requestAttach]);
  useEffect(() => {
    if (ws.prefill === null) return;
    setInput(ws.prefill);
    ws.requestPrefill(null);
  }, [ws.prefill, ws.requestPrefill]);

  const loadList = useCallback(async () => {
    const body = await apiJson<{ conversations: Conversation[] }>(
      API + "/conversations",
    );
    setConversations(body.conversations);
  }, []);

  useEffect(() => {
    void loadList().catch(() => undefined);
    void apiJson<{ scenarios: Scenario[] }>(API + "/scenarios")
      .then((body) => setScenarios(body.scenarios))
      .catch(() => undefined);
  }, [loadList]);

  useEffect(() => {
    setError("");
    if (!conversationId) {
      setMessages([]);
      return;
    }
    void apiJson<Conversation>(API + "/conversations/" + conversationId)
      .then((body) => setMessages(body.messages ?? []))
      .catch((cause) =>
        setError(cause instanceof Error ? cause.message : "会话无法读取。"),
      );
  }, [conversationId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, draft, live]);

  function upsert(message: ChatMessage) {
    if (
      message.role === "assistant" &&
      (message.kind === "plan" || message.kind === "flow")
    )
      setView("flow");
    if (message.kind === "run" && message.result) {
      focusResult(message.id);
      // Let the finished nodes show for a moment before the map takes over.
      setTimeout(() => setView("data"), 1200);
    }
    setMessages((current) => {
      const index = current.findIndex((item) => item.id === message.id);
      if (index < 0) return current.concat(message);
      const next = current.slice();
      next[index] = message;
      return next;
    });
  }

  /** Poll the conversation until an assistant message follows `afterId` (up to 30 min). */
  async function waitForReply(id: string, afterId: string) {
    setDraft((current) => ({
      status: "连接中断，流程助手仍在服务器上运行，正在等待结果…",
      text: current?.text ?? "",
      steps: [],
      graph: current?.graph,
    }));
    for (let attempt = 0; attempt < 600; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, 3000));
      try {
        const body = await apiJson<Conversation>(API + "/conversations/" + id);
        const list = body.messages ?? [];
        const index = list.findIndex((item) => item.id === afterId);
        if (
          index >= 0 &&
          list.slice(index + 1).some((m) => m.role === "assistant")
        ) {
          setMessages(list);
          return true;
        }
      } catch {
        // keep waiting through transient errors
      }
    }
    return false;
  }

  async function send(text?: string, options?: { assistant?: boolean }) {
    const content = (text ?? input).trim();
    if (!content || busy) return;
    const toAssistant =
      options?.assistant ?? routeToAssistant(content, messages);
    if (toAssistant) setView("flow");
    setError("");
    let id = conversationId;
    if (!id) {
      const created = await apiJson<Conversation>(API + "/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      id = created.id;
      navigate("/workspace/c/" + id, { replace: true });
    }
    const sentAttachments = attachments;
    setInput("");
    setAttachments([]);
    setPicker(false);
    setDraft({ status: "正在发送…", text: "", steps: [] });
    let sentId = "";
    let replied = false;
    try {
      const response = await postJson(
        API +
          "/conversations/" +
          id +
          (toAssistant ? "/assistant" : "/messages"),
        {
          content,
          attachments: sentAttachments,
        },
      );
      await readEvents(response, (event, data) => {
        if (event === "flow")
          setDraft(
            (current) =>
              current && {
                ...current,
                graph: (data as unknown as { graph: FlowGraph }).graph,
              },
          );
        else if (event === "status")
          setDraft(
            (current) =>
              current && {
                ...current,
                status: data.text ?? "",
                // Heartbeats repeat the latest line with elapsed time; keep one entry per step.
                log: (current.log ?? [])
                  .filter(
                    (line) =>
                      !(data.text ?? "").startsWith(line.replace(/….*$/, "")),
                  )
                  .concat(data.text ?? "")
                  .slice(-14),
              },
          );
        else if (event === "token")
          setDraft(
            (current) =>
              current && { ...current, text: current.text + data.text },
          );
        else if (event === "error")
          setError(String(data.text ?? "流程助手出错。"));
        else if (event === "message") {
          const message = data as unknown as ChatMessage;
          upsert(message);
          if (message.role === "user") sentId = message.id;
          if (message.role === "assistant") {
            replied = true;
            setDraft(null);
          }
        }
      });
    } catch (cause) {
      // The flow assistant keeps running on the server and saves its reply
      // even when the stream breaks (seen in Firefox); wait for it instead.
      if (toAssistant && sentId) {
        replied = await waitForReply(id, sentId);
        if (!replied) setError("流程助手长时间没有结果，请稍后刷新会话。");
      } else setError(cause instanceof Error ? cause.message : "发送失败。");
    } finally {
      setDraft(null);
      void loadList().catch(() => undefined);
    }
  }

  async function execute(
    planId: string,
    selfCheck: boolean,
    inputs?: Record<string, string>,
  ) {
    if (!conversationId || busy) return;
    setError("");
    setLive({ planId, status: "正在提交…", text: "", steps: [] });
    setView("flow");
    try {
      const response = await postJson(
        API +
          "/conversations/" +
          conversationId +
          "/plans/" +
          planId +
          "/execute",
        { self_check: selfCheck, inputs },
      );
      await readEvents(response, (event, data) => {
        setLive((current) => {
          if (!current) return current;
          if (event === "status")
            return { ...current, status: data.text ?? "" };
          if (event === "error")
            return { ...current, status: "出错：" + data.text };
          if (event === "token")
            return { ...current, text: current.text + data.text };
          if (event === "result")
            return {
              ...current,
              status: "已得到结果",
              result: data as unknown as Live["result"],
            };
          if (event === "step") {
            const step = data as unknown as StepEvent;
            const index = current.steps.findIndex(
              (item) => item.id === step.id,
            );
            const steps =
              index < 0
                ? current.steps.concat(step)
                : current.steps.map((item, i) => (i === index ? step : item));
            return { ...current, steps };
          }
          return current;
        });
        if (event === "message") upsert(data as unknown as ChatMessage);
      });
      const body = await apiJson<Conversation>(
        API + "/conversations/" + conversationId,
      );
      setMessages(body.messages ?? []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "执行失败。");
    } finally {
      setLive(null);
    }
  }

  async function runFlow(messageId: string) {
    if (!conversationId || busy) return;
    setError("");
    setLive({ planId: messageId, status: "正在提交…", text: "", steps: [] });
    setView("flow");
    try {
      const response = await postJson(
        API +
          "/conversations/" +
          conversationId +
          "/assistant-flows/" +
          messageId +
          "/run",
        {},
      );
      await readEvents(response, (event, data) => {
        setLive((current) => {
          if (!current) return current;
          if (event === "status")
            return { ...current, status: data.text ?? "" };
          if (event === "error")
            return { ...current, status: "出错：" + data.text };
          if (event === "step")
            return {
              ...current,
              steps: current.steps.concat(data as unknown as StepEvent),
            };
          return current;
        });
        if (event === "error") setError(String(data.text ?? "执行失败。"));
        if (event === "message") upsert(data as unknown as ChatMessage);
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "执行失败。");
    } finally {
      setLive(null);
    }
  }

  async function cancel(planId: string) {
    if (!conversationId) return;
    const response = await apiFetch(
      API + "/conversations/" + conversationId + "/plans/" + planId + "/cancel",
      {
        method: "POST",
      },
    );
    if (response.ok) upsert((await response.json()) as ChatMessage);
  }

  async function remove(id: string) {
    if (!window.confirm("删除这个对话？运行结果和 flow 不会被删除。")) return;
    await apiFetch(API + "/conversations/" + id, { method: "DELETE" });
    if (id === conversationId) navigate("/workspace");
    await loadList();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      void send();
    }
  }

  const attached = items.filter((item) => attachments.includes(item.id));

  // The views call back into this panel through the store; the ref keeps the latest closures.
  const handlers = useRef({ send, runFlow, execute });
  handlers.current = { send, runFlow, execute };
  useEffect(() => {
    setActions({
      send: (text, options) => void handlers.current.send(text, options),
      runFlow: (id) => void handlers.current.runFlow(id),
      execute: (id) => void handlers.current.execute(id, false),
    });
  }, [setActions]);

  const examples = scenarios
    .flatMap((scenario) =>
      scenario.examples.slice(0, 1).map((example) => [example, "已验证场景"]),
    )
    .slice(0, 3)
    .concat([
      [
        "搭建一个工作流：用 Sentinel-2 计算色萨利 9 月 10 日的 NDWI，阈值 0 提取水体并计算面积",
        "流程助手",
      ],
    ]);

  return (
    <div className="ws-panel flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 px-4 pb-2 pt-3">
        <select
          aria-label="切换对话"
          className="ws-conv-select min-w-0 flex-1 truncate rounded-lg bg-transparent py-1 text-sm font-semibold"
          value={conversationId ?? ""}
          onChange={(event) =>
            navigate(
              event.target.value
                ? "/workspace/c/" + event.target.value
                : "/workspace",
            )
          }
        >
          {!conversationId && <option value="">新对话</option>}
          {conversations.map((item) => (
            <option key={item.id} value={item.id}>
              {item.title || "新对话"}
            </option>
          ))}
        </select>
        {conversationId && (
          <button
            type="button"
            className="ws-ghost-btn rounded-lg px-2 py-1 text-xs"
            onClick={() => void remove(conversationId)}
            title="删除这个对话"
          >
            删除
          </button>
        )}
        <button
          type="button"
          className="ws-ghost-btn rounded-lg px-2.5 py-1 text-xs"
          onClick={() => navigate("/workspace")}
        >
          ＋ 新对话
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
        <div className="ws-msgs flex w-full flex-col gap-4">
          {messages.length === 0 && !draft && (
            <div className="pt-1">
              <p className="text-sm leading-6 text-muted-foreground">
                选好地点或数据后直接提问。
                <b className="text-foreground">已验证的场景</b>
                按模板生成工作流并经过科学门控；
                <b className="text-foreground">搭建或修改工作流</b>
                交给流程助手。两种都会出现在“工作流”视图里，运行结果回到“数据”视图。
              </p>
              <div className="mt-3 grid gap-2">
                {examples.map(([example, source]) => (
                  <button
                    key={example}
                    type="button"
                    className="ws-suggest rounded-xl p-2.5 text-left text-sm"
                    onClick={() => void send(example)}
                  >
                    {example}
                    <span className="mt-0.5 block text-[11px] text-muted-foreground">
                      {source}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((message, index) => {
            const previous = messages
              .slice(0, index)
              .reverse()
              .find((item) => item.role === "user");
            const handoff =
              message.role === "assistant" &&
              message.kind === "text" &&
              message.mode !== "assistant" &&
              previous?.content &&
              index === messages.length - 1
                ? () => void send(previous.content, { assistant: true })
                : undefined;
            return (
              <MessageView
                key={message.id}
                message={message}
                live={live && live.planId === message.id ? live : undefined}
                busy={busy}
                items={items}
                onExecute={(id, selfCheck, inputs) =>
                  void execute(id, selfCheck, inputs)
                }
                onCancel={(id) => void cancel(id)}
                onRunFlow={(id) => void runFlow(id)}
                onHandoff={handoff}
              />
            );
          })}
          {draft && (
            <div className="space-y-2">
              <p className="wv-status text-xs">
                <span className="ws-spinner" />
                {draft.status}
              </p>
              {draft.text && <Markdown text={draft.text} />}
            </div>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
          <div ref={bottom} />
        </div>
      </div>
      <div className="px-3 pb-3">
        {picker && (
          <div className="ws-picker mb-2 max-h-80 overflow-y-auto rounded-2xl border border-border bg-card p-3">
            <p className="mb-2 text-xs text-muted-foreground">
              选择要分析的数据（最多 {MAX_ATTACHMENTS}{" "}
              份），也可以在“数据”视图中选中后点“用于提问”。
            </p>
            <ItemPicker
              items={items}
              collections={collections}
              selected={attachments}
              onToggle={(item) =>
                setAttachments((current) =>
                  current.includes(item.id)
                    ? current.filter((id) => id !== item.id)
                    : current.concat(item.id).slice(-MAX_ATTACHMENTS),
                )
              }
            />
          </div>
        )}
        <div className="ws-composer rounded-2xl p-2.5">
          <textarea
            aria-label="输入消息"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            maxLength={1200}
            placeholder="描述地点、时间和问题，或说明要搭建、修改的工作流"
            className="w-full resize-none bg-transparent px-1 text-sm leading-6 outline-none"
          />
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            {attached.map((item) => (
              <span
                key={item.id}
                className="ws-chip flex items-center gap-1 rounded-md px-2 py-0.5 text-xs"
              >
                {item.collection_title.replace(/ · .*/, "")} ·{" "}
                {item.date ?? item.title}
                <button
                  type="button"
                  aria-label="移除数据"
                  onClick={() =>
                    setAttachments((current) =>
                      current.filter((id) => id !== item.id),
                    )
                  }
                >
                  ×
                </button>
              </span>
            ))}
            <button
              type="button"
              className="rounded-md px-1.5 py-0.5 text-xs text-primary hover:bg-muted"
              onClick={() => setPicker((value) => !value)}
            >
              ＋ 数据
            </button>
            <Button
              type="button"
              size="sm"
              className="ml-auto"
              disabled={busy || !input.trim()}
              onClick={() => void send()}
            >
              发送
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

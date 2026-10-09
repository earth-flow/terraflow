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
import {
  API,
  apiFetch,
  apiJson,
  displayTool,
  postJson,
  readEvents,
  useScenes,
} from "./api";
import ResultView from "./ResultView";
import ScenePicker from "./ScenePicker";
import type {
  ChatMessage,
  Conversation,
  GenericResult,
  PlanInput,
  Scenario,
  Scene,
  StepEvent,
} from "./types";

type Live = {
  planId?: string;
  status: string;
  text: string;
  steps: StepEvent[];
  result?: { run_id: string; result: GenericResult };
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
    <ol className="mt-3 space-y-1">
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
  options: Scene[];
  editable: boolean;
  onChange: (sceneId: string) => void;
}) {
  const chosen = options.find((scene) => scene.id === value);
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
            {options.map((scene) => (
              <option key={scene.id} value={scene.id}>
                {scene.collection_title} · {scene.acquired ?? scene.title}
              </option>
            ))}
          </select>
        ) : (
          <p className="mt-0.5 truncate text-muted-foreground">
            {(chosen?.collection_title ?? input.collection_title) +
              " · " +
              (chosen?.acquired ?? input.acquired ?? input.scene_title)}
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
  scenes,
  onExecute,
  onCancel,
}: {
  message: ChatMessage;
  live?: Live;
  busy: boolean;
  scenes: Scene[];
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
  // Swapping is limited to scenes of the same data kind: the tool order depends on it.
  const options = scenes.filter(
    (scene) =>
      !scene.self_check_for &&
      scene.input_kind === plan.input_kind &&
      (scene.input_scale ?? "linear") === (plan.input_scale ?? "linear") &&
      (scene.audience === "user" || Object.values(roles).includes(scene.id)),
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
              options={options}
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
  scenes,
  onExecute,
  onCancel,
}: {
  message: ChatMessage;
  live?: Live;
  busy: boolean;
  scenes: Scene[];
  onExecute: (
    id: string,
    selfCheck: boolean,
    inputs?: Record<string, string>,
  ) => void;
  onCancel: (id: string) => void;
}) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="ws-bubble-user max-w-[80%] rounded-2xl px-4 py-2 text-sm leading-6">
          <p className="whitespace-pre-wrap">{message.content}</p>
          {message.attachments?.length ? (
            <p className="mt-1 text-xs opacity-80">
              附加数据：{message.attachments.length} 景
            </p>
          ) : null}
        </div>
      </div>
    );
  }
  if (message.kind === "plan") {
    return (
      <PlanCard
        message={message}
        live={live}
        busy={busy}
        scenes={scenes}
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
    <div className="max-w-[90%]">
      <Markdown text={message.content ?? ""} />
      <Warnings items={message.warnings} />
    </div>
  );
}

export default function ChatPage() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { scenes, collections } = useScenes();
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
  const [error, setError] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const busy = Boolean(draft || live);

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
    setMessages((current) => {
      const index = current.findIndex((item) => item.id === message.id);
      if (index < 0) return current.concat(message);
      const next = current.slice();
      next[index] = message;
      return next;
    });
  }

  async function send(text?: string) {
    const content = (text ?? input).trim();
    if (!content || busy) return;
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
    try {
      const response = await postJson(
        API + "/conversations/" + id + "/messages",
        {
          content,
          attachments: sentAttachments,
        },
      );
      await readEvents(response, (event, data) => {
        if (event === "status")
          setDraft(
            (current) => current && { ...current, status: data.text ?? "" },
          );
        else if (event === "token")
          setDraft(
            (current) =>
              current && { ...current, text: current.text + data.text },
          );
        else if (event === "message") {
          upsert(data as unknown as ChatMessage);
          if ((data as unknown as ChatMessage).role === "assistant")
            setDraft(null);
        }
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "发送失败。");
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

  const attached = scenes.filter((item) => attachments.includes(item.id));

  return (
    <div className="flex h-full min-h-0 w-full min-w-0">
      <aside className="ws-sidebar hidden w-64 shrink-0 flex-col border-r border-border md:flex">
        <div className="p-3">
          <Button
            type="button"
            className="w-full"
            onClick={() => navigate("/workspace")}
          >
            ＋ 新对话
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
          {conversations.map((item) => (
            <div
              key={item.id}
              className={
                "group flex items-center gap-1 rounded-lg px-2 py-2 text-sm " +
                (item.id === conversationId
                  ? "ws-nav-item-active"
                  : "ws-nav-item")
              }
            >
              <button
                type="button"
                className="min-w-0 flex-1 truncate text-left"
                onClick={() => navigate("/workspace/c/" + item.id)}
              >
                {item.title || "新对话"}
              </button>
              <button
                type="button"
                className="hidden text-xs text-muted-foreground group-hover:block"
                aria-label="删除对话"
                onClick={() => void remove(item.id)}
              >
                删除
              </button>
            </div>
          ))}
        </div>
      </aside>
      <section className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6 md:px-8">
          <div className="mx-auto flex w-full max-w-4xl flex-col gap-5">
            {messages.length === 0 && !draft && (
              <div className="py-10 text-center">
                <h1 className="text-xl font-semibold">今天想分析什么？</h1>
                <p className="mt-2 text-sm text-muted-foreground">
                  描述地点、时间和问题；可以用“＋
                  添加数据”指定要分析的影像。需要运行时，Agent
                  会先给出计划请你确认。
                </p>
                <div className="mx-auto mt-6 grid max-w-2xl gap-3 sm:grid-cols-2">
                  {scenarios.flatMap((scenario) =>
                    scenario.examples.map((example) => (
                      <button
                        key={example}
                        type="button"
                        className="rounded-xl border border-border bg-card p-3 text-left text-sm hover:border-primary/50"
                        onClick={() => void send(example)}
                      >
                        <span className="block text-xs text-muted-foreground">
                          {scenario.title}
                        </span>
                        {example}
                      </button>
                    )),
                  )}
                  <button
                    type="button"
                    className="rounded-xl border border-border bg-card p-3 text-left text-sm hover:border-primary/50"
                    onClick={() =>
                      void send("你能做哪些分析？现在有哪些数据？")
                    }
                  >
                    <span className="block text-xs text-muted-foreground">
                      了解系统
                    </span>
                    你能做哪些分析？现在有哪些数据？
                  </button>
                </div>
              </div>
            )}
            {messages.map((message) => (
              <MessageView
                key={message.id}
                message={message}
                live={live && live.planId === message.id ? live : undefined}
                busy={busy}
                scenes={scenes}
                onExecute={(id, selfCheck, inputs) =>
                  void execute(id, selfCheck, inputs)
                }
                onCancel={(id) => void cancel(id)}
              />
            ))}
            {draft && (
              <div className="max-w-[90%]">
                <p className="text-xs text-muted-foreground">{draft.status}</p>
                {draft.text && <Markdown text={draft.text} />}
              </div>
            )}
            {error && <p className="text-sm text-destructive">{error}</p>}
            <div ref={bottom} />
          </div>
        </div>
        <div className="border-t border-border px-4 py-3 md:px-8">
          <div className="mx-auto w-full max-w-4xl">
            {picker && (
              <div className="mb-3 max-h-96 overflow-y-auto rounded-2xl border border-border bg-card p-3">
                <p className="mb-3 text-xs text-muted-foreground">
                  选择要分析的影像（最多 {MAX_ATTACHMENTS}{" "}
                  景）。比较变化选两景，提取水体选一景；用途由 Agent
                  在计划里指定，执行前可以更换。
                </p>
                <ScenePicker
                  scenes={scenes}
                  collections={collections}
                  selected={attachments}
                  onToggle={(scene) =>
                    setAttachments((current) =>
                      current.includes(scene.id)
                        ? current.filter((id) => id !== scene.id)
                        : current.concat(scene.id).slice(-MAX_ATTACHMENTS),
                    )
                  }
                />
              </div>
            )}
            {attached.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-2">
                {attached.map((item) => (
                  <span
                    key={item.id}
                    className="ws-chip flex items-center gap-1 rounded-full px-2 py-1 text-xs"
                  >
                    {item.collection_title} · {item.acquired ?? item.title}
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
              </div>
            )}
            <div className="ws-composer flex items-end gap-2 rounded-2xl border border-input bg-background p-2">
              <button
                type="button"
                className="shrink-0 rounded-lg px-2 py-2 text-sm text-primary hover:bg-muted"
                onClick={() => setPicker((value) => !value)}
              >
                ＋ 添加数据
              </button>
              <textarea
                aria-label="输入消息"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={onKeyDown}
                rows={2}
                maxLength={1200}
                placeholder="描述地点、时间和想分析的问题（Enter 发送，Shift+Enter 换行）"
                className="min-h-[2.5rem] flex-1 resize-none bg-transparent px-2 py-2 text-sm leading-6 outline-none"
              />
              <Button
                type="button"
                disabled={busy || !input.trim()}
                onClick={() => void send()}
              >
                发送
              </Button>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

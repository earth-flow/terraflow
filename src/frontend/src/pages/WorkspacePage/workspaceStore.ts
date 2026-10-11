import { create } from "zustand";
import type { ChatMessage, FlowGraph, GenericResult, StepEvent } from "./types";

export type View = "data" | "tools" | "flow";

/** A streaming turn: a plan or flow being executed, or a reply being written. */
export type Live = {
  planId?: string;
  status: string;
  text: string;
  steps: StepEvent[];
  result?: { run_id: string; result: GenericResult };
  /** Build-workflow requests: the flow as the assistant edits it. */
  graph?: FlowGraph;
  /** Status lines so far (the assistant's tool calls), newest last. */
  log?: string[];
};

type SendOptions = { assistant?: boolean };

/**
 * State shared by the assistant panel and the three views (data, tools,
 * workflow). The chat panel owns the conversation and mirrors it here; the
 * views read it and call back through `actions`.
 */
type WorkspaceState = {
  view: View;
  setView: (view: View) => void;
  conversationId?: string;
  messages: ChatMessage[];
  draft: Live | null;
  live: Live | null;
  mirror: (
    patch: Partial<
      Pick<WorkspaceState, "conversationId" | "messages" | "draft" | "live">
    >,
  ) => void;
  /** Catalog item shown on the data view. */
  selectedItem: string | null;
  selectItem: (id: string | null) => void;
  /** Run message whose result the data view shows. */
  resultFocus: string | null;
  focusResult: (id: string | null) => void;
  selectedTool: string | null;
  selectTool: (slug: string | null) => void;
  selectedNode: string | null;
  selectNode: (id: string | null) => void;
  /** Requests from the views to the chat panel, consumed once. */
  attachRequest: string[];
  requestAttach: (ids: string[]) => void;
  prefill: string | null;
  requestPrefill: (text: string | null) => void;
  actions: {
    send?: (text: string, options?: SendOptions) => void;
    runFlow?: (messageId: string) => void;
    execute?: (planId: string) => void;
  };
  setActions: (actions: WorkspaceState["actions"]) => void;
};

export const useWorkspace = create<WorkspaceState>((set) => ({
  view: "data",
  setView: (view) => set({ view }),
  messages: [],
  draft: null,
  live: null,
  mirror: (patch) => set(patch),
  selectedItem: null,
  selectItem: (selectedItem) => set({ selectedItem }),
  resultFocus: null,
  focusResult: (resultFocus) => set({ resultFocus }),
  selectedTool: null,
  selectTool: (selectedTool) => set({ selectedTool }),
  selectedNode: null,
  selectNode: (selectedNode) => set({ selectedNode }),
  attachRequest: [],
  requestAttach: (attachRequest) => set({ attachRequest }),
  prefill: null,
  requestPrefill: (prefill) => set({ prefill }),
  actions: {},
  setActions: (actions) => set({ actions }),
}));

// A request to build or change a workflow goes to the flow assistant; other requests to the planner.
const BUILD_RE =
  /(搭建|构建|创建|新建|生成|做|建|搭)(一|一个|个|条)?[^。！？!?\n]{0,24}(工作流|流程)|\b(build|create|make|set\s+up)\b[^.!?\n]{0,60}\b(flow|workflow|pipeline)s?\b/i;
const QUESTION_RE = /怎么|如何|怎样|为什么|是什么|什么是|\b(how|what|why)\b/i;
const EDIT_RE =
  /(改成|改用|修改|换成|替换|加上|增加|添加|删除|去掉|调整|改为|把.{0,20}改)/;

export function routeToAssistant(text: string, messages: ChatMessage[]) {
  if (BUILD_RE.test(text) && !QUESTION_RE.test(text)) return true;
  // A reply to the flow assistant's proposed plan stays with the assistant.
  const lastAssistant = [...messages]
    .reverse()
    .find((m) => m.role === "assistant");
  if (lastAssistant?.proposal) return true;
  // Edits go to the assistant when the conversation's current workflow is an assistant-built one.
  const last = [...messages]
    .reverse()
    .find((m) => m.kind === "flow" || m.kind === "plan");
  return Boolean(last && last.kind === "flow" && EDIT_RE.test(text));
}

export type SceneInfo = {
  item_id?: string;
  datetime?: string;
  relative_orbit?: number;
  orbit_state?: string;
  polarization?: string;
};

export type FixtureInfo = {
  type?: string;
  label?: string;
  title?: string;
  description?: string;
  input_kind?: string;
  event?: string;
  scenes?: { before?: SceneInfo; after?: SceneInfo };
  grid?: { crs?: string; shape?: number[]; resolution_m?: number };
  claim_boundary?: string;
};

export type FixtureOption = FixtureInfo & {
  id: string;
  label: string;
  title: string;
  description: string;
  source: "synthetic" | "observed" | "uploaded";
  category: "synthetic" | "observed" | "uploaded" | "test" | string;
  audience: "user" | "developer";
  place?: string | null;
  period?: string | null;
  sensor?: string;
  area_km2?: number | null;
  previews?: { before?: string; after?: string };
  has_mismatch?: boolean;
};

export const CATEGORY_LABELS: Record<string, string> = {
  observed: "卫星观测",
  uploaded: "我的上传",
  synthetic: "合成测试",
  test: "测试上传",
};

export type HistoryItem = {
  run_id?: string;
  job_id?: string;
  created_at?: string;
  status?: string;
  stage?: string;
  prompt?: string;
  fixture_id?: string;
  fixture_label?: string;
  model_used?: string;
  execution_count?: number;
  success_status?: string;
  rejection_reason?: string;
  error?: string;
};

export const SOURCE_LABELS = {
  synthetic: "合成",
  observed: "真实观测",
  uploaded: "上传",
} as const;

export function sceneLine(role: string, scene?: SceneInfo) {
  if (!scene) return null;
  return (
    role +
    "：" +
    (scene.datetime ?? "").slice(0, 16).replace("T", " ") +
    " UTC，" +
    (scene.polarization ?? "") +
    "，相对轨道 " +
    (scene.relative_orbit ?? "—") +
    "（" +
    (scene.orbit_state ?? "—") +
    "）"
  );
}

export function formatTime(value?: string) {
  return value ? value.slice(0, 19).replace("T", " ") : "—";
}

export type Scenario = {
  id: string;
  title: string;
  summary: string;
  inputs: { role: string; label: string }[];
  input_kinds: string[];
  requirements: string[];
  outputs: string[];
  tools: string[];
  limitations: string[];
  examples: string[];
};

export type Metric = {
  key: string;
  label: string;
  value: number | string | null;
  unit?: string;
  note?: string;
};

export type Layer = {
  key: string;
  label: string;
  url: string;
  legend?: { color: string; label: string }[];
};

export type GenericResult = {
  scenario: string;
  status: "success" | "blocked";
  headline: string;
  metrics: Metric[];
  layers: Layer[];
  gate?: {
    checked: boolean;
    passed: boolean;
    blocked_tool?: string | null;
    reason_code?: string | null;
    failed_checks?: string[];
    description?: string;
  };
  self_check?: {
    description: string;
    reason_code?: string;
    failed_checks?: string[];
  } | null;
  boundaries: string[];
};

export type StepEvent = {
  id?: string;
  tool: string;
  chain: "analysis" | "self_check";
  step?: number;
  target?: string | null;
  state: "running" | "done" | "blocked" | "failed";
  reason_code?: string | null;
  outputs?: Record<string, unknown>;
};

export type PlanInfo = {
  scenario: string;
  scenario_title: string;
  input_set: string;
  input_title: string;
  input_kind: string;
  has_self_check_sample: boolean;
  execution_order: string[];
  reason?: string;
  model?: string;
  matched_by?: string | null;
  mentioned?: string | null;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  kind: "text" | "plan" | "run";
  created_at?: string;
  content?: string;
  attachments?: string[];
  status?: "pending" | "running" | "done" | "failed" | "cancelled";
  plan?: PlanInfo;
  run_id?: string;
  input_title?: string;
  result?: GenericResult;
  warnings?: string[];
  model?: string;
  error?: string;
};

export type Conversation = {
  id: string;
  title: string;
  updated_at?: string;
  message_count?: number;
  messages?: ChatMessage[];
};

export type Scene = {
  id: string;
  title: string;
  collection_id: string;
  collection_title: string;
  place?: string | null;
  event?: string | null;
  acquired?: string | null;
  sequence?: number;
  sensor?: string;
  input_kind: string;
  input_scale?: string;
  kind_label?: string;
  category: string;
  audience: "user" | "developer";
  area_km2?: number | null;
  preview?: string;
  grid?: {
    crs?: string | null;
    shape?: number[] | null;
    resolution?: number[] | null;
  };
  source?: {
    item_id?: string;
    datetime?: string;
    platform?: string;
    relative_orbit?: number;
    orbit_state?: string;
    polarization?: string;
    stac_item_url?: string;
  };
  original_name?: string | null;
  claim_boundary?: string | null;
  self_check_for?: string;
};

export type Collection = {
  id: string;
  title: string;
  place?: string | null;
  event?: string | null;
  period?: string | null;
  category: string;
  audience: "user" | "developer";
  scene_ids: string[];
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
  scenario_title?: string;
  input_title?: string;
  model_used?: string;
  execution_count?: number;
  success_status?: string;
  rejection_reason?: string;
  error?: string;
};

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
    label?: string;
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

export type PlanInput = {
  role: string;
  label: string;
  scene_id: string;
  scene_title: string;
  collection_title: string;
  acquired?: string | null;
  preview?: string;
};

export type PlanInfo = {
  scenario: string;
  scenario_title: string;
  /** Absent on plans written before the scene library. */
  inputs?: PlanInput[];
  input_title: string;
  input_kind: string;
  input_scale?: string;
  self_check_available?: boolean;
  execution_order: string[];
  reason?: string;
  model?: string;
  matched_by?: string | null;
  mentioned?: string | null;
  notes?: string[];
  inputs_changed_by_user?: boolean;
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

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
  /** Only on scenarios with a Canvas template. */
  input_kinds?: string[];
  canvas_template?: boolean;
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
  /** How the item fills the slot (e.g. sar_vv); absent on plans before input slots. */
  capability?: string;
  accepts?: string[];
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

/** One record of the data catalog (a STAC Item), as the data page lists it. */
export type CatalogItem = {
  id: string;
  title: string;
  collection_id: string;
  collection_title: string;
  data_type: string;
  type_label: string;
  date?: string | null;
  place?: string | null;
  event?: string | null;
  origin?: string;
  origin_label?: string;
  verification?: string;
  verification_label?: string;
  audience: "user" | "developer";
  legacy: boolean;
  self_check_for?: string | null;
  facts: string[];
  grid?: string | null;
  band_count: number;
  preview: string;
  usable_in: string[];
  capabilities: string[];
};

export type CatalogCollection = {
  id: string;
  title: string;
  data_type: string;
  type_label: string;
  place?: string | null;
  event?: string | null;
  legacy: boolean;
  audience: "user" | "developer";
  item_ids: string[];
  period?: string | null;
};

export type FieldSpec = {
  key: string;
  label: string;
  kind: "text" | "textarea" | "date" | "number" | "select" | "multiselect";
  required: boolean;
  options?: { value: string; label: string }[];
  help?: string;
  default?: string;
};

export type DataTypeSpec = {
  id: string;
  label: string;
  summary: string;
  formats: string[];
  suffixes: string[];
  file_roles: string[];
  multi_file: boolean;
  fields: FieldSpec[];
  common_fields: FieldSpec[];
  class_presets?: Record<string, string>;
  notes: string;
};

export type ClassEntry = {
  value: number | string;
  name: string;
  title?: string;
  color_hint?: string;
  nodata?: boolean;
};

export type FileProfile = {
  ok: boolean;
  error?: string;
  kind?: "raster" | "vector";
  crs?: string | null;
  projected?: boolean;
  shape?: number[];
  resolution?: number[];
  bbox_wgs84?: number[] | null;
  count?: number;
  bands?: {
    index: number;
    description?: string | null;
    dtype: string;
    nodata?: number | null;
  }[];
  geometry_types?: string[];
  feature_count?: number;
  columns?: { name: string; type: string }[];
  layers?: string[];
  shapefile_missing?: string[];
};

export type UploadedFile = {
  n: number;
  original_name: string;
  format: string;
  bytes: number;
  profile: FileProfile;
};

export type BandEntry = {
  name: string;
  common_name: string;
  scale?: string;
  offset?: string;
};

export type Suggestion = {
  fields: Record<string, unknown>;
  role?: string;
  bands?: BandEntry[];
  preset?: string;
  columns?: string[];
};

export type CheckMessage = {
  level: "error" | "warning" | "info";
  text: string;
};

export type CheckResult = {
  index: number;
  files?: string[];
  status: "ok" | "warning" | "rejected";
  messages: CheckMessage[];
  item_id?: string;
};

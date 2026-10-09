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

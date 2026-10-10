import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { API, apiFetch, apiJson, postJson } from "./api";
import type {
  BandEntry,
  CheckResult,
  ClassEntry,
  DataTypeSpec,
  FieldSpec,
  Suggestion,
  UploadedFile,
} from "./types";

type TypeCatalog = {
  types: DataTypeSpec[];
  class_presets: Record<string, { title: string; classes: ClassEntry[] }>;
  collections: {
    id: string;
    title: string;
    data_type: string;
    audience: string;
  }[];
  max_upload_mb: number;
};
type Stored = { file: UploadedFile; suggestion: Suggestion };
type Draft = {
  files: number[];
  roles: Record<string, string>;
  bands: Record<string, BandEntry[]>;
  fields: Record<string, unknown>;
  classes: ClassEntry[];
  preset: string;
  result?: CheckResult;
};

const MAX_FILES = 12;
const COMMON_NAMES = [
  "coastal",
  "blue",
  "green",
  "red",
  "rededge",
  "rededge071",
  "rededge075",
  "rededge078",
  "nir",
  "nir08",
  "nir09",
  "cirrus",
  "swir16",
  "swir22",
  "pan",
  "lwir",
  "lwir11",
  "lwir12",
];
const POLARIZATIONS = ["VV", "VH", "HH", "HV"];
const LEVEL_LABEL = { error: "不通过", warning: "注意", info: "说明" };
const field =
  "w-full rounded-lg border border-input bg-background px-2.5 py-1.5 text-sm";

function size(bytes: number) {
  return bytes > 1024 * 1024
    ? (bytes / 1024 / 1024).toFixed(1) + " MB"
    : Math.ceil(bytes / 1024) + " KB";
}

function profileFacts(file: UploadedFile) {
  const p = file.profile;
  if (!p.ok) return "无法读取：" + (p.error ?? "");
  if (p.shapefile_missing?.length)
    return "Shapefile 缺少 " + p.shapefile_missing.join("、");
  const parts = [p.crs ?? (p.kind ? "无坐标系" : null)];
  if (p.kind === "raster") {
    parts.push(p.shape ? p.shape[1] + "×" + p.shape[0] : null);
    parts.push(
      p.resolution
        ? p.projected
          ? p.resolution[0] + " m"
          : p.resolution[0].toPrecision(3) + "°"
        : null,
    );
    parts.push(p.count + " 个波段 " + (p.bands?.[0]?.dtype ?? ""));
  } else if (p.kind === "vector") {
    parts.push((p.geometry_types ?? []).join("/"), p.feature_count + " 个要素");
  }
  return parts.filter(Boolean).join(" · ");
}

/** Initial records: optical files with the same date form one record; every other file is its own. */
function makeDrafts(
  spec: DataTypeSpec,
  stored: Stored[],
  presets: TypeCatalog["class_presets"],
): Draft[] {
  const defaults: Record<string, unknown> = {};
  for (const f of spec.fields) if (f.default) defaults[f.key] = f.default;
  const groups: Stored[][] = [];
  if (spec.multi_file) {
    const byDate = new Map<string, Stored[]>();
    for (const s of stored) {
      const day =
        String(s.suggestion.fields.datetime ?? "") || "file-" + s.file.n;
      byDate.set(day, [...(byDate.get(day) ?? []), s]);
    }
    groups.push(...Array.from(byDate.values()));
  } else {
    groups.push(...stored.map((s) => [s]));
  }
  return groups.map((group) => {
    const first = group[0].suggestion;
    const preset =
      first.preset ?? (spec.id === "classification" ? "custom" : "");
    const { classes, ...fields } = first.fields;
    return {
      files: group.map((s) => s.file.n),
      roles: Object.fromEntries(
        group.map((s) => [String(s.file.n), s.suggestion.role ?? "data"]),
      ),
      bands: Object.fromEntries(
        group.map((s) => [
          String(s.file.n),
          (s.suggestion.bands ?? []).map((b) => ({
            ...b,
            scale: "",
            offset: "",
          })),
        ]),
      ),
      fields: { ...defaults, ...fields },
      classes:
        preset && preset !== "custom"
          ? presets[preset].classes
          : ((classes as ClassEntry[] | undefined) ?? []),
      preset,
    };
  });
}

function FieldInput({
  spec,
  value,
  onChange,
  bandCount,
}: {
  spec: FieldSpec;
  value: unknown;
  onChange: (value: unknown) => void;
  bandCount: number;
}) {
  if (spec.kind === "multiselect") {
    // Polarizations are chosen per band, in band order.
    const list = Array.isArray(value) ? (value as string[]) : [];
    return (
      <div className="flex flex-wrap gap-2">
        {Array.from({ length: Math.max(1, bandCount) }, (_, index) => (
          <select
            key={index}
            aria-label={"波段 " + (index + 1) + " 的极化"}
            value={list[index] ?? ""}
            onChange={(event) => {
              const next = Array.from(
                { length: Math.max(1, bandCount) },
                (_, i) => list[i] ?? "",
              );
              next[index] = event.target.value;
              onChange(next);
            }}
            className="rounded-lg border border-input bg-background px-2 py-1.5 text-sm"
          >
            <option value="">波段 {index + 1}</option>
            {POLARIZATIONS.map((p) => (
              <option key={p} value={p}>
                波段 {index + 1}：{p}
              </option>
            ))}
          </select>
        ))}
      </div>
    );
  }
  if (spec.kind === "select")
    return (
      <select
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
        className={field}
      >
        <option value="">请选择</option>
        {spec.options?.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  if (spec.kind === "textarea")
    return (
      <textarea
        value={String(value ?? "")}
        maxLength={2000}
        rows={2}
        onChange={(e) => onChange(e.target.value)}
        className={field}
      />
    );
  return (
    <input
      type={
        spec.kind === "date"
          ? "date"
          : spec.kind === "number"
            ? "number"
            : "text"
      }
      value={String(value ?? "")}
      onChange={(e) => onChange(e.target.value)}
      className={field}
    />
  );
}

function Labeled({ spec, children }: { spec: FieldSpec; children: ReactNode }) {
  return (
    <label className="block text-xs font-medium">
      <span className="mb-1 block">
        {spec.label}
        {spec.required ? <span className="text-destructive"> *</span> : null}
      </span>
      {children}
      {spec.help && (
        <span className="mt-0.5 block font-normal text-muted-foreground">
          {spec.help}
        </span>
      )}
    </label>
  );
}

function ClassTable({
  draft,
  presets,
  onChange,
}: {
  draft: Draft;
  presets: TypeCatalog["class_presets"];
  onChange: (patch: Partial<Draft>) => void;
}) {
  const update = (index: number, patch: Partial<ClassEntry>) =>
    onChange({
      classes: draft.classes.map((c, i) =>
        i === index ? { ...c, ...patch } : c,
      ),
      preset: "custom",
    });
  return (
    <div className="space-y-2 text-xs">
      <label className="flex flex-wrap items-center gap-2 font-medium">
        类别表 <span className="text-destructive">*</span>
        <select
          value={draft.preset}
          onChange={(event) => {
            const preset = event.target.value;
            onChange(
              preset === "custom"
                ? { preset }
                : { preset, classes: presets[preset].classes },
            );
          }}
          className="rounded-lg border border-input bg-background px-2 py-1 font-normal"
        >
          <option value="custom">自定义</option>
          {Object.entries(presets)
            .filter(([key]) => key !== "s2_scl")
            .map(([key, preset]) => (
              <option key={key} value={key}>
                预设：{preset.title}
              </option>
            ))}
        </select>
      </label>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[34rem] border-separate border-spacing-y-1">
          <thead className="text-left text-muted-foreground">
            <tr>
              <th className="font-normal">值</th>
              <th className="font-normal">名称（字母、数字、_）</th>
              <th className="font-normal">显示名</th>
              <th className="font-normal">颜色</th>
              <th className="font-normal">nodata</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {draft.classes.map((entry, index) => (
              <tr key={index}>
                <td>
                  <input
                    aria-label={"第 " + (index + 1) + " 类的值"}
                    value={String(entry.value)}
                    onChange={(e) => update(index, { value: e.target.value })}
                    className="w-16 rounded border border-input bg-background px-1.5 py-1"
                  />
                </td>
                <td>
                  <input
                    aria-label={"第 " + (index + 1) + " 类的名称"}
                    value={entry.name}
                    onChange={(e) => update(index, { name: e.target.value })}
                    className="w-full rounded border border-input bg-background px-1.5 py-1"
                  />
                </td>
                <td>
                  <input
                    aria-label={"第 " + (index + 1) + " 类的显示名"}
                    value={entry.title ?? ""}
                    onChange={(e) => update(index, { title: e.target.value })}
                    className="w-full rounded border border-input bg-background px-1.5 py-1"
                  />
                </td>
                <td>
                  <input
                    type="color"
                    aria-label={"第 " + (index + 1) + " 类的颜色"}
                    value={"#" + (entry.color_hint || "888888")}
                    onChange={(e) =>
                      update(index, { color_hint: e.target.value.slice(1) })
                    }
                    className="h-7 w-10 rounded border border-input bg-background"
                  />
                </td>
                <td className="text-center">
                  <input
                    type="checkbox"
                    aria-label={"第 " + (index + 1) + " 类是 nodata"}
                    checked={Boolean(entry.nodata)}
                    onChange={(e) =>
                      update(index, { nodata: e.target.checked })
                    }
                  />
                </td>
                <td>
                  <button
                    type="button"
                    className="text-muted-foreground underline"
                    onClick={() =>
                      onChange({
                        classes: draft.classes.filter((_, i) => i !== index),
                        preset: "custom",
                      })
                    }
                  >
                    删除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button
        type="button"
        className="text-primary underline"
        onClick={() =>
          onChange({
            classes: [...draft.classes, { value: "", name: "" }],
            preset: "custom",
          })
        }
      >
        添加一类
      </button>
    </div>
  );
}

function BandTable({
  file,
  bands,
  onChange,
}: {
  file: UploadedFile;
  bands: BandEntry[];
  onChange: (bands: BandEntry[]) => void;
}) {
  const count = file.profile.count ?? 0;
  const rows = Array.from(
    { length: count },
    (_, i) => bands[i] ?? { name: "band" + (i + 1), common_name: "" },
  );
  const update = (index: number, patch: Partial<BandEntry>) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[30rem] border-separate border-spacing-y-1 text-xs">
        <thead className="text-left text-muted-foreground">
          <tr>
            <th className="font-normal">波段</th>
            <th className="font-normal">标准名 *</th>
            <th className="font-normal">scale</th>
            <th className="font-normal">offset</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              <td>
                <input
                  aria-label={
                    file.original_name + " 波段 " + (index + 1) + " 名称"
                  }
                  value={row.name}
                  onChange={(e) => update(index, { name: e.target.value })}
                  className="w-24 rounded border border-input bg-background px-1.5 py-1"
                />
              </td>
              <td>
                <select
                  aria-label={
                    file.original_name + " 波段 " + (index + 1) + " 标准名"
                  }
                  value={row.common_name}
                  onChange={(e) =>
                    update(index, { common_name: e.target.value })
                  }
                  className="rounded border border-input bg-background px-1.5 py-1"
                >
                  <option value="">请选择</option>
                  {COMMON_NAMES.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                <input
                  aria-label={
                    file.original_name + " 波段 " + (index + 1) + " scale"
                  }
                  value={row.scale ?? ""}
                  placeholder="如 0.0001"
                  onChange={(e) => update(index, { scale: e.target.value })}
                  className="w-24 rounded border border-input bg-background px-1.5 py-1"
                />
              </td>
              <td>
                <input
                  aria-label={
                    file.original_name + " 波段 " + (index + 1) + " offset"
                  }
                  value={row.offset ?? ""}
                  placeholder="如 -0.1"
                  onChange={(e) => update(index, { offset: e.target.value })}
                  className="w-20 rounded border border-input bg-background px-1.5 py-1"
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Result({ result }: { result: CheckResult }) {
  const tone =
    result.status === "rejected"
      ? "text-destructive"
      : result.status === "warning"
        ? "ws-text-warning"
        : "ws-text-success";
  return (
    <div className="mt-2 rounded-lg border border-border bg-muted/30 p-2 text-xs leading-5">
      <p className={"font-medium " + tone}>
        {result.status === "rejected"
          ? "未登记：没有通过检查"
          : result.item_id
            ? "已登记"
            : "通过检查"}
        {result.status === "warning" && result.item_id
          ? "（有需要注意的地方）"
          : ""}
      </p>
      {result.messages.map((message, index) => (
        <p
          key={index}
          className={
            message.level === "error"
              ? "text-destructive"
              : "text-muted-foreground"
          }
        >
          {LEVEL_LABEL[message.level]}：{message.text}
        </p>
      ))}
    </div>
  );
}

export default function UploadPanel({
  onUploaded,
}: {
  onUploaded: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [catalog, setCatalog] = useState<TypeCatalog | null>(null);
  const [typeId, setTypeId] = useState("sar");
  const [target, setTarget] = useState("");
  const [title, setTitle] = useState("");
  const [place, setPlace] = useState("");
  const [eventText, setEventText] = useState("");
  const [test, setTest] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [uploadId, setUploadId] = useState("");
  const [stored, setStored] = useState<Stored[]>([]);
  const [rejectedFiles, setRejectedFiles] = useState<string[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [failed, setFailed] = useState(false);
  const [formKey, setFormKey] = useState(0);

  useEffect(() => {
    if (!open || catalog) return;
    apiJson<TypeCatalog>(API + "/data-types")
      .then(setCatalog)
      .catch((cause) =>
        setNote(cause instanceof Error ? cause.message : "无法读取数据类型。"),
      );
  }, [open, catalog]);

  const spec = catalog?.types.find((t) => t.id === typeId);
  const sameType =
    catalog?.collections.filter(
      (c) => c.data_type === typeId && (test || c.audience === "user"),
    ) ?? [];
  const fileByN = new Map(stored.map((s) => [s.file.n, s.file]));

  async function reset() {
    setUploadId("");
    setStored([]);
    setRejectedFiles([]);
    setDrafts([]);
    setFiles([]);
    setNote("");
    setFailed(false);
    setFormKey((value) => value + 1);
    // Groups made by this upload become choices for the next one.
    setCatalog(
      await apiJson<TypeCatalog>(API + "/data-types").catch(() => catalog),
    );
  }

  async function uploadFiles(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!spec || !catalog || !files.length) return;
    setBusy(true);
    setFailed(false);
    try {
      setNote("正在创建上传记录…");
      const created = await postJson(API + "/data-uploads", {
        data_type: spec.id,
        collection_id: target || undefined,
        collection_title: title.trim(),
        place: place.trim(),
        event: eventText.trim(),
        test,
      });
      const createdBody = (await created.json()) as {
        upload_id?: string;
        error?: string;
      };
      if (!created.ok || !createdBody.upload_id)
        throw new Error(createdBody.error || "上传记录没有创建成功。");
      const id = createdBody.upload_id;
      const kept: Stored[] = [];
      const refused: string[] = [];
      for (let index = 0; index < files.length; index += 1) {
        const file = files[index];
        setNote(
          "正在上传并读取第 " +
            (index + 1) +
            "/" +
            files.length +
            " 个文件：" +
            file.name,
        );
        const response = await apiFetch(
          API + "/data-uploads/" + id + "/files",
          {
            method: "PUT",
            headers: {
              "Content-Type": "application/octet-stream",
              "X-File-Name": encodeURIComponent(file.name),
            },
            body: file,
          },
        );
        const body = (await response.json()) as {
          file?: UploadedFile;
          suggestion?: Suggestion;
          error?: string;
        };
        if (response.ok && body.file && body.suggestion)
          kept.push({ file: body.file, suggestion: body.suggestion });
        else refused.push(body.error || file.name + "：上传失败。");
      }
      setUploadId(id);
      setStored(kept);
      setRejectedFiles(refused);
      setDrafts(makeDrafts(spec, kept, catalog.class_presets));
      setNote(
        kept.length
          ? "文件已读取，请核对并补全下面每条数据的信息，再登记。"
          : "没有可登记的文件。",
      );
      setFailed(!kept.length);
    } catch (cause) {
      setFailed(true);
      setNote(cause instanceof Error ? cause.message : "上传失败。");
    } finally {
      setBusy(false);
    }
  }

  function patchDraft(index: number, patch: Partial<Draft>) {
    setDrafts((current) =>
      current.map((d, i) =>
        i === index ? { ...d, ...patch, result: undefined } : d,
      ),
    );
  }

  async function register() {
    const pending = drafts
      .map((d, index) => ({ d, index }))
      .filter(({ d }) => !d.result?.item_id);
    if (!pending.length) return;
    setBusy(true);
    setFailed(false);
    setNote("正在检查并登记…");
    try {
      const response = await postJson(
        API + "/data-uploads/" + uploadId + "/finalize",
        {
          items: pending.map(({ d }) => ({
            files: d.files,
            roles: d.roles,
            bands: d.bands,
            fields: d.fields,
            classes: d.classes,
          })),
        },
      );
      const body = (await response.json()) as {
        results?: CheckResult[];
        registered?: string[];
        error?: string;
      };
      const results = body.results;
      if (!results) throw new Error(body.error || "登记失败。");
      setDrafts((current) =>
        current.map((d, index) => {
          const at = pending.findIndex((p) => p.index === index);
          return at >= 0 ? { ...d, result: results[at] } : d;
        }),
      );
      const count = body.registered?.length ?? 0;
      setFailed(count === 0);
      setNote(
        count
          ? "已登记 " +
              count +
              " 条数据。" +
              (count < pending.length ? "未通过的可以修改后再次登记。" : "")
          : "没有数据通过检查，请按提示修改后再次登记。",
      );
      if (count) await onUploaded();
    } catch (cause) {
      setFailed(true);
      setNote(cause instanceof Error ? cause.message : "登记失败。");
    } finally {
      setBusy(false);
    }
  }

  const allDone = drafts.length > 0 && drafts.every((d) => d.result?.item_id);
  return (
    <div className="mt-4 rounded-xl border border-border bg-muted/20 p-4">
      <button
        type="button"
        className="text-sm font-medium text-primary underline"
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "收起上传" : "上传数据（SAR、光学、DEM、矢量、分类栅格）"}
      </button>
      {open && !catalog && (
        <p className="mt-3 text-xs text-muted-foreground">
          {note || "读取数据类型…"}
        </p>
      )}
      {open && catalog && spec && !uploadId && (
        <form key={formKey} onSubmit={uploadFiles} className="mt-4 space-y-4">
          <fieldset>
            <legend className="mb-2 text-sm font-medium">数据类型</legend>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
              {catalog.types.map((t) => (
                <label
                  key={t.id}
                  className={
                    "min-w-0 cursor-pointer rounded-xl border p-3 text-xs leading-5 " +
                    (t.id === typeId
                      ? "border-primary bg-primary/5"
                      : "border-border bg-background")
                  }
                >
                  <input
                    type="radio"
                    name="data-type"
                    value={t.id}
                    checked={t.id === typeId}
                    onChange={() => {
                      setTypeId(t.id);
                      setTarget("");
                      setFiles([]);
                      setFormKey((v) => v + 1);
                    }}
                    className="mr-1.5"
                  />
                  <span className="text-sm font-medium">{t.label}</span>
                  <span className="mt-1 block text-muted-foreground">
                    {t.summary}
                  </span>
                  <span className="mt-1 block">{t.formats.join("；")}</span>
                </label>
              ))}
            </div>
            <p className="mt-2 text-xs leading-5 text-muted-foreground">
              {spec.notes}
            </p>
          </fieldset>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="block text-xs font-medium">
              <span className="mb-1 block">放入数据组</span>
              <select
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                className={field}
              >
                <option value="">新建数据组</option>
                {sameType.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.title}
                  </option>
                ))}
              </select>
            </label>
            {!target && (
              <>
                <label className="block text-xs font-medium">
                  <span className="mb-1 block">
                    数据组名称 <span className="text-destructive">*</span>
                  </span>
                  <input
                    value={title}
                    maxLength={80}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder="例如：色萨利 2023 Sentinel-2"
                    className={field}
                  />
                </label>
                <label className="block text-xs font-medium">
                  <span className="mb-1 block">地点</span>
                  <input
                    value={place}
                    maxLength={80}
                    onChange={(e) => setPlace(e.target.value)}
                    className={field}
                  />
                </label>
                <label className="block text-xs font-medium">
                  <span className="mb-1 block">事件</span>
                  <input
                    value={eventText}
                    maxLength={200}
                    onChange={(e) => setEventText(e.target.value)}
                    className={field}
                  />
                </label>
              </>
            )}
          </div>
          <label className="block text-xs font-medium">
            <span className="mb-1 block">
              文件（最多 {MAX_FILES} 个，单个不超过 {catalog.max_upload_mb} MB）
            </span>
            <input
              type="file"
              multiple
              accept={spec.suffixes.join(",")}
              onChange={(e) =>
                setFiles(Array.from(e.target.files ?? []).slice(0, MAX_FILES))
              }
              className="w-full text-xs font-normal"
            />
          </label>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={test}
              onChange={(e) => setTest(e.target.checked)}
            />
            标为测试数据（只在“显示开发者数据”时列出）
          </label>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="submit"
              disabled={
                busy || files.length === 0 || (!target && !title.trim())
              }
            >
              {busy
                ? "处理中…"
                : "上传并读取 " + (files.length || "") + " 个文件"}
            </Button>
            {note && (
              <span
                className={
                  "text-xs " +
                  (failed ? "text-destructive" : "text-muted-foreground")
                }
              >
                {note}
              </span>
            )}
          </div>
        </form>
      )}
      {open && catalog && spec && uploadId && (
        <div className="mt-4 space-y-4">
          {rejectedFiles.map((text) => (
            <p key={text} className="text-xs text-destructive">
              拒收：{text}
            </p>
          ))}
          {drafts.map((draft, index) => {
            const bandCount = fileByN.get(draft.files[0])?.profile.count ?? 1;
            const done = Boolean(draft.result?.item_id);
            return (
              <fieldset
                key={index}
                disabled={done || busy}
                className="min-w-0 space-y-3 rounded-xl border border-border bg-background p-3"
              >
                <legend className="px-1 text-sm font-medium">
                  {spec.label} · 第 {index + 1} 条
                </legend>
                {draft.files.map((n) => {
                  const file = fileByN.get(n);
                  if (!file) return null;
                  return (
                    <div
                      key={n}
                      className="space-y-2 rounded-lg bg-muted/30 p-2 text-xs"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="break-all font-medium">
                          {file.original_name}
                        </span>
                        <span className="text-muted-foreground">
                          {size(file.bytes)} · {profileFacts(file)}
                        </span>
                        {spec.file_roles.length > 1 && (
                          <select
                            aria-label={file.original_name + " 的用途"}
                            value={draft.roles[String(n)]}
                            onChange={(e) =>
                              patchDraft(index, {
                                roles: {
                                  ...draft.roles,
                                  [String(n)]: e.target.value,
                                },
                              })
                            }
                            className="ml-auto rounded border border-input bg-background px-1.5 py-1"
                          >
                            <option value="data">反射率数据</option>
                            <option value="cloud-mask">
                              云掩膜（Sentinel-2 SCL）
                            </option>
                          </select>
                        )}
                      </div>
                      {spec.id === "optical" &&
                        draft.roles[String(n)] === "data" && (
                          <BandTable
                            file={file}
                            bands={draft.bands[String(n)] ?? []}
                            onChange={(bands) =>
                              patchDraft(index, {
                                bands: { ...draft.bands, [String(n)]: bands },
                              })
                            }
                          />
                        )}
                    </div>
                  );
                })}
                {spec.id === "optical" && (
                  <button
                    type="button"
                    className="text-left text-xs text-primary underline"
                    onClick={() =>
                      patchDraft(index, {
                        bands: Object.fromEntries(
                          draft.files
                            .filter((n) => draft.roles[String(n)] === "data")
                            .map((n) => {
                              const count = fileByN.get(n)?.profile.count ?? 0;
                              const rows = draft.bands[String(n)] ?? [];
                              return [
                                String(n),
                                Array.from({ length: count }, (_, i) => ({
                                  ...(rows[i] ?? {
                                    name: "band" + (i + 1),
                                    common_name: "",
                                  }),
                                  scale: "0.0001",
                                  offset: "-0.1",
                                })),
                              ];
                            }),
                        ),
                      })
                    }
                  >
                    按 Sentinel-2 L2A 处理基线 04.00 及以后填写换算系数（scale
                    0.0001，offset −0.1）
                  </button>
                )}
                <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                  {spec.fields.map((f) => (
                    <Labeled key={f.key} spec={f}>
                      {spec.id === "vector" && f.key === "label_property" ? (
                        <select
                          value={String(draft.fields[f.key] ?? "")}
                          onChange={(e) =>
                            patchDraft(index, {
                              fields: {
                                ...draft.fields,
                                [f.key]: e.target.value,
                              },
                            })
                          }
                          className={field}
                        >
                          <option value="">无（所有要素同一类）</option>
                          {(
                            fileByN.get(draft.files[0])?.profile.columns ?? []
                          ).map((c) => (
                            <option key={c.name} value={c.name}>
                              {c.name}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <FieldInput
                          spec={f}
                          value={draft.fields[f.key]}
                          bandCount={bandCount}
                          onChange={(value) =>
                            patchDraft(index, {
                              fields: { ...draft.fields, [f.key]: value },
                            })
                          }
                        />
                      )}
                    </Labeled>
                  ))}
                </div>
                {spec.id === "classification" && (
                  <ClassTable
                    draft={draft}
                    presets={catalog.class_presets}
                    onChange={(p) => patchDraft(index, p)}
                  />
                )}
                <details className="text-xs">
                  <summary className="cursor-pointer text-muted-foreground">
                    更多信息（可选）：名称、平台、来源、许可、处理说明
                  </summary>
                  <div className="mt-2 grid gap-3 md:grid-cols-2">
                    {spec.common_fields.map((f) => (
                      <Labeled key={f.key} spec={f}>
                        <FieldInput
                          spec={f}
                          value={draft.fields[f.key]}
                          bandCount={bandCount}
                          onChange={(value) =>
                            patchDraft(index, {
                              fields: { ...draft.fields, [f.key]: value },
                            })
                          }
                        />
                      </Labeled>
                    ))}
                  </div>
                </details>
                {draft.result && <Result result={draft.result} />}
              </fieldset>
            );
          })}
          <div className="flex flex-wrap items-center gap-3">
            {!allDone && drafts.length > 0 && (
              <Button
                type="button"
                disabled={busy}
                onClick={() => void register()}
              >
                {busy ? "检查中…" : "检查并登记"}
              </Button>
            )}
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => void reset()}
            >
              {allDone ? "继续上传" : "重新开始"}
            </Button>
            {note && (
              <span
                className={
                  "text-xs " +
                  (failed ? "text-destructive" : "text-muted-foreground")
                }
              >
                {note}
              </span>
            )}
          </div>
          <p className="text-xs leading-5 text-muted-foreground">
            上传数据的来源、时间和标签由上传者填写，系统只检查文件本身（坐标系、波段、数值、类别值、几何），不核实来源。
          </p>
        </div>
      )}
    </div>
  );
}

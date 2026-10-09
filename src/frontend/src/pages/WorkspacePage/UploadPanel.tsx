import { type FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";
import { apiFetch } from "./api";

type InputChoice = "sar_linear" | "sar_db" | "water_labels";
type FileRow = { file: File; acquired: string; label: string };

const INPUT_CHOICES: { id: InputChoice; label: string }[] = [
  { id: "sar_linear", label: "SAR 后向散射（线性值）" },
  { id: "sar_db", label: "SAR 后向散射（dB）" },
  { id: "water_labels", label: "0/1 水体标签" },
];
const MAX_FILES = 12;

async function readBody(response: Response) {
  return (await response.json()) as {
    error?: string;
    upload_id?: string;
  };
}

/** Guess YYYY-MM-DD from names like S1A_..._20230913T043157 or 2023-09-13_after.tif. */
function dateFromName(name: string) {
  const match = /(20\d{2})-?([01]\d)-?([0-3]\d)/.exec(name);
  return match ? match[1] + "-" + match[2] + "-" + match[3] : "";
}

export default function UploadPanel({
  onUploaded,
}: {
  onUploaded: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState<InputChoice>("sar_linear");
  const [title, setTitle] = useState("");
  const [place, setPlace] = useState("");
  const [sensor, setSensor] = useState("");
  const [rows, setRows] = useState<FileRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [failed, setFailed] = useState(false);
  const [formKey, setFormKey] = useState(0);

  function chooseFiles(list: FileList | null) {
    const files = Array.from(list ?? []).slice(0, MAX_FILES);
    setRows(
      files.map((file) => ({
        file,
        acquired: dateFromName(file.name),
        label: "",
      })),
    );
  }

  function updateRow(index: number, patch: Partial<FileRow>) {
    setRows((current) =>
      current.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    );
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!rows.length) return;
    setBusy(true);
    setFailed(false);
    try {
      setNote("正在创建上传记录…");
      const created = await apiFetch("/api/workspace/scene-uploads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          collection_title: title.trim(),
          place: place.trim(),
          sensor: sensor.trim(),
          input_kind:
            choice === "water_labels" ? "water_labels" : "sar_backscatter",
          input_scale: choice === "sar_db" ? "db" : "linear",
        }),
      });
      const createdBody = await readBody(created);
      if (!created.ok || !createdBody.upload_id)
        throw new Error(createdBody.error || "上传记录没有创建成功。");
      const uploadId = createdBody.upload_id;
      for (let index = 0; index < rows.length; index += 1) {
        const row = rows[index];
        setNote(
          "正在上传第 " +
            (index + 1) +
            "/" +
            rows.length +
            " 景：" +
            row.file.name,
        );
        const query = new URLSearchParams({
          acquired: row.acquired,
          label: row.label,
        });
        const stored = await apiFetch(
          "/api/workspace/scene-uploads/" + uploadId + "/files?" + query,
          {
            method: "PUT",
            headers: {
              "Content-Type": "application/octet-stream",
              "X-File-Name": encodeURIComponent(row.file.name),
            },
            body: row.file,
          },
        );
        const storedBody = await readBody(stored);
        if (!stored.ok) throw new Error(storedBody.error || "文件上传失败。");
      }
      setNote("正在检查栅格的坐标系和波段…");
      const finalized = await apiFetch(
        "/api/workspace/scene-uploads/" + uploadId + "/finalize",
        { method: "POST" },
      );
      const finalizedBody = await readBody(finalized);
      if (!finalized.ok)
        throw new Error(finalizedBody.error || "上传的栅格没有通过检查。");
      await onUploaded();
      setNote("上传完成，已加入数据列表。");
      setRows([]);
      setTitle("");
      setPlace("");
      setSensor("");
      setFormKey((value) => value + 1);
    } catch (cause) {
      setFailed(true);
      setNote(cause instanceof Error ? cause.message : "上传失败。");
    } finally {
      setBusy(false);
    }
  }

  const field =
    "w-full rounded-xl border border-input bg-background px-3 py-2 text-sm";
  return (
    <div className="mt-4 rounded-xl border border-border bg-muted/20 p-4">
      <button
        type="button"
        className="text-sm font-medium text-primary underline"
        onClick={() => setOpen((value) => !value)}
      >
        {open ? "收起上传" : "上传影像（GeoTIFF，一景或多景）"}
      </button>
      {open && (
        <form key={formKey} onSubmit={submit} className="mt-4 space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block text-sm font-medium">
              <span className="mb-2 block">组名</span>
              <input
                value={title}
                maxLength={80}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="例如：某河段 2024 年汛期"
                className={field}
              />
            </label>
            <label className="block text-sm font-medium">
              <span className="mb-2 block">地点（可选）</span>
              <input
                value={place}
                maxLength={80}
                onChange={(event) => setPlace(event.target.value)}
                placeholder="例如：鄱阳湖北部"
                className={field}
              />
            </label>
            <label className="block text-sm font-medium">
              <span className="mb-2 block">数据类型</span>
              <select
                value={choice}
                onChange={(event) =>
                  setChoice(event.target.value as InputChoice)
                }
                className={field + " py-2.5"}
              >
                {INPUT_CHOICES.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm font-medium">
              <span className="mb-2 block">传感器（可选）</span>
              <input
                value={sensor}
                maxLength={60}
                onChange={(event) => setSensor(event.target.value)}
                placeholder="例如：Sentinel-1 SAR（VV）"
                className={field}
              />
            </label>
          </div>
          <label className="block text-sm font-medium">
            <span className="mb-2 block">文件（最多 {MAX_FILES} 景）</span>
            <input
              type="file"
              multiple
              accept=".tif,.tiff,image/tiff"
              onChange={(event) => chooseFiles(event.target.files)}
              className="w-full text-xs font-normal"
            />
          </label>
          {rows.length > 0 && (
            <div className="space-y-2">
              {rows.map((row, index) => (
                <div
                  key={row.file.name + index}
                  className="grid items-center gap-2 rounded-xl border border-border bg-background p-2 text-xs sm:grid-cols-[1fr_9rem_10rem]"
                >
                  <span className="truncate" title={row.file.name}>
                    {row.file.name}
                  </span>
                  <input
                    type="date"
                    aria-label={"成像日期：" + row.file.name}
                    value={row.acquired}
                    onChange={(event) =>
                      updateRow(index, { acquired: event.target.value })
                    }
                    className="rounded-lg border border-input bg-background px-2 py-1"
                  />
                  <input
                    aria-label={"说明：" + row.file.name}
                    value={row.label}
                    maxLength={40}
                    placeholder="说明（可选），如“灾后”"
                    onChange={(event) =>
                      updateRow(index, { label: event.target.value })
                    }
                    className="rounded-lg border border-input bg-background px-2 py-1"
                  />
                </div>
              ))}
            </div>
          )}
          <p className="text-xs leading-5 text-muted-foreground">
            只接受单波段、带坐标参考系的 GeoTIFF，单个文件不超过 64
            MB。成像日期用于排列前后期，可从文件名自动识别，请核对。
            几景是否在同一网格、面积能否计算，由分析时的门控检查，不满足时会被拦截。上传数据的来源和时间由上传者负责，系统不做核实。
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={busy || rows.length === 0}>
              {busy ? "上传中…" : "上传 " + (rows.length || "") + " 景"}
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
    </div>
  );
}

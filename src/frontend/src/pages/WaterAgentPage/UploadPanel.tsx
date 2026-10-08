import { type FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";
import { apiFetch } from "./api";

type InputChoice = "sar_linear" | "sar_db" | "water_labels";
type Role = "before" | "after" | "mismatch";

const INPUT_CHOICES: { id: InputChoice; label: string }[] = [
  { id: "sar_linear", label: "SAR 后向散射（线性值）" },
  { id: "sar_db", label: "SAR 后向散射（dB）" },
  { id: "water_labels", label: "0/1 水体标签" },
];
const ROLES: { id: Role; label: string; required: boolean }[] = [
  { id: "before", label: "前期影像", required: true },
  { id: "after", label: "后期影像", required: true },
  { id: "mismatch", label: "拒绝样本（可选）", required: false },
];

async function readBody(response: Response) {
  return (await response.json()) as {
    status?: string;
    error?: string;
    upload_id?: string;
    fixture?: { id: string };
  };
}

export default function UploadPanel({
  disabled,
  onUploaded,
}: {
  disabled: boolean;
  onUploaded: (fixtureId: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState<InputChoice>("sar_linear");
  const [label, setLabel] = useState("");
  const [files, setFiles] = useState<Partial<Record<Role, File>>>({});
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [failed, setFailed] = useState(false);
  // Remounting the form clears the native file inputs after a finished upload.
  const [formKey, setFormKey] = useState(0);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!files.before || !files.after) return;
    setBusy(true);
    setFailed(false);
    try {
      setNote("正在创建上传记录…");
      const created = await apiFetch("/api/water-demo/uploads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          label: label.trim(),
          input_kind:
            choice === "water_labels" ? "water_labels" : "sar_backscatter",
          input_scale: choice === "sar_db" ? "db" : "linear",
        }),
      });
      const createdBody = await readBody(created);
      if (!created.ok || !createdBody.upload_id)
        throw new Error(createdBody.error || "上传记录没有创建成功。");
      const uploadId = createdBody.upload_id;
      for (const role of ROLES) {
        const file = files[role.id];
        if (!file) continue;
        setNote("正在上传" + role.label + "：" + file.name);
        const stored = await apiFetch(
          "/api/water-demo/uploads/" + uploadId + "/" + role.id,
          {
            method: "PUT",
            headers: {
              "Content-Type": "application/octet-stream",
              "X-File-Name": encodeURIComponent(file.name),
            },
            body: file,
          },
        );
        const storedBody = await readBody(stored);
        if (!stored.ok) throw new Error(storedBody.error || "文件上传失败。");
      }
      setNote("正在检查栅格的坐标系和波段…");
      const finalized = await apiFetch(
        "/api/water-demo/uploads/" + uploadId + "/finalize",
        { method: "POST" },
      );
      const finalizedBody = await readBody(finalized);
      if (!finalized.ok || !finalizedBody.fixture)
        throw new Error(finalizedBody.error || "上传的栅格没有通过检查。");
      await onUploaded(finalizedBody.fixture.id);
      setNote("上传完成，已在“输入组合”中选中。");
      setFiles({});
      setLabel("");
      setFormKey((value) => value + 1);
    } catch (cause) {
      setFailed(true);
      setNote(cause instanceof Error ? cause.message : "上传失败。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-4 rounded-xl border border-border bg-muted/20 p-4">
      <button
        type="button"
        className="text-sm font-medium text-primary underline"
        onClick={() => setOpen((value) => !value)}
      >
        {open ? "收起上传" : "上传自己的前后 GeoTIFF"}
      </button>
      {open && (
        <form key={formKey} onSubmit={submit} className="mt-4 space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label
                htmlFor="water-upload-kind"
                className="mb-2 block text-sm font-medium"
              >
                数据类型
              </label>
              <select
                id="water-upload-kind"
                value={choice}
                onChange={(event) =>
                  setChoice(event.target.value as InputChoice)
                }
                className="w-full rounded-xl border border-input bg-background px-3 py-2.5 text-sm"
              >
                {INPUT_CHOICES.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label
                htmlFor="water-upload-label"
                className="mb-2 block text-sm font-medium"
              >
                名称
              </label>
              <input
                id="water-upload-label"
                value={label}
                maxLength={80}
                onChange={(event) => setLabel(event.target.value)}
                placeholder="例如：某河段 2024 年汛期前后"
                className="w-full rounded-xl border border-input bg-background px-3 py-2 text-sm"
              />
            </div>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            {ROLES.map((role) => (
              <div key={role.id}>
                <label
                  htmlFor={"water-upload-" + role.id}
                  className="mb-2 block text-sm font-medium"
                >
                  {role.label}
                </label>
                <input
                  id={"water-upload-" + role.id}
                  type="file"
                  accept=".tif,.tiff,image/tiff"
                  onChange={(event) =>
                    setFiles((current) => ({
                      ...current,
                      [role.id]: event.target.files?.[0],
                    }))
                  }
                  className="w-full text-xs"
                />
              </div>
            ))}
          </div>
          <p className="text-xs leading-5 text-muted-foreground">
            只接受单波段、带坐标参考系的 GeoTIFF，单个文件不超过 64
            MB。两景是否同网格由 Agent 的 preflight
            门控检查，不一致时会被拦截。上传数据的来源和时相由上传者负责，系统不做核实。
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="submit"
              disabled={disabled || busy || !files.before || !files.after}
            >
              {busy ? "上传中…" : "上传并加入输入组合"}
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

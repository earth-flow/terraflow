#!/usr/bin/env python3
"""HTTP adapter for the Terraflow water-agent workspace.

The adapter keeps the browser away from Terrabox, llama.cpp, and the fixture
filesystem. It runs the same CLI runner used over SSH, but exposes a small
job/status/history/evidence API and GeoTIFF upload for the custom page.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "terraflow_water_demo.py"
HOST = os.environ.get("TERRAFLOW_WATER_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("TERRAFLOW_WATER_WEB_PORT", "7862"))
MAX_PROMPT_LENGTH = 1200
RUN_TIMEOUT_SECONDS = int(os.environ.get("TERRAFLOW_WATER_WEB_TIMEOUT", "300"))
RUNTIME_DIR = Path(
    os.environ.get(
        "TERRAFLOW_WATER_RUNTIME_DIR",
        "/data2/liyezhuoxin/terrabox-water-demo/runtime",
    )
)
SUMMARY_DIR = RUNTIME_DIR / "demo-runs"
FIXTURE_ROOT = RUNTIME_DIR / "demo-fixtures"
JOB_DIR = RUNTIME_DIR / "web-jobs"
UPLOAD_ROOT = RUNTIME_DIR / "web-uploads"
JOB_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
# Uploaded rasters are opened with rasterio from the Terrabox geo environment;
# the adapter itself stays on the standard library.
GEO_PYTHON = os.environ.get(
    "TERRAFLOW_WATER_GEO_PYTHON",
    "/data2/liyezhuoxin/envs/terrabox-water-demo/bin/python",
)
# Canvas flows are generated with the Terraflow virtualenv, which provides lfx.
FLOW_PYTHON = os.environ.get("TERRAFLOW_WATER_FLOW_PYTHON", str(ROOT / ".venv" / "bin" / "python"))
FLOW_BUILDER = ROOT / "scripts" / "build_water_flow.py"
CANVAS_INDEX = RUNTIME_DIR / "canvas-flows.json"
_CANVAS_LOCK = threading.Lock()
MAX_UPLOAD_BYTES = int(os.environ.get("TERRAFLOW_WATER_MAX_UPLOAD_MB", "64")) * 1024 * 1024
UPLOAD_ROLES = ("before", "after", "mismatch")
INPUT_KINDS = {"water_labels", "sar_backscatter"}
TIFF_MAGIC = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")

_JOB_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_ACTIVE_JOB_ID: str | None = None
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_UPLOAD_ID = re.compile(r"^upl_[0-9a-f]{12}$")
_VALIDATE_RASTER = """
import json, sys
import rasterio
result = {}
for path in sys.argv[1:]:
    try:
        with rasterio.open(path) as src:
            result[path] = {
                "ok": True,
                "crs": src.crs.to_string() if src.crs else None,
                "projected": bool(src.crs and src.crs.is_projected),
                "shape": [src.height, src.width],
                "count": src.count,
                "dtype": src.dtypes[0],
                "nodata": src.nodata,
                "resolution": list(src.res),
            }
    except Exception as exc:
        result[path] = {"ok": False, "error": str(exc)[:300]}
print(json.dumps(result))
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def read_json(path: str | Path | None) -> Any:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    try:
        return json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def parse_last_json(output: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for index, character in enumerate(output):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
    return candidates[-1] if candidates else None


def with_conversation_context(prior: list[str], task: str) -> str:
    """Prefix earlier turns while always keeping the current task intact.

    Older turns are dropped first when the planner prompt would exceed the
    length limit, so truncation never cuts into the task being submitted.
    """
    task = task[:MAX_PROMPT_LENGTH]
    kept = list(prior)
    while kept:
        prompt = "对话上下文：\n" + "\n".join(kept) + "\n当前任务：\n" + task
        if len(prompt) <= MAX_PROMPT_LENGTH:
            return prompt
        kept.pop(0)
    return task


def copy_job(job: dict[str, Any]) -> dict[str, Any]:
    result = dict(job)
    result.pop("_thread", None)
    result.pop("_progress_path", None)
    return result


def update_job(job_id: str, **fields: Any) -> None:
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        if job:
            job.update(fields)
            job["updated_at"] = utc_now()


def fixture_source(meta: dict[str, Any]) -> str:
    if meta.get("type") == "uploaded":
        return "uploaded"
    if meta.get("type") == "synthetic_engineering_fixture":
        return "synthetic"
    return "observed"


def fixture_option(fixture_id: str, path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    has_mismatch = (path / "mismatch.tif").is_file()
    scenes = meta.get("scenes") or {}
    return {
        "id": fixture_id,
        "label": meta.get("label") or fixture_id,
        "description": meta.get("description")
        or ("包含成功链和尺寸不一致拒绝样本" if has_mismatch else "只有前后两景，不运行拒绝链"),
        "path": str(path),
        "type": meta.get("type"),
        "source": fixture_source(meta),
        "input_kind": meta.get("input_kind", "water_labels"),
        "has_mismatch": has_mismatch,
        "event": meta.get("event"),
        "scenes": {
            role: {key: scene.get(key) for key in ("item_id", "datetime", "relative_orbit", "orbit_state", "polarization")}
            for role, scene in scenes.items()
            if isinstance(scene, dict)
        },
        "grid": meta.get("grid"),
        "claim_boundary": meta.get("claim_boundary"),
    }


def fixture_options() -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    candidates: list[tuple[str, Path]] = [("demo-fixtures", FIXTURE_ROOT)]
    if FIXTURE_ROOT.exists():
        candidates.extend((path.name, path) for path in sorted(FIXTURE_ROOT.iterdir()) if path.is_dir())
    if UPLOAD_ROOT.exists():
        candidates.extend(
            (f"upload-{path.name}", path) for path in sorted(UPLOAD_ROOT.iterdir(), reverse=True) if path.is_dir()
        )
    for fixture_id, path in candidates:
        if not all((path / name).is_file() for name in ("before.tif", "after.tif")):
            continue
        meta_path = path / "fixture.json"
        if meta_path.is_file():
            meta = read_json(meta_path)
            if not isinstance(meta, dict):
                continue
        elif path == FIXTURE_ROOT:
            meta = {"type": "synthetic_engineering_fixture", "label": "标准合成前后栅格", "input_kind": "water_labels"}
        else:
            continue
        options.append(fixture_option(fixture_id, path, meta))
    return options


def create_upload(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    input_kind = str(payload.get("input_kind") or "")
    input_scale = str(payload.get("input_scale") or "linear")
    if input_kind not in INPUT_KINDS:
        return 400, {"status": "invalid_request", "error": "input_kind 必须是 water_labels 或 sar_backscatter。"}
    if input_scale not in ("linear", "db"):
        return 400, {"status": "invalid_request", "error": "input_scale 必须是 linear 或 db。"}
    label = str(payload.get("label") or "").strip()[:80] or "上传的前后栅格"
    upload_id = f"upl_{uuid.uuid4().hex[:12]}"
    directory = UPLOAD_ROOT / upload_id
    directory.mkdir(parents=True)
    state = {
        "upload_id": upload_id,
        "status": "pending",
        "label": label,
        "input_kind": input_kind,
        "input_scale": input_scale,
        "created_at": utc_now(),
        "files": {},
    }
    (directory / "upload.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return 201, {"status": "created", "upload_id": upload_id}


def upload_dir(upload_id: str) -> Path | None:
    if not _UPLOAD_ID.fullmatch(upload_id):
        return None
    directory = UPLOAD_ROOT / upload_id
    return directory if (directory / "upload.json").is_file() else None


def store_upload_file(upload_id: str, role: str, stream: Any, length: int, filename: str) -> tuple[int, dict[str, Any]]:
    directory = upload_dir(upload_id)
    if directory is None:
        return 404, {"status": "not_found", "error": "上传记录不存在。"}
    if role not in UPLOAD_ROLES:
        return 400, {"status": "invalid_request", "error": "文件角色必须是 before、after 或 mismatch。"}
    state = read_json(directory / "upload.json") or {}
    if state.get("status") != "pending":
        return 409, {"status": "conflict", "error": "该上传已经完成，不能再修改文件。"}
    if length <= 0:
        return 400, {"status": "invalid_request", "error": "文件为空。"}
    if length > MAX_UPLOAD_BYTES:
        return 413, {"status": "too_large", "error": f"单个文件不能超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB。"}
    target = directory / f"{role}.tif"
    temporary = directory / f".{role}.part"
    digest = hashlib.sha256()
    remaining = length
    head = b""
    with temporary.open("wb") as handle:
        while remaining > 0:
            chunk = stream.read(min(remaining, 1024 * 1024))
            if not chunk:
                break
            if len(head) < 4:
                head += chunk[: 4 - len(head)]
            digest.update(chunk)
            handle.write(chunk)
            remaining -= len(chunk)
    if remaining:
        temporary.unlink(missing_ok=True)
        return 400, {"status": "invalid_request", "error": "上传在传输中断开。"}
    if head not in TIFF_MAGIC:
        temporary.unlink(missing_ok=True)
        return 415, {"status": "unsupported_type", "error": f"{role} 不是 TIFF/GeoTIFF 文件。"}
    temporary.replace(target)
    state.setdefault("files", {})[role] = {
        "original_name": re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", filename)[:120],
        "bytes": length,
        "sha256": digest.hexdigest(),
    }
    (directory / "upload.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return 200, {"status": "stored", "role": role, "bytes": length}


def finalize_upload(upload_id: str) -> tuple[int, dict[str, Any]]:
    directory = upload_dir(upload_id)
    if directory is None:
        return 404, {"status": "not_found", "error": "上传记录不存在。"}
    state = read_json(directory / "upload.json") or {}
    files = state.get("files") or {}
    missing = [role for role in ("before", "after") if role not in files]
    if missing:
        return 400, {"status": "invalid_request", "error": "缺少必需的文件：" + "、".join(missing)}
    paths = [str(directory / f"{role}.tif") for role in files]
    try:
        completed = subprocess.run(
            [GEO_PYTHON, "-I", "-c", _VALIDATE_RASTER, *paths],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        profiles = json.loads(completed.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return 502, {"status": "validation_unavailable", "error": f"无法检查上传的栅格：{exc}"}
    problems = []
    for role in files:
        profile = profiles.get(str(directory / f"{role}.tif")) or {"ok": False, "error": "未返回检查结果"}
        files[role]["profile"] = profile
        if not profile.get("ok"):
            problems.append(f"{role} 无法作为栅格打开（{profile.get('error')}）")
        elif not profile.get("crs"):
            problems.append(f"{role} 缺少坐标参考系")
        elif profile.get("count") != 1:
            problems.append(f"{role} 有 {profile.get('count')} 个波段，目前只支持单波段")
    if problems:
        return 422, {"status": "invalid_raster", "error": "；".join(problems), "files": files}
    before_profile = files["before"]["profile"]
    meta = {
        "schema_version": "terraflow-water-fixture.v1",
        "type": "uploaded",
        "label": state.get("label"),
        "description": (
            ("SAR 后向散射" if state.get("input_kind") == "sar_backscatter" else "0/1 水体标签")
            + f"，{before_profile['shape'][1]}×{before_profile['shape'][0]}，{before_profile['crs']}"
            + ("，含拒绝样本" if "mismatch" in files else "，不运行拒绝链")
        ),
        "input_kind": state.get("input_kind"),
        "input_scale": state.get("input_scale"),
        "uploaded_at": state.get("created_at"),
        "files": files,
        "claim_boundary": "Uploaded rasters are user-supplied; their source, acquisition time and labels are not verified by Terraflow.",
    }
    (directory / "fixture.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state["status"] = "ready"
    (directory / "upload.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return 200, {"status": "ready", "fixture": fixture_option(f"upload-{upload_id}", directory, meta)}


def canvas_flow_for_run(run_id: str, *, force: bool = False) -> tuple[int, dict[str, Any]]:
    """Create (once) a Terraflow canvas flow from a run's validated plan."""
    if summary_path_for(run_id) is None:
        return 404, {"status": "not_found", "error": "运行记录不存在"}
    with _CANVAS_LOCK:
        index = read_json(CANVAS_INDEX)
        index = index if isinstance(index, dict) else {}
        if run_id in index and not force:
            return 200, {"status": "exists", **index[run_id]}
        try:
            completed = subprocess.run(
                [FLOW_PYTHON, str(FLOW_BUILDER), "--run-id", run_id, "--runtime-dir", str(RUNTIME_DIR)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 502, {"status": "error", "error": f"无法生成 Canvas flow：{exc}"}
        result = parse_last_json(completed.stdout) or parse_last_json(completed.stderr) or {}
        if completed.returncode != 0 or result.get("status") != "created":
            return 502, {
                "status": "error",
                "error": result.get("error") or "Canvas flow 生成失败",
                "details": result.get("details") or completed.stderr[-1000:],
            }
        entry = {
            "flow_id": result["flow_id"],
            "canvas_path": result["canvas_path"],
            "name": result.get("name"),
            "created_at": utc_now(),
        }
        index[run_id] = entry
        CANVAS_INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        return 201, {"status": "created", **entry}


def resolve_fixture(fixture_id: str) -> dict[str, Any] | None:
    return next((item for item in fixture_options() if item["id"] == fixture_id), None)


def summary_path_for(run_id: str) -> Path | None:
    if not _SAFE_ID.fullmatch(run_id):
        return None
    path = SUMMARY_DIR / f"terraflow_water_demo_{run_id}.json"
    return path if path.is_file() else None


def summary_brief(summary: dict[str, Any]) -> dict[str, Any]:
    success = summary.get("success_chain") or {}
    reject = summary.get("rejection_chain") or {}
    request = summary.get("request") or {}
    fixtures = summary.get("fixtures") or {}
    model = summary.get("model") or {}
    return {
        "run_id": summary.get("run_id"),
        "created_at": summary.get("created_at"),
        "status": "complete",
        "prompt": request.get("prompt"),
        "fixture_id": (summary.get("web_request") or {}).get("fixture_id"),
        "fixture_type": fixtures.get("type"),
        "fixture_label": fixtures.get("label"),
        "success_status": success.get("status"),
        "model_used": model.get("model_used"),
        "execution_count": int(success.get("execution_count") or len(success.get("steps") or []))
        + len(reject.get("steps") or []),
        "success_count": len(success.get("steps") or []),
        "rejection_reason": reject.get("reason_code"),
        "analytics": summary.get("analytics"),
    }


def list_history(limit: int = 20) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    persisted: list[dict[str, Any]] = []
    if SUMMARY_DIR.exists():
        for path in SUMMARY_DIR.glob("terraflow_water_demo_*.json"):
            value = read_json(path)
            if isinstance(value, dict) and value.get("run_id"):
                persisted.append(summary_brief(value))
    persisted.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    with _JOB_LOCK:
        active = [
            {
                "run_id": job.get("run_id"),
                "job_id": job.get("job_id"),
                "created_at": job.get("created_at"),
                "status": job.get("status"),
                "stage": job.get("stage"),
                "prompt": job.get("prompt"),
                "fixture_id": job.get("fixture_id"),
                "model_used": None,
                "execution_count": 0,
                "error": job.get("error"),
            }
            for job in _JOBS.values()
            # Completed runs come from persisted summaries; failed jobs have no
            # summary, so keep them in this process's history instead.
            if job.get("status") != "complete"
        ]
    return (active + persisted)[:limit]


def inside_runtime(path: Path) -> bool:
    try:
        root = RUNTIME_DIR.resolve()
        candidate = path.resolve()
        return candidate == root or str(candidate).startswith(str(root) + os.sep)
    except OSError:
        return False


def evidence_for_summary(summary: dict[str, Any]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for chain_name in ("success_chain", "rejection_chain"):
        chain = summary.get(chain_name) or {}
        for step in chain.get("steps") or []:
            if not isinstance(step, dict):
                continue
            item = {
                "chain": chain_name,
                "step": step.get("step"),
                "tool_slug": step.get("tool_slug"),
                "target": step.get("target"),
                "execution_id": step.get("execution_id"),
                "trace_id": step.get("trace_id"),
                "manifest_path": step.get("manifest_path"),
                "manifest_exists": step.get("manifest_exists"),
                "inputs": step.get("inputs"),
                "outputs": step.get("outputs"),
                "reason_code": step.get("reason_code"),
                "failed_checks": step.get("failed_checks"),
                "success": step.get("success"),
                "execution_blocked": step.get("execution_blocked"),
            }
            manifest_path = step.get("manifest_path")
            if manifest_path:
                candidate = Path(str(manifest_path))
                manifest = read_json(candidate) if inside_runtime(candidate) else None
                if isinstance(manifest, dict):
                    item["manifest"] = manifest
            records.append(item)
    analytics = summary.get("analytics") or {}
    return {
        "run_id": summary.get("run_id"),
        "created_at": summary.get("created_at"),
        "records": records,
        "analytics": {
            "endpoint": analytics.get("endpoint"),
            "matched_count": analytics.get("matched_count"),
            "expected_count": analytics.get("expected_count"),
            "records": analytics.get("records"),
        },
    }


def run_job(job_id: str, prompt: str, fixture: dict[str, Any], conversation_turns: int) -> None:
    global _ACTIVE_JOB_ID
    progress_path = JOB_DIR / f"{job_id}.json"
    update_job(job_id, status="running", stage="starting", message="正在启动水体 Agent")
    command = [
        sys.executable,
        str(RUNNER),
        "--prompt",
        prompt,
        "--fixtures-dir",
        str(fixture["path"]),
        "--progress-file",
        str(progress_path),
    ]
    stdout = ""
    stderr = ""
    try:
        with _RUN_LOCK:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
            while process.poll() is None:
                progress = read_json(progress_path)
                if isinstance(progress, dict):
                    update_job(
                        job_id,
                        stage=progress.get("stage") or "running",
                        message=progress.get("message"),
                        current_step=progress.get("current_step"),
                        total_steps=progress.get("total_steps"),
                        tool=progress.get("tool"),
                    )
                if time.monotonic() > deadline:
                    process.kill()
                    raise TimeoutError(f"水体 Agent 执行超过 {RUN_TIMEOUT_SECONDS} 秒，已停止。")
                time.sleep(0.25)
            stdout, stderr = process.communicate()
            progress = read_json(progress_path)
            if isinstance(progress, dict):
                update_job(
                    job_id,
                    stage=progress.get("stage") or "finished",
                    message=progress.get("message"),
                    current_step=progress.get("current_step"),
                    total_steps=progress.get("total_steps"),
                    tool=progress.get("tool"),
                )
            cli = parse_last_json(stdout) or parse_last_json(stderr)
            if process.returncode != 0:
                raise RuntimeError((cli or {}).get("error") or "水体 Agent 执行失败")
            summary = read_json((cli or {}).get("summary_path"))
            if not isinstance(summary, dict):
                raise RuntimeError("runner 已结束，但没有找到可读取的运行摘要。")
            summary["web_request"] = {
                "prompt": prompt,
                "entrypoint": "terraflow-water-web-api",
                "fixture_id": fixture["id"],
                "conversation_turns": conversation_turns,
            }
            summary_file = summary_path_for(str(summary.get("run_id") or ""))
            if summary_file:
                summary_file.write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            update_job(
                job_id,
                status="complete",
                stage="complete",
                message="运行完成",
                run_id=summary.get("run_id"),
                summary=summary,
                cli=cli,
            )
    except Exception as exc:
        update_job(
            job_id,
            status="error",
            stage="error",
            message=str(exc),
            error=str(exc),
            stderr=stderr[-2000:],
            stdout=stdout[-2000:],
        )
    finally:
        with _JOB_LOCK:
            if _ACTIVE_JOB_ID == job_id:
                _ACTIVE_JOB_ID = None


class Handler(BaseHTTPRequestHandler):
    server_version = "TerraflowWaterWeb/0.2"

    def _send(self, status: int, payload: Any) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/")
        if path == "/api/water-demo/health":
            with _JOB_LOCK:
                active = _ACTIVE_JOB_ID
            self._send(
                200,
                {
                    "status": "ok",
                    "runner": str(RUNNER),
                    "busy": active is not None,
                    "active_job_id": active,
                },
            )
            return
        if path == "/api/water-demo/fixtures":
            self._send(200, {"status": "ok", "fixtures": fixture_options()})
            return
        if path == "/api/water-demo/history":
            try:
                limit = int(parse_qs(parsed.query).get("limit", ["20"])[0])
            except ValueError:
                limit = 20
            self._send(200, {"status": "ok", "runs": list_history(limit)})
            return

        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) == 4 and parts[:3] == ["api", "water-demo", "jobs"]:
            with _JOB_LOCK:
                job = _JOBS.get(parts[3])
            if not job:
                self._send(404, {"status": "not_found", "error": "job 不存在或已过期"})
            else:
                self._send(200, copy_job(job))
            return
        if len(parts) >= 4 and parts[:3] == ["api", "water-demo", "runs"]:
            run_id = parts[3]
            summary_file = summary_path_for(run_id)
            summary = read_json(summary_file) if summary_file else None
            if not isinstance(summary, dict):
                self._send(404, {"status": "not_found", "error": "运行记录不存在"})
                return
            if len(parts) == 5 and parts[4] == "evidence":
                self._send(200, {"status": "ok", "evidence": evidence_for_summary(summary)})
            else:
                self._send(200, {"status": "ok", "summary": summary})
            return
        self._send(404, {"status": "not_found", "error": "unknown endpoint"})

    def _read_json_body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(min(length, 64_000)) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def do_PUT(self) -> None:
        parts = [unquote(part) for part in urlsplit(self.path).path.split("/") if part]
        if len(parts) != 5 or parts[:3] != ["api", "water-demo", "uploads"]:
            self._send(404, {"status": "not_found", "error": "unknown endpoint"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        filename = unquote(self.headers.get("X-File-Name", "") or "")
        status, body = store_upload_file(parts[3], parts[4], self.rfile, length, filename)
        if status != 200:
            # Part of the body may be unread; do not reuse this connection.
            self.close_connection = True
        self._send(status, body)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path.rstrip("/")
        parts = [unquote(part) for part in path.split("/") if part]
        if path == "/api/water-demo/uploads":
            payload = self._read_json_body()
            if payload is None:
                self._send(400, {"status": "invalid_request", "error": "请求体必须是 JSON。"})
                return
            self._send(*create_upload(payload))
            return
        if len(parts) == 5 and parts[:3] == ["api", "water-demo", "uploads"] and parts[4] == "finalize":
            self._send(*finalize_upload(parts[3]))
            return
        if len(parts) == 5 and parts[:3] == ["api", "water-demo", "runs"] and parts[4] == "canvas":
            force = parse_qs(urlsplit(self.path).query).get("force", ["0"])[0] == "1"
            self._send(*canvas_flow_for_run(parts[3], force=force))
            return
        if path != "/api/water-demo/run":
            self._send(404, {"status": "not_found", "error": "unknown endpoint"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(min(length, 64_000))
            payload = json.loads(raw or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"status": "invalid_request", "error": "请求体必须是 JSON。"})
            return
        prompt = str(payload.get("prompt") or "请分析这组前后栅格，检查水体变化并给出可追溯证据。")
        prompt = prompt[:MAX_PROMPT_LENGTH]
        fixture_id = str(payload.get("fixture_id") or "demo-fixtures")
        fixture = resolve_fixture(fixture_id)
        if fixture is None:
            self._send(400, {"status": "invalid_request", "error": "选择的 fixture 不存在或缺少必要栅格。"})
            return
        messages = payload.get("messages")
        conversation_turns = 0
        if isinstance(messages, list):
            prior = []
            for message in messages[-6:]:
                if isinstance(message, dict):
                    role = "assistant" if message.get("role") == "assistant" else "user"
                    content = str(message.get("content") or "").strip()
                    if content:
                        prior.append(f"{role}: {content[:400]}")
            conversation_turns = len(prior)
            prompt = with_conversation_context(prior, prompt)
        global _ACTIVE_JOB_ID
        with _JOB_LOCK:
            if _ACTIVE_JOB_ID is not None:
                self._send(409, {"status": "busy", "error": "已有水体 Agent 任务正在运行，请等待当前任务完成。"})
                return
            job_id = f"job_{uuid.uuid4().hex[:12]}"
            _ACTIVE_JOB_ID = job_id
            _JOBS[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "stage": "queued",
                "message": "任务已排队",
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "prompt": prompt,
                "fixture_id": fixture_id,
                "conversation_turns": conversation_turns,
            }
            thread = threading.Thread(
                target=run_job,
                args=(job_id, prompt, fixture, conversation_turns),
                daemon=True,
                name=f"water-agent-{job_id}",
            )
            _JOBS[job_id]["_thread"] = thread
            thread.start()
        self._send(
            202,
            {
                "status": "queued",
                "job_id": job_id,
                "message": "任务已提交，可通过 job_id 查询阶段状态。",
            },
        )

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("[water-web] " + (format % args) + "\n")


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Terraflow water web adapter listening on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""HTTP adapter for the Terraflow water-agent workspace.

The adapter keeps the browser away from Terrabox, llama.cpp, and the fixture
filesystem. It runs the same CLI runner used over SSH, but exposes a small
job/status/history/evidence API and GeoTIFF upload for the custom page.
"""

from __future__ import annotations

import hashlib
import importlib.util
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
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import Request, urlopen

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
# When Terraflow runs without auto-login, every request (except health) must
# carry a valid Terraflow session; it is checked against /api/v1/users/whoami.
REQUIRE_LOGIN = os.environ.get("TERRAFLOW_WATER_REQUIRE_LOGIN", "1").strip().lower() not in {"0", "false", "no", "off"}
TERRAFLOW_URL = os.environ.get("TERRAFLOW_BASE_URL", "http://127.0.0.1:7861").rstrip("/")
SESSION_CACHE_SECONDS = 60
_SESSION_CACHE: dict[str, float] = {}
_SESSION_LOCK = threading.Lock()

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
def method(crs):
    try:
        from pyproj import CRS
        operation = CRS.from_user_input(crs).coordinate_operation
        return operation.method_name if operation is not None else None
    except Exception:
        return None
result = {}
for path in sys.argv[1:]:
    try:
        with rasterio.open(path) as src:
            result[path] = {
                "ok": True,
                "crs": src.crs.to_string() if src.crs else None,
                "projected": bool(src.crs and src.crs.is_projected),
                "projection_method": method(src.crs),
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
    """The last top-level JSON object in mixed output (objects nested inside it are skipped)."""
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    index = output.find("{")
    while index >= 0:
        try:
            value, end = decoder.raw_decode(output, index)
        except json.JSONDecodeError:
            index = output.find("{", index + 1)
            continue
        if isinstance(value, dict):
            candidates.append(value)
        index = output.find("{", end)
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


# Synthetic fixtures and test uploads stay available but are hidden from users by default.
DEVELOPER_CATEGORIES = {"synthetic", "test"}


def fixture_category(meta: dict[str, Any]) -> str:
    return str(meta.get("category") or fixture_source(meta))


def scene_period(scenes: dict[str, Any]) -> str | None:
    dates = [str((scenes.get(role) or {}).get("datetime") or "")[:10] for role in ("before", "after")]
    return " → ".join(dates) if all(dates) else None


def grid_area_km2(meta: dict[str, Any]) -> float | None:
    """Footprint of the pair, only when the grid is in metres (projected, recorded resolution)."""
    grid = meta.get("grid") or {}
    shape, resolution = grid.get("shape"), grid.get("resolution_m")
    if not shape or not resolution:
        profile = ((meta.get("files") or {}).get("before") or {}).get("profile") or {}
        if not profile.get("projected"):
            return None
        shape, res = profile.get("shape"), profile.get("resolution") or [None]
        resolution = abs(res[0]) if res[0] else None
        if not shape or not resolution:
            return None
    return round(shape[0] * shape[1] * resolution * resolution / 1e6, 2)


def default_sensor(meta: dict[str, Any]) -> str:
    if meta.get("type") == "synthetic_engineering_fixture":
        return "合成 0/1 水体标签"
    return "SAR 后向散射" if meta.get("input_kind") == "sar_backscatter" else "0/1 水体标签"


def fixture_option(fixture_id: str, path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    has_mismatch = (path / "mismatch.tif").is_file()
    scenes = meta.get("scenes") or {}
    category = fixture_category(meta)
    preview_base = f"/api/workspace/input-sets/{fixture_id}/preview"
    return {
        "id": fixture_id,
        "title": meta.get("title") or meta.get("label") or fixture_id,
        "place": meta.get("place"),
        "period": scene_period(scenes),
        "sensor": meta.get("sensor") or default_sensor(meta),
        "area_km2": grid_area_km2(meta),
        "category": category,
        "audience": "developer" if category in DEVELOPER_CATEGORIES else "user",
        "previews": {"before": preview_base + "/before", "after": preview_base + "/after"},
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
            meta = {
                "type": "synthetic_engineering_fixture",
                "label": "标准合成前后栅格",
                "title": "合成测试栅格（2×2）",
                "category": "synthetic",
                "input_kind": "water_labels",
            }
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
        "category": "uploaded",
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


def request_token(headers: Any) -> str | None:
    """The Terraflow access token from the Authorization header or the access_token_lf cookie."""
    authorization = headers.get("Authorization") or ""
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    for part in (headers.get("Cookie") or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == "access_token_lf" and value:
            return unquote(value)
    return None


def session_is_valid(token: str | None) -> bool:
    if not token:
        return False
    now = time.monotonic()
    with _SESSION_LOCK:
        expires = _SESSION_CACHE.get(token)
        if expires and expires > now:
            return True
    request = Request(
        f"{TERRAFLOW_URL}/api/v1/users/whoami",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            valid = response.status == 200
    except (HTTPError, URLError, OSError):
        valid = False
    if valid:
        with _SESSION_LOCK:
            _SESSION_CACHE[token] = now + SESSION_CACHE_SECONDS
    return valid


def _load_runner() -> Any:
    """Import the CLI runner (standard library only) for its catalog and presets."""
    spec = importlib.util.spec_from_file_location("terraflow_water_demo", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_RUNNER = _load_runner()
_TOOLS_CACHE: dict[str, Any] = {}
TOOLS_CACHE_SECONDS = 30


def tool_catalog() -> tuple[int, dict[str, Any]]:
    """Live Terrabox catalog plus the water scenario presets, cached briefly."""
    cached = _TOOLS_CACHE.get("value")
    if cached and time.monotonic() - _TOOLS_CACHE.get("at", 0) < TOOLS_CACHE_SECONDS:
        return 200, cached
    try:
        api_key = _RUNNER.load_api_key(Path("/tmp/terrabox-api-key"), None)
        toolkits, tools = _RUNNER.discover_catalog(_RUNNER.DEFAULT_TERRABOX_URL, api_key)
    except _RUNNER.DemoError as exc:
        return 502, {"status": "error", "error": f"无法读取 Terrabox 工具目录：{exc}"}
    used_by: dict[str, list[str]] = {}
    for spec in workspace_scenarios.TASK_SCENARIOS.values():
        for slug in spec["tools"]:
            used_by.setdefault(slug, []).append(spec["title"])
    tool_rows = [
        {
            "slug": slug,
            "name": spec.get("name"),
            "toolkit": spec.get("toolkit_slug"),
            "status": spec.get("status"),
            "toolkit_status": spec.get("toolkit_status"),
            "description": str(spec.get("description") or "")[:300],
            "scenarios": used_by.get(slug, []),
        }
        for slug, spec in sorted(tools.items())
    ]
    toolkit_rows = []
    for toolkit in toolkits:
        slug = str(toolkit.get("slug") or toolkit.get("name"))
        rows = [row for row in tool_rows if row["toolkit"] == slug]
        toolkit_rows.append(
            {
                "slug": slug,
                "name": toolkit.get("name"),
                "status": toolkit.get("status"),
                "description": str(toolkit.get("description") or "")[:300],
                "tool_count": len(rows),
                "available_count": sum(1 for row in rows if row["status"] == "available"),
            }
        )
    payload = {
        "status": "ok",
        "fetched_at": utc_now(),
        "toolkits": toolkit_rows,
        "tools": tool_rows,
        "scenario_count": len(workspace_scenarios.TASK_SCENARIOS),
        "presets": _RUNNER.SCENARIOS,
    }
    _TOOLS_CACHE.update(value=payload, at=time.monotonic())
    return 200, payload


def _read_canvas_index() -> dict[str, Any]:
    index = read_json(CANVAS_INDEX)
    return index if isinstance(index, dict) else {}


def canvas_index() -> dict[str, Any]:
    """Flow ids keyed by run id or ``template:<input set>``."""
    with _CANVAS_LOCK:
        return {"status": "ok", "flows": _read_canvas_index()}


def forget_canvas_flow(flow_id: str) -> tuple[int, dict[str, Any]]:
    """Drop index entries for a flow the user deleted in Terraflow."""
    with _CANVAS_LOCK:
        index = _read_canvas_index()
        kept = {key: entry for key, entry in index.items() if entry.get("flow_id") != flow_id}
        CANVAS_INDEX.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    return 200, {"status": "ok", "removed": len(index) - len(kept)}


def canvas_flow_for_run(run_id: str, *, force: bool = False, token: str | None = None) -> tuple[int, dict[str, Any]]:
    """Create (once) a Terraflow canvas flow from a run's validated plan."""
    if summary_path_for(run_id) is None:
        return 404, {"status": "not_found", "error": "运行记录不存在"}
    return _build_canvas_flow(run_id, ["--run-id", run_id], force=force, token=token)


def canvas_flow_for_input_set(
    set_id: str, *, force: bool = False, token: str | None = None
) -> tuple[int, dict[str, Any]]:
    """Create (once) a template canvas flow for an input set."""
    if resolve_fixture(set_id) is None:
        return 404, {"status": "not_found", "error": "输入组合不存在"}
    return _build_canvas_flow(f"template:{set_id}", ["--input-set", set_id], force=force, token=token)


def canvas_template_for_scenes(
    scenario_id: str, inputs: dict[str, str], *, force: bool = False, token: str | None = None
) -> tuple[int, dict[str, Any]]:
    """Create (once) a template canvas flow for a scenario on chosen library scenes."""
    spec = workspace_scenarios.TASK_SCENARIOS.get(scenario_id)
    if spec is None:
        return 404, {"status": "not_found", "error": "场景不存在"}
    if not spec.get("canvas_template"):
        return 400, {"status": "invalid_request", "error": f"「{spec['title']}」暂时没有 Canvas 模板。"}
    scenes, problem = LIBRARY.check_inputs(spec, inputs)
    if problem:
        return 400, {"status": "invalid_request", "error": problem}
    key = "template:" + scenario_id + ":" + ",".join(scenes[item["role"]]["id"] for item in spec["inputs"])
    args = ["--scenario", scenario_id] + [
        part for role, scene in scenes.items() for part in ("--scene", f"{role}={scene['id']}")
    ]
    return _build_canvas_flow(key, args, force=force, token=token)


def _build_canvas_flow(
    key: str, source_args: list[str], *, force: bool, token: str | None
) -> tuple[int, dict[str, Any]]:
    with _CANVAS_LOCK:
        index = _read_canvas_index()
        if key in index and not force:
            return 200, {"status": "exists", **index[key]}
        try:
            completed = subprocess.run(
                [FLOW_PYTHON, str(FLOW_BUILDER), *source_args, "--runtime-dir", str(RUNTIME_DIR)],
                cwd=ROOT,
                # The flow is created for the user who asked for it.
                env={**os.environ, **({"TERRAFLOW_ACCESS_TOKEN": token} if token else {})},
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
            "input_set": result.get("input_set"),
            "scenario": result.get("scenario"),
            "scenes": result.get("scenes"),
            "plan": result.get("plan"),
            "created_at": utc_now(),
        }
        index[key] = entry
        CANVAS_INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        return 201, {"status": "created", **entry}


PREVIEW_DIR = RUNTIME_DIR / "previews"
PREVIEW_RENDERER = ROOT / "scripts" / "render_raster_preview.py"


def render_preview(source: Path, mode: str, scale: str = "linear") -> Path | None:
    """Render (once per file version) a PNG preview of a raster inside the runtime directory."""
    if not source.is_file() or not inside_runtime(source):
        return None
    stat = source.stat()
    key = hashlib.sha1(
        f"{source.resolve()}|{stat.st_mtime_ns}|{stat.st_size}|{mode}|{scale}".encode()
    ).hexdigest()[:20]
    target = PREVIEW_DIR / f"{key}.png"
    if target.is_file():
        return target
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            [GEO_PYTHON, "-I", str(PREVIEW_RENDERER), str(source), str(target), "--mode", mode, "--scale", scale],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return target if completed.returncode == 0 and target.is_file() else None


def input_preview(fixture_id: str, role: str) -> Path | None:
    fixture = resolve_fixture(fixture_id)
    if fixture is None or role not in UPLOAD_ROLES:
        return None
    if fixture["input_kind"] == "sar_backscatter":
        meta = read_json(Path(fixture["path"]) / "fixture.json") or {}
        return render_preview(Path(fixture["path"]) / f"{role}.tif", "sar", meta.get("input_scale", "linear"))
    return render_preview(Path(fixture["path"]) / f"{role}.tif", "labels")


RUN_PREVIEWS = ("before", "after", "before_mask", "after_mask", "scene", "scene_mask")


def run_preview(run_id: str, name: str) -> Path | None:
    summary_file = summary_path_for(run_id)
    summary = read_json(summary_file) if summary_file else None
    if not isinstance(summary, dict):
        return None
    # Runs of the newer scenarios list their map layers (file and how to draw it) in the summary.
    layer = ((summary.get("success_chain") or {}).get("layers") or {}).get(name)
    if isinstance(layer, dict) and layer.get("path"):
        return CATALOG.render_layer(Path(layer["path"]), layer)
    if name not in RUN_PREVIEWS:
        return None
    fixtures = summary.get("fixtures") or {}
    sar = fixtures.get("input_kind") == "sar_backscatter"
    role = name.removesuffix("_mask")
    if not name.endswith("_mask"):
        source = fixtures.get(role)
        if not source:
            return None
        if sar:
            return render_preview(Path(source), "sar", fixtures.get("input_scale", "linear"))
        return render_preview(Path(source), "labels")
    if not sar:
        # Label inputs are already the water masks.
        return render_preview(Path(fixtures.get(role) or ""), "labels")
    for step in (summary.get("success_chain") or {}).get("steps") or []:
        outputs = step.get("outputs") if isinstance(step.get("outputs"), dict) else {}
        if step.get("target") == role and outputs.get("output_path"):
            return render_preview(Path(outputs["output_path"]), "labels")
    return None


_LLM_MODEL_ID: dict[str, str] = {}


def llm_model_id() -> str:
    if "id" not in _LLM_MODEL_ID:
        models = _RUNNER.request_json(f"{_RUNNER.DEFAULT_LLM_URL}/v1/models", timeout=10)
        _LLM_MODEL_ID["id"] = str(((models or {}).get("data") or [{}])[0].get("id") or "local")
    return _LLM_MODEL_ID["id"]


SYNTHETIC_WORDS = ("合成", "测试", "synthetic", "test")


def _mentions_candidate(mentioned: str, option: dict[str, Any]) -> bool:
    """True when the quoted words share a two-character piece with the candidate's description."""
    text = " ".join(str(option.get(key) or "") for key in ("title", "place", "event", "label")).lower()
    words = mentioned.lower()
    if option.get("category") in DEVELOPER_CATEGORIES and any(word in words for word in SYNTHETIC_WORDS):
        return True
    pieces = {words[index : index + 2] for index in range(len(words) - 1)} - {"  "}
    return any(piece.strip() and piece in text for piece in pieces)


def resolve_input_set(prompt: str, current: str | None) -> tuple[int, dict[str, Any]]:
    """Ask the local model which input set, if any, the task explicitly refers to."""
    options = fixture_options()
    candidates = [
        {
            key: option.get(key)
            for key in ("id", "title", "place", "event", "period", "sensor", "category")
            if option.get(key)
        }
        for option in options
    ]
    request = {
        "task": prompt[:MAX_PROMPT_LENGTH],
        "candidates": candidates,
        "instructions": [
            "mentioned must be the exact words copied from the task that name a place, event, date or "
            "data type; use null if the task names none (for example 'analyse this pair').",
            "Pick the candidate those words refer to.",
            "If the task asks for synthetic or test data, pick the candidate whose category is synthetic.",
            "If the named place or event matches no candidate, return input_set null and unmatched true.",
            "Do not guess.",
        ],
        "return_schema": {
            "input_set": "candidate id or null",
            "mentioned": "exact words from the task, or null",
            "unmatched": False,
            "reason": "one short sentence in Simplified Chinese",
        },
    }
    try:
        response = _RUNNER.request_json(
            f"{_RUNNER.DEFAULT_LLM_URL}/v1/chat/completions",
            method="POST",
            payload={
                "model": llm_model_id(),
                "temperature": 0,
                "max_tokens": 256,
                "response_format": {"type": "json_object"},
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [
                    {"role": "system", "content": "Return only valid JSON. Do not include a markdown fence."},
                    {"role": "user", "content": "/no_think\n" + json.dumps(request, ensure_ascii=False)},
                ],
            },
            timeout=60,
        )
        parsed = _RUNNER.parse_json_content(str(response["choices"][0]["message"].get("content") or ""))
    except (_RUNNER.DemoError, KeyError, IndexError, TypeError) as exc:
        return 502, {"status": "error", "error": f"无法识别任务中提到的数据：{exc}"}
    by_id = {option["id"]: option for option in options}
    chosen = parsed.get("input_set")
    chosen = chosen if isinstance(chosen, str) and chosen in by_id else None
    mentioned = parsed.get("mentioned")
    mentioned = mentioned.strip() if isinstance(mentioned, str) and mentioned.strip() else None
    # A 4B model sometimes "finds" data the task never named. Keep its answer
    # only when the quoted words really occur in the task and overlap the
    # chosen candidate's description; otherwise fall back to the current set.
    grounded = mentioned is not None and mentioned in prompt
    if chosen and not (grounded and _mentions_candidate(mentioned, by_id[chosen])):
        chosen, mentioned = None, (mentioned if grounded else None)
    if chosen is None:
        # Deterministic fallback for explicit requests for the synthetic fixture.
        word = next((word for word in ("合成", "synthetic") if word in prompt.lower()), None)
        synthetic = next((option for option in options if option.get("category") == "synthetic"), None)
        if word and synthetic:
            chosen, mentioned, grounded = synthetic["id"], word, True
    unmatched = bool(parsed.get("unmatched")) and chosen is None and grounded
    return 200, {
        "status": "ok",
        "input_set": chosen,
        "mentioned": mentioned if grounded else None,
        "unmatched": unmatched,
        "reason": str(parsed.get("reason") or "")[:200] if (chosen or unmatched) else "",
        "current": current,
        "model": _LLM_MODEL_ID.get("id"),
    }


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
    scenario = workspace_scenarios.TASK_SCENARIOS.get(str(summary.get("scenario") or "water_change")) or {}
    return {
        "run_id": summary.get("run_id"),
        "created_at": summary.get("created_at"),
        "status": "complete",
        "scenario": scenario.get("id"),
        "scenario_title": scenario.get("title"),
        "input_title": fixtures.get("title") or fixtures.get("label"),
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


def run_job(
    job_id: str,
    prompt: str,
    fixture: dict[str, Any],
    conversation_turns: int,
    self_check: bool = False,
    resolution: dict[str, Any] | None = None,
) -> None:
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
    if self_check:
        command.append("--self-check")
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
                "self_check": self_check,
                "input_resolution": resolution,
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

    def _send_png(self, path: Path | None) -> None:
        if path is None:
            self._send(404, {"status": "not_found", "error": "预览不可用"})
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=300")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()

    def _normalize_path(self) -> None:
        # /api/workspace/* is the public prefix; /api/water-demo/* stays as an alias.
        if self.path.startswith("/api/workspace/") or self.path == "/api/workspace":
            self.path = "/api/water-demo" + self.path[len("/api/workspace") :]

    def _stream(self, events: Any) -> None:
        """Send server-sent events; compression proxies skip bodies marked no-transform."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        try:
            for event, data in events:
                self.wfile.write(f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _authorized(self) -> bool:
        if not REQUIRE_LOGIN or session_is_valid(request_token(self.headers)):
            return True
        self.close_connection = True
        self._send(401, {"status": "unauthorized", "error": "请先登录 Terraflow。"})
        return False

    def do_GET(self) -> None:
        self._normalize_path()
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/")
        if path != "/api/water-demo/health" and not self._authorized():
            return
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
        if path == "/api/water-demo/scenarios":
            self._send(200, {"status": "ok", "scenarios": workspace_scenarios.scenario_catalog()})
            return
        if path == "/api/water-demo/conversations":
            self._send(200, {"status": "ok", "conversations": AGENT.store.list()})
            return
        if path.startswith("/api/water-demo/conversations/"):
            conversation = AGENT.store.load(path.rsplit("/", 1)[-1])
            self._send(200 if conversation else 404, conversation or {"status": "not_found", "error": "会话不存在"})
            return
        if path == "/api/water-demo/tools":
            self._send(*tool_catalog())
            return
        if path == "/api/water-demo/canvas-flows":
            self._send(200, canvas_index())
            return
        if path == "/api/water-demo/scenes":
            self._send(200, LIBRARY.catalog())
            return
        if path == "/api/water-demo/data-types":
            self._send(200, {"status": "ok", "types": data_types.public_specs(),
                             "class_presets": data_types.CLASS_PRESETS,
                             "collections": CATALOG.upload_collections(),
                             "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024)})
            return
        if path == "/api/water-demo/catalog":
            self._send(200, CATALOG.listing())
            return
        if path == "/api/water-demo/items":
            query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
            self._send(*CATALOG.search(query))
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
        if len(parts) == 5 and parts[:3] == ["api", "water-demo", "scenes"] and parts[4] == "preview":
            self._send_png(LIBRARY.preview(parts[3]))
            return
        if len(parts) == 5 and parts[:3] == ["api", "water-demo", "items"] and parts[4] == "preview":
            self._send_png(CATALOG.preview(parts[3]))
            return
        if len(parts) == 4 and parts[:3] == ["api", "water-demo", "items"]:
            found = CATALOG.get(parts[3])
            if found is None:
                self._send(404, {"status": "not_found", "error": "数据不存在"})
            else:
                item, collection = found
                self._send(200, {"status": "ok", "item": item, "collection": collection})
            return
        if len(parts) == 6 and parts[:3] == ["api", "water-demo", "input-sets"] and parts[4] == "preview":
            self._send_png(input_preview(parts[3], parts[5]))
            return
        if len(parts) == 5 and parts[:3] == ["api", "water-demo", "runs"] and parts[4] == "result":
            detail = run_result(parts[3])
            self._send(200 if detail else 404, detail or {"status": "not_found", "error": "运行记录不存在"})
            return
        if len(parts) == 6 and parts[:3] == ["api", "water-demo", "runs"] and parts[4] == "preview":
            self._send_png(run_preview(parts[3], parts[5]))
            return
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

    def do_DELETE(self) -> None:
        self._normalize_path()
        if not self._authorized():
            return
        parts = [unquote(part) for part in urlsplit(self.path).path.split("/") if part]
        if len(parts) == 4 and parts[:3] == ["api", "water-demo", "conversations"]:
            deleted = AGENT.store.delete(parts[3])
            self._send(200 if deleted else 404, {"status": "deleted" if deleted else "not_found"})
            return
        if len(parts) == 4 and parts[:3] == ["api", "water-demo", "canvas-flows"] and _SAFE_ID.fullmatch(parts[3]):
            self._send(*forget_canvas_flow(parts[3]))
            return
        self._send(404, {"status": "not_found", "error": "unknown endpoint"})

    def do_PUT(self) -> None:
        self._normalize_path()
        if not self._authorized():
            return
        parsed = urlsplit(self.path)
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        scene_upload = len(parts) == 5 and parts[:3] == ["api", "water-demo", "scene-uploads"] and parts[4] == "files"
        data_upload = len(parts) == 5 and parts[:3] == ["api", "water-demo", "data-uploads"] and parts[4] == "files"
        if not (scene_upload or data_upload) and (len(parts) != 5 or parts[:3] != ["api", "water-demo", "uploads"]):
            self._send(404, {"status": "not_found", "error": "unknown endpoint"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        filename = unquote(self.headers.get("X-File-Name", "") or "")
        if data_upload:
            status, body = CATALOG.store_file(parts[3], self.rfile, length, filename)
        elif scene_upload:
            query = parse_qs(parsed.query)
            status, body = LIBRARY.store_file(
                parts[3],
                self.rfile,
                length,
                filename,
                (query.get("acquired") or [""])[0].strip(),
                (query.get("label") or [""])[0],
            )
        else:
            status, body = store_upload_file(parts[3], parts[4], self.rfile, length, filename)
        if status != 200:
            # Part of the body may be unread; do not reuse this connection.
            self.close_connection = True
        self._send(status, body)

    def do_POST(self) -> None:
        self._normalize_path()
        if not self._authorized():
            return
        path = urlsplit(self.path).path.rstrip("/")
        parts = [unquote(part) for part in path.split("/") if part]
        if path == "/api/water-demo/conversations":
            payload = self._read_json_body() or {}
            self._send(201, AGENT.store.create(str(payload.get("title") or "") or None))
            return
        if len(parts) == 5 and parts[:3] == ["api", "water-demo", "conversations"] and parts[4] == "messages":
            payload = self._read_json_body()
            content = str((payload or {}).get("content") or "").strip()[:MAX_PROMPT_LENGTH]
            if not content or AGENT.store.load(parts[3]) is None:
                self._send(400, {"status": "invalid_request", "error": "会话不存在或消息为空。"})
                return
            attachments = [str(item) for item in (payload.get("attachments") or []) if isinstance(item, str)][:4]
            self._stream(AGENT.handle_message(parts[3], content, attachments))
            return
        if len(parts) == 7 and parts[:3] == ["api", "water-demo", "conversations"] and parts[4] == "plans":
            if parts[6] == "execute":
                payload = self._read_json_body() or {}
                inputs = payload.get("inputs")
                inputs = {str(k): str(v) for k, v in inputs.items()} if isinstance(inputs, dict) else None
                self._stream(AGENT.execute(parts[3], parts[5], bool(payload.get("self_check")), inputs))
                return
            if parts[6] == "cancel":
                updated = AGENT.cancel(parts[3], parts[5])
                self._send(200 if updated else 409, updated or {"status": "conflict", "error": "计划不能取消。"})
                return
        if path == "/api/water-demo/resolve-input":
            payload = self._read_json_body()
            if payload is None or not str(payload.get("prompt") or "").strip():
                self._send(400, {"status": "invalid_request", "error": "需要任务描述。"})
                return
            current = payload.get("current")
            self._send(*resolve_input_set(str(payload["prompt"]), str(current) if current else None))
            return
        if path == "/api/water-demo/data-uploads":
            payload = self._read_json_body()
            if payload is None:
                self._send(400, {"status": "invalid_request", "error": "请求体必须是 JSON。"})
                return
            self._send(*CATALOG.create_upload(payload))
            return
        if len(parts) == 5 and parts[:3] == ["api", "water-demo", "data-uploads"] and parts[4] == "finalize":
            payload = self._read_json_body()
            if payload is None:
                self._send(400, {"status": "invalid_request", "error": "请求体必须是 JSON。"})
                return
            self._send(*CATALOG.finalize(parts[3], payload))
            return
        if path == "/api/water-demo/scene-uploads":
            payload = self._read_json_body()
            if payload is None:
                self._send(400, {"status": "invalid_request", "error": "请求体必须是 JSON。"})
                return
            self._send(*LIBRARY.create_upload(payload))
            return
        if len(parts) == 5 and parts[:3] == ["api", "water-demo", "scene-uploads"] and parts[4] == "finalize":
            self._send(*LIBRARY.finalize(parts[3]))
            return
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
        if path == "/api/water-demo/canvas-templates":
            payload = self._read_json_body() or {}
            inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
            self._send(
                *canvas_template_for_scenes(
                    str(payload.get("scenario") or ""),
                    {str(k): str(v) for k, v in inputs.items()},
                    token=request_token(self.headers),
                )
            )
            return
        if len(parts) == 5 and parts[:3] == ["api", "water-demo", "input-sets"] and parts[4] == "canvas":
            force = parse_qs(urlsplit(self.path).query).get("force", ["0"])[0] == "1"
            self._send(*canvas_flow_for_input_set(parts[3], force=force, token=request_token(self.headers)))
            return
        if len(parts) == 5 and parts[:3] == ["api", "water-demo", "runs"] and parts[4] == "canvas":
            force = parse_qs(urlsplit(self.path).query).get("force", ["0"])[0] == "1"
            self._send(*canvas_flow_for_run(parts[3], force=force, token=request_token(self.headers)))
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
        self_check = bool(payload.get("self_check"))
        resolution = payload.get("input_resolution")
        if isinstance(resolution, dict):
            resolution = {
                key: resolution.get(key)
                for key in ("input_set", "mentioned", "unmatched", "reason", "decision", "model")
                if key in resolution
            }
        else:
            resolution = None
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
                "self_check": self_check,
            }
            thread = threading.Thread(
                target=run_job,
                args=(job_id, prompt, fixture, conversation_turns, self_check, resolution),
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


# Imported here because the agent and scenario modules use the functions above.
import data_catalog  # noqa: E402
import data_types  # noqa: E402
import scene_library  # noqa: E402
import workspace_agent  # noqa: E402
import workspace_scenarios  # noqa: E402

LIBRARY = scene_library.SceneLibrary(sys.modules[__name__])
CATALOG = data_catalog.DataCatalog(sys.modules[__name__])
AGENT = workspace_agent.WorkspaceAgent(sys.modules[__name__])


def run_result(run_id: str) -> dict[str, Any] | None:
    summary_file = summary_path_for(run_id)
    summary = read_json(summary_file) if summary_file else None
    if not isinstance(summary, dict):
        return None
    return {
        "run_id": run_id,
        "created_at": summary.get("created_at"),
        "prompt": (summary.get("request") or {}).get("prompt"),
        "dataset": {
            key: (summary.get("fixtures") or {}).get(key)
            for key in ("title", "label", "input_kind", "type", "scene_ids")
        },
        "scenario": summary.get("scenario") or "water_change",
        "model": (summary.get("model") or {}).get("model_used"),
        "plan": (summary.get("plan") or {}).get("execution_order"),
        "result": workspace_scenarios.build_result(
            summary, lambda name: f"/api/workspace/runs/{run_id}/preview/{name}"
        ),
    }


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

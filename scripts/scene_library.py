"""Scene library behind the workspace data page.

The unit of data is a scene: one single-band raster plus what is known about
it (acquisition date, sensor, data kind, place, provenance). Scenes that
belong together (one event, one upload) share a collection. Roles such as
"before" and "after" are not stored with the data; a plan assigns them when
a scenario uses the scenes.

Three sources are listed, none of them moved or rewritten:

- earlier paired input sets (``demo-fixtures/`` and ``web-uploads/``), each
  pair read as a collection of two scenes, plus its self-check raster;
- scene uploads (``scene-uploads/<id>/``), one or more files per upload;
- self-check samples generated on demand (``self-check/<id>/``).

For a run, ``prepare_inputs`` builds ``run-inputs/<key>/`` with symbolic
links named as the runner expects (before.tif/after.tif or scene.tif) and a
fixture.json composed from the scenes' metadata. ``scene-index.json`` lists
scene paths for the Terraflow Scene Input component.

Standard library only; raster work runs in the geo Python (``web.GEO_PYTHON``).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

_UPLOAD_ID = re.compile(r"^sup_[0-9a-f]{12}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_FILES_PER_UPLOAD = 12
SELF_CHECK_COLUMNS = 50
DEVELOPER_CATEGORIES = {"synthetic", "test"}
KIND_LABELS = {"sar_backscatter": "SAR 后向散射", "water_labels": "0/1 水体标签"}
SCENE_SOURCE_KEYS = ("item_id", "datetime", "platform", "relative_orbit", "orbit_state", "polarization", "stac_item_url")

_MAKE_SELF_CHECK = """
import json, sys
import rasterio
from rasterio.windows import Window
source, target, columns = sys.argv[1], sys.argv[2], int(sys.argv[3])
with rasterio.open(source) as src:
    removed = max(1, min(columns, src.width // 4 or 1))
    window = Window(0, 0, src.width - removed, src.height)
    data = src.read(window=window)
    profile = src.profile.copy()
    profile.update(width=src.width - removed, transform=src.window_transform(window))
with rasterio.open(target, "w", **profile) as dst:
    dst.write(data)
print(json.dumps({"columns_removed": removed}))
"""


# Same rule as Terrabox geo_water: no nominal-pixel areas on Mercator-family grids.
MERCATOR_CODES = {"EPSG:3857", "EPSG:900913", "EPSG:3785", "EPSG:3395", "EPSG:54004"}


def area_distorting(grid: dict[str, Any]) -> bool:
    method = str(grid.get("projection_method") or "").lower()
    return grid.get("crs") in MERCATOR_CODES or ("mercator" in method and "transverse" not in method)


def _area_km2(grid: dict[str, Any]) -> float | None:
    """Rough footprint for the data card; none where nominal pixel areas misstate ground area."""
    shape, resolution = grid.get("shape"), grid.get("resolution")
    if not grid.get("projected") or not shape or not resolution or area_distorting(grid):
        return None
    return round(shape[0] * shape[1] * abs(resolution[0]) * abs(resolution[1]) / 1e6, 2)


def _clean(text: Any, limit: int = 80) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


class SceneLibrary:
    def __init__(self, web: Any) -> None:
        self.web = web
        root = web.RUNTIME_DIR
        self.upload_root = root / "scene-uploads"
        self.self_check_root = root / "self-check"
        self.run_input_root = root / "run-inputs"
        self.index_path = root / "scene-index.json"
        self.lock = threading.RLock()

    # ------------------------------------------------------------------ listing

    def _legacy(self) -> list[dict[str, Any]]:
        """Earlier before/after input sets, each read as a collection of two scenes."""
        scenes = []
        for option in self.web.fixture_options():
            path = Path(option["path"])
            meta = self.web.read_json(path / "fixture.json") or {}
            files = meta.get("files") or {}
            category = option["category"]
            grid_meta = meta.get("grid") or {}
            for sequence, role in enumerate(("before", "after")):
                source = (meta.get("scenes") or {}).get(role) or {}
                acquired = str(source.get("datetime") or "")[:10] or None
                profile = (files.get(role) or {}).get("profile") or {}
                grid = {
                    "crs": grid_meta.get("crs") or profile.get("crs"),
                    "shape": grid_meta.get("shape") or profile.get("shape"),
                    "resolution": [grid_meta["resolution_m"]] * 2 if grid_meta.get("resolution_m") else profile.get("resolution"),
                    "projected": bool(grid_meta.get("crs")) or bool(profile.get("projected")),
                }
                scenes.append(
                    {
                        "id": f"{option['id']}.{role}",
                        "title": (acquired + " · " + option["sensor"]) if acquired
                        else f"{option['title']} · {'前期' if role == 'before' else '后期'}",
                        "collection_id": option["id"],
                        "collection_title": option["title"],
                        "place": option.get("place"),
                        "acquired": acquired,
                        "sequence": sequence,
                        "sensor": option["sensor"],
                        "input_kind": option["input_kind"],
                        "input_scale": meta.get("input_scale", "linear"),
                        "category": category,
                        "source_type": option.get("type") or "synthetic_engineering_fixture",
                        "event": option.get("event"),
                        "grid": grid,
                        "source": {key: source.get(key) for key in SCENE_SOURCE_KEYS if source.get(key) is not None},
                        "original_name": (files.get(role) or {}).get("original_name"),
                        "claim_boundary": option.get("claim_boundary"),
                        "path": str(path / f"{role}.tif"),
                    }
                )
            if option.get("has_mismatch"):
                after = scenes[-1]
                removed = (meta.get("mismatch") or {}).get("columns_removed")
                scenes.append(
                    {
                        **after,
                        "id": f"{option['id']}.mismatch",
                        "title": f"自检样本 · 由{after['title']}裁去 {removed} 列" if removed
                        else f"自检样本 · 与{after['title']}尺寸不一致",
                        "sequence": 2,
                        "category": "test",
                        "self_check_for": after["id"],
                        "grid": {**after["grid"], "shape": None},
                        "path": str(path / "mismatch.tif"),
                    }
                )
        return scenes

    def _uploaded(self) -> list[dict[str, Any]]:
        scenes = []
        if not self.upload_root.is_dir():
            return scenes
        for directory in sorted(self.upload_root.iterdir(), reverse=True):
            batch = self.web.read_json(directory / "batch.json")
            if not isinstance(batch, dict) or batch.get("status") != "ready":
                continue
            for item in batch.get("files") or []:
                profile = item.get("profile") or {}
                acquired = item.get("acquired")
                label = item.get("label") or acquired or item.get("original_name")
                scenes.append(
                    {
                        "id": f"{batch['upload_id']}.{item['n']}",
                        "title": f"{label} · {batch.get('sensor') or KIND_LABELS.get(batch['input_kind'], '')}",
                        "collection_id": batch["upload_id"],
                        "collection_title": batch.get("collection_title") or "上传的影像",
                        "place": batch.get("place") or None,
                        "acquired": acquired,
                        "sequence": item["n"],
                        "sensor": batch.get("sensor") or KIND_LABELS.get(batch["input_kind"], ""),
                        "input_kind": batch["input_kind"],
                        "input_scale": batch.get("input_scale", "linear"),
                        "category": batch.get("category") or "uploaded",
                        "source_type": "uploaded",
                        "event": batch.get("event") or None,
                        "grid": {
                            key: profile.get(key) for key in ("crs", "shape", "resolution", "projected", "projection_method")
                        },
                        "source": {},
                        "original_name": item.get("original_name"),
                        "uploaded_at": batch.get("created_at"),
                        "claim_boundary": "上传数据由上传者提供，来源、成像时间和标签未经核实。",
                        "path": str(directory / f"{item['n']}.tif"),
                    }
                )
        return scenes

    def _self_checks(self, known: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        scenes = []
        if not self.self_check_root.is_dir():
            return scenes
        for directory in sorted(self.self_check_root.iterdir()):
            meta = self.web.read_json(directory / "sample.json")
            parent = known.get(str((meta or {}).get("derived_from")))
            if not parent or not (directory / "sample.tif").is_file():
                continue
            scenes.append(
                {
                    **parent,
                    "id": f"selfcheck.{directory.name}",
                    "title": f"自检样本 · 由{parent['title']}裁去 {meta.get('columns_removed')} 列",
                    "collection_id": "self-check",
                    "collection_title": "门控自检样本",
                    "category": "test",
                    "self_check_for": parent["id"],
                    "grid": {**parent["grid"], "shape": None},
                    "path": str(directory / "sample.tif"),
                }
            )
        return scenes

    def scenes(self) -> list[dict[str, Any]]:
        scenes = self._uploaded() + self._legacy()
        scenes += self._self_checks({scene["id"]: scene for scene in scenes})
        for scene in scenes:
            scene["audience"] = "developer" if scene["category"] in DEVELOPER_CATEGORIES else "user"
            scene["area_km2"] = _area_km2(scene["grid"])
            scene["preview"] = f"/api/workspace/scenes/{scene['id']}/preview"
            scene["kind_label"] = KIND_LABELS.get(scene["input_kind"], scene["input_kind"])
        self._write_index(scenes)
        return scenes

    def get(self, scene_id: str) -> dict[str, Any] | None:
        return next((scene for scene in self.scenes() if scene["id"] == scene_id), None)

    def catalog(self) -> dict[str, Any]:
        scenes = self.scenes()
        collections: dict[str, dict[str, Any]] = {}
        for scene in scenes:
            entry = collections.setdefault(
                scene["collection_id"],
                {
                    "id": scene["collection_id"],
                    "title": scene["collection_title"],
                    "place": scene.get("place"),
                    "event": scene.get("event"),
                    "category": scene["category"],
                    "audience": scene["audience"],
                    "scene_ids": [],
                },
            )
            entry["scene_ids"].append(scene["id"])
            if scene["audience"] == "user":  # a collection is shown to users if any scene is
                entry["audience"], entry["category"] = "user", scene["category"]
        for entry in collections.values():
            dates = sorted(s["acquired"] for s in scenes if s["collection_id"] == entry["id"] and s.get("acquired"))
            entry["period"] = (dates[0] if dates[0] == dates[-1] else f"{dates[0]} → {dates[-1]}") if dates else None
        public = [{key: value for key, value in scene.items() if key != "path"} for scene in scenes]
        return {"status": "ok", "scenes": public, "collections": list(collections.values())}

    def _write_index(self, scenes: list[dict[str, Any]]) -> None:
        index = {
            scene["id"]: {
                key: scene.get(key)
                for key in ("path", "title", "collection_title", "acquired", "sensor", "input_kind", "input_scale", "category")
            }
            for scene in scenes
        }
        text = json.dumps(index, ensure_ascii=False, indent=1, sort_keys=True)
        with self.lock:
            try:
                if self.index_path.is_file() and self.index_path.read_text(encoding="utf-8") == text:
                    return
                temporary = self.index_path.with_suffix(".tmp")
                temporary.write_text(text, encoding="utf-8")
                temporary.replace(self.index_path)
            except OSError:
                return

    def preview(self, scene_id: str) -> Path | None:
        scene = self.get(scene_id)
        if scene is None:
            return None
        if scene["input_kind"] == "sar_backscatter":
            return self.web.render_preview(Path(scene["path"]), "sar", scene.get("input_scale", "linear"))
        return self.web.render_preview(Path(scene["path"]), "labels")

    # ------------------------------------------------------------------ uploads

    def create_upload(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        input_kind = str(payload.get("input_kind") or "")
        input_scale = str(payload.get("input_scale") or "linear")
        if input_kind not in KIND_LABELS:
            return 400, {"status": "invalid_request", "error": "数据类型必须是 SAR 后向散射或 0/1 水体标签。"}
        if input_scale not in ("linear", "db"):
            return 400, {"status": "invalid_request", "error": "SAR 数值尺度必须是 linear 或 db。"}
        category = "test" if payload.get("test") else "uploaded"
        upload_id = f"sup_{uuid.uuid4().hex[:12]}"
        directory = self.upload_root / upload_id
        directory.mkdir(parents=True)
        batch = {
            "schema_version": "terraflow-scene-upload.v1",
            "upload_id": upload_id,
            "status": "pending",
            "collection_title": _clean(payload.get("collection_title")) or "上传的影像",
            "place": _clean(payload.get("place")),
            "event": _clean(payload.get("event"), 200),
            "sensor": _clean(payload.get("sensor"), 60),
            "input_kind": input_kind,
            "input_scale": input_scale,
            "category": category,
            "created_at": self.web.utc_now(),
            "files": [],
        }
        self._save_batch(directory, batch)
        return 201, {"status": "created", "upload_id": upload_id}

    def _batch(self, upload_id: str) -> tuple[Path, dict[str, Any]] | None:
        if not _UPLOAD_ID.fullmatch(upload_id):
            return None
        directory = self.upload_root / upload_id
        batch = self.web.read_json(directory / "batch.json")
        return (directory, batch) if isinstance(batch, dict) else None

    def _save_batch(self, directory: Path, batch: dict[str, Any]) -> None:
        temporary = directory / "batch.json.tmp"
        temporary.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(directory / "batch.json")

    def store_file(
        self, upload_id: str, stream: Any, length: int, filename: str, acquired: str, label: str
    ) -> tuple[int, dict[str, Any]]:
        found = self._batch(upload_id)
        if found is None:
            return 404, {"status": "not_found", "error": "上传记录不存在。"}
        directory, batch = found
        if batch.get("status") != "pending":
            return 409, {"status": "conflict", "error": "该上传已经完成，不能再添加文件。"}
        if len(batch["files"]) >= MAX_FILES_PER_UPLOAD:
            return 400, {"status": "invalid_request", "error": f"一次最多上传 {MAX_FILES_PER_UPLOAD} 景。"}
        if acquired and not _DATE.fullmatch(acquired):
            return 400, {"status": "invalid_request", "error": "成像日期格式应为 YYYY-MM-DD。"}
        if length <= 0:
            return 400, {"status": "invalid_request", "error": "文件为空。"}
        if length > self.web.MAX_UPLOAD_BYTES:
            return 413, {"status": "too_large", "error": f"单个文件不能超过 {self.web.MAX_UPLOAD_BYTES // (1024 * 1024)} MB。"}
        number = len(batch["files"]) + 1
        target = directory / f"{number}.tif"
        temporary = directory / f".{number}.part"
        digest = hashlib.sha256()
        remaining, head = length, b""
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
        if head not in self.web.TIFF_MAGIC:
            temporary.unlink(missing_ok=True)
            return 415, {"status": "unsupported_type", "error": f"{filename or '文件'} 不是 TIFF/GeoTIFF。"}
        temporary.replace(target)
        batch["files"].append(
            {
                "n": number,
                "original_name": re.sub(r"[^\w.\-一-鿿]", "_", filename)[:120],
                "acquired": acquired or None,
                "label": _clean(label, 40) or None,
                "bytes": length,
                "sha256": digest.hexdigest(),
            }
        )
        self._save_batch(directory, batch)
        return 200, {"status": "stored", "n": number, "bytes": length}

    def finalize(self, upload_id: str) -> tuple[int, dict[str, Any]]:
        found = self._batch(upload_id)
        if found is None:
            return 404, {"status": "not_found", "error": "上传记录不存在。"}
        directory, batch = found
        if not batch["files"]:
            return 400, {"status": "invalid_request", "error": "至少需要一个文件。"}
        paths = [str(directory / f"{item['n']}.tif") for item in batch["files"]]
        try:
            completed = subprocess.run(
                [self.web.GEO_PYTHON, "-I", "-c", self.web._VALIDATE_RASTER, *paths],
                capture_output=True, text=True, timeout=60, check=False,
            )
            profiles = json.loads(completed.stdout or "{}")
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            return 502, {"status": "validation_unavailable", "error": f"无法检查上传的栅格：{exc}"}
        problems = []
        for item, path in zip(batch["files"], paths):
            profile = profiles.get(path) or {"ok": False, "error": "未返回检查结果"}
            item["profile"] = profile
            name = item["original_name"]
            if not profile.get("ok"):
                problems.append(f"{name} 无法作为栅格打开（{profile.get('error')}）")
            elif not profile.get("crs"):
                problems.append(f"{name} 缺少坐标参考系")
            elif profile.get("count") != 1:
                problems.append(f"{name} 有 {profile.get('count')} 个波段，目前只支持单波段")
        if problems:
            self._save_batch(directory, batch)
            return 422, {"status": "invalid_raster", "error": "；".join(problems)}
        batch["status"] = "ready"
        self._save_batch(directory, batch)
        scenes = [scene for scene in self.catalog()["scenes"] if scene["collection_id"] == upload_id]
        return 200, {"status": "ready", "collection_id": upload_id, "scenes": scenes}

    # ------------------------------------------------------------------ run inputs

    def check_inputs(self, scenario: dict[str, Any], inputs: dict[str, str]) -> tuple[dict[str, dict[str, Any]], str | None]:
        """Resolve role -> scene id for a scenario; return the scenes or a reason they cannot be used."""
        roles = [item["role"] for item in scenario["inputs"]]
        scenes: dict[str, dict[str, Any]] = {}
        for role in roles:
            scene = self.get(str(inputs.get(role) or ""))
            if scene is None:
                return {}, f"「{scenario['title']}」缺少{self._role_label(scenario, role)}。"
            scenes[role] = scene
        if len({scene["id"] for scene in scenes.values()}) < len(roles):
            return scenes, "同一景不能同时作为两个输入。"
        kinds = {scene["input_kind"] for scene in scenes.values()}
        if len(kinds) > 1:
            return scenes, "选中的几景数据类型不同（" + "、".join(KIND_LABELS.get(k, k) for k in kinds) + "），不能直接比较。"
        kind = kinds.pop()
        if kind not in scenario["input_kinds"]:
            return scenes, f"「{scenario['title']}」不支持{KIND_LABELS.get(kind, kind)}数据。"
        if len({scene.get("input_scale") for scene in scenes.values()}) > 1:
            return scenes, "选中的几景 SAR 数值尺度不同（线性和 dB），不能直接比较。"
        return scenes, None

    @staticmethod
    def _role_label(scenario: dict[str, Any], role: str) -> str:
        return next((item["label"] for item in scenario["inputs"] if item["role"] == role), role)

    def self_check_sample(self, scene: dict[str, Any]) -> tuple[Path | None, dict[str, Any] | None]:
        """A raster deliberately off the scene's grid, for the gate self-check (made once per scene)."""
        stored = next(
            (item for item in self.scenes() if item.get("self_check_for") == scene["id"]), None
        )
        if stored:
            return Path(stored["path"]), {"scene_id": stored["id"], "derived_from": scene["id"]}
        key = hashlib.sha1(scene["id"].encode()).hexdigest()[:12]
        directory = self.self_check_root / key
        directory.mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(
                [self.web.GEO_PYTHON, "-I", "-c", _MAKE_SELF_CHECK, scene["path"], str(directory / "sample.tif"),
                 str(SELF_CHECK_COLUMNS)],
                capture_output=True, text=True, timeout=120, check=False,
            )
            made = json.loads(completed.stdout or "{}")
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return None, None
        if completed.returncode != 0:
            return None, None
        meta = {"derived_from": scene["id"], "columns_removed": made.get("columns_removed"), "created_at": self.web.utc_now()}
        (directory / "sample.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return directory / "sample.tif", {"scene_id": f"selfcheck.{key}", **meta}

    def prepare_inputs(
        self, task: str, scenes: dict[str, dict[str, Any]], key: str, self_check: bool
    ) -> tuple[Path, bool]:
        """Link the chosen scenes under run-inputs/<key>/ as the runner expects; return (dir, self_check_ready)."""
        directory = self.run_input_root / key
        directory.mkdir(parents=True, exist_ok=True)
        for old in directory.glob("*.tif"):
            old.unlink()
        names = {"scene": "scene.tif"} if task == "water_extent" else {"before": "before.tif", "after": "after.tif"}
        for role, name in names.items():
            (directory / name).symlink_to(Path(scenes[role]["path"]).resolve())
        first = next(iter(scenes.values()))
        categories = {scene["category"] for scene in scenes.values()}
        source_types = {scene.get("source_type") for scene in scenes.values()}
        if categories == {"synthetic"}:
            kind = "synthetic_engineering_fixture"
        elif "uploaded" in source_types:
            kind = "uploaded"
        elif len(source_types) == 1:
            kind = source_types.pop()
        else:
            kind = "scene_library"
        collections = {scene["collection_title"] for scene in scenes.values()}
        meta: dict[str, Any] = {
            "schema_version": "terraflow-workspace-inputs.v1",
            "type": kind,
            "category": "synthetic" if kind == "synthetic_engineering_fixture" else first["category"],
            "input_kind": first["input_kind"],
            "input_scale": first.get("input_scale", "linear"),
            "title": collections.pop() if len(collections) == 1 else " / ".join(s["title"] for s in scenes.values()),
            "label": "、".join(scene["title"] for scene in scenes.values()),
            "scene_ids": {role: scene["id"] for role, scene in scenes.items()},
            "scenes": {
                role: {
                    **scene.get("source", {}),
                    "scene_id": scene["id"],
                    "title": scene["title"],
                    "acquired": scene.get("acquired"),
                    "sensor": scene.get("sensor"),
                    "collection_title": scene["collection_title"],
                }
                for role, scene in scenes.items()
            },
            "claim_boundary": " ".join(sorted({str(s.get("claim_boundary") or "") for s in scenes.values()})).strip(),
        }
        ready = False
        if self_check and task == "water_change":
            sample, info = self.self_check_sample(scenes["after"])
            if sample is not None:
                (directory / "mismatch.tif").symlink_to(sample.resolve())
                meta["self_check_sample"] = info
                ready = True
        (directory / "fixture.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return directory, ready

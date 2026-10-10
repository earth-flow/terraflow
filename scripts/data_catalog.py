"""Workspace data catalog: every dataset is a STAC 1.1 Item.

Two kinds of records are listed:

- uploads made through the typed upload (``data-uploads/<id>/``), stored as
  Item and Collection JSON under ``catalog/``;
- earlier data (demo fixtures, paired uploads, single-band scene uploads and
  self-check samples), converted read-only from their old JSON each time the
  listing is refreshed. Their ids stay the same, so earlier conversations and
  runs still resolve. Old files and old JSON are not modified.

Data type rules (formats, form fields, checks, Item layout) live in
``data_types.py``. File headers are read with ``inspect_geodata.py`` and
previews drawn with ``render_raster_preview.py`` / ``render_vector_preview.py``
in the Terrabox geo environment; this module itself uses the standard library.

The analysis scenarios still take single-band SAR backscatter or 0/1 water
labels. ``scenario_scenes`` exposes the new Items that qualify in the shape
the scenarios use (SAR with a VV band; classification rasters whose class
table is water/non-water).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

import data_types as dt

_UPLOAD_ID = re.compile(r"^dup_[0-9a-f]{12}$")
_COLLECTION_ID = re.compile(r"^col_[0-9a-f]{12}$")
MAX_FILES_PER_UPLOAD = 12
TYPE_LABELS = {key: spec["label"] for key, spec in dt.DATA_TYPES.items()}
LEGACY_BACKSCATTER = {"sentinel1_rtc_gamma0": "gamma0_terrain"}


def _clean(text: Any, limit: int = 80) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _signature(path: Path) -> str:
    stat = path.stat()
    return f"{path.resolve()}|{stat.st_mtime_ns}|{stat.st_size}"


class DataCatalog:
    def __init__(self, web: Any) -> None:
        self.web = web
        root = web.RUNTIME_DIR
        self.catalog_root = root / "catalog"
        self.collection_root = self.catalog_root / "collections"
        self.upload_root = root / "data-uploads"
        self.profile_cache_path = self.catalog_root / "legacy-profiles.json"
        self.inspector = web.ROOT / "scripts" / "inspect_geodata.py"
        self.raster_renderer = web.ROOT / "scripts" / "render_raster_preview.py"
        self.vector_renderer = web.ROOT / "scripts" / "render_vector_preview.py"
        self.lock = threading.RLock()

    # ------------------------------------------------------------------ geo subprocesses

    def inspect(self, paths: list[str], timeout: int = 180) -> dict[str, dict[str, Any]]:
        if not paths:
            return {}
        try:
            completed = subprocess.run(
                [self.web.GEO_PYTHON, "-I", str(self.inspector), "inspect", *paths],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
            result = json.loads(completed.stdout or "{}")
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            return {p: {"ok": False, "error": f"无法检查文件：{exc}"} for p in paths}
        return {p: result.get(p) or {"ok": False, "error": "未返回检查结果"} for p in paths}

    def _legacy_profiles(self, paths: list[str]) -> dict[str, dict[str, Any]]:
        """Profiles of earlier files, cached by path, modification time and size."""
        with self.lock:
            cache = self.web.read_json(self.profile_cache_path) or {}
            wanted = {}
            for path in paths:
                file = Path(path)
                if file.is_file():
                    wanted[path] = _signature(file)
            missing = [p for p, sig in wanted.items() if (cache.get(p) or {}).get("signature") != sig]
            if missing:
                for path, profile in self.inspect(missing).items():
                    cache[path] = {"signature": wanted[path], "profile": profile}
                _write_json(self.profile_cache_path, cache)
            return {p: (cache.get(p) or {}).get("profile") or {} for p in wanted}

    # ------------------------------------------------------------------ listing

    def _uploaded_items(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        out = []
        if not self.collection_root.is_dir():
            return out
        # Most recently updated group first; collection.json is rewritten whenever items are added.
        directories = [d for d in self.collection_root.iterdir() if (d / "collection.json").is_file()]
        directories.sort(key=lambda d: (d / "collection.json").stat().st_mtime_ns, reverse=True)
        for directory in directories:
            collection = self.web.read_json(directory / "collection.json")
            if not isinstance(collection, dict):
                continue
            for path in sorted((directory / "items").glob("*.json")):
                item = self.web.read_json(path)
                if isinstance(item, dict) and item.get("type") == "Feature":
                    out.append((item, collection))
        return out

    def _legacy_items(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        scenes = self.web.LIBRARY.legacy_scenes()
        profiles = self._legacy_profiles([scene["path"] for scene in scenes])
        collections: dict[str, dict[str, Any]] = {}
        out = []
        for scene in scenes:
            profile = profiles.get(scene["path"])
            if not profile or not profile.get("ok"):
                continue
            item = legacy_item(scene, profile)
            collection = collections.setdefault(
                scene["collection_id"],
                {
                    "type": "Collection",
                    "id": scene["collection_id"],
                    "title": scene["collection_title"],
                    "workspace:data_type": item["properties"]["workspace:data_type"],
                    "workspace:place": scene.get("place"),
                    "workspace:event": scene.get("event"),
                    "workspace:legacy": True,
                },
            )
            out.append((item, collection))
        return out

    def entries(self) -> list[dict[str, Any]]:
        """Items with the fields the data page shows; newest uploads first, then earlier data."""
        out = []
        for item, collection in self._uploaded_items() + self._legacy_items():
            out.append({"stac": item, "view": self._view(item, collection)})
        return out

    def _view(self, item: dict[str, Any], collection: dict[str, Any]) -> dict[str, Any]:
        p = item["properties"]
        data_type = p.get("workspace:data_type")
        day = None if p.get("workspace:date_unknown") else dt.item_date(item)
        end = str(p.get("end_datetime") or "")[:10]
        period = f"{day} → {end}" if data_type == "dem" and end and end != day else day
        facts = [period, TYPE_LABELS.get(data_type, data_type)]
        data_asset = item["assets"].get("data") or next(iter(item["assets"].values()))
        bands = data_asset.get("bands") or []
        if data_type == "sar":
            facts.append("/".join(p.get("sar:polarizations") or []))
            if p.get("sat:orbit_state"):
                facts.append({"ascending": "升轨", "descending": "降轨"}.get(p["sat:orbit_state"], p["sat:orbit_state"])
                             + (f" {p['sat:relative_orbit']}" if p.get("sat:relative_orbit") else ""))
        elif data_type == "optical":
            names = [b.get("eo:common_name") for a in item["assets"].values() for b in a.get("bands") or [] if b.get("eo:common_name")]
            facts.append(f"{len(names)} 个波段")
            if "cloud-mask" in item["assets"]:
                facts.append("含云掩膜")
        elif data_type == "dem":
            facts.append(dt.SURFACES.get(p.get("workspace:elevation_surface"), "").split("（")[0])
        elif data_type == "vector":
            facts.append(dt.VECTOR_PURPOSES.get(p.get("workspace:purpose"), ""))
            facts.append(f"{data_asset.get('table:row_count')} 个要素")
        elif data_type == "classification":
            facts.append(f"{len(data_asset.get('classification:classes') or [])} 类")
        grid = data_asset.get("proj:code") or ("自定义坐标系" if data_asset.get("proj:wkt2") else None)
        title = p.get("title") or f"{day or '日期未记录'} · {TYPE_LABELS.get(data_type, '')}"
        usable = self._usable_in(item)
        return {
            "id": item["id"],
            "title": title,
            "collection_id": collection["id"],
            "collection_title": collection.get("title") or collection["id"],
            "data_type": data_type,
            "type_label": TYPE_LABELS.get(data_type, data_type),
            "date": day,
            "place": p.get("workspace:place") or collection.get("workspace:place"),
            "event": p.get("workspace:event") or collection.get("workspace:event"),
            "origin": p.get("workspace:origin"),
            "origin_label": dt.ORIGINS.get(p.get("workspace:origin"), ""),
            "verification": p.get("workspace:verification"),
            "verification_label": dt.VERIFICATION.get(p.get("workspace:verification"), ""),
            "audience": p.get("workspace:audience", "user"),
            "legacy": bool(collection.get("workspace:legacy")),
            "self_check_for": p.get("workspace:self_check_for"),
            "facts": [f for f in facts if f],
            "grid": grid,
            "band_count": len(bands),
            "preview": f"/api/workspace/items/{item['id']}/preview",
            "usable_in": usable,
            "capabilities": capabilities(item),
        }

    @staticmethod
    def _usable_in(item: dict[str, Any]) -> list[str]:
        """Scenarios with at least one input slot this item can fill."""
        import workspace_scenarios

        if item["properties"].get("workspace:self_check_for"):
            return []
        caps = set(capabilities(item))
        return [
            spec["id"] for spec in workspace_scenarios.TASK_SCENARIOS.values()
            if any(caps & set(slot["accepts"]) for slot in spec["slots"])
        ]

    def collections(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for entry in entries:
            view = entry["view"]
            group = groups.setdefault(
                view["collection_id"],
                {
                    "id": view["collection_id"],
                    "title": view["collection_title"],
                    "data_type": view["data_type"],
                    "type_label": view["type_label"],
                    "place": view["place"],
                    "event": view["event"],
                    "legacy": view["legacy"],
                    "audience": "developer",
                    "item_ids": [],
                    "dates": [],
                },
            )
            group["item_ids"].append(view["id"])
            if view["date"]:
                group["dates"].append(view["date"])
            if view["audience"] == "user":
                group["audience"] = "user"
        for group in groups.values():
            dates = sorted(group.pop("dates"))
            group["period"] = (dates[0] if dates[0] == dates[-1] else f"{dates[0]} → {dates[-1]}") if dates else None
        return list(groups.values())

    def listing(self) -> dict[str, Any]:
        entries = self.entries()
        return {"status": "ok", "items": [e["view"] for e in entries], "collections": self.collections(entries)}

    def get(self, item_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        for item, collection in self._uploaded_items() + self._legacy_items():
            if item["id"] == item_id:
                return item, collection
        return None

    def search(self, query: dict[str, str]) -> tuple[int, dict[str, Any]]:
        """Filter by bbox (intersects), datetime (instant or start/end, open ends with ..), type, collection, text."""
        bbox = None
        if query.get("bbox"):
            try:
                bbox = [float(v) for v in query["bbox"].split(",")]
            except ValueError:
                bbox = None
            if not bbox or len(bbox) != 4 or bbox[0] > bbox[2] or bbox[1] > bbox[3]:
                return 400, {"status": "invalid_request", "error": "bbox 应为 west,south,east,north。"}
        start = end = None
        if query.get("datetime"):
            parts = query["datetime"].split("/")
            if len(parts) == 1:
                parts = [parts[0], parts[0]]
            start = dt.parse_time(parts[0]) if parts[0] not in ("", "..") else None
            end = dt.parse_time(parts[1]) if parts[1] not in ("", "..") else None
            if (parts[0] not in ("", "..") and start is None) or (parts[1] not in ("", "..") and end is None):
                return 400, {"status": "invalid_request", "error": "datetime 应为 RFC 3339 时间或 start/end 区间。"}
            if end is not None and len(parts[1]) == 10:
                end = end.replace(hour=23, minute=59, second=59)
        types = {t for t in (query.get("data_type") or "").split(",") if t}
        text = (query.get("q") or "").strip().lower()
        include_developer = query.get("developer") in ("1", "true")
        matches = []
        for entry in self.entries():
            item, view = entry["stac"], entry["view"]
            if not include_developer and view["audience"] == "developer":
                continue
            if types and view["data_type"] not in types:
                continue
            if query.get("collection") and view["collection_id"] != query["collection"]:
                continue
            if bbox:
                b = item["bbox"]
                if b[0] > bbox[2] or b[2] < bbox[0] or b[1] > bbox[3] or b[3] < bbox[1]:
                    continue
            if start or end:
                p = item["properties"]
                if p.get("workspace:date_unknown"):
                    continue
                first = dt.parse_time(p.get("datetime") or p.get("start_datetime"))
                last = dt.parse_time(p.get("datetime") or p.get("end_datetime"))
                if first is None or (end and first > end) or (start and last < start):
                    continue
            if text:
                blob = " ".join(str(view.get(k) or "") for k in ("title", "collection_title", "place", "event")).lower()
                if text not in blob:
                    continue
            matches.append(view)
        limit = max(1, min(200, int(query.get("limit") or 50))) if str(query.get("limit") or "50").isdigit() else 50
        return 200, {"status": "ok", "matched": len(matches), "items": matches[:limit]}

    # ------------------------------------------------------------------ previews

    def preview(self, item_id: str) -> Path | None:
        found = self.get(item_id)
        if found is None:
            return None
        item, _ = found
        data_type = item["properties"].get("workspace:data_type")
        assets = item["assets"]
        data = assets.get("data") or next(iter(assets.values()))
        source = Path(data["href"])
        if data_type == "vector":
            return self._render(self.vector_renderer, source, [])
        bands = data.get("bands") or []
        if data_type == "sar":
            pols = [b.get("name") for b in bands]
            index = pols.index("VV") + 1 if "VV" in pols else 1
            scale = "db" if (bands[0].get("unit") if bands else None) == "dB" else "linear"
            return self._render(self.raster_renderer, source, ["--mode", "sar", "--scale", scale, "--band", str(index)])
        if data_type == "optical":
            for asset in [a for k, a in assets.items() if k != "cloud-mask"]:
                names = [b.get("eo:common_name") for b in asset.get("bands") or []]
                if {"red", "green", "blue"} <= set(names):
                    order = ",".join(str(names.index(c) + 1) for c in ("red", "green", "blue"))
                    return self._render(self.raster_renderer, Path(asset["href"]), ["--mode", "rgb", "--bands", order])
            return self._render(self.raster_renderer, source, ["--mode", "continuous"])
        if data_type == "dem":
            return self._render(self.raster_renderer, source, ["--mode", "dem"])
        if data_type == "classification":
            classes = data.get("classification:classes") or []
            palette = {str(c["value"]): c.get("color_hint") for c in classes if c.get("color_hint") and not c.get("nodata")}
            return self._render(self.raster_renderer, source, ["--mode", "classes", "--palette", json.dumps(palette)])
        return None

    def render_layer(self, source: Path, layer: dict[str, Any]) -> Path | None:
        """A run's map layer: 0/1 labels, a continuous index, or class codes with a palette."""
        mode = layer.get("mode")
        if mode == "classes":
            return self._render(self.raster_renderer, source, ["--mode", "classes", "--palette",
                                                               json.dumps(layer.get("palette") or {})])
        if mode in ("labels", "continuous"):
            return self._render(self.raster_renderer, source, ["--mode", mode])
        if mode == "sar":
            return self._render(self.raster_renderer, source, ["--mode", "sar", "--scale", layer.get("scale", "linear")])
        return None

    def _render(self, renderer: Path, source: Path, args: list[str]) -> Path | None:
        if not source.is_file() or not self.web.inside_runtime(source):
            return None
        key = hashlib.sha1(f"{_signature(source)}|{renderer.name}|{' '.join(args)}".encode()).hexdigest()[:20]
        target = self.web.PREVIEW_DIR / f"{key}.png"
        if target.is_file():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(
                [self.web.GEO_PYTHON, "-I", str(renderer), str(source), str(target), *args],
                capture_output=True, text=True, timeout=120, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return target if completed.returncode == 0 and target.is_file() else None

    # ------------------------------------------------------------------ uploads

    def _upload(self, upload_id: str) -> tuple[Path, dict[str, Any]] | None:
        if not _UPLOAD_ID.fullmatch(upload_id):
            return None
        directory = self.upload_root / upload_id
        state = self.web.read_json(directory / "upload.json")
        return (directory, state) if isinstance(state, dict) else None

    def create_upload(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        data_type = str(payload.get("data_type") or "")
        if data_type not in dt.DATA_TYPES:
            return 400, {"status": "invalid_request", "error": "请选择数据类型。"}
        target = str(payload.get("collection_id") or "")
        if target:
            if not _COLLECTION_ID.fullmatch(target):
                return 400, {"status": "invalid_request", "error": "只能加入通过新上传建立的数据组。"}
            existing = self.web.read_json(self.collection_root / target / "collection.json")
            if not isinstance(existing, dict):
                return 404, {"status": "not_found", "error": "数据组不存在。"}
            if existing.get("workspace:data_type") != data_type:
                return 400, {"status": "invalid_request", "error": "一个数据组只放一种类型的数据。"}
            collection = {
                "id": target,
                "title": existing.get("title"),
                "place": existing.get("workspace:place"),
                "event": existing.get("workspace:event"),
                "license": existing.get("license"),
            }
        else:
            title = _clean(payload.get("collection_title"))
            if not title:
                return 400, {"status": "invalid_request", "error": "请填写数据组名称。"}
            collection = {
                "id": f"col_{uuid.uuid4().hex[:12]}",
                "title": title,
                "place": _clean(payload.get("place")) or None,
                "event": _clean(payload.get("event"), 200) or None,
                "new": True,
            }
        upload_id = f"dup_{uuid.uuid4().hex[:12]}"
        directory = self.upload_root / upload_id
        directory.mkdir(parents=True)
        state = {
            "schema_version": "workspace-data-upload.v1",
            "upload_id": upload_id,
            "status": "pending",
            "data_type": data_type,
            "collection": collection,
            "audience": "developer" if payload.get("test") else "user",
            "created_at": self.web.utc_now(),
            "files": [],
        }
        _write_json(directory / "upload.json", state)
        return 201, {"status": "created", "upload_id": upload_id, "collection_id": collection["id"]}

    def store_file(self, upload_id: str, stream: Any, length: int, filename: str) -> tuple[int, dict[str, Any]]:
        found = self._upload(upload_id)
        if found is None:
            return 404, {"status": "not_found", "error": "上传记录不存在。"}
        directory, state = found
        if state.get("status") != "pending":
            return 409, {"status": "conflict", "error": "该上传已经完成，不能再添加文件。"}
        if len(state["files"]) >= MAX_FILES_PER_UPLOAD:
            return 400, {"status": "invalid_request", "error": f"一次最多上传 {MAX_FILES_PER_UPLOAD} 个文件。"}
        safe_name = re.sub(r"[^\w.\-一-鿿]", "_", filename or "")[:120] or "file"
        fmt = dt.file_format(safe_name, state["data_type"])
        spec = dt.DATA_TYPES[state["data_type"]]
        if fmt is None:
            return 415, {
                "status": "unsupported_type",
                "error": f"{safe_name}：{spec['label']}只接受 {'、'.join(spec['formats'])}。",
            }
        if length <= 0:
            return 400, {"status": "invalid_request", "error": "文件为空。"}
        if length > self.web.MAX_UPLOAD_BYTES:
            return 413, {"status": "too_large", "error": f"单个文件不能超过 {self.web.MAX_UPLOAD_BYTES // (1024 * 1024)} MB。"}
        number = len(state["files"]) + 1
        suffix = "." + safe_name.rsplit(".", 1)[-1].lower()
        target = directory / f"{number}{suffix}"
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
        magic_ok = {
            "geotiff": head in self.web.TIFF_MAGIC,
            "zip": head.startswith(b"PK\x03\x04"),
            "gpkg": head == b"SQLi",
            "geojson": True,
        }[fmt]
        if not magic_ok:
            temporary.unlink(missing_ok=True)
            return 415, {"status": "unsupported_type", "error": f"{safe_name}：文件内容与扩展名不符。"}
        temporary.replace(target)
        profile = self.inspect([str(target)])[str(target)]
        entry = {
            "n": number,
            "original_name": safe_name,
            "format": fmt,
            "bytes": length,
            "sha256": digest.hexdigest(),
            "profile": profile,
        }
        state["files"].append(entry)
        _write_json(directory / "upload.json", state)
        suggestion = dt.suggest(state["data_type"], entry)
        return 200, {"status": "stored", "file": self._public_file(entry), "suggestion": suggestion}

    @staticmethod
    def _public_file(entry: dict[str, Any]) -> dict[str, Any]:
        profile = dict(entry.get("profile") or {})
        profile.pop("crs_wkt", None)
        return {key: entry[key] for key in ("n", "original_name", "format", "bytes")} | {"profile": profile}

    def finalize(self, upload_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Validate each requested item; register the accepted ones; report every item's outcome."""
        found = self._upload(upload_id)
        if found is None:
            return 404, {"status": "not_found", "error": "上传记录不存在。"}
        directory, state = found
        if state.get("status") != "pending":
            return 409, {"status": "conflict", "error": "该上传已经完成。"}
        requests = payload.get("items")
        if not isinstance(requests, list) or not requests:
            return 400, {"status": "invalid_request", "error": "没有要登记的数据。"}
        files = {f["n"]: f for f in state["files"]}
        used: set[int] = set(state.get("registered_files") or [])
        data_type = state["data_type"]
        results, accepted = [], []
        for index, request in enumerate(requests):
            numbers = [int(n) for n in request.get("files") or [] if str(n).isdigit()]
            if not numbers or any(n not in files or n in used for n in numbers):
                results.append({"index": index, "status": "rejected", "messages": [{"level": "error", "text": "文件编号无效、重复使用或已经登记。"}]})
                continue
            used.update(numbers)
            roles = request.get("roles") or {}
            bands = request.get("bands") or {}
            group = [
                {**files[n], "role": str(roles.get(str(n)) or "data"), "bands": bands.get(str(n)) or []}
                for n in numbers
            ]
            fields = request.get("fields") if isinstance(request.get("fields"), dict) else {}
            fields = self._normalize_fields(data_type, fields, request)
            report = dt.validate(data_type, group, fields)
            result = {"index": index, "files": [f["original_name"] for f in group], **report}
            if report["status"] != "rejected":
                problem = self._convert_vectors(directory, group)
                if problem:
                    result["status"] = "rejected"
                    result["messages"].append({"level": "error", "text": problem})
                else:
                    accepted.append((index, group, fields))
            results.append(result)
        registered = []
        if accepted:
            collection = dict(state["collection"])
            items = []
            for index, group, fields in accepted:
                item_id = f"{upload_id}.{group[0]['n']}"
                hrefs = {f["n"]: str(directory / f["stored_name"]) for f in group}
                item = dt.build_item(
                    data_type, item_id, collection, group, fields,
                    audience=state["audience"], created=self.web.utc_now(), hrefs=hrefs,
                )
                items.append(item)
                registered.append(item_id)
                next(r for r in results if r["index"] == index)["item_id"] = item_id
            self._save_items(collection, data_type, items)
            state["registered"] = (state.get("registered") or []) + registered
            state["registered_files"] = sorted(
                set(state.get("registered_files") or []) | {f["n"] for _, group, _ in accepted for f in group}
            )
            # A file that was rejected can be replaced by a corrected one in the same upload.
            if set(state["registered_files"]) >= set(files):
                state["status"] = "registered"
        state["results"] = results
        _write_json(directory / "upload.json", state)
        status = 200 if registered else 422
        return status, {
            "status": "registered" if registered else "rejected",
            "collection_id": state["collection"]["id"],
            "registered": registered,
            "results": results,
        }

    @staticmethod
    def _normalize_fields(data_type: str, fields: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in fields.items() if isinstance(v, (str, int, float, list)) or v is None}
        for key, value in list(out.items()):
            if isinstance(value, str):
                out[key] = value.strip()[:2000]
        if data_type == "classification":
            classes = request.get("classes") if isinstance(request.get("classes"), list) else fields.get("classes")
            cleaned = []
            for entry in classes or []:
                if not isinstance(entry, dict):
                    continue
                value = entry.get("value")
                if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
                    value = int(value)
                cleaned.append({**{k: entry.get(k) for k in ("name", "title", "color_hint", "description")}, "value": value,
                                "nodata": bool(entry.get("nodata"))})
            out["classes"] = cleaned
        return out

    def _convert_vectors(self, directory: Path, group: list[dict[str, Any]]) -> str | None:
        """Zipped Shapefiles become GeoPackages; other files are stored as uploaded."""
        for entry in group:
            stored = directory / f"{entry['n']}.{entry['original_name'].rsplit('.', 1)[-1].lower()}"
            entry["stored_name"] = stored.name
            if entry["format"] != "zip":
                continue
            target = directory / f"{entry['n']}.gpkg"
            try:
                completed = subprocess.run(
                    [self.web.GEO_PYTHON, "-I", str(self.inspector), "shp2gpkg", str(stored), str(target)],
                    capture_output=True, text=True, timeout=180, check=False,
                )
                result = json.loads(completed.stdout or "{}")
            except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                return f"{entry['original_name']}：转换为 GeoPackage 失败（{exc}）。"
            if not result.get("ok"):
                return f"{entry['original_name']}：转换为 GeoPackage 失败（{result.get('error')}）。"
            entry["converted_to"] = "gpkg"
            entry["source_archive_href"] = str(stored)
            entry["stored_name"] = target.name
            entry["stored_bytes"] = target.stat().st_size
            entry["stored_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
            entry["profile"] = self.inspect([str(target)])[str(target)]
        return None

    def _save_items(self, collection: dict[str, Any], data_type: str, items: list[dict[str, Any]]) -> None:
        with self.lock:
            directory = self.collection_root / collection["id"]
            for item in items:
                _write_json(directory / "items" / f"{item['id']}.json", item)
            existing = [
                self.web.read_json(path) for path in sorted((directory / "items").glob("*.json"))
            ]
            meta = {**collection, "data_type": data_type}
            _write_json(directory / "collection.json", dt.build_collection(meta, [i for i in existing if isinstance(i, dict)]))

    def upload_collections(self) -> list[dict[str, Any]]:
        out = []
        if self.collection_root.is_dir():
            for directory in sorted(self.collection_root.iterdir()):
                collection = self.web.read_json(directory / "collection.json")
                if not isinstance(collection, dict):
                    continue
                items = [self.web.read_json(path) or {} for path in (directory / "items").glob("*.json")]
                audiences = {(i.get("properties") or {}).get("workspace:audience") for i in items}
                out.append({"id": collection["id"], "title": collection.get("title"),
                            "data_type": collection.get("workspace:data_type"),
                            "audience": "user" if "user" in audiences else "developer"})
        return out

    # ------------------------------------------------------------------ scenarios

    def scenario_scenes(self) -> list[dict[str, Any]]:
        out = []
        for item, collection in self._uploaded_items():
            scene = item_scene(item, {"id": collection["id"], "title": collection.get("title") or collection["id"]})
            if scene:
                out.append(scene)
        return out


# ---------------------------------------------------------------------- conversions


def legacy_item(scene: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """An earlier scene (fixture, paired or single-band upload, self-check) as a STAC Item. Nothing is written."""
    sar = scene["input_kind"] == "sar_backscatter"
    data_type = "sar" if sar else "classification"
    source = scene.get("source") or {}
    category = scene.get("category")
    developer = category in ("synthetic", "test")
    if category == "synthetic":
        origin = "synthetic"
    elif sar:
        origin = "observed"
    elif scene.get("self_check_for"):
        origin = "synthetic"
    else:
        origin = "model_estimate" if category == "uploaded" else "observed"
    band = (profile.get("bands") or [{}])[0]
    acquired = source.get("datetime") or (f"{scene['acquired']}T00:00:00Z" if scene.get("acquired") else None)
    props: dict[str, Any] = {
        "datetime": acquired or "1970-01-01T00:00:00Z",
        "title": scene.get("title"),
        "workspace:data_type": data_type,
        "workspace:origin": origin,
        "workspace:verification": "source_linked" if source.get("stac_item_url") else "declared",
        "workspace:audience": "developer" if developer or scene.get("self_check_for") else "user",
        "workspace:legacy_id": scene["id"],
    }
    if not acquired:
        props["workspace:date_unknown"] = True
    for key in ("place", "event", "claim_boundary"):
        if scene.get(key):
            props[f"workspace:{key}"] = scene[key]
    if scene.get("self_check_for"):
        props["workspace:self_check_for"] = scene["self_check_for"]
    if source.get("platform"):
        props["platform"] = str(source["platform"]).lower()
    extensions = {"projection"}
    asset: dict[str, Any] = {
        "href": scene["path"],
        "type": dt.MEDIA_TYPES["geotiff"],
        "roles": ["data"],
        "title": scene.get("original_name") or Path(scene["path"]).name,
        **dt.projection_fields(profile, True),
    }
    if sar:
        extensions.update({"sar", "sat"})
        pol = str(source.get("polarization") or "").upper()
        if pol in dt.POLARIZATIONS:
            props["sar:polarizations"] = [pol]
        if source.get("orbit_state"):
            props["sat:orbit_state"] = source["orbit_state"]
        if source.get("relative_orbit") is not None:
            props["sat:relative_orbit"] = int(source["relative_orbit"])
        if str(scene.get("sensor") or "").lower().startswith("sentinel-1"):
            props["sar:frequency_band"] = "C"
        props["workspace:backscatter"] = LEGACY_BACKSCATTER.get(str(scene.get("source_type")), "unknown")
        unit = "dB" if scene.get("input_scale") == "db" else "1"
        asset["bands"] = [dt.band_object(band, pol if pol in dt.POLARIZATIONS else "backscatter", unit)]
    else:
        extensions.add("classification")
        asset["bands"] = [dt.band_object(band, "class")]
        asset["classification:classes"] = dt.class_objects(dt.CLASS_PRESETS["water_binary"]["classes"])
    links = [{"rel": "collection", "href": "../collection.json", "type": "application/json"}]
    if source.get("stac_item_url"):
        links.append({"rel": "derived_from", "href": source["stac_item_url"], "type": "application/geo+json"})
    if scene.get("self_check_for"):
        props["processing:lineage"] = "门控自检样本：从原数据裁去若干列，故意不在同一网格。"
        extensions.add("processing")
    bbox = profile.get("bbox_wgs84") or [0, 0, 0, 0]
    return {
        "type": "Feature",
        "stac_version": dt.STAC_VERSION,
        "stac_extensions": sorted(dt.EXTENSIONS[e] for e in extensions),
        "id": scene["id"],
        "collection": scene["collection_id"],
        "bbox": bbox,
        "geometry": dt.bbox_polygon(bbox),
        "properties": props,
        "links": links,
        "assets": {"data": asset},
    }


CAPABILITY_LABELS = {
    "sar_vv": "SAR 后向散射（含 VV）",
    "water_mask": "0/1 水体栅格",
    "optical_green_nir": "光学反射率（含 green、nir）",
    "landcover": "土地覆盖等分类栅格",
    "reference_extent": "参考水体范围（矢量）",
}


def capabilities(item: dict[str, Any]) -> list[str]:
    """What a scenario input slot can use this item as; derived from the STAC fields, never stored."""
    p = item["properties"]
    data_type = p.get("workspace:data_type")
    assets = item.get("assets") or {}
    data = assets.get("data") or {}
    out = []
    if data_type == "sar" and "VV" in [b.get("name") for b in data.get("bands") or []]:
        out.append("sar_vv")
    if data_type == "classification":
        out.append("water_mask" if _is_water_binary(data.get("classification:classes") or []) else "landcover")
    if data_type == "optical":
        names = {b.get("eo:common_name") for k, a in assets.items() if k != "cloud-mask" for b in a.get("bands") or []}
        if {"green", "nir"} <= names:
            out.append("optical_green_nir")
    if data_type == "vector" and p.get("workspace:purpose") == "reference":
        out.append("reference_extent")
    return out


def _is_water_binary(classes: list[dict[str, Any]]) -> bool:
    named = {(c["value"], c["name"]) for c in classes if not c.get("nodata")}
    return named == {(0, "non_water"), (1, "water")}


def item_scene(item: dict[str, Any], collection: dict[str, Any]) -> dict[str, Any] | None:
    """The scenario-facing shape of a typed upload, or None when no current scenario can use it."""
    p = item["properties"]
    data_type = p.get("workspace:data_type")
    if p.get("workspace:legacy_id"):
        return None
    data = item["assets"].get("data")
    if not data:
        return None
    bands = data.get("bands") or []
    band_index = 1
    if data_type == "sar":
        names = [b.get("name") for b in bands]
        if "VV" not in names:
            return None
        band_index = names.index("VV") + 1
        input_kind = "sar_backscatter"
        input_scale = "db" if bands[band_index - 1].get("unit") == "dB" else "linear"
        sensor = f"{p.get('platform') or 'SAR'}（VV）"
    elif data_type == "classification" and _is_water_binary(data.get("classification:classes") or []):
        input_kind, input_scale, sensor = "water_labels", "linear", "0/1 水体标签"
    else:
        return None
    shape = data.get("proj:shape")
    transform = data.get("proj:transform") or []
    day = dt.item_date(item)
    return {
        "id": item["id"],
        "title": p.get("title") or f"{day} · {sensor}",
        "collection_id": collection["id"],
        "collection_title": collection["title"],
        "place": p.get("workspace:place"),
        "acquired": day,
        "sequence": 0,
        "sensor": sensor,
        "input_kind": input_kind,
        "input_scale": input_scale,
        "category": "test" if p.get("workspace:audience") == "developer" else "uploaded",
        "source_type": "uploaded",
        "event": p.get("workspace:event"),
        "grid": {
            "crs": data.get("proj:code"),
            "shape": shape,
            "resolution": [abs(transform[0]), abs(transform[4])] if len(transform) >= 5 else None,
            "projected": bool(data.get("proj:code")) and data.get("proj:code") != "EPSG:4326",
        },
        "source": {k: v for k, v in {
            "polarization": "VV" if data_type == "sar" else None,
            "orbit_state": p.get("sat:orbit_state"),
            "relative_orbit": p.get("sat:relative_orbit"),
            "platform": p.get("platform"),
        }.items() if v is not None},
        "original_name": data.get("title"),
        "claim_boundary": "上传数据由上传者提供，来源、成像时间和标签未经核实。",
        "path": data["href"],
        "band_index": band_index,
        "band_count": len(bands),
        "stac_item": True,
    }

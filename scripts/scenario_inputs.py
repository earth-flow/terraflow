"""Scenario inputs: choosing catalog items for a scenario's input slots and preparing a run.

A scenario declares input slots (``workspace_scenarios.TASK_SCENARIOS[...]["slots"]``):
role, label and the capabilities it accepts (``data_catalog.capabilities``).
The planner model does not see the item list. It sees ``places()`` - for
each place the data types and dates available - and returns a scenario, a
place and any dates the message names. ``resolve`` then fills the slots
deterministically from the catalog, ``check`` validates a filled or edited
set of inputs, and ``prepare`` writes the run inputs: SAR and 0/1 labels as
before (symbolic links, VV band extracted from multi-band files), and for the
other inputs a copy aligned onto the run grid by ``align_inputs.py`` with
the method recorded in fixture.json.

Standard library only; raster work runs in the geo Python (``web.GEO_PYTHON``).
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import data_catalog
import data_types as dt
import workspace_scenarios as scenarios

INPUT_KIND = {"sar_vv": "sar_backscatter", "water_mask": "water_labels", "optical_green_nir": "optical_reflectance"}
TYPE_WORDS = {
    "sar_vv": "SAR",
    "water_mask": "0/1 水体栅格",
    "optical_green_nir": "光学",
    "landcover": "土地覆盖",
    "reference_extent": "参考水体范围",
}
_MONTH_DAY = re.compile(r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
_ISO_DATE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")


# ---------------------------------------------------------------------- catalog views


def usable_entries(catalog: Any, include_developer: bool = False) -> list[dict[str, Any]]:
    """Catalog entries that can fill some slot (self-check samples never do)."""
    out = []
    for entry in catalog.entries():
        view = entry["view"]
        if view.get("self_check_for") or not view.get("capabilities"):
            continue
        if view["audience"] == "developer" and not include_developer:
            continue
        out.append(entry)
    return out


def place_of(view: dict[str, Any]) -> str:
    return str(view.get("place") or view["collection_title"])


def places(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the planner sees: per place, the event and the dates available for each kind of data."""
    out: dict[str, dict[str, Any]] = {}
    for entry in entries:
        view = entry["view"]
        place = out.setdefault(place_of(view), {"place": place_of(view), "events": set(), "data": {}})
        if view.get("event"):
            place["events"].add(view["event"])
        for cap in view["capabilities"]:
            place["data"].setdefault(TYPE_WORDS[cap], set()).add(view.get("date") or "日期未记录")
    return [
        {
            "place": p["place"],
            "events": sorted(p["events"]),
            "data": {kind: sorted(dates) for kind, dates in sorted(p["data"].items())},
        }
        for p in out.values()
    ]


def data_summary(catalog: Any, include_developer: bool = False) -> list[dict[str, Any]]:
    """All data per place and type (including types no scenario uses yet), for answering questions."""
    out: dict[str, dict[str, Any]] = {}
    for entry in catalog.entries():
        view = entry["view"]
        if view.get("self_check_for") or (view["audience"] == "developer" and not include_developer):
            continue
        place = out.setdefault(place_of(view), {"place": place_of(view), "groups": {}})
        group = place["groups"].setdefault(view["collection_title"], {"type": view["type_label"], "dates": set(),
                                                                        "usable_in": set()})
        group["dates"].add(view.get("date") or "日期未记录")
        group["usable_in"].update(view.get("usable_in") or [])
    return [
        {"place": p["place"], "groups": [
            {"title": title, "type": g["type"], "dates": sorted(g["dates"]), "usable_in": sorted(g["usable_in"])}
            for title, g in p["groups"].items()
        ]}
        for p in out.values()
    ]


def mentioned_dates(content: str) -> list[tuple[int | None, int, int]]:
    found = [(int(y) if y else None, int(m), int(d)) for y, m, d in _MONTH_DAY.findall(content)]
    found += [(int(y), int(m), int(d)) for y, m, d in _ISO_DATE.findall(content)]
    return list(dict.fromkeys(found))


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _distance(view: dict[str, Any], wanted: tuple[int | None, int, int]) -> int:
    day = _day(view.get("date"))
    if day is None:
        return 10**6
    year, month, dom = wanted
    try:
        target = date(year or day.year, month, dom)
    except ValueError:
        return 10**6
    return abs((day - target).days)


# ---------------------------------------------------------------------- resolve


def resolve(
    spec: dict[str, Any],
    entries: list[dict[str, Any]],
    *,
    place: str | None,
    content: str,
    attachments: list[str],
    previous: list[str],
) -> tuple[dict[str, str], list[str], str | None]:
    """Fill every slot it can: attachments first, then the place's data by type and date."""
    views = {e["view"]["id"]: e["view"] for e in entries}
    attached = [views[i] for i in attachments if i in views]
    earlier = [views[i] for i in previous if i in views]
    if place is None and (attached or earlier):
        place = place_of((attached or earlier)[0])
    pool = [v for v in views.values() if place is None or place_of(v) == place]
    dates = mentioned_dates(content)
    roles: dict[str, str] = {}
    notes: list[str] = []
    source = "attachment" if attached else ("message" if place else None)
    used: set[str] = set()

    for slot in spec["slots"]:  # attachments go to the first slot that can take them
        match = next((v for v in attached if v["id"] not in used and set(v["capabilities"]) & set(slot["accepts"])), None)
        if match:
            roles[slot["role"]] = match["id"]
            used.add(match["id"])

    pair_roles = [s["role"] for s in spec["slots"] if s.get("pair") == "dates"]
    attached_pair = [roles[r] for r in pair_roles if r in roles]
    if len(attached_pair) == 2:  # two attached dates: the earlier one is "before"
        ordered = sorted(attached_pair, key=lambda i: views[i].get("date") or "")
        roles.update(zip(pair_roles, ordered))
    elif len(attached_pair) == 1:
        # One attached date: it is "after" when its group only has earlier dates, otherwise "before".
        anchor = views[attached_pair[0]]
        group = [v for v in pool if v["collection_id"] == anchor["collection_id"] and v["id"] != anchor["id"]
                 and set(v["capabilities"]) & set(anchor["capabilities"])]
        later = any((v.get("date") or "") > (anchor.get("date") or "") for v in group)
        for role in pair_roles:
            roles.pop(role, None)
        roles[pair_roles[0] if later or not group else pair_roles[1]] = anchor["id"]
    paired = [s for s in spec["slots"] if s.get("pair") == "dates" and s["role"] not in roles]
    if paired:
        _fill_pair(paired, spec, pool, roles, used, dates, notes, views)

    reference_slot = next((s for s in spec["slots"] if "reference_extent" in s["accepts"]), None)
    singles = [s for s in spec["slots"] if s["role"] not in roles and not s.get("pair")]
    # A reference is chosen before the scene so the scene can be the date closest to it.
    singles.sort(key=lambda s: 0 if s is reference_slot else 1)
    for slot in singles:
        candidates = [v for v in pool if v["id"] not in used and set(v["capabilities"]) & set(slot["accepts"])]
        if not candidates:
            continue
        anchor = views.get(roles.get(reference_slot["role"])) if reference_slot and slot is not reference_slot else None
        if dates and slot is not reference_slot:
            chosen = min(candidates, key=lambda v: (_distance(v, dates[0]), v["legacy"]))
        elif anchor and anchor.get("date"):
            target = _day(anchor["date"])
            chosen = min(candidates, key=lambda v: (abs(((_day(v.get("date")) or target) - target).days), v["legacy"]))
        else:
            chosen = max(candidates, key=lambda v: (not v["legacy"], v.get("date") or ""))
        roles[slot["role"]] = chosen["id"]
        used.add(chosen["id"])
    if earlier and not attached and not place:
        source = "previous_run"
    return roles, notes, source if roles else None


def _fill_pair(paired, spec, pool, roles, used, dates, notes, views) -> None:
    accepts = paired[0]["accepts"]
    fixed = [roles[s["role"]] for s in spec["slots"] if s.get("pair") == "dates" and s["role"] in roles]
    if len(paired) == 1 and fixed:
        # One date is given (attached); take the other from the same group, on the right side of it.
        anchor = views[fixed[0]]
        cap = next(c for c in anchor["capabilities"] if c in accepts)
        pair_roles = [s["role"] for s in spec["slots"] if s.get("pair") == "dates"]
        later = paired[0]["role"] == pair_roles[1]
        candidates = [
            v for v in pool if v["id"] not in used and cap in v["capabilities"]
            and v["collection_id"] == anchor["collection_id"]
            and ((v.get("date") or "") > (anchor.get("date") or "") if later else (v.get("date") or "") < (anchor.get("date") or ""))
        ]
        if candidates:
            pick = min(candidates, key=lambda v: v.get("date") or "") if later else max(candidates, key=lambda v: v.get("date") or "")
            roles[paired[0]["role"]] = pick["id"]
            used.add(pick["id"])
        return
    for cap in accepts:
        candidates = [v for v in pool if v["id"] not in used and cap in v["capabilities"]]
        if len(candidates) + len(fixed) < 2:
            continue
        # Prefer one group (newer uploads before converted earlier data), so both dates share a source.
        groups: dict[str, list[dict[str, Any]]] = {}
        for view in candidates:
            groups.setdefault(view["collection_id"], []).append(view)
        group = max(groups.values(), key=lambda g: (len({v.get("date") for v in g}) >= 2, not g[0]["legacy"], len(g)))
        ordered = sorted(group, key=lambda v: v.get("date") or "")
        if len(dates) >= len(paired):
            picks = [min(group, key=lambda v: _distance(v, wanted)) for wanted in dates[: len(paired)]]
            picks = sorted(dict.fromkeys(v["id"] for v in picks))
            picks = sorted((v for v in group if v["id"] in picks), key=lambda v: v.get("date") or "")
        else:
            picks = [ordered[0], ordered[-1]] if len(ordered) >= 2 else ordered
            if len(ordered) > 2:
                notes.append(f"这组有 {len(ordered)} 个日期，默认取最早和最晚两期；可以在计划里更换。")
        for slot, view in zip(paired, picks):
            roles[slot["role"]] = view["id"]
            used.add(view["id"])
        return


# ---------------------------------------------------------------------- check


def check(spec: dict[str, Any], roles: dict[str, str], catalog: Any) -> tuple[dict[str, Any], str | None]:
    """Items and capability per role, or the reason this set of inputs cannot run."""
    chosen: dict[str, Any] = {}
    for slot in spec["slots"]:
        found = catalog.get(str(roles.get(slot["role"]) or ""))
        if found is None:
            return {}, f"「{spec['title']}」缺少{slot['label']}。"
        item, collection = found
        caps = [c for c in data_catalog.capabilities(item) if c in slot["accepts"]]
        if not caps:
            need = "或".join(data_catalog.CAPABILITY_LABELS[c] for c in slot["accepts"])
            return {}, f"{slot['label']}需要{need}。"
        chosen[slot["role"]] = {"item": item, "collection": collection, "capability": caps[0]}
    ids = [c["item"]["id"] for c in chosen.values()]
    if len(set(ids)) < len(ids):
        return chosen, "同一份数据不能同时作为两个输入。"
    pair = [s["role"] for s in spec["slots"] if s.get("pair") == "dates"]
    if pair:
        first, second = chosen[pair[0]], chosen[pair[1]]
        if first["capability"] != second["capability"]:
            return chosen, f"{pair[0]} 与 {pair[1]} 数据类型不同，不能直接比较。"
        units = {_vv_unit(c["item"]) for c in (first, second)} if first["capability"] == "sar_vv" else {None}
        if len(units) > 1:
            return chosen, "两期 SAR 数值尺度不同（线性和 dB），不能直接比较。"
        d1, d2 = dt.item_date(first["item"]), dt.item_date(second["item"])
        if d1 and d2 and d1 >= d2:
            return chosen, f"前期影像（{d1}）应早于后期影像（{d2}）。"
    return chosen, None


def _vv_unit(item: dict[str, Any]) -> str | None:
    bands = (item["assets"].get("data") or {}).get("bands") or []
    return next((b.get("unit") for b in bands if b.get("name") == "VV"), None)


def input_kind(chosen: dict[str, Any]) -> tuple[str, str]:
    """Runner input kind and scale, from the slot that carries the imagery."""
    for value in chosen.values():
        cap = value["capability"]
        if cap in INPUT_KIND:
            scale = "db" if cap == "sar_vv" and _vv_unit(value["item"]) == "dB" else "linear"
            return INPUT_KIND[cap], scale
    raise ValueError("no imagery input")


def plan_inputs(spec: dict[str, Any], chosen: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for slot in spec["slots"]:
        value = chosen[slot["role"]]
        item = value["item"]
        p = item["properties"]
        out.append({
            "role": slot["role"],
            "label": slot["label"],
            "scene_id": item["id"],
            "scene_title": p.get("title") or f"{dt.item_date(item)} · {data_catalog.TYPE_LABELS.get(p.get('workspace:data_type'))}",
            "collection_title": value["collection"].get("title") or value["collection"]["id"],
            "acquired": dt.item_date(item),
            "preview": f"/api/workspace/items/{item['id']}/preview",
            "capability": value["capability"],
            "accepts": slot["accepts"],
        })
    return out


# ---------------------------------------------------------------------- prepare


def _align(web: Any, *args: str) -> dict[str, Any]:
    script = web.ROOT / "scripts" / "align_inputs.py"
    completed = subprocess.run(
        [web.GEO_PYTHON, "-I", str(script), *args], capture_output=True, text=True, timeout=300, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"对齐输入失败：{completed.stderr.strip()[-300:]}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _optical_band(item: dict[str, Any], common: str) -> tuple[str, int, float | None, float | None, float]:
    """(path, band number, scale, offset, resolution) of the finest asset holding this common band name."""
    found = []
    for key, asset in item["assets"].items():
        if key == "cloud-mask":
            continue
        for index, band in enumerate(asset.get("bands") or [], start=1):
            if band.get("eo:common_name") == common:
                resolution = abs((asset.get("proj:transform") or [asset.get("raster:spatial_resolution") or 0])[0])
                found.append((asset["href"], index, band.get("raster:scale"), band.get("raster:offset"), resolution))
    if not found:
        raise RuntimeError(f"光学数据没有 {common} 波段。")
    return min(found, key=lambda f: f[4])


def prepare(web: Any, scenario: str, chosen: dict[str, Any], key: str, self_check: bool) -> tuple[Path, bool]:
    """Write run-inputs/<key>/ for the runner; returns (directory, self_check_ready)."""
    library = web.LIBRARY
    scene_roles = [r for r, v in chosen.items() if v["capability"] in ("sar_vv", "water_mask")]
    if scenario in ("water_change", "water_extent", "landcover_impact", "reference_compare"):
        scenes = {role: library.get(chosen[role]["item"]["id"]) for role in scene_roles}
        if any(scene is None for scene in scenes.values()):
            raise RuntimeError("影像数据不可用于分析。")
        task = "water_extent" if scenario in ("water_extent", "reference_compare") else "water_change"
        directory, ready = library.prepare_inputs(task, scenes, key, self_check and scenario == "water_change")
        meta = json.loads((directory / "fixture.json").read_text(encoding="utf-8"))
    else:
        directory = library.run_input_root / key
        directory.mkdir(parents=True, exist_ok=True)
        for old in directory.glob("*.tif"):
            old.unlink()
        ready, meta = False, {"schema_version": "terraflow-workspace-inputs.v1"}
    meta["scenario"] = scenario
    meta["scene_ids"] = {role: value["item"]["id"] for role, value in chosen.items()}
    meta["alignment"] = []
    template = directory / ("after.tif" if (directory / "after.tif").exists() else "scene.tif")

    if scenario == "optical_water":
        item = chosen["scene"]["item"]
        p = item["properties"]
        for common, name in (("green", "green.tif"), ("nir", "nir.tif")):
            path, band, scale, offset, _ = _optical_band(item, common)
            args = ["band", path, str(directory / name), "--band", str(band)]
            if scale is not None:
                args += ["--scale", str(scale), "--offset", str(offset or 0.0)]
            meta["alignment"].append({**_align(web, *args), "label": f"{common} 波段"})
        mask = item["assets"].get("cloud-mask")
        if mask:
            meta["alignment"].append({**_align(web, "warp", mask["href"], str(directory / "green.tif"),
                                               str(directory / "cloud.tif"), "--resampling", "nearest"),
                                      "label": "SCL 云掩膜"})
        meta.update(
            type="optical_reflectance_from_catalog",
            category="uploaded",
            input_kind="optical_reflectance",
            input_scale="linear",
            title=chosen["scene"]["collection"].get("title"),
            label=p.get("title") or dt.item_date(item),
            scene_date=dt.item_date(item),
            processing_level=p.get("processing:level"),
            claim_boundary="光学水体为 NDWI 阈值估计，不是参考标注。",
        )
    if scenario == "landcover_impact":
        item = chosen["landcover"]["item"]
        data = item["assets"]["data"]
        record = _align(web, "warp", data["href"], str(template), str(directory / "landcover.tif"),
                        "--resampling", "nearest")
        meta["alignment"].append({**record, "label": "土地覆盖"})
        meta["classes"] = data.get("classification:classes") or []
        meta["landcover_title"] = chosen["landcover"]["collection"].get("title")
        meta["landcover_date"] = dt.item_date(item)
    if scenario == "reference_compare":
        item = chosen["reference"]["item"]
        record = _align(web, "rasterize", item["assets"]["data"]["href"], str(template),
                        str(directory / "reference.tif"))
        meta["alignment"].append({**record, "label": "参考范围"})
        scene_date = dt.item_date(chosen["scene"]["item"])
        reference_date = dt.item_date(item)
        meta.update(
            reference_title=chosen["reference"]["collection"].get("title"),
            reference_date=reference_date,
            scene_date=scene_date,
        )
        if _day(scene_date) and _day(reference_date):
            meta["date_gap_days"] = abs((_day(scene_date) - _day(reference_date)).days)
    if scenario == "water_extent" or scenario == "water_change":
        meta.pop("alignment")
    (directory / "fixture.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return directory, ready

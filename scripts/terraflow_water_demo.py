#!/usr/bin/env python3
"""Run a replayable Terraflow/Terrabox water analysis (paired change or single scene).

The script deliberately keeps the LLM boundary small and inspectable.  The
model chooses an ordered subset of tools from the live Terrabox catalog.  The
runner validates that choice against the catalog, then applies the raster
preflight gate before any pairwise operation.  It records the Terrabox
execution id, manifest path, and analytics trace for every call.

Two tasks share the runner: ``water_change`` compares before.tif and
after.tif on one grid; ``water_extent`` measures water in a single scene.tif.
Inputs are described by an optional fixture.json: 0/1 water labels (the
synthetic engineering fixture when the file is absent) or SAR backscatter,
which is first turned into threshold water masks.  Results do not claim
flood causation or a segmentation benchmark result.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_TERRABOX_URL = "http://127.0.0.1:8010"
DEFAULT_TERRAFLOW_URL = "http://127.0.0.1:7861"
DEFAULT_LLM_URL = "http://127.0.0.1:8089"
DEFAULT_RUNTIME_DIR = "/data2/liyezhuoxin/terrabox-water-demo/runtime"
DEFAULT_FIXTURE_DIR = f"{DEFAULT_RUNTIME_DIR}/demo-fixtures"
DEFAULT_SUMMARY_DIR = f"{DEFAULT_RUNTIME_DIR}/demo-runs"
DEFAULT_REQUESTED_MODEL = "Qwen3.8-27B"
# Display names for llama.cpp model ids, matched in order on the lower-cased id.
MODEL_DISPLAY_NAMES = (
    ("3.8-27b", "Qwen3.8-27B"),
    ("3.5-9b", "Qwen3.5-9B"),
    ("3.5-4b", "Qwen3.5-4B"),
)

CORE_TOOL_SLUGS = (
    "raster_viewer.inspect_geotiff",
    "geo_water.raster_profile_preflight",
    "geo_raster.raster_stats",
    "geo_raster.threshold_segmentation",
    "geo_raster.raster_diff",
    "geo_water.water_evidence",
    "geo_basic.pixel_area",
)
# Scenario presets. The planner decides whether a step runs; the input kind
# fixes how it runs, so scientifically sensitive parameters (threshold
# direction, units, label values) are never chosen by the model. A new input
# kind (for example optical NDWI) is a new entry here, not a new tool.
SCENARIOS: dict[str, dict[str, Any]] = {
    "water_labels": {"derive_labels": None},
    "sar_backscatter": {
        "derive_labels": {
            "tool": "geo_raster.threshold_segmentation",
            "preset": "sar_low_backscatter_water",
            "params": {"direction": "below", "foreground_value": 1, "background_value": 0, "nodata_value": 255},
            "transform_by_scale": {"linear": "db", "db": "none"},
            "otsu_range": [-35.0, 5.0],
            "units": "dB",
            "rule": "Otsu on the after scene, same threshold reused on the before scene",
        },
        "planner_note": (
            "Inputs are SAR backscatter, not water labels (low backscatter = water). "
            "Run geo_raster.threshold_segmentation before geo_water.water_evidence; "
            "the runner fills threshold direction and units from the scenario preset."
        ),
    },
}
# Tasks: which tools a plan may use and the minimum plan size. Ordering rules
# live in validate_order and apply to model plans and confirmed plans alike.
TASKS: dict[str, dict[str, Any]] = {
    "water_change": {
        "tools": CORE_TOOL_SLUGS,
        "min_tools": 5,
        "constraints": [
            "Inspect a raster before pairwise work.",
            "Run geo_water.raster_profile_preflight before geo_raster.raster_diff and geo_water.water_evidence.",
            "Run geo_water.water_evidence before geo_basic.pixel_area.",
        ],
        "default_prompt": "Select an executable ordered plan for a paired water GeoTIFF analysis.",
    },
    "water_extent": {
        "tools": (
            "raster_viewer.inspect_geotiff",
            "geo_raster.raster_stats",
            "geo_raster.threshold_segmentation",
            "geo_water.water_extent",
        ),
        "min_tools": 2,
        "constraints": [
            "There is one scene; do not use pairwise tools.",
            "Inspect the raster first.",
            "End with geo_water.water_extent, which checks labels and the area grid before measuring water.",
        ],
        "default_prompt": "Select an executable ordered plan to measure water in one GeoTIFF scene.",
    },
}
PAIRWISE_TOOL_SLUGS = {
    "geo_water.raster_profile_preflight",
    "geo_raster.raster_diff",
    "geo_water.water_evidence",
}
REQUIRES_PREFLIGHT = {"geo_raster.raster_diff", "geo_water.water_evidence"}
SYNTHETIC_FIXTURE = {
    "type": "synthetic_engineering_fixture",
    "input_kind": "water_labels",
    "label": "标准合成前后栅格",
    "claim_boundary": "water labels do not establish flood causation or satellite performance",
}


class DemoError(RuntimeError):
    """A user-facing demo failure with enough context for the trace."""


ProgressCallback = Callable[[str, str, int | None, int | None, str | None], None]


def write_progress(
    path: Path | None,
    stage: str,
    message: str,
    current_step: int | None = None,
    total_steps: int | None = None,
    tool: str | None = None,
) -> None:
    """Write a small atomic progress event for the web adapter.

    These are phase and tool-boundary events, not a replacement for a streaming
    Terrabox execution feed. The CLI remains usable when no progress file is
    requested.
    """
    emit_event("stage", stage=stage, message=message, current_step=current_step, total_steps=total_steps, tool=tool)
    if path is None:
        return
    payload = {
        "stage": stage,
        "message": message,
        "current_step": current_step,
        "total_steps": total_steps,
        "tool": tool,
        "updated_at": utc_now(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        # Progress is advisory; never make the scientific run fail because the
        # browser-side status file is unavailable.
        return


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compact(value: Any, *, depth: int = 0) -> Any:
    """Keep saved summaries useful without duplicating large raster outputs."""
    if depth >= 3:
        if isinstance(value, (dict, list)):
            return f"<{type(value).__name__} truncated>"
        return value
    if isinstance(value, dict):
        items = list(value.items())
        result = {str(key): compact(item, depth=depth + 1) for key, item in items[:40]}
        if len(items) > 40:
            result["_truncated_keys"] = len(items) - 40
        return result
    if isinstance(value, list):
        result = [compact(item, depth=depth + 1) for item in value[:40]]
        if len(value) > 40:
            result.append(f"<truncated {len(value) - 40} items>")
        return result
    if isinstance(value, str) and len(value) > 800:
        return value[:800] + "..."
    return value


def require_file(path: Path, label: str) -> str:
    if not path.exists():
        raise DemoError(f"{label} does not exist: {path}")
    return str(path)


def load_fixture_meta(fixtures_dir: Path) -> dict[str, Any]:
    """Read fixture.json; directories without one are the synthetic label fixture."""
    path = fixtures_dir / "fixture.json"
    if not path.exists():
        return dict(SYNTHETIC_FIXTURE)
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DemoError(f"fixture.json is unreadable: {exc}") from exc
    if not isinstance(meta, dict):
        raise DemoError("fixture.json must contain an object")
    if meta.get("type") == "sentinel1_rtc_gamma0":
        meta.setdefault("input_kind", "sar_backscatter")
    if meta.get("input_kind") not in SCENARIOS:
        raise DemoError(f"Unsupported fixture input_kind: {meta.get('input_kind')}")
    if meta["input_kind"] == "sar_backscatter" and meta.get("input_scale", "linear") not in ("linear", "db"):
        raise DemoError(f"Unsupported SAR input_scale: {meta.get('input_scale')}")
    return meta


def load_api_key(path: Path | None, explicit: str | None) -> str:
    value = explicit or os.environ.get("TERRABOX_API_KEY") or os.environ.get("TERRALINK_API_KEY")
    if value:
        return value.strip()
    if path and path.exists():
        return path.read_text(encoding="utf-8").strip()
    raise DemoError("Terrabox API key is missing; use --api-key, TERRABOX_API_KEY, or --api-key-file")


def request_json(
    url: str,
    *,
    api_key: str | None = None,
    method: str = "GET",
    payload: Any | None = None,
    timeout: float = 60,
) -> Any:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise DemoError(f"HTTP {exc.code} from {url}: {body[:500]}") from exc
    except URLError as exc:
        raise DemoError(f"Cannot reach {url}: {exc.reason}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise DemoError(f"Non-JSON response from {url}: {body[:500]}") from exc


def discover_catalog(base_url: str, api_key: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    raw = request_json(f"{base_url.rstrip('/')}/v1/sdk/toolkits", api_key=api_key)
    if not isinstance(raw, list):
        raise DemoError("Terrabox toolkit discovery did not return a list")
    tools: dict[str, dict[str, Any]] = {}
    for toolkit in raw:
        if not isinstance(toolkit, dict):
            continue
        toolkit_slug = str(toolkit.get("slug") or toolkit.get("name") or "unknown")
        for tool in toolkit.get("tools", []) or []:
            if not isinstance(tool, dict) or not tool.get("slug"):
                continue
            enriched = dict(tool)
            enriched["toolkit_slug"] = toolkit_slug
            enriched["toolkit_status"] = toolkit.get("status")
            tools[str(tool["slug"])] = enriched
    return raw, tools


def summarize_catalog(toolkits: list[dict[str, Any]], tools: dict[str, dict[str, Any]]) -> dict[str, Any]:
    available: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    not_applicable: list[dict[str, Any]] = []
    for slug, spec in sorted(tools.items()):
        status = str(spec.get("status") or "unknown")
        toolkit_status = str(spec.get("toolkit_status") or "unknown")
        if status != "available" or toolkit_status != "active":
            unavailable.append(
                {"slug": slug, "status": status, "toolkit_status": toolkit_status, "reason": "tool_not_available"}
            )
        elif slug in CORE_TOOL_SLUGS:
            available.append({"slug": slug, "reason": "applicable_to_paired_water_label_demo"})
        else:
            not_applicable.append(
                {"slug": slug, "reason": "available_but_not_selected_for_paired_water_label_demo"}
            )
    return {
        "toolkit_count": len(toolkits),
        "tool_count": len(tools),
        "available": available,
        "unavailable": unavailable,
        "not_applicable": not_applicable,
        "available_count": len(available),
        "unavailable_count": len(unavailable),
        "not_applicable_count": len(not_applicable),
    }


def detect_model(llm_url: str, requested_model: str) -> dict[str, Any]:
    """Record which model the endpoint actually serves, and whether it is the requested one."""
    models = request_json(f"{llm_url.rstrip('/')}/v1/models", timeout=30)
    model_id = "unknown"
    if isinstance(models, dict) and models.get("data"):
        model_id = str(models["data"][0].get("id") or "unknown")
    lowered = model_id.lower()
    model_used = next((name for key, name in MODEL_DISPLAY_NAMES if key in lowered), model_id)
    return {
        "model_requested": requested_model,
        "model_used": model_used,
        "endpoint_model_id": model_id,
        "fallback_reason": None if model_used == requested_model else f"endpoint_serves_{model_id}",
        "llm_endpoint": llm_url,
    }


def parse_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DemoError(f"Qwen returned non-JSON planning output: {text[:500]}") from exc
    if not isinstance(parsed, dict):
        raise DemoError("Qwen planning output must be a JSON object")
    return parsed


def plan_with_model(
    llm_url: str,
    model_state: dict[str, Any],
    tools: dict[str, dict[str, Any]],
    user_prompt: str | None = None,
    input_kind: str = "water_labels",
    task: str = "water_change",
) -> dict[str, Any]:
    scenario = SCENARIOS[input_kind]
    spec = TASKS[task]
    derive = scenario["derive_labels"]
    candidates = applicable_candidates(tools, input_kind, task)
    if derive and derive["tool"] not in {item["slug"] for item in candidates}:
        raise DemoError(f"{input_kind} input needs {derive['tool']}, which Terrabox does not report as available")
    if len(candidates) < spec["min_tools"]:
        raise DemoError(
            f"Only {len(candidates)} applicable Terrabox tools are available; need at least {spec['min_tools']}"
        )
    constraints = [
        "Use only the supplied available tool slugs.",
        f"Select at least {spec['min_tools']} tools.",
        *spec["constraints"],
    ]
    if scenario.get("planner_note"):
        constraints.append(scenario["planner_note"].replace("geo_water.water_evidence", _measure_tool(task)))
    prompt = {
        "task": user_prompt or spec["default_prompt"],
        "input_kind": input_kind,
        "constraints": constraints + [
            "Do not claim flood causation or satellite performance.",
            "If earlier conversation turns are given, answer the current task in that context.",
            "Write reason in Simplified Chinese.",
        ],
        "return_schema": {
            "selected_tools": ["tool.slug"],
            "execution_order": ["tool.slug"],
            "reason": "short explanation in Simplified Chinese",
        },
        "available_tools": candidates,
    }
    payload = {
        "model": model_state["endpoint_model_id"],
        "temperature": 0,
        "max_tokens": 512,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": "Return only valid JSON. Do not include a markdown fence."},
            {"role": "user", "content": "/no_think\n" + json.dumps(prompt, ensure_ascii=False)},
        ],
    }
    response = request_json(f"{llm_url.rstrip('/')}/v1/chat/completions", method="POST", payload=payload, timeout=120)
    try:
        choice = response["choices"][0]
        message = choice["message"]
        content = str(message.get("content") or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise DemoError(f"Unexpected llama.cpp response: {compact(response)}") from exc
    if not content:
        raise DemoError("Qwen returned no planning content; no deterministic plan was substituted")
    parsed = parse_json_content(content)
    selected = parsed.get("selected_tools")
    order = parsed.get("execution_order") or selected
    if not isinstance(selected, list) or not isinstance(order, list):
        raise DemoError("Qwen plan must contain selected_tools and execution_order arrays")
    raw_order = [str(item) for item in order]
    # A tool listed twice would run twice; keep the first occurrence and record
    # what the model returned so the normalisation stays visible in the trace.
    selected = list(dict.fromkeys(str(item) for item in selected))
    order = list(dict.fromkeys(raw_order))
    removed_duplicates = [slug for index, slug in enumerate(raw_order) if slug in raw_order[:index]]
    validate_order(order, selected, candidates, input_kind, task)
    return {
        "status": "real_model_plan",
        "response_id": response.get("id"),
        "selected_tools": selected,
        "execution_order": order,
        "removed_duplicates": removed_duplicates,
        "reason": parsed.get("reason"),
        "candidates": candidates,
    }


def _measure_tool(task: str) -> str:
    return "geo_water.water_extent" if task == "water_extent" else "geo_water.water_evidence"


def applicable_candidates(
    tools: dict[str, dict[str, Any]], input_kind: str, task: str = "water_change"
) -> list[dict[str, Any]]:
    derive = SCENARIOS[input_kind]["derive_labels"]
    allowed = TASKS[task]["tools"]
    if task == "water_extent" and not derive:
        # Label scenes are already water masks; there is nothing to threshold.
        allowed = tuple(slug for slug in allowed if slug != "geo_raster.threshold_segmentation")
    return [
        {
            "slug": slug,
            "name": tools[slug].get("name"),
            "description": tools[slug].get("description"),
            "parameters": tools[slug].get("parameters"),
        }
        for slug in allowed
        if slug in tools and tools[slug].get("status") == "available"
    ]


def validate_order(
    order: list[str],
    selected: list[str],
    candidates: list[dict[str, Any]],
    input_kind: str,
    task: str = "water_change",
) -> None:
    """Reject plans that break the scientific ordering rules, whoever wrote them."""
    derive = SCENARIOS[input_kind]["derive_labels"]
    candidate_slugs = {item["slug"] for item in candidates}
    if len(selected) < TASKS[task]["min_tools"]:
        raise DemoError(f"Plan selects only {len(selected)} tools; refusing an underspecified plan")
    if set(selected) - candidate_slugs or set(order) - candidate_slugs:
        raise DemoError("Plan uses a tool outside the live applicable catalog")
    if set(order) != set(selected):
        raise DemoError("Plan execution_order must contain exactly the selected tools")
    if task == "water_extent":
        if "geo_water.water_extent" not in order:
            raise DemoError("Plan omits geo_water.water_extent, which checks labels and the area grid")
        if derive and (
            derive["tool"] not in order or order.index(derive["tool"]) > order.index("geo_water.water_extent")
        ):
            raise DemoError(f"Plan must run {derive['tool']} before water extent for {input_kind} input")
        return
    if "geo_water.raster_profile_preflight" not in order or "geo_water.water_evidence" not in order:
        raise DemoError("Plan omits the required profile preflight or water evidence gate")
    preflight_index = order.index("geo_water.raster_profile_preflight")
    if any(order.index(slug) < preflight_index for slug in REQUIRES_PREFLIGHT if slug in order and slug != "geo_water.raster_profile_preflight"):
        raise DemoError("Plan places pairwise analysis before profile preflight")
    if "geo_basic.pixel_area" in order and order.index("geo_basic.pixel_area") < order.index("geo_water.water_evidence"):
        raise DemoError("Plan places pixel-area calculation before water evidence")
    if derive and (
        derive["tool"] not in order or order.index(derive["tool"]) > order.index("geo_water.water_evidence")
    ):
        raise DemoError(f"Plan must run {derive['tool']} before water evidence for {input_kind} input")


def confirmed_plan(
    path: Path, tools: dict[str, dict[str, Any]], input_kind: str, task: str = "water_change"
) -> dict[str, Any]:
    """A plan the user already confirmed in the workspace; it is re-validated, never re-planned."""
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DemoError(f"Confirmed plan is unreadable: {exc}") from exc
    order = [str(item) for item in stored.get("execution_order") or []]
    candidates = applicable_candidates(tools, input_kind, task)
    validate_order(order, order, candidates, input_kind, task)
    return {
        "status": "confirmed_plan",
        "plan_id": stored.get("plan_id"),
        "selected_tools": order,
        "execution_order": order,
        "removed_duplicates": stored.get("removed_duplicates") or [],
        "reason": stored.get("reason"),
        "candidates": candidates,
    }


EVENTS_PATH: Path | None = None


def emit_event(kind: str, **data: Any) -> None:
    """Append one JSON line to the events file the workspace streams to the browser."""
    if EVENTS_PATH is None:
        return
    line = json.dumps({"type": kind, "at": utc_now(), **data}, ensure_ascii=False)
    try:
        with EVENTS_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        return


def brief_outputs(outputs: Any) -> dict[str, Any]:
    """A few scalar fields of a tool result, enough to show progress in the browser."""
    if not isinstance(outputs, dict):
        return {}
    keys = (
        "reason_code", "threshold", "method", "foreground_fraction", "width", "height", "crs",
        "after_water_pixels", "new_water_area_m2", "area_km2", "value", "stat",
        "water_area_m2", "water_fraction", "valid_pixels",
    )
    return {key: outputs[key] for key in keys if key in outputs and not isinstance(outputs[key], (dict, list))}


def execution_id(run_id: str, chain: str, step: int) -> str:
    return f"exec_{run_id}_{chain}_{step:02d}_{uuid.uuid4().hex[:8]}"


def call_tool(
    base_url: str,
    api_key: str,
    *,
    run_id: str,
    chain: str,
    step: int,
    slug: str,
    inputs: dict[str, Any],
    model_state: dict[str, Any],
    scenario: str = "paired_water_water_labels",
) -> dict[str, Any]:
    call_id = execution_id(run_id, chain, step)
    target = inputs.get("_target")
    emit_event("tool_started", chain=chain, step=step, tool=slug, execution_id=call_id, target=target)
    inputs = {key: value for key, value in inputs.items() if key != "_target"}
    metadata = {
        "execution_id": call_id,
        "demo_id": run_id,
        "chain_id": chain,
        "scenario": scenario,
        "model_requested": model_state["model_requested"],
        "model_used": model_state["model_used"],
    }
    response = request_json(
        f"{base_url.rstrip('/')}/v1/sdk/tools/{slug}/execute",
        api_key=api_key,
        method="POST",
        payload={"inputs": inputs, "metadata": metadata},
        timeout=120,
    )
    outputs = response.get("outputs") if isinstance(response, dict) else None
    response_metadata = response.get("metadata") if isinstance(response, dict) else None
    manifest_path = response_metadata.get("manifest_path") if isinstance(response_metadata, dict) else None
    record = {
        "step": step,
        "tool_slug": slug,
        "execution_id": response.get("execution_id") or call_id,
        "inputs": compact(inputs),
        "success": bool(response.get("success")),
        "error": response.get("error"),
        "outputs": compact(outputs),
        "manifest_path": manifest_path,
        "manifest_exists": bool(manifest_path and Path(manifest_path).exists()),
        "trace_id": response.get("execution_id") or call_id,
    }
    if isinstance(outputs, dict):
        record["execution_blocked"] = outputs.get("execution_blocked")
        record["reason_code"] = outputs.get("reason_code")
        record["failed_checks"] = outputs.get("failed_checks")
    emit_event(
        "tool_finished",
        chain=chain,
        step=step,
        tool=slug,
        target=target,
        execution_id=call_id,
        success=record["success"],
        blocked=bool(record.get("execution_blocked")),
        reason_code=record.get("reason_code"),
        outputs=brief_outputs(outputs),
        error=record.get("error"),
    )
    return record


def tool_inputs(
    slug: str,
    *,
    before: str,
    after: str,
    output_dir: Path,
    step: int,
    evidence_outputs: dict[str, Any],
) -> dict[str, Any]:
    if slug == "raster_viewer.inspect_geotiff":
        return {"raster_path": before, "include_histogram": False, "include_tags": True}
    if slug == "geo_water.raster_profile_preflight":
        return {"before_path": before, "after_path": after}
    if slug == "geo_raster.raster_stats":
        return {"input_path": after, "stat": "mean"}
    if slug == "geo_raster.threshold_segmentation":
        return {
            "input_path": after,
            "threshold": 0.5,
            "output_path": str(output_dir / f"step_{step:02d}_threshold.tif"),
        }
    if slug == "geo_raster.raster_diff":
        return {
            "path_a": after,
            "path_b": before,
            "output_path": str(output_dir / f"step_{step:02d}_difference.tif"),
        }
    if slug == "geo_water.water_evidence":
        # SAR runs compare the derived 0/1 masks, label fixtures compare the inputs.
        masks = evidence_outputs.get("water_masks") or {}
        return {"before_path": masks.get("before", before), "after_path": masks.get("after", after)}
    if slug == "geo_basic.pixel_area":
        pixels = evidence_outputs.get("after_water_pixels")
        resolution = evidence_outputs.get("resolution")
        if pixels is None or not resolution:
            raise DemoError("pixel_area requires the preflight grid and the water_evidence output")
        return {"pixels": pixels, "gsd_x_m": abs(float(resolution[0])), "gsd_y_m": abs(float(resolution[1]))}
    raise DemoError(f"No safe input builder exists for selected tool {slug}")


def gate_refusal(record: dict[str, Any], outputs: dict[str, Any], fixture_type: str, records: list) -> dict[str, Any]:
    """A scientific gate stopped the chain: a valid outcome, not a runner error."""
    return {
        "status": "blocked_by_gate",
        "fixture_type": fixture_type,
        "blocked_tool": record["tool_slug"],
        "reason_code": outputs.get("reason_code"),
        "failed_checks": outputs.get("failed_checks"),
        "downstream_tools_skipped": True,
        "execution_count": len(records),
        "steps": records,
    }


def run_success_chain(
    base_url: str,
    api_key: str,
    *,
    run_id: str,
    plan: dict[str, Any],
    before: str,
    after: str,
    output_dir: Path,
    model_state: dict[str, Any],
    fixture_meta: dict[str, Any],
    progress_cb: ProgressCallback | None = None,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    evidence_outputs: dict[str, Any] = {}
    preflight_passed = False
    fixture_type = fixture_meta["type"]
    scenario = f"paired_water_{fixture_meta['input_kind']}"
    derive = SCENARIOS[fixture_meta["input_kind"]]["derive_labels"]
    total = len(plan["execution_order"])
    for step, slug in enumerate(plan["execution_order"], start=1):
        if progress_cb:
            progress_cb("executing_success", f"正在执行成功链工具：{slug}", step, total, slug)
        if slug in REQUIRES_PREFLIGHT and not preflight_passed:
            raise DemoError(f"Scientific gate refused {slug}: profile preflight has not passed")
        if derive and slug == derive["tool"]:
            # Otsu on the after scene, then the same threshold on the before
            # scene, so both dates are classified by one rule.
            masks: dict[str, str] = {}
            threshold = None
            for target, source in (("after", after), ("before", before)):
                inputs = {
                    "_target": target,
                    "input_path": source,
                    "output_path": str(output_dir / f"step_{step:02d}_{target}_water_mask.tif"),
                    **derive["params"],
                    "input_transform": derive["transform_by_scale"][fixture_meta.get("input_scale", "linear")],
                }
                if threshold is None:
                    inputs.update(method="otsu", otsu_range=derive["otsu_range"])
                else:
                    inputs.update(method="fixed", threshold=threshold)
                record = call_tool(
                    base_url, api_key, run_id=run_id, chain="success", step=step, slug=slug,
                    inputs=inputs, model_state=model_state, scenario=scenario,
                )
                record["target"] = target
                records.append(record)
                outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
                if not record["success"]:
                    raise DemoError(f"Success chain tool failed: {slug}: {record.get('error')}")
                if outputs.get("execution_blocked"):
                    return gate_refusal(record, outputs, fixture_type, records)
                masks[target] = str(outputs.get("output_path") or inputs["output_path"])
                threshold = outputs.get("threshold")
            evidence_outputs["water_masks"] = masks
            evidence_outputs["derived_labels"] = {
                "tool": slug,
                "preset": derive["preset"],
                "threshold": threshold,
                "units": derive["units"],
                "rule": derive["rule"],
            }
            continue
        inputs = tool_inputs(
            slug,
            before=before,
            after=after,
            output_dir=output_dir,
            step=step,
            evidence_outputs=evidence_outputs,
        )
        record = call_tool(
            base_url, api_key, run_id=run_id, chain="success", step=step, slug=slug,
            inputs=inputs, model_state=model_state, scenario=scenario,
        )
        records.append(record)
        outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
        if not record["success"] and not outputs.get("execution_blocked"):
            raise DemoError(f"Success chain tool failed: {slug}: {record.get('error')}")
        if slug == "geo_water.raster_profile_preflight":
            if outputs.get("execution_blocked"):
                return gate_refusal(record, outputs, fixture_type, records)
            preflight_passed = True
            evidence_outputs["resolution"] = (outputs.get("before") or {}).get("resolution")
        if slug == "geo_water.water_evidence":
            if outputs.get("execution_blocked") or not outputs.get("change_answerable"):
                return gate_refusal(record, outputs, fixture_type, records)
            evidence_outputs.update(outputs)
    return {
        "status": "success",
        "fixture_type": fixture_type,
        "execution_count": len(records),
        "derived_labels": evidence_outputs.get("derived_labels"),
        "steps": records,
    }


def run_extent_chain(
    base_url: str,
    api_key: str,
    *,
    run_id: str,
    plan: dict[str, Any],
    scene: str,
    output_dir: Path,
    model_state: dict[str, Any],
    fixture_meta: dict[str, Any],
    progress_cb: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Single-scene water: optional threshold mask, then geo_water.water_extent as the gate and measure."""
    records: list[dict[str, Any]] = []
    fixture_type = fixture_meta["type"]
    scenario = f"single_scene_water_{fixture_meta['input_kind']}"
    derive = SCENARIOS[fixture_meta["input_kind"]]["derive_labels"]
    labels = scene
    derived: dict[str, Any] | None = None
    total = len(plan["execution_order"])
    for step, slug in enumerate(plan["execution_order"], start=1):
        if progress_cb:
            progress_cb("executing_success", f"正在执行工具：{slug}", step, total, slug)
        if slug == "raster_viewer.inspect_geotiff":
            inputs = {"raster_path": scene, "include_histogram": False, "include_tags": True}
        elif slug == "geo_raster.raster_stats":
            inputs = {"input_path": scene, "stat": "mean"}
        elif slug == "geo_raster.threshold_segmentation" and derive:
            inputs = {
                "_target": "scene",
                "input_path": scene,
                "output_path": str(output_dir / f"step_{step:02d}_scene_water_mask.tif"),
                **derive["params"],
                "input_transform": derive["transform_by_scale"][fixture_meta.get("input_scale", "linear")],
                "method": "otsu",
                "otsu_range": derive["otsu_range"],
            }
        elif slug == "geo_water.water_extent":
            inputs = {"label_path": labels}
        else:
            raise DemoError(f"No safe input builder exists for selected tool {slug}")
        record = call_tool(
            base_url, api_key, run_id=run_id, chain="success", step=step, slug=slug,
            inputs=inputs, model_state=model_state, scenario=scenario,
        )
        if slug == "geo_raster.threshold_segmentation":
            record["target"] = "scene"
        records.append(record)
        outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
        if not record["success"] and not outputs.get("execution_blocked"):
            raise DemoError(f"Success chain tool failed: {slug}: {record.get('error')}")
        if outputs.get("execution_blocked"):
            return gate_refusal(record, outputs, fixture_type, records)
        if slug == "geo_raster.threshold_segmentation":
            labels = str(outputs.get("output_path") or inputs["output_path"])
            derived = {
                "tool": slug,
                "preset": derive["preset"],
                "threshold": outputs.get("threshold"),
                "units": derive["units"],
                "rule": "Otsu on this scene",
            }
    return {
        "status": "success",
        "fixture_type": fixture_type,
        "execution_count": len(records),
        "derived_labels": derived,
        "steps": records,
    }


def run_rejection_chain(
    base_url: str,
    api_key: str,
    *,
    run_id: str,
    before: str,
    mismatch: str | None,
    model_state: dict[str, Any],
    fixture_meta: dict[str, Any],
    self_check: bool = False,
    progress_cb: ProgressCallback | None = None,
) -> dict[str, Any]:
    if not self_check:
        return {
            "status": "not_requested",
            "fixture_type": fixture_meta["type"],
            "reason": "gate self-check not requested",
            "steps": [],
        }
    if mismatch is None:
        return {
            "status": "not_run",
            "fixture_type": fixture_meta["type"],
            "reason": "no mismatch raster supplied",
            "steps": [],
        }
    if progress_cb:
        progress_cb("executing_rejection", "正在执行尺寸不一致拒绝链", 1, 1, "geo_water.raster_profile_preflight")
    record = call_tool(
        base_url,
        api_key,
        run_id=run_id,
        chain="reject",
        step=1,
        slug="geo_water.raster_profile_preflight",
        inputs={"before_path": before, "after_path": mismatch},
        model_state=model_state,
        scenario=f"paired_water_{fixture_meta['input_kind']}",
    )
    outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
    if not record["success"] or not outputs.get("execution_blocked"):
        raise DemoError("Mismatch chain did not produce a deterministic execution_blocked refusal")
    return {
        "status": "deterministic_refusal",
        "fixture_type": fixture_meta["type"],
        "downstream_tools_skipped": True,
        "reason_code": outputs.get("reason_code"),
        "failed_checks": outputs.get("failed_checks"),
        "steps": [record],
    }


def attach_analytics(base_url: str, api_key: str, summary: dict[str, Any]) -> None:
    execution_ids = []
    for chain_name in ("success_chain", "rejection_chain"):
        for record in summary.get(chain_name, {}).get("steps", []):
            if record.get("execution_id"):
                execution_ids.append(record["execution_id"])
    try:
        query = urlencode({"size": 100})
        history = request_json(f"{base_url.rstrip('/')}/v1/sdk/executions?{query}", api_key=api_key)
        items = history.get("items", []) if isinstance(history, dict) else []
        matched = [item for item in items if item.get("trace_id") in execution_ids]
        summary["analytics"] = {
            "endpoint": "/v1/sdk/executions",
            "matched_count": len(matched),
            "expected_count": len(execution_ids),
            "records": compact(matched),
        }
    except DemoError as exc:
        summary["analytics"] = {"error": str(exc), "matched_count": 0, "expected_count": len(execution_ids)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terrabox-url", default=os.environ.get("TERRALINK_BASE_URL", DEFAULT_TERRABOX_URL))
    parser.add_argument("--terraflow-url", default=os.environ.get("TERRAFLOW_BASE_URL", DEFAULT_TERRAFLOW_URL))
    parser.add_argument("--llm-url", default=os.environ.get("LLM_BASE_URL", DEFAULT_LLM_URL))
    parser.add_argument("--api-key")
    parser.add_argument("--api-key-file", type=Path, default=Path("/tmp/terrabox-api-key"))
    parser.add_argument("--fixtures-dir", type=Path, default=Path(DEFAULT_FIXTURE_DIR))
    parser.add_argument("--summary-dir", type=Path, default=Path(DEFAULT_SUMMARY_DIR))
    parser.add_argument(
        "--requested-model",
        default=os.environ.get("TERRAFLOW_WATER_PLANNER_MODEL", DEFAULT_REQUESTED_MODEL),
        help="Model the run is meant to use; a different endpoint model is recorded as a fallback",
    )
    parser.add_argument("--prompt", help="Natural-language task sent to the model planner")
    parser.add_argument(
        "--task",
        choices=sorted(TASKS),
        default="water_change",
        help="water_change: before.tif/after.tif pair; water_extent: one scene.tif",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Also run the gate self-check: preflight on the deliberately mismatched raster, expecting a refusal",
    )
    parser.add_argument(
        "--plan-file",
        type=Path,
        help="Execute this confirmed plan (JSON with execution_order) instead of asking the model",
    )
    parser.add_argument(
        "--events-file",
        type=Path,
        help="Append one JSON event per line (stages, tool start/finish) for streaming",
    )
    parser.add_argument(
        "--progress-file",
        type=Path,
        help="Optional JSON status file for the web adapter",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    progress_path = args.progress_file
    global EVENTS_PATH
    EVENTS_PATH = args.events_file
    try:
        write_progress(progress_path, "starting", "正在启动水体 Agent")
        api_key = load_api_key(args.api_key_file, args.api_key)
        if args.task == "water_extent":
            scene = require_file(args.fixtures_dir / "scene.tif", "scene")
            before = after = mismatch = None
        else:
            scene = None
            before = require_file(args.fixtures_dir / "before.tif", "before fixture")
            after = require_file(args.fixtures_dir / "after.tif", "after fixture")
            mismatch_path = args.fixtures_dir / "mismatch.tif"
            mismatch = str(mismatch_path) if mismatch_path.exists() else None
        fixture_meta = load_fixture_meta(args.fixtures_dir)
        args.summary_dir.mkdir(parents=True, exist_ok=True)
        output_dir = args.summary_dir / run_id / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)

        write_progress(progress_path, "discovering", "正在发现 Terrabox 工具")
        toolkits, tools = discover_catalog(args.terrabox_url, api_key)
        catalog = summarize_catalog(toolkits, tools)
        write_progress(
            progress_path,
            "planning",
            "正在载入已确认的计划" if args.plan_file else "正在请求模型生成工具执行顺序",
        )
        try:
            model_state = detect_model(args.llm_url, args.requested_model)
        except DemoError as exc:
            if not args.plan_file:
                raise
            # A confirmed plan runs without the model; record who planned it instead.
            planned_by = json.loads(args.plan_file.read_text(encoding="utf-8")).get("model") or "unknown"
            model_state = {
                "model_requested": args.requested_model,
                "model_used": planned_by,
                "endpoint_model_id": None,
                "fallback_reason": f"model endpoint unavailable at execution ({exc}); plan was confirmed earlier",
                "llm_endpoint": args.llm_url,
            }
        if args.plan_file:
            plan = confirmed_plan(args.plan_file, tools, fixture_meta["input_kind"], args.task)
        else:
            plan = plan_with_model(
                args.llm_url,
                model_state,
                tools,
                user_prompt=args.prompt,
                input_kind=fixture_meta["input_kind"],
                task=args.task,
            )
        emit_event("plan", execution_order=plan["execution_order"], status=plan["status"])
        summary: dict[str, Any] = {
            "schema_version": "terraflow-water-demo.v1",
            "scenario": args.task,
            "run_id": run_id,
            "created_at": utc_now(),
            "terraflow_url": args.terraflow_url,
            "terrabox_url": args.terrabox_url,
            "branch": "feature/terraflow-water-demo",
            "fixtures": {
                **fixture_meta,
                **({"scene": scene} if scene else {"before": before, "after": after, "mismatch": mismatch}),
            },
            "catalog": catalog,
            "model": model_state,
            "plan": plan,
            "request": {"prompt": args.prompt or TASKS[args.task]["default_prompt"]},
        }
        progress = lambda stage, message, current, total, tool: write_progress(  # noqa: E731
            progress_path, stage, message, current, total, tool
        )
        if scene:
            summary["success_chain"] = run_extent_chain(
                args.terrabox_url,
                api_key,
                run_id=run_id,
                plan=plan,
                scene=scene,
                output_dir=output_dir,
                model_state=model_state,
                fixture_meta=fixture_meta,
                progress_cb=progress,
            )
            summary["rejection_chain"] = {
                "status": "not_applicable",
                "fixture_type": fixture_meta["type"],
                "reason": "single-scene task has no pair to mismatch",
                "steps": [],
            }
        else:
            summary["success_chain"] = run_success_chain(
                args.terrabox_url,
                api_key,
                run_id=run_id,
                plan=plan,
                before=before,
                after=after,
                output_dir=output_dir,
                model_state=model_state,
                fixture_meta=fixture_meta,
                progress_cb=progress,
            )
            summary["rejection_chain"] = run_rejection_chain(
                args.terrabox_url,
                api_key,
                run_id=run_id,
                before=before,
                mismatch=mismatch,
                model_state=model_state,
                fixture_meta=fixture_meta,
                self_check=args.self_check,
                progress_cb=progress,
            )
        write_progress(progress_path, "recording", "正在整理执行记录和证据索引")
        attach_analytics(args.terrabox_url, api_key, summary)
        summary_path = args.summary_dir / f"terraflow_water_demo_{run_id}.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_progress(progress_path, "complete", "运行完成")
        emit_event("run_finished", run_id=run_id, summary_path=str(summary_path))
        print(json.dumps({
            "status": "complete",
            "summary_path": str(summary_path),
            "run_id": run_id,
            "toolkits": catalog["toolkit_count"],
            "tools": catalog["tool_count"],
            "plan": plan["execution_order"],
            "model_requested": model_state["model_requested"],
            "model_used": model_state["model_used"],
            "fallback_reason": model_state["fallback_reason"],
            "fixture_type": fixture_meta["type"],
            "success_status": summary["success_chain"]["status"],
            "success_steps": summary["success_chain"]["execution_count"],
            "reject_reason": summary["rejection_chain"].get("reason_code"),
            "analytics_matches": summary.get("analytics", {}).get("matched_count"),
        }, ensure_ascii=False, indent=2))
        return 0
    except DemoError as exc:
        write_progress(progress_path, "error", str(exc))
        emit_event("run_failed", run_id=run_id, error=str(exc))
        print(json.dumps({"status": "blocked", "run_id": run_id, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

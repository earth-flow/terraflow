"""Terraflow's flow assistant inside the Agent Workspace (N041 stage 2).

In "build workflow" mode a chat message goes to Terraflow's flow assistant
(``/api/v1/agentic/assist/stream``) together with a short list of catalog data
the Scene Input / Water Input Set components can read. Each conversation owns
one Terraflow flow; the assistant's live ``flow_update`` events are applied to
a working copy here (the same reducer as the canvas), streamed to the page as
a node graph, and saved to the flow when the turn ends, so the next message
continues from it and the flow can be opened on the canvas.

"Run" executes the saved flow with Terraflow's engine and turns the Terrabox
tool outputs (water areas, thresholds, agreement, gate refusals, output
rasters) into the workspace's generic result card.

The workflow is built by a language model; only the Terrabox tools' own
checks (grid preflight, labels, projected area grid) apply. Flows that contain
assistant-written components are flagged before running.

Standard library only; the caller passes the user's Terraflow access token.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TERRAFLOW_URL = os.environ.get("TERRAFLOW_BASE_URL", "http://127.0.0.1:7861").rstrip("/")
PROVIDER = os.environ.get("WORKSPACE_ASSISTANT_PROVIDER", "Qwen")
MODEL = os.environ.get("WORKSPACE_ASSISTANT_MODEL", "qwen3.8-flash")
MAX_INPUT = 2000  # Terraflow's assistant request limit
Event = tuple[str, dict[str, Any]]

# Component fields worth showing on a graph node (values only; connections are edges).
SHOWN_PARAMS = (
    "scene_id", "input_set", "method", "threshold", "direction", "input_transform", "otsu_range",
    "index_name", "sensor", "field_path", "output_path", "stat",
)
METRICS = (
    ("valid_area_m2", "有效面积", "km²"),
    ("water_area_m2", "水体面积", "km²"),
    ("before_water_area_m2", "前期水体", "km²"),
    ("after_water_area_m2", "后期水体", "km²"),
    ("new_water_area_m2", "新增水体", "km²"),
    ("receded_water_area_m2", "消退水体", "km²"),
    ("predicted_water_area_m2", "识别水体", "km²"),
    ("reference_water_area_m2", "参考范围水体", "km²"),
)
RATIOS = (("water_fraction", "水体占比"), ("iou", "IoU"), ("f1", "F1"), ("precision", "精确率"), ("recall", "召回率"))


class TerraflowError(RuntimeError):
    pass


def _request(method: str, path: str, token: str, payload: Any = None, timeout: int = 60) -> Any:
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(TERRAFLOW_URL + path, data=body, method=method,
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - local Terraflow
            raw = response.read()
    except HTTPError as exc:
        raise TerraflowError(f"Terraflow {method} {path} 返回 {exc.code}：{exc.read().decode()[:200]}") from exc
    except URLError as exc:
        raise TerraflowError(f"无法连接 Terraflow：{exc}") from exc
    return json.loads(raw) if raw else None


def _get_flow(flow_id: str, token: str, attempts: int = 5) -> dict[str, Any]:
    """GET a flow, retrying a 404 briefly: under load a just-created flow can
    be read before Terraflow's create transaction is visible."""
    for attempt in range(attempts):
        try:
            return _request("GET", f"/api/v1/flows/{flow_id}", token)
        except TerraflowError as exc:
            if " 404" not in str(exc) or attempt == attempts - 1:
                raise
            time.sleep(0.5 * (attempt + 1))
    raise AssertionError("unreachable")


# Terraflow assistant tool names and steps, in the workspace's words.
TOOL_LABELS = {
    "search_components": "查找组件", "describe_component": "查看组件说明", "describe_flow_io": "查看工作流输入输出",
    "get_field_value": "读取参数", "build_flow": "生成工作流", "add_component": "添加节点",
    "connect_components": "连线", "configure_component": "设置参数", "remove_component": "删除节点",
    "run_flow": "试运行工作流", "propose_plan": "拟定计划", "generate_component": "编写自定义组件",
}
STEP_LABELS = {
    "generating_flow": "流程助手正在构思工作流…", "generation_complete": "流程助手已完成，正在检查…",
    "retrying": "检查发现问题，流程助手正在修改…", "validating": "正在试运行检查工作流…",
    "flow_proposal_ready": "工作流已生成",
}


def progress_text(event: dict[str, Any]) -> str:
    """Status line for a Terraflow progress event; tool calls read e.g. "查找组件：threshold"."""
    step, message = event.get("step") or "", event.get("message") or ""
    if step in {"searching_components", "building_flow"} and message:
        name, _, argument = message.partition(": ")
        label = TOOL_LABELS.get(name, name)
        return f"{label}：{argument}" if argument else label
    return STEP_LABELS.get(step) or message or "处理中…"


def _detached(events: Iterator[Event], waiting: str, heartbeat: float = 15.0) -> Iterator[Event]:
    """Run *events* to completion in a worker thread and relay them.

    The assistant can stay silent for minutes; a status line every *heartbeat*
    seconds keeps proxies from closing the stream, and if the page disconnects
    anyway the worker still finishes and stores its message, so a reload shows it.
    """
    queue: Queue[Event | None] = Queue()

    def work() -> None:
        try:
            for item in events:
                queue.put(item)
        except Exception as exc:  # noqa: BLE001 - reported to the page instead of a dead stream
            queue.put(("error", {"text": f"流程助手出错：{exc}"}))
        finally:
            queue.put(None)

    threading.Thread(target=work, daemon=True).start()
    started, last = time.monotonic(), f"{waiting}…"
    while True:
        try:
            item = queue.get(timeout=heartbeat)
        except Empty:
            yield "status", {"text": f"{last.rstrip('…')}…（已用 {round(time.monotonic() - started)} s）"}
            continue
        if item is None:
            return
        if item[0] == "status":
            last = item[1].get("text") or last
        yield item


def _stream_lines(path: str, token: str, payload: Any, timeout: int, accept: str) -> Iterator[str]:
    request = Request(TERRAFLOW_URL + path, data=json.dumps(payload).encode(), method="POST",
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": accept})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - local Terraflow
            for raw in response:
                yield raw.decode("utf-8", "replace").rstrip("\n")
    except HTTPError as exc:
        raise TerraflowError(f"Terraflow 返回 {exc.code}：{exc.read().decode()[:200]}") from exc
    except URLError as exc:
        raise TerraflowError(f"无法连接 Terraflow：{exc}") from exc


# ---------------------------------------------------------------------- flow working copy


def apply_update(data: dict[str, Any], event: dict[str, Any]) -> bool:
    """Apply one assistant ``flow_update`` to ``{"nodes", "edges"}`` like the canvas does. True if it changed."""
    action = event.get("action")
    nodes, edges = data.setdefault("nodes", []), data.setdefault("edges", [])
    if action == "set_flow":
        flow = (event.get("flow") or {}).get("data") or {}
        if isinstance(flow.get("nodes"), list):
            data["nodes"], data["edges"] = flow["nodes"], flow.get("edges") or []
            return True
    elif action == "add_component" and isinstance(event.get("node"), dict):
        nodes.append(event["node"])
        return True
    elif action == "connect" and isinstance(event.get("edge"), dict):
        edges.append(event["edge"])
        return True
    elif action == "remove_component" and event.get("component_id"):
        target = event["component_id"]
        data["nodes"] = [n for n in nodes if n.get("id") != target]
        data["edges"] = [e for e in edges if target not in (e.get("source"), e.get("target"))]
        return True
    elif action in ("configure", "edit_field"):
        params = event.get("params")
        if not isinstance(params, dict) and event.get("field_name"):
            params = {event["field_name"]: event.get("new_value", event.get("value"))}
        if isinstance(params, dict):
            for node in nodes:
                if node.get("id") == event.get("component_id"):
                    template = node.setdefault("data", {}).setdefault("node", {}).setdefault("template", {})
                    for key, value in params.items():
                        template[key] = {**(template.get(key) or {}), "value": value}
                    return True
    elif action == "enable_tool_mode" and isinstance(event.get("outputs"), list):
        for node in nodes:
            if node.get("id") == event.get("component_id"):
                inner = node.setdefault("data", {}).setdefault("node", {})
                inner.update(tool_mode=True, outputs=event["outputs"])
                return True
    return False


def _value(template: dict[str, Any], key: str) -> Any:
    field = template.get(key)
    return field.get("value") if isinstance(field, dict) else None


def graph(data: dict[str, Any]) -> dict[str, Any]:
    """Nodes in dependency order with a few parameters, edges as (source output -> target input)."""
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []
    by_id = {n.get("id"): n for n in nodes}
    incoming = {n.get("id"): 0 for n in nodes}
    for edge in edges:
        if edge.get("target") in incoming:
            incoming[edge["target"]] += 1
    order, ready = [], [i for i, d in incoming.items() if d == 0]
    remaining = dict(incoming)
    while ready:
        current = ready.pop(0)
        order.append(current)
        for edge in edges:
            if edge.get("source") == current and edge.get("target") in remaining:
                remaining[edge["target"]] -= 1
                if remaining[edge["target"]] == 0:
                    ready.append(edge["target"])
    order += [i for i in incoming if i not in order]
    out_nodes = []
    for node_id in order:
        node = by_id.get(node_id) or {}
        inner = (node.get("data") or {}).get("node") or {}
        template = inner.get("template") or {}
        node_type = (node.get("data") or {}).get("type") or ""
        params = {}
        for key in SHOWN_PARAMS:
            value = _value(template, key)
            if value not in (None, "", [], {}):
                params[key] = Path(str(value)).name if key == "output_path" else value
        out_nodes.append({
            "id": node_id,
            "type": node_type,
            "label": inner.get("display_name") or node_type,
            "terrabox": node_type.startswith("Terrabox"),
            "custom": node_type == "CustomComponent",
            "params": params,
        })
    out_edges = []
    for edge in edges:
        data_ = edge.get("data") or {}
        out_edges.append({
            "source": edge.get("source"),
            "target": edge.get("target"),
            "output": (data_.get("sourceHandle") or {}).get("name"),
            "input": (data_.get("targetHandle") or {}).get("fieldName"),
        })
    return {"nodes": out_nodes, "edges": out_edges}


def layer_names(data: dict[str, Any]) -> dict[str, str]:
    """Result-layer names from the wiring: a mask made from the before/after scene is
    "前期水体掩膜"/"后期水体掩膜", an index raster is named after its index."""
    nodes = {n.get("id"): n for n in data.get("nodes") or [] if isinstance(n, dict)}
    inputs = {}
    for edge in data.get("edges") or []:
        handles = (edge or {}).get("data") or {}
        source, target = handles.get("sourceHandle") or {}, handles.get("targetHandle") or {}
        if source.get("id") and target.get("id"):
            inputs[(target["id"], target.get("fieldName"))] = (source["id"], source.get("name") or "")

    def value(node_id: str, field: str) -> Any:
        template = (((nodes.get(node_id) or {}).get("data") or {}).get("node") or {}).get("template") or {}
        return (template.get(field) or {}).get("value") if isinstance(template.get(field), dict) else None

    def date_of(node_id: str, field: str) -> str | None:
        link, seen = inputs.get((node_id, field)), set()
        while link and link[0] not in seen:
            seen.add(link[0])
            source_type = ((nodes.get(link[0]) or {}).get("data") or {}).get("type")
            if source_type == "ToolResultField":
                link = inputs.get((link[0], "result"))
                continue
            if source_type == "WaterInputSet":
                return {"before_path": "前期", "after_path": "后期"}.get(link[1])
            if source_type == "SceneInput":
                scene = str(value(link[0], "scene_id") or "")
                return "前期" if scene.endswith(".before") else "后期" if scene.endswith(".after") else None
            return None
        return None

    names = {}
    for node_id, node in nodes.items():
        node_type = (node.get("data") or {}).get("type") or ""
        if node_type == "TerraboxGeoRasterThresholdSegmentation":
            names[node_id] = f"{date_of(node_id, 'input_path') or ''}水体掩膜"
        elif node_type == "TerraboxGeoRasterCalculateIndex":
            names[node_id] = f"{value(node_id, 'index_name') or '指数'} 栅格"
    return names


def domain_checks(summary: dict[str, Any]) -> list[str]:
    """Scientific problems visible in the flow itself, shown before running and kept on the result."""
    nodes = summary.get("nodes") or []
    notes = []
    otsu = [n for n in nodes if n["type"] == "TerraboxGeoRasterThresholdSegmentation"
            and str(n["params"].get("method") or "").lower() == "otsu"]
    pairwise = any(n["type"] == "TerraboxGeoWaterWaterEvidence" for n in nodes)
    if pairwise and len(otsu) >= 2:
        notes.append("两期影像各自用 Otsu 求阈值：应在后期影像上求一次阈值、前期沿用同一阈值，"
                     "否则洪水前（水少）的阈值会偏高，前期水体被高估、新增水体被低估。")
    custom = [n["label"] for n in nodes if n["custom"]]
    if custom:
        notes.append("包含流程助手自写的组件（" + "、".join(custom) + "），这些步骤不经过 Terrabox 的检查。")
    if any(n["type"] == "TerraboxGeoWaterWaterEvidence" for n in nodes) and not any(
        n["type"] == "TerraboxGeoWaterRasterProfilePreflight" for n in nodes
    ):
        notes.append("两期比较前没有网格预检（raster_profile_preflight）。")
    return notes


# ---------------------------------------------------------------------- assistant turn


class FlowAssistant:
    def __init__(self, web: Any, store: Any) -> None:
        self.web = web
        self.store = store
        self.run_root = web.RUNTIME_DIR / "assistant-runs"

    def _data_hint(self, attachments: list[str]) -> str:
        """Catalog data the geo input components can read: attached items first, else the user's data."""
        index = self.web.read_json(self.web.RUNTIME_DIR / "scene-index.json") or {}
        scenes = {k: v for k, v in index.items() if v.get("category") not in ("synthetic", "test")}
        pairs = sorted({k.rsplit(".", 1)[0] for k in scenes if k.endswith((".before", ".after"))})
        chosen = [a for a in attachments if a in scenes] or sorted(scenes)[:12]
        lines = [f"- scene_id={sid}（{scenes[sid].get('collection_title')}，{scenes[sid].get('acquired') or '日期未记录'}，"
                 f"{scenes[sid].get('sensor') or ''}）" for sid in chosen]
        if pairs:
            lines.append("- 成对数据 input_set=" + "、".join(pairs))
        unusable = [a for a in attachments if a not in scenes]
        if unusable:
            lines.append("- 附加的 " + "、".join(unusable) + " 不能用 Scene Input 读取（多波段或非栅格），不要使用。")
        return "\n\n[可用数据，供 Scene Input / Water Input Set 使用]\n" + "\n".join(lines) if lines else ""

    def _ensure_flow(self, conversation: dict[str, Any], token: str) -> dict[str, Any]:
        flow_id = conversation.get("assistant_flow_id")
        if flow_id:
            try:
                return _get_flow(flow_id, token)
            except TerraflowError:
                pass  # deleted on the canvas: start a new one
        title = (conversation.get("title") or "工作区工作流")[:40]
        flow = _request("POST", "/api/v1/flows/", token, {
            "name": f"工作区助手 · {title} · {uuid.uuid4().hex[:4]}",
            "description": "由 Agent Workspace 中的流程助手搭建",
            "data": {"nodes": [], "edges": []},
        })
        self.store.set_meta(conversation["id"], assistant_flow_id=flow["id"],
                            assistant_session_id=str(uuid.uuid4()))
        return flow

    def build(self, conversation_id: str, content: str, attachments: list[str], token: str) -> Iterator[Event]:
        return _detached(self._build(conversation_id, content, attachments, token), "流程助手正在搭建工作流")

    def _build(self, conversation_id: str, content: str, attachments: list[str], token: str) -> Iterator[Event]:
        user = self.store.append(conversation_id, {"role": "user", "kind": "text", "mode": "assistant",
                                                   "content": content, "attachments": attachments})
        yield "message", user
        conversation = self.store.load(conversation_id) or {}
        try:
            flow = self._ensure_flow(conversation, token)
            flow_id = flow["id"]
            conversation = self.store.load(conversation_id) or {}
        except TerraflowError as exc:
            yield from self._fail(conversation_id, str(exc))
            return
        data = flow.get("data") or {"nodes": [], "edges": []}
        hint = self._data_hint(attachments)
        text = content[: MAX_INPUT - len(hint)] + hint
        yield "status", {"text": "流程助手正在理解任务…"}
        reply, changed, usage, error, started = "", False, None, None, time.monotonic()
        payload = {"flow_id": flow_id, "input_value": text, "provider": PROVIDER, "model_name": MODEL,
                   "session_id": conversation.get("assistant_session_id")}
        try:
            for line in _stream_lines("/api/v1/agentic/assist/stream", token, payload, 1800, "text/event-stream"):
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                kind = event.get("event")
                if kind == "progress":
                    yield "status", {"text": progress_text(event)}
                elif kind == "token":
                    reply += event.get("chunk") or ""
                    yield "token", {"text": event.get("chunk") or ""}
                elif kind == "flow_update" and apply_update(data, event):
                    changed = True
                    yield "flow", {"flow_id": flow_id, "graph": graph(data), "action": event.get("action")}
                elif kind == "complete":
                    result = event.get("data") or {}
                    reply = result.get("result") or reply
                    usage = result.get("usage")
                elif kind == "error":
                    error = event.get("message") or "流程助手出错"
        except TerraflowError as exc:
            error = str(exc)
        seconds = round(time.monotonic() - started, 1)
        if changed:
            try:
                _request("PATCH", f"/api/v1/flows/{flow_id}", token, {"data": data})
            except TerraflowError as exc:
                error = error or f"工作流没有保存：{exc}"
        summary = graph(data)
        message = self.store.append(conversation_id, {
            "role": "assistant",
            "kind": "flow" if summary["nodes"] else "text",
            "mode": "assistant",
            "content": reply or (f"流程助手没有完成：{error}" if error else "流程助手没有给出回复。"),
            "flow_id": flow_id,
            "graph": summary,
            "changed": changed,
            "error": error,
            "model": f"{PROVIDER} / {MODEL}",
            "seconds": seconds,
            "usage": usage,
            "custom_components": [n["label"] for n in summary["nodes"] if n["custom"]],
            "checks": domain_checks(summary),
        })
        yield "message", message
        yield "done", {}

    def _fail(self, conversation_id: str, text: str) -> Iterator[Event]:
        message = self.store.append(conversation_id, {"role": "assistant", "kind": "text", "mode": "assistant",
                                                      "content": f"流程助手没有完成：{text}"})
        yield "message", message
        yield "done", {}

    # ------------------------------------------------------------------ run

    def run(self, conversation_id: str, message_id: str, token: str) -> Iterator[Event]:
        return _detached(self._run_locked(conversation_id, message_id, token), "工作流正在执行")

    def _run_locked(self, conversation_id: str, message_id: str, token: str) -> Iterator[Event]:
        conversation = self.store.load(conversation_id) or {}
        message = next((m for m in conversation.get("messages", []) if m.get("id") == message_id), None)
        if not message or message.get("kind") != "flow":
            yield "error", {"text": "工作流不存在。"}
            return
        if not self.web._RUN_LOCK.acquire(blocking=False):
            yield "error", {"text": "已有任务在运行，请稍后再执行。"}
            return
        try:
            yield from self._run(conversation_id, message, token)
        finally:
            self.web._RUN_LOCK.release()

    def _run(self, conversation_id: str, message: dict[str, Any], token: str) -> Iterator[Event]:
        flow_id = message["flow_id"]
        try:
            flow = _get_flow(flow_id, token)
        except TerraflowError as exc:
            yield "error", {"text": str(exc)}
            return
        data = flow.get("data") or {}
        names = {n["id"]: n for n in graph(data)["nodes"]}
        yield "status", {"text": "Terraflow 引擎正在执行工作流…"}
        steps: list[dict[str, Any]] = []
        errors: list[str] = []
        started = time.monotonic()
        try:
            for line in _stream_lines(f"/api/v1/build/{flow_id}/flow?event_delivery=direct", token, {"data": data},
                                      900, "application/x-ndjson"):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind, payload = event.get("event"), event.get("data") or {}
                if kind == "end_vertex":
                    built = payload.get("build_data") or {}
                    node = names.get(built.get("id")) or {"label": built.get("id"), "type": ""}
                    outputs = _tool_outputs(built)
                    step = {
                        "chain": "assistant",
                        "node_id": built.get("id"),
                        "step": len(steps) + 1,
                        "tool_slug": node["type"],
                        "target": node["label"],
                        "success": bool(built.get("valid")) and not (outputs or {}).get("execution_blocked"),
                        "execution_blocked": bool((outputs or {}).get("execution_blocked")),
                        "reason_code": (outputs or {}).get("reason_code"),
                        "outputs": outputs if outputs is not None else _short(
                            (built.get("data") or {}).get("results") or (built.get("data") or {}).get("outputs")),
                        "error": None if built.get("valid") else _short(built.get("params")),
                    }
                    steps.append(step)
                    yield "step", {"id": f"s{step['step']}", "tool": node["label"], "chain": "analysis",
                                   "step": step["step"], "target": None, "reason_code": step["reason_code"],
                                   "state": "done" if step["success"] else "blocked" if step["execution_blocked"]
                                   else "failed", "outputs": _brief(outputs)}
                elif kind == "error":
                    errors.append(_short(payload, 300))
        except TerraflowError as exc:
            errors.append(str(exc))
        run_id = "af_" + time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        result = self._result(run_id, flow_id, steps, errors, message, layer_names(data))
        result["seconds"] = round(time.monotonic() - started, 1)
        self.run_root.mkdir(parents=True, exist_ok=True)
        (self.run_root / f"{run_id}.json").write_text(json.dumps(
            {"run_id": run_id, "flow_id": flow_id, "created_at": self.web.utc_now(), "steps": steps,
             "errors": errors, "layers": result.pop("_layer_paths")}, ensure_ascii=False, indent=2), encoding="utf-8")
        yield "result", {"run_id": run_id, "result": result}
        stored = self.store.append(conversation_id, {
            "role": "assistant", "kind": "run", "mode": "assistant", "run_id": run_id,
            "plan_message_id": message["id"], "input_title": "流程助手搭建的工作流", "scenario": "assistant_flow",
            "result": result, "content": "", "model": message.get("model"),
        })
        yield "message", stored
        yield "done", {}

    def _result(self, run_id: str, flow_id: str, steps: list[dict[str, Any]], errors: list[str],
                message: dict[str, Any], names: dict[str, str] | None = None) -> dict[str, Any]:
        outputs = [s["outputs"] for s in steps if isinstance(s.get("outputs"), dict)]
        merged: dict[str, Any] = {}
        for item in outputs:
            merged.update({k: v for k, v in item.items() if v is not None})
        metrics = []
        for key, label, unit in METRICS:
            if isinstance(merged.get(key), (int, float)):
                metrics.append({"key": key, "label": label, "value": round(merged[key] / 1e6, 2), "unit": unit})
        for key, label in RATIOS:
            if isinstance(merged.get(key), (int, float)):
                metrics.append({"key": key, "label": label, "value": round(100 * merged[key], 1), "unit": "%"})
        threshold = next((o.get("threshold") for o in outputs if isinstance(o.get("threshold"), (int, float))), None)
        if threshold is not None:
            transform = next((o.get("input_transform") for o in outputs if o.get("input_transform")), None)
            metrics.append({"key": "threshold", "label": "阈值", "value": round(threshold, 2),
                            "unit": "dB" if transform == "db" else ""})
        blocked = next((s for s in steps if s["execution_blocked"]), None)
        failed = [s for s in steps if not s["success"] and not s["execution_blocked"]]
        geo_checked = any(s["tool_slug"].startswith("TerraboxGeoWater") for s in steps)
        gate = {
            "checked": geo_checked or blocked is not None,
            "passed": blocked is None and not failed and not errors,
            "blocked_tool": blocked["target"] if blocked else None,
            "reason_code": blocked["reason_code"] if blocked else None,
            "failed_checks": [blocked["reason_code"]] if blocked and blocked["reason_code"] else [],
            "label": "Terrabox 检查",
            "description": "网格、标签和面积坐标系由 Terrabox 工具检查；工作流本身由流程助手搭建，未经场景规则校验",
        }
        names = names or {}
        layer_paths = []
        for step in steps:
            path = (step.get("outputs") or {}).get("output_path") if isinstance(step.get("outputs"), dict) else None
            if path and str(path).endswith((".tif", ".tiff")) and Path(path).is_file():
                mode = "continuous" if "Index" in step["tool_slug"] else "labels"
                label = names.get(step.get("node_id") or "") or f"{step['target']} 输出"
                layer_paths.append({"path": path, "mode": mode, "label": label})
        layers = [{
            "key": f"layer{i}", "label": item["label"],
            "url": f"/api/workspace/assistant-runs/{run_id}/preview/{i}",
            "legend": [{"color": "#2570eb", "label": "水体"}, {"color": "#484d57", "label": "非水体"}]
            if item["mode"] == "labels" else [{"color": "linear-gradient(90deg,#000,#fff)", "label": "低 → 高"}],
        } for i, item in enumerate(layer_paths)]
        by_key = {m["key"]: m for m in metrics}
        if errors or failed:
            headline = f"工作流有 {len(failed) or len(errors)} 处运行失败" + (f"：{failed[0]['target']}" if failed else "") + "。"
        elif blocked:
            headline = f"Terrabox 检查拦截了「{blocked['target']}」（{blocked['reason_code']}），没有给出结果。"
        elif "water_area_m2" in by_key:
            headline = f"水体面积 {by_key['water_area_m2']['value']} km²" + (
                f"，占有效面积 {by_key['valid_area_m2']['value']} km² 的 {by_key['water_fraction']['value']}%" if
                "valid_area_m2" in by_key and "water_fraction" in by_key else "") + "。"
        elif "new_water_area_m2" in by_key:
            headline = f"新增水体 {by_key['new_water_area_m2']['value']} km²。"
        elif "iou" in by_key:
            headline = f"与参考范围相比 IoU {by_key['iou']['value']}%。"
        else:
            headline = f"工作流运行完成，共 {len(steps)} 个节点。"
        boundaries = [
            f"工作流由流程助手（{message.get('model')}）搭建，执行前没有经过工作区的场景规则校验；"
            "只有 Terrabox 工具自身的检查生效。",
        ]
        if merged.get("area_method"):
            boundaries.append(f"面积按 {merged.get('crs')} 的像元面积计算（{merged.get('projection_method')}）。")
        if threshold is not None:
            boundaries.append("水体由阈值估计，不是参考标注。")
        boundaries += [f"运行前检查发现：{note}" for note in message.get("checks") or []]
        return {
            "scenario": "assistant_flow",
            "status": "success" if gate["passed"] else "blocked",
            "headline": headline,
            "metrics": metrics,
            "layers": layers,
            "gate": gate,
            "self_check": None,
            "boundaries": boundaries,
            "canvas": True,
            "canvas_url": f"/flow/{flow_id}",
            "steps_inline": steps,
            "_layer_paths": layer_paths,
        }

    def preview(self, run_id: str, index: int) -> Path | None:
        if not re.fullmatch(r"af_\d{8}_\d{6}_[0-9a-f]{6}", run_id):
            return None
        record = self.web.read_json(self.run_root / f"{run_id}.json") or {}
        layers = record.get("layers") or []
        if not 0 <= index < len(layers):
            return None
        return self.web.CATALOG.render_layer(Path(layers[index]["path"]), layers[index])


def _tool_outputs(built: dict[str, Any]) -> dict[str, Any] | None:
    """The Terrabox tool result (``outputs``) from an engine vertex, when the vertex is a Terrabox tool."""
    data = built.get("data") or {}
    # Terrabox tool vertices report on ``outputs``; ``results`` is often empty for them.
    results = next((part for part in (data.get("results"), data.get("outputs"))
                    if isinstance(part, dict) and part.get("api_run_model")), {})
    message = (results.get("api_run_model") or {}).get("message")
    if isinstance(message, str):
        try:
            message = json.loads(message)
        except json.JSONDecodeError:
            return None
    if isinstance(message, dict):
        outputs = message.get("outputs")
        return outputs if isinstance(outputs, dict) else None
    return None


def _brief(outputs: dict[str, Any] | None) -> dict[str, Any]:
    keys = ("threshold", "water_area_m2", "water_fraction", "new_water_area_m2", "iou", "reason_code", "crs")
    return {k: outputs[k] for k in keys if outputs and k in outputs and not isinstance(outputs[k], (dict, list))}


def _short(value: Any, limit: int = 600) -> Any:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    return text[:limit]

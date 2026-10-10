#!/usr/bin/env python3
"""Turn a validated water-agent plan into a Terraflow canvas flow.

The flow is built from the live component catalog: Terrabox tool components
for each planned step, plus the geo_workflow components (Water Input Set,
Raster Pair Gate, Tool Result Field). Scientifically sensitive parameters
come from the runner's SCENARIOS presets, exactly as in the CLI/web runs.

Without --run-id a template flow is built for --input-set using --plan (or
the default SAR/label plan). Run with the Terraflow virtualenv, which
provides lfx:

    .venv/bin/python scripts/build_water_flow.py --input-set thessaly_2023_s1
    .venv/bin/python scripts/build_water_flow.py --run-id 20261008_094505_41ae4f

Prints a JSON object with the created flow id and canvas path.
"""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from lfx.graph.flow_builder.builder import build_flow_from_spec
from lfx.interface.earthflow_terrabox import component_class_name

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace_scenarios import TASK_SCENARIOS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TERRAFLOW_URL = os.environ.get("TERRAFLOW_BASE_URL", "http://127.0.0.1:7861")
DEFAULT_RUNTIME_DIR = os.environ.get("TERRAFLOW_WATER_RUNTIME_DIR", "/data2/liyezhuoxin/terrabox-water-demo/runtime")
DEFAULT_EXTENT_PLANS = {
    "sar_backscatter": [
        "raster_viewer.inspect_geotiff",
        "geo_raster.threshold_segmentation",
        "geo_water.water_extent",
    ],
    "water_labels": ["raster_viewer.inspect_geotiff", "geo_raster.raster_stats", "geo_water.water_extent"],
}
DEFAULT_PLANS = {
    "sar_backscatter": [
        "raster_viewer.inspect_geotiff",
        "geo_water.raster_profile_preflight",
        "geo_raster.threshold_segmentation",
        "geo_raster.raster_diff",
        "geo_water.water_evidence",
        "geo_basic.pixel_area",
    ],
    "water_labels": [
        "raster_viewer.inspect_geotiff",
        "geo_water.raster_profile_preflight",
        "geo_raster.raster_stats",
        "geo_water.water_evidence",
        "geo_basic.pixel_area",
    ],
}


def load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("terraflow_water_demo", ROOT / "scripts" / "terraflow_water_demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def http_json(url: str, *, token: str | None = None, payload: Any = None) -> Any:
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")  # noqa: S310
    with urlopen(request, timeout=180) as response:  # noqa: S310 - fixed http(s) Terraflow URL
        body = response.read()
        # Terraflow compresses large responses even without Accept-Encoding.
        if response.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return json.loads(body)


def access_token(base_url: str) -> str:
    """Token of the requesting user (set by the web adapter), else auto-login, else a login file."""
    token = os.environ.get("TERRAFLOW_ACCESS_TOKEN", "").strip()
    if token:
        return token
    try:
        return http_json(f"{base_url}/api/v1/auto_login")["access_token"]
    except (HTTPError, KeyError):
        pass
    login_file = Path(os.environ.get("TERRAFLOW_LOGIN_FILE", "~/.config/agent-workspace/terraflow-login.json")).expanduser()
    credentials = json.loads(login_file.read_text(encoding="utf-8"))
    form = urlencode({"username": credentials["username"], "password": credentials["password"]}).encode()
    request = Request(  # noqa: S310 - fixed http(s) Terraflow URL
        f"{base_url}/api/v1/login",
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    with urlopen(request, timeout=60) as response:  # noqa: S310 - fixed http(s) Terraflow URL
        body = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return json.loads(body)["access_token"]


def live_registry(base_url: str, token: str) -> dict[str, dict]:
    catalog = http_json(f"{base_url}/api/v1/all", token=token)
    registry: dict[str, dict] = {}
    for category, components in catalog.items():
        if not isinstance(components, dict):
            continue
        for name, template in components.items():
            if isinstance(template, dict) and "template" in template:
                registry[name] = {**template, "category": category}
    return registry


def input_set_id(directory: Path, runtime_dir: Path) -> str:
    fixtures = runtime_dir / "demo-fixtures"
    if directory == fixtures:
        return "demo-fixtures"
    if directory.parent == fixtures:
        return directory.name
    if directory.parent == runtime_dir / "web-uploads":
        return f"upload-{directory.name}"
    msg = f"{directory} is not a known input set location under {runtime_dir}"
    raise ValueError(msg)


def build_spec(
    *,
    name: str,
    description: str,
    set_id: str,
    plan: list[str],
    input_kind: str,
    input_scale: str,
    has_mismatch: bool,
    scenarios: dict[str, Any],
    output_dir: Path,
    scene_ids: dict[str, str] | None = None,
) -> str:
    nodes: dict[str, str] = {}
    edges: list[str] = []
    config: dict[str, Any] = {}

    def node(node_id: str, component: str) -> str:
        nodes[node_id] = component
        return node_id

    def tool(node_id: str, slug: str) -> str:
        return node(node_id, component_class_name(slug))

    def field(node_id: str, source: str, path: str) -> str:
        node(node_id, "ToolResultField")
        edges.append(f"{source}.api_run_model -> {node_id}.result")
        config[f"{node_id}.field_path"] = path
        return f"{node_id}.value"

    if scene_ids:
        # Scene library runs: one Scene Input per role.
        for node_id, role in (("Before", "before"), ("After", "after"), ("SelfCheck", "mismatch")):
            if scene_ids.get(role):
                node(node_id, "SceneInput")
                config[f"{node_id}.scene_id"] = scene_ids[role]
        before_src, after_src, mismatch_src = "Before.path", "After.path", "SelfCheck.path"
    else:
        node("Input", "WaterInputSet")
        config["Input.input_set"] = set_id
        before_src, after_src, mismatch_src = "Input.before_path", "Input.after_path", "Input.mismatch_path"
    tool("Preflight", "geo_water.raster_profile_preflight")
    edges += [f"{before_src} -> Preflight.before_path", f"{after_src} -> Preflight.after_path"]
    node("Gate", "RasterPairGate")
    edges += [
        "Preflight.api_run_model -> Gate.preflight",
        f"{before_src} -> Gate.before_path",
        f"{after_src} -> Gate.after_path",
    ]

    derive = scenarios[input_kind]["derive_labels"]
    label_before, label_after = "Gate.gated_before_path", "Gate.gated_after_path"
    for slug in plan:
        if slug == "geo_water.raster_profile_preflight":
            continue
        if slug == "raster_viewer.inspect_geotiff":
            tool("Inspect", slug)
            edges.append(f"{before_src} -> Inspect.raster_path")
            config["Inspect.include_histogram"] = False
        elif slug == "geo_raster.raster_stats":
            tool("Stats", slug)
            edges.append(f"{after_src} -> Stats.input_path")
            config["Stats.stat"] = "mean"
        elif slug == "geo_raster.raster_diff":
            tool("Diff", slug)
            edges += ["Gate.gated_after_path -> Diff.path_a", "Gate.gated_before_path -> Diff.path_b"]
            config["Diff.output_path"] = str(output_dir / "difference.tif")
        elif slug == "geo_raster.threshold_segmentation" and derive:
            # Same rule as the runner: Otsu on the after scene, threshold reused on the before scene.
            preset = {
                **derive["params"],
                "input_transform": derive["transform_by_scale"][input_scale],
            }
            tool("MaskAfter", slug)
            tool("MaskBefore", slug)
            edges += [
                "Gate.gated_after_path -> MaskAfter.input_path",
                "Gate.gated_before_path -> MaskBefore.input_path",
            ]
            for target in ("MaskAfter", "MaskBefore"):
                for key, value in preset.items():
                    config[f"{target}.{key}"] = value
            config["MaskAfter.method"] = "otsu"
            config["MaskAfter.otsu_range"] = json.dumps(derive["otsu_range"])
            config["MaskAfter.output_path"] = str(output_dir / "after_water_mask.tif")
            config["MaskBefore.method"] = "fixed"
            config["MaskBefore.output_path"] = str(output_dir / "before_water_mask.tif")
            edges.append(f"{field('AfterThreshold', 'MaskAfter', 'outputs.threshold')} -> MaskBefore.threshold")
            label_after = field("AfterMaskPath", "MaskAfter", "outputs.output_path")
            label_before = field("BeforeMaskPath", "MaskBefore", "outputs.output_path")
        elif slug == "geo_raster.threshold_segmentation":
            tool("Threshold", slug)
            edges.append(f"{after_src} -> Threshold.input_path")
            config["Threshold.threshold"] = 0.5
            config["Threshold.output_path"] = str(output_dir / "threshold.tif")
        elif slug == "geo_water.water_evidence":
            tool("Evidence", slug)
            edges += [f"{label_before} -> Evidence.before_path", f"{label_after} -> Evidence.after_path"]
        elif slug == "geo_basic.pixel_area":
            tool("Area", slug)
            edges += [
                f"{field('AfterWaterPixels', 'Evidence', 'outputs.after_water_pixels')} -> Area.pixels",
                f"{field('PixelSizeX', 'Preflight', 'outputs.before.resolution.0')} -> Area.gsd_x_m",
                f"{field('PixelSizeY', 'Preflight', 'outputs.before.resolution.1')} -> Area.gsd_y_m",
            ]
        else:
            msg = f"No canvas mapping for planned tool {slug}"
            raise ValueError(msg)

    if has_mismatch:
        # Refusal branch: the gate should stop evidence on the mismatched pair.
        tool("RefusalPreflight", "geo_water.raster_profile_preflight")
        node("RefusalGate", "RasterPairGate")
        tool("RefusalEvidence", "geo_water.water_evidence")
        edges += [
            f"{before_src} -> RefusalPreflight.before_path",
            f"{mismatch_src} -> RefusalPreflight.after_path",
            "RefusalPreflight.api_run_model -> RefusalGate.preflight",
            f"{before_src} -> RefusalGate.before_path",
            f"{mismatch_src} -> RefusalGate.after_path",
            "RefusalGate.gated_before_path -> RefusalEvidence.before_path",
            "RefusalGate.gated_after_path -> RefusalEvidence.after_path",
        ]
    return render_spec(name, description, nodes, edges, config)


def build_extent_spec(
    *,
    name: str,
    description: str,
    scene_id: str,
    plan: list[str],
    input_kind: str,
    input_scale: str,
    scenarios: dict[str, Any],
    output_dir: Path,
) -> str:
    """Single-scene water: Scene Input, optional threshold mask, then water extent (the area gate)."""
    nodes: dict[str, str] = {"Scene": "SceneInput"}
    edges: list[str] = []
    config: dict[str, Any] = {"Scene.scene_id": scene_id}
    derive = scenarios[input_kind]["derive_labels"]
    labels = "Scene.path"
    for slug in plan:
        if slug == "raster_viewer.inspect_geotiff":
            nodes["Inspect"] = component_class_name(slug)
            edges.append("Scene.path -> Inspect.raster_path")
            config["Inspect.include_histogram"] = False
        elif slug == "geo_raster.raster_stats":
            nodes["Stats"] = component_class_name(slug)
            edges.append("Scene.path -> Stats.input_path")
            config["Stats.stat"] = "mean"
        elif slug == "geo_raster.threshold_segmentation" and derive:
            nodes["Mask"] = component_class_name(slug)
            edges.append("Scene.path -> Mask.input_path")
            preset = {**derive["params"], "input_transform": derive["transform_by_scale"][input_scale]}
            for key, value in preset.items():
                config[f"Mask.{key}"] = value
            config["Mask.method"] = "otsu"
            config["Mask.otsu_range"] = json.dumps(derive["otsu_range"])
            config["Mask.output_path"] = str(output_dir / "scene_water_mask.tif")
            nodes["MaskPath"] = "ToolResultField"
            edges.append("Mask.api_run_model -> MaskPath.result")
            config["MaskPath.field_path"] = "outputs.output_path"
            labels = "MaskPath.value"
        elif slug == "geo_water.water_extent":
            nodes["Extent"] = component_class_name(slug)
        else:
            msg = f"No canvas mapping for planned tool {slug}"
            raise ValueError(msg)
    if "Extent" in nodes:
        edges.append(f"{labels} -> Extent.label_path")
    return render_spec(name, description, nodes, edges, config)


def render_spec(name: str, description: str, nodes: dict[str, str], edges: list[str], config: dict[str, Any]) -> str:
    lines = [f"name: {name}", f"description: {description}", "", "nodes:"]
    lines += [f"  {node_id}: {component}" for node_id, component in nodes.items()]
    lines += ["", "edges:"] + [f"  {edge}" for edge in edges]

    def text(value: Any) -> str:
        return ("true" if value else "false") if isinstance(value, bool) else str(value)

    lines += ["", "config:"] + [f"  {key}: {text(value)}" for key, value in config.items()]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run-id", help="Build the flow from this run summary's validated plan")
    source.add_argument("--input-set", help="Build a template flow for this earlier before/after input set")
    source.add_argument(
        "--scenario",
        choices=sorted(TASK_SCENARIOS),
        help="Build a template flow for this scenario from library scenes (see --scene)",
    )
    parser.add_argument(
        "--scene",
        action="append",
        default=[],
        metavar="ROLE=SCENE_ID",
        help="Scene for one scenario role, e.g. before=thessaly_2023_s1.before (repeat per role)",
    )
    parser.add_argument("--plan", nargs="+", help="Tool slugs for a template flow (default per input kind)")
    parser.add_argument("--terraflow-url", default=DEFAULT_TERRAFLOW_URL)
    parser.add_argument("--runtime-dir", type=Path, default=Path(DEFAULT_RUNTIME_DIR))
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Template flows: add the gate self-check branch on the mismatch raster",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the spec without creating the flow")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runner = load_runner()
    runtime_dir = args.runtime_dir

    scene_ids = None
    task = "water_change"
    if args.run_id:
        summary_path = runtime_dir / "demo-runs" / f"terraflow_water_demo_{args.run_id}.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        task = summary.get("scenario") or "water_change"
        fixtures = summary["fixtures"]
        directory = Path(fixtures.get("scene") or fixtures["before"]).parent
        meta = runner.load_fixture_meta(directory)
        plan = summary["plan"]["execution_order"]
        scene_ids = dict(meta.get("scene_ids") or {}) or None
        set_id = None if scene_ids else input_set_id(directory, runtime_dir)
        # Mirror the run: only runs that did the gate self-check get the refusal branch.
        self_check = (summary.get("rejection_chain") or {}).get("status") == "deterministic_refusal"
        if scene_ids and self_check and (meta.get("self_check_sample") or {}).get("scene_id"):
            scene_ids["mismatch"] = meta["self_check_sample"]["scene_id"]
        flow_key = args.run_id
        description = f"Generated from workspace run {args.run_id} (plan validated by the runner)."
    elif args.scenario:
        # Template from library scenes: roles come from the command line, kinds from the scene index.
        task = args.scenario
        index = json.loads((runtime_dir / "scene-index.json").read_text(encoding="utf-8"))
        scene_ids = dict(item.split("=", 1) for item in args.scene)
        roles = [item["role"] for item in TASK_SCENARIOS[task]["inputs"]]
        missing = [role for role in roles if scene_ids.get(role) not in index]
        if missing:
            msg = f"Missing or unknown scenes for roles: {', '.join(missing)}"
            raise SystemExit(msg)
        first = index[scene_ids[roles[0]]]
        meta = {
            "input_kind": first["input_kind"],
            "input_scale": first.get("input_scale") or "linear",
            "title": first.get("collection_title"),
        }
        plan = args.plan or (DEFAULT_EXTENT_PLANS if task == "water_extent" else DEFAULT_PLANS)[meta["input_kind"]]
        set_id, self_check, directory = None, False, runtime_dir
        flow_key = "template_" + "_".join(scene_ids[role] for role in roles)
        description = f"Template {task} workflow for scenes " + ", ".join(f"{r}={scene_ids[r]}" for r in roles) + "."
    else:
        set_id = args.input_set
        directory = runtime_dir / "demo-fixtures" if set_id == "demo-fixtures" else None
        if directory is None:
            directory = (
                runtime_dir / "web-uploads" / set_id.removeprefix("upload-")
                if set_id.startswith("upload-")
                else runtime_dir / "demo-fixtures" / set_id
            )
        meta = runner.load_fixture_meta(directory)
        plan = args.plan or DEFAULT_PLANS[meta["input_kind"]]
        self_check = args.self_check
        flow_key = f"template_{set_id}"
        description = f"Template water workflow for input set {set_id}."

    output_dir = runtime_dir / "canvas-runs" / flow_key
    output_dir.mkdir(parents=True, exist_ok=True)
    name = f"{TASK_SCENARIOS[task]['title']} · {meta.get('title') or meta.get('label') or set_id} · {flow_key}"
    if task == "water_extent":
        spec = build_extent_spec(
            name=name,
            description=description,
            scene_id=(scene_ids or {})["scene"],
            plan=plan,
            input_kind=meta["input_kind"],
            input_scale=meta.get("input_scale", "linear"),
            scenarios=runner.SCENARIOS,
            output_dir=output_dir,
        )
    else:
        spec = build_spec(
            name=name,
            description=description,
            set_id=set_id,
            plan=plan,
            input_kind=meta["input_kind"],
            input_scale=meta.get("input_scale", "linear"),
            has_mismatch=self_check and (directory / "mismatch.tif").is_file(),
            scenarios=runner.SCENARIOS,
            output_dir=output_dir,
            scene_ids=scene_ids,
        )
    if args.dry_run:
        print(spec)
        return 0

    base_url = args.terraflow_url.rstrip("/")
    token = access_token(base_url)
    built = build_flow_from_spec(spec, registry=live_registry(base_url, token))
    if "error" in built:
        print(json.dumps({"status": "error", **built}, ensure_ascii=False), file=sys.stderr)
        return 2
    flow = built["flow"]
    created = http_json(
        f"{base_url}/api/v1/flows/",
        token=token,
        # Workspace flows are found by this tag rather than by name.
        payload={
            "name": flow["name"],
            "description": flow["description"],
            "data": flow["data"],
            "tags": ["agent-workspace", task],
        },
    )
    print(
        json.dumps(
            {
                "status": "created",
                "flow_id": created["id"],
                "name": created["name"],
                "canvas_path": f"/flow/{created['id']}",
                "node_count": built["node_count"],
                "edge_count": built["edge_count"],
                "input_set": set_id,
                "scenario": task,
                "scenes": scene_ids,
                "plan": plan,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

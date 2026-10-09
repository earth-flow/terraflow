#!/usr/bin/env python3
"""Fetch a small aligned Sentinel-1 RTC before/after pair from Planetary Computer.

Both scenes are read through windowed COG requests and resampled onto one
snapped grid in the before scene's projected CRS, so only the AOI is
downloaded. Pixels that are invalid in either scene are set to nodata in
both, giving the pair an identical valid support. A third raster cropped from
the after scene keeps the shape-mismatch refusal path on real pixels.

Requires rasterio (for example the Terrabox geo environment). Output is
runtime data and must not be committed.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
SAS_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-1-rtc"
COLLECTION = "sentinel-1-rtc"
NODATA = -32768.0
MIN_VALID_FRACTION = 0.95
MISMATCH_COLUMNS_REMOVED = 50


def fetch_json(url: str) -> Any:
    with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=120) as response:
        return json.load(response)


def scene_record(item: dict[str, Any], polarization: str) -> dict[str, Any]:
    props = item["properties"]
    return {
        "item_id": item["id"],
        "datetime": props.get("datetime"),
        "platform": props.get("platform"),
        "relative_orbit": props.get("sat:relative_orbit"),
        "orbit_state": props.get("sat:orbit_state"),
        "polarization": polarization.upper(),
        "asset_href": item["assets"][polarization]["href"].split("?")[0],
        "stac_item_url": f"{STAC_URL}/collections/{COLLECTION}/items/{item['id']}",
    }


def snapped_grid(bbox: list[float], crs, resolution: float):
    from rasterio.transform import from_origin
    from rasterio.warp import transform_bounds

    left, bottom, right, top = transform_bounds("EPSG:4326", crs, *bbox, densify_pts=21)
    left = math.floor(left / resolution) * resolution
    top = math.ceil(top / resolution) * resolution
    width = int(math.ceil((right - left) / resolution))
    height = int(math.ceil((top - bottom) / resolution))
    return from_origin(left, top, resolution, resolution), width, height


def read_on_grid(href: str, token: str, crs, transform, width: int, height: int):
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    with rasterio.open(f"{href}?{token}") as src:
        with WarpedVRT(
            src,
            crs=crs,
            transform=transform,
            width=width,
            height=height,
            resampling=Resampling.bilinear,
            src_nodata=src.nodata if src.nodata is not None else NODATA,
            nodata=NODATA,
        ) as vrt:
            data = vrt.read(1).astype("float32")
    valid = np.isfinite(data) & (data != NODATA) & (data > 0)
    return data, valid


def write_raster(path: Path, data, crs, transform) -> None:
    import rasterio

    profile = {
        "driver": "GTiff",
        "height": data.shape[0],
        "width": data.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": crs,
        "transform": transform,
        "nodata": NODATA,
        "compress": "deflate",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-item", required=True)
    parser.add_argument("--after-item", required=True)
    parser.add_argument("--bbox", required=True, nargs=4, type=float, metavar=("W", "S", "E", "N"))
    parser.add_argument("--polarization", default="vv", choices=["vv", "vh"])
    parser.add_argument("--resolution", type=float, default=10.0)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--label", required=True, help="Short label shown in the web fixture list")
    parser.add_argument("--event", required=True, help="Event description recorded in fixture.json")
    parser.add_argument("--title", help="User-facing title, e.g. place and event")
    parser.add_argument("--place", help="User-facing place description")
    return parser


def main(argv: list[str] | None = None) -> int:
    import numpy as np
    import rasterio

    args = build_parser().parse_args(argv)
    before_item = fetch_json(f"{STAC_URL}/collections/{COLLECTION}/items/{args.before_item}")
    after_item = fetch_json(f"{STAC_URL}/collections/{COLLECTION}/items/{args.after_item}")
    before_scene = scene_record(before_item, args.polarization)
    after_scene = scene_record(after_item, args.polarization)
    for field in ("relative_orbit", "orbit_state"):
        if before_scene[field] != after_scene[field]:
            print(f"refused: before/after {field} differ ({before_scene[field]} vs {after_scene[field]})", file=sys.stderr)
            return 2
    if not before_scene["datetime"] < after_scene["datetime"]:
        print("refused: before scene is not earlier than after scene", file=sys.stderr)
        return 2

    token = fetch_json(SAS_URL)["token"]
    with rasterio.open(f"{before_scene['asset_href']}?{token}") as src:
        crs = src.crs
    if crs is None or not crs.is_projected:
        print(f"refused: before scene CRS is not projected ({crs})", file=sys.stderr)
        return 2
    transform, width, height = snapped_grid(args.bbox, crs, args.resolution)

    before, before_valid = read_on_grid(before_scene["asset_href"], token, crs, transform, width, height)
    after, after_valid = read_on_grid(after_scene["asset_href"], token, crs, transform, width, height)
    common = before_valid & after_valid
    fraction = float(np.count_nonzero(common)) / common.size
    if fraction < MIN_VALID_FRACTION:
        print(f"refused: only {fraction:.1%} of the AOI is valid in both scenes", file=sys.stderr)
        return 2
    before[~common] = NODATA
    after[~common] = NODATA

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_raster(args.out_dir / "before.tif", before, crs, transform)
    write_raster(args.out_dir / "after.tif", after, crs, transform)
    write_raster(args.out_dir / "mismatch.tif", after[:, : width - MISMATCH_COLUMNS_REMOVED], crs, transform)

    metadata = {
        "schema_version": "terraflow-water-fixture.v1",
        "type": "sentinel1_rtc_gamma0",
        "input_kind": "sar_backscatter",
        "category": "observed",
        "label": args.label,
        "title": args.title or args.label,
        "place": args.place,
        "sensor": f"Sentinel-1 SAR（{args.polarization.upper()}）",
        "description": f"{before_scene['datetime'][:10]} / {after_scene['datetime'][:10]} Sentinel-1 RTC {args.polarization.upper()}，同轨道 {before_scene['relative_orbit']}（{before_scene['orbit_state']}）",
        "input_scale": "linear",
        "event": args.event,
        "source": "Microsoft Planetary Computer, collection sentinel-1-rtc",
        "license": "CC-BY-4.0 (collection metadata); contains modified Copernicus Sentinel data",
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "aoi_bbox_wgs84": args.bbox,
        "grid": {
            "crs": crs.to_string(),
            "transform": list(transform)[:6],
            "shape": [height, width],
            "resolution_m": args.resolution,
            "resampling": "bilinear onto a grid snapped to the resolution in the before scene CRS",
        },
        "common_valid": {
            "valid_pixels": int(np.count_nonzero(common)),
            "excluded_pixels": int(common.size - np.count_nonzero(common)),
            "rule": "invalid in either scene (nodata, non-finite or <= 0) is nodata in both",
        },
        "scenes": {"before": before_scene, "after": after_scene},
        "mismatch": {
            "derived_from": "after.tif",
            "columns_removed": MISMATCH_COLUMNS_REMOVED,
            "purpose": "real-pixel shape mismatch for the refusal chain",
        },
        "claim_boundary": "Backscatter threshold masks are estimates, not reference water maps; differences between dates do not by themselves establish flood causation.",
    }
    (args.out_dir / "fixture.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "out_dir": str(args.out_dir), "shape": [height, width], "valid_fraction": fraction}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

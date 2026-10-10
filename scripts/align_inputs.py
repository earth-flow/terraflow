#!/usr/bin/env python3
"""Put catalog data onto the grid a run needs (Terrabox geo environment).

Each command writes one single-band GeoTIFF and prints a JSON record of what
was done, which the workspace stores in the run's fixture.json as lineage:

  band      SRC TARGET --band N [--scale S --offset O]
            one band as float32; with scale/offset the stored values become
            physical values (e.g. Sentinel-2 L2A DN -> reflectance); source
            nodata becomes NaN
  warp      SRC TEMPLATE TARGET --resampling nearest|bilinear [--band N]
            reproject onto the template's CRS, transform and shape; nearest for
            class codes, bilinear for continuous values; source nodata kept
  rasterize VECTOR TEMPLATE TARGET
            0/1 uint8 on the template grid: 1 where a pixel centre falls inside
            a polygon (rasterio all_touched=False), 0 elsewhere

Nothing is modified in place.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject


def _write(target: Path, data, profile: dict) -> None:
    profile = {**profile, "count": 1, "compress": "deflate", "tiled": True, "blockxsize": 256, "blockysize": 256}
    if profile["width"] < 256 or profile["height"] < 256:
        profile.update(tiled=False)
        profile.pop("blockxsize"), profile.pop("blockysize")
    temporary = target.with_suffix(".tmp.tif")
    with rasterio.open(temporary, "w", **profile) as dst:
        dst.write(data, 1)
    temporary.replace(target)


def band(args) -> dict:
    with rasterio.open(args.src) as src:
        data = src.read(args.band, masked=True).astype("float64")
        profile = src.profile.copy()
        description = src.descriptions[args.band - 1]
    values = data.filled(np.nan)
    if args.scale is not None:
        values = values * args.scale + (args.offset or 0.0)
    profile.update(dtype="float32", nodata=float("nan"))
    _write(args.target, values.astype("float32"), profile)
    return {
        "operation": "band",
        "source": str(args.src),
        "band": args.band,
        "band_description": description,
        "scale": args.scale,
        "offset": args.offset,
        "output_nodata": "nan",
        "resampling": None,
    }


def warp(args) -> dict:
    with rasterio.open(args.template) as tpl:
        dst_crs, dst_transform, shape = tpl.crs, tpl.transform, (tpl.height, tpl.width)
    with rasterio.open(args.src) as src:
        nodata = src.nodata
        dtype = src.dtypes[args.band - 1] if args.resampling == "nearest" else "float32"
        fill = nodata if nodata is not None else (0 if args.resampling == "nearest" else np.nan)
        destination = np.full(shape, fill, dtype=dtype)
        reproject(
            source=rasterio.band(src, args.band),
            destination=destination,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=nodata,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            dst_nodata=fill,
            resampling=getattr(Resampling, args.resampling),
        )
        profile = src.profile.copy()
        source_crs = src.crs.to_string() if src.crs else None
        source_res = list(src.res)
    profile.update(crs=dst_crs, transform=dst_transform, width=shape[1], height=shape[0], dtype=dtype,
                   nodata=None if fill is None or (isinstance(fill, float) and np.isnan(fill) and dtype != "float32") else fill)
    if dtype == "float32":
        profile["nodata"] = float("nan") if nodata is None else nodata
    _write(args.target, destination, profile)
    return {
        "operation": "warp",
        "source": str(args.src),
        "source_crs": source_crs,
        "source_resolution": source_res,
        "target_crs": dst_crs.to_string(),
        "target_resolution": [abs(dst_transform.a), abs(dst_transform.e)],
        "target_shape": list(shape),
        "resampling": args.resampling,
        "band": args.band,
    }


def rasterize_vector(args) -> dict:
    import pyogrio
    from rasterio.features import rasterize

    with rasterio.open(args.template) as tpl:
        crs, transform, shape = tpl.crs, tpl.transform, (tpl.height, tpl.width)
        profile = tpl.profile.copy()
    frame = pyogrio.read_dataframe(args.vector)
    source_crs = frame.crs.to_string() if frame.crs is not None else None
    frame = frame[~frame.geometry.is_empty & frame.geometry.notna()].to_crs(crs)
    geometries = [(geometry, 1) for geometry in frame.geometry]
    data = (
        rasterize(geometries, out_shape=shape, transform=transform, fill=0, all_touched=False, dtype="uint8")
        if geometries else np.zeros(shape, dtype="uint8")
    )
    profile.update(dtype="uint8", nodata=255)
    _write(args.target, data, profile)
    return {
        "operation": "rasterize",
        "source": str(args.vector),
        "source_crs": source_crs,
        "features": int(len(frame)),
        "target_crs": crs.to_string(),
        "target_shape": list(shape),
        "rule": "pixel centre inside polygon (all_touched=False); outside polygons = 0",
        "reference_pixels": int(np.count_nonzero(data == 1)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("band")
    p.add_argument("src", type=Path)
    p.add_argument("target", type=Path)
    p.add_argument("--band", type=int, default=1)
    p.add_argument("--scale", type=float)
    p.add_argument("--offset", type=float)
    p = sub.add_parser("warp")
    p.add_argument("src", type=Path)
    p.add_argument("template", type=Path)
    p.add_argument("target", type=Path)
    p.add_argument("--resampling", choices=["nearest", "bilinear"], required=True)
    p.add_argument("--band", type=int, default=1)
    p = sub.add_parser("rasterize")
    p.add_argument("vector", type=Path)
    p.add_argument("template", type=Path)
    p.add_argument("target", type=Path)
    args = parser.parse_args()
    handler = {"band": band, "warp": warp, "rasterize": rasterize_vector}[args.command]
    print(json.dumps(handler(args), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

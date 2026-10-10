#!/usr/bin/env python3
"""Read what an uploaded geodata file says about itself, for the workspace data catalog.

Usage (Terrabox geo environment):
  inspect_geodata.py inspect <path> [<path> ...]   print {path: profile} as JSON
  inspect_geodata.py shp2gpkg <zip> <target.gpkg>  convert a zipped Shapefile to GeoPackage

Raster profiles give the grid, the bands and value statistics read from a
decimated copy (at most ~1000 px on the long side): valid fraction, 2/98
percentiles, minimum/maximum, the share of negative values, and for integer
rasters the distinct values (up to 256; from the full raster up to 40 Mpx). Vector profiles give the driver,
layers, CRS, geometry types, feature count, columns, bounds and the number
of invalid geometries. Bounds in WGS 84 are computed with densified edges.
Nothing is modified except the GeoPackage written by ``shp2gpkg``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

SAMPLE_SIZE = 1000
MAX_DISTINCT = 256
FULL_DISTINCT_PIXELS = 40_000_000
VECTOR_SUFFIXES = {".geojson", ".json", ".gpkg", ".zip"}


def crs_info(crs) -> dict:
    if crs is None:
        return {"crs": None, "crs_wkt": None, "projected": False, "projection_method": None, "linear_unit": None}
    from pyproj import CRS

    parsed = CRS.from_user_input(crs)
    authority = parsed.to_authority(min_confidence=70)
    operation = parsed.coordinate_operation
    unit = None
    if parsed.axis_info:
        unit = parsed.axis_info[0].unit_name
    return {
        "crs": ":".join(authority) if authority else None,
        "crs_wkt": parsed.to_wkt(),
        "projected": parsed.is_projected,
        "projection_method": operation.method_name if operation is not None else None,
        "linear_unit": unit,
    }


def wgs84_bounds(crs, bounds) -> list[float] | None:
    if crs is None or bounds is None:
        return None
    from rasterio.warp import transform_bounds

    west, south, east, north = transform_bounds(crs, "EPSG:4326", *bounds, densify_pts=21)
    return [round(value, 6) for value in (west, south, east, north)]


def raster_profile(path: Path) -> dict:
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling

    with rasterio.open(path) as src:
        scale = min(1.0, SAMPLE_SIZE / max(src.width, src.height))
        shape = (max(1, round(src.height * scale)), max(1, round(src.width * scale)))
        integer = all(np.issubdtype(np.dtype(dtype), np.integer) for dtype in src.dtypes)
        resampling = Resampling.nearest if integer else Resampling.average
        bands = []
        for index in range(1, src.count + 1):
            data = src.read(index, out_shape=shape, resampling=resampling, masked=True)
            values = np.ma.compressed(data).astype("float64")
            values = values[np.isfinite(values)]
            stats: dict = {"valid_fraction": round(values.size / data.size, 4) if data.size else 0.0}
            if values.size:
                low, high = np.percentile(values, (2, 98))
                stats.update(
                    minimum=float(values.min()),
                    maximum=float(values.max()),
                    p2=float(low),
                    p98=float(high),
                    negative_fraction=round(float((values < 0).mean()), 4),
                )
                if integer:
                    # Distinct values from the full raster when it is small enough, so a rare
                    # undeclared class value is not missed by the decimated sample.
                    full = src.width * src.height <= FULL_DISTINCT_PIXELS
                    source = np.ma.compressed(src.read(index, masked=True)) if full else values
                    distinct = np.unique(source)
                    stats["distinct_full_resolution"] = full
                    stats["distinct"] = [int(v) for v in distinct[:MAX_DISTINCT]]
                    stats["distinct_truncated"] = bool(distinct.size > MAX_DISTINCT)
            bands.append(
                {
                    "index": index,
                    "description": src.descriptions[index - 1],
                    "dtype": src.dtypes[index - 1],
                    "nodata": src.nodatavals[index - 1],
                    "statistics": stats,
                }
            )
        crs = crs_info(src.crs)
        return {
            "ok": True,
            "kind": "raster",
            "driver": src.driver,
            **crs,
            "shape": [src.height, src.width],
            "transform": [round(v, 10) for v in list(src.transform)[:6]],
            "resolution": [abs(src.res[0]), abs(src.res[1])],
            "bounds": list(src.bounds),
            "bbox_wgs84": wgs84_bounds(src.crs, src.bounds),
            "count": src.count,
            "integer": integer,
            "bands": bands,
            "sampled_shape": list(shape),
        }


def _vector_source(path: Path, workdir: Path) -> tuple[Path, dict]:
    """The path pyogrio should open, plus notes about a zipped Shapefile's members."""
    if path.suffix.lower() != ".zip":
        return path, {}
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if not n.endswith("/") and "__MACOSX" not in n]
        shps = [n for n in names if n.lower().endswith(".shp")]
        if len(shps) != 1:
            raise ValueError(f"zip 中应当正好有一个 .shp 文件，实际 {len(shps)} 个")
        stem = shps[0][:-4]
        members = {Path(n).suffix.lower() for n in names if n[: len(stem)] == stem}
        missing = [ext for ext in (".shp", ".shx", ".dbf", ".prj") if ext not in members]
        for name in names:
            target = (workdir / name).resolve()
            if workdir.resolve() not in target.parents:
                raise ValueError("zip 中有不安全的路径")
        archive.extractall(workdir, members=names)
    return workdir / shps[0], {"shapefile_members": sorted(members), "shapefile_missing": missing}


def vector_profile(path: Path) -> dict:
    import pyogrio

    with tempfile.TemporaryDirectory() as tmp:
        source, notes = _vector_source(path, Path(tmp))
        if notes.get("shapefile_missing"):
            return {"ok": True, "kind": "vector", **notes, "crs": None, "layers": []}
        layers = [str(row[0]) for row in pyogrio.list_layers(source)]
        layer = layers[0] if layers else None
        info = pyogrio.read_info(source, layer=layer)
        frame = pyogrio.read_dataframe(source, layer=layer)
        geometry = frame.geometry
        invalid = int((~geometry.is_valid & ~geometry.is_empty).sum())
        types = sorted({str(t) for t in geometry.geom_type.dropna().unique()})
        bounds = list(geometry.total_bounds) if len(frame) else None
        crs = crs_info(frame.crs)
        columns = [
            {"name": str(name), "type": str(dtype)}
            for name, dtype in zip(info["fields"], info["dtypes"])
        ]
        return {
            "ok": True,
            "kind": "vector",
            **notes,
            "driver": info.get("driver"),
            "layers": layers,
            "layer": layer,
            **crs,
            "geometry_types": types,
            "feature_count": int(len(frame)),
            "invalid_geometries": invalid,
            "empty_geometries": int(geometry.is_empty.sum()),
            "columns": columns,
            "bounds": bounds,
            "bbox_wgs84": wgs84_bounds(frame.crs, bounds) if bounds is not None else None,
        }


def inspect(path: Path) -> dict:
    try:
        if path.suffix.lower() in VECTOR_SUFFIXES:
            return vector_profile(path)
        return raster_profile(path)
    except Exception as exc:  # report, do not crash: one bad file must not hide the others
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:400]}


def shp2gpkg(archive: Path, target: Path) -> dict:
    import pyogrio

    with tempfile.TemporaryDirectory() as tmp:
        source, notes = _vector_source(archive, Path(tmp))
        if notes.get("shapefile_missing"):
            return {"ok": False, "error": "缺少 " + "、".join(notes["shapefile_missing"])}
        frame = pyogrio.read_dataframe(source)
        temporary = target.with_suffix(".tmp.gpkg")
        temporary.unlink(missing_ok=True)
        pyogrio.write_dataframe(frame, temporary, driver="GPKG", layer=Path(source).stem)
        temporary.replace(target)
    return {"ok": True, "layer": Path(source).stem, "features": int(len(frame))}


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "inspect":
        print(json.dumps({p: inspect(Path(p)) for p in argv[1:]}, ensure_ascii=False))
        return 0
    if len(argv) == 3 and argv[0] == "shp2gpkg":
        try:
            result = shp2gpkg(Path(argv[1]), Path(argv[2]))
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:400]}
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("ok") else 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

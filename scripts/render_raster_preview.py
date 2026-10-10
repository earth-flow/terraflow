#!/usr/bin/env python3
"""Render a GeoTIFF as a small PNG preview for the web workspace.

Modes:
  sar        backscatter shown in dB, stretched from -25 to 0 dB (``--scale``
             says whether the file holds linear values or dB; ``--band``
             picks the polarization band)
  labels     0/1 water labels: non-water grey, water blue, nodata transparent
  classes    integer classes coloured by ``--palette`` (JSON {value: "rrggbb"});
             values without a colour are transparent
  continuous 2-98 percentile grey stretch of ``--band``
  rgb        true colour from ``--bands r,g,b``, each band stretched 2-98 %
  dem        hillshade (sun from the north-west) over an elevation colour ramp

Invalid pixels (nodata, non-finite, and values <= 0 for linear SAR) are
transparent. Tiny rasters are enlarged with nearest-neighbour so they stay
visible. Requires rasterio, numpy and Pillow (the Terrabox geo environment).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling

SAR_DB_RANGE = (-25.0, 0.0)
LABEL_COLORS = {0: (72, 77, 87, 255), 1: (37, 112, 235, 255)}


def read_preview(path: Path, max_size: int, mode: str, band: int = 1):
    with rasterio.open(path) as src:
        scale = min(1.0, max_size / max(src.width, src.height))
        shape = (max(1, round(src.height * scale)), max(1, round(src.width * scale)))
        resampling = Resampling.nearest if mode in ("labels", "classes") else Resampling.average
        data_band = src.read(band, out_shape=shape, resampling=resampling, masked=True)
    data = np.ma.filled(data_band.astype("float64"), np.nan)
    valid = ~np.ma.getmaskarray(data_band) & np.isfinite(data)
    return data, valid


def stretch(values, valid):
    low, high = (np.percentile(values[valid], (2, 98)) if valid.any() else (0.0, 1.0))
    if high <= low:
        high = low + 1.0
    return np.nan_to_num(np.clip((values - low) / (high - low), 0, 1) * 255).astype(np.uint8)


def render_rgb(path: Path, max_size: int, bands: list[int]) -> np.ndarray:
    layers, masks = [], []
    for band in bands:
        data, valid = read_preview(path, max_size, "rgb", band)
        layers.append(data)
        masks.append(valid)
    valid = np.logical_and.reduce(masks)
    rgba = np.zeros((*layers[0].shape, 4), dtype=np.uint8)
    for channel, data in enumerate(layers):
        rgba[..., channel] = stretch(data, valid)
    rgba[..., 3] = np.where(valid, 255, 0).astype(np.uint8)
    return rgba


DEM_RAMP = [(0.0, (46, 120, 70)), (0.35, (170, 190, 110)), (0.65, (200, 160, 100)), (1.0, (245, 240, 235))]


def render_dem(data, valid) -> np.ndarray:
    filled = np.where(valid, data, np.nanmean(data[valid]) if valid.any() else 0.0)
    dy, dx = np.gradient(filled)
    slope = np.pi / 2 - np.arctan(np.hypot(dx, dy) / max(1e-6, np.nanstd(filled) / 50))
    aspect = np.arctan2(-dx, dy)
    azimuth, altitude = np.radians(315), np.radians(45)
    shade = np.sin(altitude) * np.sin(slope) + np.cos(altitude) * np.cos(slope) * np.cos(azimuth - aspect)
    shade = np.clip(shade, 0, 1)
    low, high = (np.percentile(data[valid], (2, 98)) if valid.any() else (0.0, 1.0))
    level = np.clip((filled - low) / max(1e-6, high - low), 0, 1)
    color = np.zeros((*data.shape, 3))
    for (x0, c0), (x1, c1) in zip(DEM_RAMP, DEM_RAMP[1:]):
        inside = (level >= x0) & (level <= x1)
        f = ((level - x0) / (x1 - x0))[..., None]
        color[inside] = (np.array(c0) * (1 - f) + np.array(c1) * f)[inside]
    rgba = np.zeros((*data.shape, 4), dtype=np.uint8)
    rgba[..., :3] = np.clip(color * (0.45 + 0.55 * shade[..., None]), 0, 255).astype(np.uint8)
    rgba[..., 3] = np.where(valid, 255, 0).astype(np.uint8)
    return rgba


def render_classes(data, valid, palette: dict[str, str]) -> np.ndarray:
    rgba = np.zeros((*data.shape, 4), dtype=np.uint8)
    for value, color in palette.items():
        rgb = tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))
        rgba[valid & (data == float(value))] = (*rgb, 255)
    return rgba


def render(data, valid, mode: str, scale: str) -> np.ndarray:
    rgba = np.zeros((*data.shape, 4), dtype=np.uint8)
    if mode == "labels":
        for value, color in LABEL_COLORS.items():
            rgba[valid & (data == value)] = color
        return rgba
    if mode == "sar":
        if scale == "linear":
            valid = valid & (data > 0)
            values = np.where(valid, 10.0 * np.log10(np.where(valid, data, 1.0)), np.nan)
        else:
            values = data
        low, high = SAR_DB_RANGE
    else:
        values = data
        low, high = (np.percentile(values[valid], (2, 98)) if valid.any() else (0.0, 1.0))
        if high <= low:
            high = low + 1.0
    gray = np.clip((values - low) / (high - low), 0, 1)
    level = np.nan_to_num(gray * 255).astype(np.uint8)
    rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = level
    rgba[..., 3] = np.where(valid, 255, 0).astype(np.uint8)
    return rgba


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--mode", choices=["sar", "labels", "continuous", "rgb", "dem", "classes"], required=True)
    parser.add_argument("--scale", choices=["linear", "db"], default="linear")
    parser.add_argument("--band", type=int, default=1)
    parser.add_argument("--bands", default="1,2,3", help="r,g,b band numbers for --mode rgb")
    parser.add_argument("--palette", default="{}", help='JSON {"value": "rrggbb"} for --mode classes')
    parser.add_argument("--max-size", type=int, default=512)
    parser.add_argument("--min-size", type=int, default=160)
    args = parser.parse_args()

    if args.mode == "rgb":
        rgba = render_rgb(args.input, args.max_size, [int(b) for b in args.bands.split(",")])
    else:
        data, valid = read_preview(args.input, args.max_size, args.mode, args.band)
        if args.mode == "dem":
            rgba = render_dem(data, valid)
        elif args.mode == "classes":
            rgba = render_classes(data, valid, json.loads(args.palette))
        else:
            rgba = render(data, valid, args.mode, args.scale)
    image = Image.fromarray(rgba, mode="RGBA")
    if max(image.size) < args.min_size:
        factor = max(1, args.min_size // max(image.size))
        image = image.resize((image.width * factor, image.height * factor), Image.NEAREST)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp.png")
    image.save(temporary, format="PNG", optimize=True)
    temporary.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

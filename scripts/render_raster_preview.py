#!/usr/bin/env python3
"""Render a single-band GeoTIFF as a small PNG preview for the web workspace.

Modes:
  sar        backscatter shown in dB, stretched from -25 to 0 dB (``--scale``
             says whether the file holds linear values or dB)
  labels     0/1 water labels: non-water grey, water blue, nodata transparent
  continuous 2-98 percentile grey stretch

Invalid pixels (nodata, non-finite, and values <= 0 for linear SAR) are
transparent. Tiny rasters are enlarged with nearest-neighbour so they stay
visible. Requires rasterio, numpy and Pillow (the Terrabox geo environment).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling

SAR_DB_RANGE = (-25.0, 0.0)
LABEL_COLORS = {0: (72, 77, 87, 255), 1: (37, 112, 235, 255)}


def read_preview(path: Path, max_size: int, mode: str):
    with rasterio.open(path) as src:
        scale = min(1.0, max_size / max(src.width, src.height))
        shape = (max(1, round(src.height * scale)), max(1, round(src.width * scale)))
        resampling = Resampling.nearest if mode == "labels" else Resampling.average
        band = src.read(1, out_shape=shape, resampling=resampling, masked=True)
    data = np.ma.filled(band.astype("float64"), np.nan)
    valid = ~np.ma.getmaskarray(band) & np.isfinite(data)
    return data, valid


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
    parser.add_argument("--mode", choices=["sar", "labels", "continuous"], required=True)
    parser.add_argument("--scale", choices=["linear", "db"], default="linear")
    parser.add_argument("--max-size", type=int, default=512)
    parser.add_argument("--min-size", type=int, default=160)
    args = parser.parse_args()

    data, valid = read_preview(args.input, args.max_size, args.mode)
    image = Image.fromarray(render(data, valid, args.mode, args.scale), mode="RGBA")
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

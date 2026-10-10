#!/usr/bin/env python3
"""Render a vector file (GeoJSON, GeoPackage) as a small PNG preview for the web workspace.

Features are drawn in WGS 84 with longitude scaled by cos(latitude) so shapes
are not stretched: polygons filled translucent orange with a darker outline,
lines and points in the same colour, on a transparent background. Files with
more than ``--max-features`` features are drawn as their bounding boxes only.
Requires pyogrio, shapely and Pillow (the Terrabox geo environment).
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import pyogrio
from PIL import Image, ImageDraw

FILL = (234, 120, 40, 90)
LINE = (190, 80, 20, 255)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--size", type=int, default=512)
    parser.add_argument("--max-features", type=int, default=20000)
    args = parser.parse_args()

    frame = pyogrio.read_dataframe(args.input)
    if frame.crs is not None and frame.crs.to_epsg() != 4326:
        frame = frame.to_crs("EPSG:4326")
    geometries = [g for g in frame.geometry if g is not None and not g.is_empty]
    if len(geometries) > args.max_features:
        geometries = [g.envelope for g in geometries]
    west, south, east, north = frame.total_bounds
    kx = math.cos(math.radians((south + north) / 2))
    width_deg, height_deg = max((east - west) * kx, 1e-9), max(north - south, 1e-9)
    pad = 8
    scale = (args.size - 2 * pad) / max(width_deg, height_deg)
    width = int(width_deg * scale) + 2 * pad
    height = int(height_deg * scale) + 2 * pad

    def xy(coords):
        return [(pad + (x - west) * kx * scale, pad + (north - y) * scale) for x, y, *_ in coords]

    image = Image.new("RGBA", (max(width, 32), max(height, 32)), (0, 0, 0, 0))
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    def draw_geometry(geometry):
        kind = geometry.geom_type
        if kind.startswith("Multi") or kind == "GeometryCollection":
            for part in geometry.geoms:
                draw_geometry(part)
        elif kind == "Polygon":
            draw.polygon(xy(geometry.exterior.coords), fill=FILL, outline=LINE)
            for ring in geometry.interiors:
                draw.polygon(xy(ring.coords), fill=(0, 0, 0, 0), outline=LINE)
        elif kind in ("LineString", "LinearRing"):
            draw.line(xy(geometry.coords), fill=LINE, width=2)
        elif kind == "Point":
            (x, y), = xy(geometry.coords)
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=LINE)

    for geometry in geometries:
        draw_geometry(geometry)
    image = Image.alpha_composite(image, overlay)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp.png")
    image.save(temporary, format="PNG", optimize=True)
    temporary.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

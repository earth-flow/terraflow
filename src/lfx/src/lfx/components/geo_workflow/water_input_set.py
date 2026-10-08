"""Choose a paired before/after raster set for the water workflow.

Sets live under ``$TERRAFLOW_WATER_RUNTIME_DIR``: the synthetic fixture in
``demo-fixtures/``, observed scenes in ``demo-fixtures/<name>/`` and web
uploads in ``web-uploads/<id>/``. Each set needs ``before.tif`` and
``after.tif``; ``mismatch.tif`` is optional and ``fixture.json`` describes the
input kind and provenance.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DropdownInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message

RUNTIME_DIR_ENV = "TERRAFLOW_WATER_RUNTIME_DIR"
SYNTHETIC_SET_ID = "demo-fixtures"


def _runtime_dir() -> Path | None:
    value = os.getenv(RUNTIME_DIR_ENV, "").strip()
    return Path(value) if value else None


def list_input_sets() -> dict[str, Path]:
    """Return {set_id: directory} for every directory holding a before/after pair."""
    root = _runtime_dir()
    if root is None:
        return {}
    fixtures = root / "demo-fixtures"
    uploads = root / "web-uploads"
    candidates: list[tuple[str, Path]] = [(SYNTHETIC_SET_ID, fixtures)]
    if fixtures.is_dir():
        candidates += [(path.name, path) for path in sorted(fixtures.iterdir()) if path.is_dir()]
    if uploads.is_dir():
        candidates += [
            (f"upload-{path.name}", path)
            for path in sorted(uploads.iterdir(), reverse=True)
            if (path / "fixture.json").is_file()
        ]
    return {
        set_id: path
        for set_id, path in candidates
        if (path / "before.tif").is_file() and (path / "after.tif").is_file()
    }


def read_set_metadata(path: Path) -> dict[str, Any]:
    meta_path = path / "fixture.json"
    if not meta_path.is_file():
        return {"type": "synthetic_engineering_fixture", "input_kind": "water_labels"}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if meta.get("type") == "sentinel1_rtc_gamma0":
        meta.setdefault("input_kind", "sar_backscatter")
    return meta if isinstance(meta, dict) else {}


class WaterInputSetComponent(Component):
    display_name = "Water Input Set"
    description = "Select a paired before/after raster set (synthetic, observed or uploaded) for water analysis."
    icon = "Layers"
    name = "WaterInputSet"

    inputs = [
        DropdownInput(
            name="input_set",
            display_name="Input Set",
            info=f"Sets found under ${RUNTIME_DIR_ENV}. Use refresh after a new upload.",
            options=list(list_input_sets()),
            value=SYNTHETIC_SET_ID,
            refresh_button=True,
            real_time_refresh=True,
        ),
    ]

    outputs = [
        Output(display_name="Before Path", name="before_path", method="before_path_message", group_outputs=True),
        Output(display_name="After Path", name="after_path", method="after_path_message", group_outputs=True),
        Output(display_name="Mismatch Path", name="mismatch_path", method="mismatch_path_message", group_outputs=True),
        Output(display_name="Set Metadata", name="metadata", method="metadata_data", group_outputs=True),
    ]

    def update_build_config(self, build_config: dict, field_value: Any, field_name: str | None = None) -> dict:  # noqa: ARG002
        if field_name == "input_set":
            build_config["input_set"]["options"] = list(list_input_sets())
        return build_config

    def _set_dir(self) -> Path:
        sets = list_input_sets()
        if self.input_set not in sets:
            msg = f"Input set '{self.input_set}' not found under ${RUNTIME_DIR_ENV}"
            raise ValueError(msg)
        return sets[self.input_set]

    def before_path_message(self) -> Message:
        return Message(text=str(self._set_dir() / "before.tif"))

    def after_path_message(self) -> Message:
        return Message(text=str(self._set_dir() / "after.tif"))

    def mismatch_path_message(self) -> Message:
        path = self._set_dir() / "mismatch.tif"
        if not path.is_file():
            # No refusal sample in this set: skip the branch fed by this output.
            self.stop("mismatch_path")
            return Message(text="")
        return Message(text=str(path))

    def metadata_data(self) -> Data:
        path = self._set_dir()
        meta = read_set_metadata(path)
        data = {
            "input_set": self.input_set,
            "directory": str(path),
            "type": meta.get("type"),
            "label": meta.get("label"),
            "input_kind": meta.get("input_kind"),
            "input_scale": meta.get("input_scale"),
            "event": meta.get("event"),
            "scenes": meta.get("scenes"),
            "claim_boundary": meta.get("claim_boundary"),
        }
        self.status = data
        return Data(data=data)

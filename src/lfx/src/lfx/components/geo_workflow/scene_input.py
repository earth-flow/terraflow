"""Choose one scene from the workspace scene library.

The workspace lists every scene (observed, uploaded, synthetic and gate
self-check samples) in ``$TERRAFLOW_WATER_RUNTIME_DIR/scene-index.json``.
A flow uses one Scene Input per role, so a change analysis has a before and
an after scene and a single-scene analysis has one; the role is decided by
the flow, not stored with the data.
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
INDEX_NAME = "scene-index.json"


def read_scene_index() -> dict[str, dict[str, Any]]:
    """Return {scene_id: record} from the workspace index; empty when unavailable."""
    value = os.getenv(RUNTIME_DIR_ENV, "").strip()
    if not value:
        return {}
    try:
        index = json.loads((Path(value) / INDEX_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(key): record for key, record in index.items() if isinstance(record, dict) and record.get("path")}


class SceneInputComponent(Component):
    display_name = "Scene Input"
    description = "Select one raster scene from the workspace data library."
    icon = "Image"
    name = "SceneInput"

    inputs = [
        DropdownInput(
            name="scene_id",
            display_name="Scene",
            info=f"Scenes listed in ${RUNTIME_DIR_ENV}/{INDEX_NAME}. Use refresh after a new upload.",
            options=list(read_scene_index()),
            value="",
            refresh_button=True,
            real_time_refresh=True,
        ),
    ]

    outputs = [
        Output(display_name="Raster Path", name="path", method="path_message", group_outputs=True),
        Output(display_name="Scene Metadata", name="metadata", method="metadata_data", group_outputs=True),
    ]

    def update_build_config(self, build_config: dict, field_value: Any, field_name: str | None = None) -> dict:  # noqa: ARG002
        if field_name == "scene_id":
            build_config["scene_id"]["options"] = list(read_scene_index())
        return build_config

    def _record(self) -> dict[str, Any]:
        record = read_scene_index().get(self.scene_id)
        if record is None:
            msg = f"Scene '{self.scene_id}' is not in the workspace scene library"
            raise ValueError(msg)
        if not Path(record["path"]).is_file():
            msg = f"Raster for scene '{self.scene_id}' is missing: {record['path']}"
            raise ValueError(msg)
        return record

    def path_message(self) -> Message:
        return Message(text=str(self._record()["path"]))

    def metadata_data(self) -> Data:
        data = {"scene_id": self.scene_id, **self._record()}
        self.status = data
        return Data(data=data)

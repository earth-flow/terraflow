"""Gate paired raster analysis on the result of a grid/profile preflight.

Downstream pairwise tools only receive the before/after paths when the
preflight passed. A refusal stops both path branches and is reported on the
Decision output, so the canvas shows which check blocked the pair.
"""

from __future__ import annotations

from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


def preflight_outputs(value: Any) -> dict[str, Any]:
    """Extract the tool outputs from a Terrabox component result (Data or list of Data)."""
    if isinstance(value, list):
        value = value[0] if value else None
    payload = value.data if isinstance(value, Data) else value
    if not isinstance(payload, dict):
        return {}
    outputs = payload.get("outputs")
    return outputs if isinstance(outputs, dict) else payload


class RasterPairGateComponent(Component):
    display_name = "Raster Pair Gate"
    description = (
        "Pass before/after paths downstream only when Raster Profile Preflight reports no grid, "
        "shape, CRS, transform, bounds, resolution, nodata or valid-mask mismatch."
    )
    icon = "ShieldCheck"
    name = "RasterPairGate"

    inputs = [
        DataInput(
            name="preflight",
            display_name="Preflight Result",
            info="Output of the Terrabox Raster Profile Preflight component.",
            required=True,
        ),
        MessageTextInput(name="before_path", display_name="Before Path", required=True),
        MessageTextInput(name="after_path", display_name="After Path", required=True),
    ]

    outputs = [
        Output(display_name="Before Path", name="gated_before_path", method="gated_before", group_outputs=True),
        Output(display_name="After Path", name="gated_after_path", method="gated_after", group_outputs=True),
        Output(display_name="Decision", name="decision", method="decision_data", group_outputs=True),
    ]

    def _decision(self) -> dict[str, Any]:
        outputs = preflight_outputs(self.preflight)
        passed = bool(outputs) and outputs.get("execution_blocked") is False and outputs.get("success") is True
        return {
            "passed": passed,
            "reason_code": None if passed else (outputs.get("reason_code") or "preflight_unavailable"),
            "failed_checks": outputs.get("failed_checks") or ([] if passed else ["preflight_unavailable"]),
            "grid_id": outputs.get("grid_id"),
            "common_valid_pixels": outputs.get("common_valid_pixels"),
        }

    def _gated(self, output_name: str, path: str) -> Message:
        if not self._decision()["passed"]:
            self.stop(output_name)
            self.graph.exclude_branch_conditionally(self._id, output_name=output_name)
            return Message(text="")
        return Message(text=str(path))

    def gated_before(self) -> Message:
        return self._gated("gated_before_path", self.before_path)

    def gated_after(self) -> Message:
        return self._gated("gated_after_path", self.after_path)

    def decision_data(self) -> Data:
        decision = self._decision()
        self.status = decision
        return Data(data=decision)

"""Read one field from a Terrabox tool result and pass it on as text.

Terrabox components return the execute response (``success``, ``outputs``,
``metadata``...). Paths and numbers that a later tool needs, such as
``outputs.output_path`` or ``outputs.threshold``, are nested inside it.
"""

from __future__ import annotations

from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, Output
from lfx.schema.data import Data
from lfx.schema.message import Message


def read_field(value: Any, path: str) -> Any:
    """Follow a dotted path through dicts and lists (``outputs.before.resolution.0``)."""
    if isinstance(value, list):
        value = value[0] if value else None
    current: Any = value.data if isinstance(value, Data) else value
    for part in [segment for segment in path.split(".") if segment]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            msg = f"Field '{path}' not found (stopped at '{part}')"
            raise ValueError(msg)
    return current


class ToolResultFieldComponent(Component):
    display_name = "Tool Result Field"
    description = "Extract a field from a Terrabox tool result, e.g. outputs.output_path, as text for the next tool."
    icon = "Braces"
    name = "ToolResultField"

    inputs = [
        DataInput(name="result", display_name="Tool Result", required=True),
        MessageTextInput(
            name="field_path",
            display_name="Field Path",
            info="Dotted path into the result; list items by index.",
            value="outputs.output_path",
        ),
    ]

    outputs = [
        Output(display_name="Value", name="value", method="field_value"),
    ]

    def field_value(self) -> Message:
        value = read_field(self.result, self.field_path)
        text = value if isinstance(value, str) else str(value)
        self.status = text
        return Message(text=text)

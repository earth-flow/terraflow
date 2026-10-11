"""Event consumption utilities for streaming flow execution."""

import asyncio
import json
from collections.abc import AsyncGenerator, Callable, Coroutine
from typing import Any

from lfx.log.logger import logger


def parse_event_data(event_data: bytes) -> tuple[str | None, dict[str, Any]]:
    """Parse raw event bytes into event type and data."""
    event_str = event_data.decode("utf-8").strip()
    if not event_str:
        return None, {}

    event_json = json.loads(event_str)
    return event_json.get("event"), event_json.get("data", {})


def tool_steps(message: dict[str, Any], states: dict[tuple[str, int], bool]) -> list[dict[str, Any]]:
    """New or finished tool calls in an agent message's "Agent Steps" block.

    The agent re-sends the whole message on every tool start/end; ``states``
    remembers what was already reported so each call yields one start and
    one finish.
    """
    steps: list[dict[str, Any]] = []
    message_id = str(message.get("id") or "")
    for block in message.get("content_blocks") or []:
        for index, content in enumerate((block or {}).get("contents") or []):
            if not isinstance(content, dict) or content.get("type") != "tool_use":
                continue
            done = content.get("output") is not None or content.get("error") is not None
            key = (message_id, index)
            previous = states.get(key)
            if previous is True or previous == done:
                continue
            states[key] = done
            steps.append({"name": content.get("name") or "", "input": content.get("tool_input"), "done": done})
    return steps


async def consume_streaming_events(
    event_queue: asyncio.Queue[tuple[str, bytes, float] | None],
    is_disconnected: Callable[[], Coroutine[Any, Any, bool]] | None = None,
    cancel_event: asyncio.Event | None = None,
) -> AsyncGenerator[tuple[str, str], None]:
    """Consume events from queue and yield parsed token events.

    Args:
        event_queue: Queue receiving streaming events from the flow execution.
        is_disconnected: Optional async function to check if client disconnected.
        cancel_event: Optional event to signal cancellation from outside.

    Yields:
        Tuples of (event_type, data) where event_type is "token", "flow_preview",
        "tool" (an agent tool call started or finished), "end", or "cancelled".
    """
    check_interval = 0.5  # Check every 500ms
    tool_states: dict[tuple[str, int], bool] = {}  # (message id, step index) -> finished

    while True:
        if cancel_event is not None and cancel_event.is_set():
            logger.info("Cancel event set, stopping event consumption")
            yield ("cancelled", "")
            return

        try:
            event = await asyncio.wait_for(event_queue.get(), timeout=check_interval)
        except asyncio.TimeoutError:
            if cancel_event is not None and cancel_event.is_set():
                logger.info("Cancel event set, stopping event consumption")
                yield ("cancelled", "")
                return

            if is_disconnected is not None:
                try:
                    disconnected = await is_disconnected()
                    if disconnected:
                        logger.info("Client disconnected, stopping event consumption")
                        yield ("cancelled", "")
                        return
                except Exception:  # noqa: BLE001, S110
                    pass  # Intentionally ignore disconnection check failures
            continue

        if event is None:
            break

        _event_id, event_data, _timestamp = event

        try:
            event_type, data = parse_event_data(event_data)
            if event_type == "token":
                chunk = data.get("chunk", "")
                if chunk:
                    yield ("token", chunk)
            elif event_type == "flow_preview":
                yield ("flow_preview", data)
            elif event_type == "add_message":
                for step in tool_steps(data, tool_states):
                    yield ("tool", step)
            elif event_type == "end":
                yield ("end", "")
                break
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.debug(f"Failed to parse event: {e}")
            continue

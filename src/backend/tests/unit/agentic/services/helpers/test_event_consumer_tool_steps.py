"""Agent tool calls are surfaced from add_message events (one start, one finish per call)."""

from terraflow.agentic.services.helpers.event_consumer import tool_steps


def _message(*contents):
    return {"id": "m1", "content_blocks": [{"title": "Agent Steps", "contents": list(contents)}]}


def _tool(name, output=None):
    return {"type": "tool_use", "name": name, "tool_input": {"query": "threshold"}, "output": output}


def test_each_call_reports_one_start_and_one_finish():
    states: dict = {}
    assert tool_steps(_message(_tool("search_components")), states) == [
        {"name": "search_components", "input": {"query": "threshold"}, "done": False}
    ]
    # The agent re-sends the whole message: an unchanged step is not reported again.
    assert tool_steps(_message(_tool("search_components")), states) == []
    finished = tool_steps(_message(_tool("search_components", output="4 results"), _tool("build_flow")), states)
    assert finished == [
        {"name": "search_components", "input": {"query": "threshold"}, "done": True},
        {"name": "build_flow", "input": {"query": "threshold"}, "done": False},
    ]
    assert tool_steps(_message(_tool("search_components", output="4 results"), _tool("build_flow")), states) == []


def test_text_blocks_are_ignored():
    assert tool_steps(_message({"type": "text", "text": "thinking"}), {}) == []

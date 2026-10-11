from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from lfx.custom.utils import create_component_template
from lfx.interface.earthflow_components import (
    EARTHFLOW_ASSISTANT_TOOLS_CATEGORY,
    _terrabox_categories_seen,
    apply_earthflow_component_policy,
    refresh_terrabox_components_cache,
)
from lfx.interface.earthflow_terrabox import (
    TerraboxToolSpec,
    build_terrabox_component_template,
    load_terrabox_tool_specs,
    terrabox_toolkit_category,
)


def _stub_component(display_name: str) -> dict[str, Any]:
    return {
        "display_name": display_name,
        "description": f"{display_name} component",
        "template": {},
    }


def _distance_spec() -> TerraboxToolSpec:
    return TerraboxToolSpec(
        slug="geo_basic.distance",
        name="Distance",
        description="Measure distance between two lon/lat points.",
        parameters={
            "type": "object",
            "properties": {
                "lon1": {"type": "number", "description": "First longitude"},
                "lat1": {"type": "number", "description": "First latitude"},
                "lon2": {"type": "number", "description": "Second longitude"},
                "lat2": {"type": "number", "description": "Second latitude"},
                "unit": {"type": "string", "enum": ["meters", "kilometers"], "default": "meters"},
                "metadata": {"type": "object", "description": "Optional request metadata"},
            },
            "required": ["lon1", "lat1", "lon2", "lat2"],
        },
    )


def test_earthflow_policy_keeps_all_native_components_and_injects_terrabox_tools():
    all_types = {
        "input_output": {
            "ChatInput": _stub_component("Chat Input"),
            "ChatOutput": _stub_component("Chat Output"),
        },
        "deepseek": {"DeepSeekModelComponent": _stub_component("DeepSeek")},
        "tools": {
            "TavilyAISearch": _stub_component("Tavily"),
            "WikipediaAPI": _stub_component("Wikipedia"),
            "PythonREPLTool": _stub_component("Python REPL"),
        },
        "Notion": {"NotionListPages": _stub_component("Notion List Pages")},
        "openai": {"OpenAIModelComponent": _stub_component("OpenAI")},
    }

    filtered = apply_earthflow_component_policy(all_types, terrabox_tools=[_distance_spec()])

    # All stock Langflow components pass through untouched -- the policy no
    # longer hides anything, it only regroups EarthFlow-specific additions.
    assert "ChatInput" in filtered["input_output"]
    assert "ChatOutput" in filtered["input_output"]
    assert "DeepSeekModelComponent" in filtered["deepseek"]
    assert "TavilyAISearch" in filtered["tools"]
    assert "WikipediaAPI" in filtered["tools"]
    assert "PythonREPLTool" in filtered["tools"]
    assert "NotionListPages" in filtered["Notion"]
    assert "OpenAIModelComponent" in filtered["openai"]
    # The generated Terrabox tool lands in its own per-toolkit category
    # ("geo_basic" for the "geo_basic.distance" slug, no "earthflow"/"tools"
    # prefix so the sidebar's auto-titled label stays short), not one giant
    # flat bucket -- each Terrabox toolkit is its own sidebar group.
    geo_basic_category = terrabox_toolkit_category("geo_basic.distance")
    assert geo_basic_category == "geo_basic"
    assert "TerraboxGeoBasicDistance" in filtered[geo_basic_category]


def test_earthflow_policy_splits_terrabox_tools_by_toolkit():
    earth_sci_spec = TerraboxToolSpec(
        slug="earth_sci.calculate_ati",
        name="Calculate ATI",
        description="Calculate ATI.",
        parameters={"type": "object", "properties": {}},
    )

    filtered = apply_earthflow_component_policy({}, terrabox_tools=[_distance_spec(), earth_sci_spec])

    assert "TerraboxGeoBasicDistance" in filtered["geo_basic"]
    assert "TerraboxEarthSciCalculateAti" in filtered["earth_sci"]
    # Toolkits don't leak into each other's category.
    assert "TerraboxEarthSciCalculateAti" not in filtered["geo_basic"]
    assert "TerraboxGeoBasicDistance" not in filtered["earth_sci"]


def test_earthflow_policy_regroups_native_earthflow_tools_into_assistant_toolkit():
    all_types = {
        "files_and_knowledge": {
            "File": _stub_component("File"),
            "WordDocumentTool": _stub_component("Word Document"),
            "CommandExecutionTool": _stub_component("Command Execution"),
        },
    }

    filtered = apply_earthflow_component_policy(all_types, terrabox_tools=[])

    # The stock file tool stays in its native category...
    assert "File" in filtered["files_and_knowledge"]
    assert "WordDocumentTool" not in filtered["files_and_knowledge"]
    assert "CommandExecutionTool" not in filtered["files_and_knowledge"]
    # ...while the EarthFlow-specific tools move into their own assistant toolkit.
    assert "WordDocumentTool" in filtered[EARTHFLOW_ASSISTANT_TOOLS_CATEGORY]
    assert "CommandExecutionTool" in filtered[EARTHFLOW_ASSISTANT_TOOLS_CATEGORY]


def test_terrabox_toolspec_builds_evalable_langflow_tool_component_template():
    template = build_terrabox_component_template(_distance_spec())

    assert template["display_name"] == "Distance"
    assert template["description"] == "Measure distance between two lon/lat points."
    assert template["icon"] == "Satellite"
    assert {"JSON", "Tool"}.issubset(set(template["base_classes"]))
    assert [output["name"] for output in template["outputs"]] == ["api_run_model", "api_build_tool"]
    assert template["template"]["base_url"]["value"] == "http://localhost:8000"
    assert template["template"]["api_key"]["_input_type"] == "SecretStrInput"
    assert template["template"]["lon1"]["_input_type"] == "FloatInput"
    assert template["template"]["unit"]["_input_type"] == "DropdownInput"
    assert template["template"]["param_metadata"]["_input_type"] == "DictInput"

    rebuilt_template, instance = create_component_template(
        component={
            "code": template["template"]["code"]["value"],
            "output_types": [],
        },
    )

    assert rebuilt_template["display_name"] == "Distance"
    assert instance.display_name == "Distance"


async def test_generated_terrabox_component_executes_via_sdk_rest_endpoint(monkeypatch):
    template = build_terrabox_component_template(_distance_spec())
    _, instance = create_component_template(
        component={
            "code": template["template"]["code"]["value"],
            "output_types": [],
        },
    )
    instance.base_url = "http://terrabox.local/"
    instance.api_key = "test-key"
    instance.timeout = 7
    instance.lon1 = 1.0
    instance.lat1 = 2.0
    instance.lon2 = 3.0
    instance.lat2 = 4.0
    instance.unit = "kilometers"
    instance.param_metadata = {"source": "unit-test"}

    captured: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "success": True,
                "outputs": {"distance": 313.7},
                "execution_id": "exec-1",
            }

    def fake_post(url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: int):
        captured.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("requests.post", fake_post)

    result = await instance.run_model()

    assert captured == {
        "url": "http://terrabox.local/v1/sdk/tools/geo_basic.distance/execute",
        "headers": {"X-API-Key": "test-key"},
        "json": {
            "inputs": {
                "lon1": 1.0,
                "lat1": 2.0,
                "lon2": 3.0,
                "lat2": 4.0,
                "unit": "kilometers",
                "metadata": {"source": "unit-test"},
            }
        },
        "timeout": 7,
    }
    assert result[0].data["success"] is True
    assert result[0].data["outputs"]["distance"] == 313.7

    tool = instance.build_tool()
    assert tool.name == "geo_basic_distance"
    assert set(tool.args_schema.model_fields) >= {"lon1", "lat1", "lon2", "lat2", "unit", "metadata"}


async def test_generated_terrabox_component_bridges_file_like_outputs_when_user_id_set(monkeypatch):
    template = build_terrabox_component_template(_distance_spec())
    _, instance = create_component_template(
        component={
            "code": template["template"]["code"]["value"],
            "output_types": [],
        },
    )
    instance.base_url = "http://terrabox.local"
    instance.api_key = "test-key"
    instance.timeout = 7
    instance.lon1 = 1.0
    instance.lat1 = 2.0
    instance.lon2 = 3.0
    instance.lat2 = 4.0
    instance.unit = "meters"
    instance._user_id = "user-1"

    class FakeExecuteResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"success": True, "outputs": {"saved": "/abs/runtime/scene.tif"}}

    class FakeRuntimeFileResponse:
        content = b"tif-bytes"

        def raise_for_status(self) -> None:
            return None

    class FakeUploaded:
        id = "22222222-2222-2222-2222-222222222222"
        name = "scene.tif"
        size = 9

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: FakeExecuteResponse())

    with (
        patch("lfx.interface.earthflow_terrabox.requests.get", return_value=FakeRuntimeFileResponse()),
        patch(
            "lfx.interface.earthflow_terrabox._upload_bridged_file",
            new=AsyncMock(return_value=FakeUploaded()),
        ),
    ):
        result = await instance.run_model()

    assert result[0].data["_generated_files"] == [
        {
            "field": "saved",
            "file_id": "22222222-2222-2222-2222-222222222222",
            "name": "scene.tif",
            "size": 9,
        }
    ]


async def test_generated_terrabox_component_skips_bridging_without_user_id(monkeypatch):
    template = build_terrabox_component_template(_distance_spec())
    _, instance = create_component_template(
        component={
            "code": template["template"]["code"]["value"],
            "output_types": [],
        },
    )
    instance.base_url = "http://terrabox.local"
    instance.api_key = "test-key"
    instance.timeout = 7
    instance.lon1 = 1.0
    instance.lat1 = 2.0
    instance.lon2 = 3.0
    instance.lat2 = 4.0
    instance.unit = "meters"
    # instance.user_id left unset (None) — no flow/user context, e.g. a design-time test call.

    class FakeExecuteResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"success": True, "outputs": {"saved": "/abs/runtime/scene.tif"}}

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: FakeExecuteResponse())

    with patch("lfx.interface.earthflow_terrabox.requests.get") as fake_get:
        result = await instance.run_model()

    fake_get.assert_not_called()
    assert "_generated_files" not in result[0].data


async def test_terrabox_reserved_frontend_field_names_are_aliased_without_changing_payload(monkeypatch):
    spec = TerraboxToolSpec(
        slug="earth_sci.microwave_ddm",
        name="Microwave DDM",
        description="Uses beta as a model parameter.",
        parameters={
            "type": "object",
            "properties": {
                "beta": {"type": "number", "description": "Model beta parameter"},
            },
            "required": ["beta"],
        },
    )

    template = build_terrabox_component_template(spec)

    assert "beta" not in template["template"]
    assert template["template"]["param_beta"]["display_name"] == "Beta"
    assert template["template"]["param_beta"]["_input_type"] == "FloatInput"

    _, instance = create_component_template(
        component={
            "code": template["template"]["code"]["value"],
            "output_types": [],
        },
    )
    instance.base_url = "http://terrabox.local"
    instance.api_key = "test-key"
    instance.timeout = 7
    instance.param_beta = 0.42

    captured: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"success": True, "outputs": {"ok": True}}

    def fake_post(url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: int):
        captured.update({"json": json})
        return FakeResponse()

    monkeypatch.setattr("requests.post", fake_post)

    await instance.run_model()

    assert captured["json"] == {"inputs": {"beta": 0.42}}


def _fake_tool(slug: str) -> dict[str, Any]:
    return {
        "slug": slug,
        "name": slug,
        "description": slug,
        "parameters": {"type": "object", "properties": {}},
        "requires_connection": False,
    }


def _fake_toolkit(slug: str, tool_slugs: list[str]) -> dict[str, Any]:
    return {
        "slug": slug,
        "name": slug,
        "description": "",
        "status": "active",
        "tools": [_fake_tool(tool_slug) for tool_slug in tool_slugs],
    }


def _fake_toolkits_response() -> list[dict[str, Any]]:
    """Mimics the body of GET /v1/sdk/toolkits for a handful of toolkits."""
    return [
        _fake_toolkit("geo_basic", ["geo_basic.distance"]),
        _fake_toolkit("geopatch", ["geopatch.train_init", "geopatch.generate_seg"]),
        _fake_toolkit(
            "geo_perception",
            [
                "geo_perception.vlm_analyze",
                "geo_perception.sam2_segment",
                "geo_perception.remoteclip_analysis",
                "geo_perception.strip_rcnn_detect",
                "geo_perception.remotesam",
                "geo_perception.instructsam",
                "geo_perception.draw_bboxes",
                "geo_perception.add_text",
                "geo_perception.ocr_extract",
                "geo_perception.bbox_expand",
                "geo_perception.bbox_to_centroid",
                "geo_perception.centroid_distance_extremes",
                "geo_perception.bbox_area",
            ],
        ),
        # A toolkit no Terraflow code has ever heard of -- stands in for
        # Terrabox shipping a brand-new toolkit after Terraflow was deployed.
        _fake_toolkit("brand_new_toolkit", ["brand_new_toolkit.frobnicate"]),
    ]


class _FakeToolkitsResponse:
    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[dict[str, Any]]:
        return self._payload


def _mock_terrabox_toolkits_endpoint(monkeypatch, payload: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Stubs requests.get for GET /v1/sdk/toolkits and returns the captured call kwargs."""
    captured: dict[str, Any] = {}

    def fake_get(url: str, *, headers: dict[str, str], timeout: int):
        captured.update({"url": url, "headers": headers, "timeout": timeout})
        return _FakeToolkitsResponse(payload if payload is not None else _fake_toolkits_response())

    monkeypatch.setenv("TERRALINK_API_KEY", "test-key")
    monkeypatch.setattr("requests.get", fake_get)
    return captured


def test_load_terrabox_tool_specs_calls_sdk_toolkits_endpoint_with_api_key(monkeypatch):
    captured = _mock_terrabox_toolkits_endpoint(monkeypatch)
    monkeypatch.setenv("TERRALINK_BASE_URL", "http://terrabox.local")

    load_terrabox_tool_specs()

    assert captured["url"] == "http://terrabox.local/v1/sdk/toolkits"
    assert captured["headers"] == {"X-API-Key": "test-key"}


def _fail_if_called(*_args, **_kwargs):
    msg = "requests.get should not be called when TERRALINK_API_KEY is unset"
    raise AssertionError(msg)


def test_load_terrabox_tool_specs_returns_empty_without_api_key(monkeypatch):
    monkeypatch.delenv("TERRALINK_API_KEY", raising=False)
    monkeypatch.setattr("requests.get", _fail_if_called)

    assert load_terrabox_tool_specs() == []


def test_load_terrabox_tool_specs_includes_new_toolkits_automatically(monkeypatch):
    """A toolkit absent from every allow/deny list must still come through.

    This is the "automatic" behavior this refactor exists for: discovery is a
    live API call, and the default policy is "everything Terrabox reports
    minus DEFAULT_EXCLUDED_TERRABOX_TOOLKITS", not a hardcoded allowlist --
    so a brand-new Terrabox toolkit doesn't need an Terraflow code change to
    show up.
    """
    _mock_terrabox_toolkits_endpoint(monkeypatch)

    specs = load_terrabox_tool_specs()
    slugs = {spec.slug for spec in specs}

    assert "brand_new_toolkit.frobnicate" in slugs


def test_default_terrabox_tool_filter_exposes_lightweight_remaining_tools(monkeypatch):
    _mock_terrabox_toolkits_endpoint(monkeypatch)

    specs = load_terrabox_tool_specs(include_toolkits={"geo_basic", "geopatch", "geo_perception"})
    slugs = {spec.slug for spec in specs}

    assert "geo_basic.distance" in slugs
    assert "geopatch.train_init" in slugs
    assert "geopatch.generate_seg" in slugs
    assert "geo_perception.bbox_area" in slugs
    assert "geo_perception.draw_bboxes" in slugs
    assert "geo_perception.ocr_extract" in slugs
    assert "geo_perception.vlm_analyze" not in slugs
    assert "geo_perception.sam2_segment" not in slugs
    assert "geo_perception.remoteclip_analysis" not in slugs
    assert "geo_perception.strip_rcnn_detect" not in slugs
    assert "geo_perception.remotesam" not in slugs
    assert "geo_perception.instructsam" not in slugs


def test_terrabox_tool_slug_allowlist_and_denylist_precisely_filter_tools(monkeypatch):
    _mock_terrabox_toolkits_endpoint(monkeypatch)
    monkeypatch.setenv("EARTHFLOW_TERRABOX_TOOLS", "geo_basic.distance,geo_perception.bbox_area")
    monkeypatch.setenv("EARTHFLOW_TERRABOX_EXCLUDE_TOOLS", "geo_basic.distance")

    specs = load_terrabox_tool_specs(include_toolkits={"geo_basic", "geopatch", "geo_perception"})

    assert [spec.slug for spec in specs] == ["geo_perception.bbox_area"]


def test_refresh_terrabox_components_cache_is_a_noop_before_the_cache_is_built(monkeypatch):
    from lfx.interface.components import component_cache

    monkeypatch.setattr(component_cache, "all_types_dict", None)

    assert refresh_terrabox_components_cache(terrabox_tools=[_distance_spec()]) == 0


def test_refresh_terrabox_components_cache_merges_new_and_drops_stale_toolkit_categories(monkeypatch):
    from lfx.interface.components import component_cache

    # Seed a base cache containing an unrelated native category plus a stale
    # Terrabox toolkit category from a previous refresh -- that category is
    # tracked in _terrabox_categories_seen (mirroring what
    # apply_earthflow_component_policy would have left behind at startup)
    # but the "live" tool set passed to refresh below no longer reports it,
    # simulating a toolkit that disappeared from Terrabox.
    monkeypatch.setattr(
        component_cache,
        "all_types_dict",
        {
            "input_output": {"ChatInput": {"display_name": "Chat Input"}},
            "earth_sci": {"TerraboxEarthSciCalculateAti": {"display_name": "Calculate ATI"}},
        },
    )
    _terrabox_categories_seen.clear()
    _terrabox_categories_seen.add("earth_sci")

    count = refresh_terrabox_components_cache(terrabox_tools=[_distance_spec()])

    assert count == 1
    assert "TerraboxGeoBasicDistance" in component_cache.all_types_dict["geo_basic"]
    # The stale toolkit category (no longer reported) is removed...
    assert "earth_sci" not in component_cache.all_types_dict
    # ...while an unrelated native category is left untouched.
    assert "ChatInput" in component_cache.all_types_dict["input_output"]


async def test_optional_numeric_field_without_default_is_omitted_when_blank(monkeypatch):
    # FloatInput turns a blank field into 0.0; an optional number without a schema
    # default must stay blank (omitted) so the tool's own default applies.
    spec = TerraboxToolSpec(
        slug="geo_raster.calculate_index",
        name="Calculate Index",
        description="Normalized difference of two bands.",
        parameters={
            "type": "object",
            "properties": {
                "band_a_path": {"type": "string"},
                "scale_factor": {"type": "number", "description": "Optional scaling factor (default: 1.0)."},
                "bins": {"type": "integer"},
                "gain": {"type": "number", "default": 2.0},
            },
            "required": ["band_a_path"],
        },
    )
    template = build_terrabox_component_template(spec)
    assert template["template"]["scale_factor"]["_input_type"] == "MessageTextInput"
    assert template["template"]["bins"]["_input_type"] == "MessageTextInput"
    assert template["template"]["gain"]["_input_type"] == "FloatInput"

    _, instance = create_component_template(
        component={"code": template["template"]["code"]["value"], "output_types": []},
    )
    instance.base_url = "http://terrabox.local"
    instance.api_key = "test-key"
    instance.timeout = 7
    instance.band_a_path = "/data/green.tif"
    instance.scale_factor = ""
    instance.bins = " 12 "
    instance.gain = 2.0
    assert instance._collect_inputs() == {"band_a_path": "/data/green.tif", "bins": 12, "gain": 2.0}
    instance.scale_factor = "0.0001"
    assert instance._collect_inputs()["scale_factor"] == 0.0001

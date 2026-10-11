from __future__ import annotations

import keyword
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from lfx.custom.utils import create_component_template

logger = logging.getLogger(__name__)

DEFAULT_TERRABOX_BASE_URL = "http://localhost:8000"

# Heuristic for spotting file references inside a Terrabox tool's arbitrary
# output dict. Terrabox tool handlers don't follow a single naming
# convention for "this value is a generated file" (e.g. stac_basic.py uses a
# plain "saved" key, not "*_path"), so the extension is the only reliable
# signal — any string value ending in one of these suffixes is treated as a
# file reference, regardless of its key name.
_KNOWN_FILE_EXTENSIONS = frozenset(
    {
        ".tif",
        ".tiff",
        ".png",
        ".jpg",
        ".jpeg",
        ".docx",
        ".xlsx",
        ".xls",
        ".csv",
        ".geojson",
        ".pdf",
        ".shp",
        ".zip",
    }
)
_MAX_FILE_CANDIDATES = 10
_MAX_SCAN_DEPTH = 6
DEFAULT_EXCLUDED_TERRABOX_TOOLKITS: frozenset[str] = frozenset(
    {
        "bash",
        "example",
        "github",
        "ipython",
        # Requires its own external Bing Search API connection/credentials
        # that the service account calling Terrabox doesn't have configured;
        # excluded by default for the same reason as bash/github, not because
        # it's unsupported.
        "bing_search",
    }
)
DEFAULT_LIGHTWEIGHT_GEO_PERCEPTION_TOOLS: frozenset[str] = frozenset(
    {
        "geo_perception.draw_bboxes",
        "geo_perception.add_text",
        "geo_perception.ocr_extract",
        "geo_perception.bbox_expand",
        "geo_perception.bbox_to_centroid",
        "geo_perception.centroid_distance_extremes",
        "geo_perception.bbox_area",
    }
)
DEFAULT_EXCLUDED_TERRABOX_TOOLS: frozenset[str] = frozenset(
    {
        "geo_perception.vlm_analyze",
        "geo_perception.sam2_segment",
        "geo_perception.remoteclip_analysis",
        "geo_perception.strip_rcnn_detect",
        "geo_perception.remotesam",
        "geo_perception.instructsam",
    }
)
_FRONTEND_NODE_RESERVED_FIELDS: frozenset[str] = frozenset(
    {
        "base_classes",
        "beta",
        "conditional_paths",
        "custom_fields",
        "description",
        "display_name",
        "documentation",
        "edited",
        "error",
        "field_order",
        "frozen",
        "full_path",
        "icon",
        "is_composition",
        "is_input",
        "is_output",
        "legacy",
        "metadata",
        "minimized",
        "name",
        "output_types",
        "outputs",
        "pinned",
        "priority",
        "replacement",
        "template",
        "tool_mode",
    }
)


@dataclass(frozen=True)
class TerraboxToolSpec:
    slug: str
    name: str
    description: str
    parameters: dict[str, Any]
    requires_connection: bool = False
    required_scopes: list[str] | None = None


def _as_tool_spec(raw: dict[str, Any]) -> TerraboxToolSpec:
    """Parse one ``ToolSpecOut`` JSON object (from ``GET /v1/sdk/toolkits``) into a TerraboxToolSpec."""
    return TerraboxToolSpec(
        slug=raw["slug"],
        name=raw["name"],
        description=raw.get("description") or "",
        parameters=raw.get("parameters") or {"type": "object", "properties": {}},
        requires_connection=bool(raw.get("requires_connection", False)),
        required_scopes=raw.get("required_scopes"),
    )


def find_file_like_values(obj: Any) -> list[tuple[str, str]]:
    """Recursively find dict values that look like generated file paths.

    Returns a list of ``(json_path, value)`` pairs, capped at
    ``_MAX_FILE_CANDIDATES`` total matches and ``_MAX_SCAN_DEPTH`` levels of
    nesting to stay safe against arbitrarily large/deep tool outputs.
    """
    matches: list[tuple[str, str]] = []
    _walk_file_like_values(obj, matches, depth=0, prefix="")
    return matches


def _walk_file_like_values(obj: Any, matches: list[tuple[str, str]], *, depth: int, prefix: str) -> None:
    if depth > _MAX_SCAN_DEPTH or len(matches) >= _MAX_FILE_CANDIDATES:
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            if len(matches) >= _MAX_FILE_CANDIDATES:
                return
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, str) and Path(value).suffix.lower() in _KNOWN_FILE_EXTENSIONS:
                matches.append((child_prefix, value))
            elif isinstance(value, dict | list):
                _walk_file_like_values(value, matches, depth=depth + 1, prefix=child_prefix)
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            if len(matches) >= _MAX_FILE_CANDIDATES:
                return
            if isinstance(item, dict | list):
                _walk_file_like_values(item, matches, depth=depth + 1, prefix=f"{prefix}[{index}]")


async def _upload_bridged_file(file_name: str, content: bytes, user_id: str) -> Any:
    """Upload raw bytes into Terraflow's user-scoped file storage.

    Reuses the exact upload path ``SaveToFileComponent._upload_file()`` already
    calls (see ``lfx.components.files_and_knowledge.save_file``), so bridged
    Terrabox files land in the same storage and are fetchable the same way as
    any other user-uploaded/generated file.
    """
    import io

    from fastapi import UploadFile

    from terraflow.api.v2.files import upload_user_file
    from terraflow.services.database.models.user.crud import get_user_by_id
    from lfx.services.deps import get_settings_service, get_storage_service, session_scope

    async with session_scope() as db:
        current_user = await get_user_by_id(db, user_id)
        return await upload_user_file(
            file=UploadFile(filename=file_name, file=io.BytesIO(content), size=len(content)),
            session=db,
            current_user=current_user,
            storage_service=get_storage_service(),
            settings_service=get_settings_service(),
            append=False,
        )


async def bridge_generated_files(
    payload: dict[str, Any],
    *,
    api_key: str,
    base_url: str,
    user_id: str,
    timeout: int = 60,
) -> list[dict[str, Any]]:
    """Fetch file-like Terrabox tool outputs and re-upload them into Terraflow's
    own (user-scoped) file storage so the frontend can fetch them.

    Terrabox tool outputs only ever carry an absolute path on Terrabox's own
    host (see ``terrabox.core.services.tool_service.ToolService.execute_tool``);
    nothing else in this codebase copies that file into Terraflow's storage, so
    without this step no frontend widget can ever load it. Best-effort: any
    failure here is logged and swallowed, never raised, since a broken preview
    must not break the underlying tool call.
    """
    outputs = payload.get("outputs")
    if not isinstance(outputs, dict):
        return []

    candidates = find_file_like_values(outputs)
    bridged: list[dict[str, Any]] = []
    for field_path, abs_path in candidates:
        try:
            response = requests.get(
                f"{base_url.rstrip('/')}/v1/sdk/runtime/files",
                params={"path": abs_path},
                headers={"X-API-Key": api_key},
                timeout=timeout,
            )
            response.raise_for_status()
        except Exception:  # noqa: BLE001
            logger.warning("Failed to fetch Terrabox runtime file %s", abs_path, exc_info=True)
            continue

        try:
            uploaded = await _upload_bridged_file(Path(abs_path).name, response.content, user_id)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to upload bridged Terrabox file for %s", field_path, exc_info=True)
            continue

        bridged.append(
            {
                "field": field_path,
                "file_id": str(uploaded.id),
                "name": uploaded.name,
                "size": uploaded.size,
            }
        )
    return bridged


def _safe_identifier(value: str, *, title: bool = False) -> str:
    parts = [part for part in re.split(r"[^0-9A-Za-z]+", value) if part]
    if not parts:
        return "Tool"
    if title:
        identifier = "".join(part[:1].upper() + part[1:] for part in parts)
    else:
        identifier = "_".join(part.lower() for part in parts)
    if identifier[0].isdigit():
        identifier = f"_{identifier}"
    if keyword.iskeyword(identifier):
        identifier = f"{identifier}_"
    return identifier


def component_class_name(slug: str) -> str:
    return f"Terrabox{_safe_identifier(slug, title=True)}"


def langchain_tool_name(slug: str) -> str:
    return _safe_identifier(slug)


def _display_name(field_name: str) -> str:
    return field_name.replace("_", " ").replace(".", " ").title()


def _schema_variants(schema: dict[str, Any]) -> list[dict[str, Any]]:
    variants = schema.get("oneOf") or schema.get("anyOf") or []
    return [variant for variant in variants if isinstance(variant, dict)]


def _schema_type(schema: dict[str, Any]) -> str:
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((item for item in schema_type if item != "null"), None)
    if isinstance(schema_type, str):
        return schema_type

    variants = _schema_variants(schema)
    variant_types = [_schema_type(variant) for variant in variants]
    if "object" in variant_types and "string" in variant_types:
        return "json_or_string"
    if variant_types:
        return variant_types[0]
    return "string"


def _component_input_aliases(field_names: list[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    used = {"base_url", "api_key", "timeout"}
    for field_name in field_names:
        candidate = field_name
        if (
            not re.match(r"^[A-Za-z_][0-9A-Za-z_]*$", candidate)
            or keyword.iskeyword(candidate)
            or candidate in _FRONTEND_NODE_RESERVED_FIELDS
        ):
            candidate = f"param_{_safe_identifier(field_name)}"
        base = candidate
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        aliases[field_name] = candidate
        used.add(candidate)
    return aliases


def _input_call(field_name: str, input_name: str, field_schema: dict[str, Any], *, required: bool) -> str:
    info = field_schema.get("description") or ""
    default = field_schema.get("default")
    base_kwargs = {
        "name": input_name,
        "display_name": _display_name(field_name),
        "info": info,
        "required": required,
    }

    options = field_schema.get("enum")
    if isinstance(options, list) and options:
        kwargs = {**base_kwargs, "options": options, "value": default if default is not None else options[0]}
        return f"DropdownInput(**{kwargs!r})"

    schema_type = _schema_type(field_schema)
    if schema_type in {"number", "integer"} and not required and default is None:
        # A FloatInput/IntInput turns a blank field into 0, which the tool would
        # receive as a real value (e.g. scale_factor=0 zeroes every band). A text
        # field keeps "blank" distinct, so the argument is omitted and the tool's
        # own default applies; _coerce_value converts the text to a number.
        hint = "Leave blank to use the tool's default."
        kwargs = {**base_kwargs, "info": f"{info} {hint}".strip(), "input_types": ["Message"]}
        return f"MessageTextInput(**{kwargs!r})"
    if schema_type == "number":
        kwargs = dict(base_kwargs)
        if default is not None:
            kwargs["value"] = default
        kwargs["input_types"] = ["Message"]
        return f"FloatInput(**{kwargs!r})"
    if schema_type == "integer":
        kwargs = dict(base_kwargs)
        if default is not None:
            kwargs["value"] = default
        kwargs["input_types"] = ["Message"]
        return f"IntInput(**{kwargs!r})"
    if schema_type == "boolean":
        kwargs = dict(base_kwargs)
        if default is not None:
            kwargs["value"] = default
        kwargs["input_types"] = ["Message"]
        return f"BoolInput(**{kwargs!r})"
    if schema_type == "object":
        kwargs = dict(base_kwargs)
        if isinstance(default, dict):
            kwargs["value"] = default
        kwargs["input_types"] = ["JSON"]
        return f"DictInput(**{kwargs!r})"
    if schema_type in {"array", "json_or_string"}:
        hint = "Enter JSON for structured values."
        kwargs = {**base_kwargs, "info": f"{info} {hint}".strip()}
        if default is not None:
            kwargs["value"] = default if isinstance(default, str) else repr(default)
        return f"MultilineInput(**{kwargs!r})"

    kwargs = dict(base_kwargs)
    if default is not None:
        kwargs["value"] = default
    return f"MessageTextInput(**{kwargs!r})"


def _schema_properties(parameters: dict[str, Any]) -> dict[str, Any]:
    properties = parameters.get("properties") if isinstance(parameters, dict) else None
    return properties if isinstance(properties, dict) else {}


def _required_fields(parameters: dict[str, Any]) -> set[str]:
    required = parameters.get("required") if isinstance(parameters, dict) else None
    return set(required) if isinstance(required, list) else set()


def build_terrabox_component_source(spec: TerraboxToolSpec) -> str:
    class_name = component_class_name(spec.slug)
    tool_name = langchain_tool_name(spec.slug)
    parameters = spec.parameters or {"type": "object", "properties": {}}
    properties = _schema_properties(parameters)
    required = _required_fields(parameters)
    input_aliases = _component_input_aliases(list(properties))

    input_calls = [
        "StrInput(name='base_url', display_name='Terrabox Base URL', info='Terrabox service base URL.', "
        f"value=os.getenv('TERRALINK_BASE_URL', {DEFAULT_TERRABOX_BASE_URL!r}), advanced=True)",
        "SecretStrInput(name='api_key', display_name='Terrabox API Key', "
        "info='API key used as X-API-Key when calling Terrabox.', required=False, advanced=True)",
        "IntInput(name='timeout', display_name='Timeout', info='Terrabox request timeout in seconds.', "
        "value=60, advanced=True)",
    ]
    input_calls.extend(
        _input_call(
            field_name,
            input_aliases[field_name],
            field_schema if isinstance(field_schema, dict) else {},
            required=field_name in required,
        )
        for field_name, field_schema in properties.items()
    )
    inputs_source = ",\n        ".join(input_calls)

    return f"""import json
import logging
import os
from typing import Any

import requests
from pydantic import Field, create_model

from lfx.base.langchain_utilities.model import LCToolComponent
from lfx.field_typing import Tool
from lfx.inputs.inputs import (
    BoolInput,
    DictInput,
    DropdownInput,
    FloatInput,
    IntInput,
    MessageTextInput,
    MultilineInput,
    SecretStrInput,
    StrInput,
)
from lfx.interface.earthflow_terrabox import bridge_generated_files
from lfx.schema.data import Data

logger = logging.getLogger(__name__)


class {class_name}(LCToolComponent):
    display_name = {spec.name!r}
    description = {spec.description!r}
    icon = "Satellite"
    name = {class_name!r}

    inputs = [
        {inputs_source},
    ]

    _TERRABOX_SLUG = {spec.slug!r}
    _TERRABOX_TOOL_NAME = {tool_name!r}
    _TERRABOX_PARAMETERS = {parameters!r}
    _TERRABOX_FIELDS = {list(properties)!r}
    _TERRABOX_INPUT_ALIASES = {input_aliases!r}

    def _schema_type(self, schema: dict[str, Any]) -> str:
        schema_type = schema.get("type")
        if isinstance(schema_type, list):
            schema_type = next((item for item in schema_type if item != "null"), None)
        if isinstance(schema_type, str):
            return schema_type
        variants = schema.get("oneOf") or schema.get("anyOf") or []
        variant_types = [self._schema_type(variant) for variant in variants if isinstance(variant, dict)]
        if "object" in variant_types and "string" in variant_types:
            return "json_or_string"
        return variant_types[0] if variant_types else "string"

    def _python_type_for_schema(self, schema: dict[str, Any]):
        schema_type = self._schema_type(schema)
        if schema_type == "integer":
            return int
        if schema_type == "number":
            return float
        if schema_type == "boolean":
            return bool
        if schema_type == "array":
            return list
        if schema_type == "object":
            return dict
        return str

    def _coerce_value(self, field_name: str, value: Any) -> Any:
        if value == "" or value is None:
            return None
        schema = self._TERRABOX_PARAMETERS.get("properties", {{}}).get(field_name, {{}})
        schema_type = self._schema_type(schema)
        if schema_type in {{"number", "integer"}} and isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                number = float(stripped)
            except ValueError:
                return value
            return int(number) if schema_type == "integer" and number.is_integer() else number
        if schema_type in {{"array", "object", "json_or_string"}} and isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
        return value

    def _collect_inputs(self) -> dict[str, Any]:
        required = set(self._TERRABOX_PARAMETERS.get("required", []))
        inputs = {{}}
        for field_name in self._TERRABOX_FIELDS:
            input_name = self._TERRABOX_INPUT_ALIASES.get(field_name, field_name)
            value = self._coerce_value(field_name, getattr(self, input_name, None))
            if value is None and field_name not in required:
                continue
            inputs[field_name] = value
        return inputs

    def _api_key_value(self) -> str:
        api_key = getattr(self, "api_key", None) or os.getenv("TERRALINK_API_KEY", "")
        get_secret_value = getattr(api_key, "get_secret_value", None)
        if callable(get_secret_value):
            api_key = get_secret_value()
        return str(api_key or "")

    def _base_url_value(self) -> str:
        return (
            getattr(self, "base_url", None)
            or os.getenv("TERRALINK_BASE_URL", {DEFAULT_TERRABOX_BASE_URL!r})
        ).rstrip("/")

    def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        api_key = self._api_key_value()
        if not api_key:
            return {{
                "success": False,
                "error": "Terrabox API key is required. Set the component API key or TERRALINK_API_KEY.",
            }}

        base_url = self._base_url_value()
        url = f"{{base_url}}/v1/sdk/tools/{{self._TERRABOX_SLUG}}/execute"
        headers = {{"X-API-Key": api_key}}
        try:
            response = requests.post(
                url,
                headers=headers,
                json={{"inputs": inputs}},
                timeout=int(getattr(self, "timeout", 60) or 60),
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            payload = {{
                "success": False,
                "error": f"Terrabox request failed: {{exc}}",
                "inputs": inputs,
                "tool": self._TERRABOX_SLUG,
            }}
        return payload

    async def run_model(self) -> list[Data]:
        payload = self._execute(self._collect_inputs())
        # ``self.user_id`` falls back to a PlaceholderGraph's ``str(None)`` (the
        # literal string "None", not Python's None) when no real flow/vertex is
        # attached -- e.g. in isolated component construction/tests -- so it
        # must be checked explicitly rather than trusted for truthiness alone.
        user_id = self.user_id
        if payload.get("success") and user_id and user_id != "None":
            try:
                bridged = await bridge_generated_files(
                    payload,
                    api_key=self._api_key_value(),
                    base_url=self._base_url_value(),
                    user_id=user_id,
                )
                if bridged:
                    payload["_generated_files"] = bridged
            except Exception:  # noqa: BLE001
                logger.warning("Terrabox file bridging failed for %s", self._TERRABOX_SLUG, exc_info=True)
        self.status = payload
        return [Data(data=payload)]

    def _args_schema(self):
        fields = {{}}
        properties = self._TERRABOX_PARAMETERS.get("properties", {{}})
        required = set(self._TERRABOX_PARAMETERS.get("required", []))
        for field_name in self._TERRABOX_FIELDS:
            schema = properties.get(field_name, {{}})
            default = ... if field_name in required else schema.get("default", None)
            fields[field_name] = (
                self._python_type_for_schema(schema),
                Field(default, description=schema.get("description") or ""),
            )
        return create_model(f"{{self.name}}Schema", **fields)

    def build_tool(self) -> Tool:
        from langchain_core.tools import StructuredTool

        def call_terrabox_tool(**kwargs):
            return self._execute(kwargs)

        return StructuredTool.from_function(
            name=self._TERRABOX_TOOL_NAME,
            description=self.description,
            func=call_terrabox_tool,
            args_schema=self._args_schema(),
        )
"""


def build_terrabox_component_template(spec: TerraboxToolSpec) -> dict[str, Any]:
    source = build_terrabox_component_source(spec)
    template, _ = create_component_template(
        component={
            "code": source,
            "output_types": [],
        }
    )
    return template


def terrabox_toolkit_name(slug: str) -> str:
    """The Terrabox toolkit a tool slug belongs to, e.g. "geo_basic" for "geo_basic.distance"."""
    return slug.split(".", 1)[0]


def terrabox_toolkit_category(slug: str) -> str:
    """Sidebar category for a tool's toolkit, e.g. "geo_basic" for "geo_basic.distance".

    Grouping generated components by toolkit (rather than dumping all ~100+ of
    them into one flat bucket) gives each Terrabox toolkit its own collapsible
    sidebar section, with its own tools inside -- a toolkit-as-catalog structure
    instead of one long unbrowsable list. The category is just the bare toolkit
    name (no "earthflow"/"tools" prefix) so the sidebar's auto-titled fallback
    label (`toTitleCase(categoryName)`) stays short, e.g. "Geo Basic" rather than
    "Earthflow Tools Geo Basic".
    """
    return terrabox_toolkit_name(slug)


def build_terrabox_components_by_category(tool_specs: list[TerraboxToolSpec]) -> dict[str, dict[str, dict[str, Any]]]:
    """Build generated Terrabox tool components, grouped by toolkit category.

    Returns ``{category: {component_name: template}}`` so each Terrabox
    toolkit becomes its own sidebar group.
    """
    by_category: dict[str, dict[str, dict[str, Any]]] = {}
    for spec in tool_specs:
        try:
            template = build_terrabox_component_template(spec)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to build Terrabox component template for %s", spec.slug)
            continue
        category = terrabox_toolkit_category(spec.slug)
        by_category.setdefault(category, {})[component_class_name(spec.slug)] = template
    return by_category


def _parse_csv_env(value: str | None) -> set[str] | None:
    if not value:
        return None
    parsed = {part.strip() for part in value.split(",") if part.strip()}
    return parsed or None


def _default_include_tool(slug: str) -> bool:
    toolkit = slug.split(".", 1)[0]
    if toolkit == "geo_perception":
        return slug in DEFAULT_LIGHTWEIGHT_GEO_PERCEPTION_TOOLS
    return True


def load_terrabox_tool_specs(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: int = 15,
    include_toolkits: set[str] | None = None,
    exclude_toolkits: set[str] | None = None,
    include_tools: set[str] | None = None,
    exclude_tools: set[str] | None = None,
) -> list[TerraboxToolSpec]:
    """Discover Terrabox's currently available tools over its live SDK API.

    Terrabox already exposes this exact catalog to its own terralink frontend
    (``GET /v1/gui/toolkits``, JWT-authenticated); this calls the equivalent
    API-key-authenticated ``GET /v1/sdk/toolkits`` endpoint, reusing the same
    ``TERRALINK_BASE_URL``/``TERRALINK_API_KEY`` credentials the generated
    components already call Terrabox with for execution. Because this fetches
    live rather than importing Terrabox's Python package, a toolkit or tool
    Terrabox adds is visible here immediately -- no Terraflow dependency bump
    or code change needed, just a call to
    :func:`lfx.interface.earthflow_components.refresh_terrabox_components_cache`
    (or a process restart) to push it into the running server's component
    cache. ``include_toolkits``/``exclude_toolkits`` default to "everything
    Terrabox currently reports, minus ``DEFAULT_EXCLUDED_TERRABOX_TOOLKITS``"
    rather than a fixed allowlist, so new toolkits opt in automatically.
    """
    import os

    resolved_base_url = (base_url or os.getenv("TERRALINK_BASE_URL") or DEFAULT_TERRABOX_BASE_URL).rstrip("/")
    resolved_api_key = api_key or os.getenv("TERRALINK_API_KEY", "")

    # Unlike toolkit exclusion, toolkit inclusion has no default allowlist --
    # a value here (env or param) narrows to exactly these toolkits; leaving
    # it unset includes every toolkit Terrabox reports except `exclude`.
    include = (
        include_toolkits if include_toolkits is not None else _parse_csv_env(os.getenv("EARTHFLOW_TERRABOX_TOOLKITS"))
    )
    exclude = (
        exclude_toolkits
        if exclude_toolkits is not None
        else _parse_csv_env(os.getenv("EARTHFLOW_TERRABOX_EXCLUDE_TOOLKITS")) or set(DEFAULT_EXCLUDED_TERRABOX_TOOLKITS)
    )
    tool_allowlist = (
        include_tools if include_tools is not None else _parse_csv_env(os.getenv("EARTHFLOW_TERRABOX_TOOLS"))
    )
    tool_denylist = set(DEFAULT_EXCLUDED_TERRABOX_TOOLS)
    env_exclude_tools = (
        exclude_tools if exclude_tools is not None else _parse_csv_env(os.getenv("EARTHFLOW_TERRABOX_EXCLUDE_TOOLS"))
    )
    if env_exclude_tools:
        tool_denylist.update(env_exclude_tools)

    if not resolved_api_key:
        logger.warning(
            "TERRALINK_API_KEY is not set; skipping Terrabox tool discovery against %s.",
            resolved_base_url,
        )
        return []

    try:
        response = requests.get(
            f"{resolved_base_url}/v1/sdk/toolkits",
            headers={"X-API-Key": resolved_api_key},
            timeout=timeout,
        )
        response.raise_for_status()
        toolkits = response.json()
    except Exception:  # noqa: BLE001
        logger.exception("Failed to fetch Terrabox toolkits from %s", resolved_base_url)
        return []

    if not isinstance(toolkits, list):
        logger.warning("Unexpected Terrabox toolkits response shape from %s", resolved_base_url)
        return []

    specs: list[TerraboxToolSpec] = []
    for toolkit in toolkits:
        if not isinstance(toolkit, dict):
            continue
        toolkit_slug = toolkit.get("slug")
        if not toolkit_slug or toolkit_slug in exclude or (include is not None and toolkit_slug not in include):
            continue
        for raw_spec in toolkit.get("tools") or []:
            if not isinstance(raw_spec, dict) or not raw_spec.get("slug"):
                continue
            slug = raw_spec["slug"]
            if slug in tool_denylist:
                continue
            if tool_allowlist is not None:
                if slug not in tool_allowlist:
                    continue
            elif not _default_include_tool(slug):
                continue
            try:
                specs.append(_as_tool_spec(raw_spec))
            except KeyError:
                logger.warning("Skipping malformed Terrabox tool spec: %r", raw_spec)
    return sorted(specs, key=lambda item: item.slug)

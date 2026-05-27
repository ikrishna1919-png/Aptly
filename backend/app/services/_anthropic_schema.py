"""JSON-schema preparation helpers shared by every Anthropic structured-
output call site.

Anthropic's validator has two strictness rules that Pydantic's default
schema output doesn't satisfy:

  1. Every object node must explicitly set `additionalProperties: false`.
  2. A handful of JSON-schema keywords are not allowed: numeric/string/
     array range constraints (`minimum` / `maxItems` / etc.), AND the
     annotation keywords Pydantic ships by default — `default`, `title`,
     `examples`, `readOnly`, `writeOnly`, `deprecated`. `default` is the
     live regression that caused the resume-parse 400: Pydantic emits
     `"default": null` for every `field: T | None = None`, and the
     validator now rejects it.

`prepare_schema(model)` applies both passes recursively. Use it wherever
you'd otherwise pass `Model.model_json_schema()` to
`messages.create(output_config=...)`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# Keywords Anthropic's structured-output validator rejects (or that
# add no signal for the model and are stripped pre-emptively to keep
# this list as the single point of contact when the validator
# tightens further). Stripping is always safe — every enforcement
# that mattered lives in the Pydantic model + the prompt text.
_UNSUPPORTED_KEYS: tuple[str, ...] = (
    # Numeric range
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    # String range / shape
    "minLength",
    "maxLength",
    "pattern",
    "format",
    # Array range / shape
    "minItems",
    "maxItems",
    "uniqueItems",
    "contains",
    "minContains",
    "maxContains",
    # Annotations Anthropic rejects (`default`) or that are pure
    # noise once the schema reaches the API.
    "default",
    "title",
    "examples",
    "readOnly",
    "writeOnly",
    "deprecated",
)


def strictify_object_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively set `additionalProperties: false` on every object node.

    Walks: the root, `$defs`/`definitions`, `properties` values, `items`,
    `prefixItems`, and `anyOf`/`oneOf`/`allOf` branches."""

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        node_type = node.get("type")
        is_object = node_type == "object" or "properties" in node
        if is_object and "additionalProperties" not in node:
            node["additionalProperties"] = False
        for key in ("properties", "patternProperties", "$defs", "definitions"):
            if key in node and isinstance(node[key], dict):
                for sub in node[key].values():
                    walk(sub)
        if "items" in node:
            walk(node["items"])
        if "prefixItems" in node:
            walk(node["prefixItems"])
        for key in ("anyOf", "oneOf", "allOf"):
            if key in node:
                walk(node[key])

    walk(schema)
    return schema


def drop_unsupported_constraints(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively delete unsupported JSON-schema keywords (numeric /
    string / array range constraints — see `_UNSUPPORTED_KEYS`)."""

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        for key in _UNSUPPORTED_KEYS:
            node.pop(key, None)
        for key in ("properties", "patternProperties", "$defs", "definitions"):
            if key in node and isinstance(node[key], dict):
                for sub in node[key].values():
                    walk(sub)
        if "items" in node:
            walk(node["items"])
        if "prefixItems" in node:
            walk(node["prefixItems"])
        for key in ("anyOf", "oneOf", "allOf"):
            if key in node:
                walk(node[key])

    walk(schema)
    return schema


def prepare_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Render a Pydantic model to a JSON schema Anthropic accepts."""
    schema = model.model_json_schema()
    drop_unsupported_constraints(schema)
    strictify_object_schema(schema)
    return schema

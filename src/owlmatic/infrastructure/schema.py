from __future__ import annotations

from jsonschema import Draft202012Validator, SchemaError, ValidationError
from referencing import Registry

from ..domain import JsonObject
from ..errors import OwlError


class JsonSchemaValidator:
    def check_schema(self, schema: JsonObject) -> None:
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            raise OwlError(
                "INVALID_SCHEMA", "Schema does not conform to JSON Schema draft 2020-12"
            ) from error
        if schema.get("type") != "object":
            raise OwlError("INVALID_SCHEMA", "Input and output schemas must declare type object")

        # Walk schema objects, never fetch external references during discovery or execution.
        def check(node: object) -> None:
            if isinstance(node, dict):
                for key, item in node.items():
                    if key in {"$ref", "$dynamicRef"} and isinstance(item, str) and not item.startswith("#"):
                        raise OwlError(
                            "REMOTE_SCHEMA", "Only document-local JSON Schema references are supported"
                        )
                    check(item)
            elif isinstance(node, list):
                for item in node:
                    check(item)

        check(schema)

    def validate(self, schema: JsonObject, value: JsonObject, code: str) -> None:
        try:
            # An explicitly empty registry refuses retrieval by default. Do not use
            # jsonschema's backwards-compatible implicit network resolver.
            validator = Draft202012Validator(schema, registry=Registry())
            validator.validate(value)
        except ValidationError as error:
            path = ".".join(str(p) for p in error.absolute_path) or "root"
            raise OwlError(
                code, f"Validation failed at {path} ({error.validator}); consult the schema"
            ) from error
        except Exception as error:
            raise OwlError(code, "Schema evaluation failed; check document-local references") from error

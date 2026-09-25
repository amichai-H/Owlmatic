"""Small typed helpers at the genuinely dynamic host JSON boundary."""

from pydantic import JsonValue

from ...domain import JsonObject
from ...measurement.contracts import TokenUsage


def object_value(value: JsonValue | None) -> JsonObject:
    return value if isinstance(value, dict) else {}


def text_value(value: JsonValue | None) -> str | None:
    return value if isinstance(value, str) else None


def number(value: JsonValue | None) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("Invalid token count")
    return value


def usage(value: JsonObject, *, claude: bool) -> TokenUsage:
    incoming = number(value.get("input_tokens"))
    cached_key = "cache_read_input_tokens" if claude else "cached_input_tokens"
    write_key = "cache_creation_input_tokens" if claude else "cache_write_input_tokens"
    cached = number(value[cached_key]) if cached_key in value else None
    writes = number(value[write_key]) if write_key in value else None
    return TokenUsage(
        input_tokens=incoming + (cached or 0) + (writes or 0) if claude else incoming,
        cached_input_tokens=cached,
        cache_write_input_tokens=writes,
        output_tokens=number(value.get("output_tokens")),
        reasoning_output_tokens=number(value["reasoning_output_tokens"])
        if "reasoning_output_tokens" in value
        else None,
    )


def total(values: tuple[TokenUsage, ...]) -> TokenUsage | None:
    if not values:
        return None
    return TokenUsage(
        input_tokens=sum(v.input_tokens for v in values),
        output_tokens=sum(v.output_tokens for v in values),
        cached_input_tokens=sum(v.cached_input_tokens or 0 for v in values)
        if all(v.cached_input_tokens is not None for v in values)
        else None,
        cache_write_input_tokens=sum(v.cache_write_input_tokens or 0 for v in values)
        if all(v.cache_write_input_tokens is not None for v in values)
        else None,
        reasoning_output_tokens=sum(v.reasoning_output_tokens or 0 for v in values)
        if all(v.reasoning_output_tokens is not None for v in values)
        else None,
    )

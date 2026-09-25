"""Host event decoding only. Never infer token counts from command output size."""

from dataclasses import dataclass, field

from ...domain import JsonObject
from ...errors import OwlError
from ...measurement.contracts import TokenUsage
from ...serialization import json_object
from .decoding import object_value, text_value, total, usage


@dataclass
class Events:
    claude: bool
    partial_selection: bool = False
    usages: dict[str, TokenUsage] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    models: set[str] = field(default_factory=set)
    complete: bool = False
    human_prompts: int = 0
    issues: set[str] = field(default_factory=set)
    cumulative: TokenUsage | None = None
    initial: TokenUsage | None = None
    run_ids: set[str] = field(default_factory=set)
    started: bool = False

    def check_usage(self, previous: TokenUsage | None, value: TokenUsage) -> None:
        if previous and (
            value.input_tokens < previous.input_tokens or value.output_tokens < previous.output_tokens
        ):
            self.issues.add("usage_counter_regressed")

    def identify_runs(self) -> None:
        # Correlation hints only; the application verifies IDs against its own run ledger.
        for output in self.outputs.values():
            for line in output.splitlines():
                try:
                    value = json_object(line)
                except (ValueError, OwlError):
                    continue
                run = object_value(value.get("run"))
                run_id = text_value(run.get("run_id"))
                if run_id and run_id.startswith("run_") and isinstance(run.get("workflow_ref"), str):
                    self.run_ids.add(run_id)

    def feed(self, event: JsonObject, line: int, included: bool) -> None:
        kind = event.get("type")
        payload = object_value(event.get("payload"))
        message = object_value(event.get("message"))
        if kind == "event_msg" and payload.get("type") == "token_count":
            raw = object_value(object_value(payload.get("info")).get("total_token_usage"))
            if raw:
                value = usage(raw, claude=False)
                self.check_usage(self.cumulative or self.initial, value)
                if included:
                    self.cumulative = value
                else:
                    self.initial = value
        if not included:
            return
        if kind == "turn.started" or (
            kind == "event_msg" and payload.get("type") in {"task_started", "user_message"}
        ):
            self.started = True
        for raw_model in (event.get("model"), payload.get("model"), message.get("model")):
            if isinstance(raw_model, str):
                self.models.add(raw_model)
        if kind == "turn.completed":
            self.usages[str(event.get("id") or line)] = usage(object_value(event.get("usage")), claude=False)
            self.complete = True
        elif kind in {"turn.started", "turn.failed", "error"}:
            self.complete = False
            self.human_prompts += int(kind == "turn.started")
        elif kind == "event_msg":
            if payload.get("type") == "task_complete":
                self.complete = True
            elif payload.get("type") in {"task_started", "user_message"}:
                self.complete = False
                self.human_prompts += int(payload.get("type") == "user_message")
        elif kind == "item.completed":
            item = object_value(event.get("item"))
            if item.get("type") in {"command_execution", "mcp_tool_call"}:
                output = text_value(item.get("aggregated_output"))
                if output is not None:
                    self.outputs[str(item.get("id") or line)] = output
                else:
                    self.issues.add("tool_output_format_unavailable")
        elif kind == "response_item" and payload.get("type") == "function_call_output":
            output = text_value(payload.get("output"))
            if output is not None:
                self.outputs[str(payload.get("call_id") or line)] = output
            else:
                self.issues.add("tool_output_format_unavailable")
        if not self.claude:
            return
        if kind == "result":
            value = usage(object_value(event.get("usage")), claude=True)
            self.check_usage(total(tuple(self.usages.values())), value)
            self.cumulative = value
            if self.partial_selection:
                self.issues.add("session_summary_cannot_be_sliced")
            model_usage = object_value(event.get("modelUsage"))
            if len(model_usage) > 1:
                self.issues.add("mixed_models")
            self.complete = not bool(event.get("is_error"))
            if bool(event.get("is_error")):
                self.issues.add("host_reported_error")
        elif kind == "assistant":
            raw = object_value(message.get("usage"))
            key = text_value(message.get("id"))
            if raw and key:
                value = usage(raw, claude=True)
                self.check_usage(self.usages.get(key), value)
                self.usages[key] = value
                self.complete = message.get("stop_reason") in {"end_turn", "stop_sequence"}
            elif raw:
                self.issues.add("message_identity_missing")
        elif kind == "user":
            content = message.get("content")
            results = False
            if isinstance(content, list):
                for index, part in enumerate(content):
                    block = object_value(part)
                    if block.get("type") == "tool_result":
                        results = True
                        content_value = block.get("content")
                        if isinstance(content_value, str):
                            self.outputs[str(block.get("tool_use_id") or f"{line}:{index}")] = content_value
                        elif isinstance(content_value, list):
                            chunks = [text_value(object_value(item).get("text")) for item in content_value]
                            if all(chunk is not None for chunk in chunks):
                                self.outputs[str(block.get("tool_use_id") or f"{line}:{index}")] = "\n".join(
                                    chunk for chunk in chunks if chunk is not None
                                )
                            else:
                                self.issues.add("nontext_tool_output")
            if not results:
                self.human_prompts += 1
                self.complete = False
                self.started = True
        elif kind == "stream_event":
            self.issues.add("unsupported_partial_stream_format")
        if event.get("parent_tool_use_id") or event.get("isSidechain"):
            self.issues.add("subagent_usage_unattributed")

    def consumption(self) -> TokenUsage | None:
        if self.cumulative is None:
            return total(tuple(self.usages.values()))
        last = self.cumulative
        first = self.initial
        if first is None:
            return last
        return TokenUsage(
            input_tokens=last.input_tokens - first.input_tokens,
            output_tokens=last.output_tokens - first.output_tokens,
            cached_input_tokens=last.cached_input_tokens - first.cached_input_tokens
            if last.cached_input_tokens is not None and first.cached_input_tokens is not None
            else None,
            cache_write_input_tokens=last.cache_write_input_tokens - first.cache_write_input_tokens
            if last.cache_write_input_tokens is not None and first.cache_write_input_tokens is not None
            else None,
            reasoning_output_tokens=last.reasoning_output_tokens - first.reasoning_output_tokens
            if last.reasoning_output_tokens is not None and first.reasoning_output_tokens is not None
            else None,
        )

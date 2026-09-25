"""Bounded local history import. Raw transcripts are read, never copied into the ledger."""

import hashlib

from ...errors import OwlError
from ...measurement.contracts import Exposure, ImportRequest, TaskObservation
from ...measurement.ports import ExposureCounter
from ...serialization import json_object
from .host_events import Events


class LocalHistory:
    def __init__(self, counter: ExposureCounter) -> None:
        self.counter = counter

    def read(self, request: ImportRequest, observed_at: str) -> TaskObservation:
        path = request.session.expanduser().resolve()
        if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
            raise OwlError("HISTORY_LIMIT", "Select a regular history file of at most 32 MiB")
        events = Events(claude=request.host == "claude", partial_selection=request.first_line > 1)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for line, raw in enumerate(iter(lambda: stream.readline(1024 * 1024 + 1), b""), 1):
                if request.last_line is not None and line > request.last_line:
                    break
                if len(raw) > 1024 * 1024:
                    raise OwlError("HISTORY_LIMIT", "A history event exceeds 1 MiB")
                included = line >= request.first_line
                if included:
                    digest.update(raw)
                try:
                    event = json_object(raw)
                    events.feed(event, line, included)
                except (ValueError, OwlError):
                    events.issues.add("invalid_or_partial_event")
        try:
            consumption = events.consumption()
        except ValueError:
            consumption = None
            events.issues.add("usage_counter_regressed")
        if events.human_prompts > 1:
            events.issues.add("multiple_tasks_select_line_range")
        if request.first_line > 1 and not events.started:
            events.issues.add("task_boundary_unavailable")
        if len(events.models) > 1:
            events.issues.add("mixed_models")
        model = next(iter(events.models)) if len(events.models) == 1 else request.model
        if request.model and events.models and events.models != {request.model}:
            events.issues.add("model_override_conflict")
        if consumption is None:
            events.issues.add("provider_usage_unavailable")
        if model is None:
            events.issues.add("model_unavailable")
        complete = events.complete and consumption is not None and not events.issues
        events.identify_runs()
        counts = self.counter.count(tuple(events.outputs.values()))
        exposure = Exposure(
            visible_bytes=counts.visible_bytes,
            reference_tokens=counts.reference_tokens,
            counter=counts.counter,
            tool_results=counts.tool_results,
            complete=complete and bool(events.outputs),
        )
        return TaskObservation(
            task_id=request.task_id,
            host=request.host,
            model=model,
            purpose=request.purpose,
            workload=request.workload,
            workflow_ref=request.workflow_ref,
            run_ids=request.run_ids or tuple(sorted(events.run_ids))
            if request.purpose == "reuse"
            else request.run_ids,
            verified=request.verified,
            verification_basis="user_attested" if request.verified else "unverified",
            usage=consumption,
            complete=complete,
            exposure=exposure,
            issues=tuple(sorted(events.issues)),
            source_digest=digest.hexdigest(),
            source_key=hashlib.sha256(
                f"{request.host}:{path}:{request.first_line}:{request.last_line}".encode()
            ).hexdigest(),
            source_file_key=hashlib.sha256(f"{request.host}:{path}".encode()).hexdigest(),
            first_line=request.first_line,
            last_line=request.last_line,
            observed_at=observed_at,
        )

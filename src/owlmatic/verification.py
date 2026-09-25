"""Deterministic evidence policy; schema validation is injected."""

from .domain import CheckCounts, Evidence, Workflow
from .errors import OwlError
from .ports import SchemaValidator


def verify_evidence(
    evidence: Evidence, workflow: Workflow, run_id: str, exit_code: int, schemas: SchemaValidator
) -> CheckCounts:
    if evidence.run_id != run_id:
        raise OwlError("STALE_EVIDENCE", "Evidence belongs to another invocation")
    names = [check.name for check in evidence.checks]
    if len(set(names)) != len(names):
        raise OwlError("DUPLICATE_CHECK", "Evidence contains duplicate check names")
    schemas.validate(workflow.output_schema, evidence.outputs, "INVALID_OUTPUT")
    required = workflow.manifest.verification.required_checks
    statuses = {check.name: check.status for check in evidence.checks}
    if evidence.outcome == "pass" and (
        exit_code != 0
        or any(statuses.get(name) != "pass" for name in required)
        or any(check.status == "fail" for check in evidence.checks)
        or evidence.effects in {"partial", "unknown"}
    ):
        raise OwlError(
            "UNPROVEN_PASS", "Passing evidence requires successful execution and all required checks"
        )
    if evidence.outcome == "fail" and not any(check.status == "fail" for check in evidence.checks):
        raise OwlError("UNPROVEN_FAILURE", "Failure evidence must identify a failed check")
    if exit_code not in {0, 1}:
        raise OwlError("PROCESS_FAILED", "Workflow process ended unexpectedly")
    return CheckCounts(
        required=len(required),
        passed=sum(c.status == "pass" for c in evidence.checks),
        failed=sum(c.status == "fail" for c in evidence.checks),
        skipped=sum(c.status == "skip" for c in evidence.checks),
    )

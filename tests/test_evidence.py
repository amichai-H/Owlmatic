import pytest

from owlmatic.domain import Check, Evidence, Workflow
from owlmatic.errors import OwlError
from owlmatic.infrastructure.schema import JsonSchemaValidator
from owlmatic.verification import verify_evidence


def evidence() -> Evidence:
    return Evidence(
        run_id="invocation",
        outcome="pass",
        checks=(Check(name="suite_passed", status="pass"),),
        outputs={"tests": 2, "suite_passed": True},
        effects="none",
    )


@pytest.mark.parametrize(
    "invalid,code",
    [
        (evidence().model_copy(update={"run_id": "old-invocation"}), "STALE_EVIDENCE"),
        (evidence().model_copy(update={"checks": ()}), "UNPROVEN_PASS"),
        (
            evidence().model_copy(update={"checks": (Check(name="suite_passed", status="skip"),)}),
            "UNPROVEN_PASS",
        ),
        (evidence().model_copy(update={"effects": "unknown"}), "UNPROVEN_PASS"),
        (evidence().model_copy(update={"outcome": "fail"}), "UNPROVEN_FAILURE"),
        (evidence().model_copy(update={"outputs": {}}), "INVALID_OUTPUT"),
        (evidence().model_copy(update={"checks": evidence().checks * 2}), "DUPLICATE_CHECK"),
    ],
)
def test_false_success_is_rejected(workflow: Workflow, invalid: Evidence, code: str) -> None:
    with pytest.raises(OwlError) as caught:
        verify_evidence(invalid, workflow, "invocation", 0, JsonSchemaValidator())
    assert caught.value.code == code


def test_nonzero_exit_cannot_claim_pass(workflow: Workflow) -> None:
    with pytest.raises(OwlError) as caught:
        verify_evidence(evidence(), workflow, "invocation", 1, JsonSchemaValidator())
    assert caught.value.code == "UNPROVEN_PASS"


def test_valid_failure_is_completed_evidence(workflow: Workflow) -> None:
    failure = Evidence(
        run_id="invocation",
        outcome="fail",
        checks=(Check(name="suite_passed", status="fail"),),
        outputs={"tests": 2, "suite_passed": False},
        effects="none",
    )
    counts = verify_evidence(failure, workflow, "invocation", 1, JsonSchemaValidator())
    assert counts.failed == 1

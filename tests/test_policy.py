from pathlib import Path

import pytest
from pydantic import ValidationError

from owlmatic.domain import Profile, RunRequest, Settings, Workflow
from owlmatic.errors import OwlError
from owlmatic.infrastructure.schema import JsonSchemaValidator
from owlmatic.policy import ExecutionPolicy, environment_for
from tests.fakes import FakeBundles, FakeHost, MemorySettings


def test_untrusted_code_never_reaches_bundle_or_host(workflow: Workflow) -> None:
    bundles = FakeBundles(workflow)
    policy = ExecutionPolicy(MemorySettings(), bundles, JsonSchemaValidator(), FakeHost())
    with pytest.raises(OwlError, match="explicitly trust"):
        policy.check(workflow, {"healthy": True}, "default", "simulation", Path("/fixture"))
    assert bundles.verified == 0


@pytest.mark.parametrize("inputs", [{"healthy": "true"}, {"healthy": True, "extra": 1}, {}])
def test_input_contract_is_not_coerced(workflow: Workflow, inputs: dict[str, object]) -> None:
    import json

    from owlmatic.serialization import json_object

    settings = MemorySettings(Settings(profiles={"default": Profile(grants=(workflow.ref,))}))
    policy = ExecutionPolicy(settings, FakeBundles(workflow), JsonSchemaValidator(), FakeHost())
    with pytest.raises(OwlError) as caught:
        policy.check(workflow, json_object(json.dumps(inputs)), "default", "simulation", Path("/fixture"))
    assert caught.value.code == "INVALID_INPUT"


def test_environment_requires_both_grants(workflow: Workflow) -> None:
    settings = MemorySettings(Settings(profiles={"default": Profile(grants=(workflow.ref,))}))
    policy = ExecutionPolicy(settings, FakeBundles(workflow), JsonSchemaValidator(), FakeHost())
    with pytest.raises(OwlError) as caught:
        policy.check(workflow, {"healthy": True}, "default", "production", Path("/fixture"))
    assert caught.value.code == "ENVIRONMENT_DENIED"


def test_only_explicit_environment_reaches_workflow(workflow: Workflow) -> None:
    changed = workflow.model_copy(
        update={"manifest": workflow.manifest.model_copy(update={"credentials": ("api",)})}
    )
    selected = environment_for(
        changed,
        Profile(env=("LANGUAGE",), credentials={"api": "API_KEY"}),
        {"PATH": "/bin", "HOME": "/secret", "API_KEY": "top-secret", "LANGUAGE": "en"},
    )
    assert selected.values["OWLMATIC_CREDENTIAL_API"] == "top-secret"
    assert "API_KEY" not in selected.values
    assert "HOME" not in selected.values
    assert selected.secrets == ("top-secret",)


def test_reserved_environment_cannot_override_runtime(workflow: Workflow) -> None:
    with pytest.raises(OwlError) as caught:
        environment_for(workflow, Profile(env=("PYTHONPATH",)), {"PYTHONPATH": "/injected"})
    assert caught.value.code == "INVALID_PROFILE"


def test_contract_rejects_unknown_fields_and_coercion() -> None:
    with pytest.raises(ValidationError):
        RunRequest.model_validate_json('{"ref":"x","workspace":"/tmp","wait_seconds":"20"}')
    with pytest.raises(ValidationError):
        Profile.model_validate_json('{"administrator":true}')

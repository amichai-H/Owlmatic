from pathlib import Path

from owlmatic.bootstrap import create_application
from owlmatic.domain import SearchRequest, Workflow
from owlmatic.serialization import encode


def catalog_entries(workflow: Workflow, count: int) -> list[Workflow]:
    result: list[Workflow] = []
    for index in range(count):
        manifest = workflow.manifest.model_copy(
            update={
                "id": f"probe.{index:03}",
                "description": "health probe",
                "environments": ("simulation",) if index < count - 1 else ("integration",),
            }
        )
        result.append(
            workflow.model_copy(
                update={
                    "ref": f"examples/probe.{index:03}@{workflow.digest}",
                    "id": manifest.id,
                    "manifest": manifest,
                }
            )
        )
    return result


def test_filtering_precedes_search_limit(tmp_path: Path, workflow: Workflow) -> None:
    app = create_application(tmp_path)
    entries = catalog_entries(workflow, 150)
    app.catalog.workflows.replace_catalog("examples", entries)
    result = app.catalog.find(SearchRequest(query="health", environment="integration"))
    assert len(result.results) == 1 and result.results[0].ref == entries[-1].ref
    assert not result.more
    first = app.catalog.workflows.search("health", offset=0, limit=3)
    second = app.catalog.workflows.search("health", offset=3, limit=3)
    assert len(first) == len(second) == 3
    assert not {item.ref for item in first} & {item.ref for item in second}


def test_oversized_metadata_does_not_hide_matches(tmp_path: Path, workflow: Workflow) -> None:
    app = create_application(tmp_path)
    first, second = catalog_entries(workflow, 2)
    first = first.model_copy(
        update={"manifest": first.manifest.model_copy(update={"effects": ("x" * 256,) * 10})}
    )
    app.catalog.workflows.replace_catalog("examples", [first, second])
    result = app.catalog.find(SearchRequest(query="health"))
    assert {item.ref for item in result.results} == {first.ref, second.ref}
    assert next(item for item in result.results if item.ref == first.ref).details_required
    assert len(encode(result).encode()) <= 2048


def test_exact_search_wins_and_empty_search_has_no_false_more(tmp_path: Path, workflow: Workflow) -> None:
    app = create_application(tmp_path)
    entries = catalog_entries(workflow, 150)
    app.catalog.workflows.replace_catalog("examples", entries)
    found = app.catalog.find(SearchRequest(query=entries[-1].id))
    assert found.results[0].ref == entries[-1].ref
    assert app.catalog.find(SearchRequest(query="unknown-unrelated-word")).results == ()

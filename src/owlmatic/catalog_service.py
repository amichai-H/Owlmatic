from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .domain import Candidate, Catalog, SearchRequest, SearchResult, Settings, Workflow
from .errors import OwlError
from .messages import CatalogList, CatalogReport, TrustReport
from .policy import get_profile
from .ports import CatalogSource, Host, SettingsStore, WorkflowRepository
from .serialization import encode


@dataclass(frozen=True)
class CatalogService:
    settings: SettingsStore
    workflows: WorkflowRepository
    sources: CatalogSource
    host: Host

    def add(self, catalog: Catalog) -> CatalogReport:
        if any(c.name == catalog.name for c in self.settings.load().catalogs):
            raise OwlError("CATALOG_EXISTS", "Catalog already exists; use catalog sync")
        if catalog.source.startswith("-") or (catalog.revision and catalog.revision.startswith("-")):
            raise OwlError("INVALID_SOURCE", "Sources and revisions cannot be Git options")
        if catalog.kind == "git":
            parsed = urlparse(catalog.source)
            if (
                parsed.password
                or parsed.query
                or parsed.fragment
                or (parsed.scheme == "https" and parsed.username)
            ):
                raise OwlError(
                    "SECRET_IN_URL", "Use existing Git authentication instead of credentials in URLs"
                )
            if not (
                catalog.source.startswith(("https://", "ssh://", "git@"))
                or self.host.workspace_exists(Path(catalog.source))
            ):
                raise OwlError("INVALID_SOURCE", "Use an HTTPS/SSH URL or an existing local Git repository")
        workflows, revision = self.sources.read(catalog)
        self._unique(workflows)
        updated = catalog.at_revision(revision)

        def change(settings: Settings) -> Settings:
            if any(c.name == catalog.name for c in settings.catalogs):
                raise OwlError("CATALOG_EXISTS", "Catalog was registered by another process")
            self.workflows.replace_catalog(catalog.name, workflows)
            return settings.with_catalogs((*settings.catalogs, updated))

        self.settings.update(change)
        return CatalogReport(catalog=catalog.name, workflows=len(workflows), revision=revision)

    def sync(self, name: str) -> CatalogReport:
        catalog = next((c for c in self.settings.load().catalogs if c.name == name), None)
        if catalog is None:
            raise OwlError("CATALOG_NOT_FOUND", f"Unknown catalog: {name}")
        workflows, revision = self.sources.read(catalog)
        self._unique(workflows)
        self.workflows.replace_catalog(name, workflows)
        self.settings.update(
            lambda s: s.with_catalogs(
                tuple(c.at_revision(revision) if c.name == name else c for c in s.catalogs)
            )
        )
        return CatalogReport(catalog=name, workflows=len(workflows), revision=revision)

    @staticmethod
    def _unique(workflows: Sequence[Workflow]) -> None:
        if len({w.id for w in workflows}) != len(workflows):
            raise OwlError("DUPLICATE_ID", "A catalog cannot contain duplicate workflow IDs")

    def list(self) -> CatalogList:
        return CatalogList(catalogs=self.settings.load().catalogs)

    def describe(self, ref: str) -> Workflow:
        return self.workflows.get(ref)

    def trust(self, ref: str, profile_name: str = "default", *, revoke: bool = False) -> TrustReport:
        self.workflows.get(ref)

        def change(settings: Settings) -> Settings:
            p = settings.profiles.get(profile_name)
            if p is None:
                raise OwlError("INVALID_PROFILE", f"Unknown profile: {profile_name}")
            grants = tuple(g for g in p.grants if g != ref)
            if not revoke:
                grants = (*grants, ref)
            return settings.with_profile(profile_name, p.with_grants(grants))

        self.settings.update(change)
        return TrustReport(ref=ref, profile=profile_name, trusted=not revoke)

    def find(self, request: SearchRequest) -> SearchResult:
        if not request.query.strip():
            raise OwlError("INVALID_QUERY", "Search query must not be blank")
        profile = get_profile(self.settings, request.profile)
        candidates: list[Candidate] = []
        for workflow in self.workflows.search(
            request.query,
            environment=request.environment,
            repository=request.repository,
            platform=self.host.platform,
            limit=request.limit + 1,
        ):
            m = workflow.manifest
            required = workflow.input_schema.get("required", [])
            names = tuple(v for v in required if isinstance(v, str)) if isinstance(required, list) else ()
            candidates.append(
                Candidate(
                    ref=workflow.ref,
                    description=m.description,
                    environments=m.environments,
                    effects=m.effects,
                    required_inputs=names,
                    trust="trusted" if workflow.ref in profile.grants else "untrusted",
                )
            )
        selected: list[Candidate] = []
        for candidate in candidates:
            if len(encode(SearchResult(results=(candidate,), more=True)).encode()) > 2048:
                candidate = Candidate(
                    ref=candidate.ref,
                    description=candidate.description,
                    environments=(),
                    effects=(),
                    required_inputs=(),
                    trust=candidate.trust,
                    details_required=True,
                )
            proposed = SearchResult(results=(*selected, candidate), more=True)
            if len(selected) < request.limit and len(encode(proposed).encode()) <= 2048:
                selected.append(candidate)
        return SearchResult(results=tuple(selected), more=len(candidates) > len(selected))

"""Extract once, preserve the evidence, then run the declared bounded experiment."""

from dataclasses import dataclass

from .contracts import Inputs, Result, SnapshotUnavailable, Summary
from .ports import EvidenceStore, IncidentRepository
from .service import Reproducer, summarize


@dataclass(frozen=True)
class Experiment:
    incidents: IncidentRepository
    reproducer: Reproducer
    evidence: EvidenceStore

    def run(self, options: Inputs) -> Summary:
        try:
            incident = self.incidents.load(options.execution_id)
        except SnapshotUnavailable as error:
            result = Result(finding="blocked", reason=str(error))
        else:
            self.evidence.snapshot(incident)
            result = self.reproducer.run(incident, options)
        self.evidence.attempts(result)
        return summarize(result)

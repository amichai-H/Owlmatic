"""Escaped offline rendering of evidence-based measurements."""

from html import escape

from ..measurement.contracts import MeasurementReport


def render_measurements(report: MeasurementReport | None) -> str:
    if report is None:
        return ""

    def number(value: int | None) -> str:
        return f"{value:,}" if value is not None else "unknown"

    rows = "".join(
        f"<tr><td>{escape(row.workflow_ref)}</td><td>{escape(row.quality)}</td>"
        f"<td>{row.measured_tasks}/{row.observed_tasks}</td><td>{number(row.measured_agent_tokens)}</td>"
        f"<td>{number(row.estimated_context_reference_tokens)}</td>"
        f"<td>{number(row.estimated_operational_savings)}</td><td>{number(row.estimated_net_savings)}</td>"
        f"<td>{row.baseline_samples}</td><td>{escape(', '.join(row.issues))}</td></tr>"
        for row in report.assessments
    )
    return (
        "<section class='panel'><h2>Evidence-based measurements</h2>"
        f"<p>Retained measurement history; {report.unattributed_tasks} unattributed tasks. "
        f"Latest observation: {escape(report.latest_observation_at or 'unknown')}.</p>"
        "<p>Context counts use a reference tokenizer, not provider billing. Comparisons against recorded "
        "manual tasks are estimates, not guaranteed future savings. Missing creation costs prevent net claims.</p>"
        "<div class='scroll'><table><thead><tr><th>Workflow</th><th>Quality</th><th>Measured/tasks</th>"
        "<th>Execution tokens</th><th>Context reduction</th><th>Operational estimate</th><th>Net estimate</th>"
        f"<th>Samples</th><th>Coverage</th></tr></thead><tbody>{rows}</tbody></table></div></section>"
    )

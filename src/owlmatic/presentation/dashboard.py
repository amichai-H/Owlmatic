"""Offline HTML presentation of a typed statistics snapshot; no executable scripts."""

from html import escape
from importlib.resources import files

from ..statistics import StatisticsReport, WorkflowStatistics


def number(value: int | None) -> str:
    return f"{value:,}" if value is not None else "—"


def workflow_row(workflow: WorkflowStatistics) -> str:
    baseline = workflow.baseline
    details = "<span class='muted'>No baseline configured</span>"
    if baseline:
        details = (
            f"<details><summary>{escape(baseline.basis.title())} baseline</summary>"
            f"<p>Manual: {number(baseline.manual_tokens)} tokens / verified task<br>"
            f"Owlmatic: {number(baseline.owlmatic_tokens)} tokens / attempt<br>"
            f"Setup: {number(baseline.setup_tokens)} tokens<br>"
            f"Samples: {baseline.sample_size}<br>Source: {escape(baseline.source)}</p></details>"
        )
    return (
        f"<tr><td><code>{escape(workflow.ref)}</code>{details}</td>"
        f"<td>{number(workflow.runs)}</td><td>{number(workflow.verified)}</td>"
        f"<td>{number(workflow.detected_failures)}</td><td>{number(workflow.execution_errors)}</td>"
        f"<td class='value'>{number(workflow.estimated_net_tokens_saved)}</td></tr>"
    )


def chart(report: StatisticsReport) -> str:
    modeled = report.estimated_net_tokens_saved is not None
    label = "Daily estimated token savings, before setup" if modeled else "Daily workflow executions"
    values = [(day.estimated_tokens_saved_before_setup or 0) if modeled else day.runs for day in report.daily]
    low, high = min(0, *values), max(1, *values)

    def y(value: int) -> float:
        return 160 - (value - low) / (high - low) * 132

    points = " ".join(
        f"{56 + i / max(1, len(values) - 1) * 840:.1f},{y(value):.1f}" for i, value in enumerate(values)
    )
    last = report.daily[-1].day.isoformat()
    first = report.daily[0].day.isoformat()
    rows = "".join(
        f"<tr><td>{d.day.isoformat()}</td><td>{d.runs}</td><td>{d.verified}</td>"
        f"<td>{number(d.estimated_tokens_saved_before_setup)}</td></tr>"
        for d in report.daily
    )
    return (
        f"<h2>{label}</h2><svg viewBox='0 0 940 200' role='img' aria-label='{label}'>"
        f"<title>{label}. Exact values are in the daily data table below.</title>"
        f"<line x1='56' y1='{y(0):.1f}' x2='896' y2='{y(0):.1f}' class='axis'/>"
        f"<text x='4' y='32'>{high:,}</text><text x='4' y='162'>{low:,}</text>"
        f"<polyline points='{points}' class='trend'/>"
        f"<circle cx='896' cy='{y(values[-1]):.1f}' r='4' class='endpoint'/>"
        f"<text x='56' y='193'>{first}</text><text x='896' y='193' text-anchor='end'>{last}</text>"
        "</svg><details><summary>View daily data</summary><div class='scroll'><table>"
        "<thead><tr><th>Date (UTC)</th><th>Runs</th><th>Verified</th><th>Est. tokens before setup</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div></details>"
    )


def render_dashboard(report: StatisticsReport) -> str:
    css = files("owlmatic.presentation").joinpath("dashboard.css").read_text(encoding="utf-8")
    coverage = report.modeled_runs / report.terminal * 100 if report.terminal else 0
    rate = f"{report.verified / report.terminal * 100:.1f}%" if report.terminal else "—"
    estimate = report.estimated_net_tokens_saved
    net_label = (
        "Baseline needed"
        if estimate is None
        else ("Estimated net savings" if estimate >= 0 else "Estimated net overhead")
    )
    cards = (
        (
            net_label,
            number(abs(estimate) if estimate is not None else None),
            "tokens · after setup and failed attempts",
        ),
        (
            "Workflow executions",
            number(report.runs),
            f"{report.running:,} running · {report.execution_errors:,} execution errors",
        ),
        ("Verified completion", rate, "Includes valid negative findings"),
        (
            "Workflow runtime",
            f"{report.duration_ms / 1000:,.1f}s",
            "Measured execution time · not time saved",
        ),
    )
    card_html = "".join(
        f"<article class='card'><p class='label'>{escape(label)}</p><strong>{escape(value)}</strong>"
        f"<p class='muted'>{escape(note)}</p></article>"
        for label, value, note in cards
    )
    rows = "".join(workflow_row(w) for w in sorted(report.workflows, key=lambda w: (-w.runs, w.ref)))
    if not rows:
        rows = "<tr><td colspan='6' class='empty'>No workflow executions in this window. Run a trusted workflow to start measuring reuse.</td></tr>"
    setup = number(report.estimated_setup_tokens)
    manual = number(report.estimated_manual_tokens)
    automated = number(report.estimated_owlmatic_tokens)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Owlmatic · Savings dashboard</title><style>{css}</style></head><body>
<header><a class="brand" href="#overview"><span class="mark">◉</span> owlmatic<span class="wordmark"> / insights</span></a>
<span class="local">● Local snapshot</span></header>
<main id="overview"><div class="heading"><div><p class="eyebrow">EXECUTABLE MEMORY / IMPACT</p>
<h1>Less rediscovery.<br><span>More work done.</span></h1>
<p class="subtitle">A clear view of workflow reuse, reliability, and modeled token savings.</p></div>
<div class="period"><strong>Last {report.days} UTC calendar days</strong><br>
<span class="muted">Generated {escape(report.generated_at)}</span><br><span class="badge">Retained local runs only</span></div></div>
<section class="cards" aria-label="Key metrics">{card_html}</section>
<section class="panel coverage"><div><h2>How much of this is modeled?</h2>
<p>{report.modeled_runs:,} of {report.terminal:,} terminal executions have a baseline. Unknown savings are left uncounted.</p></div>
<div class="coverage-meter"><strong>{coverage:.0f}%</strong><progress value="{report.modeled_runs}" max="{max(1, report.terminal)}" aria-label="Baseline coverage">{coverage:.0f}%</progress></div></section>
<section class="panel">{chart(report)}</section>
<section class="panel"><div class="section-heading"><h2>Workflow contribution</h2><span class="muted">Exact versions · {len(report.workflows)} workflows</span></div>
<div class="scroll"><table><thead><tr><th>Workflow / baseline</th><th>Runs</th><th>Verified</th><th>Negative findings</th><th>Exec. errors</th><th>Est. net tokens</th></tr></thead><tbody>{rows}</tbody></table></div></section>
<section class="method"><div><p class="eyebrow">THE MATH, WITHOUT THE MAGIC</p><h2>Estimates you can audit.</h2>
<p>Manual equivalent <strong>{manual}</strong> − Owlmatic attempts <strong>{automated}</strong> − setup <strong>{setup}</strong> tokens.</p>
<p>Only verified tasks earn manual-equivalent credit. Every terminal attempt with a baseline incurs its configured Owlmatic cost.
Negative savings remain negative. A valid failed check counts as a verified negative finding; an execution error does not.</p></div>
<div><h3>What these numbers do not claim</h3><ul><li>Provider tokens and dollar savings are not measured.</li>
<li>Each baseline must include discovery, prompts, reasoning, retries, and inspected logs.</li>
<li>Full setup cost is charged once per modeled workflow version in this window. Do not add overlapping windows.</li>
<li>{report.excluded_validation_runs:,} validation runs and {report.excluded_legacy_runs:,} legacy runs with unknown purpose are excluded.</li>
<li>Retention deletes history. This is a current ledger snapshot, not lifetime accounting. Running states are not reconciled by this report.</li></ul></div></section>
<section class="panel setup"><h2>Calibrate your next estimate</h2><p>Use matched manual and Owlmatic sessions, then record a baseline for the exact workflow reference:</p>
<pre>owlmatic stats baseline '&lt;ref&gt;' --manual-tokens 8000 --owlmatic-tokens 800 --setup-tokens 20000 --source 'Planning estimate; replace with benchmark'</pre>
<p class="muted">Example assumptions only. Rebuild this snapshot with <code>owlmatic dashboard</code>; get structured data with <code>owlmatic stats --json</code>.</p></section>
</main><footer>Owlmatic · Think once. Run automatically.<span>No remote assets. No telemetry. No server.</span></footer></body></html>"""

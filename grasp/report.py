"""Render a ReviewReport for a terminal or as Markdown.

The terminal view is for the person running the tool. The Markdown view is
meant to be pasted into a pull request, so it uses headings and lists that
render cleanly on code hosts. Both group files by risk, highest first, so
attention goes where it is needed.
"""

from __future__ import annotations

from rich.console import Console

from .risk import RiskLevel
from .schema import FileReview, ReviewReport

_COLOR = {RiskLevel.HIGH: "red", RiskLevel.MEDIUM: "yellow", RiskLevel.LOW: "green"}
_WORD = {RiskLevel.HIGH: "HIGH", RiskLevel.MEDIUM: "MEDIUM", RiskLevel.LOW: "low"}


def _summary_line(report: ReviewReport) -> str:
    return (
        f"{len(report.files)} file(s): "
        f"{len(report.high)} high risk, {len(report.medium)} medium, {len(report.low)} low. "
        f"Overall: {report.overall_risk.label}."
    )


def render_terminal(console: Console, report: ReviewReport) -> None:
    console.rule("[bold]Grasp review[/bold]")
    console.print(_summary_line(report))
    if report.warnings:
        for warning in report.warnings:
            console.print(f"[dim]note: {warning}[/dim]")

    if not report.files:
        console.print("No changes found to review.")
        return

    for level in (RiskLevel.HIGH, RiskLevel.MEDIUM, RiskLevel.LOW):
        files = [f for f in report.files if f.risk is level]
        if not files:
            continue
        console.print()
        console.print(f"[bold {_COLOR[level]}]{_WORD[level]} risk[/bold {_COLOR[level]}]")
        for file in files:
            _render_file_terminal(console, file)


def _render_file_terminal(console: Console, file: FileReview) -> None:
    color = _COLOR[file.risk]
    console.print(
        f"  [bold]{file.path}[/bold] "
        f"[dim]({file.change_type.value}, +{file.added} -{file.removed})[/dim]"
    )
    for reason in file.risk_reasons:
        console.print(f"    [{color}].[/{color}] {reason}")
    if file.secret_lines:
        lines = ", ".join(map(str, file.secret_lines))
        console.print(f"    [red]![/red] possible secret on line(s) {lines}")

    narrative = file.narrative
    if narrative:
        console.print(f"    [dim]summary:[/dim] {narrative.summary}")
        if narrative.intent:
            console.print(f"    [dim]intent:[/dim] {narrative.intent}")
        if narrative.unverified:
            console.print("    [dim]not verified:[/dim]")
            for item in narrative.unverified:
                console.print(f"      - {item}")
        if narrative.questions:
            console.print("    [dim]questions:[/dim]")
            for question in narrative.questions:
                console.print(f"      - {question}")
    elif file.model_skipped_reason:
        console.print(f"    [dim]{file.model_skipped_reason}[/dim]")


def render_markdown(report: ReviewReport) -> str:
    out: list[str] = []
    out.append("## Grasp review")
    out.append("")
    out.append(_summary_line(report))
    out.append("")
    for warning in report.warnings:
        out.append(f"> note: {warning}")
    if report.warnings:
        out.append("")

    if not report.files:
        out.append("No changes found to review.")
        return "\n".join(out)

    for level in (RiskLevel.HIGH, RiskLevel.MEDIUM, RiskLevel.LOW):
        files = [f for f in report.files if f.risk is level]
        if not files:
            continue
        out.append(f"### {_WORD[level]} risk")
        out.append("")
        for file in files:
            out.extend(_render_file_markdown(file))
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def _render_file_markdown(file: FileReview) -> list[str]:
    lines: list[str] = []
    lines.append(f"**{file.path}** ({file.change_type.value}, +{file.added} -{file.removed})")
    for reason in file.risk_reasons:
        lines.append(f"- {reason}")
    if file.secret_lines:
        nums = ", ".join(map(str, file.secret_lines))
        lines.append(f"- possible secret on line(s) {nums}")

    narrative = file.narrative
    if narrative:
        lines.append("")
        lines.append(f"{narrative.summary}")
        if narrative.intent:
            lines.append("")
            lines.append(f"Intent: {narrative.intent}")
        if narrative.unverified:
            lines.append("")
            lines.append("Not verified:")
            for item in narrative.unverified:
                lines.append(f"- {item}")
        if narrative.questions:
            lines.append("")
            lines.append("Questions for the reviewer:")
            for question in narrative.questions:
                lines.append(f"- {question}")
    elif file.model_skipped_reason:
        lines.append(f"_{file.model_skipped_reason}_")
    return lines

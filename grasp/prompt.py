"""Prompt construction for the per file narrative.

The prompt gives the model the diff for one file, the deterministic risk
reasons as grounded facts, and a strict instruction to return only the
narrative fields. It is told to be honest about uncertainty and to focus
on what a reviewer should check, which is the behavior developers actually
want from a review assistant.
"""

from __future__ import annotations

from .diffparser import FileDiff, LineKind
from .risk import RiskAssessment

SYSTEM = (
    "You assist a human who is reviewing a code change. Your job is to help them "
    "understand it quickly and to point out what deserves attention. Be accurate and "
    "plain. Do not invent behavior that is not in the diff. If intent is unclear, say so "
    "rather than guessing. Prefer pointing out what to check over making confident claims. "
    "You do not decide risk levels; those are provided to you as facts. "
    "Return only the requested JSON object and nothing else."
)


def render_hunks(file: FileDiff) -> str:
    """Render the file's hunks with markers and new file line numbers."""
    out: list[str] = []
    for hunk in file.hunks:
        header = f"@@ hunk near line {hunk.new_start}"
        if hunk.section:
            header += f" ({hunk.section})"
        out.append(header)
        for line in hunk.lines:
            if line.kind is LineKind.ADDED:
                out.append(f"+ {line.new_lineno}: {line.text}")
            elif line.kind is LineKind.REMOVED:
                out.append(f"- {line.old_lineno}: {line.text}")
            else:
                out.append(f"  {line.new_lineno}: {line.text}")
    return "\n".join(out)


def build_user_prompt(file: FileDiff, assessment: RiskAssessment) -> str:
    facts = "; ".join(assessment.reasons)
    body = render_hunks(file)
    return (
        f"File: {file.path}\n"
        f"Change type: {file.change_type.value}\n"
        f"Added lines: {file.added}, removed lines: {file.removed}\n"
        f"Risk level already decided: {assessment.level.label}\n"
        f"Reasons for that risk: {facts}\n\n"
        f"Diff (a plus means an added line, a minus means a removed line, "
        f"numbers are new file line numbers):\n{body}\n\n"
        "Return a JSON object with these fields:\n"
        "summary: two or three sentences on what this change does.\n"
        "intent: one sentence on why it was likely made, or say it is unclear.\n"
        "unverified: a list of things the change assumes or does not prove, "
        "for example missing error handling, untested paths, or edge cases. "
        "Each item short and specific. Empty list if nothing stands out.\n"
        "questions: up to three short questions the reviewer should be able to answer, "
        "drawn from this diff."
    )

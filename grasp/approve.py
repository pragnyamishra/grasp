"""The approval step.

Nothing is ever posted to a pull request without the reviewer saying so.
This walks the findings one at a time, highest severity first, and asks.
That keeps a human in the loop at the only moment that matters, the moment
something leaves your machine and becomes visible to your team.

In a non interactive shell there is nobody to ask, so the run stops unless
the caller passed an explicit yes.
"""

from __future__ import annotations

import sys

from rich.console import Console

from .errors import InputError
from .publish import Candidate

_PROMPT = "  include this? [y]es / [n]o / [a]ll remaining / [q]uit: "

_SEVERITY_COLOR = {"high": "red", "medium": "yellow", "low": "green"}


def approve(
    candidates: list[Candidate],
    console: Console,
    *,
    assume_yes: bool = False,
) -> list[Candidate]:
    """Return the findings the reviewer approved for posting."""
    if not candidates:
        return []

    if assume_yes:
        return list(candidates)

    if not sys.stdin.isatty():
        raise InputError(
            "Grasp will not post without approval, and this shell is not interactive. "
            "Re-run in a terminal, or pass --yes to approve every finding up front."
        )

    console.print()
    console.rule("[bold]Approve what gets posted[/bold]")
    console.print(
        f"{len(candidates)} finding(s). Only the ones you approve are posted.\n"
    )

    approved: list[Candidate] = []
    for index, candidate in enumerate(candidates, start=1):
        _show(console, index, len(candidates), candidate)
        answer = _ask(console)
        if answer == "q":
            console.print("\n[dim]Stopped. Nothing will be posted.[/dim]")
            return []
        if answer == "a":
            approved.append(candidate)
            approved.extend(candidates[index:])
            console.print("\n[dim]Approving the rest.[/dim]")
            break
        if answer == "y":
            approved.append(candidate)

    return approved


def _show(console: Console, index: int, total: int, candidate: Candidate) -> None:
    finding = candidate.finding
    color = _SEVERITY_COLOR.get(finding.severity.label, "white")
    where = f"line {finding.line}" if finding.is_line_anchored else "file level"
    console.print(
        f"[dim]{index}/{total}[/dim] "
        f"[{color}]{finding.severity.label.upper()}[/{color}] "
        f"[bold]{candidate.path}[/bold] [dim]({where})[/dim]"
    )
    console.print(f"  {finding.message}")


def _ask(console: Console) -> str:
    while True:
        try:
            raw = input(_PROMPT).strip().lower()
        except EOFError:
            return "q"
        except KeyboardInterrupt:
            console.print()
            return "q"
        if raw in {"", "n", "no"}:
            return "n"
        if raw in {"y", "yes"}:
            return "y"
        if raw in {"a", "all"}:
            return "a"
        if raw in {"q", "quit"}:
            return "q"
        console.print("  [dim]Please answer y, n, a, or q.[/dim]")

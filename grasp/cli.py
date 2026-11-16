"""Command line entry point.

One command, review, with a few clear options. The default reviews the
staged change, which is the common case right before a commit. Every
expected failure becomes a short message and a stable exit code.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from . import __version__
from . import diffsource
from .config import load_settings
from .diffparser import parse_diff
from .errors import GraspError, InputError
from .model import HttpModelClient
from .report import render_markdown, render_terminal
from .review import review as run_review

app = typer.Typer(add_completion=False, help="Understand and triage a change before you merge it.")
console = Console()
err_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"grasp {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    _version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show the version."
    ),
) -> None:
    """Grasp: a reviewer's assistant for pull requests."""


def _get_diff_text(range_ref: Optional[str], file: Optional[Path], use_stdin: bool) -> str:
    chosen = [bool(range_ref), bool(file), use_stdin]
    if sum(chosen) > 1:
        raise InputError("Choose only one of --range, --file, or --stdin.")
    if range_ref:
        return diffsource.from_range(range_ref)
    if file:
        return diffsource.from_file(file)
    if use_stdin:
        return diffsource.from_stdin()
    return diffsource.from_staged()


@app.command()
def review(
    range_ref: Optional[str] = typer.Option(
        None, "--range", help="A git ref or range, for example main...HEAD."
    ),
    file: Optional[Path] = typer.Option(None, "--file", help="A patch file to review."),
    use_stdin: bool = typer.Option(False, "--stdin", help="Read a diff from standard input."),
    mode: str = typer.Option("auto", help="auto, local, or api."),
    fmt: str = typer.Option("text", "--format", help="text or md."),
    out: Optional[Path] = typer.Option(None, help="Write the report to this file instead of the screen."),
    no_model: bool = typer.Option(False, "--no-model", help="Risk triage only, no model."),
    allow_secrets: bool = typer.Option(
        False, "--allow-secrets", help="Redact detected secrets and continue in API mode."
    ),
    max_files: int = typer.Option(40, help="Most files to send to the model."),
    yes: bool = typer.Option(False, "--yes", help="Do not prompt for confirmation on a large diff."),
) -> None:
    """Review a change and report what to look at."""
    try:
        settings = load_settings(
            mode_override=mode,
            use_model=not no_model,
            max_files_override=max_files,
        )

        text = _get_diff_text(range_ref, file, use_stdin)
        if not text.strip():
            console.print("Nothing to review. There is no change here.")
            raise typer.Exit()

        if len(text.encode("utf-8", errors="ignore")) > settings.max_diff_bytes:
            size_mb = settings.max_diff_bytes / 1_000_000
            if not yes and sys.stdin.isatty():
                proceed = typer.confirm(
                    f"This diff is larger than {size_mb:.1f} MB and may be slow or costly. Continue?"
                )
                if not proceed:
                    raise typer.Exit()
            else:
                err_console.print(
                    f"[yellow]Warning: this diff is larger than {size_mb:.1f} MB.[/yellow]"
                )

        diff = parse_diff(text)
        if not diff.files:
            console.print("No files found in the diff.")
            raise typer.Exit()

        if settings.use_model:
            with HttpModelClient(settings) as client:
                report = run_review(diff, settings, client, allow_secrets=allow_secrets)
        else:
            report = run_review(diff, settings, None, allow_secrets=allow_secrets)

        _emit(report, fmt, out)

    except GraspError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exc.exit_code)


def _emit(report, fmt: str, out: Optional[Path]) -> None:
    if fmt not in {"text", "md"}:
        raise InputError("Format must be text or md.")
    if fmt == "md" or out is not None:
        text = render_markdown(report)
        if out is not None:
            try:
                out.write_text(text, encoding="utf-8")
            except OSError as exc:
                raise InputError(f"Could not write to {out}: {exc}") from exc
            console.print(f"Wrote the review to {out}")
        else:
            console.print(text)
    else:
        render_terminal(console, report)


if __name__ == "__main__":
    app()

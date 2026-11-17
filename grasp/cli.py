"""Command line entry point.

One command, review, with a few clear options. The default reviews the
staged change, which is the common case right before a commit. Pointing it
at a pull request and adding --post turns it into a reviewer's assistant
that leaves inline comments, but only on the findings you approve.

Every expected failure becomes a short message and a stable exit code.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from . import __version__
from . import diffsource, gh
from .approve import approve
from .config import load_settings
from .diffparser import Diff, parse_diff
from .errors import GraspError, InputError
from .model import HttpModelClient
from .publish import DEFAULT_MAX_COMMENTS, build_review, candidates
from .report import render_markdown, render_terminal
from .review import review as run_review
from .risk import rule_catalog
from .schema import ReviewReport

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


def _get_diff_text(
    range_ref: Optional[str],
    file: Optional[Path],
    use_stdin: bool,
    pr: Optional[int],
    repo: Optional[str],
) -> str:
    chosen = [bool(range_ref), bool(file), use_stdin, pr is not None]
    if sum(chosen) > 1:
        raise InputError("Choose only one of --pr, --range, --file, or --stdin.")
    if pr is not None:
        if not repo:
            raise InputError("--pr needs --repo, in the form owner/repo.")
        gh.ensure_available()
        return gh.pr_diff(repo, pr)
    if range_ref:
        return diffsource.from_range(range_ref)
    if file:
        return diffsource.from_file(file)
    if use_stdin:
        return diffsource.from_stdin()
    return diffsource.from_staged()


@app.command()
def review(
    pr: Optional[int] = typer.Option(None, "--pr", help="A pull request number to review."),
    repo: Optional[str] = typer.Option(None, "--repo", help="owner/repo, required with --pr."),
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
    post: bool = typer.Option(
        False, "--post", help="After approval, post the findings as inline comments on the pull request."
    ),
    approve_all: bool = typer.Option(
        False, "--approve-all", help="Skip the per finding prompt and approve every finding."
    ),
    max_comments: int = typer.Option(
        DEFAULT_MAX_COMMENTS, help="Most inline comments to post. The rest go in the review body."
    ),
) -> None:
    """Review a change and report what to look at."""
    try:
        settings = load_settings(
            mode_override=mode,
            use_model=not no_model,
            max_files_override=max_files,
        )

        repo_name = gh.normalize_repo(repo) if repo else None
        if post and pr is None:
            raise InputError("--post needs --pr and --repo, so Grasp knows where to comment.")

        text = _get_diff_text(range_ref, file, use_stdin, pr, repo_name)
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

        if post:
            _post_flow(
                report=report,
                diff=diff,
                repo=repo_name,
                pr=pr,
                max_comments=max_comments,
                approve_all=approve_all,
            )

    except GraspError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exc.exit_code)


def _post_flow(
    *,
    report: ReviewReport,
    diff: Diff,
    repo: Optional[str],
    pr: Optional[int],
    max_comments: int,
    approve_all: bool,
) -> None:
    """Ask which findings to post, then post them as one review."""
    if repo is None or pr is None:
        raise InputError("--post needs --pr and --repo.")

    offered = candidates(report)
    if not offered:
        console.print("\nNothing worth commenting on. No review posted.")
        return

    chosen = approve(offered, console, assume_yes=approve_all)
    if not chosen:
        console.print("\nNothing approved. No review posted.")
        return

    payload = build_review(chosen, report, diff, max_comments=max_comments)

    console.print()
    console.print(
        f"Ready to post to {repo} pull request {pr}: "
        f"{payload.inline_count} inline comment(s), "
        f"{len(payload.body_only)} note(s) in the review body."
    )
    if not approve_all and sys.stdin.isatty():
        if not typer.confirm("Post this review now?"):
            console.print("Not posted.")
            return

    gh.ensure_available()
    head_sha = gh.pr_head_sha(repo, pr)
    url = gh.post_review(repo, pr, payload.to_api(head_sha))
    if url:
        console.print(f"[green]Posted.[/green] {url}")
    else:
        console.print("[green]Posted.[/green]")


@app.command()
def rules() -> None:
    """List every rule, so you know what you can disable or re-grade."""
    try:
        settings = load_settings(use_model=False)
        catalog = rule_catalog(settings.rules)
        disabled = settings.rules.disabled

        console.rule("[bold]Rules[/bold]")
        console.print(
            "Disable or re-grade any of these in a .grasp.toml file. "
            "Nothing here is fixed.\n"
        )
        colors = {"high": "red", "medium": "yellow", "low": "green"}
        for kind in ("path", "line", "structural"):
            entries = [e for e in catalog if e[3] == kind]
            if not entries:
                continue
            console.print(f"[bold]{kind}[/bold]")
            for code, severity, message, _ in entries:
                color = colors[severity.label]
                off = " [dim](disabled)[/dim]" if code in disabled else ""
                console.print(
                    f"  [{color}]{severity.label:<6}[/{color}] "
                    f"[bold]{code:<22}[/bold] {message}{off}"
                )
            console.print()

        console.print(
            "[dim]Example:\n"
            "  [rules]\n"
            '  disable = ["todo", "plain-http"]\n\n'
            "  [rules.severity]\n"
            '  cast-any = "low"\n\n'
            "  [[rules.path]]\n"
            '  code = "path-tenant"\n'
            '  pattern = "(?i)(tenant|organisation)"\n'
            '  message = "touches tenant isolation"\n'
            '  severity = "high"[/dim]'
        )
    except GraspError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exc.exit_code)


def _emit(report: ReviewReport, fmt: str, out: Optional[Path]) -> None:
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

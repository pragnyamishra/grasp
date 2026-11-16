"""Orchestrate a full review.

The flow is: parse the diff, score every file deterministically, protect
secrets before anything leaves the machine, then ask the model for
narrative on the files that matter most, highest risk first. The model is
optional. Without it, the deterministic risk triage is still a complete and
useful result, which means the tool degrades gracefully and works offline.
"""

from __future__ import annotations

from typing import Optional

from dataclasses import replace

from .config import Settings
from .diffparser import ChangeType, Diff, FileDiff, Hunk, Line, LineKind
from .errors import ModelResponseError, ModelUnavailableError, SecretFoundError
from .grounding import secret_hits_in_added
from .model import ModelClient
from .prompt import SYSTEM, build_user_prompt
from .risk import RiskAssessment, RiskLevel, assess_file, overall_level
from .schema import FileNarrative, FileReview, ReviewReport

_REDACTION = "[redacted by grasp: possible secret removed before sending]"


def review(
    diff: Diff,
    settings: Settings,
    client: Optional[ModelClient],
    *,
    allow_secrets: bool = False,
) -> ReviewReport:
    """Produce a ReviewReport. Pass client=None for triage only."""
    assessments: dict[str, RiskAssessment] = {}
    secrets: dict[str, list[int]] = {}

    for file in diff.files:
        assessments[file.path] = assess_file(file)
        secrets[file.path] = secret_hits_in_added(file)

    # Protect the user before any content leaves the machine.
    if client is not None and settings.mode == "api":
        found = {path: lines for path, lines in secrets.items() if lines}
        if found and not allow_secrets:
            detail = "; ".join(f"{p} (line(s) {', '.join(map(str, ls))})" for p, ls in found.items())
            raise SecretFoundError(
                "Possible secrets found in the change, so nothing was sent to the cloud model: "
                f"{detail}. Remove them, use --mode local, or pass --allow-secrets to redact "
                "and continue."
            )

    reviews: list[FileReview] = []
    for file in diff.files:
        reviews.append(
            FileReview(
                path=file.path,
                change_type=file.change_type,
                risk=assessments[file.path].level,
                risk_reasons=assessments[file.path].reasons,
                added=file.added,
                removed=file.removed,
                is_binary=file.is_binary,
                secret_lines=secrets[file.path],
                findings=assessments[file.path].findings,
            )
        )

    if client is not None:
        _attach_narratives(diff, reviews, assessments, settings, client, allow_secrets)

    reviews.sort(key=lambda r: (-int(r.risk), -r.added, r.path))
    return ReviewReport(
        files=reviews,
        overall_risk=overall_level(list(assessments.values())),
        warnings=list(diff.warnings),
    )


def _attach_narratives(
    diff: Diff,
    reviews: list[FileReview],
    assessments: dict[str, RiskAssessment],
    settings: Settings,
    client: ModelClient,
    allow_secrets: bool,
) -> None:
    by_path = {f.path: f for f in diff.files}
    review_by_path = {r.path: r for r in reviews}

    candidates = [f for f in diff.files if f.hunks and not f.is_binary]
    candidates.sort(key=lambda f: (-int(assessments[f.path].level), -f.added))

    for review_item in reviews:
        file = by_path[review_item.path]
        if file.is_binary:
            review_item.model_skipped_reason = "binary file, nothing to explain"
        elif not file.hunks:
            review_item.model_skipped_reason = "no line changes to explain"

    for file in candidates[: settings.max_files]:
        review_item = review_by_path[file.path]
        prompt_file = file
        if settings.mode == "api" and allow_secrets and review_item.secret_lines:
            prompt_file = _redact(file, set(review_item.secret_lines))

        user = build_user_prompt(prompt_file, assessments[file.path])
        try:
            narrative = client.narrate(SYSTEM, user)
        except (ModelResponseError, ModelUnavailableError) as exc:
            # One bad file must not sink the whole review.
            review_item.model_skipped_reason = f"model unavailable for this file: {exc}"
            continue
        review_item.narrative = _verify_questions(narrative)

    for file in candidates[settings.max_files:]:
        review_by_path[file.path].model_skipped_reason = (
            "skipped to stay within the file limit; raise it with --max-files"
        )


def _verify_questions(narrative: FileNarrative) -> FileNarrative:
    # Keep at most three questions, drop empty strings, and trim stray items.
    questions = [q.strip() for q in narrative.questions if q and q.strip()][:3]
    unverified = [u.strip() for u in narrative.unverified if u and u.strip()]
    return narrative.model_copy(update={"questions": questions, "unverified": unverified})


def _redact(file: FileDiff, secret_lines: set[int]) -> FileDiff:
    """Return a copy of the file with added secret lines masked."""
    new_hunks: list[Hunk] = []
    for hunk in file.hunks:
        lines: list[Line] = []
        for line in hunk.lines:
            if line.kind is LineKind.ADDED and line.new_lineno in secret_lines:
                lines.append(replace(line, text=_REDACTION))
            else:
                lines.append(line)
        new_hunks.append(replace(hunk, lines=lines))
    return replace(file, hunks=new_hunks)

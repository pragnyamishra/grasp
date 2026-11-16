"""Turn approved findings into a single GitHub pull request review.

Design notes that matter when reading this:

- One review, not many comments. Every approved finding that can be pinned
  to a line becomes an inline comment, and GitHub groups them under one
  review. Findings that belong to the file as a whole go in the review body.
  A reviewer gets notes on the lines they are about, without the pull
  request being buried under separate top level comments.

- The review is always posted as a comment, never as an approval or a
  request for changes. Grasp informs the human; it does not cast a vote.

- Every inline comment is validated against the parsed diff first. GitHub's
  reviews endpoint is all or nothing, so a single comment on a line that is
  not part of the diff would reject the entire review. Anything that cannot
  be validated is moved into the body rather than silently dropped.

- Comment bodies never contain source text from the diff, only the finding
  messages. That keeps a secret finding safe to post in public.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .diffparser import Diff, FileDiff, LineKind
from .risk import SIDE_LEFT, SIDE_RIGHT, Finding, RiskLevel
from .schema import FileReview, ReviewReport

# GitHub accepts more than this, but a review with hundreds of inline
# comments is unreadable, so Grasp caps it and says what it left out.
DEFAULT_MAX_COMMENTS = 30

_MARKER = "<!-- posted-by: grasp -->"


@dataclass(frozen=True)
class Candidate:
    """A finding offered to the user for approval, with the file it is in."""

    path: str
    finding: Finding

    @property
    def label(self) -> str:
        where = f"line {self.finding.line}" if self.finding.is_line_anchored else "file level"
        return f"[{self.finding.severity.label}] {self.path} ({where}): {self.finding.message}"


def commentable_lines(file: FileDiff) -> set[tuple[int, str]]:
    """The (line, side) pairs GitHub will accept an inline comment on.

    Added and context lines are addressable on the new file side. Removed
    lines are addressable on the old file side.
    """
    out: set[tuple[int, str]] = set()
    for hunk in file.hunks:
        for line in hunk.lines:
            if line.kind is LineKind.ADDED and line.new_lineno is not None:
                out.add((line.new_lineno, SIDE_RIGHT))
            elif line.kind is LineKind.CONTEXT and line.new_lineno is not None:
                out.add((line.new_lineno, SIDE_RIGHT))
            elif line.kind is LineKind.REMOVED and line.old_lineno is not None:
                out.add((line.old_lineno, SIDE_LEFT))
    return out


def candidates(
    report: ReviewReport, min_severity: RiskLevel = RiskLevel.MEDIUM
) -> list[Candidate]:
    """Findings worth offering, highest severity first.

    Low severity findings, including the "nothing stood out" note, are not
    offered. There is no value in commenting on a pull request to say that a
    file looked fine.
    """
    out: list[Candidate] = []
    for file in report.files:
        for finding in file.findings:
            if finding.severity >= min_severity:
                out.append(Candidate(path=file.path, finding=finding))

    out.sort(
        key=lambda c: (
            -int(c.finding.severity),
            c.path,
            c.finding.line if c.finding.line is not None else 10**9,
            c.finding.code,
        )
    )
    return out


@dataclass
class ReviewPayload:
    body: str
    comments: list[dict]
    inline_count: int
    body_only: list[Candidate]

    def to_api(self, head_sha: str) -> dict:
        return {
            "commit_id": head_sha,
            "event": "COMMENT",
            "body": self.body,
            "comments": self.comments,
        }


def build_review(
    approved: list[Candidate],
    report: ReviewReport,
    diff: Diff,
    *,
    max_comments: int = DEFAULT_MAX_COMMENTS,
) -> ReviewPayload:
    """Group approved findings into inline comments plus a review body."""
    by_path: dict[str, FileDiff] = {f.path: f for f in diff.files}
    reviews_by_path: dict[str, FileReview] = {f.path: f for f in report.files}
    valid_lines: dict[str, set[tuple[int, str]]] = {
        path: commentable_lines(file) for path, file in by_path.items()
    }

    # Group line anchored findings so one line gets one comment.
    grouped: dict[tuple[str, int, str], list[Candidate]] = {}
    body_only: list[Candidate] = []

    for candidate in approved:
        finding = candidate.finding
        if not finding.is_line_anchored:
            body_only.append(candidate)
            continue
        line_no = int(finding.line)
        if (line_no, finding.side) not in valid_lines.get(candidate.path, set()):
            # The line is not part of the diff GitHub knows about, so an
            # inline comment there would be rejected. Keep it in the body.
            body_only.append(candidate)
            continue
        grouped.setdefault((candidate.path, line_no, finding.side), []).append(candidate)

    ordered_keys = sorted(
        grouped,
        key=lambda k: (
            -int(max(c.finding.severity for c in grouped[k])),
            k[0],
            k[1],
        ),
    )

    comments: list[dict] = []
    for key in ordered_keys[:max_comments]:
        path, line, side = key
        messages = [c.finding.message for c in grouped[key]]
        comments.append(
            {
                "path": path,
                "line": line,
                "side": side,
                "body": _comment_body(messages),
            }
        )

    # Anything past the cap is reported in the body rather than lost.
    overflow = [c for key in ordered_keys[max_comments:] for c in grouped[key]]
    body_only.extend(overflow)

    body = _build_body(approved, comments, body_only, report, reviews_by_path)
    return ReviewPayload(
        body=body, comments=comments, inline_count=len(comments), body_only=body_only
    )


def _comment_body(messages: list[str]) -> str:
    if len(messages) == 1:
        head = f"**Grasp:** {messages[0]}"
    else:
        bullets = "\n".join(f"- {m}" for m in messages)
        head = f"**Grasp** flagged this line:\n{bullets}"
    return f"{head}\n\n_Worth a look rather than a verdict._ {_MARKER}"


def _build_body(
    approved: list[Candidate],
    comments: list[dict],
    body_only: list[Candidate],
    report: ReviewReport,
    reviews_by_path: dict[str, FileReview],
) -> str:
    paths = sorted({c.path for c in approved})
    lines: list[str] = []
    lines.append("## Grasp review")
    lines.append("")
    lines.append(
        f"{len(approved)} finding(s) across {len(paths)} file(s), "
        f"approved by the reviewer. {len(comments)} posted on specific lines."
    )
    lines.append("")

    if body_only:
        lines.append("### Notes that are not tied to one line")
        lines.append("")
        for path in sorted({c.path for c in body_only}):
            lines.append(f"**{path}**")
            for candidate in [c for c in body_only if c.path == path]:
                suffix = ""
                if candidate.finding.is_line_anchored:
                    suffix = f" (near line {candidate.finding.line})"
                lines.append(
                    f"- [{candidate.finding.severity.label}] {candidate.finding.message}{suffix}"
                )
            lines.append("")

    narrated = [
        p for p in paths if reviews_by_path.get(p) and reviews_by_path[p].narrative
    ]
    if narrated:
        lines.append("### What these changes do")
        lines.append("")
        for path in narrated:
            narrative = reviews_by_path[path].narrative
            lines.append(f"**{path}**")
            lines.append("")
            lines.append(narrative.summary)
            if narrative.unverified:
                lines.append("")
                lines.append("Not verified:")
                for item in narrative.unverified:
                    lines.append(f"- {item}")
            if narrative.questions:
                lines.append("")
                lines.append("Questions:")
                for question in narrative.questions:
                    lines.append(f"- {question}")
            lines.append("")

    lines.append(
        "_Risk levels come from deterministic rules over the diff, not from a model. "
        "Grasp comments; it does not approve or block._ " + _MARKER
    )
    return "\n".join(lines).rstrip() + "\n"

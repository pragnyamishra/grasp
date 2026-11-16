"""Tests for turning approved findings into a GitHub review payload."""

from __future__ import annotations

from grasp.config import Settings
from grasp.diffparser import parse_diff
from grasp.publish import (
    Candidate,
    build_review,
    candidates,
    commentable_lines,
)
from grasp.risk import SIDE_LEFT, SIDE_RIGHT, Finding, RiskLevel
from grasp.review import review

_DIFF = (
    "diff --git a/auth/session.py b/auth/session.py\n"
    "--- a/auth/session.py\n"
    "+++ b/auth/session.py\n"
    "@@ -10,3 +10,5 @@\n"
    " def create_session(user):\n"
    "-    store(token)\n"
    "+    store(token, verify=False)\n"
    '+    API_KEY = "sk-abcdefghijklmnopqrstuvwxyz0123"\n'
    "     return token\n"
)


def _report(diff):
    return review(diff, Settings(mode="local", use_model=False), client=None)


def test_commentable_lines_cover_added_context_and_removed():
    diff = parse_diff(_DIFF)
    lines = commentable_lines(diff.files[0])
    # Added lines are addressable on the new file side.
    assert (12, SIDE_RIGHT) in lines
    assert (13, SIDE_RIGHT) in lines
    # The removed line is addressable on the old file side.
    assert (11, SIDE_LEFT) in lines


def test_findings_are_line_anchored():
    diff = parse_diff(_DIFF)
    report = _report(diff)
    file = report.files[0]
    anchored = {f.code: f.line for f in file.findings if f.is_line_anchored}
    # New file numbering: 10 is the def, 11 the changed store call, 12 the key.
    assert anchored["tls-off"] == 11
    assert anchored["secret"] == 12
    # A path based finding has no line.
    path_findings = [f for f in file.findings if f.code == "path-auth"]
    assert path_findings and path_findings[0].line is None


def test_candidates_skip_low_and_sort_high_first():
    diff = parse_diff(_DIFF)
    report = _report(diff)
    offered = candidates(report)
    assert offered
    assert all(c.finding.severity >= RiskLevel.MEDIUM for c in offered)
    assert offered[0].finding.severity is RiskLevel.HIGH


def test_build_review_groups_by_line_and_validates():
    diff = parse_diff(_DIFF)
    report = _report(diff)
    offered = candidates(report)
    payload = build_review(offered, report, diff)

    # Every inline comment points at a line that is really in the diff.
    valid = commentable_lines(diff.files[0])
    for comment in payload.comments:
        assert (comment["line"], comment["side"]) in valid

    # One comment per line, not one per finding.
    keys = [(c["path"], c["line"], c["side"]) for c in payload.comments]
    assert len(keys) == len(set(keys))

    # The file level finding could not be pinned, so it is in the body.
    assert any(c.finding.code == "path-auth" for c in payload.body_only)
    assert "Grasp review" in payload.body


def test_secret_value_never_appears_in_the_payload():
    diff = parse_diff(_DIFF)
    report = _report(diff)
    payload = build_review(candidates(report), report, diff)
    blob = payload.body + "".join(c["body"] for c in payload.comments)
    assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in blob


def test_unanchorable_line_falls_back_to_body():
    diff = parse_diff(_DIFF)
    report = _report(diff)
    # A finding pointing at a line that is not part of the diff.
    bogus = Candidate(
        path="auth/session.py",
        finding=Finding(
            code="made-up", message="not a real line", severity=RiskLevel.HIGH, line=9999
        ),
    )
    payload = build_review([bogus], report, diff)
    assert payload.comments == []
    assert payload.body_only == [bogus]


def test_max_comments_cap_moves_overflow_to_body():
    diff = parse_diff(_DIFF)
    report = _report(diff)
    offered = [c for c in candidates(report) if c.finding.is_line_anchored]
    assert len(offered) >= 2
    payload = build_review(offered, report, diff, max_comments=1)
    assert payload.inline_count == 1
    assert len(payload.body_only) >= 1


def test_to_api_shape():
    diff = parse_diff(_DIFF)
    report = _report(diff)
    payload = build_review(candidates(report), report, diff)
    api = payload.to_api("abc123")
    assert api["commit_id"] == "abc123"
    # Grasp comments; it never approves or requests changes.
    assert api["event"] == "COMMENT"
    assert isinstance(api["comments"], list)
    assert api["body"]

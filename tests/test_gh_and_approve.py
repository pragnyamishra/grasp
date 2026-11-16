"""Tests for the GitHub CLI wrapper helpers and the approval step."""

from __future__ import annotations

import pytest
from rich.console import Console

from grasp.approve import approve
from grasp.errors import InputError
from grasp.gh import normalize_repo
from grasp.publish import Candidate
from grasp.risk import Finding, RiskLevel


def _candidate(message: str = "something to check") -> Candidate:
    return Candidate(
        path="auth/session.py",
        finding=Finding(code="x", message=message, severity=RiskLevel.HIGH, line=12),
    )


def test_normalize_repo_accepts_owner_repo():
    assert normalize_repo("TechBuddyz/techbuddyz-frontend") == "TechBuddyz/techbuddyz-frontend"


def test_normalize_repo_accepts_a_url():
    assert (
        normalize_repo("https://github.com/TechBuddyz/techbuddyz-frontend")
        == "TechBuddyz/techbuddyz-frontend"
    )
    assert (
        normalize_repo("https://github.com/TechBuddyz/techbuddyz-frontend.git")
        == "TechBuddyz/techbuddyz-frontend"
    )


def test_normalize_repo_rejects_nonsense():
    with pytest.raises(InputError):
        normalize_repo("not a repo")
    with pytest.raises(InputError):
        normalize_repo("")


def test_approve_all_returns_everything():
    items = [_candidate("a"), _candidate("b")]
    out = approve(items, Console(), assume_yes=True)
    assert out == items


def test_approve_with_nothing_offered_returns_empty():
    assert approve([], Console(), assume_yes=False) == []


def test_approve_refuses_to_guess_in_a_non_interactive_shell():
    # Under pytest stdin is not a terminal, so there is nobody to ask.
    # Grasp must stop rather than post something nobody approved.
    with pytest.raises(InputError):
        approve([_candidate()], Console(), assume_yes=False)

"""Tests for secret detection and language detection."""

from __future__ import annotations

from grasp.diffparser import parse_diff
from grasp.grounding import language_for, secret_hits_in_added


def test_language_detection():
    assert language_for("src/app.py") == "Python"
    assert language_for("web/main.ts") == "TypeScript"
    assert language_for("Dockerfile") == "Unknown"
    assert language_for("schema.sql") == "SQL"


def test_secret_detected_only_in_added_lines():
    text = (
        "diff --git a/s.py b/s.py\n"
        "--- a/s.py\n"
        "+++ b/s.py\n"
        "@@ -1,2 +1,3 @@\n"
        " x = 1\n"
        '-old = "nothing"\n'
        '+password = "supersecretvalue"\n'
    )
    file = parse_diff(text).files[0]
    hits = secret_hits_in_added(file)
    # The added secret is new file line 2. The removed line does not take a new number.
    assert hits == [2]


def test_no_false_positive_on_plain_code():
    text = (
        "diff --git a/s.py b/s.py\n"
        "--- a/s.py\n"
        "+++ b/s.py\n"
        "@@ -1 +1,2 @@\n"
        " x = 1\n"
        "+total = price * quantity\n"
    )
    file = parse_diff(text).files[0]
    assert secret_hits_in_added(file) == []

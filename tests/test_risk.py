"""Tests for the deterministic risk engine."""

from __future__ import annotations

from grasp.diffparser import parse_diff
from grasp.risk import RiskLevel, assess_file


def _one(text: str):
    return parse_diff(text).files[0]


def test_sensitive_path_is_high():
    text = (
        "diff --git a/auth/login.py b/auth/login.py\n"
        "--- a/auth/login.py\n"
        "+++ b/auth/login.py\n"
        "@@ -1 +1,2 @@\n"
        " def login():\n"
        "+    return True\n"
    )
    assessment = assess_file(_one(text))
    assert assessment.level is RiskLevel.HIGH
    assert any("authentication" in r for r in assessment.reasons)


def test_payment_path_is_high():
    text = (
        "diff --git a/billing/charge.py b/billing/charge.py\n"
        "--- a/billing/charge.py\n"
        "+++ b/billing/charge.py\n"
        "@@ -1 +1,2 @@\n"
        " def charge():\n"
        "+    pass\n"
    )
    assert assess_file(_one(text)).level is RiskLevel.HIGH


def test_dangerous_content_is_high():
    text = (
        "diff --git a/util.py b/util.py\n"
        "--- a/util.py\n"
        "+++ b/util.py\n"
        "@@ -1 +1,2 @@\n"
        " import os\n"
        "+    os.system(user_input)\n"
    )
    assessment = assess_file(_one(text))
    assert assessment.level is RiskLevel.HIGH
    assert any("os.system" in r for r in assessment.reasons)


def test_disabled_tls_is_high():
    text = (
        "diff --git a/client.py b/client.py\n"
        "--- a/client.py\n"
        "+++ b/client.py\n"
        "@@ -1 +1,2 @@\n"
        " import requests\n"
        "+    requests.get(url, verify=False)\n"
    )
    assert assess_file(_one(text)).level is RiskLevel.HIGH


def test_secret_in_added_is_high():
    text = (
        "diff --git a/settings.py b/settings.py\n"
        "--- a/settings.py\n"
        "+++ b/settings.py\n"
        "@@ -1 +1,2 @@\n"
        " config = {}\n"
        '+API_KEY = "sk-abcdefghijklmnopqrstuvwxyz0123"\n'
    )
    assessment = assess_file(_one(text))
    assert assessment.level is RiskLevel.HIGH
    assert any("secret" in r for r in assessment.reasons)


def test_todo_is_medium():
    text = (
        "diff --git a/util.py b/util.py\n"
        "--- a/util.py\n"
        "+++ b/util.py\n"
        "@@ -1 +1,2 @@\n"
        " x = 1\n"
        "+    # TODO fix this later\n"
    )
    assert assess_file(_one(text)).level is RiskLevel.MEDIUM


def test_benign_change_is_low():
    text = (
        "diff --git a/readme_helper.py b/readme_helper.py\n"
        "--- a/readme_helper.py\n"
        "+++ b/readme_helper.py\n"
        "@@ -1 +1,2 @@\n"
        " greeting = 'hi'\n"
        "+farewell = 'bye'\n"
    )
    assessment = assess_file(_one(text))
    assert assessment.level is RiskLevel.LOW
    assert assessment.reasons


def test_large_change_is_at_least_medium():
    added = "".join(f"+line{i}\n" for i in range(400))
    text = (
        "diff --git a/big.py b/big.py\n"
        "--- a/big.py\n"
        "+++ b/big.py\n"
        "@@ -1 +1,401 @@\n"
        " start = 1\n" + added
    )
    assert assess_file(_one(text)).level >= RiskLevel.MEDIUM


def test_removed_test_assertion_is_medium():
    text = (
        "diff --git a/tests/test_app.py b/tests/test_app.py\n"
        "--- a/tests/test_app.py\n"
        "+++ b/tests/test_app.py\n"
        "@@ -1,2 +1 @@\n"
        " def test_x():\n"
        "-    assert result == 1\n"
    )
    assessment = assess_file(_one(text))
    assert assessment.level >= RiskLevel.MEDIUM

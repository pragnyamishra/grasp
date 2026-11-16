"""End to end tests for the review orchestration, using a fake model."""

from __future__ import annotations

import pytest

from grasp.config import Settings
from grasp.diffparser import parse_diff
from grasp.errors import ModelResponseError, SecretFoundError
from grasp.model import ModelClient
from grasp.report import render_markdown
from grasp.review import review
from grasp.risk import RiskLevel
from grasp.schema import FileNarrative


class FakeClient(ModelClient):
    """Records prompts and returns a canned narrative."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def narrate(self, system: str, user: str) -> FileNarrative:
        self.prompts.append(user)
        return FileNarrative(summary="Explains the change.", questions=["Is this tested?"])


class BrokenClient(ModelClient):
    def narrate(self, system: str, user: str) -> FileNarrative:
        raise ModelResponseError("boom")


_TWO_FILES = (
    "diff --git a/auth/login.py b/auth/login.py\n"
    "--- a/auth/login.py\n"
    "+++ b/auth/login.py\n"
    "@@ -1 +1,2 @@\n"
    " def login():\n"
    "+    return check()\n"
    "diff --git a/docs_helper.py b/docs_helper.py\n"
    "--- a/docs_helper.py\n"
    "+++ b/docs_helper.py\n"
    "@@ -1 +1,2 @@\n"
    " a = 1\n"
    "+b = 2\n"
)


def _settings(mode: str = "local", api_key: str | None = None) -> Settings:
    return Settings(mode=mode, api_key=api_key, use_model=True, max_files=40)


def test_triage_only_without_model():
    diff = parse_diff(_TWO_FILES)
    report = review(diff, _settings(), client=None)
    assert len(report.files) == 2
    assert report.overall_risk is RiskLevel.HIGH
    # High risk file comes first.
    assert report.files[0].path == "auth/login.py"
    assert report.files[0].narrative is None


def test_model_narrative_attached_and_sorted():
    diff = parse_diff(_TWO_FILES)
    client = FakeClient()
    report = review(diff, _settings(), client=client)
    assert report.files[0].path == "auth/login.py"
    assert report.files[0].narrative is not None
    assert report.files[0].narrative.summary == "Explains the change."
    # The high risk file was sent to the model first.
    assert "auth/login.py" in client.prompts[0]


def test_secret_blocks_in_api_mode():
    text = (
        "diff --git a/s.py b/s.py\n"
        "--- a/s.py\n"
        "+++ b/s.py\n"
        "@@ -1 +1,2 @@\n"
        " x = 1\n"
        '+token = "ghp_abcdefghijklmnopqrstuvwxyz0123"\n'
    )
    diff = parse_diff(text)
    with pytest.raises(SecretFoundError):
        review(diff, _settings(mode="api", api_key="k"), client=FakeClient())


def test_secret_redacted_when_allowed():
    text = (
        "diff --git a/s.py b/s.py\n"
        "--- a/s.py\n"
        "+++ b/s.py\n"
        "@@ -1 +1,2 @@\n"
        " x = 1\n"
        '+token = "ghp_abcdefghijklmnopqrstuvwxyz0123"\n'
    )
    diff = parse_diff(text)
    client = FakeClient()
    report = review(diff, _settings(mode="api", api_key="k"), client=client, allow_secrets=True)
    # The real secret must not appear in anything sent to the model.
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123" not in client.prompts[0]
    assert "redacted" in client.prompts[0]
    # It is still reported to the local user.
    assert report.files[0].secret_lines == [2]


def test_local_mode_does_not_block_on_secret():
    text = (
        "diff --git a/s.py b/s.py\n"
        "--- a/s.py\n"
        "+++ b/s.py\n"
        "@@ -1 +1,2 @@\n"
        " x = 1\n"
        '+token = "ghp_abcdefghijklmnopqrstuvwxyz0123"\n'
    )
    diff = parse_diff(text)
    # Local mode sends nothing to the cloud, so it should not raise.
    report = review(diff, _settings(mode="local"), client=FakeClient())
    assert report.files[0].secret_lines == [2]


def test_one_bad_file_does_not_sink_the_review():
    diff = parse_diff(_TWO_FILES)
    report = review(diff, _settings(), client=BrokenClient())
    # The run completes and records why narrative was missing.
    assert all(f.narrative is None for f in report.files)
    assert any(f.model_skipped_reason for f in report.files)


def test_binary_file_is_not_sent_to_model():
    text = (
        "diff --git a/logo.png b/logo.png\n"
        "Binary files a/logo.png and b/logo.png differ\n"
    )
    diff = parse_diff(text)
    client = FakeClient()
    report = review(diff, _settings(), client=client)
    assert client.prompts == []
    assert report.files[0].model_skipped_reason is not None


def test_max_files_limit_caps_model_calls():
    diff = parse_diff(_TWO_FILES)
    settings = Settings(mode="local", use_model=True, max_files=1)
    client = FakeClient()
    report = review(diff, settings, client=client)
    assert len(client.prompts) == 1
    skipped = [f for f in report.files if f.model_skipped_reason and "limit" in f.model_skipped_reason]
    assert len(skipped) == 1


def test_markdown_renders():
    diff = parse_diff(_TWO_FILES)
    report = review(diff, _settings(), client=FakeClient())
    md = render_markdown(report)
    assert "Grasp review" in md
    assert "auth/login.py" in md

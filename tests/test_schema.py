"""Tests for model output validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from grasp.schema import FileNarrative


def test_valid_narrative():
    narrative = FileNarrative.model_validate(
        {
            "summary": "Adds a retry around the network call.",
            "intent": "To handle flaky connections.",
            "unverified": ["No test covers the retry path."],
            "questions": ["What happens after the final retry fails?"],
        }
    )
    assert narrative.summary.startswith("Adds")
    assert len(narrative.questions) == 1


def test_summary_is_required():
    with pytest.raises(ValidationError):
        FileNarrative.model_validate({"intent": "x"})


def test_defaults_fill_optional_fields():
    narrative = FileNarrative.model_validate({"summary": "A small change."})
    assert narrative.unverified == []
    assert narrative.questions == []


def test_too_many_questions_rejected():
    with pytest.raises(ValidationError):
        FileNarrative.model_validate(
            {"summary": "x", "questions": ["a", "b", "c", "d", "e"]}
        )

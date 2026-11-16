"""The shapes of model output and of the assembled review.

The model is only trusted to produce narrative: a plain summary, the
intent, the things that were not verified, and a few questions. It never
produces the risk level, which comes from the deterministic engine. Using
pydantic for the model output means a malformed reply is rejected rather
than shown as if it were valid.
"""

from __future__ import annotations

from typing import Optional

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .diffparser import ChangeType
from .risk import Finding, RiskLevel


class FileNarrative(BaseModel):
    """What the model is asked to return for a single file. Validated strictly."""

    summary: str = Field(min_length=1)
    intent: str = ""
    unverified: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list, max_length=4)


@dataclass
class FileReview:
    """One file in the assembled review: facts plus optional narrative."""

    path: str
    change_type: ChangeType
    risk: RiskLevel
    risk_reasons: list[str]
    added: int
    removed: int
    is_binary: bool
    secret_lines: list[int] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    narrative: Optional[FileNarrative] = None
    model_skipped_reason: Optional[str] = None


@dataclass
class ReviewReport:
    """The full review across all files in the diff."""

    files: list[FileReview]
    overall_risk: RiskLevel
    warnings: list[str] = field(default_factory=list)

    @property
    def high(self) -> list[FileReview]:
        return [f for f in self.files if f.risk is RiskLevel.HIGH]

    @property
    def medium(self) -> list[FileReview]:
        return [f for f in self.files if f.risk is RiskLevel.MEDIUM]

    @property
    def low(self) -> list[FileReview]:
        return [f for f in self.files if f.risk is RiskLevel.LOW]

"""Deterministic risk triage.

The most valuable thing this tool does is tell a busy reviewer where to
look. That judgement must be trustworthy, so it is based on explicit rules
over the real diff, not on a model guess. Every risk level comes with the
concrete reasons that produced it, so a reviewer can agree or dismiss at a
glance. The model, when present, adds narrative on top of this, but never
sets the risk level.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum

from .diffparser import ChangeType, FileDiff, LineKind
from .grounding import secret_hits_in_added


class RiskLevel(IntEnum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2

    @property
    def label(self) -> str:
        return {0: "low", 1: "medium", 2: "high"}[int(self)]


@dataclass
class RiskAssessment:
    level: RiskLevel
    reasons: list[str]


# Sensitive areas, matched against the file path. A hit here means a human
# should look carefully, because a mistake is expensive.
_SENSITIVE_PATH = [
    (re.compile(r"(?i)(auth|login|logout|signin|signup|session|oauth|sso|jwt|password|credential)"),
     "touches authentication or sessions"),
    (re.compile(r"(?i)(permission|authoriz|access[_-]?control|\bacl\b|\brole\b|\badmin\b)"),
     "touches authorization or access control"),
    (re.compile(r"(?i)(payment|billing|invoice|charge|refund|checkout|stripe|paypal|wallet|ledger)"),
     "touches payments or money"),
    (re.compile(r"(?i)(crypto|encrypt|decrypt|secret|vault|kms|signing|certificate)"),
     "touches cryptography or secrets"),
    (re.compile(r"(?i)(migration|schema|\bddl\b|\bdao\b|repository|models?\.py|database|/db/)"),
     "touches the data layer or a migration"),
    (re.compile(r"(?i)(terraform|\.tf$|dockerfile|kubernetes|k8s|helm|/deploy|/infra|\.github/workflows)"),
     "touches infrastructure or deployment"),
]

# Dangerous shapes in added code. Matched only against added lines.
_DANGEROUS_CONTENT = [
    (re.compile(r"\beval\s*\("), "uses eval on dynamic input"),
    (re.compile(r"\bexec\s*\("), "uses exec on dynamic input"),
    (re.compile(r"os\.system\s*\("), "shells out with os.system"),
    (re.compile(r"subprocess\.[A-Za-z_]+\([^)]*shell\s*=\s*True"), "runs a shell with shell=True"),
    (re.compile(r"pickle\.loads?\s*\("), "deserializes with pickle, which can execute code"),
    (re.compile(r"yaml\.load\s*\((?![^)]*Loader)"), "uses unsafe yaml.load without a safe loader"),
    (re.compile(r"(?i)(verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true)"),
     "disables TLS certificate verification"),
    (re.compile(r"(?i)execute\s*\(\s*[f\"']?.*(\+|%|\{).*(select|insert|update|delete)", re.DOTALL),
     "may build SQL by string concatenation"),
    (re.compile(r"(?i)\bhttp://"), "uses a plain http URL"),
]

# Medium weight shapes worth a second look but not alarming on their own.
_CAUTION_CONTENT = [
    (re.compile(r"(?i)except\s*:\s*$|except\s+Exception\s*:\s*(pass|\.\.\.)?\s*$"),
     "swallows errors with a broad except"),
    (re.compile(r"(?i)#\s*(todo|fixme|hack|xxx)"), "leaves a TODO or FIXME behind"),
    (re.compile(r"(?i)(console\.log|print)\s*\(.*(token|password|secret|key)", ),
     "may log a sensitive value"),
]

_TEST_PATH = re.compile(r"(?i)(^|/)(tests?|__tests__|spec)(/|\.|_)|(_test|\.test|\.spec)\.")

# A change larger than this many added lines is hard to review well.
_LARGE_ADDED = 300


def assess_file(file: FileDiff) -> RiskAssessment:
    """Return the risk level and the reasons behind it for one file."""
    reasons: list[str] = []
    level = RiskLevel.LOW

    path = file.path
    added = file.added_text()

    # A secret in added code is the single clearest high risk signal.
    if secret_hits_in_added(file):
        reasons.append("adds something that looks like a secret or key")
        level = RiskLevel.HIGH

    for pattern, reason in _SENSITIVE_PATH:
        if pattern.search(path):
            reasons.append(reason)
            level = max(level, RiskLevel.HIGH)

    for pattern, reason in _DANGEROUS_CONTENT:
        if pattern.search(added):
            reasons.append(reason)
            level = max(level, RiskLevel.HIGH)

    for pattern, reason in _CAUTION_CONTENT:
        if pattern.search(added):
            reasons.append(reason)
            level = max(level, RiskLevel.MEDIUM)

    if file.change_type is ChangeType.DELETED and _TEST_PATH.search(path):
        reasons.append("deletes a test file, which can hide a regression")
        level = max(level, RiskLevel.MEDIUM)

    if _removes_test_assertions(file):
        reasons.append("removes test assertions")
        level = max(level, RiskLevel.MEDIUM)

    if file.added > _LARGE_ADDED:
        reasons.append(f"is large, with {file.added} added lines, which is hard to review")
        level = max(level, RiskLevel.MEDIUM)

    if not reasons:
        reasons.append("no sensitive areas or risky patterns detected")

    # De duplicate while preserving order, since paths can match twice.
    seen: set[str] = set()
    unique = [r for r in reasons if not (r in seen or seen.add(r))]
    return RiskAssessment(level=level, reasons=unique)


def _removes_test_assertions(file: FileDiff) -> bool:
    if not _TEST_PATH.search(file.path):
        return False
    assert_re = re.compile(r"(?i)\b(assert|expect|should)\b")
    for hunk in file.hunks:
        for line in hunk.lines:
            if line.kind is LineKind.REMOVED and assert_re.search(line.text):
                return True
    return False


def overall_level(assessments: list[RiskAssessment]) -> RiskLevel:
    if not assessments:
        return RiskLevel.LOW
    return max(a.level for a in assessments)

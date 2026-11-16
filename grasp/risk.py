"""Deterministic risk triage, as line anchored findings.

The most valuable thing this tool does is tell a busy reviewer where to
look. That judgement must be trustworthy, so it is based on explicit rules
over the real diff, not on a model guess.

Every rule produces a Finding. A Finding carries the line it belongs to
whenever the rule found it on a specific line, which is what lets Grasp
post it as an inline comment on exactly that line. Rules that are about
the file as a whole, such as "this path touches authentication", carry no
line and are reported at file level instead of being pinned to an
arbitrary line.

Finding messages never include the matched source text. That is deliberate:
a secret finding must be safe to post publicly on a pull request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

from .diffparser import ChangeType, FileDiff, Line, LineKind
from .grounding import looks_secret


class RiskLevel(IntEnum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2

    @property
    def label(self) -> str:
        return {0: "low", 1: "medium", 2: "high"}[int(self)]


# Which side of the diff a line lives on, in GitHub's terms.
SIDE_RIGHT = "RIGHT"  # the new file: added and context lines
SIDE_LEFT = "LEFT"  # the old file: removed lines


@dataclass(frozen=True)
class Finding:
    """One concrete thing a reviewer should look at."""

    code: str
    message: str
    severity: RiskLevel
    line: Optional[int] = None
    side: str = SIDE_RIGHT

    @property
    def is_line_anchored(self) -> bool:
        return self.line is not None


@dataclass
class RiskAssessment:
    level: RiskLevel
    findings: list[Finding] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        """The finding messages, de duplicated, in order."""
        seen: set[str] = set()
        out: list[str] = []
        for finding in self.findings:
            if finding.message not in seen:
                seen.add(finding.message)
                out.append(finding.message)
        return out


# Sensitive areas, matched against the file path. These are properties of
# the file, not of one line, so they carry no line number.
_SENSITIVE_PATH: list[tuple[str, re.Pattern[str], str]] = [
    (
        "path-auth",
        re.compile(r"(?i)(auth|login|logout|signin|signup|session|oauth|sso|jwt|password|credential)"),
        "touches authentication or sessions",
    ),
    (
        "path-authz",
        re.compile(r"(?i)(permission|authoriz|access[_-]?control|\bacl\b|\brole\b|\badmin\b)"),
        "touches authorization or access control",
    ),
    (
        "path-money",
        re.compile(r"(?i)(payment|billing|invoice|charge|refund|checkout|stripe|paypal|wallet|ledger)"),
        "touches payments or money",
    ),
    (
        "path-crypto",
        re.compile(r"(?i)(crypto|encrypt|decrypt|secret|vault|kms|signing|certificate)"),
        "touches cryptography or secrets",
    ),
    (
        "path-data",
        re.compile(r"(?i)(migration|schema|\bddl\b|\bdao\b|repository|models?\.py|database|/db/)"),
        "touches the data layer or a migration",
    ),
    (
        "path-infra",
        re.compile(r"(?i)(terraform|\.tf$|dockerfile|kubernetes|k8s|helm|/deploy|/infra|\.github/workflows)"),
        "touches infrastructure or deployment",
    ),
]

# Dangerous shapes, matched against a single added line so the finding can
# point at that line. The lookbehind on exec and eval keeps method calls
# such as a regular expression's .exec() from being mistaken for the
# dangerous builtin, which is a common false positive in JavaScript.
_DANGEROUS_CONTENT: list[tuple[str, re.Pattern[str], str]] = [
    ("exec-call", re.compile(r"(?<![\w.$])exec\s*\("), "calls exec on dynamic input"),
    ("eval-call", re.compile(r"(?<![\w.$])eval\s*\("), "calls eval on dynamic input"),
    ("os-system", re.compile(r"os\.system\s*\("), "shells out with os.system"),
    (
        "shell-true",
        re.compile(r"subprocess\.[A-Za-z_]+\([^)]*shell\s*=\s*True"),
        "runs a shell with shell=True",
    ),
    ("pickle", re.compile(r"pickle\.loads?\s*\("), "deserializes with pickle, which can execute code"),
    (
        "unsafe-yaml",
        re.compile(r"yaml\.load\s*\((?![^)]*Loader)"),
        "uses unsafe yaml.load without a safe loader",
    ),
    (
        "tls-off",
        re.compile(r"(?i)(verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true)"),
        "disables TLS certificate verification",
    ),
    (
        "sql-concat",
        re.compile(r"(?i)execute\s*\(\s*[f\"'`].*(\+|%s*['\"]|\$\{).*(select|insert|update|delete)"),
        "may build SQL by string concatenation",
    ),
]

# Worth a second look, but not alarming on its own.
_CAUTION_CONTENT: list[tuple[str, re.Pattern[str], str]] = [
    (
        "broad-except",
        re.compile(r"(?i)except\s*:\s*$|except\s+Exception\s*:\s*(pass|\.\.\.)?\s*$"),
        "swallows errors with a broad except",
    ),
    ("todo", re.compile(r"(?i)(#|//)\s*(todo|fixme|hack|xxx)\b"), "leaves a TODO or FIXME behind"),
    (
        "log-secret",
        re.compile(r"(?i)(console\.log|print)\s*\(.*(token|password|secret|api[_-]?key)"),
        "may log a sensitive value",
    ),
    (
        "plain-http",
        re.compile(r"(?i)\bhttp://(?!localhost|127\.0\.0\.1|0\.0\.0\.0)"),
        "uses a plain http URL",
    ),
]

_TEST_PATH = re.compile(r"(?i)(^|/)(tests?|__tests__|spec)(/|\.|_)|(_test|\.test|\.spec)\.")
_ASSERT_RE = re.compile(r"(?i)\b(assert|expect|should)\b")

# A change larger than this many added lines is hard to review well.
_LARGE_ADDED = 300


def assess_file(file: FileDiff) -> RiskAssessment:
    """Return the risk level and the findings behind it for one file."""
    findings: list[Finding] = []

    findings.extend(_path_findings(file.path))
    findings.extend(_line_findings(file))
    findings.extend(_structural_findings(file))

    if not findings:
        findings.append(
            Finding(
                code="clear",
                message="no sensitive areas or risky patterns detected",
                severity=RiskLevel.LOW,
            )
        )

    level = max(f.severity for f in findings)
    return RiskAssessment(level=level, findings=_dedupe(findings))


def _path_findings(path: str) -> list[Finding]:
    out: list[Finding] = []
    for code, pattern, message in _SENSITIVE_PATH:
        if pattern.search(path):
            out.append(Finding(code=code, message=message, severity=RiskLevel.HIGH))
    return out


def _line_findings(file: FileDiff) -> list[Finding]:
    """Scan each added line on its own so findings can point at that line."""
    out: list[Finding] = []
    for line in file.added_lines():
        if line.new_lineno is None:
            continue
        text = line.text

        if looks_secret(text):
            out.append(
                Finding(
                    code="secret",
                    message="adds something that looks like a secret or key",
                    severity=RiskLevel.HIGH,
                    line=line.new_lineno,
                )
            )

        for code, pattern, message in _DANGEROUS_CONTENT:
            if pattern.search(text):
                out.append(
                    Finding(
                        code=code,
                        message=message,
                        severity=RiskLevel.HIGH,
                        line=line.new_lineno,
                    )
                )

        for code, pattern, message in _CAUTION_CONTENT:
            if pattern.search(text):
                out.append(
                    Finding(
                        code=code,
                        message=message,
                        severity=RiskLevel.MEDIUM,
                        line=line.new_lineno,
                    )
                )
    return out


def _structural_findings(file: FileDiff) -> list[Finding]:
    out: list[Finding] = []
    path = file.path

    if file.change_type is ChangeType.DELETED and _TEST_PATH.search(path):
        out.append(
            Finding(
                code="test-deleted",
                message="deletes a test file, which can hide a regression",
                severity=RiskLevel.MEDIUM,
            )
        )

    if _TEST_PATH.search(path):
        for line in _removed_lines(file):
            if line.old_lineno is not None and _ASSERT_RE.search(line.text):
                out.append(
                    Finding(
                        code="assert-removed",
                        message="removes a test assertion",
                        severity=RiskLevel.MEDIUM,
                        line=line.old_lineno,
                        side=SIDE_LEFT,
                    )
                )

    if file.added > _LARGE_ADDED:
        out.append(
            Finding(
                code="large-change",
                message=f"is large, with {file.added} added lines, which is hard to review",
                severity=RiskLevel.MEDIUM,
            )
        )
    return out


def _removed_lines(file: FileDiff) -> list[Line]:
    return [ln for hunk in file.hunks for ln in hunk.lines if ln.kind is LineKind.REMOVED]


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """Drop exact repeats, keeping the first occurrence and its order."""
    seen: set[tuple[str, Optional[int], str]] = set()
    out: list[Finding] = []
    for finding in findings:
        key = (finding.code, finding.line, finding.side)
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return out


def overall_level(assessments: list[RiskAssessment]) -> RiskLevel:
    if not assessments:
        return RiskLevel.LOW
    return max(a.level for a in assessments)

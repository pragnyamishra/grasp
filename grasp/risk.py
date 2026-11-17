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

Three principles keep this useful:

- This is not a linter. ESLint, Ruff and friends already enforce style and
  catch unused variables. A rule belongs here only if a human reviewer
  should personally stop and think about it. Rules that a reviewer would
  scroll past are worse than no rule, because they train people to ignore
  the output.

- Precision over recall. A rule that fires on a common harmless pattern
  gets cut, even if it would occasionally catch something real. Every rule
  below has a negative test proving the harmless form does not trigger it.

- Nothing is fixed. The rules below are defaults, not the law. Every one can
  be disabled, re-graded or added to from configuration, because what counts
  as sensitive differs per codebase. One team's money code lives in
  /billing, another's in /transactions, and neither should have to edit this
  file. See RuleConfig.

Finding messages never include the matched source text. That is deliberate:
a secret finding must be safe to post publicly on a pull request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Mapping, Optional

from .diffparser import ChangeType, FileDiff, Line, LineKind
from .grounding import looks_secret


class RiskLevel(IntEnum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2

    @property
    def label(self) -> str:
        return {0: "low", 1: "medium", 2: "high"}[int(self)]


LEVEL_BY_NAME = {"low": RiskLevel.LOW, "medium": RiskLevel.MEDIUM, "high": RiskLevel.HIGH}

# Which side of the diff a line lives on, in GitHub's terms.
SIDE_RIGHT = "RIGHT"  # the new file: added and context lines
SIDE_LEFT = "LEFT"  # the old file: removed lines

DEFAULT_LARGE_ADDED = 300


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


@dataclass(frozen=True)
class PathRule:
    """A property of the file as a whole, matched against its path."""

    code: str
    pattern: re.Pattern[str]
    message: str
    severity: RiskLevel


@dataclass(frozen=True)
class LineRule:
    """A pattern looked for on a single added line.

    ``unless`` is an escape hatch for the cases where one pattern is only
    interesting in the absence of another on the same line, for example a
    link that opens a new tab without the attribute that makes it safe.
    """

    code: str
    pattern: re.Pattern[str]
    message: str
    severity: RiskLevel
    unless: Optional[re.Pattern[str]] = None

    def matches(self, text: str) -> bool:
        if not self.pattern.search(text):
            return False
        if self.unless is not None and self.unless.search(text):
            return False
        return True


@dataclass(frozen=True)
class RuleConfig:
    """How the rules behave for this codebase.

    This is what keeps Grasp from being tuned to whoever wrote it. A team
    can silence a rule that is noisy for them, re-grade one that matters
    more or less in their context, add patterns for their own domain, and
    move the thresholds, all without touching Python.
    """

    disabled: frozenset = frozenset()
    severity_overrides: Mapping[str, RiskLevel] = field(default_factory=dict)
    large_added: int = DEFAULT_LARGE_ADDED
    extra_path_rules: tuple = ()
    extra_line_rules: tuple = ()

    def enabled(self, code: str) -> bool:
        return code not in self.disabled

    def severity(self, code: str, default: RiskLevel) -> RiskLevel:
        return self.severity_overrides.get(code, default)

    def path_rules(self) -> list[PathRule]:
        return [r for r in (*DEFAULT_PATH_RULES, *self.extra_path_rules) if self.enabled(r.code)]

    def line_rules(self) -> list[LineRule]:
        return [r for r in (*DEFAULT_LINE_RULES, *self.extra_line_rules) if self.enabled(r.code)]


DEFAULT_RULE_CONFIG = RuleConfig()

# A line that is purely a comment. Used to stop rules from firing on prose.
_COMMENT_ONLY = re.compile(r"^\s*(//|#|\*|/\*)")

DEFAULT_PATH_RULES: tuple = (
    PathRule(
        "path-auth",
        re.compile(r"(?i)(auth|login|logout|signin|signup|session|oauth|sso|jwt|password|credential)"),
        "touches authentication or sessions",
        RiskLevel.HIGH,
    ),
    PathRule(
        "path-authz",
        re.compile(r"(?i)(permission|authoriz|access[_-]?control|\bacl\b|\brole\b|\badmin\b)"),
        "touches authorization or access control",
        RiskLevel.HIGH,
    ),
    PathRule(
        "path-money",
        re.compile(r"(?i)(payment|billing|invoice|charge|refund|checkout|stripe|paypal|wallet|ledger)"),
        "touches payments or money",
        RiskLevel.HIGH,
    ),
    PathRule(
        "path-crypto",
        re.compile(r"(?i)(crypto|encrypt|decrypt|secret|vault|kms|signing|certificate)"),
        "touches cryptography or secrets",
        RiskLevel.HIGH,
    ),
    PathRule(
        "path-data",
        re.compile(r"(?i)(migration|schema|\bddl\b|\bdao\b|repository|models?\.py|database|/db/)"),
        "touches the data layer or a migration",
        RiskLevel.HIGH,
    ),
    PathRule(
        "path-infra",
        re.compile(r"(?i)(terraform|\.tf$|dockerfile|kubernetes|k8s|helm|/deploy|/infra|\.github/workflows)"),
        "touches infrastructure or deployment",
        RiskLevel.HIGH,
    ),
    PathRule(
        "path-build",
        re.compile(
            r"(?i)((^|/)(next|vite|webpack|rollup|babel|jest|vitest)\.config\.[a-z]+$"
            r"|(^|/)tsconfig[\w.]*\.json$)"
        ),
        "changes build or compiler configuration, which affects the whole app",
        RiskLevel.MEDIUM,
    ),
)

DEFAULT_LINE_RULES: tuple = (
    # Code execution, server side and general.
    LineRule(
        "exec-call",
        # The lookbehind keeps a regular expression's .exec() from being
        # mistaken for the dangerous builtin, a common false positive in
        # JavaScript and TypeScript.
        re.compile(r"(?<![\w.$])exec\s*\("),
        "calls exec on dynamic input",
        RiskLevel.HIGH,
    ),
    LineRule(
        "eval-call",
        re.compile(r"(?<![\w.$])eval\s*\("),
        "calls eval on dynamic input",
        RiskLevel.HIGH,
    ),
    LineRule(
        "new-function",
        re.compile(r"\bnew\s+Function\s*\("),
        "builds a function from a string, which executes code",
        RiskLevel.HIGH,
    ),
    LineRule(
        "os-system",
        re.compile(r"os\.system\s*\("),
        "shells out with os.system",
        RiskLevel.HIGH,
    ),
    LineRule(
        "shell-true",
        re.compile(r"subprocess\.[A-Za-z_]+\([^)]*shell\s*=\s*True"),
        "runs a shell with shell=True",
        RiskLevel.HIGH,
    ),
    LineRule(
        "pickle",
        re.compile(r"pickle\.loads?\s*\("),
        "deserializes with pickle, which can execute code",
        RiskLevel.HIGH,
    ),
    LineRule(
        "unsafe-yaml",
        re.compile(r"yaml\.load\s*\((?![^)]*Loader)"),
        "uses unsafe yaml.load without a safe loader",
        RiskLevel.HIGH,
    ),
    LineRule(
        "tls-off",
        re.compile(r"(?i)(verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true)"),
        "disables TLS certificate verification",
        RiskLevel.HIGH,
    ),
    LineRule(
        "sql-concat",
        re.compile(r"(?i)execute\s*\(\s*[f\"'`].*(\+|%s*['\"]|\$\{).*(select|insert|update|delete)"),
        "may build SQL by string concatenation",
        RiskLevel.HIGH,
    ),
    # Browser side cross site scripting vectors.
    LineRule(
        "react-dangerous-html",
        re.compile(r"dangerouslySetInnerHTML"),
        "sets raw HTML in the page, which is the main cross site scripting risk in React",
        RiskLevel.HIGH,
    ),
    LineRule(
        "inner-html",
        re.compile(r"\.(inner|outer)HTML\s*="),
        "assigns raw HTML, which can introduce cross site scripting",
        RiskLevel.HIGH,
    ),
    LineRule(
        "document-write",
        re.compile(r"document\.write(ln)?\s*\("),
        "writes directly into the document, which can introduce cross site scripting",
        RiskLevel.HIGH,
    ),
    # Secrets that reach the browser bundle. These prefixes are compiled into
    # client side code by Next, Vite and Create React App, so anything
    # genuinely secret behind them is published to every visitor.
    LineRule(
        "public-env-secret",
        re.compile(r"(NEXT_PUBLIC_|VITE_|REACT_APP_)[A-Z0-9_]*(SECRET|PASSWORD|PASSWD|PRIVATE)"),
        "puts a secret in a public env var, which ships to the browser bundle",
        RiskLevel.HIGH,
    ),
    LineRule(
        "public-env-key",
        # KEY and TOKEN are ambiguous, because publishable keys legitimately
        # live here, so this is a glance rather than an alarm.
        re.compile(r"(NEXT_PUBLIC_|VITE_|REACT_APP_)[A-Z0-9_]*(KEY|TOKEN)"),
        "exposes a key or token to the browser bundle, confirm it is meant to be public",
        RiskLevel.MEDIUM,
    ),
    LineRule(
        "token-in-storage",
        re.compile(r"(?i)(localStorage|sessionStorage)\.setItem\s*\([^)]*(token|jwt|auth|credential|password)"),
        "stores an auth value in browser storage, where any cross site scripting bug can read it",
        RiskLevel.HIGH,
    ),
    # Quality signals a reviewer should know about.
    LineRule(
        "ts-ignore",
        re.compile(r"@ts-(ignore|expect-error)"),
        "turns off type checking on this line",
        RiskLevel.MEDIUM,
    ),
    LineRule(
        "test-only",
        re.compile(r"\b(describe|it|test|context)\.only\s*\("),
        "uses .only, which silently skips the rest of the test suite",
        RiskLevel.MEDIUM,
    ),
    LineRule(
        "cast-any",
        re.compile(r"\bas\s+any\b"),
        "casts to any, which switches off type safety here",
        RiskLevel.MEDIUM,
        unless=_COMMENT_ONLY,
    ),
    LineRule(
        "blank-target",
        re.compile(r"""target\s*=\s*['"{]?_blank"""),
        'opens a new tab without rel="noopener", which is a tabnabbing risk',
        RiskLevel.MEDIUM,
        unless=re.compile(r"noopener"),
    ),
    LineRule(
        "empty-catch",
        re.compile(r"catch\s*(\([^)]*\))?\s*\{\s*\}"),
        "swallows the error with an empty catch",
        RiskLevel.MEDIUM,
    ),
    LineRule(
        "broad-except",
        re.compile(r"(?i)except\s*:\s*$|except\s+Exception\s*:\s*(pass|\.\.\.)?\s*$"),
        "swallows errors with a broad except",
        RiskLevel.MEDIUM,
    ),
    LineRule(
        "todo",
        re.compile(r"(?i)(#|//)\s*(todo|fixme|hack|xxx)\b"),
        "leaves a TODO or FIXME behind",
        RiskLevel.MEDIUM,
    ),
    LineRule(
        "log-secret",
        re.compile(r"(?i)(console\.log|print)\s*\(.*(token|password|secret|api[_-]?key)"),
        "may log a sensitive value",
        RiskLevel.MEDIUM,
    ),
    LineRule(
        "plain-http",
        re.compile(r"(?i)\bhttp://(?!localhost|127\.0\.0\.1|0\.0\.0\.0)"),
        "uses a plain http URL",
        RiskLevel.MEDIUM,
        unless=_COMMENT_ONLY,
    ),
)

# Rules that are not a single pattern, so they are listed here for the
# catalogue and so they can be disabled and re-graded like any other.
STRUCTURAL_RULES: tuple = (
    ("secret", RiskLevel.HIGH, "a line that looks like it holds a secret or key"),
    ("env-file", RiskLevel.HIGH, "a committed environment file, ignoring sample files"),
    ("dependency-change", RiskLevel.MEDIUM, "a dependency added or changed in package.json"),
    ("test-deleted", RiskLevel.MEDIUM, "a deleted test file"),
    ("assert-removed", RiskLevel.MEDIUM, "a removed test assertion"),
    ("large-change", RiskLevel.MEDIUM, "a change too large to review reliably"),
)

_TEST_PATH = re.compile(r"(?i)(^|/)(tests?|__tests__|spec)(/|\.|_)|(_test|\.test|\.spec)\.")
_ASSERT_RE = re.compile(r"(?i)\b(assert|expect|should)\b")

# An env file in a diff is almost always a mistake. Sample files are fine.
_ENV_FILE = re.compile(r"(^|/)\.env(?!\.(example|sample|template|dist))(\.|$)")

_PKG_JSON = re.compile(r"(^|/)package\.json$")
_DEP_LINE = re.compile(r'"[^"]+"\s*:\s*"[\^~>=<]*\d')
# Fields that look like a dependency line but are not one. Without this, a
# routine version bump would be reported as a dependency change.
_NON_DEP_KEY = re.compile(
    r'"(version|name|description|license|main|module|types|typings|engines|packageManager)"\s*:'
)


def rule_catalog(config: Optional[RuleConfig] = None) -> list[tuple]:
    """Every rule code with its severity and description.

    Exposed so `grasp rules` can list them, which is what makes disabling a
    rule by code discoverable rather than guesswork.
    """
    config = config or DEFAULT_RULE_CONFIG
    out: list[tuple] = []
    for rule in (*DEFAULT_PATH_RULES, *config.extra_path_rules):
        out.append((rule.code, config.severity(rule.code, rule.severity), rule.message, "path"))
    for rule in (*DEFAULT_LINE_RULES, *config.extra_line_rules):
        out.append((rule.code, config.severity(rule.code, rule.severity), rule.message, "line"))
    for code, severity, description in STRUCTURAL_RULES:
        out.append((code, config.severity(code, severity), description, "structural"))
    return out


def all_rule_codes(config: Optional[RuleConfig] = None) -> set:
    return {entry[0] for entry in rule_catalog(config)}


def assess_file(file: FileDiff, config: Optional[RuleConfig] = None) -> RiskAssessment:
    """Return the risk level and the findings behind it for one file."""
    config = config or DEFAULT_RULE_CONFIG
    findings: list[Finding] = []

    findings.extend(_path_findings(file.path, config))
    findings.extend(_line_findings(file, config))
    findings.extend(_structural_findings(file, config))

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


def _finding(
    config: RuleConfig,
    code: str,
    message: str,
    severity: RiskLevel,
    line: Optional[int] = None,
    side: str = SIDE_RIGHT,
) -> Optional[Finding]:
    """Build a Finding unless the rule is switched off, applying any re-grade."""
    if not config.enabled(code):
        return None
    return Finding(
        code=code,
        message=message,
        severity=config.severity(code, severity),
        line=line,
        side=side,
    )


def _path_findings(path: str, config: RuleConfig) -> list[Finding]:
    out: list[Finding] = []
    for rule in config.path_rules():
        if rule.pattern.search(path):
            found = _finding(config, rule.code, rule.message, rule.severity)
            if found:
                out.append(found)
    return out


def _line_findings(file: FileDiff, config: RuleConfig) -> list[Finding]:
    """Scan each added line on its own so findings can point at that line."""
    out: list[Finding] = []
    rules = config.line_rules()
    for line in file.added_lines():
        if line.new_lineno is None:
            continue
        text = line.text

        if looks_secret(text):
            found = _finding(
                config,
                "secret",
                "adds something that looks like a secret or key",
                RiskLevel.HIGH,
                line.new_lineno,
            )
            if found:
                out.append(found)

        for rule in rules:
            if rule.matches(text):
                found = _finding(config, rule.code, rule.message, rule.severity, line.new_lineno)
                if found:
                    out.append(found)
    return out


def _structural_findings(file: FileDiff, config: RuleConfig) -> list[Finding]:
    out: list[Finding] = []
    path = file.path

    if _ENV_FILE.search(path):
        found = _finding(
            config,
            "env-file",
            "changes a committed environment file, which usually should not be in the repo",
            RiskLevel.HIGH,
        )
        if found:
            out.append(found)

    if _PKG_JSON.search(path):
        for line in file.added_lines():
            if _NON_DEP_KEY.search(line.text):
                continue
            if line.new_lineno is not None and _DEP_LINE.search(line.text):
                found = _finding(
                    config,
                    "dependency-change",
                    "adds or changes a dependency, which is worth a supply chain glance",
                    RiskLevel.MEDIUM,
                    line.new_lineno,
                )
                if found:
                    out.append(found)

    if file.change_type is ChangeType.DELETED and _TEST_PATH.search(path):
        found = _finding(
            config,
            "test-deleted",
            "deletes a test file, which can hide a regression",
            RiskLevel.MEDIUM,
        )
        if found:
            out.append(found)

    if _TEST_PATH.search(path):
        for line in _removed_lines(file):
            if line.old_lineno is not None and _ASSERT_RE.search(line.text):
                found = _finding(
                    config,
                    "assert-removed",
                    "removes a test assertion",
                    RiskLevel.MEDIUM,
                    line.old_lineno,
                    SIDE_LEFT,
                )
                if found:
                    out.append(found)

    if file.added > config.large_added:
        found = _finding(
            config,
            "large-change",
            f"is large, with {file.added} added lines, which is hard to review",
            RiskLevel.MEDIUM,
        )
        if found:
            out.append(found)
    return out


def _removed_lines(file: FileDiff) -> list[Line]:
    return [ln for hunk in file.hunks for ln in hunk.lines if ln.kind is LineKind.REMOVED]


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """Drop exact repeats, keeping the first occurrence and its order."""
    seen: set = set()
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

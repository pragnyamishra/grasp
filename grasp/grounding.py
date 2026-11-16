"""Facts the tool can establish without a model.

Grounding keeps the review honest. Secret detection protects the user
before any code leaves the machine. Language detection and size limits
keep the model request sane. None of this depends on the model, so it is
reliable and explainable.
"""

from __future__ import annotations

import re

from .diffparser import FileDiff, LineKind

# Patterns are intentionally conservative. A false positive here costs the
# user a glance. A false negative could leak a secret, so we err toward
# flagging. Each pattern targets a shape, not a specific product.
_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|passwd|password|client[_-]?secret)\s*[:=]\s*['\"][^'\"]{6,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),              # AWS access key id
    re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*\S{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),           # common secret key shape
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),          # github token
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # slack token
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

_EXT_LANGUAGE = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".java": "Java",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".php": "PHP",
    ".cs": "C#",
    ".c": "C",
    ".cpp": "C++",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".sql": "SQL",
    ".sh": "Shell",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".tf": "Terraform",
}


def language_for(path: str) -> str:
    lower = path.lower()
    for ext, lang in _EXT_LANGUAGE.items():
        if lower.endswith(ext):
            return lang
    return "Unknown"


def looks_secret(text: str) -> bool:
    """True if a single line looks like it contains a secret or key."""
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def secret_hits_in_added(file: FileDiff) -> list[int]:
    """Return the new file line numbers of added lines that look like secrets.

    Only added lines are checked, because a removed secret is being taken
    out, which is good, and a context line was already in the code.
    """
    hits: list[int] = []
    for hunk in file.hunks:
        for line in hunk.lines:
            if line.kind is LineKind.ADDED and looks_secret(line.text):
                if line.new_lineno is not None:
                    hits.append(line.new_lineno)
    return hits

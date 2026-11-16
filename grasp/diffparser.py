"""A defensive parser for unified (git) diffs.

Real diffs are messier than the textbook format. This parser handles new,
deleted, renamed, copied, mode changed, and binary files, hunks with or
without counts, the "no newline at end of file" marker, multiple files,
plain non git diffs, and quoted paths. Anything it cannot place is
recorded as a warning rather than crashing the run, because a reviewer is
better served by a partial, honest result than by an exception.
"""

from __future__ import annotations

from typing import Optional

import re
from dataclasses import dataclass, field
from enum import Enum

_HUNK_RE = re.compile(r"^@@+ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@+(.*)$")
_DIFF_GIT_RE = re.compile(r"^diff --git (.+) (.+)$")


class ChangeType(str, Enum):
    ADDED = "added"
    DELETED = "deleted"
    MODIFIED = "modified"
    RENAMED = "renamed"
    COPIED = "copied"
    MODE_CHANGED = "mode_changed"
    BINARY = "binary"
    UNKNOWN = "unknown"


class LineKind(str, Enum):
    CONTEXT = "context"
    ADDED = "added"
    REMOVED = "removed"


@dataclass
class Line:
    kind: LineKind
    text: str
    old_lineno: Optional[int]
    new_lineno: Optional[int]


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section: str
    lines: list[Line] = field(default_factory=list)


@dataclass
class FileDiff:
    old_path: Optional[str]
    new_path: Optional[str]
    change_type: ChangeType = ChangeType.UNKNOWN
    is_binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)
    added: int = 0
    removed: int = 0
    similarity: Optional[int] = None

    @property
    def path(self) -> str:
        """The best single name to show for this file."""
        return self.new_path or self.old_path or "(unknown)"

    def added_lines(self) -> list[Line]:
        return [ln for hunk in self.hunks for ln in hunk.lines if ln.kind is LineKind.ADDED]

    def added_text(self) -> str:
        return "\n".join(ln.text for ln in self.added_lines())


@dataclass
class Diff:
    files: list[FileDiff] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _strip_prefix(path: str) -> Optional[str]:
    """Turn a diff path such as a/foo or b/foo or /dev/null into a real path."""
    path = path.strip()
    if path.startswith('"') and path.endswith('"') and len(path) >= 2:
        path = path[1:-1]
    if path == "/dev/null":
        return None
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def parse_diff(text: str) -> Diff:
    """Parse unified diff text into a structured Diff."""
    diff = Diff()
    current: Optional[FileDiff] = None
    hunk: Optional[Hunk] = None
    old_no = 0
    new_no = 0

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]

        git_match = _DIFF_GIT_RE.match(raw)
        if git_match:
            current = _start_file(diff, git_match)
            hunk = None
            i += 1
            continue

        # A plain, non git unified diff starts straight at the --- line.
        if raw.startswith("--- ") and current is None:
            current = FileDiff(old_path=None, new_path=None)
            diff.files.append(current)

        if current is None:
            # Preamble such as commit headers from format-patch. Ignore.
            i += 1
            continue

        if raw.startswith("old mode ") or raw.startswith("new mode "):
            if current.change_type is ChangeType.UNKNOWN:
                current.change_type = ChangeType.MODE_CHANGED
        elif raw.startswith("new file mode"):
            current.change_type = ChangeType.ADDED
        elif raw.startswith("deleted file mode"):
            current.change_type = ChangeType.DELETED
        elif raw.startswith("rename from "):
            current.change_type = ChangeType.RENAMED
            current.old_path = _strip_prefix(raw[len("rename from "):])
        elif raw.startswith("rename to "):
            current.change_type = ChangeType.RENAMED
            current.new_path = _strip_prefix(raw[len("rename to "):])
        elif raw.startswith("copy from "):
            current.change_type = ChangeType.COPIED
            current.old_path = _strip_prefix(raw[len("copy from "):])
        elif raw.startswith("copy to "):
            current.change_type = ChangeType.COPIED
            current.new_path = _strip_prefix(raw[len("copy to "):])
        elif raw.startswith("similarity index "):
            current.similarity = _parse_percent(raw)
        elif raw.startswith("Binary files ") or raw.startswith("GIT binary patch"):
            current.is_binary = True
            current.change_type = ChangeType.BINARY
        elif raw.startswith("--- "):
            # A value of None means /dev/null, which marks an added file.
            current.old_path = _strip_prefix(raw[4:])
        elif raw.startswith("+++ "):
            # A value of None means /dev/null, which marks a deleted file.
            current.new_path = _strip_prefix(raw[4:])
        elif raw.startswith("@@"):
            match = _HUNK_RE.match(raw)
            if not match:
                diff.warnings.append(f"Skipped a malformed hunk header: {raw[:60]}")
                i += 1
                continue
            hunk, old_no, new_no = _start_hunk(current, match)
        elif hunk is not None and raw[:1] in (" ", "+", "-"):
            old_no, new_no = _consume_line(current, hunk, raw, old_no, new_no)
        elif raw.startswith("\\"):
            # "\ No newline at end of file". A marker, not content.
            pass
        elif raw.startswith("index "):
            pass  # The blob index line. Nothing to do.
        else:
            if hunk is not None and raw == "":
                # Some tools emit a bare empty line as a context line.
                _consume_line(current, hunk, " ", old_no, new_no)

        i += 1

    _finalize(diff)
    return diff


def _start_file(diff: Diff, match: re.Match[str]) -> FileDiff:
    old_raw, new_raw = match.group(1), match.group(2)
    file = FileDiff(old_path=_strip_prefix(old_raw), new_path=_strip_prefix(new_raw))
    diff.files.append(file)
    return file


def _parse_percent(raw: str) -> Optional[int]:
    digits = re.search(r"(\d+)%", raw)
    return int(digits.group(1)) if digits else None


def _start_hunk(current: FileDiff, match: re.Match[str]) -> tuple[Hunk, int, int]:
    old_start = int(match.group(1))
    old_count = int(match.group(2)) if match.group(2) is not None else 1
    new_start = int(match.group(3))
    new_count = int(match.group(4)) if match.group(4) is not None else 1
    hunk = Hunk(
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
        section=match.group(5).strip(),
    )
    current.hunks.append(hunk)
    return hunk, old_start, new_start


def _consume_line(
    current: FileDiff, hunk: Hunk, raw: str, old_no: int, new_no: int
) -> tuple[int, int]:
    marker, body = raw[:1], raw[1:]
    if marker == " ":
        hunk.lines.append(Line(LineKind.CONTEXT, body, old_no, new_no))
        return old_no + 1, new_no + 1
    if marker == "-":
        hunk.lines.append(Line(LineKind.REMOVED, body, old_no, None))
        current.removed += 1
        return old_no + 1, new_no
    if marker == "+":
        hunk.lines.append(Line(LineKind.ADDED, body, None, new_no))
        current.added += 1
        return old_no, new_no + 1
    return old_no, new_no


def _finalize(diff: Diff) -> None:
    for file in diff.files:
        if file.change_type in (ChangeType.UNKNOWN,) and file.hunks:
            file.change_type = ChangeType.MODIFIED
        if file.change_type is ChangeType.UNKNOWN and not file.hunks and not file.is_binary:
            # A header only entry, for example a pure mode change we did not tag.
            file.change_type = ChangeType.MODE_CHANGED

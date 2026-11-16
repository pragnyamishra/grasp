"""Obtain the diff text to review.

A diff can come from four places: the staged change in the current git
repository, a range of git refs, a patch file on disk, or standard input.
This module only fetches the raw text. Parsing happens in diffparser.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .errors import InputError

# A context of three lines is the git default and gives the model enough
# surrounding code to explain a change without sending the whole file.
_UNIFIED = "--unified=3"


def _run_git(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise InputError("git is not installed or not on the PATH.") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or "").strip() or "git reported an error."
        raise InputError(f"git failed: {message}") from exc
    return result.stdout


def _ensure_git_repo() -> None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise InputError("git is not installed or not on the PATH.") from exc
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise InputError(
            "This does not look like a git repository. "
            "Run grasp from inside one, or pass a patch file with --file."
        )


def from_staged() -> str:
    """Return the staged change, which is what you are about to commit."""
    _ensure_git_repo()
    # --diff-filter excludes nothing here on purpose. We want deletions too,
    # so the reviewer sees what was removed. The parser handles each type.
    return _run_git(["diff", "--cached", _UNIFIED])


def from_range(ref: str) -> str:
    """Return the diff for a ref or a range such as main...HEAD.

    A three dot range means the change on the branch since it diverged,
    which is what a pull request shows. A two dot range or a single ref
    are passed through to git unchanged.
    """
    _ensure_git_repo()
    if not ref or not ref.strip():
        raise InputError("The range is empty. Pass something like main...HEAD.")
    return _run_git(["diff", _UNIFIED, ref])


def from_file(path: Path) -> str:
    """Return the contents of a patch file on disk."""
    if not path.exists():
        raise InputError(f"No patch file found at {path}")
    if path.is_dir():
        raise InputError(f"{path} is a directory, not a patch file.")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InputError(f"Could not read {path}: {exc}") from exc
    if b"\x00" in raw:
        raise InputError(f"{path} looks like a binary file, not a patch.")
    return raw.decode("utf-8", errors="replace")


def from_stdin() -> str:
    """Return a diff piped in on standard input."""
    if sys.stdin.isatty():
        raise InputError("No diff was piped in on standard input.")
    return sys.stdin.read()

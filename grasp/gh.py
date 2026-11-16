"""A thin wrapper around the GitHub CLI.

Grasp deliberately does not implement its own GitHub authentication. If you
can run `gh`, you are already authenticated, including when you keep several
accounts and switch between them. That means Grasp never asks for, stores,
or transmits a GitHub token of its own, which is one less secret to manage
and one less thing a reviewer of this code has to trust.

The trade off is that posting a review requires the GitHub CLI to be
installed and logged in. That is checked up front with a clear message
rather than failing deep inside a request.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Optional

from .errors import InputError

_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_URL_REPO_RE = re.compile(r"github\.com[/:]([A-Za-z0-9._-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?$")


def normalize_repo(value: str) -> str:
    """Accept either owner/repo or a full GitHub URL and return owner/repo."""
    value = (value or "").strip()
    if not value:
        raise InputError("A repository is required, in the form owner/repo.")
    if _REPO_RE.match(value):
        return value
    match = _URL_REPO_RE.search(value)
    if match:
        return match.group(1)
    raise InputError(f"Could not read '{value}' as a repository. Use the form owner/repo.")


def ensure_available() -> None:
    """Fail early and clearly if the GitHub CLI is missing or logged out."""
    if shutil.which("gh") is None:
        raise InputError(
            "The GitHub CLI is not installed, so Grasp cannot talk to GitHub. "
            "Install it from https://cli.github.com and run 'gh auth login'."
        )
    result = subprocess.run(
        ["gh", "auth", "status"], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise InputError(
            "The GitHub CLI is not logged in. Run 'gh auth login' first. "
            "If you use several accounts, pick one with 'gh auth switch'."
        )


def _run(args: list[str], stdin: Optional[str] = None) -> str:
    try:
        result = subprocess.run(
            ["gh", *args],
            input=stdin,
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise InputError(
            "The GitHub CLI is not installed. Install it from https://cli.github.com"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        detail = detail.splitlines()[0] if detail else "gh reported an error."
        raise InputError(f"GitHub CLI failed: {detail}") from exc
    return result.stdout


def pr_diff(repo: str, number: int) -> str:
    """Return the unified diff for a pull request."""
    return _run(["pr", "diff", str(number), "--repo", repo])


def pr_head_sha(repo: str, number: int) -> str:
    """Return the head commit of a pull request, which a review must pin to."""
    raw = _run(
        ["pr", "view", str(number), "--repo", repo, "--json", "headRefOid"]
    )
    try:
        data = json.loads(raw)
        sha = data["headRefOid"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise InputError(
            f"Could not read the head commit of pull request {number} in {repo}."
        ) from exc
    if not sha:
        raise InputError(f"Pull request {number} in {repo} has no head commit.")
    return str(sha)


def post_review(repo: str, number: int, payload: dict) -> str:
    """Create a single pull request review, with its inline comments.

    The reviews endpoint is all or nothing: if one inline comment points at a
    line GitHub does not consider part of the diff, the whole review is
    rejected. That is why every comment is validated against the parsed diff
    before this is called.
    """
    body = json.dumps(payload)
    out = _run(
        [
            "api",
            f"repos/{repo}/pulls/{number}/reviews",
            "--method",
            "POST",
            "--input",
            "-",
        ],
        stdin=body,
    )
    try:
        data = json.loads(out)
        return str(data.get("html_url") or "")
    except json.JSONDecodeError:
        return ""

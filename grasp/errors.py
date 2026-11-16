"""Typed errors that map cleanly to exit codes and friendly messages.

Every failure a user can reasonably cause is represented here so the CLI
layer can show a short, clear message and return a stable exit code,
instead of leaking a stack trace. Unexpected failures are allowed to
propagate, because those are bugs we want to see.
"""

from __future__ import annotations


class GraspError(Exception):
    """Base class for all expected, user facing failures."""

    exit_code: int = 1


class InputError(GraspError):
    """The input was missing, unreadable, or not what the command expected."""

    exit_code = 2


class ModelUnavailableError(GraspError):
    """No model could be reached in the selected mode."""

    exit_code = 3


class ModelResponseError(GraspError):
    """The model replied, but not with something we could use."""

    exit_code = 4


class SecretFoundError(GraspError):
    """A likely secret was found and would have been sent to a cloud model."""

    exit_code = 5


class ConfigError(GraspError):
    """The configuration file or environment was invalid."""

    exit_code = 6

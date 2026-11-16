"""Settings and how they are resolved.

Grasp works with zero configuration. A small optional file named
``.grasp.toml`` in the current directory or the home directory can set
defaults. The API key is never read from the config file or a command
line flag, only from the environment variable ``GRASP_API_KEY``, so that
keys are never written to disk or shell history by this tool.
"""

from __future__ import annotations

from typing import Optional

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

import httpx

from .errors import ConfigError, ModelUnavailableError

API_KEY_ENV = "GRASP_API_KEY"
CONFIG_NAME = ".grasp.toml"


@dataclass(frozen=True)
class Settings:
    """Everything the engine needs to run one review."""

    mode: str = "auto"  # auto, local, or api
    local_model: str = "qwen2.5-coder"
    local_endpoint: str = "http://localhost:11434"
    api_model: str = "gpt-4o-mini"
    api_endpoint: str = "https://api.openai.com/v1"
    api_key: Optional[str] = None
    request_timeout: float = 60.0
    retries: int = 3
    max_files: int = 40
    max_diff_bytes: int = 2_000_000
    confirm_large_inputs: bool = True
    use_model: bool = True
    # File level fields below are populated at resolve time only.
    _config_sources: tuple[str, ...] = field(default=(), repr=False)


_ALLOWED_KEYS = {
    "mode",
    "local_model",
    "local_endpoint",
    "api_model",
    "api_endpoint",
    "request_timeout",
    "retries",
    "max_files",
    "max_diff_bytes",
    "confirm_large_inputs",
}


def _candidate_config_paths() -> list[Path]:
    here = Path.cwd() / CONFIG_NAME
    home = Path.home() / CONFIG_NAME
    # The project level file wins over the home level file.
    return [p for p in (here, home) if p.is_file()]


def _toml_module():
    """Return a TOML reader. Uses the standard library on 3.11+, else tomli.

    Imported lazily so that users without a config file never need TOML at
    all, which keeps the tool usable on Python 3.9 and 3.10 out of the box.
    """
    try:
        import tomllib

        return tomllib
    except ModuleNotFoundError:
        try:
            import tomli

            return tomli
        except ModuleNotFoundError as exc:
            raise ConfigError(
                "Reading .grasp.toml needs Python 3.11 or newer, or the 'tomli' "
                "package. Install tomli, or remove the config file to use defaults."
            ) from exc


def _read_config_file(path: Path) -> dict:
    toml = _toml_module()
    try:
        with path.open("rb") as handle:
            data = toml.load(handle)
    except toml.TOMLDecodeError as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not open {path}: {exc}") from exc

    unknown = set(data) - _ALLOWED_KEYS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ConfigError(f"Unknown settings in {path}: {names}")
    return data


def _local_is_reachable(endpoint: str, timeout: float = 1.5) -> bool:
    """Return True if a local model server answers quickly."""
    try:
        resp = httpx.get(endpoint, timeout=timeout)
        return resp.status_code < 500
    except httpx.HTTPError:
        return False


def load_settings(
    *,
    mode_override: Optional[str] = None,
    use_model: Optional[bool] = None,
    max_files_override: Optional[int] = None,
) -> Settings:
    """Build Settings from defaults, the config file, and the environment.

    Resolution order, lowest priority first: dataclass defaults, then the
    config file, then environment and explicit overrides. ``auto`` mode is
    resolved to ``local`` or ``api`` based on what is actually available.
    """
    settings = Settings()

    sources: list[str] = []
    for path in _candidate_config_paths():
        data = _read_config_file(path)
        settings = replace(settings, **data)
        sources.append(str(path))

    settings = replace(settings, _config_sources=tuple(sources))

    key = os.environ.get(API_KEY_ENV) or None
    settings = replace(settings, api_key=key)

    if mode_override and mode_override != "auto":
        settings = replace(settings, mode=mode_override)
    if use_model is not None:
        settings = replace(settings, use_model=use_model)
    if max_files_override is not None:
        settings = replace(settings, max_files=max_files_override)

    if not settings.use_model:
        # No model needed, so mode does not matter. Leave it as is.
        return settings

    settings = _resolve_mode(settings)
    _validate(settings)
    return settings


def _resolve_mode(settings: Settings) -> Settings:
    if settings.mode == "auto":
        if _local_is_reachable(settings.local_endpoint):
            return replace(settings, mode="local")
        if settings.api_key:
            return replace(settings, mode="api")
        raise ModelUnavailableError(
            "No model is available. Start a local model, for example with "
            "'ollama serve', or set "
            f"{API_KEY_ENV} and use --mode api. "
            "You can also run with --no-model for risk triage only."
        )
    return settings


def _validate(settings: Settings) -> None:
    if settings.mode not in {"local", "api"}:
        raise ConfigError(f"Unknown mode '{settings.mode}'. Use auto, local, or api.")
    if settings.mode == "api" and not settings.api_key:
        raise ModelUnavailableError(
            f"API mode needs a key. Set the {API_KEY_ENV} environment variable, "
            "or use --mode local, or run with --no-model."
        )
    if settings.request_timeout <= 0:
        raise ConfigError("request_timeout must be positive.")
    if settings.retries < 1:
        raise ConfigError("retries must be at least 1.")
    if settings.max_files < 1:
        raise ConfigError("max_files must be at least 1.")

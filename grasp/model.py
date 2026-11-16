"""The model client.

The client is an interface with one method, narrate, so the rest of the
code never depends on a specific provider and tests can supply a fake. The
HTTP implementation talks to a local model server or an OpenAI compatible
API, enforces JSON output, retries transient failures, and attempts one
repair when the reply is not valid before giving up with a clear error.
"""

from __future__ import annotations

from typing import Optional

import json
import time
from abc import ABC, abstractmethod

import httpx
from pydantic import ValidationError

from .config import Settings
from .errors import ModelResponseError, ModelUnavailableError
from .schema import FileNarrative


class ModelClient(ABC):
    @abstractmethod
    def narrate(self, system: str, user: str) -> FileNarrative:
        """Return a validated narrative for one file."""


def _validate(raw: str) -> FileNarrative:
    if not raw or not raw.strip():
        raise ModelResponseError("The model returned an empty response.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelResponseError("The model did not return valid JSON.") from exc
    try:
        return FileNarrative.model_validate(data)
    except ValidationError as exc:
        raise ModelResponseError(f"The model reply did not match the expected shape: {exc}") from exc


class HttpModelClient(ModelClient):
    def __init__(self, settings: Settings, transport: Optional[httpx.BaseTransport] = None) -> None:
        self._s = settings
        # A transport can be injected in tests so no real network is used.
        self._client = httpx.Client(timeout=settings.request_timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpModelClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def narrate(self, system: str, user: str) -> FileNarrative:
        raw = self._chat(system, user, temperature=0.1)
        try:
            return _validate(raw)
        except ModelResponseError:
            repair = (
                "Your previous reply was not a single valid JSON object with the "
                "requested fields. Return only that JSON object now.\n\n" + raw
            )
            raw2 = self._chat(system, repair, temperature=0.0)
            return _validate(raw2)

    def _chat(self, system: str, user: str, temperature: float) -> str:
        if self._s.mode == "local":
            return self._chat_local(system, user, temperature)
        return self._chat_api(system, user, temperature)

    def _chat_local(self, system: str, user: str, temperature: float) -> str:
        url = f"{self._s.local_endpoint.rstrip('/')}/api/chat"
        payload = {
            "model": self._s.local_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": temperature},
        }
        body = self._post(url, payload, headers=None)
        try:
            return body["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise ModelResponseError("The local model reply was missing its content.") from exc

    def _chat_api(self, system: str, user: str, temperature: float) -> str:
        url = f"{self._s.api_endpoint.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self._s.api_key}"}
        payload = {
            "model": self._s.api_model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        body = self._post(url, payload, headers=headers)
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelResponseError("The API reply did not contain a message.") from exc

    def _post(self, url: str, payload: dict, headers: Optional[dict]) -> dict:
        last: Optional[Exception] = None
        for attempt in range(1, self._s.retries + 1):
            try:
                resp = self._client.post(url, json=payload, headers=headers or {})
            except httpx.ConnectError as exc:
                raise ModelUnavailableError(
                    "Could not reach the model. In local mode, start it first, for example "
                    "with 'ollama serve'. In API mode, check your network and endpoint."
                ) from exc
            except httpx.TimeoutException as exc:
                last = exc
                if attempt < self._s.retries:
                    time.sleep(attempt)
                    continue
                raise ModelResponseError(
                    "The model timed out. Try a smaller change or a faster model."
                ) from exc

            if resp.status_code == 429 or resp.status_code >= 500:
                last = httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
                if attempt < self._s.retries:
                    time.sleep(2 * attempt)
                    continue
                raise ModelResponseError(
                    f"The model provider is unavailable (status {resp.status_code})."
                )
            if resp.status_code >= 400:
                detail = _short_error(resp)
                raise ModelResponseError(f"The model provider rejected the request: {detail}")

            try:
                return resp.json()
            except json.JSONDecodeError as exc:
                raise ModelResponseError("The model provider did not return JSON.") from exc

        raise ModelResponseError("The model could not be reached.") from last


def _short_error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict) and "message" in err:
                return str(err["message"])[:200]
            if isinstance(err, str):
                return err[:200]
    except (json.JSONDecodeError, ValueError):
        pass
    return f"status {resp.status_code}"

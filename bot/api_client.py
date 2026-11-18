"""Thin HTTP client for the STS2MCP mod's local REST API."""
from __future__ import annotations

import json
import time
from typing import Any

import requests

BASE_URL = "http://localhost:15526/api/v1"


class ApiError(RuntimeError):
    pass


def _error_detail(data: dict[str, Any]) -> str:
    """Everything the mod said about a rejected action.

    `message` used to be read on its own, and the mod does not always use that
    key: every rejection on the Crystal Sphere screen logged `-> None` while
    the reply itself carried the reason. That screen has now cost two runs --
    a floor-31 one, and a floor-39 one that burned 3h35m on 11-13 -- and its
    correct action name is still unknown, because the one message that would
    name it was being thrown away. Report the whole body rather than nothing.
    """
    for key in ("message", "error", "reason", "detail", "description"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    body = {k: v for k, v in data.items() if k != "status"}
    if not body:
        return "no detail in the reply"
    try:
        return json.dumps(body, sort_keys=True, default=str)[:400]
    except (TypeError, ValueError):
        return str(body)[:400]


class ApiClient:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 10.0):
        self.base_url = base_url
        self.timeout = timeout
        self.session = requests.Session()

    def get_state(self) -> dict[str, Any]:
        resp = self.session.get(f"{self.base_url}/singleplayer", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def post_action(self, action: str, **fields: Any) -> dict[str, Any]:
        payload = {"action": action, **fields}
        resp = self.session.post(f"{self.base_url}/singleplayer", json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "error":
            raise ApiError(f"{action}({fields}) -> {_error_detail(data)}")
        return data

    def wait_until_ready(self, attempts: int = 30, delay: float = 2.0) -> dict[str, Any]:
        last_err: Exception | None = None
        for _ in range(attempts):
            try:
                return self.get_state()
            except (requests.ConnectionError, requests.Timeout) as e:
                last_err = e
                time.sleep(delay)
        raise ApiError(f"API never became reachable at {self.base_url}: {last_err}")

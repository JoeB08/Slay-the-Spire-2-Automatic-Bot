"""Thin HTTP client for the STS2MCP mod's local REST API."""
from __future__ import annotations

import time
from typing import Any

import requests

BASE_URL = "http://localhost:15526/api/v1"


class ApiError(RuntimeError):
    pass


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
            raise ApiError(f"{action}({fields}) -> {data.get('message')}")
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

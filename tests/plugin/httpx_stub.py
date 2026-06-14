"""httpx stub for testing environments where httpx is not installed.

Provides minimal mock classes used by relay-forward.py imports.
"""
import types


class TimeoutException(Exception):
    pass


class RequestError(Exception):
    pass


class Client:
    """Mock httpx.Client that records requests and returns configurable responses."""

    def __init__(self, timeout=10):
        self.timeout = timeout
        self._responses = {}
        self.requests = []

    def _set_response(self, method, url, status_code=200, json_data=None, headers=None):
        key = (method.upper(), url)
        self._responses[key] = {"status_code": status_code, "json_data": json_data or {}, "headers": headers or {}}

    def get(self, url, **kwargs):
        self.requests.append(("GET", url, kwargs))
        return self._resolve("GET", url)

    def post(self, url, **kwargs):
        self.requests.append(("POST", url, kwargs))
        return self._resolve("POST", url)

    def _resolve(self, method, url):
        result = self._responses.get((method, url), {"status_code": 200, "json_data": {}, "headers": {}})
        return MockResponse(
            status_code=result["status_code"],
            json_data=result["json_data"],
            headers=result["headers"],
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class MockResponse:
    def __init__(self, status_code, json_data, headers=None):
        self.status_code = status_code
        self._json_data = json_data
        self.headers = headers or {}

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

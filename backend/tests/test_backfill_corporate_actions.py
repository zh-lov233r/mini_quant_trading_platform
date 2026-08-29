import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from backend.utils.backfill_corporate_actions import _fetch_page, _safe_url


class _ResponseContext:
    def __init__(self, *, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.status = 200
        self.reason = "OK"

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self):
        return self.payload


class _Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return next(self.responses)


class CorporateActionBackfillTest(unittest.IsolatedAsyncioTestCase):
    async def test_transient_timeout_is_retried(self):
        session = _Session(
            [
                _ResponseContext(error=asyncio.TimeoutError()),
                _ResponseContext(payload={"results": [{"id": "ok"}]}),
            ]
        )

        with patch(
            "backend.utils.backfill_corporate_actions.asyncio.sleep",
            new=AsyncMock(),
        ):
            payload = await _fetch_page(
                session,
                "https://api.massive.com/v3/reference/dividends?apiKey=secret",
                None,
            )

        self.assertEqual(payload["results"], [{"id": "ok"}])
        self.assertEqual(session.calls, 2)

    def test_safe_url_removes_query_credentials(self):
        self.assertEqual(
            _safe_url("https://api.massive.com/v3/reference/dividends?apiKey=secret"),
            "https://api.massive.com/v3/reference/dividends",
        )


if __name__ == "__main__":
    unittest.main()

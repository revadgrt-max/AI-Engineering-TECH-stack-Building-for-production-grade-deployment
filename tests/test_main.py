import json

import app.main as main
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_returns_api_links() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "FastAPI Ask" in response.text
    assert 'fetch(`/ask/stream?' in response.text


def test_api_info_lists_documentation_route() -> None:
    response = client.get("/api")

    assert response.status_code == 200
    assert response.json()["docs"] == "/docs"
    assert response.json()["health"] == "/health"


def test_ask_returns_schema_response(monkeypatch) -> None:
    async def fake_openrouter(messages, model, *, json_mode=False):
        assert messages[-1] == {"role": "user", "content": "What is prompt engineering?"}
        assert model == "gpt-5.4-mini"
        assert json_mode is True
        return '{"answer":"Prompt engineering is...","confidence":0.92}', {
            "usage": {"total_tokens": 37}
        }

    monkeypatch.setattr(main, "_call_openrouter", fake_openrouter)

    response = client.get(
        "/ask",
        params={"question": "What is prompt engineering?", "model": "gpt-5.4-mini"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "answer": "Prompt engineering is...",
        "sources": [],
        "confidence": 0.92,
        "model": "gpt-5.4-mini",
        "tokens_used": 37,
    }


def test_ask_returns_safe_response_when_upstream_fails(monkeypatch) -> None:
    async def fail_openrouter(*args, **kwargs):
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(main, "_call_openrouter", fail_openrouter)

    response = client.get("/ask", params={"question": "Test question"})

    assert response.status_code == 200
    assert response.json() == {
        "answer": "sorry i could not answer that right now",
        "sources": [],
        "confidence": 0,
        "model": "openai/gpt-4o-mini",
        "tokens_used": 0,
    }


def test_ask_stream_emits_deltas_and_schema_response(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "openrouter_api_key", "test-key")

    class FakeStreamingResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"{\\\"answer\\\":\\\"Streamed answer\\\",\\\"confidence\\\":0.88}"}}]}'
            yield 'data: {"choices":[],"usage":{"total_tokens":19}}'
            yield "data: [DONE]"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, *args, **kwargs):
            return FakeStreamingResponse()

    def fake_async_client(*, timeout):
        assert timeout == 20.0
        return FakeClient()

    monkeypatch.setattr(main.httpx, "AsyncClient", fake_async_client)

    response = client.get("/ask/stream", params={"question": "Test streaming"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = response.text.split("\n\n")
    assert any(event.startswith("event: delta") for event in events)
    completed_event = next(event for event in events if event.startswith("event: complete"))
    final_response = json.loads(completed_event.split("data: ", 1)[1])
    assert final_response == {
        "answer": "Streamed answer",
        "sources": [],
        "confidence": 0.88,
        "model": "openai/gpt-4o-mini",
        "tokens_used": 19,
    }


def test_openrouter_retries_transient_response_with_backoff(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "openrouter_api_key", "test-key")
    attempts = 0
    delays = []

    class FakeResponse:
        def __init__(self, status_code, body):
            self.status_code = status_code
            self.body = body

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "transient error", request=httpx.Request("POST", "https://test"),
                    response=httpx.Response(self.status_code),
                )

        def json(self):
            return self.body

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts <= 3:
                return FakeResponse(503, {})
            return FakeResponse(
                200,
                {"choices": [{"message": {"content": "answer"}}], "usage": {}},
            )

    def fake_async_client(*, timeout):
        assert timeout == 20.0
        return FakeClient()

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(main.httpx, "AsyncClient", fake_async_client)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    import asyncio

    content, _ = asyncio.run(main._call_openrouter([], "test-model"))

    assert content == "answer"
    assert attempts == 4
    assert delays == [0.5, 1.0, 2.0]

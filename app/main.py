import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.responses import FileResponse, StreamingResponse


class Settings(BaseSettings):
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-4o-mini"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
app = FastAPI(title="OpenRouter API", version="0.1.0")
logger = logging.getLogger(__name__)
WEB_PAGE = Path(__file__).parent / "static" / "index.html"
OPENROUTER_TIMEOUT_SECONDS = 20.0
OPENROUTER_MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 0.5


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    model: str | None = None


class ChatResponse(BaseModel):
    model: str
    reply: str


class AskCompletion(BaseModel):
    answer: str
    confidence: float = Field(ge=0, le=1)


class AskResponse(BaseModel):
    answer: str
    sources: list[str]
    confidence: float = Field(ge=0, le=1)
    model: str = Field(min_length=1)
    tokens_used: int = Field(ge=0)


def _ask_messages(question: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Answer the question. Return a JSON object with exactly two keys: "
                "answer (string) and confidence (number from 0 to 1). Confidence "
                "is your self-assessment, not a calibrated probability. Do not "
                "invent or return sources; the API has no retrieval corpus."
            ),
        },
        {"role": "user", "content": question},
    ]


def _safe_ask_response(model: str) -> AskResponse:
    return AskResponse(
        answer="sorry i could not answer that right now",
        sources=[],
        confidence=0,
        model=model,
        tokens_used=0,
    )


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _provider_error_message(status_code: int) -> tuple[str, str]:
    if status_code in (401, 403):
        return (
            "invalid_api_key",
            "OpenRouter rejected the API key. Check the Vercel OPENROUTER_API_KEY setting, then redeploy.",
        )
    if status_code == 402:
        return (
            "insufficient_credits",
            "The OpenRouter account has insufficient credits or billing is not enabled.",
        )
    if status_code == 404:
        return (
            "model_not_found",
            "OpenRouter could not find that model. Choose a model ID available to your account.",
        )
    if status_code == 429:
        return (
            "rate_limited",
            "OpenRouter rate limit reached. Wait a moment and try again.",
        )
    if status_code >= 500:
        return (
            "provider_unavailable",
            "OpenRouter is temporarily unavailable. Try again shortly.",
        )
    return (
        "provider_request_failed",
        f"OpenRouter rejected the request (HTTP {status_code}). Check the model and request settings.",
    )


async def _call_openrouter(
    messages: list[dict[str, str]], model: str, *, json_mode: bool = False
) -> tuple[str, dict[str, Any]]:
    api_key = settings.openrouter_api_key.strip()
    if not api_key or api_key == "your_openrouter_api_key_here":
        raise HTTPException(
            status_code=503,
            detail="Set OPENROUTER_API_KEY in the root .env file before making requests.",
        )

    payload: dict[str, Any] = {"model": model, "messages": messages}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    try:
        async with httpx.AsyncClient(timeout=OPENROUTER_TIMEOUT_SECONDS) as client:
            for attempt in range(OPENROUTER_MAX_RETRIES + 1):
                try:
                    response = await client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json=payload,
                    )
                except httpx.TransportError:
                    if attempt == OPENROUTER_MAX_RETRIES:
                        raise
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
                    continue

                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < OPENROUTER_MAX_RETRIES:
                        await asyncio.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
                        continue

                response.raise_for_status()
                break

            data: Any = response.json()
            content = data["choices"][0]["message"]["content"]
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="OpenRouter request failed.") from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="OpenRouter returned an unexpected response.",
        ) from exc

    if not isinstance(content, str):
        raise HTTPException(status_code=502, detail="OpenRouter returned no text reply.")

    return content, data


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(WEB_PAGE, media_type="text/html")


@app.get("/api")
async def api_info() -> dict[str, Any]:
    return {
        "service": "OpenRouter API",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
        "endpoints": {
            "ask": "/ask?question=Your+question",
            "stream": "/ask/stream?question=Your+question",
        },
    }


@app.get("/ask", response_model=AskResponse)
async def ask(
    question: str = Query(min_length=1),
    model: str | None = None,
) -> AskResponse:
    selected_model = model or settings.openrouter_model
    try:
        content, data = await _call_openrouter(
            _ask_messages(question),
            selected_model,
            json_mode=True,
        )
        completion = AskCompletion.model_validate_json(content)
        usage = data.get("usage", {})
        raw_tokens = usage.get("total_tokens", 0) if isinstance(usage, dict) else 0
        tokens_used = raw_tokens if isinstance(raw_tokens, int) and raw_tokens >= 0 else 0

        return AskResponse(
            answer=completion.answer,
            sources=[],
            confidence=completion.confidence,
            model=selected_model,
            tokens_used=tokens_used,
        )
    except Exception:
        logger.exception("/ask failed; returning a safe fallback response")
        return _safe_ask_response(selected_model)


@app.get("/ask/stream")
async def ask_stream(
    question: str = Query(min_length=1),
    model: str | None = None,
) -> StreamingResponse:
    selected_model = model or settings.openrouter_model

    async def events():
        api_key = settings.openrouter_api_key.strip()
        if not api_key or api_key == "your_openrouter_api_key_here":
            yield _sse_event(
                "error",
                {
                    "code": "missing_api_key",
                    "message": "OPENROUTER_API_KEY is not configured for this deployment. Add it in Vercel Project Settings → Environment Variables, then redeploy.",
                },
            )
            yield _sse_event("complete", _safe_ask_response(selected_model).model_dump())
            return

        payload = {
            "model": selected_model,
            "messages": _ask_messages(question),
            "response_format": {"type": "json_object"},
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        content = ""
        tokens_used = 0

        try:
            async with httpx.AsyncClient(timeout=OPENROUTER_TIMEOUT_SECONDS) as client:
                for attempt in range(OPENROUTER_MAX_RETRIES + 1):
                    try:
                        async with client.stream(
                            "POST",
                            "https://openrouter.ai/api/v1/chat/completions",
                            headers={"Authorization": f"Bearer {api_key}"},
                            json=payload,
                        ) as response:
                            if response.status_code == 429 or response.status_code >= 500:
                                if attempt < OPENROUTER_MAX_RETRIES:
                                    await asyncio.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
                                    continue
                            response.raise_for_status()

                            async for line in response.aiter_lines():
                                if not line.startswith("data:"):
                                    continue
                                raw_data = line.removeprefix("data:").strip()
                                if raw_data == "[DONE]":
                                    break
                                chunk = json.loads(raw_data)
                                usage = chunk.get("usage")
                                if isinstance(usage, dict):
                                    raw_tokens = usage.get("total_tokens", 0)
                                    if isinstance(raw_tokens, int) and raw_tokens >= 0:
                                        tokens_used = raw_tokens
                                choices = chunk.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    piece = delta.get("content")
                                    if isinstance(piece, str) and piece:
                                        content += piece
                                        yield _sse_event("delta", {"text": piece})
                            break
                    except httpx.TransportError:
                        if content or attempt == OPENROUTER_MAX_RETRIES:
                            raise
                        await asyncio.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))

            completion = AskCompletion.model_validate_json(content)
            result = AskResponse(
                answer=completion.answer,
                sources=[],
                confidence=completion.confidence,
                model=selected_model,
                tokens_used=tokens_used,
            )
            yield _sse_event("complete", result.model_dump())
        except httpx.HTTPStatusError as exc:
            logger.warning("OpenRouter streaming request failed with HTTP %s", exc.response.status_code)
            code, message = _provider_error_message(exc.response.status_code)
            yield _sse_event("error", {"code": code, "message": message})
            yield _sse_event("complete", _safe_ask_response(selected_model).model_dump())
        except httpx.TimeoutException:
            logger.warning("OpenRouter streaming request timed out")
            yield _sse_event(
                "error",
                {"code": "provider_timeout", "message": "OpenRouter timed out. Try again shortly."},
            )
            yield _sse_event("complete", _safe_ask_response(selected_model).model_dump())
        except httpx.HTTPError:
            logger.exception("OpenRouter streaming connection failed")
            yield _sse_event(
                "error",
                {"code": "provider_connection_failed", "message": "Could not connect to OpenRouter. Try again shortly."},
            )
            yield _sse_event("complete", _safe_ask_response(selected_model).model_dump())
        except Exception:
            logger.exception("/ask/stream failed; returning a safe fallback response")
            yield _sse_event(
                "error",
                {"code": "invalid_provider_response", "message": "OpenRouter returned an unreadable response. Check the selected model and try again."},
            )
            yield _sse_event("complete", _safe_ask_response(selected_model).model_dump())

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    model = request.model or settings.openrouter_model
    reply, _ = await _call_openrouter(
        [{"role": "user", "content": request.message}], model
    )

    return ChatResponse(model=model, reply=reply)

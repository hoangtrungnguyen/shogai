"""Thin async wrapper around the google-genai SDK used by every Shōgai module."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ValidationError

from .config import Settings

T = TypeVar("T", bound=BaseModel)
StatusFn = Callable[[str], Awaitable[None]]

# Inline requests are capped at ~20 MB in total; stay well below and use the
# Files API for anything larger.
INLINE_LIMIT_BYTES = 14 * 1024 * 1024


class GeminiError(RuntimeError):
    """A user-presentable failure talking to Gemini."""


def _thinking_config(model: str, level: str | None) -> types.ThinkingConfig | None:
    level = (level or "").strip().lower()
    if not level or level == "default":
        return None
    # Gemini 2.x models use thinking_budget instead of thinking_level.
    if model.startswith("gemini-2") or model.startswith("models/gemini-2"):
        return None
    return types.ThinkingConfig(thinking_level=level)


def _strip_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    return match.group(1) if match else text


def describe_empty_response(resp: types.GenerateContentResponse) -> str:
    reason = None
    try:
        if resp.prompt_feedback and resp.prompt_feedback.block_reason:
            reason = f"prompt blocked ({resp.prompt_feedback.block_reason})"
        elif resp.candidates:
            reason = f"finish reason {resp.candidates[0].finish_reason}"
    except Exception:  # pragma: no cover - defensive
        pass
    return f"Gemini returned no usable content ({reason or 'unknown reason'})."


def friendly_error(exc: BaseException) -> str:
    if isinstance(exc, GeminiError):
        return str(exc)
    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", None)
        message = getattr(exc, "message", None) or str(exc)
        if code in (401, 403):
            return f"Gemini rejected the API key ({code}). Check GEMINI_API_KEY in backend/.env."
        if code == 429:
            return "Gemini rate limit or quota exceeded (429). Wait a moment and try again."
        return f"Gemini error {code}: {message}"
    return f"{type(exc).__name__}: {exc}"


def web_sources(resp: types.GenerateContentResponse) -> list[dict[str, str]]:
    """Collect grounding (Google Search / URL context) sources from a response."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for cand in resp.candidates or []:
        meta = getattr(cand, "grounding_metadata", None)
        for chunk in (getattr(meta, "grounding_chunks", None) or []):
            web = getattr(chunk, "web", None)
            uri = getattr(web, "uri", None)
            if uri and uri not in seen:
                seen.add(uri)
                out.append({"title": getattr(web, "title", None) or uri, "uri": uri})
        url_meta = getattr(cand, "url_context_metadata", None)
        for item in (getattr(url_meta, "url_metadata", None) or []):
            uri = getattr(item, "retrieved_url", None)
            if uri and uri not in seen:
                seen.add(uri)
                out.append({"title": uri, "uri": uri})
    return out


def search_queries(resp: types.GenerateContentResponse) -> list[str]:
    queries: list[str] = []
    for cand in resp.candidates or []:
        meta = getattr(cand, "grounding_metadata", None)
        queries.extend(getattr(meta, "web_search_queries", None) or [])
    return queries


class GeminiService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: genai.Client | None = None

    @property
    def configured(self) -> bool:
        return bool(self.settings.gemini_api_key)

    @property
    def client(self) -> genai.Client:
        if not self.settings.gemini_api_key:
            raise GeminiError("GEMINI_API_KEY is not set. Add it to backend/.env and restart the server.")
        if self._client is None:
            self._client = genai.Client(
                api_key=self.settings.gemini_api_key,
                http_options=types.HttpOptions(
                    api_version=self.settings.gemini_api_version,
                    timeout=10 * 60 * 1000,
                    retry_options=types.HttpRetryOptions(
                        attempts=4,
                        initial_delay=2.0,
                        max_delay=30.0,
                        http_status_codes=[408, 429, 500, 502, 503, 504],
                    ),
                ),
            )
        return self._client

    # ------------------------------------------------------------- generation
    async def _generate(self, model: str, contents: Any, config: types.GenerateContentConfig,
                        thinking: str | None) -> types.GenerateContentResponse:
        thinking_cfg = _thinking_config(model, thinking)
        if thinking_cfg is not None:
            config = config.model_copy(update={"thinking_config": thinking_cfg})
        try:
            return await self.client.aio.models.generate_content(model=model, contents=contents, config=config)
        except genai_errors.ClientError as exc:
            # Some models don't accept thinking_level; retry once without it.
            if thinking_cfg is not None and exc.code == 400 and "thinking" in str(exc).lower():
                config = config.model_copy(update={"thinking_config": None})
                return await self.client.aio.models.generate_content(model=model, contents=contents, config=config)
            raise

    async def generate_structured(
        self,
        *,
        model: str,
        contents: Any,
        schema: type[T],
        system: str | None = None,
        tools: list[types.Tool] | None = None,
        thinking: str | None = None,
        temperature: float | None = None,
    ) -> tuple[T, types.GenerateContentResponse]:
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            tools=tools or None,
            temperature=temperature,
        )
        resp = await self._generate(model, contents, config, thinking)
        parsed = resp.parsed
        if isinstance(parsed, schema):
            return parsed, resp
        text = resp.text
        if not text:
            raise GeminiError(describe_empty_response(resp))
        try:
            return schema.model_validate_json(_strip_fences(text)), resp
        except (ValidationError, json.JSONDecodeError) as exc:
            raise GeminiError(f"Gemini returned malformed JSON: {exc}") from exc

    async def generate_text(
        self,
        *,
        model: str,
        contents: Any,
        system: str | None = None,
        tools: list[types.Tool] | None = None,
        thinking: str | None = None,
    ) -> tuple[str, types.GenerateContentResponse]:
        config = types.GenerateContentConfig(system_instruction=system, tools=tools or None)
        resp = await self._generate(model, contents, config, thinking)
        text = resp.text
        if not text:
            raise GeminiError(describe_empty_response(resp))
        return text, resp

    async def stream(self, *, model: str, contents: Any, config: types.GenerateContentConfig,
                     thinking: str | None = None):
        thinking_cfg = _thinking_config(model, thinking)
        if thinking_cfg is not None:
            config = config.model_copy(update={"thinking_config": thinking_cfg})
        return await self.client.aio.models.generate_content_stream(model=model, contents=contents, config=config)

    # ------------------------------------------------------------------ files
    async def file_part(self, path: Path, mime_type: str, status: StatusFn | None = None,
                        force_upload: bool = False) -> tuple[Any, str | None]:
        """Return (content part, uploaded file name or None). Small files go inline."""
        size = path.stat().st_size
        if not force_upload and size <= INLINE_LIMIT_BYTES:
            data = await asyncio.to_thread(path.read_bytes)
            return types.Part.from_bytes(data=data, mime_type=mime_type), None
        uploaded = await self.upload_and_wait(path, mime_type, status)
        return uploaded, uploaded.name

    async def upload_and_wait(self, path: Path, mime_type: str, status: StatusFn | None = None,
                              timeout_s: float = 900) -> types.File:
        size_mb = path.stat().st_size / (1024 * 1024)
        if status:
            await status(f"Uploading to Gemini ({size_mb:.1f} MB)…")
        display = re.sub(r"[^A-Za-z0-9._-]", "_", path.name)[:100]
        uploaded = await self.client.aio.files.upload(
            file=str(path), config=types.UploadFileConfig(mime_type=mime_type, display_name=display)
        )
        started = time.monotonic()
        announced = False
        while uploaded.state is not None and uploaded.state.name == "PROCESSING":
            if status and not announced:
                await status("Gemini is processing the file…")
                announced = True
            if time.monotonic() - started > timeout_s:
                raise GeminiError("Timed out waiting for Gemini to process the file.")
            await asyncio.sleep(3)
            uploaded = await self.client.aio.files.get(name=uploaded.name)
        if uploaded.state is not None and uploaded.state.name == "FAILED":
            raise GeminiError("Gemini could not process this file (unsupported or corrupt?).")
        return uploaded

    async def delete_file(self, name: str | None) -> None:
        if not name:
            return
        try:
            await self.client.aio.files.delete(name=name)
        except Exception:
            pass  # uploads expire automatically after 48h

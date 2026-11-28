"""Video (and audio) analysis with Gemini: uploaded files or YouTube links."""

from __future__ import annotations

from google.genai import types

from ..config import Settings
from ..gemini import GeminiError, GeminiService
from ..prompts import ANALYZER_SYSTEM, MEDIA_TASK, VOICE_MEMO_ORIGIN
from ..schemas import MediaAnalysis
from .base import AnalysisResult, Item, StatusFn, SubjectContext, render_source_markdown


async def analyze_media(
    gem: GeminiService, settings: Settings, ctx: SubjectContext, item: Item, status: StatusFn
) -> AnalysisResult:
    media_kind = "voice memo" if item.recorded else "audio recording" if item.kind == "audio" else "video"
    uploaded = None
    if item.url:
        part = types.Part(file_data=types.FileData(file_uri=item.url))
    elif item.path is not None and item.mime_type:
        part, uploaded = await gem.file_part(item.path, item.mime_type, status)
    else:
        raise GeminiError("Video/audio item has no file or link.")
    try:
        await status("Listening…" if item.kind == "audio" else "Watching the video…")
        prompt = ctx.render() + "\n" + MEDIA_TASK.format(
            media_kind=media_kind, label=item.label, note=item.note or "(none)",
            origin=VOICE_MEMO_ORIGIN if item.recorded else "",
        )
        analysis, _ = await gem.generate_structured(
            model=settings.model_analyze,
            contents=[part, prompt],
            schema=MediaAnalysis,
            system=ANALYZER_SYSTEM.format(language=settings.language),
            thinking=settings.analyze_thinking,
        )
    finally:
        await gem.delete_file(uploaded)
    return AnalysisResult(
        item=item,
        analysis=analysis,
        markdown=render_source_markdown(item, analysis, ctx.name),
        model=settings.model_analyze,
    )

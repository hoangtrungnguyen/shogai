"""Image analysis (photos, screenshots, scanned documents) with Gemini vision."""

from __future__ import annotations

from ..config import Settings
from ..gemini import GeminiError, GeminiService
from ..prompts import ANALYZER_SYSTEM, IMAGE_TASK
from ..schemas import ImageAnalysis
from .base import AnalysisResult, Item, StatusFn, SubjectContext, render_source_markdown


async def analyze_image(
    gem: GeminiService, settings: Settings, ctx: SubjectContext, item: Item, status: StatusFn
) -> AnalysisResult:
    if item.path is None or not item.mime_type:
        raise GeminiError("Image item has no file.")
    part, uploaded = await gem.file_part(item.path, item.mime_type, status)
    try:
        await status("Looking at the image…")
        prompt = ctx.render() + "\n" + IMAGE_TASK.format(
            filename=item.original or item.label, note=item.note or "(none)"
        )
        analysis, _ = await gem.generate_structured(
            model=settings.model_analyze,
            contents=[part, prompt],
            schema=ImageAnalysis,
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
        extra_meta={"image_type": analysis.image_type},
        is_portrait=bool(analysis.is_portrait and analysis.about_subject),
    )

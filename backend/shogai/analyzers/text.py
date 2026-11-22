"""Text analysis: personal thoughts, notes, chat exports, documents and web pages."""

from __future__ import annotations

from google.genai import types

from ..config import Settings
from ..gemini import GeminiError, GeminiService, web_sources
from ..prompts import ANALYZER_SYSTEM, TEXT_TASK
from ..schemas import SourceAnalysis
from .base import AnalysisResult, Item, StatusFn, SubjectContext, render_source_markdown

MAX_TEXT_CHARS = 1_500_000  # ~400k tokens; leaves room for the prompt


async def analyze_text(
    gem: GeminiService, settings: Settings, ctx: SubjectContext, item: Item, status: StatusFn
) -> AnalysisResult:
    contents: list = []
    tools: list[types.Tool] | None = None
    uploaded = None

    if item.text is not None:
        text = item.text.strip()
        if not text:
            raise GeminiError("The text is empty.")
        truncated = ""
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS]
            truncated = "\n(Note: the source was truncated because it is very long.)"
        content = f"<source>\n{text}\n</source>{truncated}"
    elif item.path is not None and item.mime_type:
        part, uploaded = await gem.file_part(item.path, item.mime_type, status)
        contents.append(part)
        content = "(The document is attached above.)"
    elif item.url:
        tools = [types.Tool(url_context=types.UrlContext())]
        content = f"Read this web page with the URL context tool: {item.url}"
    else:
        raise GeminiError("Text item has no content.")

    try:
        await status("Reading…")
        contents.append(
            ctx.render() + "\n" + TEXT_TASK.format(label=item.label, note=item.note or "(none)", content=content)
        )
        analysis, resp = await gem.generate_structured(
            model=settings.model_analyze,
            contents=contents,
            schema=SourceAnalysis,
            system=ANALYZER_SYSTEM.format(language=settings.language),
            tools=tools,
            thinking=settings.analyze_thinking,
        )
    finally:
        await gem.delete_file(uploaded)
    web = web_sources(resp) if tools else []
    return AnalysisResult(
        item=item,
        analysis=analysis,
        markdown=render_source_markdown(item, analysis, ctx.name, web=web),
        model=settings.model_analyze,
        web_sources=web,
    )

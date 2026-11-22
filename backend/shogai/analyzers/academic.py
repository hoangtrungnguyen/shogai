"""Academic papers: extract what they say (and how they relate to the subject),
then fact-check the key claims with Gemini + Google Search grounding."""

from __future__ import annotations

from google.genai import types

from ..config import Settings
from ..gemini import GeminiError, GeminiService, friendly_error, search_queries, web_sources
from ..prompts import ACADEMIC_TASK, ANALYZER_SYSTEM, FACT_CHECK_SYSTEM, FACT_CHECK_TASK
from ..schemas import AcademicExtraction, FactCheck
from .base import AnalysisResult, Item, StatusFn, SubjectContext, render_source_markdown


async def analyze_academic(
    gem: GeminiService, settings: Settings, ctx: SubjectContext, item: Item, status: StatusFn
) -> AnalysisResult:
    contents: list = []
    tools: list[types.Tool] | None = None
    uploaded = None
    url_hint = ""
    if item.path is not None:
        part, uploaded = await gem.file_part(item.path, item.mime_type or "application/pdf", status)
        contents.append(part)
    elif item.url:
        tools = [types.Tool(url_context=types.UrlContext())]
        url_hint = (
            f"PAPER URL: {item.url}\nRead it with the URL context tool; if the page only shows an abstract, "
            "also open the linked full text / PDF.\n"
        )
    elif item.text:
        contents.append(f"<paper>\n{item.text}\n</paper>")
    else:
        raise GeminiError("Paper item has no file, link or text.")

    # 1) Extraction ---------------------------------------------------------
    try:
        await status("Reading the paper…")
        contents.append(
            ctx.render() + "\n" + ACADEMIC_TASK.format(label=item.label, note=item.note or "(none)", url_hint=url_hint)
        )
        paper, resp = await gem.generate_structured(
            model=settings.model_analyze,
            contents=contents,
            schema=AcademicExtraction,
            system=ANALYZER_SYSTEM.format(language=settings.language),
            tools=tools,
            thinking=settings.analyze_thinking,
        )
    finally:
        await gem.delete_file(uploaded)
    web = web_sources(resp) if tools else []

    # 2) Fact-check with Google Search -------------------------------------------
    fact: FactCheck | None = None
    fact_error: str | None = None
    queries: list[str] = []
    try:
        await status("Fact-checking claims with Google Search…")
        claims = "\n".join(
            f"{i}. {c.claim}" + (f" ({c.location})" if c.location else "") for i, c in enumerate(paper.key_claims, 1)
        ) or "(no explicit claims extracted)"
        authorship_task = (
            f"confirm whether {ctx.name} is an author of this paper and their affiliation."
            if paper.subject_relation == "author"
            else "not needed (leave empty) unless the paper names the subject."
        )
        fact, fresp = await gem.generate_structured(
            model=settings.model_analyze,
            contents=FACT_CHECK_TASK.format(
                paper_title=paper.paper_title,
                authors=", ".join(paper.authors) or "unknown",
                year=paper.year or "unknown",
                venue=paper.venue or "unknown",
                identifier=paper.identifier or item.url or "unknown",
                methodology=paper.methodology,
                flags="; ".join(paper.credibility_flags) or "none",
                claims=claims,
                name=ctx.name,
                relation=paper.subject_relation.replace("_", " "),
                role=f", {paper.subject_role}" if paper.subject_role else "",
                authorship_task=authorship_task,
            ),
            schema=FactCheck,
            system=FACT_CHECK_SYSTEM.format(language=settings.language),
            tools=[types.Tool(google_search=types.GoogleSearch())],
            thinking="medium",
        )
        for source in web_sources(fresp):
            if source["uri"] not in {w["uri"] for w in web}:
                web.append(source)
        queries = search_queries(fresp)
    except Exception as exc:  # fact-check is best effort; keep the extraction
        fact_error = friendly_error(exc)

    verdicts = [c.verdict for c in fact.checks] if fact else []
    return AnalysisResult(
        item=item,
        analysis=paper,
        markdown=render_source_markdown(
            item, paper, ctx.name, fact_check=fact, fact_check_error=fact_error, web=web
        ),
        model=settings.model_analyze,
        extra_meta={
            "paper_title": paper.paper_title,
            "subject_relation": paper.subject_relation,
            "fact_check": "ok" if fact else "failed",
            "verdicts": verdicts,
            "search_queries": queries[:10],
        },
        web_sources=web,
    )

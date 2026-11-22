"""Shared types and Markdown rendering for the four source analyzers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import Settings
from ..gemini import GeminiService
from ..prompts import SUBJECT_CONTEXT
from ..schemas import (
    AcademicExtraction,
    FactCheck,
    ImageAnalysis,
    MediaAnalysis,
    SourceAnalysis,
)

StatusFn = Callable[[str], Awaitable[None]]

CATEGORY_LABELS = {
    "identity": "Identity",
    "personality": "Personality",
    "communication": "Communication",
    "interests": "Interests",
    "preferences": "Preferences",
    "values": "Values",
    "work_education": "Work & education",
    "relationships": "Relationships",
    "life_events": "Life events",
    "appearance": "Appearance",
    "health_wellbeing": "Health & wellbeing",
    "relationship_with_user": "Our relationship",
    "other": "Other",
}

VERDICT_LABELS = {
    "supported": "✅ supported",
    "partially_supported": "🟡 partially supported",
    "contested": "⚖️ contested",
    "contradicted": "❌ contradicted",
    "unverifiable": "❔ unverifiable",
}


@dataclass
class Item:
    """One thing to analyze: an uploaded file, a pasted note, or a link."""

    id: str
    kind: str  # image | text | video | audio | academic
    label: str
    note: str = ""
    text: str | None = None
    path: Path | None = None
    mime_type: str | None = None
    url: str | None = None
    original: str | None = None  # original filename
    media: str | None = None  # stored copy, relative to the person folder (e.g. "media/x.jpg")


@dataclass
class SubjectContext:
    slug: str
    name: str
    relationship: str
    aliases: list[str]
    known: str

    def render(self) -> str:
        return SUBJECT_CONTEXT.format(
            name=self.name,
            relationship=f" (relationship to the user: {self.relationship})" if self.relationship else "",
            aliases=f"; also known as {', '.join(self.aliases)}" if self.aliases else "",
            known=self.known.strip() or "(nothing yet — this is a new profile)",
        )


@dataclass
class AnalysisResult:
    item: Item
    analysis: SourceAnalysis
    markdown: str
    model: str
    extra_meta: dict[str, Any] = field(default_factory=dict)
    is_portrait: bool = False
    web_sources: list[dict[str, str]] = field(default_factory=list)


Analyzer = Callable[[GeminiService, Settings, SubjectContext, Item, StatusFn], Awaitable[AnalysisResult]]


def _cell(text: Any) -> str:
    return str(text or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _media_embed(item: Item) -> list[str]:
    lines: list[str] = []
    if item.media:
        rel = f"../{item.media}"
        if item.kind == "image":
            lines.append(f"![{_cell(item.original or item.label)}]({rel})")
        else:
            lines.append(f"📎 Original file: [{_cell(item.original or item.label)}]({rel})")
    if item.url:
        lines.append(f"🔗 <{item.url}>")
    return lines


def render_source_markdown(
    item: Item,
    analysis: SourceAnalysis,
    subject_name: str,
    *,
    fact_check: FactCheck | None = None,
    fact_check_error: str | None = None,
    web: list[dict[str, str]] | None = None,
) -> str:
    out: list[str] = [f"# {analysis.title}", "", f"**Summary.** {analysis.summary}", ""]
    if item.note:
        out += [f"> **User note:** {item.note}", ""]
    embeds = _media_embed(item)
    if embeds:
        out += embeds + [""]
    if not analysis.about_subject:
        out += [f"> ⚠️ Gemini judged that this source does not appear to concern {subject_name}.", ""]

    if isinstance(analysis, AcademicExtraction):
        out += ["## Paper", ""]
        out.append(f"- **Title:** {analysis.paper_title}")
        if analysis.authors:
            out.append(f"- **Authors:** {', '.join(analysis.authors)}")
        out.append(f"- **Year / venue:** {analysis.year or 'n/a'} · {analysis.venue or 'n/a'}")
        if analysis.identifier:
            out.append(f"- **Identifier:** {analysis.identifier}")
        role = f" — {analysis.subject_role}" if analysis.subject_role else ""
        out.append(f"- **Relation to {subject_name}:** {analysis.subject_relation.replace('_', ' ')}{role}")
        out.append("")
        if analysis.key_claims:
            out += ["## Key claims", ""]
            for i, claim in enumerate(analysis.key_claims, 1):
                where = f" _({claim.location})_" if claim.location else ""
                out.append(f"{i}. {claim.claim}{where}  \n   ↳ _Relevance:_ {claim.relevance}")
            out.append("")
        out += ["## Methodology", "", analysis.methodology, ""]
        if analysis.credibility_flags:
            out += ["## Credibility flags", ""] + [f"- {flag}" for flag in analysis.credibility_flags] + [""]

    if analysis.observations:
        out += ["## Observations", ""]
        for obs in analysis.observations:
            label = CATEGORY_LABELS.get(obs.category, obs.category)
            when = f" _(when: {obs.when})_" if obs.when else ""
            lock = " 🔒" if obs.sensitive else ""
            out.append(
                f"- **{label}** · {obs.certainty} — {obs.statement}{when}{lock}  \n  _Evidence:_ {obs.evidence}"
            )
        out.append("")

    if isinstance(analysis, ImageAnalysis) and analysis.visible_text:
        out += ["## Visible text", "", analysis.visible_text, ""]

    if isinstance(analysis, MediaAnalysis):
        if analysis.key_moments:
            out += ["## Key moments", ""] + [f"- `{m.timestamp}` — {m.description}" for m in analysis.key_moments] + [""]
        if analysis.quotes:
            out += ["## Quotes", ""] + [f"> “{q}”\n" for q in analysis.quotes]

    if analysis.people:
        out += ["## Other people", ""] + [f"- **{p.name}** — {p.relation_to_subject}" for p in analysis.people] + [""]

    if isinstance(analysis, AcademicExtraction):
        out += ["## Fact-check", ""]
        if fact_check is not None:
            out += [f"**Publication status.** {fact_check.publication_status}", ""]
            if fact_check.authorship_check:
                out += [f"**Authorship.** {fact_check.authorship_check}", ""]
            if fact_check.checks:
                out += ["| Claim | Verdict | Evidence |", "|---|---|---|"]
                for check in fact_check.checks:
                    out.append(
                        f"| {_cell(check.claim)} | {VERDICT_LABELS.get(check.verdict, check.verdict)} | {_cell(check.explanation)} |"
                    )
                out.append("")
            out += [f"**Applicability to {subject_name}.** {fact_check.applicability}", ""]
            out += [f"**Overall.** {fact_check.overall}", ""]
        else:
            out += [f"_Fact-check unavailable: {fact_check_error or 'unknown error'}_", ""]

    if analysis.open_questions:
        out += ["## Open questions", ""] + [f"- {q}" for q in analysis.open_questions] + [""]

    if web:
        out += ["## Web sources", ""] + [f"{i}. [{_cell(w['title'])}]({w['uri']})" for i, w in enumerate(web, 1)] + [""]

    return "\n".join(out).strip() + "\n"

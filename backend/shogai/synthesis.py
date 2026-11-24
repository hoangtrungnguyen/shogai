"""Merge new source analyses into a person's living Markdown profile."""

from __future__ import annotations

import re
from typing import Awaitable, Callable

from .config import Settings
from .gemini import GeminiService
from .prompts import SYNTHESIS_SYSTEM, SYNTHESIS_TASK
from .schemas import ProfileUpdate
from .store import Person, SourceRecord, Store, split_frontmatter, split_references, strip_source_log

PLACEHOLDER_MARKER = "_Nothing recorded yet."

# (source record, source markdown body) pairs handed to the synthesizer
SourceDoc = tuple[SourceRecord, str]
Synthesizer = Callable[[GeminiService, Settings, Person, list[SourceDoc]], Awaitable[ProfileUpdate]]

_IMAGE_EMBED_RE = re.compile(r"^!\[[^\]]*\]\([^)]*\)\s*$", re.M)


def _is_placeholder(body: str) -> bool:
    return PLACEHOLDER_MARKER in body and len(body) < 600


def _source_block(record: SourceRecord, markdown: str) -> str:
    _, body = split_frontmatter(markdown)
    body = _IMAGE_EMBED_RE.sub("", body).strip()
    return f"### [{record.id}] {record.kind} — {record.title}\n\n{body}"


async def synthesize_profile(
    gem: GeminiService, settings: Settings, person: Person, sources: list[SourceDoc]
) -> ProfileUpdate:
    # The References section (links to other people) is the app's and the user's: Gemini never sees it.
    body, _ = split_references(person.body)
    current = "" if _is_placeholder(body) else body
    prompt = SYNTHESIS_TASK.format(
        name=person.name,
        relationship=person.relationship or "(not set)",
        aliases=", ".join(person.aliases) or "(none)",
        tags=", ".join(person.tags) or "(none)",
        profile=current or "(empty — this is a new profile)",
        sources="\n\n".join(_source_block(r, md) for r, md in sources),
    )
    update, _ = await gem.generate_structured(
        model=settings.model_synthesize,
        contents=prompt,
        schema=ProfileUpdate,
        system=SYNTHESIS_SYSTEM.format(name=person.name, language=settings.language),
        thinking=settings.synthesize_thinking,
    )
    return update


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


def apply_update(
    store: Store, person: Person, update: ProfileUpdate, avatar_candidate: str | None = None
) -> Person:
    body = strip_source_log((update.profile_markdown or "").strip())
    # Some models wrap the document in a ```markdown fence.
    fenced = re.match(r"^```(?:markdown|md)?\s*\n(.*?)\n```\s*$", body, re.S)
    if fenced:
        body = fenced.group(1).strip()
    if not body:
        raise ValueError("The synthesized profile was empty; keeping the previous version.")
    if not body.lstrip().startswith("#"):
        body = f"# {person.name}\n\n{body}"
    body, _ = split_references(body)  # save_profile keeps the stored References section

    meta: dict = {"summary": (update.summary or "").strip() or person.summary}
    if not person.relationship and (update.relationship or "").strip():
        meta["relationship"] = update.relationship.strip()
    name_key = person.name.strip().lower()
    meta["aliases"] = [a for a in _dedupe(person.aliases + list(update.aliases or [])) if a.lower() != name_key]
    meta["tags"] = _dedupe([t.strip().lower() for t in list(update.tags or []) + person.tags if t.strip()])[:10]
    if not person.avatar and avatar_candidate:
        meta["avatar"] = avatar_candidate
    return store.save_profile(person.slug, body=body, meta_updates=meta)

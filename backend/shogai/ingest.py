"""Feature 1 — Consume Info: route each input to its Gemini analyzer, save one
source file per input, then merge everything into the person's profile.md."""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .analyzers import ANALYZERS, AnalysisResult, Analyzer, Item, SubjectContext
from .config import Settings
from .gemini import GeminiService, friendly_error
from .jobs import Job
from .properties import extract_from_note, merge_found, properties_from_links
from .store import Store
from .synthesis import Synthesizer, apply_update, synthesize_profile

IMAGE_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    ".heic": "image/heic", ".heif": "image/heif",
}
VIDEO_TYPES = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".mpeg": "video/mpeg", ".mpg": "video/mpeg", ".avi": "video/x-msvideo", ".wmv": "video/x-ms-wmv",
    ".flv": "video/x-flv", ".3gp": "video/3gpp",
}
AUDIO_TYPES = {
    ".mp3": "audio/mp3", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac",
    ".aif": "audio/aiff", ".aiff": "audio/aiff",
}
DOCUMENT_TYPES = {".pdf": "application/pdf"}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".html", ".htm", ".xml",
    ".yaml", ".yml", ".srt", ".vtt", ".rtf",
}
KINDS = {"image", "text", "video", "audio", "academic"}
MAX_PARALLEL = 3


class IngestError(ValueError):
    """Bad ingest input (reported to the client as HTTP 400)."""


@dataclass
class FileKind:
    kind: str
    mime_type: str
    is_text: bool = False


def detect_kind(filename: str, content_type: str | None = None, requested: str | None = None) -> FileKind:
    """Pick the analyzer for an uploaded file; `requested` lets the user override when sensible."""
    ext = os.path.splitext(filename or "")[1].lower()
    ctype = (content_type or "").split(";")[0].strip().lower()
    requested = (requested or "").strip().lower() or None
    if requested and requested not in KINDS:
        raise IngestError(f"Unknown source type {requested!r} for {filename}.")

    if ext in IMAGE_TYPES or (not ext and ctype in IMAGE_TYPES.values()):
        _require(requested, {"image"}, filename)
        return FileKind("image", IMAGE_TYPES.get(ext, ctype))
    if ext in VIDEO_TYPES or (not ext and ctype.startswith("video/")):
        _require(requested, {"video"}, filename)
        return FileKind("video", VIDEO_TYPES.get(ext, ctype))
    if ext in AUDIO_TYPES or (not ext and ctype.startswith("audio/")):
        _require(requested, {"audio", "video"}, filename)
        return FileKind("audio", AUDIO_TYPES.get(ext, ctype))
    if ext in DOCUMENT_TYPES or ctype == "application/pdf":
        _require(requested, {"academic", "text"}, filename)
        return FileKind(requested or "academic", "application/pdf")
    if ext in TEXT_EXTENSIONS or ctype.startswith("text/") or ctype == "application/json":
        _require(requested, {"text", "academic"}, filename)
        return FileKind(requested or "text", "text/plain", is_text=True)
    raise IngestError(
        f"Unsupported file type for {filename!r}. Supported: images (jpg, png, webp, heic), "
        "videos (mp4, mov, webm…), audio (mp3, wav, m4a…), PDFs and text files."
    )


def _require(requested: str | None, allowed: set[str], filename: str) -> None:
    if requested and requested not in allowed:
        raise IngestError(f"{filename} cannot be processed as {requested!r} (allowed: {', '.join(sorted(allowed))}).")


_YOUTUBE_RE = re.compile(r"^(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)$", re.I)
_SOCIAL_HOST_RE = re.compile(r"^(?:[\w-]+\.)*(?:facebook|instagram|linkedin|github)\.com$", re.I)


def link_kind(url: str, requested: str | None = None) -> str:
    """Analyzer for a link: YouTube is video, social profiles are web pages, anything else a paper."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise IngestError(f"Not a valid http(s) link: {url!r}")
    youtube = bool(_YOUTUBE_RE.match(parsed.netloc))
    requested = (requested or "").strip().lower() or None
    if requested:
        if requested not in {"video", "academic", "text"}:
            raise IngestError(f"Links can be processed as video, academic or text, not {requested!r}.")
        if requested == "video" and not youtube:
            raise IngestError("Only YouTube links can be analyzed as video. Download the video and upload it instead.")
        return requested
    if youtube:
        return "video"
    return "text" if _SOCIAL_HOST_RE.match(parsed.hostname or "") else "academic"


@dataclass
class IngestRequest:
    slug: str
    items: list[Item]
    cleanup: list[Path] = field(default_factory=list)


class IngestPipeline:
    def __init__(
        self,
        store: Store,
        gem: GeminiService,
        settings: Settings,
        analyzers: dict[str, Analyzer] | None = None,
        synthesizer: Synthesizer | None = None,
    ):
        self.store = store
        self.gem = gem
        self.settings = settings
        self.analyzers = analyzers or ANALYZERS
        self.synthesizer = synthesizer or synthesize_profile
        self._merge_locks: dict[str, asyncio.Lock] = {}

    def merge_lock(self, slug: str) -> asyncio.Lock:
        return self._merge_locks.setdefault(slug, asyncio.Lock())

    def subject_context(self, slug: str) -> SubjectContext:
        person = self.store.get(slug)
        known = person.summary + "\n\n" + person.body if person.summary else person.body
        if "_Nothing recorded yet." in known:
            known = ""
        return SubjectContext(
            slug=slug,
            name=person.name,
            relationship=person.relationship,
            aliases=person.aliases,
            known=known[:4000],
        )

    async def run(self, job: Job, req: IngestRequest) -> None:
        try:
            await self._run(job, req)
        except Exception as exc:  # noqa: BLE001 - surface every failure to the UI
            await job.finish("error", {"type": "error", "message": friendly_error(exc)})
        finally:
            for path in req.cleanup:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    async def _run(self, job: Job, req: IngestRequest) -> None:
        slug = req.slug
        await job.emit({
            "type": "plan",
            "items": [{"id": i.id, "kind": i.kind, "label": i.label} for i in req.items],
        })
        ctx = self.subject_context(slug)
        await job.emit({"type": "stage", "stage": "analyzing", "message": f"Analyzing {len(req.items)} source(s) with Gemini…"})

        sem = asyncio.Semaphore(MAX_PARALLEL)
        results: dict[str, AnalysisResult] = {}
        errors: dict[str, str] = {}

        async def run_item(item: Item) -> None:
            async with sem:
                async def status(message: str) -> None:
                    await job.emit({"type": "item", "id": item.id, "status": "running", "message": message})

                await status("Starting…")
                try:
                    analyzer = self.analyzers[item.kind]
                    result = await analyzer(self.gem, self.settings, ctx, item, status)
                    results[item.id] = result
                    await job.emit({
                        "type": "item", "id": item.id, "status": "done",
                        "message": result.analysis.title,
                        "summary": result.analysis.summary,
                        "observations": len(result.analysis.observations),
                        "about_subject": result.analysis.about_subject,
                    })
                except Exception as exc:  # noqa: BLE001
                    errors[item.id] = friendly_error(exc)
                    await job.emit({"type": "item", "id": item.id, "status": "error", "message": errors[item.id]})

        await asyncio.gather(*(run_item(item) for item in req.items))
        ordered = [results[i.id] for i in req.items if i.id in results]
        failed = [{"id": k, "message": v} for k, v in errors.items()]
        # A profile link added for this person fills its property even when Gemini could not open the
        # page: Instagram, and most Facebook and LinkedIn pages, only show it a login screen.
        from_links = properties_from_links(i.url for i in req.items if i.url)
        link_filled = self.store.fill_properties(slug, from_links) if from_links else []
        link_changes = ["Filled properties from the links: " + ", ".join(link_filled)] if link_filled else []
        if not ordered:
            if not link_filled:
                raise RuntimeError("None of the sources could be analyzed. " + " | ".join(errors.values()))
            await job.finish("done", {"type": "done", "result": {
                "person": slug, "name": ctx.name, "sources": [], "changes": link_changes, "failed": failed,
            }})
            return

        await job.emit({"type": "stage", "stage": "saving", "message": "Saving source notes…"})
        docs = []
        avatar = None
        for result in ordered:
            item = result.item
            record = self.store.add_source(
                slug,
                kind=item.kind,
                title=result.analysis.title,
                summary=result.analysis.summary,
                markdown=result.markdown,
                original=item.original,
                media=item.media,
                note=item.note or None,
                url=item.url,
                extra={"model": result.model, **result.extra_meta},
            )
            docs.append((record, result.markdown))
            if result.is_portrait and item.media and avatar is None:
                avatar = item.media

        person = self.store.get(slug)
        await job.emit({"type": "stage", "stage": "merging", "message": f"Updating {person.name}'s profile…"})
        async with self.merge_lock(slug):
            person = self.store.get(slug)
            try:
                update = await self.synthesizer(self.gem, self.settings, person, docs)
                person = apply_update(self.store, person, update, avatar)
                # Contact details / priority written in the notes themselves fill empty properties.
                found: dict = {}
                names = [person.name, slug, *person.aliases]
                for result in ordered:
                    if result.item.text:
                        merge_found(found, extract_from_note(result.item.text, names))
                filled = self.store.fill_properties(slug, found) if found else []
                if filled:
                    update.changes.append("Filled properties from the notes: " + ", ".join(filled))
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"Sources were saved ({', '.join(r.id for r, _ in docs)}), but merging them into the profile "
                    f"failed: {friendly_error(exc)} — use 'Rebuild profile' to retry."
                ) from exc

        await job.finish("done", {
            "type": "done",
            "result": {
                "person": slug,
                "name": person.name,
                "sources": [r.to_dict() for r, _ in docs],
                "changes": update.changes + link_changes,
                "failed": failed,
            },
        })

    async def rebuild(self, job: Job, slug: str) -> None:
        """Re-merge every saved source into the profile (recovery / refresh)."""
        try:
            person = self.store.get(slug)
            records = self.store.list_sources(slug)
            if not records:
                raise RuntimeError("This person has no sources yet.")
            await job.emit({"type": "stage", "stage": "merging",
                            "message": f"Rebuilding {person.name}'s profile from {len(records)} source(s)…"})
            docs = [(r, self.store.get_source(slug, r.id)[1]) for r in records]
            async with self.merge_lock(slug):
                person = self.store.get(slug)
                update = await self.synthesizer(self.gem, self.settings, person, docs)
                person = apply_update(self.store, person, update)
            await job.finish("done", {"type": "done", "result": {
                "person": slug, "name": person.name, "sources": [], "changes": update.changes, "failed": [],
            }})
        except Exception as exc:  # noqa: BLE001
            await job.finish("error", {"type": "error", "message": friendly_error(exc)})

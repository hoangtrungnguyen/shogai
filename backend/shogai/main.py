"""Shōgai HTTP API (FastAPI). Run with `uv run shogai` or `uv run uvicorn shogai.main:app --reload`."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .advisor import Advisor
from .analyzers import Analyzer, Item
from .config import Settings, load_settings
from .gemini import GeminiService, friendly_error
from .ingest import IngestError, IngestPipeline, IngestRequest, detect_kind, link_kind
from .jobs import JobManager
from .library import Library
from .store import NotFound, Store, StoreError, now_iso, safe_filename
from .synthesis import Synthesizer

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
MAX_TEXT_FILE_BYTES = 8 * 1024 * 1024


def sse(data: dict[str, Any], event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None and event_id >= 0 else ""
    return f"{head}data: {json.dumps(data, ensure_ascii=False)}\n\n"


class PersonCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    relationship: str = ""
    aliases: list[str] = []
    tags: list[str] = []


class RawUpdate(BaseModel):
    markdown: str


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    focus: list[str] = []


def create_app(
    settings: Settings | None = None,
    gemini: GeminiService | None = None,
    analyzers: dict[str, Analyzer] | None = None,
    synthesizer: Synthesizer | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    store = Store(settings.data_dir)
    gem = gemini or GeminiService(settings)
    jobs = JobManager()
    pipeline = IngestPipeline(store, gem, settings, analyzers=analyzers, synthesizer=synthesizer)
    advisor = Advisor(store, gem, settings)
    library = Library(store, gem, settings)
    tmp_root = settings.data_dir / ".tmp"

    app = FastAPI(title="Shōgai API", version="0.1.0")
    app.state.settings = settings
    app.state.store = store
    app.state.jobs = jobs
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(StoreError)
    async def _store_error(_: Request, exc: StoreError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(IngestError)
    async def _ingest_error(_: Request, exc: IngestError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def require_gemini() -> None:
        if not gem.configured:
            raise HTTPException(503, "GEMINI_API_KEY is not configured. Add it to backend/.env and restart.")

    # ----------------------------------------------------------------- health
    @app.get("/api/health")
    async def health():
        return {
            "ok": True,
            "gemini_configured": gem.configured,
            "models": {
                "analyze": settings.model_analyze,
                "synthesize": settings.model_synthesize,
                "advisor": settings.model_advisor,
            },
            "language": settings.language,
            "keep_media": settings.keep_media,
            "data_dir": str(settings.data_dir),
            "people": len(store.list_people()),
        }

    # ----------------------------------------------------------------- people
    @app.get("/api/people")
    async def list_people():
        return [p.to_summary() for p in store.list_people()]

    @app.post("/api/people", status_code=201)
    async def create_person(body: PersonCreate):
        return store.create(body.name, body.relationship, body.aliases, body.tags).to_dict()

    @app.get("/api/people/{slug}")
    async def get_person(slug: str):
        return store.get(slug).to_dict()

    @app.put("/api/people/{slug}")
    async def update_person(slug: str, body: RawUpdate):
        return store.write_raw(slug, body.markdown).to_dict()

    @app.delete("/api/people/{slug}")
    async def delete_person(slug: str):
        trash = store.delete(slug)
        return {"deleted": slug, "moved_to": str(trash)}

    @app.post("/api/people/{slug}/rebuild", status_code=202)
    async def rebuild_person(slug: str):
        require_gemini()
        person = store.get(slug)
        job = jobs.create("rebuild", slug, person.name, f"Rebuild {person.name}'s profile")
        job.task = asyncio.create_task(pipeline.rebuild(job, slug))
        return job.snapshot()

    @app.get("/api/people/{slug}/sources")
    async def list_sources(slug: str):
        return [r.to_dict() for r in store.list_sources(slug)]

    @app.get("/api/people/{slug}/sources/{source_id}")
    async def get_source(slug: str, source_id: str):
        record, markdown = store.get_source(slug, source_id)
        return {**record.to_dict(), "markdown": markdown}

    @app.delete("/api/people/{slug}/sources/{source_id}")
    async def delete_source(slug: str, source_id: str):
        record, _ = store.get_source(slug, source_id)
        src = store.person_dir(slug) / "sources" / record.filename
        trash = store.trash_dir / f"{slug}-sources"
        trash.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(trash / f"{now_iso().replace(':', '')}-{record.filename}"))
        store.save_profile(slug)  # refresh the Source Log
        return {"deleted": record.id}

    @app.get("/api/people/{slug}/media")
    async def list_media(slug: str):
        return store.list_media(slug)

    @app.get("/api/people/{slug}/media/{name}")
    async def get_media(slug: str, name: str):
        return FileResponse(store.media_file(slug, name))

    # ----------------------------------------------------------------- ingest
    @app.post("/api/ingest", status_code=202)
    async def ingest(
        person: str = Form(...),
        thoughts: str = Form(""),
        thoughts_note: str = Form(""),
        links: str = Form("[]"),
        manifest: str = Form("[]"),
        files: list[UploadFile] = File(default=[]),
    ):
        require_gemini()
        target = store.get(person)
        try:
            manifest_list = json.loads(manifest or "[]")
            link_list = json.loads(links or "[]")
            assert isinstance(manifest_list, list) and isinstance(link_list, list)
        except (json.JSONDecodeError, AssertionError):
            raise HTTPException(400, "manifest and links must be JSON arrays.")

        files = [f for f in files if f.filename]
        # Validate everything before touching the disk.
        kinds = []
        for i, upload in enumerate(files):
            spec = manifest_list[i] if i < len(manifest_list) and isinstance(manifest_list[i], dict) else {}
            fk = detect_kind(upload.filename or "", upload.content_type, spec.get("kind"))
            if fk.is_text and (upload.size or 0) > MAX_TEXT_FILE_BYTES:
                raise HTTPException(400, f"{upload.filename} is larger than 8 MB; split it or upload a PDF instead.")
            kinds.append((upload, fk, str(spec.get("note") or "").strip()))
        parsed_links = []
        for link in link_list:
            if isinstance(link, str):
                link = {"url": link}
            url = str((link or {}).get("url") or "").strip()
            if url:
                parsed_links.append((url, link_kind(url, link.get("kind")), str(link.get("note") or "").strip()))
        if not thoughts.strip() and not kinds and not parsed_links:
            raise HTTPException(400, "Nothing to consume: add a note, a file or a link.")

        items: list[Item] = []
        cleanup: list[Path] = []
        counter = 0

        def next_id() -> str:
            nonlocal counter
            counter += 1
            return f"i{counter}"

        if thoughts.strip():
            item = Item(id=next_id(), kind="text", label="Personal thoughts", text=thoughts.strip(),
                        note=thoughts_note.strip())
            if settings.keep_media:  # keep the raw note too, so later scans (e.g. the library) can read it
                dest = store.new_media_path(target.slug, "thoughts.md")
                dest.write_text(thoughts.strip() + "\n", encoding="utf-8")
                item.media, item.original = f"media/{dest.name}", "thoughts.md"
            items.append(item)
        for upload, fk, note in kinds:
            name = upload.filename or "upload"
            if settings.keep_media:
                dest = store.new_media_path(target.slug, name)
                media_rel = f"media/{dest.name}"
            else:
                tmp_root.mkdir(parents=True, exist_ok=True)
                dest = Path(tempfile.mkstemp(dir=tmp_root, suffix="-" + safe_filename(name))[1])
                media_rel = None
                cleanup.append(dest)
            await run_in_threadpool(_copy_upload, upload, dest)
            item = Item(id=next_id(), kind=fk.kind, label=name, note=note, path=dest, mime_type=fk.mime_type,
                        original=name, media=media_rel)
            if fk.is_text:
                item.text = dest.read_text(encoding="utf-8", errors="replace")
                item.path = None
            items.append(item)
        for url, kind, note in parsed_links:
            items.append(Item(id=next_id(), kind=kind, label=url, url=url, note=note))

        job = jobs.create("ingest", target.slug, target.name, f"Consume {len(items)} source(s) for {target.name}")
        job.task = asyncio.create_task(pipeline.run(job, IngestRequest(slug=target.slug, items=items, cleanup=cleanup)))
        return job.snapshot()

    # ---------------------------------------------------------------- library
    @app.get("/api/library")
    async def get_library():
        return await run_in_threadpool(library.snapshot)

    @app.post("/api/library/scan", status_code=202)
    async def scan_library(force: bool = False):
        require_gemini()
        running = next((j for j in jobs.list() if j.kind == "library" and not j.finished), None)
        if running:
            return running.snapshot()
        job = jobs.create("library", "", "Library", "Scan notes for academic resources")
        job.task = asyncio.create_task(library.scan(job, force=force))
        return job.snapshot()

    # ------------------------------------------------------------------- jobs
    @app.get("/api/jobs")
    async def list_jobs():
        return [j.snapshot() for j in jobs.list()]

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str):
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        return job.snapshot(with_events=True)

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request, since: int = 0):
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        last_id = request.headers.get("last-event-id", "")
        start = int(last_id) + 1 if last_id.isdigit() else since
        if job.finished and start >= len(job.events):
            return Response(status_code=204)  # tells EventSource to stop reconnecting

        async def stream():
            async for idx, event in job.stream(start):
                if await request.is_disconnected():
                    break
                yield ": ping\n\n" if event is None else sse(event, idx)

        return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)

    # ------------------------------------------------------------------- chat
    @app.post("/api/chat")
    async def chat(body: ChatRequest):
        require_gemini()
        messages = [m.model_dump() for m in body.messages]

        async def stream():
            try:
                async for event in advisor.chat(messages, body.focus):
                    yield sse(event)
            except Exception as exc:  # noqa: BLE001
                yield sse({"type": "error", "message": friendly_error(exc)})

        return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)

    # -------------------------------------------------------- built frontend
    dist = settings.frontend_dist
    if (dist / "index.html").is_file():
        if (dist / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(404, "Not found")
            candidate = (dist / full_path).resolve()
            if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


def _copy_upload(upload: UploadFile, dest: Path) -> None:
    upload.file.seek(0)
    with dest.open("wb") as out:
        shutil.copyfileobj(upload.file, out, length=1024 * 1024)


app = create_app()


def run() -> None:
    import uvicorn

    settings = load_settings()
    uvicorn.run("shogai.main:app", host=settings.host, port=settings.port)

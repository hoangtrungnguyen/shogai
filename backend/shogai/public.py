"""Your public side: a folder of Markdown about you, and the requests people send you.

Layout::

    data/public/            # SHOGAI_PUBLIC_DIR: .md files about you that anyone may read
    data/requests/<id>.md   # one request per file, fetched from your relay (private)

Everything in the public folder is public, except files and folders whose name starts with a dot
(``.obsidian/``, temp files) and files with ``draft: true`` in their frontmatter.

The public listener (``create_public_app``, port 8001 by default) only serves the public folder
(``public_router``): it is read-only. Requests come only through your Shōgai Relay, from people you gave
a key (see relay.py); they wait in data/requests/ until you reply, and nothing is answered automatically.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import secrets
import shutil
import socket
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator

import uvicorn
import yaml
from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .config import VERSION, Settings, load_settings
from .prompts import REPLY_DRAFT_SYSTEM, REPLY_DRAFT_TASK
from .store import NotFound, StoreError, atomic_write, now_iso, split_frontmatter

if TYPE_CHECKING:  # pragma: no cover
    from .gemini import GeminiService

log = logging.getLogger("shogai")
server_log = logging.getLogger("uvicorn.error")  # printed at INFO next to uvicorn's own lines

MAIN_FILE = "about.md"  # its frontmatter `name` and `headline` introduce you on the public page
FAQ_FILE = "faq.md"  # replies you choose to publish are appended here
REQUESTS_DIR = "requests"
MAX_FILE_CHARS = 1_000_000
MAX_CONTEXT_CHARS = 200_000  # public notes sent to Gemini when drafting a reply
REQUEST_STATUSES = ("pending", "replied", "declined")
REQUEST_NOTE = "_A request sent to you through your Shōgai Relay. Reply to it in Shōgai → Requests._"

_SEGMENT_RE = re.compile(r"^[^./\\][^/\\]*$")  # one path segment: no leading dot, no slashes
_REQUEST_ID_RE = re.compile(r"^r\d{8}-\d{6}-[0-9a-f]{6}$")
_RELAY_ID_RE = re.compile(r"^[a-z]_[0-9a-f]{16}$")  # ids the relay gives requests (q_…) and keys (k_…)
_H1_RE = re.compile(r"^#[ \t]+(.+?)[ \t#]*$", re.M)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean_text(value: Any) -> str:
    """Text from a visitor or an API client: valid UTF-8, \\n line breaks, no control characters."""
    text = str(value or "").encode("utf-8", "replace").decode("utf-8")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL_RE.sub("", text).strip()


def one_line(value: Any) -> str:
    return " ".join(clean_text(value).split())


def _is_draft(meta: dict[str, Any]) -> bool:
    return str(meta.get("draft")).strip().lower() in {"true", "yes", "1"}


def _jsonable(value: Any) -> Any:
    """Frontmatter as JSON: YAML extras (sets, binary, …) become text."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _optional(value: Any) -> str | None:
    text = one_line(value) if value is not None else ""
    return text or None


# ------------------------------------------------------------------ public notes

@dataclass
class PublicFile:
    path: str  # relative to the public folder, with forward slashes
    title: str
    updated: str
    size: int
    draft: bool
    meta: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    raw: str = ""

    def summary(self) -> dict[str, Any]:
        return {"path": self.path, "title": self.title, "updated": self.updated, "size": self.size,
                "draft": self.draft}

    def to_dict(self, raw: bool = False) -> dict[str, Any]:
        data = {**self.summary(), "meta": _jsonable(self.meta), "body": self.body}
        if raw:
            data["raw"] = self.raw
        return data


class PublicFolder:
    """The Markdown files about you that anyone may read."""

    def __init__(self, root: Path, trash_dir: Path):
        self.root = Path(root)
        self.trash_dir = Path(trash_dir)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ paths
    def _path(self, rel: str) -> tuple[str, Path]:
        """Validate a path inside the folder; returns (normalized path, file path)."""
        rel = (rel or "").strip().replace("\\", "/")
        parts = rel.split("/")
        if (
            not rel.lower().endswith(".md")
            or len(rel) > 200
            or any(not _SEGMENT_RE.match(p) or p != p.strip() or _CONTROL_RE.search(p) for p in parts)
        ):
            raise StoreError(f"Invalid file name {rel!r}: use a name like work.md or notes/work.md.")
        root = self.root.resolve()
        if root not in (root / rel).resolve().parents:  # e.g. a link that points outside the folder
            raise StoreError(f"{rel!r} is outside the public folder.")
        return rel, self.root / rel

    def _walk(self) -> Iterator[tuple[str, Path]]:
        if not self.root.is_dir():
            return
        root = self.root.resolve()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if name.startswith(".") or not name.lower().endswith(".md"):
                    continue
                path = Path(dirpath) / name
                try:
                    real = path.resolve()
                except OSError:
                    continue
                if root in real.parents and real.is_file():
                    yield path.relative_to(root).as_posix(), path

    def _load(self, rel: str, path: Path) -> PublicFile:
        raw = path.read_text(encoding="utf-8", errors="replace")
        meta, body = split_frontmatter(raw)
        title = one_line(meta.get("title"))
        if not title:
            heading = _H1_RE.search(body)
            title = heading.group(1).strip() if heading else Path(rel).stem.replace("-", " ").replace("_", " ")
        stat = path.stat()
        return PublicFile(
            path=rel,
            title=title,
            updated=datetime.fromtimestamp(stat.st_mtime, timezone.utc).replace(microsecond=0).isoformat(),
            size=stat.st_size,
            draft=_is_draft(meta),
            meta=meta,
            body=body.strip(),
            raw=raw,
        )

    # ------------------------------------------------------------------ files
    def files(self, include_drafts: bool = False) -> list[PublicFile]:
        out: list[PublicFile] = []
        for rel, path in self._walk():
            try:
                public_file = self._load(rel, path)
            except OSError:
                continue
            if include_drafts or not public_file.draft:
                out.append(public_file)
        out.sort(key=lambda f: (f.path.lower() != MAIN_FILE, f.path.lower()))
        return out

    def read(self, rel: str, include_drafts: bool = False) -> PublicFile:
        rel, path = self._path(rel)
        if not path.is_file():
            raise NotFound(f"No public file {rel!r}")
        public_file = self._load(rel, path)
        if public_file.draft and not include_drafts:
            raise NotFound(f"No public file {rel!r}")
        return public_file

    def write(self, rel: str, text: str) -> PublicFile:
        rel, path = self._path(rel)
        text = (text or "").replace("\r\n", "\n")
        if len(text) > MAX_FILE_CHARS:
            raise StoreError("This file is too large (1 MB at most).")
        with self._lock:
            atomic_write(path, text.rstrip() + "\n")
        return self._load(rel, path)

    def delete(self, rel: str) -> Path:
        """Move a file to the trash (never erased)."""
        rel, path = self._path(rel)
        with self._lock:
            if not path.is_file():
                raise NotFound(f"No public file {rel!r}")
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            target = self.trash_dir / "public" / f"{stamp}-{rel.replace('/', '__')}"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
        return target

    def add_faq(self, question: str, answer: str) -> PublicFile:
        """Append a question and your reply to faq.md (created when missing)."""
        heading = one_line(question).lstrip("#").strip() or "Question"
        if len(heading) > 200:
            heading = heading[:199].rstrip() + "…"
        with self._lock:
            path = self.root / FAQ_FILE
            if path.is_file():
                text = path.read_text(encoding="utf-8").rstrip() + "\n\n"
            else:
                text = "---\ntitle: Questions people asked\n---\n\n# Questions people asked\n\n"
            atomic_write(path, f"{text}## {heading}\n\n{answer.strip()}\n")
        return self.read(FAQ_FILE, include_drafts=True)

    # ------------------------------------------------------------------ reading
    def owner(self) -> dict[str, str | None]:
        """Your name and one-line headline, from the frontmatter of about.md."""
        meta: dict[str, Any] = {}
        path = self.root / MAIN_FILE
        if path.is_file():
            with contextlib.suppress(OSError):
                meta, _ = split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        if _is_draft(meta):
            meta = {}
        return {"name": _optional(meta.get("name")), "headline": _optional(meta.get("headline"))}

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        terms = [t for t in re.findall(r"\w+", (query or "").lower()) if len(t) > 1][:12]
        if not terms:
            return []
        hits: list[tuple[int, dict[str, Any]]] = []
        for public_file in self.files():
            lines = public_file.raw.splitlines()
            for i, line in enumerate(lines):
                low = line.lower()
                matched = sum(1 for t in terms if t in low)
                if not matched:
                    continue
                context = " ".join(x.strip() for x in lines[max(0, i - 1): i + 2] if x.strip())
                hits.append((matched * 10 + sum(low.count(t) for t in terms), {
                    "path": public_file.path, "title": public_file.title, "line": i + 1, "snippet": context[:400],
                }))
        hits.sort(key=lambda h: h[0], reverse=True)
        out: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for _, hit in hits:
            if (hit["path"], hit["snippet"]) not in seen:
                seen.add((hit["path"], hit["snippet"]))
                out.append(hit)
            if len(out) >= limit:
                break
        return out

    def _notes(self, files: list[PublicFile]) -> str:
        if not files:
            return "_Nothing published yet._"
        return "\n\n".join(f"---\n\n<!-- file: {f.path} -->\n\n{f.body}" for f in files)

    def bundle(self) -> str:
        """Every public note in one Markdown document."""
        owner = self.owner()
        head = f"# {owner['name'] or 'About me'}\n\n"
        if owner["headline"]:
            head += f"> {owner['headline']}\n\n"
        return head + self._notes(self.files()) + "\n"

    def llms_txt(self, relay_url: str | None = None) -> str:
        """The bundle, preceded by how an AI agent can use the public API and ask the owner."""
        owner = self.owner()
        who = owner["name"] or "the owner of this page"
        head = f"# {owner['name'] or 'About me'}\n\n"
        if owner["headline"]:
            head += f"> {owner['headline']}\n\n"
        head += (
            f"Public notes that {who} chose to share. The API has the same notes:\n\n"
            "- `GET /api/public` lists them (`?full=true` includes their text)\n"
            "- `GET /api/public/files/{path}` returns one note\n"
            "- `GET /api/public/search?q=...` searches them by keyword\n\n"
        )
        if relay_url:
            head += (f"To ask {who} something that isn't here, you need a key from them. With a key, ask on "
                     f"their Shōgai Relay: {relay_url} (how: {relay_url}/llms.txt).\n\n")
        else:
            head += f"To ask {who} something that isn't here, ask them for a key to their Shōgai Relay.\n\n"
        return head + self._notes(self.files()) + "\n"

    def context(self, limit: int = MAX_CONTEXT_CHARS) -> str:
        """The public notes as context for Gemini (drafts excluded)."""
        blocks: list[str] = []
        size = 0
        for public_file in self.files():
            block = f"=== FILE: {public_file.path} ===\n{public_file.raw.strip()}\n=== END FILE: {public_file.path} ==="
            if size + len(block) > limit:
                blocks.append(f"(… more files left out: the notes are longer than {limit} characters)")
                break
            blocks.append(block)
            size += len(block)
        return "\n\n".join(blocks) or "(no public notes yet)"


# ------------------------------------------------------------------ requests

class _RequestDumper(yaml.SafeDumper):
    """Multi-line text as `|` blocks so a request reads well in an editor; nothing is ever folded."""


def _represent_request_str(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|" if "\n" in data else None)


_RequestDumper.add_representer(str, _represent_request_str)
_RequestDumper.add_representer(type(None), lambda d, _v: d.represent_scalar("tag:yaml.org,2002:null", ""))


@dataclass
class InfoRequest:
    id: str
    status: str  # pending | replied | declined
    name: str  # as the sender typed it (not verified)
    question: str
    created: str
    updated: str
    reply: str = ""  # your reply, or the message sent with a decline
    answered: str | None = None
    relay_id: str = ""  # the request on the relay
    key_id: str = ""  # the key it was sent with (data/relay.json has your label for it)
    synced: bool = True  # the relay has your latest reply (or there is nothing to send)
    relay_gone: bool = False  # deleted on the relay (its key was revoked): a reply can't reach the sender

    def to_owner(self) -> dict[str, Any]:
        return {
            "id": self.id, "status": self.status, "name": self.name, "question": self.question,
            "reply": self.reply, "created": self.created, "updated": self.updated, "answered": self.answered,
            "relay_id": self.relay_id, "key_id": self.key_id, "synced": self.synced, "relay_gone": self.relay_gone,
        }


class RequestQueue:
    """Requests sent to you, one Markdown file each. All their text lives in the YAML frontmatter, so
    nothing a sender writes can be mistaken for your reply."""

    def __init__(self, root: Path, trash_dir: Path):
        self.root = Path(root)
        self.trash_dir = Path(trash_dir)
        self._lock = threading.RLock()

    def _file(self, request_id: str) -> Path:
        if not _REQUEST_ID_RE.match(request_id or ""):
            raise NotFound(f"No request {request_id!r}")
        return self.root / f"{request_id}.md"

    def _new_id(self) -> str:
        while True:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            request_id = f"r{stamp}-{secrets.token_hex(3)}"
            if not self._file(request_id).exists():
                return request_id

    def _write(self, record: InfoRequest) -> None:
        meta = {
            "id": record.id,
            "status": record.status,
            "from": record.name or None,
            "created": record.created,
            "updated": record.updated,
            "answered": record.answered,
            "question": record.question,
            "reply": record.reply or None,
            "relay_id": record.relay_id or None,
            "key_id": record.key_id or None,
            "synced": record.synced,
            "relay_gone": record.relay_gone,
        }
        front = yaml.dump(meta, Dumper=_RequestDumper, allow_unicode=True, sort_keys=False, width=1_000_000_000)
        atomic_write(self._file(record.id), f"---\n{front}---\n\n{REQUEST_NOTE}\n")

    def _read(self, path: Path) -> InfoRequest | None:
        try:
            meta, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            return None
        request_id = str(meta.get("id") or "")
        if request_id != path.stem or not _REQUEST_ID_RE.match(request_id):
            return None
        status = str(meta.get("status") or "pending")
        text = lambda key: "" if meta.get(key) is None else str(meta.get(key))  # noqa: E731
        return InfoRequest(
            id=request_id,
            status=status if status in REQUEST_STATUSES else "pending",
            name=text("from"),
            question=text("question"),
            created=text("created"),
            updated=text("updated") or text("created"),
            reply=text("reply"),
            answered=text("answered") or None,
            relay_id=text("relay_id"),
            key_id=text("key_id"),
            synced=meta.get("synced") is not False,
            relay_gone=meta.get("relay_gone") is True,
        )

    def list(self) -> list[InfoRequest]:
        """Every request, newest first."""
        if not self.root.is_dir():
            return []
        records = [r for r in (self._read(p) for p in self.root.glob("r*.md")) if r]
        records.sort(key=lambda r: (r.created, r.id), reverse=True)
        return records

    @staticmethod
    def counts(records: list[InfoRequest]) -> dict[str, int]:
        counts = {status: 0 for status in REQUEST_STATUSES}
        for record in records:
            counts[record.status] += 1
        return {**counts, "all": len(records)}

    def get(self, request_id: str) -> InfoRequest:
        path = self._file(request_id)
        record = self._read(path) if path.is_file() else None
        if record is None:
            raise NotFound(f"No request {request_id!r}")
        return record

    def add_from_relay(self, items: list[dict[str, Any]]) -> list[InfoRequest]:
        """Requests fetched from the relay (``id``, ``key_id``, ``name``, ``question``, ``created``);
        ones already here are skipped."""
        added: list[InfoRequest] = []
        with self._lock:
            known = {r.relay_id for r in self.list() if r.relay_id}
            for item in items:
                relay_id, key_id = str(item.get("id") or ""), str(item.get("key_id") or "")
                question = clean_text(item.get("question"))[:4000]
                if relay_id in known or not _RELAY_ID_RE.match(relay_id) or not _RELAY_ID_RE.match(key_id) or not question:
                    continue
                known.add(relay_id)
                stamp = now_iso()
                record = InfoRequest(
                    id=self._new_id(), status="pending", name=one_line(item.get("name"))[:120], question=question,
                    created=one_line(item.get("created"))[:40] or stamp, updated=stamp, relay_id=relay_id,
                    key_id=key_id,
                )
                self._write(record)
                added.append(record)
        return added

    def unsynced(self) -> list[InfoRequest]:
        """Replies and declines the relay doesn't have yet."""
        return [r for r in self.list() if r.relay_id and not r.synced and not r.relay_gone and r.status != "pending"]

    def mark_synced(self, request_id: str, gone: bool = False) -> InfoRequest:
        with self._lock:
            record = self.get(request_id)
            record.synced, record.relay_gone = True, record.relay_gone or gone
            self._write(record)
        return record

    def reply(self, request_id: str, message: str) -> InfoRequest:
        message = clean_text(message)
        if not message:
            raise StoreError("Write a reply first.")
        return self._answer(request_id, "replied", message)

    def decline(self, request_id: str, message: str = "") -> InfoRequest:
        return self._answer(request_id, "declined", clean_text(message))

    def _answer(self, request_id: str, status: str, message: str) -> InfoRequest:
        with self._lock:
            record = self.get(request_id)
            stamp = now_iso()
            record.status, record.reply, record.answered, record.updated = status, message, stamp, stamp
            record.synced = not record.relay_id or record.relay_gone  # to send at the next sync
            self._write(record)
        return record

    def delete(self, request_id: str) -> Path:
        """Move a request to the trash (never erased)."""
        with self._lock:
            path = self._file(request_id)
            if not path.is_file():
                raise NotFound(f"No request {request_id!r}")
            target = self.trash_dir / REQUESTS_DIR / path.name
            if target.exists():
                target = target.with_name(f"{path.stem}-{secrets.token_hex(2)}.md")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
        return target


async def draft_reply(gem: "GeminiService", settings: Settings, folder: PublicFolder, record: InfoRequest,
                      instructions: str = "") -> str:
    """A reply drafted by Gemini from your public notes only; you edit it before sending."""
    owner = folder.owner()["name"] or "not given"
    instructions = clean_text(instructions)
    contents = REPLY_DRAFT_TASK.format(
        name=record.name or "(no name given)",
        question=record.question,
        instructions=f"\nTHE OWNER'S INSTRUCTIONS FOR THIS REPLY: {instructions}\n" if instructions else "",
    )
    text, _ = await gem.generate_text(
        model=settings.model_advisor,
        contents=contents,
        system=REPLY_DRAFT_SYSTEM.format(owner=owner, notes=folder.context()),
        thinking="low",
    )
    return text.strip()


# ------------------------------------------------------------------ API

def public_router(folder: PublicFolder, relay_url: Callable[[], str | None] = lambda: None) -> APIRouter:
    """The only routes the public listener has: read the public notes. Requests go through the relay."""
    router = APIRouter(prefix="/api/public", tags=["public"])

    @router.get("", summary="The owner, the list of public notes, and where to ask them")
    def public_info(full: bool = False):
        return {
            "owner": folder.owner(),
            "files": [f.to_dict() if full else f.summary() for f in folder.files()],
            "relay": relay_url(),  # with a key from the owner, ask them there
        }

    @router.get("/files/{path:path}", summary="One public note")
    def public_file(path: str):
        return folder.read(path).to_dict()

    @router.get("/all", summary="Every public note in one Markdown document", response_class=PlainTextResponse)
    def public_all():
        return PlainTextResponse(folder.bundle(), media_type="text/markdown")

    @router.get("/search", summary="Keyword search in the public notes")
    def public_search(q: str = Query("", max_length=200)):
        return {"query": q, "results": folder.search(q)}

    return router


def create_public_app(
    settings: Settings | None = None,
    *,
    folder: PublicFolder | None = None,
    relay_url: Callable[[], str | None] = lambda: None,
) -> FastAPI:
    """The app behind the public listener: your public notes (read-only) and nothing else.

    Run on its own with ``uvicorn --factory shogai.public:create_public_app`` if you prefer a separate
    process; by default the main app serves it on SHOGAI_PUBLIC_PORT.
    """
    settings = settings or load_settings()
    trash = settings.data_dir / ".trash"
    folder = folder or PublicFolder(settings.public_root, trash)

    app = FastAPI(
        title="Public notes",
        version=VERSION,
        description="Read the notes someone chose to make public. To ask them something, you need a key "
                    "from them: see /llms.txt.",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(StoreError)
    async def _store_error(_: Request, exc: StoreError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    app.include_router(public_router(folder, relay_url))

    @app.get("/llms.txt", include_in_schema=False)
    def llms_txt():
        return PlainTextResponse(folder.llms_txt(relay_url()))

    dist = settings.frontend_dist
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    @app.get("/ask.html", include_in_schema=False)
    def visitor_page():
        page = dist / "ask.html"
        if page.is_file():
            return FileResponse(page)
        return HTMLResponse(
            "<p>The public page isn't built yet (<code>npm --prefix frontend run build</code>). "
            "The API works: <a href='/docs'>/docs</a> · <a href='/llms.txt'>/llms.txt</a></p>"
        )

    return app


# ------------------------------------------------------------------ the public listener

class _EmbeddedServer(uvicorn.Server):
    """A uvicorn server running inside the main one, which keeps handling Ctrl-C and SIGTERM."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def _listen_socket(host: str, port: int) -> socket.socket:
    family, _, _, _, address = socket.getaddrinfo(host or None, port, type=socket.SOCK_STREAM,
                                                  flags=socket.AI_PASSIVE)[0]
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(address)
    except OSError:
        sock.close()
        raise
    return sock


class PublicServer:
    """Serves the public app on its own port, in the main app's event loop. If the port is taken, it
    logs why and the main app carries on without it."""

    def __init__(self, app: FastAPI, host: str, port: int, url: str | None = None):
        self.app, self.host, self.port, self.url = app, host, port, url
        self.server: _EmbeddedServer | None = None
        self.task: asyncio.Task | None = None
        self.error: str | None = None

    @property
    def local_url(self) -> str:
        host = "127.0.0.1" if self.host in ("", "0.0.0.0", "::") else self.host
        return f"http://{f'[{host}]' if ':' in host else host}:{self.port}"

    @property
    def running(self) -> bool:
        return bool(self.server and self.server.started and self.task and not self.task.done())

    def status(self) -> dict[str, Any]:
        return {"enabled": True, "running": self.running, "local_url": self.local_url, "url": self.url,
                "error": self.error}

    async def start(self) -> bool:
        try:
            sock = _listen_socket(self.host, self.port)
        except OSError as exc:
            self.error = f"Couldn't listen on {self.local_url}: {exc.strerror or exc}."
            log.warning("Public page not started. %s", self.error)
            return False
        config = uvicorn.Config(self.app, host=self.host, port=self.port, lifespan="off", log_config=None,
                                access_log=False, server_header=False, timeout_graceful_shutdown=5)
        self.server = _EmbeddedServer(config)
        self.error = None
        self.task = asyncio.create_task(self._serve(sock))
        server_log.info("Public page on %s (your public notes only, read-only)", self.local_url)
        return True

    async def _serve(self, sock: socket.socket) -> None:
        try:
            await self.server.serve(sockets=[sock])  # type: ignore[union-attr]
        except (Exception, SystemExit) as exc:  # never take the main app down with it
            self.error = f"The public page stopped: {exc!r}"
            log.exception("The public page stopped.")
        finally:
            sock.close()

    async def stop(self) -> None:
        if self.server is None or self.task is None:
            return
        self.server.should_exit = True
        done, _ = await asyncio.wait({self.task}, timeout=10)
        if not done:
            self.task.cancel()

"""Shōgai Relay: where people (and their AI agents) ask someone a question with a key that person gave them.

Each person's local Shōgai app has an account here. It makes keys (only their hashes are sent here),
hands them out, picks up the requests sent with them and sends back the replies its owner writes. The
relay never reaches into anyone's app, and nothing is answered automatically.

Who sees what:
- a request and its reply: only whoever sent it — they need the key *and* the request's own token
  (returned when they sent it) — and the person they asked, in their own app;
- an app's account token opens only that app's keys and requests.

Run: ``uv run shogai-relay`` (http://127.0.0.1:8090) or ``docker compose up -d`` in this folder.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .store import Conflict, RelayStore, digest

log = logging.getLogger("uvicorn.error")

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"  # relay/data when run from source
MAX_PENDING = 500  # unanswered requests per person; beyond that new ones are refused
MAX_KEYS = 200  # keys per person
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    signup_code: str | None  # when set, an app needs it to open an account here
    host: str = "127.0.0.1"
    port: int = 8090


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("SHOGAI_RELAY_DATA_DIR") or DEFAULT_DATA_DIR).expanduser().resolve(),
        signup_code=(os.environ.get("SHOGAI_RELAY_SIGNUP_CODE") or "").strip() or None,
        host=os.environ.get("SHOGAI_RELAY_HOST") or "127.0.0.1",
        port=int(os.environ.get("SHOGAI_RELAY_PORT") or "8090"),
    )


def clean(value: str | None) -> str:
    text = str(value or "").encode("utf-8", "replace").decode("utf-8")
    return _CONTROL_RE.sub("", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def one_line(value: str | None) -> str:
    return " ".join(clean(value).split())


class Window:
    """At most `limit` events per `seconds`, per key (in memory)."""

    def __init__(self, limit: int, seconds: float):
        self.limit, self.seconds = limit, seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] > self.seconds:
            hits.popleft()
        if len(self._hits) > 10_000:
            self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] <= self.seconds}
            self._hits.setdefault(key, hits)
        return hits

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._recent(key, time.monotonic())) >= self.limit

    def hit(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._recent(key, now).append(now)

    def allow(self, key: str) -> bool:
        """Count an event; False when over the limit."""
        with self._lock:
            now = time.monotonic()
            hits = self._recent(key, now)
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def secret_from(request: Request) -> str:
    """A bearer secret: `Authorization: Bearer <secret>`, or `X-Shogai-Key: <key>`."""
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return request.headers.get("x-shogai-key", "").strip()


def sender_view(row: dict[str, Any]) -> dict[str, Any]:
    """A request as the person who sent it sees it."""
    return {k: row[k] for k in ("id", "status", "name", "question", "reply", "created", "delivered", "answered")}


# ------------------------------------------------------------------ bodies

class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=4000, description="What you would like to know.")
    name: str = Field(default="", max_length=120, description="Optional: who is asking.")


class Signup(BaseModel):
    code: str = Field(default="", max_length=200)


class Profile(BaseModel):
    name: str = Field(default="", max_length=120)
    headline: str = Field(default="", max_length=300)


class NewKey(BaseModel):
    key_hash: str = Field(pattern=r"^[0-9a-f]{64}$", description="SHA-256 (hex) of the key; the key itself stays with you.")


class Answer(BaseModel):
    status: Literal["replied", "declined"]
    reply: str = Field(default="", max_length=20_000)


LLMS_TXT = """\
# Shōgai Relay

Ask a person a question when they gave you a key (it starts with `shk_`). The person answers
themselves, in their own Shōgai app: nothing is answered automatically, so check back later.

Send the key as `Authorization: Bearer <key>` on every call:

- `GET /api/key` → who the key lets you ask (`to.name`, `to.headline`)
- `POST /api/requests` with JSON `{"question": "...", "name": "who is asking (optional)"}` → `id` and `token`.
  Keep both: the reply is only shown to whoever has the key and the request's token.
- `GET /api/requests/{id}` with the header `X-Request-Token: <token>` → `status` (pending, replied or
  declined), `delivered` (when their app picked the question up) and `reply`

Full API description: /docs
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    store = RelayStore(settings.data_dir / "relay.db")
    signups = Window(5, 3600)  # per address
    bad_keys = Window(30, 600)  # unknown keys tried, per address
    asks_per_key = Window(20, 3600)
    asks_per_ip = Window(30, 3600)
    if not settings.signup_code:
        log.warning("Anyone can open an account on this relay. Set SHOGAI_RELAY_SIGNUP_CODE to require a code.")

    app = FastAPI(
        title="Shōgai Relay",
        version=__version__,
        description="Ask a person a question with the key they gave you; they answer in their own Shōgai app. "
                    "Requests are answered by the person, never automatically.",
        redoc_url=None,
    )
    app.state.store = store
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-Token", "X-Shogai-Key"],
    )

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "no-referrer")
        path = request.url.path
        if path.startswith("/api/"):
            headers.setdefault("Cache-Control", "no-store")
        if not path.startswith(("/docs", "/openapi.json")):  # the API docs page loads its scripts from a CDN
            headers.setdefault("Content-Security-Policy", CSP)
            headers.setdefault("X-Frame-Options", "DENY")
        return response

    # ------------------------------------------------------------ auth
    def owner_auth(request: Request) -> dict[str, Any]:
        owner = store.owner_by_token(secret_from(request))
        if not owner:
            raise HTTPException(401, "Unknown or missing account token.", headers={"WWW-Authenticate": "Bearer"})
        return owner

    def key_auth(request: Request) -> dict[str, Any]:
        ip = client_ip(request)
        if bad_keys.blocked(ip):
            raise HTTPException(429, "Too many unknown keys tried. Please wait a few minutes.")
        key = store.key_by_secret(secret_from(request))
        if not key:
            bad_keys.hit(ip)
            raise HTTPException(401, "Unknown key. Use the key the person gave you.",
                                headers={"WWW-Authenticate": "Bearer"})
        return key

    # ------------------------------------------------------------ asking (key holders)
    @app.get("/api/key", tags=["ask"], summary="Who this key lets you ask")
    def whose_key(key: dict = Depends(key_auth)):
        return {"key_id": key["key_id"], "to": {"name": key["name"] or None, "headline": key["headline"] or None}}

    @app.post("/api/requests", status_code=201, tags=["ask"], summary="Ask the person a question")
    def send_request(body: Ask, request: Request, key: dict = Depends(key_auth)):
        """Returns the request's `id` and `token`. Keep the token: with the key, it is the only way to read
        the reply."""
        question, name = clean(body.question), one_line(body.name)[:120]
        if not question:
            raise HTTPException(400, "Write your question.")
        if not asks_per_key.allow(key["key_id"]) or not asks_per_ip.allow(client_ip(request)):
            raise HTTPException(429, "Too many questions. Please try again later.")
        if store.count_pending(key["owner_id"]) >= MAX_PENDING:
            raise HTTPException(503, "This person has too many unanswered questions. Please try again later.")
        row, token = store.add_request(key["owner_id"], key["key_id"], name, question)
        return {**sender_view(row), "token": token}

    @app.get("/api/requests/{request_id}", tags=["ask"], summary="The status and reply of a question you sent")
    def request_status(request_id: str, request: Request, token: str = "", key: dict = Depends(key_auth)):
        """Send the request's token in the `X-Request-Token` header (or as `?token=`)."""
        row = store.request_for_sender(request_id, key["key_id"], request.headers.get("x-request-token") or token)
        if not row:
            raise HTTPException(404, "No request with this id and token for this key.")
        return sender_view(row)

    # ------------------------------------------------------------ a person's Shōgai app
    @app.post("/api/owner/signup", status_code=201, tags=["owner"], summary="Open an account for a Shōgai app")
    def signup(request: Request, body: Signup | None = None):
        if not signups.allow(client_ip(request)):
            raise HTTPException(429, "Too many sign-ups from this address. Please try again later.")
        code = (body.code if body else "").strip()
        if settings.signup_code and not hmac.compare_digest(digest(code), digest(settings.signup_code)):
            raise HTTPException(403, "Wrong sign-up code. Ask whoever runs this relay.")
        owner_id, token = store.create_owner()
        return {"owner_id": owner_id, "owner_token": token}

    @app.get("/api/owner", tags=["owner"], summary="This app's account")
    def account(owner: dict = Depends(owner_auth)):
        return {"owner_id": owner["id"], "name": owner["name"], "headline": owner["headline"],
                "keys": store.keys(owner["id"]), "pending": store.count_pending(owner["id"])}

    @app.put("/api/owner/profile", tags=["owner"], summary="The name and headline key holders see")
    def set_profile(body: Profile, owner: dict = Depends(owner_auth)):
        store.set_profile(owner["id"], one_line(body.name)[:120], one_line(body.headline)[:300])
        return {"ok": True}

    @app.delete("/api/owner", tags=["owner"], summary="Delete this account, its keys and requests")
    def delete_account(owner: dict = Depends(owner_auth)):
        store.delete_owner(owner["id"])
        return {"deleted": owner["id"]}

    @app.post("/api/owner/keys", status_code=201, tags=["owner"], summary="Register a key (its hash)")
    def add_key(body: NewKey, owner: dict = Depends(owner_auth)):
        if len(store.keys(owner["id"])) >= MAX_KEYS:
            raise HTTPException(400, f"An account can have {MAX_KEYS} keys at most; revoke some first.")
        try:
            return {"key_id": store.add_key(owner["id"], body.key_hash)}
        except Conflict as exc:
            raise HTTPException(409, str(exc))

    @app.delete("/api/owner/keys/{key_id}", tags=["owner"], summary="Revoke a key and delete its requests")
    def revoke_key(key_id: str, owner: dict = Depends(owner_auth)):
        if not store.delete_key(owner["id"], key_id):
            raise HTTPException(404, "No such key.")
        return {"deleted": key_id}

    @app.get("/api/owner/requests", tags=["owner"], summary="Requests after a sequence number")
    def new_requests(after: int = 0, limit: int = Query(100, ge=1, le=500), owner: dict = Depends(owner_auth)):
        rows = store.requests_after(owner["id"], after, limit)
        return {"requests": rows, "cursor": rows[-1]["seq"] if rows else max(0, after)}

    @app.post("/api/owner/requests/{request_id}/answer", tags=["owner"], summary="Send the reply (or a decline)")
    def answer(request_id: str, body: Answer, owner: dict = Depends(owner_auth)):
        if not store.answer(owner["id"], request_id, body.status, clean(body.reply)):
            raise HTTPException(404, "No such request.")
        return {"id": request_id, "status": body.status}

    @app.delete("/api/owner/requests/{request_id}", tags=["owner"], summary="Delete a request")
    def delete_request(request_id: str, owner: dict = Depends(owner_auth)):
        if not store.delete_request(owner["id"], request_id):
            raise HTTPException(404, "No such request.")
        return {"deleted": request_id}

    # ------------------------------------------------------------ page, docs, health
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def page():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/llms.txt", include_in_schema=False)
    def llms_txt():
        return PlainTextResponse(LLMS_TXT)

    @app.get("/health", tags=["relay"])
    def health():
        return {"ok": True, "version": __version__, "signup_code_required": bool(settings.signup_code)}

    return app


def run() -> None:
    import uvicorn

    settings = load_settings()
    uvicorn.run("shogai_relay.app:create_app", factory=True, host=settings.host, port=settings.port,
                proxy_headers=True)

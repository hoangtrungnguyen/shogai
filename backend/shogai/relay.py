"""Your connection to a Shōgai Relay: the server where people you gave a key ask you things.

The relay never reaches into this app; this app calls it. Every SHOGAI_RELAY_POLL seconds (and when you
press *Sync*) the app

1. sends your name and headline (from about.md), so key holders see whom they are asking,
2. fetches new requests into the queue (data/requests/),
3. sends the replies and declines you wrote since the last sync, and deletes on the relay the requests
   you deleted here.

data/relay.json (readable only by you) keeps the relay's address, this app's account token there, and
the keys you made, each with the label you gave it. The relay only ever gets a key's SHA-256: never the
key itself (until someone uses it) or its label.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import secrets
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .public import PublicFolder, RequestQueue, one_line
from .store import now_iso

log = logging.getLogger("uvicorn.error")

STATE_FILE = "relay.json"
KEY_PREFIX = "shk_"
TIMEOUT_S = 20.0
PAGE = 100
LOOPBACK = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}  # traffic that stays on this machine


class RelayError(Exception):
    """A problem with the relay, worded for the person using the app. `status` is the relay's HTTP status."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def normalize_server(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.query or parsed.fragment:
        raise RelayError("Enter the relay's address, like https://relay.example.com", 400)
    return url


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@dataclass
class RelayKey:
    id: str  # the relay's id for the key
    label: str  # whom you gave it to; stays in this app
    key: str
    created: str


class RelayState:
    """data/relay.json. Written readable by you only: it holds this app's account token."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._reset()
        self.load()

    def _reset(self) -> None:
        self.server: str | None = None
        self.owner_id: str | None = None
        self.token: str | None = None
        self.keys: list[RelayKey] = []
        self.cursor = 0  # the relay's sequence number of the last request fetched
        self.last_sync: str | None = None
        self.error: str | None = None
        self.profile_sig: str | None = None  # what was last sent as your name and headline
        self.pending_deletes: list[str] = []  # requests deleted here, still to delete on the relay

    @property
    def connected(self) -> bool:
        return bool(self.server and self.token)

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        with self._lock:
            self.server, self.owner_id, self.token = data.get("server"), data.get("owner_id"), data.get("token")
            self.keys = [RelayKey(**{k: str(item.get(k) or "") for k in ("id", "label", "key", "created")})
                         for item in data.get("keys") or [] if isinstance(item, dict)]
            self.cursor = int(data.get("cursor") or 0)
            self.last_sync, self.error = data.get("last_sync"), data.get("error")
            self.profile_sig = data.get("profile_sig")
            self.pending_deletes = [str(x) for x in data.get("pending_deletes") or []]

    def save(self) -> None:
        with self._lock:
            data = {
                "server": self.server, "owner_id": self.owner_id, "token": self.token,
                "keys": [asdict(k) for k in self.keys], "cursor": self.cursor, "last_sync": self.last_sync,
                "error": self.error, "profile_sig": self.profile_sig, "pending_deletes": self.pending_deletes,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex[:8]}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(data, out, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)

    def clear(self) -> None:
        with self._lock:
            self._reset()
            self.path.unlink(missing_ok=True)

    @property
    def share_url(self) -> str | None:
        """The relay's address for people (key links, the public page). An app in Docker reaches a relay on the
        same machine at host.docker.internal, which that machine's own browser knows as localhost."""
        if not self.server:
            return None
        if urlparse(self.server).hostname == "host.docker.internal":
            return self.server.replace("host.docker.internal", "localhost", 1)
        return self.server

    def label(self, key_id: str) -> str | None:
        return next((k.label for k in self.keys if k.id == key_id), None)

    def view(self) -> dict[str, Any]:
        """For your own app's page (it shows the keys so you can hand them out)."""
        host = urlparse(self.server or "").hostname or ""
        return {
            "connected": self.connected,
            "server": self.server,
            "share_url": self.share_url,
            "insecure": bool(self.server) and self.server.startswith("http://") and host not in LOOPBACK,
            "keys": [asdict(k) for k in self.keys],
            "last_sync": self.last_sync,
            "error": self.error,
        }


class RelayClient:
    def __init__(self, server: str, token: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.server, self.token, self.transport = server, token, transport

    async def call(self, method: str, path: str, *, json: Any = None, params: dict | None = None,
                   auth: bool = True) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if auth and self.token else {}
        try:
            async with httpx.AsyncClient(base_url=self.server, timeout=TIMEOUT_S, transport=self.transport) as client:
                resp = await client.request(method, path, json=json, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise RelayError(f"Couldn't reach the relay at {self.server} ({type(exc).__name__}: {exc})") from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail")
            except (ValueError, AttributeError):
                detail = None
            message = detail if isinstance(detail, str) else f"The relay answered HTTP {resp.status_code}."
            raise RelayError(message, resp.status_code)
        try:
            return resp.json() if resp.content else None
        except ValueError as exc:
            raise RelayError(f"{self.server} doesn't look like a Shōgai Relay.") from exc


class Relay:
    """Keeps the request queue and your relay in step."""

    def __init__(self, data_dir: Path, queue: RequestQueue, folder: PublicFolder, poll: float,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.state = RelayState(Path(data_dir) / STATE_FILE)
        self.queue, self.folder, self.poll, self.transport = queue, folder, poll, transport
        self._lock = asyncio.Lock()  # one conversation with the relay at a time
        self._task: asyncio.Task | None = None

    def client(self) -> RelayClient:
        if not self.state.connected:
            raise RelayError("Not connected to a relay.", 400)
        return RelayClient(self.state.server or "", self.state.token, self.transport)

    # ------------------------------------------------------------ account
    async def connect(self, server: str, code: str = "") -> dict[str, Any]:
        server = normalize_server(server)
        if self.state.connected:
            raise RelayError("Already connected to a relay. Disconnect first.", 400)
        client = RelayClient(server, transport=self.transport)
        health = await client.call("GET", "/health", auth=False)
        if not isinstance(health, dict) or "signup_code_required" not in health:
            raise RelayError(f"{server} doesn't look like a Shōgai Relay.")
        account = await client.call("POST", "/api/owner/signup", json={"code": code.strip()}, auth=False)
        with self.state._lock:
            self.state._reset()
            self.state.server, self.state.owner_id, self.state.token = server, account["owner_id"], account["owner_token"]
            self.state.save()
        log.info("Connected to the relay at %s.", server)
        return await self.sync()

    async def disconnect(self) -> str | None:
        """Delete this app's account on the relay (its keys and requests go with it), then forget the relay.
        Returns a warning when the relay couldn't be reached."""
        warning = None
        if self.state.connected:
            async with self._lock:
                try:
                    await self.client().call("DELETE", "/api/owner")
                except RelayError as exc:
                    if exc.status != 401:  # 401: the account is gone already
                        warning = (f"The relay couldn't be reached ({exc}), so your account there wasn't deleted. "
                                   "Your keys keep working until whoever runs the relay removes it.")
        self.state.clear()
        return warning

    # ------------------------------------------------------------ keys
    async def create_key(self, label: str) -> RelayKey:
        label = one_line(label)[:80] or "Key"
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        async with self._lock:
            result = await self.client().call("POST", "/api/owner/keys", json={"key_hash": key_hash(key)})
            entry = RelayKey(id=str(result["key_id"]), label=label, key=key, created=now_iso())
            with self.state._lock:
                self.state.keys.append(entry)
                self.state.save()
        return entry

    async def revoke_key(self, key_id: str) -> None:
        """The key stops working and the relay deletes the requests sent with it; your copies stay here."""
        if not any(k.id == key_id for k in self.state.keys):
            raise RelayError("No such key.", 404)
        async with self._lock:
            try:
                await self.client().call("DELETE", f"/api/owner/keys/{key_id}")
            except RelayError as exc:
                if exc.status != 404:
                    raise
            with self.state._lock:
                self.state.keys = [k for k in self.state.keys if k.id != key_id]
                self.state.save()
        for record in self.queue.list():
            if record.key_id == key_id and not record.relay_gone:
                self.queue.mark_synced(record.id, gone=True)

    # ------------------------------------------------------------ sync
    async def sync(self) -> dict[str, Any]:
        """Send your profile, fetch new requests, send replies and deletions. Errors are kept in the state
        (shown in the app) instead of raised."""
        added = sent = 0
        if self.state.connected:
            async with self._lock:
                try:
                    client = self.client()
                    await self._send_profile(client)
                    added = await self._fetch(client)
                    sent = await self._send_answers(client)
                    await self._send_deletes(client)
                    self.state.error = None
                    self.state.last_sync = now_iso()
                except RelayError as exc:
                    self.state.error = (
                        "The relay doesn't know this app anymore (its account was deleted). "
                        "Disconnect, then connect again." if exc.status == 401 else str(exc)
                    )
                finally:
                    self.state.save()
        return {**self.state.view(), "added": added, "sent": sent}

    async def _send_profile(self, client: RelayClient) -> None:
        owner = self.folder.owner()
        profile = {"name": owner["name"] or "", "headline": owner["headline"] or ""}
        sig = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
        if sig != self.state.profile_sig:
            await client.call("PUT", "/api/owner/profile", json=profile)
            self.state.profile_sig = sig

    async def _fetch(self, client: RelayClient) -> int:
        added = 0
        while True:
            page = await client.call("GET", "/api/owner/requests", params={"after": self.state.cursor, "limit": PAGE})
            rows = page.get("requests") or []
            added += len(await asyncio.to_thread(self.queue.add_from_relay, rows))
            self.state.cursor = max(self.state.cursor, int(page.get("cursor") or 0))
            self.state.save()  # keep the progress even if a later page fails
            if len(rows) < PAGE:
                break
        if added:
            log.info("%d new request(s) from the relay.", added)
        return added

    async def _send_answer(self, client: RelayClient, request_id: str) -> bool:
        record = self.queue.get(request_id)
        try:
            await client.call("POST", f"/api/owner/requests/{record.relay_id}/answer",
                              json={"status": record.status, "reply": record.reply})
        except RelayError as exc:
            if exc.status == 404:  # deleted on the relay: its key was revoked
                self.queue.mark_synced(record.id, gone=True)
                return False
            raise
        self.queue.mark_synced(record.id)
        return True

    async def _send_answers(self, client: RelayClient) -> int:
        return sum([await self._send_answer(client, r.id) for r in self.queue.unsynced()])

    async def _send_deletes(self, client: RelayClient) -> None:
        for relay_id in list(self.state.pending_deletes):
            try:
                await client.call("DELETE", f"/api/owner/requests/{relay_id}")
            except RelayError as exc:
                if exc.status != 404:
                    raise
            self.state.pending_deletes.remove(relay_id)
            self.state.save()

    async def send_answer(self, request_id: str) -> str | None:
        """Send one reply right away (after you reply). Returns why it couldn't be sent; the next sync retries."""
        record = self.queue.get(request_id)
        if not record.relay_id or record.synced or not self.state.connected:
            return None
        async with self._lock:
            try:
                await self._send_answer(self.client(), request_id)
            except RelayError as exc:
                return str(exc)
        return None

    async def forget_request(self, relay_id: str) -> None:
        """A request deleted here is deleted on the relay too (now, or at the next sync)."""
        if not relay_id or not self.state.connected:
            return
        with self.state._lock:
            self.state.pending_deletes.append(relay_id)
            self.state.save()
        async with self._lock:
            with contextlib.suppress(RelayError):
                await self._send_deletes(self.client())

    # ------------------------------------------------------------ background
    def start(self) -> None:
        if self.poll > 0 and self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            if self.state.connected:
                try:
                    await self.sync()
                except Exception:  # noqa: BLE001 — never stop syncing over one bad round
                    log.exception("Relay sync failed.")
            await asyncio.sleep(self.poll)

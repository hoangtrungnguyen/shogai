"""The relay's database (SQLite): the Shōgai apps that use it, the keys they handed out, and requests.

Secrets are never stored, only their SHA-256: an app's account token, every key, and every request's
token. Keys and tokens are long random strings, so a plain hash is enough to make a stolen database
useless for impersonating anyone.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS owners (          -- one per Shōgai app: the person people ask
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    headline TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL,
    last_seen TEXT
);

CREATE TABLE IF NOT EXISTS keys (            -- keys an owner handed out; whoever holds one can ask them
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
    key_hash TEXT NOT NULL UNIQUE,
    created TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS requests (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,   -- the owner's app syncs "everything after seq N"
    id TEXT NOT NULL UNIQUE,
    owner_id TEXT NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
    key_id TEXT NOT NULL REFERENCES keys(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL,                -- held by whoever sent it: with the key, it opens the reply
    name TEXT NOT NULL DEFAULT '',
    question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | replied | declined
    reply TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL,
    delivered TEXT,                          -- when the owner's app picked it up
    answered TEXT
);

CREATE INDEX IF NOT EXISTS requests_by_owner ON requests (owner_id, seq);
"""

STATUSES = ("pending", "replied", "declined")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


class Conflict(Exception):
    pass


class RelayStore:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():  # readable by the relay only; SQLite gives its -wal/-shm files the same mode
            os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
        for file in (path, path.with_name(f"{path.name}-wal"), path.with_name(f"{path.name}-shm")):
            with contextlib.suppress(OSError):  # databases made before this rule
                if file.exists():
                    file.chmod(0o600)
        self.path = path
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _one(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    def _run(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            return self._db.execute(sql, args).rowcount

    # ------------------------------------------------------------------ owners
    def create_owner(self) -> tuple[str, str]:
        """A new account for a Shōgai app; returns (owner id, account token)."""
        owner_id, token = new_id("o"), "sho_" + secrets.token_urlsafe(32)
        self._run("INSERT INTO owners (id, token_hash, created) VALUES (?, ?, ?)", (owner_id, digest(token), now_iso()))
        return owner_id, token

    def owner_by_token(self, token: str) -> dict[str, Any] | None:
        owner = self._one("SELECT * FROM owners WHERE token_hash = ?", (digest(token),)) if token else None
        if owner:
            self._run("UPDATE owners SET last_seen = ? WHERE id = ?", (now_iso(), owner["id"]))
        return owner

    def set_profile(self, owner_id: str, name: str, headline: str) -> None:
        self._run("UPDATE owners SET name = ?, headline = ? WHERE id = ?", (name, headline, owner_id))

    def delete_owner(self, owner_id: str) -> None:
        self._run("DELETE FROM owners WHERE id = ?", (owner_id,))  # keys and requests go with it

    # -------------------------------------------------------------------- keys
    def add_key(self, owner_id: str, key_hash: str) -> str:
        key_id = new_id("k")
        try:
            self._run("INSERT INTO keys (id, owner_id, key_hash, created) VALUES (?, ?, ?, ?)",
                      (key_id, owner_id, key_hash, now_iso()))
        except sqlite3.IntegrityError as exc:
            raise Conflict("This key is already registered.") from exc
        return key_id

    def keys(self, owner_id: str) -> list[dict[str, Any]]:
        return self._all("SELECT id, created FROM keys WHERE owner_id = ? ORDER BY created", (owner_id,))

    def delete_key(self, owner_id: str, key_id: str) -> bool:
        """Revoke a key: it stops working, and the requests sent with it are deleted."""
        return self._run("DELETE FROM keys WHERE id = ? AND owner_id = ?", (key_id, owner_id)) > 0

    def key_by_secret(self, key: str) -> dict[str, Any] | None:
        """The key and the person it belongs to."""
        if not key:
            return None
        return self._one(
            "SELECT keys.id AS key_id, owners.id AS owner_id, owners.name, owners.headline "
            "FROM keys JOIN owners ON owners.id = keys.owner_id WHERE keys.key_hash = ?",
            (digest(key),),
        )

    # ---------------------------------------------------------------- requests
    def count_pending(self, owner_id: str) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM requests WHERE owner_id = ? AND status = 'pending'", (owner_id,))
        return int(row["n"]) if row else 0

    def add_request(self, owner_id: str, key_id: str, name: str, question: str) -> tuple[dict[str, Any], str]:
        """Returns the request and its token: with the key, the token opens the reply."""
        request_id, token = new_id("q"), secrets.token_urlsafe(24)
        self._run(
            "INSERT INTO requests (id, owner_id, key_id, token_hash, name, question, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (request_id, owner_id, key_id, digest(token), name, question, now_iso()),
        )
        return self.request(request_id), token  # type: ignore[return-value]

    def request(self, request_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM requests WHERE id = ?", (request_id,))

    def request_for_sender(self, request_id: str, key_id: str, token: str) -> dict[str, Any] | None:
        """The request, only for whoever sent it: the right key and the request's own token."""
        row = self.request(request_id)
        if not row or not token or row["key_id"] != key_id:
            return None
        return row if secrets.compare_digest(digest(token), row["token_hash"]) else None

    def requests_after(self, owner_id: str, after: int, limit: int) -> list[dict[str, Any]]:
        """New requests for an owner's app; marks them delivered."""
        stamp = now_iso()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                rows = [dict(r) for r in self._db.execute(
                    "SELECT seq, id, key_id, name, question, status, created FROM requests "
                    "WHERE owner_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                    (owner_id, max(0, after), limit),
                ).fetchall()]
                if rows:
                    self._db.execute(
                        f"UPDATE requests SET delivered = ? WHERE delivered IS NULL AND id IN ({','.join('?' * len(rows))})",
                        (stamp, *[r["id"] for r in rows]),
                    )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return rows

    def answer(self, owner_id: str, request_id: str, status: str, reply: str) -> bool:
        return self._run(
            "UPDATE requests SET status = ?, reply = ?, answered = ? WHERE id = ? AND owner_id = ?",
            (status, reply, now_iso(), request_id, owner_id),
        ) > 0

    def delete_request(self, owner_id: str, request_id: str) -> bool:
        return self._run("DELETE FROM requests WHERE id = ? AND owner_id = ?", (request_id, owner_id)) > 0

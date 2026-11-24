"""The Shōgai database: a folder of Markdown files.

Layout (one folder per person)::

    data/people/<slug>/
        profile.md          # the synthesized profile (YAML frontmatter + Markdown)
        sources/sNNN-*.md   # one analysis per ingested source (image, note, video, paper)
        media/              # original uploads (optional, SHOGAI_KEEP_MEDIA)
        history/            # previous versions of profile.md
    data/.trash/            # deleted people are moved here, never hard-deleted

Everything is plain Markdown, so the folder can also be opened in Obsidian,
VS Code, or any editor.
"""

from __future__ import annotations

import os
import re
import shutil
import threading
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .properties import PROPERTY_KEYS, apply_defaults, coerce, coerce_properties, default_properties, is_empty

PROFILE_FILE = "profile.md"
SOURCES_DIR = "sources"
MEDIA_DIR = "media"
HISTORY_DIR = "history"
HISTORY_KEEP = 20
SOURCE_LOG_HEADING = "## Source Log"
FRONTMATTER_ORDER = ["name", "relationship", *PROPERTY_KEYS, "aliases", "tags", "summary", "avatar",
                     "created", "updated", "sources"]

KIND_ICONS = {
    "image": "🖼",
    "text": "📝",
    "video": "🎬",
    "audio": "🎙",
    "academic": "📄",
}

_SLUG_RE = re.compile(r"^[\w][\w-]{0,79}$", re.UNICODE)
_SOURCE_ID_RE = re.compile(r"^s(\d{1,6})$")
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)(.*)\Z", re.S)
_SOURCE_LOG_RE = re.compile(r"^##\s+Source Log\s*$", re.M | re.I)


class StoreError(Exception):
    pass


class NotFound(StoreError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _fold_char(ch: str) -> str:
    if ch in "đĐ":
        return "d"
    if "LATIN" in unicodedata.name(ch, ""):
        decomposed = unicodedata.normalize("NFKD", ch)
        return "".join(c for c in decomposed if not unicodedata.combining(c))
    return ch


def slugify(text: str, max_len: int = 60) -> str:
    """URL/file-safe slug. Latin diacritics are folded (Nguyễn Văn Đạt -> nguyen-van-dat);
    other scripts are kept as-is (田中 太郎 -> 田中-太郎)."""
    folded = "".join(_fold_char(ch) for ch in unicodedata.normalize("NFC", text or ""))
    slug = re.sub(r"[\W_]+", "-", folded.lower(), flags=re.UNICODE)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:max_len].strip("-")


def safe_filename(name: str, max_len: int = 80) -> str:
    base = os.path.basename(name or "").strip()
    stem, ext = os.path.splitext(base)
    stem = slugify(stem, max_len=max_len) or "file"
    ext = re.sub(r"[^A-Za-z0-9.]", "", ext.lower())[:10]
    return f"{stem}{ext}"


def validate_slug(slug: str) -> str:
    if not slug or not _SLUG_RE.match(slug):
        raise NotFound(f"Invalid person id: {slug!r}")
    return slug


def _normalize_meta_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, list):
        return [_normalize_meta_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _normalize_meta_value(v) for k, v in value.items()}
    return value


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text or "")
    if not match:
        return {}, text or ""
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return {str(k): _normalize_meta_value(v) for k, v in meta.items()}, match.group(2)


class _FrontmatterDumper(yaml.SafeDumper):
    """Block-style mappings (one key per line) with short scalar lists kept inline: `tags: [a, b]`."""


def _represent_list(dumper: yaml.SafeDumper, data: list) -> yaml.Node:
    inline = all(isinstance(x, (str, int, float, bool)) and len(str(x)) <= 80 for x in data)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=inline)


_FrontmatterDumper.add_representer(list, _represent_list)
_NUMBER_LIKE_RE = re.compile(r"[-+]?(?:0[xob][0-9a-f]+|[0-9][0-9_]*(?:\.[0-9]*)?|\.[0-9]+)(?:e[-+]?[0-9]+)?", re.I)


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    # Quote strings such as phone numbers ("0965…") that YAML 1.2 readers (Obsidian) would turn into numbers.
    if _NUMBER_LIKE_RE.fullmatch(data):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="'")
    return dumper.represent_str(data)


_FrontmatterDumper.add_representer(str, _represent_str)
# Empty properties are written as `key:` (blank) so Obsidian shows them as empty fields.
_FrontmatterDumper.add_representer(type(None), lambda d, _v: d.represent_scalar("tag:yaml.org,2002:null", ""))


def join_frontmatter(meta: dict[str, Any], body: str) -> str:
    clean = {k: v for k, v in meta.items() if v is not None or k in PROPERTY_KEYS}
    front = yaml.dump(
        clean, Dumper=_FrontmatterDumper, allow_unicode=True, sort_keys=False,
        default_flow_style=False, width=10_000,
    ).strip()
    return f"---\n{front}\n---\n\n{(body or '').strip()}\n"


def order_profile_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Canonical key order for profile frontmatter; every property key is present."""
    apply_defaults(meta)
    ordered ={k: meta[k] for k in FRONTMATTER_ORDER if k in meta}
    ordered.update({k: v for k, v in meta.items() if k not in ordered})
    return ordered


def strip_source_log(body: str) -> str:
    """Remove the app-managed '## Source Log' section (and everything after it)."""
    match = _SOURCE_LOG_RE.search(body or "")
    if match:
        body = body[: match.start()]
    return (body or "").rstrip()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


@dataclass
class SourceRecord:
    id: str
    n: int
    kind: str
    title: str
    summary: str
    created: str
    filename: str
    original: str | None = None
    media: str | None = None
    note: str | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "summary": self.summary,
            "created": self.created,
            "filename": self.filename,
            "original": self.original,
            "media": self.media,
            "note": self.note,
            "url": self.url,
        }


@dataclass
class Person:
    slug: str
    name: str
    relationship: str = ""
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    avatar: str | None = None
    created: str = ""
    updated: str = ""
    source_count: int = 0
    properties: dict[str, Any] = field(default_factory=default_properties)
    body: str = ""
    raw: str = ""

    def to_summary(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "relationship": self.relationship,
            "aliases": self.aliases,
            "tags": self.tags,
            "summary": self.summary,
            "avatar": self.avatar,
            "created": self.created,
            "updated": self.updated,
            "source_count": self.source_count,
            "properties": self.properties,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.to_summary(), "body": self.body, "raw": self.raw}


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.people_dir = self.root / "people"
        self.trash_dir = self.root / ".trash"
        self.people_dir.mkdir(parents=True, exist_ok=True)
        self._global_lock = threading.RLock()
        self._locks: dict[str, threading.RLock] = {}

    # ------------------------------------------------------------------ paths
    def lock(self, slug: str) -> threading.RLock:
        with self._global_lock:
            return self._locks.setdefault(slug, threading.RLock())

    def person_dir(self, slug: str) -> Path:
        return self.people_dir / validate_slug(slug)

    def profile_path(self, slug: str) -> Path:
        return self.person_dir(slug) / PROFILE_FILE

    def exists(self, slug: str) -> bool:
        try:
            return self.profile_path(slug).is_file()
        except NotFound:
            return False

    def _require(self, slug: str) -> Path:
        path = self.profile_path(slug)
        if not path.is_file():
            raise NotFound(f"No person with id {slug!r}")
        return path

    # ----------------------------------------------------------------- people
    def list_people(self) -> list[Person]:
        people: list[Person] = []
        for child in sorted(self.people_dir.iterdir()):
            if child.is_dir() and (child / PROFILE_FILE).is_file() and _SLUG_RE.match(child.name):
                try:
                    people.append(self.get(child.name))
                except StoreError:
                    continue
        people.sort(key=lambda p: p.name.lower())
        return people

    def get(self, slug: str) -> Person:
        path = self._require(slug)
        raw = path.read_text(encoding="utf-8")
        meta, body = split_frontmatter(raw)
        return Person(
            slug=slug,
            name=str(meta.get("name") or slug),
            relationship=str(meta.get("relationship") or ""),
            aliases=_as_list(meta.get("aliases")),
            tags=_as_list(meta.get("tags")),
            summary=str(meta.get("summary") or ""),
            avatar=meta.get("avatar") or None,
            created=str(meta.get("created") or ""),
            updated=str(meta.get("updated") or ""),
            source_count=len(self._source_files(slug)),
            properties=coerce_properties(meta),
            body=strip_source_log(body).strip(),
            raw=raw,
        )

    def create(
        self,
        name: str,
        relationship: str = "",
        aliases: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> Person:
        name = (name or "").strip()
        if not name:
            raise StoreError("Name is required")
        with self._global_lock:
            base = slugify(name) or "person"
            slug, i = base, 2
            while (self.people_dir / slug).exists():
                slug = f"{base}-{i}"
                i += 1
            pdir = self.people_dir / slug
            (pdir / SOURCES_DIR).mkdir(parents=True)
            stamp = now_iso()
            meta = {
                "name": name,
                "relationship": (relationship or "").strip(),
                "aliases": _dedupe(aliases or []),
                "tags": _dedupe(tags or []),
                **default_properties(),
                "summary": "",
                "avatar": None,
                "created": stamp,
                "updated": stamp,
                "sources": 0,
            }
            body = (
                f"# {name}\n\n> _Nothing recorded yet. Feed Shōgai photos, personal notes, videos "
                f"or papers about {name} to build this profile._"
            )
            atomic_write(pdir / PROFILE_FILE,
                         join_frontmatter(order_profile_meta(meta), body + "\n\n" + self._render_source_log(slug)))
        return self.get(slug)

    def save_profile(
        self,
        slug: str,
        *,
        body: str | None = None,
        meta_updates: dict[str, Any] | None = None,
        backup: bool = True,
        touch: bool = True,
    ) -> Person:
        with self.lock(slug):
            path = self._require(slug)
            raw = path.read_text(encoding="utf-8")
            meta, old_body = split_frontmatter(raw)
            if backup:
                self._backup(slug, raw)
            for key, value in (meta_updates or {}).items():
                meta[key] = value
            if touch or not meta.get("updated"):
                meta["updated"] = now_iso()
            meta["sources"] = len(self._source_files(slug))
            new_body = strip_source_log(body if body is not None else old_body)
            atomic_write(path, join_frontmatter(order_profile_meta(meta),
                                                new_body + "\n\n" + self._render_source_log(slug)))
        return self.get(slug)

    def write_raw(self, slug: str, text: str) -> Person:
        """Replace profile.md with user-edited Markdown (frontmatter must keep a name)."""
        with self.lock(slug):
            path = self._require(slug)
            old_meta, _ = split_frontmatter(path.read_text(encoding="utf-8"))
            meta, body = split_frontmatter(text)
            if not str(meta.get("name") or "").strip():
                raise StoreError("The frontmatter must contain a 'name' field.")
            meta.setdefault("created", old_meta.get("created") or now_iso())
            meta["aliases"] = _as_list(meta.get("aliases"))
            meta["tags"] = _as_list(meta.get("tags"))
            self._backup(slug, path.read_text(encoding="utf-8"))
            meta["updated"] = now_iso()
            meta["sources"] = len(self._source_files(slug))
            apply_defaults(meta)  # keep every property present
            atomic_write(
                path,
                join_frontmatter(meta, strip_source_log(body) + "\n\n" + self._render_source_log(slug)),
            )
        return self.get(slug)

    def update_properties(self, slug: str, updates: dict[str, Any]) -> Person:
        """Set properties (validated/normalized); unknown keys are rejected."""
        unknown = set(updates) - set(PROPERTY_KEYS)
        if unknown:
            raise StoreError(f"Unknown properties: {', '.join(sorted(unknown))}")
        return self.save_profile(slug, meta_updates={k: coerce(k, v) for k, v in updates.items()})

    def fill_properties(self, slug: str, found: dict[str, Any]) -> list[str]:
        """Fill only properties that are still empty (never overwrite the user's values).
        related_webs is merged. Returns the keys that changed."""
        with self.lock(slug):
            current = self.get(slug).properties
            updates: dict[str, Any] = {}
            for key, value in found.items():
                value = coerce(key, value)
                if key == "related_webs":
                    merged = current["related_webs"] + [u for u in value if u not in current["related_webs"]]
                    if merged != current["related_webs"]:
                        updates[key] = merged
                elif is_empty(key, current.get(key)) and not is_empty(key, value):
                    updates[key] = value
            if updates:
                self.save_profile(slug, meta_updates=updates)
            return list(updates)

    def ensure_properties(self, slug: str) -> bool:
        """Add any missing property keys to profile.md. Returns True if the file changed."""
        with self.lock(slug):
            meta, _ = split_frontmatter(self._require(slug).read_text(encoding="utf-8"))
            if all(k in meta for k in PROPERTY_KEYS):
                return False
            self.save_profile(slug)
            return True

    def delete(self, slug: str) -> Path:
        with self.lock(slug):
            pdir = self._require(slug).parent
            self.trash_dir.mkdir(parents=True, exist_ok=True)
            target = self.trash_dir / f"{slug}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            shutil.move(str(pdir), str(target))
            return target

    def _backup(self, slug: str, raw: str) -> None:
        hdir = self.person_dir(slug) / HISTORY_DIR
        hdir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        atomic_write(hdir / f"profile-{stamp}.md", raw)
        versions = sorted(hdir.glob("profile-*.md"))
        for old in versions[:-HISTORY_KEEP]:
            old.unlink(missing_ok=True)

    def resolve(self, ref: str) -> str | None:
        """Find a person's slug from a slug, name, alias, or unambiguous partial name."""
        ref = (ref or "").strip()
        if not ref:
            return None
        if self.exists(ref):
            return ref
        slug = slugify(ref)
        if slug and self.exists(slug):
            return slug
        needle = ref.lower()
        people = self.list_people()
        for p in people:
            if needle == p.name.lower() or needle in (a.lower() for a in p.aliases):
                return p.slug
        partial = [
            p for p in people
            if needle in p.name.lower() or any(needle in a.lower() for a in p.aliases)
        ]
        return partial[0].slug if len(partial) == 1 else None

    # ---------------------------------------------------------------- sources
    def _source_files(self, slug: str) -> list[Path]:
        sdir = self.person_dir(slug) / SOURCES_DIR
        if not sdir.is_dir():
            return []
        return sorted(p for p in sdir.glob("s*.md") if p.is_file())

    def _read_source_record(self, path: Path) -> SourceRecord | None:
        meta, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        sid = str(meta.get("id") or path.name.split("-", 1)[0])
        match = _SOURCE_ID_RE.match(sid.lower())
        if not match:
            return None
        return SourceRecord(
            id=f"s{int(match.group(1))}",
            n=int(match.group(1)),
            kind=str(meta.get("kind") or "text"),
            title=str(meta.get("title") or path.stem),
            summary=str(meta.get("summary") or ""),
            created=str(meta.get("created") or ""),
            filename=path.name,
            original=meta.get("original"),
            media=meta.get("media"),
            note=meta.get("note"),
            url=meta.get("url"),
        )

    def list_sources(self, slug: str) -> list[SourceRecord]:
        self._require(slug)
        records = [r for r in (self._read_source_record(p) for p in self._source_files(slug)) if r]
        records.sort(key=lambda r: r.n)
        return records

    def get_source(self, slug: str, source_id: str) -> tuple[SourceRecord, str]:
        match = _SOURCE_ID_RE.match((source_id or "").strip().lower())
        if not match:
            raise NotFound(f"Invalid source id {source_id!r}")
        n = int(match.group(1))
        for record in self.list_sources(slug):
            if record.n == n:
                path = self.person_dir(slug) / SOURCES_DIR / record.filename
                return record, path.read_text(encoding="utf-8")
        raise NotFound(f"No source {source_id!r} for {slug!r}")

    def add_source(
        self,
        slug: str,
        *,
        kind: str,
        title: str,
        summary: str,
        markdown: str,
        original: str | None = None,
        media: str | None = None,
        note: str | None = None,
        url: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> SourceRecord:
        with self.lock(slug):
            self._require(slug)
            existing = self.list_sources(slug)
            n = max((r.n for r in existing), default=0) + 1
            sid = f"s{n}"
            filename = f"s{n:03d}-{kind}-{slugify(title, max_len=40) or 'source'}.md"
            meta: dict[str, Any] = {
                "id": sid,
                "person": slug,
                "kind": kind,
                "title": title.strip() or f"{kind} source",
                "summary": summary.strip(),
                "created": now_iso(),
                "original": original,
                "media": media,
                "url": url,
                "note": note or None,
            }
            meta.update(extra or {})
            atomic_write(self.person_dir(slug) / SOURCES_DIR / filename, join_frontmatter(meta, markdown))
            return SourceRecord(
                id=sid,
                n=n,
                kind=kind,
                title=meta["title"],
                summary=meta["summary"],
                created=meta["created"],
                filename=filename,
                original=original,
                media=media,
                note=note,
                url=url,
            )

    def _render_source_log(self, slug: str) -> str:
        records = [r for r in (self._read_source_record(p) for p in self._source_files(slug)) if r]
        records.sort(key=lambda r: r.n)
        lines = [SOURCE_LOG_HEADING, "", "_Managed by Shōgai — every claim above cites one of these sources._", ""]
        if not records:
            lines.append("_No sources yet._")
        for r in records:
            day = r.created[:10]
            icon = KIND_ICONS.get(r.kind, "•")
            summary = f" — {r.summary}" if r.summary else ""
            lines.append(
                f"- **[{r.id}]** {day} · {icon} {r.kind} · [{r.title}]({SOURCES_DIR}/{r.filename}){summary}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------ media
    def media_dir(self, slug: str) -> Path:
        return self.person_dir(slug) / MEDIA_DIR

    def new_media_path(self, slug: str, original_name: str) -> Path:
        self._require(slug)
        mdir = self.media_dir(slug)
        mdir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return mdir / f"{stamp}-{uuid.uuid4().hex[:4]}-{safe_filename(original_name)}"

    def list_media(self, slug: str) -> list[dict[str, Any]]:
        self._require(slug)
        mdir = self.media_dir(slug)
        if not mdir.is_dir():
            return []
        return [
            {"name": p.name, "size": p.stat().st_size, "path": f"{MEDIA_DIR}/{p.name}"}
            for p in sorted(mdir.iterdir())
            if p.is_file() and not p.name.startswith(".")
        ]

    def media_file(self, slug: str, name: str) -> Path:
        self._require(slug)
        mdir = self.media_dir(slug).resolve()
        path = (mdir / os.path.basename(name or "")).resolve()
        if path.parent != mdir or not path.is_file():
            raise NotFound(f"No media file {name!r}")
        return path

    # ----------------------------------------------------------------- search
    def search(self, query: str, limit: int = 12) -> list[dict[str, Any]]:
        terms = [t for t in re.findall(r"\w+", (query or "").lower(), flags=re.UNICODE) if len(t) > 1]
        if not terms:
            return []
        hits: list[tuple[int, dict[str, Any]]] = []
        for person in self.list_people():
            files: list[tuple[Path, str | None]] = [(self.profile_path(person.slug), None)]
            for record in self.list_sources(person.slug):
                files.append((self.person_dir(person.slug) / SOURCES_DIR / record.filename, record.id))
            for path, source_id in files:
                lines = path.read_text(encoding="utf-8").splitlines()
                for i, line in enumerate(lines):
                    low = line.lower()
                    matched = sum(1 for t in terms if t in low)
                    if not matched:
                        continue
                    score = matched * 10 + sum(low.count(t) for t in terms)
                    context = " ".join(x.strip() for x in lines[max(0, i - 1): i + 2] if x.strip())
                    hits.append((score, {
                        "person": person.slug,
                        "name": person.name,
                        "source_id": source_id,
                        "file": path.relative_to(self.root).as_posix(),
                        "line": i + 1,
                        "snippet": context[:400],
                    }))
        hits.sort(key=lambda h: h[0], reverse=True)
        out, seen = [], set()
        for _, hit in hits:
            key = (hit["file"], hit["snippet"])
            if key in seen:
                continue
            seen.add(key)
            out.append(hit)
            if len(out) >= limit:
                break
        return out

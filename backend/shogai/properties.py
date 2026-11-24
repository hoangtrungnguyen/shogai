"""Structured profile properties (YAML frontmatter of profile.md).

    priority: number        follow_up: boolean
    phone: string           email: string
    linkedin: url           facebook: url
    instagram: name         github: url
    blog: url               related_webs: [url, …]

Every profile has a priority: a whole number where 1 is the highest. Blank or invalid values mean the
default, 4.

They can be filled from the user's own notes: Obsidian-style frontmatter (`fb:`, `linked:`, `web:` …),
labelled lines (`Facebook: …`, `phone:: …`, `[Facebook](…)`) and bare profile links. A bare social
link is only attributed to the person when its handle matches their name; otherwise it is kept in
related_webs so nothing is lost or misattributed. A profile link the user adds for a person on the
Consume page is theirs whatever its handle.
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING, Any, Iterable
from urllib.parse import parse_qs, urlparse

import yaml

if TYPE_CHECKING:  # pragma: no cover
    from .store import Store

PROPERTY_KEYS = [
    "priority", "follow_up", "phone", "email", "linkedin", "facebook", "instagram", "github", "blog", "related_webs",
]
URL_KEYS = {"linkedin", "facebook", "github", "blog"}
DEFAULT_PRIORITY = 4  # 1 = highest


def default_properties() -> dict[str, Any]:
    return {
        "priority": DEFAULT_PRIORITY, "follow_up": False, "phone": None, "email": None, "linkedin": None,
        "facebook": None, "instagram": None, "github": None, "blog": None, "related_webs": [],
    }


def apply_defaults(meta: dict[str, Any]) -> dict[str, Any]:
    """Add the missing property keys to profile frontmatter; a blank priority gets the default."""
    for key, value in default_properties().items():
        meta.setdefault(key, value)
    if meta["priority"] in (None, ""):
        meta["priority"] = DEFAULT_PRIORITY
    return meta


# ------------------------------------------------------------------ normalizing

def normalize_url(value: Any) -> str | None:
    s = str(value or "").strip().strip("<>")
    if not s:
        return None
    if not re.match(r"^[a-z][a-z0-9+.-]*://", s, re.I):
        s = "https://" + s.lstrip("/")
    return s


def instagram_handle(value: Any) -> str | None:
    s = str(value or "").strip()
    m = re.search(r"instagram\.com/([A-Za-z0-9._]+)", s, re.I)
    if m:
        s = m.group(1)
    s = s.strip().lstrip("@").strip("/ ")
    return s or None


def _priority(value: Any) -> int:
    """A whole number >= 1 ("P2" reads as 2). Blank or invalid values give the default."""
    if value is None or isinstance(value, bool):
        return DEFAULT_PRIORITY
    try:
        num = float(re.sub(r"^[pP]\s*", "", str(value).strip()))
    except ValueError:
        return DEFAULT_PRIORITY
    return int(num) if num.is_integer() and num >= 1 else DEFAULT_PRIORITY


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "yes", "y", "1", "on"}


def _text(value: Any) -> str | None:
    s = str(value).strip() if value is not None else ""
    return s or None


def _url_list(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else re.split(r"[\s,]+", str(value))
    out: list[str] = []
    for item in items:
        url = normalize_url(item)
        if url and url not in out:
            out.append(url)
    return out


def coerce(key: str, value: Any) -> Any:
    if key == "priority":
        return _priority(value)
    if key == "follow_up":
        return _boolean(value)
    if key == "instagram":
        return instagram_handle(value)
    if key == "related_webs":
        return _url_list(value)
    if key in URL_KEYS:
        return normalize_url(value)
    return _text(value)


def coerce_properties(meta: dict[str, Any]) -> dict[str, Any]:
    """Read properties from frontmatter leniently (hand edits may use odd types)."""
    props = default_properties()
    for key in PROPERTY_KEYS:
        if key in meta:
            props[key] = coerce(key, meta[key])
    return props


def is_empty(key: str, value: Any) -> bool:
    """Unset or still at its default, so a value found in the notes may fill it."""
    if key == "follow_up":
        return value is not True
    if key == "priority":
        return value in (None, "", DEFAULT_PRIORITY)
    return value in (None, "", [])


# ------------------------------------------------------------------ extraction

_FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---", re.S)
NOTE_KEYS = {
    "priority": "priority", "follow_up": "follow_up", "follow up": "follow_up", "followup": "follow_up",
    "phone": "phone", "sdt": "phone", "sđt": "phone", "tel": "phone", "mobile": "phone",
    "email": "email", "e-mail": "email", "mail": "email",
    "linkedin": "linkedin", "linked": "linkedin",
    "facebook": "facebook", "fb": "facebook",
    "instagram": "instagram", "insta": "instagram", "ig": "instagram",
    "github": "github",
    "blog": "blog",
    "web": "related_webs", "website": "related_webs", "related_webs": "related_webs",
}
_LABEL_RE = re.compile(
    r"(?im)^[\s>*+\-(]*(fb|facebook|insta|instagram|ig|linkedin|linked|github|e-?mail|mail|phone|sđt|sdt|tel|mobile"
    r"|web|website|blog)\s*::?\s*(.+?)\)?\s*$"
)
_MDLINK_LABEL_RE = re.compile(
    r"\[(facebook|fb|instagram|insta|linkedin|github|blog|website|web)\]\(\[?(https?://[^)\s]+)\)", re.I
)
_SOCIAL_RE = re.compile(r"https?://(?:www\.|m\.|vn\.)?(facebook|instagram|linkedin|github)\.com/[^\s)\]>\"'<|]+", re.I)
_URL_RE = re.compile(r"https?://[^\s)\]>\"'<|]+", re.I)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"\+?\d[\d .-]{7,14}\d")

_NOT_PROFILES = {
    "facebook": {"share", "sharer", "sharer.php", "groups", "pages", "watch", "reel", "reels", "story.php", "events",
                 "photo", "photo.php", "permalink.php", "login", "marketplace", "hashtag", "stories", "people", "search"},
    "instagram": {"p", "reel", "reels", "stories", "explore", "tv", "accounts"},
    "github": {"orgs", "topics", "features", "about", "settings", "login", "search", "marketplace"},
}


def profile_link(platform: str, url: str) -> tuple[str, str] | None:
    """(canonical value, handle) for a personal profile URL, or None for posts/shares/searches."""
    u = urlparse(url)
    segs = [s for s in u.path.split("/") if s]
    platform = platform.lower()
    if platform == "facebook":
        if segs and segs[0].lower() == "profile.php":
            ids = parse_qs(u.query).get("id")
            return (f"https://www.facebook.com/profile.php?id={ids[0]}", "") if ids else None
        if not segs or segs[0].lower() in _NOT_PROFILES["facebook"]:
            return None
        return f"https://www.facebook.com/{segs[0]}", segs[0]
    if platform == "instagram":
        if not segs or segs[0].lower() in _NOT_PROFILES["instagram"]:
            return None
        return segs[0], segs[0]
    if platform == "linkedin":
        if len(segs) >= 2 and segs[0].lower() == "in":
            return f"https://www.linkedin.com/in/{segs[1]}", segs[1]
        return None
    if platform == "github":
        if not segs or segs[0].lower() in _NOT_PROFILES["github"]:
            return None
        return f"https://github.com/{segs[0]}", segs[0]
    return None


def _fold(text: str) -> str:
    out = []
    for ch in unicodedata.normalize("NFC", text or ""):
        if ch in "đĐ":
            out.append("d")
        elif "LATIN" in unicodedata.name(ch, ""):
            out.append("".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)))
        else:
            out.append(ch)
    return "".join(out).lower()


def handle_matches(handle: str, names: list[str]) -> bool:
    h = re.sub(r"[^a-z]", "", _fold(handle))
    tokens = {t for n in names for t in re.split(r"[\W_\d]+", _fold(n)) if len(t) >= 3}
    return bool(h) and any(t in h for t in tokens)


def extract_from_note(text: str, names: list[str]) -> dict[str, Any]:
    """Properties found in one note. Only keys with a value are returned."""
    found: dict[str, Any] = {}
    related: list[str] = []

    def put(key: str, value: Any) -> None:
        value = coerce(key, value)
        if key == "related_webs":
            related.extend(v for v in value if v not in related)
        elif not is_empty(key, value) and key not in found:
            found[key] = value

    # 1) the note's own frontmatter
    body = text
    m = _FM_RE.match(text or "")
    if m:
        body = text[m.end():]
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            meta = {}
        if isinstance(meta, dict):
            for raw_key, value in meta.items():
                key = NOTE_KEYS.get(str(raw_key).strip().lower())
                if key and value not in (None, ""):
                    if key in ("facebook", "linkedin", "github", "instagram"):
                        link = profile_link(key, normalize_url(value) or "")
                        value = link[0] if link else value
                    put(key, value)

    # 2) labelled lines: "Facebook: url", "phone:: 09…", "[Facebook](url)"
    for match in _MDLINK_LABEL_RE.finditer(body):
        key = NOTE_KEYS.get(match.group(1).lower())
        link = profile_link(key, match.group(2)) if key in ("facebook", "instagram", "linkedin", "github") else None
        put(key, link[0] if link else match.group(2))
    for match in _LABEL_RE.finditer(body):
        key = NOTE_KEYS.get(match.group(1).lower().replace("-", ""), NOTE_KEYS.get(match.group(1).lower()))
        value = match.group(2).strip()
        if key == "email":
            em = _EMAIL_RE.search(value)
            value = em.group(0) if em else None
        elif key == "phone":
            ph = _PHONE_RE.search(value)
            value = ph.group(0).strip() if ph else None
        else:
            url = _URL_RE.search(value)
            if key in ("facebook", "instagram", "linkedin", "github"):
                link = profile_link(key, url.group(0)) if url else None
                value = link[0] if link else (value if key == "instagram" and not url else None)
            else:
                value = url.group(0) if url else None
        if key and value:
            put(key, value)

    # 3) bare profile links: attribute only when the handle matches the person's name
    for match in _SOCIAL_RE.finditer(body):
        platform = match.group(1).lower()
        link = profile_link(platform, match.group(0))
        if not link:
            continue
        value, handle = link
        if handle and handle_matches(handle, names):
            put(platform, value)
        else:
            put("related_webs", value if platform != "instagram" else f"https://www.instagram.com/{value}")

    # 4) a single e-mail address anywhere in the note
    if "email" not in found:
        emails = {e.lower() for e in _EMAIL_RE.findall(body)}
        if len(emails) == 1:
            put("email", emails.pop())

    if related:
        known = {found.get(k) for k in ("facebook", "linkedin", "github", "blog")}
        related = [u for u in related if u not in known]
        if related:
            found["related_webs"] = related
    return found


def properties_from_links(urls: Iterable[str]) -> dict[str, Any]:
    """Properties from the links the user added for a person on the Consume page.

    Unlike a bare link in a note, a profile link added for this person is taken as theirs, whatever its
    handle. Only the profile itself counts: a post, repository or share link may be someone else's.
    A second profile on the same platform goes to related_webs."""
    found: dict[str, Any] = {}
    related: list[str] = []
    for url in urls:
        match = _SOCIAL_RE.match((url or "").strip())
        if not match:
            continue
        platform = match.group(1).lower()
        segs = [s for s in urlparse(match.group(0)).path.split("/") if s]
        link = profile_link(platform, match.group(0)) if len(segs) == (2 if platform == "linkedin" else 1) else None
        if not link:
            continue
        value = coerce(platform, link[0])
        if platform not in found:
            found[platform] = value
        elif value != found[platform]:
            web = f"https://www.instagram.com/{value}" if platform == "instagram" else value
            if web not in related:
                related.append(web)
    if related:
        found["related_webs"] = related
    return found


def merge_found(target: dict[str, Any], found: dict[str, Any]) -> None:
    for key, value in found.items():
        if key == "related_webs":
            existing = target.setdefault("related_webs", [])
            existing.extend(v for v in value if v not in existing)
        else:
            target.setdefault(key, value)


def properties_from_notes(store: "Store", slug: str) -> dict[str, Any]:
    """Properties found across all of a person's original text notes."""
    from .library import TEXT_NOTE_EXT  # local import: library imports store

    person = store.get(slug)
    names = [person.name, slug, *person.aliases]
    found: dict[str, Any] = {}
    for record in store.list_sources(slug):
        if not record.media or not record.media.lower().endswith(tuple(TEXT_NOTE_EXT)):
            continue
        try:
            path = store.media_file(slug, record.media.split("/", 1)[-1])
        except Exception:  # noqa: BLE001
            continue
        merge_found(found, extract_from_note(path.read_text(encoding="utf-8", errors="replace"), names))
    return found


def migrate_properties(store: "Store") -> dict[str, int]:
    """One-time upgrade of older profiles, keeping their dates:
    - missing property keys are added, pre-filled from the person's own notes;
    - a blank priority gets the default. The notes aren't consulted here: the user may have cleared it.
    Profiles already upgraded are skipped."""
    from .store import split_frontmatter

    stats = {"upgraded": 0, "filled": 0}
    for person in store.list_people():
        meta, _ = split_frontmatter(store.profile_path(person.slug).read_text(encoding="utf-8"))
        missing = [k for k in PROPERTY_KEYS if k not in meta]
        blank_priority = meta.get("priority") in (None, "")
        if not missing and not blank_priority:
            continue
        found = properties_from_notes(store, person.slug) if missing else {}
        updates = {k: coerce(k, v) for k, v in found.items() if k in missing}
        if blank_priority:
            updates.setdefault("priority", DEFAULT_PRIORITY)
        store.save_profile(person.slug, meta_updates=updates, touch=False)  # not a content change
        stats["upgraded"] += 1
        stats["filled"] += sum(1 for k, v in updates.items() if not is_empty(k, v))
    return stats

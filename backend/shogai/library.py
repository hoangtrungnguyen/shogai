"""Academic resources mentioned in the notes (the "Library").

Gemini reads the user's original text notes and lists the academic works they mention. Every
mention must come with a quote that is checked against the note itself, so the library only ever
contains resources that are actually written in the notes. Consumed papers (academic sources) are
added directly with their fact-check verdicts.

Per-note results are cached by content hash in data/library/.index.json, so a scan only sends new
or edited notes to Gemini. data/library/resources.md is a readable export of the catalogue.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .gemini import GeminiService, friendly_error
from .jobs import Job
from .prompts import LIBRARY_SYSTEM, LIBRARY_TASK
from .schemas import FoundResource, LibraryExtraction
from .store import NotFound, Store, StoreError, atomic_write, join_frontmatter, now_iso, split_frontmatter

LIBRARY_DIR = "library"
INDEX_FILE = ".index.json"
CATALOG_FILE = "resources.md"
TEXT_NOTE_EXT = {
    ".md", ".markdown", ".txt", ".csv", ".tsv", ".json", ".log", ".html", ".htm", ".xml",
    ".yaml", ".yml", ".srt", ".vtt", ".rtf",
}
BATCH_CHARS = 40_000
MAX_NOTE_CHARS = 120_000
TYPE_TITLES = {
    "paper": "Papers & studies",
    "report": "Reports",
    "thesis": "Theses",
    "book": "Books",
    "textbook": "Textbooks",
    "chapter": "Chapters",
    "course": "Courses",
    "lecture": "Lectures",
    "other": "Other",
}

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_MDLINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def plain(text: str) -> str:
    """Normalize Markdown text for robust 'is this quote really in the note' checks."""
    s = unicodedata.normalize("NFKC", text or "")
    s = _WIKILINK_RE.sub(lambda m: m.group(2) or m.group(1), s)
    s = _MDLINK_RE.sub(r"\1", s)
    s = re.sub(r"[*_`#>~\"'“”‘’«»「」『』]+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip().casefold()


def title_key(title: str) -> str:
    s = unicodedata.normalize("NFKC", title or "").casefold().strip()
    s = re.sub(r"^(the|a|an)\s+", "", s)
    s = re.sub(r"[\W_]+", "", s)
    return s or hashlib.sha1((title or "").encode()).hexdigest()[:10]


def ground_quote(found: FoundResource, text: str, text_plain: str) -> str | None:
    """Return the quote to store if the mention is really in `text`, else None."""
    quote = plain(found.quote)
    if len(quote) >= 4 and quote in text_plain:
        return found.quote.strip()
    parts = [p.strip() for p in re.split(r"\.\.\.|…", quote) if p.strip()]
    if len(parts) > 1 and all(len(p) >= 6 and p in text_plain for p in parts):
        return found.quote.strip()
    # Fall back to the title as written: show the real line of the note that contains it.
    title = plain(found.title)
    if len(title) >= 3 and title in text_plain:
        for line in text.splitlines():
            if title in plain(line):
                return line.strip()[:300]
    return None


@dataclass
class Note:
    key: str  # path relative to the data folder
    slug: str
    name: str
    source_id: str
    source_file: str
    text: str
    sha: str


class Library:
    def __init__(self, store: Store, gem: GeminiService, settings: Settings):
        self.store = store
        self.gem = gem
        self.settings = settings
        self.dir = Path(settings.data_dir) / LIBRARY_DIR
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ inputs
    def notes(self) -> list[Note]:
        """The user's original text notes: text-file originals attached to sources."""
        out: list[Note] = []
        for person in self.store.list_people():
            for record in self.store.list_sources(person.slug):
                if not record.media or os.path.splitext(record.media)[1].lower() not in TEXT_NOTE_EXT:
                    continue
                try:
                    path = self.store.media_file(person.slug, record.media.split("/", 1)[-1])
                except NotFound:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                out.append(Note(
                    key=f"people/{person.slug}/{record.media}",
                    slug=person.slug,
                    name=person.name,
                    source_id=record.id,
                    source_file=record.filename,
                    text=text,
                    sha=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                ))
        return out

    def papers(self) -> list[dict[str, Any]]:
        """Academic sources the user consumed (PDFs / paper links)."""
        out = []
        for person in self.store.list_people():
            for record in self.store.list_sources(person.slug):
                if record.kind != "academic":
                    continue
                try:
                    _, text = self.store.get_source(person.slug, record.id)
                except StoreError:
                    continue
                meta, body = split_frontmatter(text)
                authors = meta.get("paper_authors")
                if not authors:
                    m = re.search(r"^- \*\*Authors:\*\* (.+)$", body, re.M)
                    authors = [a.strip() for a in m.group(1).split(",")] if m else []
                year = meta.get("paper_year")
                if not year:
                    m = re.search(r"^- \*\*Year / venue:\*\* (\S+)", body, re.M)
                    year = m.group(1) if m and m.group(1) != "n/a" else None
                out.append({
                    "title": str(meta.get("paper_title") or record.title),
                    "authors": list(authors),
                    "year": str(year) if year else None,
                    "venue": meta.get("paper_venue"),
                    "identifier": meta.get("paper_identifier") or record.url,
                    "person": person.slug,
                    "name": person.name,
                    "source_id": record.id,
                    "source_file": record.filename,
                    "relation": meta.get("subject_relation"),
                    "verdicts": list(meta.get("verdicts") or []),
                })
        return out

    # ------------------------------------------------------------------ index
    @property
    def index_path(self) -> Path:
        return self.dir / INDEX_FILE

    def load_index(self) -> dict[str, Any]:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("files"), dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return {"version": 1, "updated": None, "files": {}}

    def save_index(self, index: dict[str, Any]) -> None:
        atomic_write(self.index_path, json.dumps(index, ensure_ascii=False, indent=1))

    def status(self, notes: list[Note] | None = None, index: dict[str, Any] | None = None) -> dict[str, Any]:
        notes = self.notes() if notes is None else notes
        index = self.load_index() if index is None else index
        files = index["files"]
        pending = [n for n in notes if files.get(n.key, {}).get("sha") != n.sha]
        return {
            "notes": len(notes),
            "scanned": len(notes) - len(pending),
            "pending": len(pending),
            "updated": index.get("updated"),
            "scanning": self._lock.locked(),
        }

    # -------------------------------------------------------------- catalogue
    def resources(self, notes: list[Note] | None = None, index: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        notes = self.notes() if notes is None else notes
        files = (self.load_index() if index is None else index)["files"]
        groups: dict[str, dict[str, Any]] = {}

        def group(title: str, rtype: str) -> dict[str, Any]:
            key = title_key(title)
            if key not in groups:
                groups[key] = {
                    "id": hashlib.sha1(key.encode()).hexdigest()[:10],
                    "title": title, "type": rtype, "authors": [], "year": None, "field": None,
                    "identified": False, "mentions": [], "consumed": [],
                }
            return groups[key]

        for note in notes:
            entry = files.get(note.key)
            if not entry or entry.get("sha") != note.sha:
                continue  # new or edited since the last scan
            for m in entry.get("mentions", []):
                res = group(m["title"], m.get("type") or "other")
                if len(m.get("authors") or []) > len(res["authors"]):
                    res["authors"] = list(m["authors"])
                res["year"] = res["year"] or m.get("year")
                res["field"] = res["field"] or m.get("field")
                res["identified"] = res["identified"] or bool(m.get("identified"))
                res["mentions"].append({
                    "person": note.slug, "name": note.name, "source_id": note.source_id,
                    "source_file": note.source_file, "quote": m.get("quote", ""), "context": m.get("context", ""),
                })

        for paper in self.papers():
            res = group(paper["title"], "paper")
            res["type"] = "paper"
            if len(paper["authors"]) > len(res["authors"]):
                res["authors"] = paper["authors"]
            res["year"] = res["year"] or paper["year"]
            res["consumed"].append({k: paper[k] for k in (
                "person", "name", "source_id", "source_file", "relation", "verdicts", "venue", "identifier")})

        out = list(groups.values())
        out.sort(key=lambda r: (-len({m["person"] for m in r["mentions"] + r["consumed"]}), r["title"].casefold()))
        return out

    def snapshot(self) -> dict[str, Any]:
        notes = self.notes()
        index = self.load_index()
        return {"status": self.status(notes, index), "resources": self.resources(notes, index)}

    def write_catalog(self, resources: list[dict[str, Any]]) -> None:
        lines = [
            "# Academic resources",
            "",
            "_Only works mentioned in your notes, each with the line that mentions it. "
            "Generated by Shōgai — rewritten on every scan._",
            "",
        ]
        if not resources:
            lines.append("_No academic resources found in the notes yet._")
        for rtype, heading in TYPE_TITLES.items():
            items = [r for r in resources if r["type"] == rtype]
            if not items:
                continue
            lines += [f"## {heading}", ""]
            for r in items:
                lines.append(f"### {r['title']}")
                meta = " · ".join(x for x in [", ".join(r["authors"]), r["year"] or "", r["field"] or ""] if x)
                if meta or r["identified"]:
                    lines.append(f"_{meta}_" + (" (details completed by Gemini)" if r["identified"] else ""))
                lines.append("")
                for m in r["mentions"]:
                    link = f"../people/{m['person']}/sources/{m['source_file']}"
                    lines.append(f"- [{m['name']}](../people/{m['person']}/profile.md) · [{m['source_id']}]({link}) — {m['context']}")
                    lines.append(f"  > {m['quote']}")
                for c in r["consumed"]:
                    link = f"../people/{c['person']}/sources/{c['source_file']}"
                    verdicts = ", ".join(v.replace("_", " ") for v in c["verdicts"]) or "not fact-checked"
                    lines.append(f"- Consumed for [{c['name']}](../people/{c['person']}/profile.md) · "
                                 f"[{c['source_id']}]({link}) — fact-check: {verdicts}")
                lines.append("")
        meta = {"updated": now_iso(), "resources": len(resources)}
        atomic_write(self.dir / CATALOG_FILE, join_frontmatter(meta, "\n".join(lines)))

    # ------------------------------------------------------------------- scan
    def batches(self, notes: list[Note]) -> list[list[Note]]:
        out: list[list[Note]] = []
        current: list[Note] = []
        size = 0
        for note in notes:
            n = min(len(note.text), MAX_NOTE_CHARS) + 100
            if current and size + n > BATCH_CHARS:
                out.append(current)
                current, size = [], 0
            current.append(note)
            size += n
        if current:
            out.append(current)
        return out

    async def extract(self, batch: list[Note]) -> LibraryExtraction:
        blocks = [
            f"=== NOTE {i} | about: {note.name} ===\n{note.text[:MAX_NOTE_CHARS]}\n=== END NOTE {i} ==="
            for i, note in enumerate(batch, 1)
        ]
        result, _ = await self.gem.generate_structured(
            model=self.settings.model_analyze,
            contents=LIBRARY_TASK.format(notes="\n\n".join(blocks)),
            schema=LibraryExtraction,
            system=LIBRARY_SYSTEM.format(language=self.settings.language),
            thinking="medium",
        )
        return result

    @staticmethod
    def verify(batch: list[Note], result: LibraryExtraction) -> tuple[dict[str, list[dict[str, Any]]], int]:
        """Keep only mentions whose quote (or title) really appears in a note of this batch."""
        plains = [plain(n.text) for n in batch]
        kept: dict[str, list[dict[str, Any]]] = {}
        seen: set[tuple[str, str]] = set()
        dropped = 0
        for found in result.resources:
            order = list(range(len(batch)))
            if 1 <= found.note <= len(batch):  # try the claimed note first
                order.remove(found.note - 1)
                order.insert(0, found.note - 1)
            match = None
            for j in order:
                quote = ground_quote(found, batch[j].text, plains[j])
                if quote:
                    match = (batch[j], quote)
                    break
            if match is None:
                dropped += 1
                continue
            note, quote = match
            if (note.key, title_key(found.title)) in seen:
                continue
            seen.add((note.key, title_key(found.title)))
            kept.setdefault(note.key, []).append({
                "title": found.title.strip(),
                "type": found.type,
                "authors": [a.strip() for a in found.authors if a.strip()],
                "year": (found.year or "").strip() or None,
                "field": (found.field or "").strip() or None,
                "identified": bool(found.identified),
                "context": found.context.strip(),
                "quote": quote,
            })
        return kept, dropped

    async def scan(self, job: Job, force: bool = False) -> None:
        async with self._lock:
            try:
                await self._scan(job, force)
            except Exception as exc:  # noqa: BLE001
                await job.finish("error", {"type": "error", "message": friendly_error(exc)})

    async def _scan(self, job: Job, force: bool) -> None:
        notes = self.notes()
        index = self.load_index()
        files = index["files"]
        live = {n.key for n in notes}
        for key in [k for k in files if k not in live]:
            del files[key]  # person or source deleted
        todo = notes if force else [n for n in notes if files.get(n.key, {}).get("sha") != n.sha]
        batches = self.batches(todo)
        items, start = [], 1
        for i, batch in enumerate(batches, 1):
            items.append({"id": f"b{i}", "kind": "text",
                          "label": f"Notes {start}–{start + len(batch) - 1} of {len(todo)}"})
            start += len(batch)
        await job.emit({"type": "plan", "items": items})
        await job.emit({"type": "stage", "stage": "scanning",
                        "message": f"Scanning {len(todo)} note(s) for academic resources…" if todo
                        else "Every note is already scanned."})

        found = dropped_total = 0
        failed: list[dict[str, str]] = []
        for i, batch in enumerate(batches, 1):
            bid = f"b{i}"
            if failed and "429" in failed[-1]["message"]:
                await job.emit({"type": "item", "id": bid, "status": "error", "message": "Skipped (quota)."})
                failed.append({"id": bid, "message": "Skipped after a quota error."})
                continue
            await job.emit({"type": "item", "id": bid, "status": "running",
                            "message": f"Gemini is reading {len(batch)} note(s)…"})
            try:
                result = await self.extract(batch)
                kept, dropped = self.verify(batch, result)
                for note in batch:
                    files[note.key] = {
                        "sha": note.sha, "scanned": now_iso(), "person": note.slug,
                        "source_id": note.source_id, "mentions": kept.get(note.key, []),
                    }
                index["updated"] = now_iso()
                index["model"] = self.settings.model_analyze
                self.save_index(index)  # keep progress even if a later batch fails
                count = sum(len(v) for v in kept.values())
                found += count
                dropped_total += dropped
                msg = f"{count} mention(s) found"
                if dropped:
                    msg += f" · {dropped} dropped (not verbatim in the notes)"
                await job.emit({"type": "item", "id": bid, "status": "done", "message": msg, "observations": count})
            except Exception as exc:  # noqa: BLE001
                message = friendly_error(exc)
                failed.append({"id": bid, "message": message})
                await job.emit({"type": "item", "id": bid, "status": "error", "message": message})

        if batches and len(failed) == len(batches):
            raise RuntimeError(failed[0]["message"])
        self.save_index(index)
        resources = self.resources(notes, index)
        self.write_catalog(resources)
        changes = [f"Scanned {len(todo) - sum(len(batches[int(f['id'][1:]) - 1]) for f in failed)} note(s)",
                   f"{found} mention(s) found in this scan",
                   f"{len(resources)} resource(s) in the library"]
        if dropped_total:
            changes.append(f"{dropped_total} unverifiable mention(s) dropped — not written in the notes")
        await job.finish("done", {"type": "done", "result": {
            "person": "", "name": "Library", "sources": [], "changes": changes, "failed": failed,
        }})

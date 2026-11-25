"""Your circle as a graph: people are linked through the '## References' section of their profiles.

The section is plain Markdown the user can edit (in the app or in Obsidian). Shōgai adds links it
finds on its own:

- wiki links in the person's notes that name someone in the circle (``[[Trương Bảo Ngọc]]``), and
- other people's full names written in the person's profile.

Names are matched conservatively: a single-word name only counts when nobody else's name contains
that word, and a match that is part of a longer capitalised name ("Vạn Xuân" for "Xuân") is skipped.
Found links are only ever added; removing one is up to the user.
"""

from __future__ import annotations

import re
from functools import cached_property
from typing import Any, Iterable

from .library import person_notes
from .store import Person, Store, slugify, split_references

_WIKI_RE = re.compile(r"\[\[([^\]|#]+)")
_WORD_RE = re.compile(r"\w+")
_PREV_WORD_RE = re.compile(r"(\w+)[ \t]+$")
_NEXT_WORD_RE = re.compile(r"[ \t]+(\w+)")
MIN_NAME_CHARS = 4


def _inside_longer_name(text: str, start: int, end: int) -> bool:
    """True when the match touches another capitalised word on the same line ("Vạn Xuân")."""
    before = _PREV_WORD_RE.search(text[max(0, start - 40): start])
    after = _NEXT_WORD_RE.match(text, end)
    return any(m is not None and m.group(1)[0].isupper() for m in (before, after))


class PeopleIndex:
    """Resolve names, aliases and slugs to people, and find people mentioned in text."""

    def __init__(self, people: Iterable[Person]):
        self.people: dict[str, Person] = {p.slug: p for p in people}
        self._keys: dict[str, set[str]] = {}
        for p in self.people.values():
            for term in (p.name, *p.aliases, p.slug):
                key = slugify(term)
                if key:
                    self._keys.setdefault(key, set()).add(p.slug)

    def resolve(self, target: str) -> str | None:
        """The slug a References entry or wiki link points at (exact slug, name or alias only)."""
        target = (target or "").strip()
        if target in self.people:
            return target
        hits = self._keys.get(slugify(target), set())
        return next(iter(hits)) if len(hits) == 1 else None

    @cached_property
    def _mention_patterns(self) -> list[tuple[re.Pattern[str], str, bool]]:
        """(pattern, slug, single_word) for every name or alias that can be told apart in text."""
        word_owners: dict[str, set[str]] = {}
        for p in self.people.values():
            for word in _WORD_RE.findall(p.name.lower()):
                word_owners.setdefault(word, set()).add(p.slug)
        owners: dict[str, set[str]] = {}
        for p in self.people.values():
            name = " ".join(p.name.split())
            for term in {name, *(" ".join(a.split()) for a in p.aliases)}:
                if len(term) < MIN_NAME_CHARS:
                    continue
                if " " not in term:
                    # One word ("Quang"): only the person's own name, and only if no one else shares it.
                    if term != name or word_owners.get(term.lower(), set()) != {p.slug}:
                        continue
                owners.setdefault(term.lower(), set()).add(p.slug)
        return [
            (re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.I), next(iter(slugs)), " " not in term)
            for term, slugs in owners.items()
            if len(slugs) == 1
        ]

    def mentions(self, text: str, exclude: str = "") -> list[str]:
        """People whose name is written in the text (other than `exclude`), in reading order."""
        first: dict[str, int] = {}
        for pattern, slug, single_word in self._mention_patterns:
            if slug == exclude:
                continue
            for m in pattern.finditer(text):
                if single_word and not m.group(0)[0].isupper():
                    continue  # "rosalind" in running text is a word, "Rosalind" is a name
                if _inside_longer_name(text, m.start(), m.end()):
                    continue
                first[slug] = min(first.get(slug, m.start()), m.start())
                break
        return sorted(first, key=first.__getitem__)

    def wiki_links(self, text: str, exclude: str = "") -> list[str]:
        """People a note links to with Obsidian wiki links ([[Name]], [[folder/Name|label]])."""
        found: list[str] = []
        for m in _WIKI_RE.finditer(text):
            target = m.group(1).strip().split("/")[-1].removesuffix(".md").strip()
            slug = self.resolve(target)
            if slug and slug != exclude and slug not in found:
                found.append(slug)
        return found


def find_links(store: Store, index: PeopleIndex, person: Person) -> list[str]:
    """People this person's notes link to, then people named in their profile."""
    found: list[str] = []
    for note in person_notes(store, person):
        found += [s for s in index.wiki_links(note.text, person.slug) if s not in found]
    rest, _ = split_references(person.body)
    found += [s for s in index.mentions(rest, person.slug) if s not in found]
    return found


def link_people(store: Store, slugs: Iterable[str] | None = None) -> dict[str, Any]:
    """Add the links found in notes and profiles to References sections (all people by default).

    Returns how many profiles were scanned and changed, and the links added per person.
    """
    index = PeopleIndex(store.list_people())
    wanted = index.people.values() if slugs is None else [index.people[s] for s in slugs if s in index.people]
    added: dict[str, list[str]] = {}
    scanned = 0
    for person in wanted:
        scanned += 1
        listed = {index.resolve(t) for t in person.references}
        new = [s for s in find_links(store, index, person) if s not in listed]
        if new:
            got = store.add_references(person.slug, [(s, index.people[s].name) for s in new])
            if got:
                added[person.slug] = got
    return {"scanned": scanned, "profiles": len(added), "links": sum(map(len, added.values())), "added": added}


def build_graph(store: Store) -> dict[str, Any]:
    """Edges between people from their References sections, and entries that match no one."""
    index = PeopleIndex(store.list_people())
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    unresolved: list[dict[str, str]] = []
    for person in index.people.values():
        for target in person.references:
            slug = index.resolve(target)
            if slug is None:
                unresolved.append({"person": person.slug, "target": target})
                continue
            if slug == person.slug:
                continue
            key = (person.slug, slug) if person.slug < slug else (slug, person.slug)
            edge = edges.get(key)
            if edge is None:
                edges[key] = {"source": person.slug, "target": slug, "mutual": False}
            elif edge["source"] != person.slug:
                edge["mutual"] = True
    return {"edges": list(edges.values()), "unresolved": unresolved}

"""Library: only resources really written in the notes survive; scans are cached per note."""

import time

import pytest
from fastapi.testclient import TestClient

from shogai.jobs import Job
from shogai.library import Library, ground_quote, plain, title_key
from shogai.main import create_app
from shogai.schemas import FoundResource, LibraryExtraction

NOTE_A = """# Minh
- Book recommendation: 80-20
- Tặng chị [[Hoàng Thương|Thương]] cuốn sách [[The Secret Garden]] này
- He says I should read Thinking, Fast and Slow before the trip
"""
NOTE_B = "# Lan\n- Đang học giáo trình [[ベトナム人のための日本語]] mỗi tối\n"


def found(note, quote, title, rtype="book", identified=False, authors=()):
    return FoundResource(note=note, quote=quote, title=title, type=rtype, authors=list(authors),
                         identified=identified, context="ctx")


EXTRACTION = LibraryExtraction(resources=[
    found(1, "Book recommendation: 80-20", "The 80/20 Principle", identified=True, authors=["Richard Koch"]),
    found(1, "Tặng chị Thương cuốn sách The Secret Garden", "The Secret Garden"),
    found(1, "He recommended Kahneman's book", "Thinking, Fast and Slow"),  # paraphrased quote, title is in note
    found(1, "read Attached by Levine", "Attached"),  # hallucinated: not in the notes
    found(1, "Đang học giáo trình ベトナム人のための日本語", "ベトナム人のための日本語", "textbook"),  # wrong note no.
])


class FakeGem:
    configured = True

    def __init__(self):
        self.calls = 0

    async def generate_structured(self, **kwargs):
        self.calls += 1
        return EXTRACTION, None


def add_note(store, name, text):
    person = store.create(name)
    path = store.new_media_path(person.slug, "note.md")
    path.write_text(text, encoding="utf-8")
    store.add_source(person.slug, kind="text", title=f"{name} note", summary="", markdown="x",
                     media=f"media/{path.name}", original="note.md")
    return person.slug, path


def test_plain_and_grounding_helpers():
    assert plain("[[Hoàng Thương|Thương]] **bold** [x](http://y)") == "thương bold x"
    assert plain("𝗩𝗼̛́𝗶") == plain("Với")  # styled Unicode letters fold to plain text
    assert title_key("The 80/20 Principle") == title_key("80-20 principle")
    f = found(1, "nothing like this", "Thinking, Fast and Slow")
    assert ground_quote(f, NOTE_A, plain(NOTE_A)) == "- He says I should read Thinking, Fast and Slow before the trip"
    assert ground_quote(found(1, "read Attached by Levine", "Attached"), NOTE_A, plain(NOTE_A)) is None


async def test_scan_keeps_only_grounded_mentions_and_caches(store, settings):
    gem = FakeGem()
    lib = Library(store, gem, settings)
    a_slug, a_path = add_note(store, "Minh", NOTE_A)
    b_slug, _ = add_note(store, "Lan", NOTE_B)
    assert lib.status()["pending"] == 2

    job = Job("library", "", "Library", "scan")
    await lib.scan(job)
    assert job.status == "done", job.error
    assert gem.calls == 1  # both small notes fit in one batch
    assert any("1 unverifiable" in c for c in job.result["changes"])

    resources = {r["title"]: r for r in lib.resources()}
    assert set(resources) == {"The 80/20 Principle", "The Secret Garden", "Thinking, Fast and Slow", "ベトナム人のための日本語"}
    assert "Attached" not in resources
    koch = resources["The 80/20 Principle"]
    assert koch["identified"] and koch["authors"] == ["Richard Koch"]
    assert koch["mentions"][0]["person"] == a_slug and koch["mentions"][0]["source_id"] == "s1"
    assert resources["ベトナム人のための日本語"]["mentions"][0]["person"] == b_slug  # re-homed to the right note
    assert resources["Thinking, Fast and Slow"]["mentions"][0]["quote"].startswith("- He says")

    catalog = (settings.data_dir / "library" / "resources.md").read_text()
    assert "### The 80/20 Principle" in catalog and "Attached" not in catalog

    # Unchanged notes are not sent to Gemini again.
    await lib.scan(Job("library", "", "Library", "scan"))
    assert gem.calls == 1
    # An edited note becomes pending and its old mentions are hidden until rescanned.
    a_path.write_text(NOTE_A + "- new line\n", encoding="utf-8")
    assert lib.status()["pending"] == 1
    assert "The 80/20 Principle" not in {r["title"] for r in lib.resources()}


def test_consumed_papers_are_listed(store, settings):
    person = store.create("Linh")
    md = "---\nid: s1\nkind: academic\ntitle: Paper\npaper_title: Shared Novel Activities\n" \
         "paper_authors: [Linh Tran, Minh Pham]\npaper_year: '2026'\nverdicts: [supported]\n---\n\n# Paper\n"
    store.add_source(person.slug, kind="academic", title="Shared Novel Activities", summary="", markdown="x")
    src = next((store.person_dir(person.slug) / "sources").glob("s001-*.md"))
    src.write_text(md, encoding="utf-8")
    lib = Library(store, FakeGem(), settings)
    [paper] = lib.resources()
    assert paper["type"] == "paper" and paper["authors"] == ["Linh Tran", "Minh Pham"]
    assert paper["consumed"][0]["verdicts"] == ["supported"]


@pytest.fixture
def client(settings):
    gem = FakeGem()
    app = create_app(settings=settings, gemini=gem)
    with TestClient(app) as c:
        c.gem = gem
        yield c


def test_library_api(client):
    store = client.app.state.store
    add_note(store, "Minh", NOTE_A)
    data = client.get("/api/library").json()
    assert data["status"] == {"notes": 1, "scanned": 0, "pending": 1, "updated": None, "scanning": False}
    assert data["resources"] == []
    job = client.post("/api/library/scan").json()
    deadline = time.time() + 5
    while client.get(f"/api/jobs/{job['id']}").json()["status"] == "running" and time.time() < deadline:
        time.sleep(0.05)
    data = client.get("/api/library").json()
    assert data["status"]["pending"] == 0
    assert len(data["resources"]) == 3  # the Japanese textbook is not in this note

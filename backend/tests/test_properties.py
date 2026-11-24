import json
import time

import pytest
from fastapi.testclient import TestClient

from shogai.main import create_app
from shogai.advisor import Advisor
from shogai.ingest import link_kind
from shogai.properties import (
    DEFAULT_PRIORITY,
    PROPERTY_KEYS,
    coerce,
    extract_from_note,
    migrate_properties,
    properties_from_links,
)
from shogai.store import StoreError, join_frontmatter, split_frontmatter

NOTE = """---
priority: 2
follow_up: true
fb: https://www.facebook.com/quocbao.pham.21
linked: https://www.linkedin.com/in/quocbao/?skipRedirect=true
instagram: https://www.instagram.com/quocbao.p/
web: https://quocbao.dev/
---
# Phạm Quốc Bảo
- phone:: 0901234567
- [Github]([https://github.com/quocbao-dev)
- Works at https://www.facebook.com/acmecorpvn
- Shared a post https://www.facebook.com/share/r/18UKR9Cth4/
- Mail me: bao@example.com
"""
NAMES = ["Phạm Quốc Bảo", "pham-quoc-bao"]


def test_extract_from_note_frontmatter_labels_and_bare_links():
    found = extract_from_note(NOTE, NAMES)
    assert found["priority"] == 2 and found["follow_up"] is True
    assert found["facebook"] == "https://www.facebook.com/quocbao.pham.21"
    assert found["linkedin"] == "https://www.linkedin.com/in/quocbao"
    assert found["instagram"] == "quocbao.p"
    assert found["phone"] == "0901234567"
    assert found["github"] == "https://github.com/quocbao-dev"
    assert found["email"] == "bao@example.com"
    # company page doesn't match the person's name -> kept as a related web, not as their facebook
    assert found["related_webs"] == ["https://quocbao.dev/", "https://www.facebook.com/acmecorpvn"]


def test_bare_profile_link_attributed_only_on_name_match():
    assert extract_from_note("Lưu Ngọc Diệp\nhttps://www.instagram.com/ngocdiep.luu/", ["Lưu Ngọc Diệp"]) == {
        "instagram": "ngocdiep.luu"}
    assert extract_from_note("https://www.instagram.com/moonlit_07/", ["Đặng Khánh Linh"]) == {
        "related_webs": ["https://www.instagram.com/moonlit_07"]}
    assert extract_from_note("two mails a@x.io b@y.io", ["X"]) == {}


def test_profile_links_added_for_a_person_are_theirs():
    found = properties_from_links([
        "https://www.instagram.com/moonlit_07?igsh=MTFzd3B4a2E=",  # handle unlike the name: still theirs
        "https://instagram.com/moonlit_07/",  # the same profile again
        "https://m.facebook.com/khanhlinh.dang/",
        "https://www.linkedin.com/in/klinh/?originalSubdomain=vn",
        "https://github.com/klinh-dev",
        "https://www.instagram.com/moonlit.art/",  # a second Instagram profile
    ])
    assert found == {
        "instagram": "moonlit_07",
        "facebook": "https://www.facebook.com/khanhlinh.dang",
        "linkedin": "https://www.linkedin.com/in/klinh",
        "github": "https://github.com/klinh-dev",
        "related_webs": ["https://www.instagram.com/moonlit.art"],
    }
    # posts, repositories and share links may be someone else's; videos and papers aren't profiles
    assert properties_from_links([
        "https://www.instagram.com/p/C0ffee/", "https://www.instagram.com/someone/p/C0ffee/",
        "https://github.com/facebook/react", "https://www.facebook.com/share/1AbCdEf/",
        "https://www.youtube.com/watch?v=abc", "https://arxiv.org/abs/1234.5678",
    ]) == {}


def test_profile_frontmatter_has_all_properties_and_quotes_phone(store):
    person = store.create("Ana Lima")
    meta, _ = split_frontmatter(person.raw)
    assert list(meta)[:12] == ["name", "relationship", *PROPERTY_KEYS]
    assert "priority: 4\n" in person.raw and "related_webs: []" in person.raw
    updated = store.update_properties(person.slug, {"phone": "0901234567", "priority": "1", "follow_up": True,
                                                    "instagram": "@ana", "related_webs": ["ana.dev"]})
    assert "phone: '0901234567'" in updated.raw  # Obsidian would otherwise read a number
    assert updated.properties["priority"] == 1 and updated.properties["instagram"] == "ana"
    assert updated.properties["related_webs"] == ["https://ana.dev"]
    with pytest.raises(StoreError):
        store.update_properties(person.slug, {"twitter": "x"})
    # filling never overwrites the user's values; related_webs is merged
    assert store.fill_properties(person.slug, {"priority": 3, "email": "a@b.co", "related_webs": ["https://x.org"]}) \
        == ["email", "related_webs"]
    props = store.get(person.slug).properties
    assert props["priority"] == 1 and props["related_webs"] == ["https://ana.dev", "https://x.org"]


@pytest.mark.parametrize("raw, level", [
    (1, 1), ("2", 2), (" P3 ", 3), ("p1", 1), (2.0, 2), (7, 7),
    (None, 4), ("", 4), (0, 4), (-1, 4), (2.5, 4), ("high", 4), (True, 4), ("nan", 4), ("inf", 4),
])
def test_priority_is_a_whole_number_from_1_with_default_4(raw, level):
    assert DEFAULT_PRIORITY == 4
    assert coerce("priority", raw) == level


def test_every_profile_has_a_priority(store):
    person = store.create("Ana Lima")
    assert person.properties["priority"] == 4
    # a priority found in the notes replaces the default, but never a level the user chose
    assert store.fill_properties(person.slug, {"priority": 2}) == ["priority"]
    assert store.fill_properties(person.slug, {"priority": 1}) == []
    assert store.get(person.slug).properties["priority"] == 2
    # clearing it resets to the default
    assert store.update_properties(person.slug, {"priority": None}).properties["priority"] == 4
    # a blank priority from a hand edit reads as the default, and is written back as it
    path = store.profile_path(person.slug)
    path.write_text(path.read_text().replace("priority: 4\n", "priority:\n"))
    assert store.get(person.slug).properties["priority"] == 4
    assert "priority: 4\n" in store.write_raw(person.slug, path.read_text()).raw


def test_advisor_tags_only_priorities_off_the_default(store):
    store.create("Ana Lima")
    store.update_properties(store.create("Phạm Quốc Bảo").slug, {"priority": 1})
    prompt = Advisor(store, gem=None, settings=None).system_prompt([])
    assert "1 = highest (no tag means the default, 4)" in prompt
    assert "- Ana Lima (id: ana-lima)\n- Phạm Quốc Bảo (id: pham-quoc-bao) [priority 1]\n" in prompt


def test_migration_fills_from_notes_once(store):
    person = store.create("Phạm Quốc Bảo")
    path = store.profile_path(person.slug)
    meta, body = split_frontmatter(path.read_text())
    legacy = {k: v for k, v in meta.items() if k not in PROPERTY_KEYS}
    path.write_text(join_frontmatter(legacy, body))  # a profile from before properties existed
    note = store.new_media_path(person.slug, "note.md")
    note.write_text(NOTE)
    store.add_source(person.slug, kind="text", title="note", summary="", markdown="x", media=f"media/{note.name}")

    before = store.get(person.slug).updated
    assert migrate_properties(store) == {"upgraded": 1, "filled": 9}
    assert store.get(person.slug).updated == before  # upgrade doesn't count as an edit
    props = store.get(person.slug).properties
    assert props["facebook"] == "https://www.facebook.com/quocbao.pham.21" and props["priority"] == 2
    store.update_properties(person.slug, {"priority": None})  # user clears it: back to the default
    assert migrate_properties(store) == {"upgraded": 0, "filled": 0}
    assert store.get(person.slug).properties["priority"] == 4


def test_migration_gives_blank_priorities_the_default(store):
    person = store.create("Ana Lima")
    note = store.new_media_path(person.slug, "note.md")
    note.write_text("---\npriority: 1\n---\nAna")
    store.add_source(person.slug, kind="text", title="note", summary="", markdown="x", media=f"media/{note.name}")
    path = store.profile_path(person.slug)
    path.write_text(path.read_text().replace("priority: 4\n", "priority:\n"))  # as saved before the default existed

    before = store.get(person.slug).updated
    assert migrate_properties(store) == {"upgraded": 1, "filled": 0}
    after = store.get(person.slug)
    # the key was there, so the user may have cleared it: the notes don't refill it
    assert after.properties["priority"] == 4 and "priority: 4\n" in after.raw
    assert after.updated == before
    assert migrate_properties(store) == {"upgraded": 0, "filled": 0}


@pytest.fixture
def client(settings):
    from test_api import fake_analyzer, fake_synth

    app = create_app(settings=settings, analyzers={k: fake_analyzer for k in ("image", "text", "video", "audio", "academic")},
                     synthesizer=fake_synth)
    with TestClient(app) as c:
        yield c


def test_properties_api_and_ingest_fill(client):
    assert client.post("/api/people", json={"name": "Ana Lima"}).json()["properties"]["priority"] == 4
    for bad in (0, -2, 1.5, "high"):
        assert client.patch("/api/people/ana-lima/properties", json={"priority": bad}).status_code == 422
    r = client.patch("/api/people/ana-lima/properties", json={"priority": 2, "follow_up": True, "blog": "ana.blog"})
    assert r.status_code == 200
    assert r.json()["properties"]["blog"] == "https://ana.blog"
    assert client.patch("/api/people/ana-lima/properties", json={"twitter": "x"}).status_code == 422
    assert client.patch("/api/people/ana-lima/properties", json={"follow_up": "maybe"}).status_code == 422
    assert client.get("/api/people").json()[0]["properties"]["follow_up"] is True

    job = client.post("/api/ingest", data={"person": "ana-lima", "thoughts": "Ana's email: ana@lima.dev\nphone: 0912 345 678"}).json()
    deadline = time.time() + 5
    while (state := client.get(f"/api/jobs/{job['id']}").json())["status"] == "running" and time.time() < deadline:
        time.sleep(0.05)
    assert any("Filled properties" in c for c in state["result"]["changes"]), json.dumps(state["result"])
    props = client.get("/api/people/ana-lima").json()["properties"]
    assert props["email"] == "ana@lima.dev" and props["phone"] == "0912 345 678" and props["priority"] == 2
    r = client.patch("/api/people/ana-lima/properties", json={"priority": None})
    assert r.json()["properties"]["priority"] == 4


def test_ingest_fills_properties_from_profile_links(client):
    """A profile link added in the Consume page's Links box fills the property, whatever its handle."""
    from test_api import wait_job

    slug = client.post("/api/people", json={"name": "Đặng Khánh Linh"}).json()["slug"]
    ig = "https://www.instagram.com/moonlit_07?igsh=MTFzd3B4a2E="
    assert link_kind(ig) == "text"  # read as a web page, not a paper
    job = client.post("/api/ingest", data={"person": slug, "links": json.dumps([{"url": ig, "kind": "text"}])}).json()
    state = wait_job(client, job["id"])
    assert state["status"] == "done", state
    assert len(state["result"]["sources"]) == 1
    assert "Filled properties from the links: instagram" in state["result"]["changes"]
    assert client.get(f"/api/people/{slug}").json()["properties"]["instagram"] == "moonlit_07"


def test_profile_link_fills_property_even_when_gemini_cannot_open_it(settings):
    from test_api import fake_synth, wait_job

    async def login_page(gem, settings, ctx, item, status):
        raise RuntimeError("URL retrieval failed")

    app = create_app(settings=settings, analyzers={"text": login_page}, synthesizer=fake_synth)
    with TestClient(app) as client:
        slug = client.post("/api/people", json={"name": "Đặng Khánh Linh"}).json()["slug"]

        def consume(url):
            job = client.post("/api/ingest", data={"person": slug, "links": json.dumps([{"url": url, "kind": "text"}])})
            return wait_job(client, job.json()["id"])

        state = consume("https://www.instagram.com/moonlit_07/")
        assert state["status"] == "done", state
        assert state["result"]["sources"] == [] and [f["id"] for f in state["result"]["failed"]] == ["i1"]
        assert state["result"]["changes"] == ["Filled properties from the links: instagram"]
        # a value already set is never overwritten; with nothing analyzed or filled, the job fails as before
        state = consume("https://www.instagram.com/someone.else/")
        assert state["status"] == "error" and "None of the sources could be analyzed" in state["error"]
        assert client.get(f"/api/people/{slug}").json()["properties"]["instagram"] == "moonlit_07"

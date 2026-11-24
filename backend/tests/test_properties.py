import json
import time

import pytest
from fastapi.testclient import TestClient

from shogai.main import create_app
from shogai.properties import PROPERTY_KEYS, extract_from_note, migrate_properties
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


def test_profile_frontmatter_has_all_properties_and_quotes_phone(store):
    person = store.create("Ana Lima")
    meta, _ = split_frontmatter(person.raw)
    assert list(meta)[:12] == ["name", "relationship", *PROPERTY_KEYS]
    assert "priority:\n" in person.raw and "related_webs: []" in person.raw
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
    store.update_properties(person.slug, {"priority": None})  # user clears it
    assert migrate_properties(store) == {"upgraded": 0, "filled": 0}
    assert store.get(person.slug).properties["priority"] is None


@pytest.fixture
def client(settings):
    from test_api import fake_analyzer, fake_synth

    app = create_app(settings=settings, analyzers={k: fake_analyzer for k in ("image", "text", "video", "audio", "academic")},
                     synthesizer=fake_synth)
    with TestClient(app) as c:
        yield c


def test_properties_api_and_ingest_fill(client):
    client.post("/api/people", json={"name": "Ana Lima"})
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

import time

import pytest
from fastapi.testclient import TestClient

from shogai.graph import PeopleIndex, build_graph, link_people
from shogai.main import create_app
from shogai.schemas import ProfileUpdate
from shogai.store import reference_targets, split_references
from shogai.synthesis import apply_update

# Invented people only.
SECTION = """- [Đoàn Minh Khoa](../doan-minh-khoa/profile.md) — studied together
- [Rosalind](/people/rosalind)
- [[people/bui-thanh-ha/profile|Hà]]
- [[Lò Văn Sáng]]
- **Mai Quế Chi** — cousin (likely)
- [her blog](https://example.org/rosalind)
_Some note_"""


def test_reference_targets_read_links_wiki_links_and_plain_names():
    assert reference_targets(SECTION) == ["doan-minh-khoa", "rosalind", "bui-thanh-ha", "Lò Văn Sáng", "Mai Quế Chi"]
    body = "# A\n\nIntro\n\n## References\n\n- [[B]]\n\n## My own notes\n\nkeep me"
    rest, section = split_references(body)
    assert section == "- [[B]]"
    assert rest == "# A\n\nIntro\n\n## My own notes\n\nkeep me"
    assert split_references("# A\n\nno links") == ("# A\n\nno links", None)


def _person_with_note(store, name, note_text):
    person = store.create(name)
    note = store.new_media_path(person.slug, "note.md")
    note.write_text(note_text)
    store.add_source(person.slug, kind="text", title="note", summary="", markdown="x", media=f"media/{note.name}")
    return person


def test_synthesis_and_edits_keep_the_references_section(store):
    a, b = store.create("Lò Văn Sáng"), store.create("Rosalind")
    assert store.add_references(a.slug, [(b.slug, b.name), ("nobody", "Nobody"), (a.slug, a.name)]) == ["rosalind"]
    assert store.add_references(a.slug, [(b.slug, b.name)]) == []  # already listed
    assert store.get(a.slug).references == ["rosalind"]

    # Gemini's rewrite has no References section (or makes one up): the stored links stay, at the end.
    update = ProfileUpdate(summary="s", profile_markdown="# Lò Văn Sáng\n\n- Likes tea [s1]\n\n## References\n\n- [[X]]",
                           changes=[])
    person = apply_update(store, store.get(a.slug), update)
    assert person.references == ["rosalind"]
    assert person.raw.index("- Likes tea") < person.raw.index("## References") < person.raw.index("## Source Log")

    # A hand edit is the user's call: the section can move, be edited, or go.
    raw = person.raw.replace("- Likes tea [s1]", "## References\n\n- [[Rosalind]]\n\n## Mine\n\nnote")
    raw = raw.replace("## References\n\n- [Rosalind](../rosalind/profile.md)\n\n", "")
    person = store.write_raw(a.slug, raw)
    assert person.references == ["Rosalind"]
    assert person.raw.index("## Mine") < person.raw.index("## References")
    person = store.write_raw(a.slug, person.raw.replace("## References\n\n- [[Rosalind]]", ""))
    assert person.references == [] and "## References" not in person.raw


def test_mentions_skip_shared_given_names_and_longer_names(store):
    for name in ("Đoàn Minh Khoa", "Khoa", "Rosalind", "Bùi Thanh Hà", "Lò Văn Sáng"):
        store.create(name)
    index = PeopleIndex(store.list_people())
    text = ("Studied with Đoàn Minh Khoa [s1]. She met Rosalind at a fair. Khoa means department here. "
            "Trần Bùi Thanh Hà is someone else.")
    assert index.mentions(text, "lo-van-sang") == ["doan-minh-khoa", "rosalind"]
    assert index.mentions("Walked in Rosalind Park; rosalind is a flower.", "lo-van-sang") == []
    assert index.mentions("Talked to Rosalind.", "rosalind") == []  # never oneself
    assert index.wiki_links("See [[Rosalind]], [[Vault/Đoàn Minh Khoa.md|Khoa]] and [[2024-01-02]]") == [
        "rosalind", "doan-minh-khoa"]


def test_link_people_fills_references_and_builds_the_graph(store):
    _person_with_note(store, "Lò Văn Sáng", "Coffee with [[Rosalind]] and [[Unknown Person]].")
    rosalind = store.create("Rosalind")
    store.save_profile(rosalind.slug, body="# Rosalind\n\n- Sister of Lò Văn Sáng [s1]")
    store.create("Mai Quế Chi")
    store.write_raw("mai-que-chi", store.get("mai-que-chi").raw.replace(
        "## Source Log", "## References\n\n- Rosalind — friend\n- Someone Unknown\n\n## Source Log"))
    before = store.get("lo-van-sang").updated

    result = link_people(store)
    assert result["added"] == {"lo-van-sang": ["rosalind"], "rosalind": ["lo-van-sang"]}
    assert result["links"] == 2 and result["scanned"] == 3
    assert store.get("lo-van-sang").updated == before  # adding links isn't an edit
    assert "- [Rosalind](../rosalind/profile.md)" in store.get("lo-van-sang").raw
    assert link_people(store)["links"] == 0

    graph = build_graph(store)
    edges = {(e["source"], e["target"]): e["mutual"] for e in graph["edges"]}
    assert edges == {("lo-van-sang", "rosalind"): True, ("mai-que-chi", "rosalind"): False}
    assert graph["unresolved"] == [{"person": "mai-que-chi", "target": "Someone Unknown"}]


@pytest.fixture
def client(settings):
    from test_api import fake_analyzer, fake_synth

    app = create_app(settings=settings, analyzers={k: fake_analyzer for k in ("image", "text", "video", "audio", "academic")},
                     synthesizer=fake_synth)
    with TestClient(app) as c:
        yield c


def test_graph_api_and_ingest_links(client):
    for name in ("Lò Văn Sáng", "Rosalind"):
        client.post("/api/people", json={"name": name})
    assert client.get("/api/graph").json() == {"edges": [], "unresolved": []}

    job = client.post("/api/ingest", data={"person": "lo-van-sang", "thoughts": "Dinner with [[Rosalind]] on Friday."}).json()
    deadline = time.time() + 5
    while (state := client.get(f"/api/jobs/{job['id']}").json())["status"] == "running" and time.time() < deadline:
        time.sleep(0.05)
    assert "Linked to Rosalind (References)" in state["result"]["changes"]
    assert client.get("/api/graph").json()["edges"] == [{"source": "lo-van-sang", "target": "rosalind", "mutual": False}]

    r = client.post("/api/graph/link").json()
    assert r["links"] == 0 and len(r["graph"]["edges"]) == 1

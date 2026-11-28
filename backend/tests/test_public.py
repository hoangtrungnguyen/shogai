"""About me (public): the public folder, the read-only public listener, and the local request queue."""

import asyncio
import socket
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from shogai.main import create_app
from shogai.public import PublicFolder, PublicServer, RequestQueue, create_public_app
from shogai.store import StoreError


class FakeDraftGem:
    configured = True

    def __init__(self):
        self.calls = []

    async def generate_text(self, *, model, contents, system=None, tools=None, thinking=None):
        self.calls.append({"contents": contents, "system": system})
        return "  Hi! I work on [your project].\n", None


@pytest.fixture
def gem():
    return FakeDraftGem()


@pytest.fixture
def client(settings, gem):
    with TestClient(create_app(settings=settings, gemini=gem)) as c:
        yield c


@pytest.fixture
def public_client(settings):
    with TestClient(create_public_app(settings)) as c:
        yield c


@pytest.fixture
def queue(settings):
    return RequestQueue(settings.data_dir / "requests", settings.data_dir / ".trash")


def write(settings, rel, text):
    path = settings.public_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def relay_item(n=1, question="Which tea do you like?", name="Bao"):
    return {"id": f"q_{n:016x}", "key_id": "k_00000000000000aa", "name": name, "question": question,
            "created": "2026-11-26T06:00:00+00:00"}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------ the public folder

def test_public_folder_serves_only_public_markdown(settings, store, public_client):
    store.create("Zebediah Quux")  # someone in the private circle
    write(settings, "about.md", "---\nname: Trung Nguyen\nheadline: Builds things\n---\n\n# About me\n\nI like tea.\n")
    write(settings, "work/projects.md", "---\ntags: !!set {a, b}\n---\n\n# Projects\n\nShōgai, a notebook.\n")
    write(settings, "later.md", "---\ndraft: true\n---\n\nMy salary.\n")
    write(settings, ".obsidian/workspace.md", "hidden")
    write(settings, ".notes.md", "hidden")
    write(settings, "photo.png", "not markdown")

    info = public_client.get("/api/public").json()
    assert info["owner"] == {"name": "Trung Nguyen", "headline": "Builds things"}
    assert info["relay"] is None
    assert [f["path"] for f in info["files"]] == ["about.md", "work/projects.md"]
    assert info["files"][1]["title"] == "Projects" and "body" not in info["files"][1]
    full = public_client.get("/api/public", params={"full": "true"}).json()
    assert full["files"][0]["body"].endswith("I like tea.")

    projects = public_client.get("/api/public/files/work/projects.md").json()
    assert projects["body"].startswith("# Projects")
    assert sorted(projects["meta"]["tags"].strip("{}").replace("'", "").split(", ")) == ["a", "b"]  # a YAML set, as text
    assert public_client.get("/api/public/files/later.md").status_code == 404  # a draft
    for bad in ["..%2Fpeople%2Fzebediah-quux%2Fprofile.md", ".obsidian/workspace.md", ".notes.md", "photo.png",
                "work%2F..%2Flater.md", "missing.md"]:
        assert public_client.get(f"/api/public/files/{bad}").status_code in (400, 404), bad

    bundle = public_client.get("/api/public/all")
    assert bundle.headers["content-type"].startswith("text/markdown")
    assert "I like tea." in bundle.text and "Shōgai, a notebook." in bundle.text
    llms = public_client.get("/llms.txt").text
    assert llms.startswith("# Trung Nguyen") and "ask them for a key" in llms
    for text in (bundle.text, llms):
        assert "salary" not in text and "hidden" not in text and "Zebediah" not in text

    hits = public_client.get("/api/public/search", params={"q": "tea"}).json()["results"]
    assert [h["path"] for h in hits] == ["about.md"] and hits[0]["line"] == 8
    for q in ["salary", "Zebediah", "hidden"]:
        assert public_client.get("/api/public/search", params={"q": q}).json()["results"] == [], q


@pytest.mark.parametrize("rel", ["../x.md", "a/../../x.md", ".obsidian/a.md", "/etc/passwd.md", "a//b.md",
                                 "notes.txt", " a.md", "a.md/", "a\x00.md"])
def test_public_folder_rejects_paths_outside_or_hidden(tmp_path, rel):
    with pytest.raises(StoreError):
        PublicFolder(tmp_path / "public", tmp_path / "trash").read(rel)


def test_links_pointing_outside_the_folder_are_ignored(tmp_path):
    root = tmp_path / "public"
    root.mkdir()
    (tmp_path / "private.md").write_text("secret")
    (root / "link.md").symlink_to(tmp_path / "private.md")
    (root / "ok.md").write_text("# Ok")
    folder = PublicFolder(root, tmp_path / "trash")
    assert [f.path for f in folder.files(include_drafts=True)] == ["ok.md"]
    with pytest.raises(StoreError):
        folder.read("link.md")


def test_owner_edits_public_files(client, settings):
    saved = client.put("/api/me/files/about.md", json={"markdown": "---\r\nname: Trung\r\n---\r\n\r\n# About me"})
    assert saved.status_code == 200 and saved.json()["raw"] == "---\nname: Trung\n---\n\n# About me\n"
    client.put("/api/me/files/notes/later.md", json={"markdown": "---\ndraft: true\n---\n\nNot yet."})
    me = client.get("/api/me").json()
    assert me["owner"]["name"] == "Trung" and me["folder"] == str(settings.public_root)
    assert {f["path"]: f["draft"] for f in me["files"]} == {"about.md": False, "notes/later.md": True}
    assert me["public"]["enabled"] is False  # public_port=0 in tests
    assert [f["path"] for f in client.get("/api/public").json()["files"]] == ["about.md"]
    assert client.get("/api/me/files/notes/later.md").json()["body"] == "Not yet."

    assert client.put("/api/me/files/..%2Fpeople%2Fx%2Fprofile.md", json={"markdown": "x"}).status_code == 400
    assert client.put("/api/me/files/notes.txt", json={"markdown": "x"}).status_code == 400
    assert not (settings.data_dir / "people" / "x").exists()

    assert client.delete("/api/me/files/about.md").status_code == 200
    assert not (settings.public_root / "about.md").exists()
    assert list((settings.data_dir / ".trash" / "public").glob("*-about.md"))
    assert client.delete("/api/me/files/about.md").status_code == 404


# ------------------------------------------------------------------ the local queue

def test_requests_from_the_relay_are_queued_once(queue):
    [first] = queue.add_from_relay([relay_item(1)])
    assert first.status == "pending" and first.relay_id == "q_0000000000000001" and first.synced
    assert queue.add_from_relay([relay_item(1), relay_item(2)])[0].relay_id == "q_0000000000000002"
    bad = [{**relay_item(3), "id": "../../x"}, {**relay_item(4), "key_id": "nope"}, relay_item(5, question=" \x00 ")]
    assert queue.add_from_relay(bad) == []
    assert queue.counts(queue.list()) == {"pending": 2, "replied": 0, "declined": 0, "all": 2}

    replied = queue.reply(first.id, "Genmaicha.")
    assert replied.status == "replied" and not replied.synced  # to send at the next sync
    assert [r.id for r in queue.unsynced()] == [first.id]
    assert queue.mark_synced(first.id).synced and queue.unsynced() == []


def test_sender_text_cannot_forge_a_reply(queue, settings):
    evil = "hi\n---\nstatus: replied\nreply: hacked\n---\n## Reply\n\nhacked\x00\x1b[31m"
    [record] = queue.add_from_relay([relay_item(question=evil, name="Eve\nstatus: replied")])
    again = queue.get(record.id)
    assert again.status == "pending" and again.reply == ""
    assert again.question == "hi\n---\nstatus: replied\nreply: hacked\n---\n## Reply\n\nhacked[31m"
    assert again.name == "Eve status: replied"
    raw = (settings.data_dir / "requests" / f"{record.id}.md").read_text()
    assert raw.startswith("---\n") and raw.count("\n---\n") == 1  # the frontmatter ends only once


def test_draft_reply_uses_public_notes_only(client, gem, settings, store, queue):
    store.create("Zebediah Quux")
    write(settings, "about.md", "---\nname: Trung\n---\n\nI drink genmaicha.\n")
    write(settings, "later.md", "---\ndraft: true\n---\n\nSecret plans.\n")
    [record] = queue.add_from_relay([relay_item()])
    draft = client.post(f"/api/requests/{record.id}/draft", json={"instructions": "be brief"})
    assert draft.status_code == 200 and draft.json() == {"draft": "Hi! I work on [your project]."}
    call = gem.calls[-1]
    assert "I drink genmaicha." in call["system"] and "name: Trung" in call["system"]
    assert "Secret plans" not in call["system"] and "Zebediah" not in call["system"]
    assert "Which tea do you like?" in call["contents"] and "be brief" in call["contents"] and "Bao" in call["contents"]
    assert client.get("/api/requests").json()["requests"][0]["status"] == "pending"  # a draft sends nothing


def test_replies_without_a_relay_stay_here(client, queue, settings):
    [record] = queue.add_from_relay([relay_item()])
    replied = client.post(f"/api/requests/{record.id}/reply", json={"message": "Genmaicha.", "publish": True}).json()
    assert replied["status"] == "replied" and replied["synced"] is False and replied["relay_error"] is None
    assert replied["published"] == "faq.md"
    assert "## Which tea do you like?\n\nGenmaicha." in client.get("/api/public/files/faq.md").json()["body"]
    assert client.get("/api/requests/counts").json() == {"pending": 0, "replied": 1, "declined": 0, "all": 1}
    assert client.get("/api/requests", params={"status": "nope"}).status_code == 400
    assert client.post("/api/requests/not-a-request/reply", json={"message": "x"}).status_code == 404
    assert client.post(f"/api/requests/{record.id}/reply", json={"message": "   "}).status_code == 400
    assert client.delete(f"/api/requests/{record.id}").status_code == 200
    assert (settings.data_dir / ".trash" / "requests" / f"{record.id}.md").is_file()


# ------------------------------------------------------------------ the public listener

def test_public_listener_is_read_only(public_client, store):
    store.create("Ana")
    assert public_client.get("/api/public").status_code == 200
    for path in ["/api/people", "/api/people/ana", "/api/me", "/api/me/files/about.md", "/api/requests",
                 "/api/relay", "/api/graph", "/api/health", "/api/jobs", "/api/library"]:
        assert public_client.get(path).status_code == 404, path
    for path in ["/api/public/requests", "/api/chat", "/api/ingest", "/api/people", "/api/relay/connect"]:
        assert public_client.post(path, json={}).status_code in (404, 405), path
    assert set(public_client.get("/openapi.json").json()["paths"]) == {
        "/api/public", "/api/public/files/{path}", "/api/public/all", "/api/public/search",
    }
    page = public_client.get("/")
    assert page.status_code == 200 and "isn't built" in page.text
    assert page.headers["referrer-policy"] == "no-referrer"


async def test_public_server_runs_on_its_own_port(settings):
    port = free_port()
    server = PublicServer(create_public_app(settings), "127.0.0.1", port)
    assert await server.start()
    for _ in range(100):
        if server.running:
            break
        await asyncio.sleep(0.02)
    assert server.running and server.local_url == f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient(base_url=server.local_url) as http:
        assert (await http.get("/api/public")).status_code == 200
        assert (await http.get("/api/people")).status_code == 404
    # A second listener on the same port reports why instead of taking the app down.
    busy = PublicServer(create_public_app(settings), "127.0.0.1", port)
    assert await busy.start() is False and "Couldn't listen" in busy.error
    await server.stop()
    assert not server.running


def test_main_app_starts_and_stops_the_public_listener(settings, gem):
    port = free_port()
    app = create_app(settings=settings.with_overrides(public_port=port), gemini=gem)
    with TestClient(app) as c:
        deadline = time.time() + 5
        while not (status := c.get("/api/me").json()["public"])["running"] and time.time() < deadline:
            time.sleep(0.02)
        assert status == {"enabled": True, "running": True, "local_url": f"http://127.0.0.1:{port}",
                          "url": None, "error": None}
        assert httpx.get(f"http://127.0.0.1:{port}/api/public").status_code == 200
        assert httpx.get(f"http://127.0.0.1:{port}/api/people").status_code == 404
    with pytest.raises(httpx.ConnectError):
        httpx.get(f"http://127.0.0.1:{port}/api/public", timeout=1)

"""The local app and a Shōgai Relay, end to end: the real relay app runs in-process (relay/shogai_relay)."""

import sqlite3
import stat
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from shogai.main import create_app

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "relay"))
from shogai_relay.app import Settings as RelaySettings  # noqa: E402
from shogai_relay.app import create_app as create_relay  # noqa: E402

SERVER = "http://relay.test"


class FakeGem:
    configured = True


class Switch(httpx.AsyncBaseTransport):
    """Reaches the relay app in-process; `down = True` makes the relay unreachable."""

    def __init__(self, app):
        self.inner, self.down = httpx.ASGITransport(app=app), False

    async def handle_async_request(self, request):
        if self.down:
            raise httpx.ConnectError("relay is down", request=request)
        return await self.inner.handle_async_request(request)


@pytest.fixture
def relay_app(tmp_path):
    return create_relay(RelaySettings(data_dir=tmp_path / "relay", signup_code="letmein"))


@pytest.fixture
def network(relay_app):
    return Switch(relay_app)


@pytest.fixture
def requester(relay_app):
    """Someone you gave a key, using the relay."""
    with TestClient(relay_app) as c:
        yield c


@pytest.fixture
def client(settings, network):
    with TestClient(create_app(settings=settings, gemini=FakeGem(), relay_transport=network)) as c:
        yield c


def connect(client):
    resp = client.post("/api/relay/connect", json={"server": SERVER + "/", "code": "letmein"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def new_key(client, label):
    resp = client.post("/api/relay/keys", json={"label": label})
    assert resp.status_code == 201, resp.text
    return resp.json()["key"]


def ask(requester, key, question="Which tea do you like?", name="Linh Tran"):
    resp = requester.post("/api/requests", json={"question": question, "name": name},
                          headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def seen(requester, key, ticket):
    return requester.get(f"/api/requests/{ticket['id']}",
                         headers={"Authorization": f"Bearer {key}", "X-Request-Token": ticket["token"]})


def test_connecting_needs_the_relays_code(client, settings):
    assert client.get("/api/relay").json()["connected"] is False
    assert client.post("/api/relay/connect", json={"server": "ftp://relay.test"}).status_code == 400
    wrong = client.post("/api/relay/connect", json={"server": SERVER, "code": "nope"})
    assert wrong.status_code == 400 and "sign-up code" in wrong.json()["detail"]
    view = connect(client)
    assert view["connected"] and view["server"] == SERVER and view["error"] is None and view["insecure"] is True
    state = settings.data_dir / "relay.json"
    assert stat.S_IMODE(state.stat().st_mode) == 0o600  # it holds this app's account token
    assert client.post("/api/relay/connect", json={"server": SERVER, "code": "letmein"}).status_code == 400


def test_keys_requests_and_replies_end_to_end(client, requester, relay_app, settings, store):
    store.create("Linh Trần", "cousin")
    client.put("/api/me/files/about.md", json={"markdown": "---\nname: Alex Demo\nheadline: Builds things\n---\n\nHi."})
    connect(client)  # also sends the name and headline
    key = new_key(client, "Linh")
    assert key["key"].startswith("shk_") and key["label"] == "Linh"
    assert requester.get("/api/key", headers={"Authorization": f"Bearer {key['key']}"}).json()["to"] == {
        "name": "Alex Demo", "headline": "Builds things"}
    dump = "\n".join(sqlite3.connect(relay_app.state.store.path).iterdump())
    assert key["key"] not in dump and "Linh" not in dump  # the relay has the key's hash, never the key or its label

    tea = ask(requester, key["key"])
    home = ask(requester, key["key"], question="Where do you live?")
    synced = client.post("/api/relay/sync").json()
    assert synced["added"] == 2 and synced["error"] is None and synced["last_sync"]
    assert client.post("/api/relay/sync").json()["added"] == 0  # nothing fetched twice
    assert seen(requester, key["key"], tea).json()["delivered"]  # the sender sees it reached you

    listing = client.get("/api/requests").json()
    assert listing["counts"]["pending"] == 2
    request = next(r for r in listing["requests"] if r["question"] == "Which tea do you like?")
    assert request["key_label"] == "Linh" and request["name"] == "Linh Tran" and request["relay_id"] == tea["id"]
    assert request["match"]["slug"] == "linh-tran"  # same name as someone in your circle (not proof)

    replied = client.post(f"/api/requests/{request['id']}/reply", json={"message": "Genmaicha."}).json()
    assert replied["synced"] is True and replied["relay_error"] is None
    answer = seen(requester, key["key"], tea).json()
    assert answer["status"] == "replied" and answer["reply"] == "Genmaicha."

    other = next(r for r in listing["requests"] if r["question"] == "Where do you live?")
    client.post(f"/api/requests/{other['id']}/decline", json={"message": "I keep that private."})
    assert seen(requester, key["key"], home).json()["reply"] == "I keep that private."

    # Deleting a request here deletes it on the relay.
    assert client.delete(f"/api/requests/{other['id']}").status_code == 200
    assert seen(requester, key["key"], home).status_code == 404


def test_replies_wait_while_the_relay_is_down(client, requester, network):
    connect(client)
    key = new_key(client, "Bao")["key"]
    ticket = ask(requester, key)
    client.post("/api/relay/sync")
    [request] = client.get("/api/requests").json()["requests"]

    network.down = True
    replied = client.post(f"/api/requests/{request['id']}/reply", json={"message": "Genmaicha."}).json()
    assert replied["status"] == "replied" and replied["synced"] is False
    assert "Couldn't reach the relay" in replied["relay_error"]
    assert "Couldn't reach the relay" in client.post("/api/relay/sync").json()["error"]
    assert seen(requester, key, ticket).json()["status"] == "pending"

    network.down = False
    after = client.post("/api/relay/sync").json()
    assert after["sent"] == 1 and after["error"] is None
    assert seen(requester, key, ticket).json()["reply"] == "Genmaicha."
    assert client.get("/api/requests").json()["requests"][0]["synced"] is True


def test_revoking_a_key(client, requester):
    connect(client)
    gone, kept = new_key(client, "Old phone"), new_key(client, "Linh")
    ask(requester, gone["key"])
    client.post("/api/relay/sync")
    view = client.delete(f"/api/relay/keys/{gone['id']}").json()
    assert [k["label"] for k in view["keys"]] == ["Linh"]
    assert requester.get("/api/key", headers={"Authorization": f"Bearer {gone['key']}"}).status_code == 401
    assert requester.get("/api/key", headers={"Authorization": f"Bearer {kept['key']}"}).status_code == 200
    [request] = client.get("/api/requests").json()["requests"]
    assert request["relay_gone"] is True  # your copy stays; a reply can't reach the sender anymore
    replied = client.post(f"/api/requests/{request['id']}/reply", json={"message": "Too late"}).json()
    assert replied["synced"] is True and replied["relay_error"] is None
    assert client.delete(f"/api/relay/keys/{gone['id']}").status_code == 400  # no such key


def test_disconnecting_deletes_the_account_on_the_relay(client, requester, settings):
    connect(client)
    key = new_key(client, "Linh")["key"]
    result = client.post("/api/relay/disconnect").json()
    assert result["connected"] is False and result["warning"] is None and result["keys"] == []
    assert not (settings.data_dir / "relay.json").exists()
    assert requester.get("/api/key", headers={"Authorization": f"Bearer {key}"}).status_code == 401


def test_links_use_localhost_for_a_relay_reached_through_docker(tmp_path):
    from shogai.relay import RelayState

    state = RelayState(tmp_path / "relay.json")
    state.server, state.token = "http://host.docker.internal:8090", "sho_x"
    view = state.view()
    assert view["share_url"] == "http://localhost:8090" and view["insecure"] is False
    state.server = "https://relay.example.com"
    assert state.view()["share_url"] == "https://relay.example.com"


def test_the_public_page_points_to_the_relay(client):
    assert client.get("/api/public").json()["relay"] is None
    connect(client)
    assert client.get("/api/public").json()["relay"] == SERVER
    assert f"ask on their Shōgai Relay: {SERVER}" in client.get("/llms.txt").text

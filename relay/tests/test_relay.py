"""Shōgai Relay: accounts, keys, requests, and who can see what."""

import hashlib
import secrets
import sqlite3

import pytest
from fastapi.testclient import TestClient

import shogai_relay.app as relay_app
from shogai_relay.app import Settings, create_app


def sha(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def bearer(secret, **extra):
    return {"Authorization": f"Bearer {secret}", **extra}


@pytest.fixture
def make(tmp_path):
    def _make(code=None):
        return TestClient(create_app(Settings(data_dir=tmp_path / "data", signup_code=code)))
    return _make


@pytest.fixture
def relay(make):
    with make() as c:
        yield c


def account(c, code=""):
    resp = c.post("/api/owner/signup", json={"code": code})
    assert resp.status_code == 201, resp.text
    return resp.json()["owner_token"]


def new_key(c, token):
    key = "shk_" + secrets.token_urlsafe(32)
    resp = c.post("/api/owner/keys", json={"key_hash": sha(key)}, headers=bearer(token))
    assert resp.status_code == 201, resp.text
    return key, resp.json()["key_id"]


def ask(c, key, question="What tea do you like?", name="Linh"):
    return c.post("/api/requests", json={"question": question, "name": name}, headers=bearer(key))


def test_signup_needs_the_code_when_one_is_set(make):
    with make(code="open-sesame") as c:
        assert c.get("/health").json()["signup_code_required"] is True
        assert c.post("/api/owner/signup").status_code == 403
        assert c.post("/api/owner/signup", json={"code": "wrong"}).status_code == 403
        assert c.post("/api/owner/signup", json={"code": " open-sesame "}).status_code == 201
    with make() as c:
        assert c.get("/health").json()["signup_code_required"] is False
        assert c.post("/api/owner/signup").status_code == 201


def test_owner_endpoints_need_the_account_token(relay):
    assert relay.get("/api/owner").status_code == 401
    assert relay.get("/api/owner", headers=bearer("sho_nope")).status_code == 401
    assert relay.get("/api/owner/requests", headers=bearer("sho_nope")).status_code == 401
    token = account(relay)
    me = relay.get("/api/owner", headers=bearer(token)).json()
    assert me["keys"] == [] and me["pending"] == 0


def test_ask_deliver_and_answer(relay):
    token = account(relay)
    assert relay.put("/api/owner/profile", json={"name": "Alex Demo", "headline": "Builds things"},
                     headers=bearer(token)).status_code == 200
    key, key_id = new_key(relay, token)

    assert relay.get("/api/key", headers=bearer(key)).json() == {
        "key_id": key_id, "to": {"name": "Alex Demo", "headline": "Builds things"}}
    assert relay.get("/api/key", headers={"X-Shogai-Key": key}).status_code == 200

    sent = ask(relay, key, question="  What tea\r\ndo you like?\x00 ", name="Linh\nTran").json()
    assert sent["status"] == "pending" and sent["token"] and sent["delivered"] is None
    assert sent["question"] == "What tea\ndo you like?" and sent["name"] == "Linh Tran"

    inbox = relay.get("/api/owner/requests", params={"after": 0}, headers=bearer(token)).json()
    [item] = inbox["requests"]
    assert item["id"] == sent["id"] and item["key_id"] == key_id and item["question"] == "What tea\ndo you like?"
    assert "token_hash" not in item and inbox["cursor"] == item["seq"]
    assert relay.get("/api/owner/requests", params={"after": inbox["cursor"]},
                     headers=bearer(token)).json() == {"requests": [], "cursor": inbox["cursor"]}

    status_url = f"/api/requests/{sent['id']}"
    seen = relay.get(status_url, headers=bearer(key, **{"X-Request-Token": sent["token"]})).json()
    assert seen["status"] == "pending" and seen["delivered"]  # the owner's app has it

    assert relay.post(f"/api/owner/requests/{sent['id']}/answer", json={"status": "replied", "reply": "Genmaicha."},
                      headers=bearer(token)).status_code == 200
    seen = relay.get(status_url, params={"token": sent["token"]}, headers=bearer(key)).json()
    assert seen["status"] == "replied" and seen["reply"] == "Genmaicha." and seen["answered"]

    other = ask(relay, key, question="Where do you live?").json()
    relay.post(f"/api/owner/requests/{other['id']}/answer", json={"status": "declined"}, headers=bearer(token))
    seen = relay.get(f"/api/requests/{other['id']}", headers=bearer(key, **{"X-Request-Token": other["token"]}))
    assert seen.json()["status"] == "declined" and seen.json()["reply"] == ""
    assert relay.post(f"/api/owner/requests/{other['id']}/answer", json={"status": "maybe"},
                      headers=bearer(token)).status_code == 422


def test_only_whoever_sent_a_request_sees_its_reply(relay):
    token = account(relay)
    key_a, _ = new_key(relay, token)
    key_b, _ = new_key(relay, token)
    stranger_key, _ = new_key(relay, account(relay))
    sent = ask(relay, key_a).json()
    url = f"/api/requests/{sent['id']}"
    right = {"X-Request-Token": sent["token"]}

    assert relay.get(url, headers=bearer(key_a, **right)).status_code == 200
    assert relay.get(url, headers=bearer(key_a)).status_code == 404  # the key alone isn't enough
    assert relay.get(url, headers=bearer(key_a, **{"X-Request-Token": "guess"})).status_code == 404
    assert relay.get(url, headers=bearer(key_b, **right)).status_code == 404  # another key of the same person
    assert relay.get(url, headers=bearer(stranger_key, **right)).status_code == 404
    assert relay.get(url, headers={"X-Request-Token": sent["token"]}).status_code == 401  # no key at all


def test_apps_only_reach_their_own_requests(relay):
    alex, other = account(relay), account(relay)
    key, _ = new_key(relay, alex)
    sent = ask(relay, key).json()
    assert relay.get("/api/owner/requests", headers=bearer(other)).json()["requests"] == []
    assert relay.post(f"/api/owner/requests/{sent['id']}/answer", json={"status": "replied", "reply": "Hacked"},
                      headers=bearer(other)).status_code == 404
    assert relay.delete(f"/api/owner/requests/{sent['id']}", headers=bearer(other)).status_code == 404
    assert relay.delete(f"/api/owner/keys/{relay.get('/api/key', headers=bearer(key)).json()['key_id']}",
                        headers=bearer(other)).status_code == 404
    seen = relay.get(f"/api/requests/{sent['id']}", headers=bearer(key, **{"X-Request-Token": sent["token"]}))
    assert seen.json()["reply"] == ""


def test_revoking_a_key_deletes_its_requests(relay):
    token = account(relay)
    key, key_id = new_key(relay, token)
    kept_key, _ = new_key(relay, token)
    sent = ask(relay, key).json()
    kept = ask(relay, kept_key).json()
    assert relay.delete(f"/api/owner/keys/{key_id}", headers=bearer(token)).status_code == 200
    assert relay.get("/api/key", headers=bearer(key)).status_code == 401
    assert ask(relay, key).status_code == 401
    ids = [r["id"] for r in relay.get("/api/owner/requests", headers=bearer(token)).json()["requests"]]
    assert ids == [kept["id"]] and sent["id"] not in ids
    assert relay.delete(f"/api/owner/keys/{key_id}", headers=bearer(token)).status_code == 404

    assert relay.delete(f"/api/owner/requests/{kept['id']}", headers=bearer(token)).status_code == 200
    assert relay.get(f"/api/requests/{kept['id']}",
                     headers=bearer(kept_key, **{"X-Request-Token": kept["token"]})).status_code == 404


def test_deleting_an_account_removes_everything(relay):
    token = account(relay)
    key, _ = new_key(relay, token)
    ask(relay, key)
    assert relay.delete("/api/owner", headers=bearer(token)).status_code == 200
    assert relay.get("/api/owner", headers=bearer(token)).status_code == 401
    assert relay.get("/api/key", headers=bearer(key)).status_code == 401
    db = sqlite3.connect(relay.app.state.store.path)
    assert [db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("owners", "keys", "requests")] == [0, 0, 0]


def test_keys_are_registered_by_hash_only(relay):
    token = account(relay)
    assert relay.post("/api/owner/keys", json={"key_hash": "shk_plaintext"}, headers=bearer(token)).status_code == 422
    key, _ = new_key(relay, token)
    assert relay.post("/api/owner/keys", json={"key_hash": sha(key)}, headers=bearer(token)).status_code == 409
    sent = ask(relay, key).json()
    dump = "\n".join(sqlite3.connect(relay.app.state.store.path).iterdump())
    for secret in (key, token, sent["token"]):
        assert secret not in dump  # only hashes are stored
    assert relay.app.state.store.path.stat().st_mode & 0o777 == 0o600


def test_existing_databases_are_made_private(tmp_path):
    db = tmp_path / "data" / "relay.db"
    db.parent.mkdir()
    db.touch(mode=0o644)
    db.chmod(0o644)
    create_app(Settings(data_dir=tmp_path / "data", signup_code=None))
    assert db.stat().st_mode & 0o777 == 0o600


def test_limits(relay, monkeypatch):
    token = account(relay)
    key, _ = new_key(relay, token)
    assert ask(relay, key, question="x" * 4001).status_code == 422
    assert ask(relay, key, question=" \x00 ").status_code == 400
    assert [ask(relay, key).status_code for _ in range(21)][-2:] == [201, 429]  # 20 an hour per key

    monkeypatch.setattr(relay_app, "MAX_PENDING", 1)
    other_key, _ = new_key(relay, account(relay))
    assert ask(relay, other_key).status_code == 201
    assert ask(relay, other_key).status_code == 503  # too many unanswered

    codes = [relay.get("/api/key", headers=bearer(f"shk_wrong{i}")).status_code for i in range(31)]
    assert codes[:30] == [401] * 30 and codes[30] == 429
    assert relay.get("/api/key", headers=bearer(key)).status_code == 429  # this address is paused for a while


def test_page_and_security_headers(relay):
    page = relay.get("/")
    assert page.status_code == 200 and 'id="key-input"' in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["x-frame-options"] == "DENY"
    assert relay.get("/static/relay.js").status_code == 200
    assert "POST /api/requests" in relay.get("/llms.txt").text
    assert "content-security-policy" not in relay.get("/docs").headers  # Swagger loads from a CDN
    assert relay.get("/api/key").headers["cache-control"] == "no-store"
    assert relay.options("/api/requests", headers={
        "Origin": "https://agent.example", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type"}).status_code == 200

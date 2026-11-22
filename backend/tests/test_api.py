"""API + pipeline tests with Gemini replaced by fakes (no network)."""

import json
import time

import pytest
from fastapi.testclient import TestClient
from google.genai import types

from shogai.analyzers import AnalysisResult, render_source_markdown
from shogai.ingest import IngestError, detect_kind, link_kind
from shogai.main import create_app
from shogai.schemas import ImageAnalysis, Observation, ProfileUpdate, SourceAnalysis

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


async def fake_analyzer(gem, settings, ctx, item, status):
    await status("Faking it…")
    if item.label == "broken.png":
        raise RuntimeError("boom")
    obs = [Observation(category="preferences", statement=f"Likes tea ({item.kind})", evidence="test", certainty="likely")]
    if item.kind == "image":
        analysis = ImageAnalysis(title=f"Photo {item.label}", summary="A photo.", about_subject=True,
                                 observations=obs, image_type="photo_of_person", is_portrait=True)
    else:
        analysis = SourceAnalysis(title=f"{item.kind} source", summary="Some notes.", about_subject=True,
                                  observations=obs)
    return AnalysisResult(item=item, analysis=analysis, markdown=render_source_markdown(item, analysis, ctx.name),
                          model="fake", is_portrait=item.kind == "image")


SYNTH_CALLS = []


async def fake_synth(gem, settings, person, docs):
    SYNTH_CALLS.append([r.id for r, _ in docs])
    ids = ", ".join(r.id for r, _ in docs)
    return ProfileUpdate(summary="A friend who loves tea.", relationship="friend", aliases=["Annie", person.name],
                         tags=["Tea"], profile_markdown=f"# {person.name}\n\n- Loves tea [{ids}]",
                         changes=["Added tea preference"])


class FakeGem:
    configured = True

    def __init__(self):
        self.calls = []

    async def stream(self, *, model, contents, config, thinking=None):
        self.calls.append(contents)
        round_no = len(self.calls)

        async def gen():
            if round_no == 1:
                part = types.Part(function_call=types.FunctionCall(id="c1", name="get_person_profile", args={"person": "Ana"}))
                yield types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[part]))])
            else:
                for text in ["Ana loves ", "tea [s1]."]:
                    yield types.GenerateContentResponse(candidates=[types.Candidate(
                        content=types.Content(role="model", parts=[types.Part(text=text)]))])
        return gen()


@pytest.fixture
def client(settings):
    app = create_app(settings=settings, gemini=FakeGem(), analyzers={
        "image": fake_analyzer, "text": fake_analyzer, "video": fake_analyzer,
        "audio": fake_analyzer, "academic": fake_analyzer,
    }, synthesizer=fake_synth)
    with TestClient(app) as c:
        yield c


def wait_job(client, job_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def parse_sse(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_detect_kind_and_overrides():
    assert detect_kind("a.JPG").kind == "image"
    assert detect_kind("clip.mov").mime_type == "video/quicktime"
    assert detect_kind("memo.m4a").kind == "audio"
    assert detect_kind("paper.pdf").kind == "academic"
    assert detect_kind("resume.pdf", requested="text").kind == "text"
    assert detect_kind("chat.txt").is_text
    with pytest.raises(IngestError):
        detect_kind("photo.png", requested="video")
    with pytest.raises(IngestError):
        detect_kind("archive.zip")
    assert link_kind("https://youtu.be/abc") == "video"
    assert link_kind("https://arxiv.org/abs/1234.5678") == "academic"
    with pytest.raises(IngestError):
        link_kind("ftp://example.com/x")
    with pytest.raises(IngestError):
        link_kind("https://vimeo.com/1", "video")


def test_people_crud(client):
    assert client.get("/api/health").json()["gemini_configured"] is True
    created = client.post("/api/people", json={"name": "Ana Lima", "relationship": "neighbor"}).json()
    assert created["slug"] == "ana-lima"
    assert [p["slug"] for p in client.get("/api/people").json()] == ["ana-lima"]
    raw = created["raw"].replace("# Ana Lima", "# Ana Lima\n\nHand-written line.")
    edited = client.put("/api/people/ana-lima", json={"markdown": raw}).json()
    assert "Hand-written line." in edited["body"]
    assert client.get("/api/people/nobody").status_code == 404
    assert client.delete("/api/people/ana-lima").status_code == 200
    assert client.get("/api/people").json() == []


def test_ingest_end_to_end_with_partial_failure(client):
    SYNTH_CALLS.clear()
    client.post("/api/people", json={"name": "Ana"})
    resp = client.post(
        "/api/ingest",
        data={
            "person": "ana",
            "thoughts": "She brought me matcha when I was sick.",
            "manifest": json.dumps([{"note": "at the cafe"}, {}, {"kind": "text"}]),
            "links": json.dumps([{"url": "https://www.youtube.com/watch?v=abc"}]),
        },
        files=[
            ("files", ("cafe.png", PNG_1PX, "image/png")),
            ("files", ("broken.png", PNG_1PX, "image/png")),
            ("files", ("chat.txt", "hello there".encode(), "text/plain")),
        ],
    )
    assert resp.status_code == 202, resp.text
    job = wait_job(client, resp.json()["id"])
    assert job["status"] == "done", job
    result = job["result"]
    assert [s["id"] for s in result["sources"]] == ["s1", "s2", "s3", "s4"]
    assert [f["id"] for f in result["failed"]] == ["i3"]  # broken.png
    assert SYNTH_CALLS == [["s1", "s2", "s3", "s4"]]

    person = client.get("/api/people/ana").json()
    assert person["summary"] == "A friend who loves tea."
    assert person["relationship"] == "friend"
    assert person["aliases"] == ["Annie"]  # own name filtered out
    assert person["tags"] == ["tea"]
    assert person["avatar"].startswith("media/") and person["avatar"].endswith("cafe.png")
    assert person["source_count"] == 4
    assert "Loves tea [s1, s2, s3, s4]" in person["body"]

    sources = client.get("/api/people/ana/sources").json()
    assert [s["kind"] for s in sources] == ["text", "image", "text", "video"]
    assert sources[1]["note"] == "at the cafe"
    detail = client.get("/api/people/ana/sources/s2").json()
    assert "![cafe.png](../media/" in detail["markdown"]
    media = client.get("/api/people/ana/media").json()
    assert len(media) == 3  # cafe.png, broken.png, chat.txt kept as originals
    img = client.get(f"/api/people/ana/media/{person['avatar'].split('/', 1)[1]}")
    assert img.status_code == 200 and img.content == PNG_1PX

    events = parse_sse(client.get(f"/api/jobs/{job['id']}/events").text)
    assert events[0]["type"] == "plan" and len(events[0]["items"]) == 5
    assert events[-1]["type"] == "done"
    assert {e["status"] for e in events if e["type"] == "item"} >= {"running", "done", "error"}
    # Fully replayed job with a Last-Event-ID past the end -> 204 so EventSource stops.
    last = len(events) - 1
    assert client.get(f"/api/jobs/{job['id']}/events", headers={"Last-Event-ID": str(last)}).status_code == 204

    rebuilt = wait_job(client, client.post("/api/people/ana/rebuild").json()["id"])
    assert rebuilt["status"] == "done" and SYNTH_CALLS[-1] == ["s1", "s2", "s3", "s4"]

    assert client.delete("/api/people/ana/sources/s4").status_code == 200
    assert client.get("/api/people/ana").json()["source_count"] == 3


def test_ingest_validation(client):
    client.post("/api/people", json={"name": "Ana"})
    assert client.post("/api/ingest", data={"person": "ana"}).status_code == 400
    assert client.post("/api/ingest", data={"person": "nobody", "thoughts": "x"}).status_code == 404
    bad = client.post("/api/ingest", data={"person": "ana"}, files=[("files", ("a.zip", b"PK", "application/zip"))])
    assert bad.status_code == 400 and "Unsupported" in bad.json()["detail"]


def test_chat_streams_tool_calls_and_text(client):
    client.post("/api/people", json={"name": "Ana"})
    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "What does Ana like?"}], "focus": []})
    events = parse_sse(resp.text)
    assert events[0] == {"type": "tool", "name": "get_person_profile", "label": "Reading Ana's profile"}
    assert "".join(e["delta"] for e in events if e["type"] == "text") == "Ana loves tea [s1]."
    assert events[-1]["type"] == "done"
    bad = parse_sse(client.post("/api/chat", json={"messages": [{"role": "assistant", "content": "hi"}]}).text)
    assert bad[0]["type"] == "error"

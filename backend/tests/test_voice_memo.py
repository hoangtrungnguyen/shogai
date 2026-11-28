"""Voice memos: recorded in the browser (WebM) or uploaded, and read as the user's own words."""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shogai.analyzers import AnalysisResult, Item, SubjectContext, render_source_markdown
from shogai.analyzers.video import analyze_media
from shogai.ingest import IngestError, detect_kind
from shogai.main import create_app
from shogai.schemas import MediaAnalysis, ProfileUpdate, SourceAnalysis

# The first bytes of a WebM file; the fakes below never decode it.
WEBM = bytes.fromhex("1a45dfa3") + b"\x00" * 32


def test_webm_is_video_unless_it_is_a_recording():
    assert detect_kind("clip.webm").kind == "video"
    assert detect_kind("clip.webm", "video/webm").mime_type == "video/webm"
    # The voice-memo recorder sends audio/webm; a .webm picked as a recording asks for "audio".
    recorded = detect_kind("Voice memo 2026-11-28 14.05.33.webm", "audio/webm;codecs=opus")
    assert (recorded.kind, recorded.mime_type) == ("audio", "audio/webm")
    picked = detect_kind("note.webm", "video/webm", requested="audio")
    assert (picked.kind, picked.mime_type) == ("audio", "audio/webm")
    assert detect_kind("note.webm", "audio/webm", requested="video").kind == "video"
    assert detect_kind("memo.weba").mime_type == "audio/webm"
    # Only WebM can hold sound only here; other videos stay video.
    with pytest.raises(IngestError):
        detect_kind("clip.mov", requested="audio")


def wait_job(client, job_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_recorded_memo_reaches_the_audio_analyzer_as_recorded(settings):
    seen: list[Item] = []

    async def analyzer(gem, settings, ctx, item, status):
        seen.append(item)
        analysis = SourceAnalysis(title=item.label, summary="s", about_subject=True, observations=[])
        return AnalysisResult(item=item, analysis=analysis, model="fake",
                              markdown=render_source_markdown(item, analysis, ctx.name))

    async def synth(gem, settings, person, docs):
        return ProfileUpdate(summary="", relationship="", aliases=[], tags=[],
                             profile_markdown=f"# {person.name}\n", changes=[])

    class Gem:
        configured = True

    app = create_app(settings=settings, gemini=Gem(), synthesizer=synth,
                     analyzers={k: analyzer for k in ("image", "text", "video", "audio", "academic")})
    with TestClient(app) as client:
        client.post("/api/people", json={"name": "Ana"})
        resp = client.post(
            "/api/ingest",
            data={
                "person": "ana",
                "manifest": json.dumps([
                    {"kind": "audio", "note": "after dinner", "recorded": True},
                    {"kind": "audio"},  # a recording picked from disk
                    {"kind": "video", "recorded": True},  # only audio can be a recorded memo
                ]),
            },
            files=[
                ("files", ("Voice memo 2026-11-28 14.05.33.webm", WEBM, "audio/webm")),
                ("files", ("voice-message.webm", WEBM, "video/webm")),
                ("files", ("party.webm", WEBM, "video/webm")),
            ],
        )
        assert resp.status_code == 202, resp.text
        job = wait_job(client, resp.json()["id"])
        assert job["status"] == "done", job

        by_label = {item.label: item for item in seen}
        memo = by_label["Voice memo 2026-11-28 14.05.33.webm"]
        assert (memo.kind, memo.mime_type, memo.recorded, memo.note) == ("audio", "audio/webm", True, "after dinner")
        assert memo.path is not None and memo.path.read_bytes() == WEBM  # stored as sent
        picked = by_label["voice-message.webm"]
        assert (picked.kind, picked.mime_type, picked.recorded) == ("audio", "audio/webm", False)
        party = by_label["party.webm"]
        assert (party.kind, party.mime_type, party.recorded) == ("video", "video/webm", False)

        sources = client.get("/api/people/ana/sources").json()
        assert [s["kind"] for s in sources] == ["audio", "audio", "video"]
        assert sources[0]["media"].endswith("voice-memo-2026-11-28-14-05-33.webm")


class PromptGem:
    """Records what analyze_media asks Gemini."""

    def __init__(self):
        self.prompts: list[str] = []

    async def file_part(self, path, mime_type, status=None, force_upload=False):
        return f"<{mime_type}>", None

    async def generate_structured(self, *, model, contents, schema, system, thinking=None):
        self.prompts.append(contents[1])
        return MediaAnalysis(title="Memo", summary="s", about_subject=True, observations=[]), None

    async def delete_file(self, name):
        pass


@pytest.mark.parametrize("recorded", [True, False])
async def test_a_recorded_memo_tells_gemini_the_voice_is_the_users(settings, tmp_path: Path, recorded):
    path = tmp_path / "memo.webm"
    path.write_bytes(WEBM)
    item = Item(id="i1", kind="audio", label="memo.webm", path=path, mime_type="audio/webm", recorded=recorded)
    ctx = SubjectContext(slug="ana", name="Ana", relationship="friend", aliases=[], known="")
    gem = PromptGem()

    async def status(message):
        pass

    result = await analyze_media(gem, settings, ctx, item, status)

    assert result.analysis.title == "Memo"
    prompt = gem.prompts[0]
    if recorded:
        assert "SOURCE TYPE: voice memo — memo.webm." in prompt
        assert "ORIGIN: the user recorded this voice memo" in prompt
    else:
        assert "SOURCE TYPE: audio recording — memo.webm." in prompt
        assert "ORIGIN:" not in prompt
        assert "USER NOTE: (none)\n\nWatch/listen" in prompt  # unchanged for other recordings

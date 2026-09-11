import asyncio
import json
import sys
import threading
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parents[2] / "backend"))

from meetingbox.inference import InferenceError
from meetingbox.models import Report
from meetingbox_engine.audio import AudioError, AudioProcessor, parse_whisper, speaker_for
from meetingbox_engine.config import EngineSettings
from meetingbox_engine.main import create_app
from meetingbox_engine.reasoner import VerifiedReasoner

TOKEN = "engine-test-token-at-least-16"
AUTH = {"Authorization": "Bearer " + TOKEN}
EMPTY = {"summary": "", "decisions": [], "action_items": [], "open_questions": [], "topics": [], "risks": []}


class FakeReasoner:
    async def close(self):
        pass

    async def reconcile(self, meeting, sequence, final):
        return Report.model_validate(EMPTY)


class FakeAudio:
    def __init__(self):
        self.sources = []

    def capabilities(self):
        return {"transcription": {"ready": True, "missing": []}, "diarization": {"ready": False}}

    def transcribe(self, source, language):
        self.sources.append(source)
        assert source.read_bytes()
        return {"segments": [], "duration": 1, "diarization": {"enabled": False, "status": "unavailable"}}


def test_auth_before_upload_and_cleanup(tmp_path):
    audio = FakeAudio()
    settings = EngineSettings(token=TOKEN, temp_directory=str(tmp_path), upload_bytes=8)
    with TestClient(create_app(settings, FakeReasoner(), audio)) as client:
        assert client.post("/v1/transcribe", content=b"speech").status_code == 401
        assert list(tmp_path.iterdir()) == []
        assert client.post("/v1/transcribe", headers=AUTH, content=b"too many bytes").status_code == 413
        assert list(tmp_path.iterdir()) == []
        assert client.post("/v1/transcribe", headers=AUTH, content=b"").status_code == 400
        response = client.post("/v1/transcribe", headers=AUTH, content=b"speech")
        assert response.status_code == 200
        assert response.json()["diarization"]["enabled"] is False
        assert all(not path.exists() for path in audio.sources)


def test_health_remains_responsive_during_audio(tmp_path):
    started, release = threading.Event(), threading.Event()

    class SlowAudio(FakeAudio):
        def transcribe(self, source, language):
            started.set()
            assert release.wait(5)
            return super().transcribe(source, language)

    settings = EngineSettings(token=TOKEN, temp_directory=str(tmp_path))
    with TestClient(create_app(settings, FakeReasoner(), SlowAudio())) as client:
        responses = []
        thread = threading.Thread(target=lambda: responses.append(client.post("/v1/transcribe", headers=AUTH, content=b"speech")))
        thread.start()
        assert started.wait(2)
        try:
            health = client.get("/health", headers=AUTH)
            assert health.status_code == 200
            assert health.json()["audio_busy"] is True
        finally:
            release.set()
            thread.join(3)
        assert responses[0].status_code == 200


def test_invalid_contract_rejected():
    with TestClient(create_app(EngineSettings(token=TOKEN), FakeReasoner(), FakeAudio())) as client:
        assert client.post("/v1/transcribe?language=../../en", headers=AUTH, content=b"x").status_code == 422
        assert client.post("/v1/transcribe", headers=dict(AUTH, **{"Content-Type": "multipart/form-data"}), content=b"x").status_code == 415
        assert client.post("/v1/reconcile", headers=AUTH, json={"meeting": {"segments": []}, "through_sequence": 1, "final": True}).status_code == 422
        response = client.post("/v1/reconcile", headers=AUTH, json={"meeting": {"segments": []}, "through_sequence": 0, "final": True})
        assert response.json() == EMPTY


def test_missing_models_explicit():
    with TestClient(create_app(EngineSettings(token=TOKEN), FakeReasoner())) as client:
        health = client.get("/health", headers=AUTH).json()
        assert not health["capabilities"]["transcription"]["ready"]
        assert not health["capabilities"]["diarization"]["ready"]
        assert client.post("/v1/transcribe", headers=AUTH, content=b"x").status_code == 503


def test_model_url_rejects_remote():
    with pytest.raises(ValueError, match="loopback"):
        EngineSettings(token=TOKEN, ollama_url="https://example.com")
    with pytest.raises(ValueError, match="cloud"):
        EngineSettings(token=TOKEN, model="qwen3.5:cloud")


def test_timestamp_and_ambiguous_speakers():
    turns = [{"start": 0, "end": 1, "speaker": "speaker_00"}, {"start": 1, "end": 2, "speaker": "speaker_01"}]
    assert speaker_for(0, 2, turns) == "unknown"
    assert speaker_for(0, 0.9, turns) == "speaker_00"
    payload = {"transcription": [{"text": " Hello ", "offsets": {"from": 0, "to": 900}}]}
    assert parse_whisper(payload, 2, turns)[0] == {"sequence": 1, "start": 0, "end": 0.9, "speaker": "speaker_00", "text": "Hello"}
    payload["transcription"][0]["offsets"]["to"] = float("nan")
    with pytest.raises(AudioError, match="timestamps"):
        parse_whisper(payload, 2, turns)


def test_fake_executables_use_real_subprocess(tmp_path):
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_text("#!" + sys.executable + "\nimport sys,wave\nwith wave.open(sys.argv[-1],'wb') as w:\n w.setparams((1,2,16000,0,'NONE','not compressed'))\n w.writeframes(b'\\0' * 32000)\n")
    whisper = tmp_path / "whisper"
    whisper.write_text("#!" + sys.executable + "\nimport sys,json,pathlib\np=pathlib.Path(sys.argv[sys.argv.index('-of')+1]+'.json')\np.write_text(json.dumps({'transcription':[{'text':'Test speech','offsets':{'from':0,'to':900}}]}))\n")
    ffmpeg.chmod(0o700)
    whisper.chmod(0o700)
    model, source = tmp_path / "model.bin", tmp_path / "audio.mp3"
    model.write_bytes(b"fake-model")
    source.write_bytes(b"fake-audio")
    processor = AudioProcessor(EngineSettings(token=TOKEN, ffmpeg_binary=str(ffmpeg), whisper_binary=str(whisper), whisper_model=str(model)))
    result = processor.transcribe(source, "en")
    assert result["segments"][0]["text"] == "Test speech"
    assert result["duration"] == 1
    assert not result["diarization"]["enabled"]


@pytest.mark.parametrize("supported", [True, False])
def test_semantic_verification(supported):
    def handler(request):
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps({"verdicts": [{"id": 0, "supported": supported}]})}})

    async def run():
        reasoner = VerifiedReasoner(EngineSettings(token=TOKEN).reasoner_settings(), transport=httpx.MockTransport(handler))
        report = Report.model_validate(dict(EMPTY, action_items=[{"task": "Send report", "owner": "Dana", "due": "Monday", "evidence": [1]}]))
        meeting = {"segments": [{"sequence": 1, "start": 0, "end": 1, "speaker": "unknown", "text": "Dana will send the report Monday."}]}
        try:
            if supported:
                await reasoner.verify(report, meeting, 1)
            else:
                with pytest.raises(InferenceError, match="unsupported"):
                    await reasoner.verify(report, meeting, 1)
        finally:
            await reasoner.close()
    asyncio.run(run())


def test_pdf_unicode_and_markup(tmp_path):
    pytest.importorskip("reportlab")
    from meetingbox_engine.pdf import render_pdf
    font = "/System/Library/Fonts/Supplemental/Arial.ttf"
    if not Path(font).exists():
        pytest.skip("Unicode test font unavailable")
    document = render_pdf({"title": "Қазақша Русский <img src='https://invalid/evil'>", "segments": [], "report": EMPTY}, font)
    assert document.startswith(b"%PDF")
    assert len(document) > 1000

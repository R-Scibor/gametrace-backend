"""
tests/api/test_voice.py

Phase 3 — POST /api/v1/voice/transcribe

Patches openai.AsyncOpenAI and app.api.v1.endpoints.voice._gemini_parse for endpoint
tests. For the markdown-fence test, _gemini_parse is called directly with vertexai mocked
to test the stripping logic inside the function itself.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.v1.endpoints.voice import _gemini_parse


def _voice_settings(openai_key: str = "test-key", gcp_project: str = "test-project"):
    s = MagicMock()
    s.openai_api_key = openai_key
    s.gcp_project = gcp_project
    s.gcp_location = "us-central1"
    return s


def _mock_openai(transcript_text: str) -> MagicMock:
    transcription = MagicMock()
    transcription.text = transcript_text
    client = MagicMock()
    client.audio.transcriptions.create = AsyncMock(return_value=transcription)
    return client


# ── Happy path ────────────────────────────────────────────────────────────────

async def test_transcribe_happy_path(authed_client):
    gemini_result = {
        "game": "Hades",
        "date": "2024-01-15",
        "start_time": "20:00",
        "end_time": "21:00",
        "duration_minutes": 60,
    }

    with patch("app.api.v1.endpoints.voice.settings", _voice_settings()), \
         patch("app.api.v1.endpoints.voice.AsyncOpenAI",
               return_value=_mock_openai("Grałem w Hades od 20:00")), \
         patch("app.api.v1.endpoints.voice._gemini_parse", return_value=gemini_result):

        resp = await authed_client.post(
            "/api/v1/voice/transcribe",
            files={"file": ("session.m4a", b"fake_audio_data", "audio/m4a")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["game"] == "Hades"
    assert data["date"] == "2024-01-15"
    assert data["start_time"] == "20:00"
    assert data["end_time"] == "21:00"
    assert data["duration_minutes"] == 60
    assert data["raw_transcript"] == "Grałem w Hades od 20:00"


async def test_partial_fields_preserved(authed_client):
    """Null fields from Gemini pass through unmodified."""
    gemini_result = {
        "game": "Hades",
        "date": None,
        "start_time": None,
        "end_time": None,
        "duration_minutes": None,
    }

    with patch("app.api.v1.endpoints.voice.settings", _voice_settings()), \
         patch("app.api.v1.endpoints.voice.AsyncOpenAI",
               return_value=_mock_openai("Grałem w Hades")), \
         patch("app.api.v1.endpoints.voice._gemini_parse", return_value=gemini_result):

        resp = await authed_client.post(
            "/api/v1/voice/transcribe",
            files={"file": ("session.m4a", b"data", "audio/m4a")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["game"] == "Hades"
    assert data["date"] is None
    assert data["start_time"] is None
    assert data["duration_minutes"] is None


# ── Gemini failure modes ──────────────────────────────────────────────────────

async def test_gemini_returns_non_json(authed_client):
    """When Gemini returns non-JSON, endpoint returns 200 with null fields and raw_transcript."""
    with patch("app.api.v1.endpoints.voice.settings", _voice_settings()), \
         patch("app.api.v1.endpoints.voice.AsyncOpenAI",
               return_value=_mock_openai("some transcript")), \
         patch("app.api.v1.endpoints.voice._gemini_parse",
               side_effect=json.JSONDecodeError("msg", "doc", 0)):

        resp = await authed_client.post(
            "/api/v1/voice/transcribe",
            files={"file": ("session.m4a", b"data", "audio/m4a")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["game"] is None
    assert data["date"] is None
    assert data["raw_transcript"] == "some transcript"


def test_gemini_markdown_fence_stripped():
    """_gemini_parse strips ```json...``` fences before calling json.loads."""
    mock_response = MagicMock()
    mock_response.text = '```json\n{"game": "Hades", "date": null}\n```'

    with patch("vertexai.init"), \
         patch("vertexai.generative_models.GenerativeModel") as MockModel:
        MockModel.return_value.generate_content.return_value = mock_response
        result = _gemini_parse("test transcript")

    assert result["game"] == "Hades"
    assert result["date"] is None


# ── Whisper failure ───────────────────────────────────────────────────────────

async def test_whisper_failure_returns_502(authed_client):
    failing_client = MagicMock()
    failing_client.audio.transcriptions.create = AsyncMock(
        side_effect=Exception("Connection error")
    )

    with patch("app.api.v1.endpoints.voice.settings", _voice_settings()), \
         patch("app.api.v1.endpoints.voice.AsyncOpenAI", return_value=failing_client):

        resp = await authed_client.post(
            "/api/v1/voice/transcribe",
            files={"file": ("session.m4a", b"data", "audio/m4a")},
        )

    assert resp.status_code == 502


# ── Input validation ──────────────────────────────────────────────────────────

async def test_empty_file_returns_400(authed_client):
    with patch("app.api.v1.endpoints.voice.settings", _voice_settings()):
        resp = await authed_client.post(
            "/api/v1/voice/transcribe",
            files={"file": ("session.m4a", b"", "audio/m4a")},
        )

    assert resp.status_code == 400


# ── Missing config ────────────────────────────────────────────────────────────

async def test_missing_openai_key_returns_503(authed_client):
    with patch("app.api.v1.endpoints.voice.settings", _voice_settings(openai_key="")):
        resp = await authed_client.post(
            "/api/v1/voice/transcribe",
            files={"file": ("session.m4a", b"data", "audio/m4a")},
        )

    assert resp.status_code == 503


async def test_missing_gcp_project_returns_503(authed_client):
    with patch("app.api.v1.endpoints.voice.settings", _voice_settings(gcp_project="")):
        resp = await authed_client.post(
            "/api/v1/voice/transcribe",
            files={"file": ("session.m4a", b"data", "audio/m4a")},
        )

    assert resp.status_code == 503

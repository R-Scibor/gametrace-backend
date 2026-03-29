"""
POST /api/v1/voice/transcribe

Pipeline:
1. Accept audio file upload (m4a / wav / mp3 / ogg).
2. Send to OpenAI Whisper API (STT) — chosen for mixed-language quality
   (Polish sentences + English game names).
3. Send transcript to Gemini Flash via Vertex AI for structured JSON extraction:
   {game, date, start_time, end_time, duration_minutes} — unknown fields as null.
4. Return parsed JSON to the frontend for user verification before saving.

Auth: Vertex AI uses Application Default Credentials (ADC).
Set GOOGLE_APPLICATION_CREDENTIALS to a service account JSON if not running on GCP.

User always confirms the result — this endpoint only suggests values.
"""
import asyncio
import json
import logging
import os
import tempfile
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.models.user import User

router = APIRouter()
logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-1.5-flash-002"

GEMINI_PROMPT = """\
You are a structured data extractor. The user dictated a gaming session in Polish or English.
Extract the following fields from the transcription and return ONLY a valid JSON object with these keys:
- "game": string — the name of the game (as the user said it), or null if not mentioned
- "date": string — date in YYYY-MM-DD format, or null if not mentioned
- "start_time": string — start time in HH:MM format (24h), or null if not mentioned
- "end_time": string — end time in HH:MM format (24h), or null if not mentioned
- "duration_minutes": integer — duration in minutes, or null if not mentioned

If a value is ambiguous or absent, use null.
Return ONLY the JSON object, no explanation.

Transcription:
{transcript}
"""


class TranscribeResponse(BaseModel):
    game: Optional[str] = None
    date: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_minutes: Optional[int] = None
    raw_transcript: str


def _gemini_parse(transcript: str) -> dict:
    """
    Call Gemini Flash via Vertex AI (sync — run in thread executor).
    Returns parsed dict; empty dict on JSON decode failure.
    """
    import vertexai
    from vertexai.generative_models import GenerativeModel

    vertexai.init(project=settings.gcp_project, location=settings.gcp_location)
    model = GenerativeModel(GEMINI_MODEL)
    prompt = GEMINI_PROMPT.format(transcript=transcript)
    response = model.generate_content(prompt)
    raw_json = response.text.strip()

    # Strip markdown code fences if Gemini wraps the response
    if raw_json.startswith("```"):
        raw_json = raw_json.split("```")[1]
        if raw_json.startswith("json"):
            raw_json = raw_json[4:]
        raw_json = raw_json.strip()

    return json.loads(raw_json)


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(
    file: UploadFile,
    user: User = Depends(get_current_user),
):
    """
    Upload an audio file (m4a/wav/mp3/ogg).
    Returns extracted session fields for user confirmation.
    """
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail="Voice pipeline not configured (missing OPENAI_API_KEY).",
        )
    if not settings.gcp_project:
        raise HTTPException(
            status_code=503,
            detail="Voice pipeline not configured (missing GCP_PROJECT).",
        )

    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    filename = file.filename or "audio.m4a"
    suffix = "." + filename.rsplit(".", 1)[-1] if "." in filename else ".m4a"

    # ── Step 1: Whisper STT ──────────────────────────────────────────────────
    openai_client = AsyncOpenAI(api_key=settings.openai_api_key)

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as audio_file:
            transcription = await openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language=None,  # auto-detect — handles Polish + English mixed
            )
        transcript: str = transcription.text
    except Exception as exc:
        logger.exception("Whisper transcription failed")
        raise HTTPException(status_code=502, detail=f"Transcription failed: {exc}") from exc
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    logger.info("voice/transcribe: transcript=%r", transcript[:120])

    # ── Step 2: Gemini Flash via Vertex AI — JSON extraction ─────────────────
    # Vertex AI SDK is synchronous; run in thread pool to avoid blocking the loop.
    try:
        parsed = await asyncio.to_thread(_gemini_parse, transcript)
    except json.JSONDecodeError as exc:
        logger.warning("Gemini returned non-JSON: %s", exc)
        parsed = {}
    except Exception as exc:
        logger.exception("Gemini/Vertex AI parsing failed")
        raise HTTPException(status_code=502, detail=f"Parsing failed: {exc}") from exc

    return TranscribeResponse(
        game=parsed.get("game"),
        date=parsed.get("date"),
        start_time=parsed.get("start_time"),
        end_time=parsed.get("end_time"),
        duration_minutes=parsed.get("duration_minutes"),
        raw_transcript=transcript,
    )

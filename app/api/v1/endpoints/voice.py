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
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from openai import AsyncOpenAI
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.services.upload_validation import looks_like_audio
from app.services.voice_context import (
    build_candidate_block,
    build_datetime_block,
    fetch_user_library,
    match_candidates,
)

router = APIRouter()
logger = logging.getLogger(__name__)


GEMINI_PROMPT = """\
You are a structured data extractor. The user dictated a gaming session in Polish or English.
Extract these fields from the transcription:
- game: the name of the game (as the user said it), or null if not mentioned
- date: date in YYYY-MM-DD format, or null if not mentioned
- start_time: start time in HH:MM format (24h), or null if not mentioned
- end_time: end time in HH:MM format (24h), or null if not mentioned
- duration_minutes: duration in minutes, or null if not mentioned

If a value is ambiguous or absent, use null.
When duration is given without explicit times, leave start_time and end_time null
unless the transcript anchors them ("I just finished" + 30 min → end_time = now, start_time = now - 30 min).

{datetime_block}

Transcript language (auto-detected by Whisper): {language}

{candidate_block}

Transcription:
{transcript}
"""


GEMINI_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "game": {"type": "string", "nullable": True},
        "date": {"type": "string", "nullable": True},
        "start_time": {"type": "string", "nullable": True},
        "end_time": {"type": "string", "nullable": True},
        "duration_minutes": {"type": "integer", "nullable": True},
    },
    "required": ["game", "date", "start_time", "end_time", "duration_minutes"],
}


class TranscribeResponse(BaseModel):
    game: Optional[str] = None
    date: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_minutes: Optional[int] = None
    raw_transcript: str


def _gemini_parse(
    transcript: str,
    datetime_block: str,
    language: str | None,
    candidate_block: str,
) -> dict:
    """
    Call Gemini Flash via Vertex AI with structured output.
    Sync — run in thread executor.
    """
    import vertexai
    from vertexai.generative_models import GenerationConfig, GenerativeModel

    vertexai.init(project=settings.gcp_project, location=settings.gcp_location)
    model = GenerativeModel(settings.gemini_model)
    prompt = GEMINI_PROMPT.format(
        transcript=transcript,
        datetime_block=datetime_block,
        language=language or "unknown",
        candidate_block=candidate_block,
    )
    response = model.generate_content(
        prompt,
        generation_config=GenerationConfig(
            response_mime_type="application/json",
            response_schema=GEMINI_RESPONSE_SCHEMA,
        ),
    )
    raw_json = response.text
    logger.warning("voice/transcribe: gemini raw response=%r", raw_json)
    return json.loads(raw_json)


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(
    file: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
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

    # Reject obvious non-audio before paying for the Whisper request.
    if not looks_like_audio(audio_bytes):
        raise HTTPException(
            status_code=422, detail="Uploaded file is not a recognized audio format."
        )

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
                response_format="verbose_json",
            )
        transcript: str = transcription.text
        detected_language: Optional[str] = getattr(transcription, "language", None)
    except Exception as exc:
        logger.exception("Whisper transcription failed")
        # Upstream exception text can carry internals (project ids, paths) — keep
        # the client-facing detail generic; the log line has the real error.
        raise HTTPException(status_code=502, detail="Transcription failed.") from exc
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    logger.warning(
        "voice/transcribe: whisper transcript=%r language=%r",
        transcript,
        detected_language,
    )

    # ── Step 2: Gemini Flash via Vertex AI — JSON extraction ─────────────────
    # Vertex AI SDK is synchronous; run in thread pool to avoid blocking the loop.
    datetime_block = build_datetime_block(user.timezone)
    library = await fetch_user_library(db, user.discord_id)
    matches = match_candidates(transcript, library)
    candidate_block = build_candidate_block(matches)
    logger.warning("voice/transcribe: library_size=%d matches=%r", len(library), matches)

    try:
        parsed = await asyncio.to_thread(
            _gemini_parse, transcript, datetime_block, detected_language, candidate_block
        )
        logger.warning("voice/transcribe: parsed=%r", parsed)
    except Exception as exc:
        logger.exception("Gemini/Vertex AI parsing failed")
        raise HTTPException(status_code=502, detail="Parsing failed.") from exc

    return TranscribeResponse(
        game=parsed.get("game"),
        date=parsed.get("date"),
        start_time=parsed.get("start_time"),
        end_time=parsed.get("end_time"),
        duration_minutes=parsed.get("duration_minutes"),
        raw_transcript=transcript,
    )

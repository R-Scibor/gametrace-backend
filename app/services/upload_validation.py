"""Lightweight magic-byte checks for uploaded files.

Signature sniffing without a libmagic dependency — enough to reject a file whose
bytes don't match the expected kind before we store it or pay for an external
API call. Not a full content validator: it inspects only the leading bytes.
"""


def sniff_image_extension(data: bytes) -> str | None:
    """Canonical extension if `data` starts with a supported image signature."""
    if data[:3] == b"\xff\xd8\xff":  # JPEG
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":  # PNG
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":  # WebP
        return "webp"
    return None


def looks_like_audio(data: bytes) -> bool:
    """True if `data` starts with a recognized audio-container signature.

    Covers the formats the voice endpoint accepts (m4a/wav/mp3/ogg/webm)."""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":  # WAV
        return True
    if data[:3] == b"ID3":  # MP3 with an ID3v2 tag
        return True
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:  # MP3 frame sync
        return True
    if data[4:8] == b"ftyp":  # MP4 / M4A container
        return True
    if data[:4] == b"OggS":  # Ogg
        return True
    if data[:4] == b"\x1a\x45\xdf\xa3":  # EBML (WebM / Matroska) — Chrome MediaRecorder
        return True
    return False

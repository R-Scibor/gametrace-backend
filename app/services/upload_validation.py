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


# ISO-BMFF image brands. A major or compatible brand of one of these is a photo,
# not an m4a, even though the box type is still `ftyp`.
_IMAGE_FTYP_BRANDS = {b"heic", b"avif", b"mif1"}


# A real ftyp box is a short header. Size 0 means "extends to EOF" in ISO-BMFF,
# and a hostile size can be larger than the upload. Either one used to walk the
# whole buffer on the request thread.
_FTYP_SCAN_CAP = 256


def _ftyp_brands(data: bytes) -> list[bytes]:
    """Major brand, then each compatible brand, lowercased.

    Compatible brands start after the 4-byte minor version. The walk stops at
    the declared box size when that size fits in the cap, and never reads past
    `_FTYP_SCAN_CAP` bytes."""
    if len(data) < 12 or data[4:8] != b"ftyp":
        return []
    brands = [data[8:12].lower()]
    size = int.from_bytes(data[:4], "big")
    end = min(len(data), _FTYP_SCAN_CAP)
    if 16 <= size <= end:
        end = size
    offset = 16
    while offset + 4 <= end:
        brands.append(data[offset : offset + 4].lower())
        offset += 4
    return brands


def looks_like_audio(data: bytes) -> str | None:
    """Allowlisted extension if `data` starts with a supported audio signature.

    Whisper is told this name (`audio.wav`, `audio.webm`, …). `None` is not
    audio: plain junk, or an `ftyp` whose brand is an image (`heic`, `avif`,
    `mif1`)."""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":  # WAV
        return "wav"
    if data[:3] == b"ID3":  # MP3 with an ID3v2 tag
        return "mp3"
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:  # MP3 frame sync
        return "mp3"
    if data[4:8] == b"ftyp":  # MP4 / M4A, unless the brand is an image
        if any(brand in _IMAGE_FTYP_BRANDS for brand in _ftyp_brands(data)):
            return None
        return "m4a"
    if data[:4] == b"OggS":  # Ogg
        return "ogg"
    if data[:4] == b"\x1a\x45\xdf\xa3":  # EBML (WebM / Matroska) — Chrome MediaRecorder
        return "webm"
    return None

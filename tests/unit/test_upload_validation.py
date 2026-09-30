"""Magic-byte upload checks — image and audio signature sniffing."""
from app.services.upload_validation import looks_like_audio, sniff_image_extension


def test_sniff_image_recognizes_supported_signatures():
    assert sniff_image_extension(b"\xff\xd8\xff\xe0rest") == "jpg"
    assert sniff_image_extension(b"\x89PNG\r\n\x1a\nrest") == "png"
    assert sniff_image_extension(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"


def test_sniff_image_rejects_non_image():
    assert sniff_image_extension(b"not an image at all") is None
    assert sniff_image_extension(b"") is None
    # A WAV also starts with RIFF but must not read as WebP.
    assert sniff_image_extension(b"RIFF\x00\x00\x00\x00WAVEfmt ") is None


def _ftyp(major: bytes, compatible: tuple[bytes, ...] = ()) -> bytes:
    payload = major + b"\x00\x00\x00\x00" + b"".join(compatible)
    return (8 + len(payload)).to_bytes(4, "big") + b"ftyp" + payload


def test_looks_like_audio_returns_the_allowlisted_extension():
    assert looks_like_audio(b"RIFF\x00\x00\x00\x00WAVEfmt ") == "wav"
    assert looks_like_audio(b"ID3\x03\x00rest") == "mp3"
    assert looks_like_audio(b"\xff\xfbrest") == "mp3"
    assert looks_like_audio(b"\x00\x00\x00\x18ftypM4A ") == "m4a"
    assert looks_like_audio(_ftyp(b"mp42")) == "m4a"
    assert looks_like_audio(b"OggS\x00\x02rest") == "ogg"
    assert looks_like_audio(b"\x1a\x45\xdf\xa3rest") == "webm"


def test_looks_like_audio_rejects_non_audio():
    assert looks_like_audio(b"this is not audio at all") is None
    assert looks_like_audio(b"") is None
    assert looks_like_audio(b"\x89PNG\r\n\x1a\n") is None


def test_looks_like_audio_rejects_image_ftyp_brands():
    assert looks_like_audio(_ftyp(b"heic")) is None
    assert looks_like_audio(_ftyp(b"avif")) is None
    assert looks_like_audio(_ftyp(b"mif1")) is None
    assert looks_like_audio(_ftyp(b"HEIC")) is None
    # A photo can hide the image brand in the compatible list.
    assert looks_like_audio(_ftyp(b"isom", (b"heic",))) is None


def _ftyp_with_size(size: int, body: bytes) -> bytes:
    return size.to_bytes(4, "big") + b"ftyp" + body


def test_size_zero_ftyp_ignores_an_image_brand_past_the_cap():
    # Size 0 means the box runs to EOF. A heic brand past the cap is payload,
    # not a compatible brand, and must not reject the audio.
    body = b"isom" + b"\x00\x00\x00\x00" + (b"\x00" * 400)
    data = bytearray(_ftyp_with_size(0, body))
    data[300:304] = b"heic"
    assert looks_like_audio(bytes(data)) == "m4a"


def test_oversized_ftyp_ignores_an_image_brand_past_the_cap():
    body = b"mp42" + b"\x00\x00\x00\x00" + (b"\x00" * 400)
    data = bytearray(_ftyp_with_size(10_000_000, body))
    data[300:304] = b"heic"
    assert looks_like_audio(bytes(data)) == "m4a"


def test_size_zero_ftyp_rejects_an_image_brand_inside_the_cap():
    body = b"isom" + b"\x00\x00\x00\x00" + b"heic" + (b"\x00" * 400)
    assert looks_like_audio(_ftyp_with_size(0, body)) is None

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


def test_looks_like_audio_recognizes_supported_signatures():
    assert looks_like_audio(b"RIFF\x00\x00\x00\x00WAVEfmt ")  # WAV
    assert looks_like_audio(b"ID3\x03\x00rest")  # MP3 w/ ID3
    assert looks_like_audio(b"\xff\xfbrest")  # MP3 frame sync
    assert looks_like_audio(b"\x00\x00\x00\x18ftypM4A ")  # M4A
    assert looks_like_audio(b"OggS\x00\x02rest")  # Ogg
    assert looks_like_audio(b"\x1a\x45\xdf\xa3rest")  # WebM / Matroska (EBML)


def test_looks_like_audio_rejects_non_audio():
    assert not looks_like_audio(b"this is not audio at all")
    assert not looks_like_audio(b"")
    assert not looks_like_audio(b"\x89PNG\r\n\x1a\n")  # an image is not audio

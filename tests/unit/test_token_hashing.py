"""UserAuthToken.hash_token — auth tokens are stored as SHA-256 at rest."""
import hashlib

from app.models.user import UserAuthToken


def test_hash_token_is_sha256_hex():
    raw = "abc123"
    assert UserAuthToken.hash_token(raw) == hashlib.sha256(raw.encode()).hexdigest()


def test_hash_token_deterministic_not_identity_and_fits_column():
    raw = UserAuthToken.generate_token()
    assert UserAuthToken.hash_token(raw) == UserAuthToken.hash_token(raw)
    assert UserAuthToken.hash_token(raw) != raw
    assert len(UserAuthToken.hash_token(raw)) == 64  # fits String(64)

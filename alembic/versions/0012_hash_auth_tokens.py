"""Hash auth tokens at rest

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-06

Auth tokens are now stored as SHA-256 hashes (see UserAuthToken.hash_token).
Existing rows hold raw tokens that can no longer be matched against the hashed
lookup, so they are purged: every active session is invalidated and users
re-authenticate. The `token` column stays String(64) — a SHA-256 hex digest is
also 64 chars — so no schema change is needed.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Invalidate all existing sessions; raw tokens cannot be re-hashed in place.
    op.execute("DELETE FROM user_auth_tokens")


def downgrade() -> None:
    # Hashing is one-way; there is nothing to restore. Purge again so no
    # hash-format tokens linger if the app is rolled back to raw comparison.
    op.execute("DELETE FROM user_auth_tokens")

"""
Unit tests for the demo seed snapshot models (DemoSeedSession, DemoSeedPreference).

Contracts:
  1. Both models are importable from app.models and map to the documented
     table names.
  2. Both models expose the documented columns.
  3. Neither model has a `user_id` or `is_flicker` column — the demo snapshot
     is intentionally user-agnostic and does not track flicker state.
"""


def test_demo_seed_session_table_name():
    from app.models import DemoSeedSession

    assert DemoSeedSession.__tablename__ == "demo_seed_sessions"


def test_demo_seed_session_columns():
    from app.models import DemoSeedSession

    columns = DemoSeedSession.__table__.columns.keys()
    for col in ("id", "game_id", "start_time", "end_time", "duration_seconds", "status", "source"):
        assert col in columns

    assert "user_id" not in columns
    assert "is_flicker" not in columns


def test_demo_seed_session_nullable_columns():
    from app.models import DemoSeedSession

    table = DemoSeedSession.__table__
    assert table.columns["end_time"].nullable is True
    assert table.columns["duration_seconds"].nullable is True
    assert table.columns["start_time"].nullable is False
    assert table.columns["status"].nullable is False
    assert table.columns["source"].nullable is False


def test_demo_seed_preference_table_name():
    from app.models import DemoSeedPreference

    assert DemoSeedPreference.__tablename__ == "demo_seed_preferences"


def test_demo_seed_preference_columns():
    from app.models import DemoSeedPreference

    columns = DemoSeedPreference.__table__.columns.keys()
    for col in ("id", "game_id", "is_ignored", "is_accepted", "custom_tag"):
        assert col in columns

    assert "user_id" not in columns
    assert "is_flicker" not in columns


def test_demo_seed_preference_nullable_columns():
    from app.models import DemoSeedPreference

    table = DemoSeedPreference.__table__
    assert table.columns["is_accepted"].nullable is True
    assert table.columns["custom_tag"].nullable is True
    assert table.columns["is_ignored"].nullable is False

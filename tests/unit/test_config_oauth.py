from app.core.config import Settings


def _settings(**over):
    base = dict(
        database_url="postgresql+asyncpg://x/y",
        redis_url="redis://x",
        discord_oauth_redirect_uris="gametrace://redirect, https://auth.expo.io/@me/gametrace",
        discord_guild_ids="123, 456",
    )
    base.update(over)
    return Settings(**base)


def test_redirect_uri_allowlist_splits_and_strips():
    s = _settings()
    assert s.discord_redirect_uri_allowlist == {
        "gametrace://redirect",
        "https://auth.expo.io/@me/gametrace",
    }


def test_guild_id_set_splits_and_strips():
    s = _settings()
    assert s.discord_guild_id_set == {"123", "456"}


def test_empty_config_yields_empty_sets():
    s = _settings(discord_oauth_redirect_uris="", discord_guild_ids="")
    assert s.discord_redirect_uri_allowlist == set()
    assert s.discord_guild_id_set == set()

from app.bot.replies import (
    LINK_CODES_UNCONFIGURED,
    MENU_ACTION_FAILED,
    MENU_ACTION_FAILED_EN,
    NOT_REGISTERED,
    MenuCard,
    action_wait,
    code_wait,
    followup_busy,
    menu_code,
    menu_failed,
    menu_loading,
    menu_logout,
    menu_logout_unregistered,
    menu_recent,
    menu_stats,
    menu_text,
    recent_empty,
)


def test_returning_code_is_a_big_number_and_keeps_the_web_url(monkeypatch):
    monkeypatch.setattr("app.bot.replies.settings.gametrace_web_url", "https://gt.example")
    card = menu_code(code="482 913", created=False)
    assert card == MenuCard(
        title="Kod logowania",
        caption="Ważny 5 minut",
        body="# 482 913\n-# Wpisz go w aplikacji.\nhttps://gt.example",
        accent_body="",
    )


def test_first_login_keeps_the_welcome_and_the_wizard_line(monkeypatch):
    monkeypatch.setattr("app.bot.replies.settings.gametrace_web_url", "https://gt.example")
    card = menu_code(code="482 913", created=True)
    assert card.title == "Kod logowania"
    assert card.caption == "Ważny 5 minut"
    assert card.body.startswith("# 482 913\n-# Wpisz go w aplikacji.")
    assert "Co dalej" in card.body
    assert "https://gt.example/login" in card.body


def test_unconfigured_code_is_the_constant_and_paints_red():
    card = menu_text(title="Kod logowania", body=LINK_CODES_UNCONFIGURED)
    assert card.caption is None
    assert card.body == LINK_CODES_UNCONFIGURED
    assert card.accent_body == LINK_CODES_UNCONFIGURED


def test_quiet_week_has_no_big_number_and_keeps_the_review_line():
    card = menu_stats(
        total_seconds=0,
        per_game=[],
        pending_errors_count=0,
        review_count=2,
    )
    assert card.caption == "Ostatnie 7 dni"
    assert not card.body.startswith("#")
    assert "2 gier czeka na potwierdzenie" in card.body
    assert "stąd też się nie liczą" in card.body
    assert "/stats" not in card.body


def test_stats_with_playtime_puts_the_duration_in_the_heading_block():
    card = menu_stats(
        total_seconds=12 * 3600 + 40 * 60,
        per_game=[{"game_name": "Hades II", "total_seconds": 6 * 3600 + 10 * 60}],
        pending_errors_count=1,
        review_count=0,
    )
    assert card.body.startswith("# 12 godz. 40 min\n-# Łącznie")
    assert "**Najwięcej grane**" in card.body
    assert "Hades II — 6 godz. 10 min" in card.body
    assert "\n\nMasz 1 sesje wymagające poprawy" in card.body
    assert "Poza tym masz" not in card.body


def test_playtime_card_keeps_both_pending_and_review_lines():
    card = menu_stats(
        total_seconds=3600,
        per_game=[{"game_name": "Hades II", "total_seconds": 3600}],
        pending_errors_count=3,
        review_count=4,
    )
    assert "\n\nMasz 3 sesje wymagające poprawy — popraw je w aplikacji." in card.body
    assert "\n\n4 gier czeka na potwierdzenie w aplikacji." in card.body
    assert "Poza tym masz" not in card.body
    assert "stąd też się nie liczą" not in card.body


def test_stats_lists_only_the_top_three_games():
    card = menu_stats(
        total_seconds=10_000,
        per_game=[
            {"game_name": "Alpha", "total_seconds": 4000},
            {"game_name": "Beta", "total_seconds": 3000},
            {"game_name": "Gamma", "total_seconds": 2000},
            {"game_name": "Delta", "total_seconds": 1000},
        ],
        pending_errors_count=0,
        review_count=0,
    )
    assert "Alpha" in card.body
    assert "Beta" in card.body
    assert "Gamma" in card.body
    assert "Delta" not in card.body


def test_recent_appends_the_see_more_line():
    card = menu_recent(
        sessions=[{
            "status": "COMPLETED",
            "game": {"primary_name": "Hades II"},
            "start_time": "2026-11-07T20:40:00+00:00",
            "duration_seconds": 7800,
        }],
        user_timezone="UTC",
    )
    assert "- **Hades II** — 07.11 20:40 (2 godz. 10 min)" in card.body
    assert "Zobacz więcej w aplikacji." in card.body


def _completed(name: str) -> dict:
    return {
        "status": "COMPLETED",
        "game": {"primary_name": name},
        "start_time": "2026-11-07T20:40:00+00:00",
        "duration_seconds": 60,
    }


def test_recent_drops_ongoing_and_a_sixth_completed_session():
    card = menu_recent(
        sessions=[
            _completed("Alpha"),
            {
                "status": "ONGOING",
                "game": {"primary_name": "Live Now"},
                "start_time": "2026-11-07T21:00:00+00:00",
                "duration_seconds": None,
            },
            _completed("Beta"),
            _completed("Gamma"),
            _completed("Delta"),
            _completed("Epsilon"),
            _completed("Zeta"),
        ],
        user_timezone="UTC",
    )
    for name in ("Alpha", "Beta", "Gamma", "Delta", "Epsilon"):
        assert name in card.body
    assert "Live Now" not in card.body
    assert "Zeta" not in card.body


def test_empty_recent_uses_recent_empty_without_the_see_more_line():
    card = menu_recent(sessions=[], user_timezone="UTC")
    assert card.body == recent_empty()
    assert "Zobacz więcej" not in card.body
    assert card.caption == "Pięć ostatnich"


def test_logout_caption_uses_the_count_without_parsing_polish():
    card = menu_logout(count=2)
    assert card.caption == "Aplikacja odłączona"
    assert card.body == "Tokeny logowania w aplikacji zostały unieważnione.\n-# 2 tokenów"


def test_logout_unregistered_stays_orange():
    card = menu_logout_unregistered()
    assert card.body == "Nie jesteś zarejestrowany."
    assert card.accent_body == ""


def test_loading_and_failure_frames():
    assert menu_loading("Statystyki") == MenuCard(
        title="Statystyki", caption="Chwila", body="Pobieram…", accent_body=""
    )
    failed = menu_failed("Statystyki")
    assert failed.body == MENU_ACTION_FAILED
    assert failed.accent_body == MENU_ACTION_FAILED
    assert failed.caption is None


def test_not_registered_menu_text_uses_the_raw_string_for_accent():
    card = menu_text(title="Statystyki", body=NOT_REGISTERED)
    assert card.accent_body == NOT_REGISTERED


def test_english_code_card_for_a_new_account(monkeypatch):
    monkeypatch.setattr("app.bot.replies.settings.gametrace_web_url", "https://gt.example")
    card = menu_code(code="482 913", created=True, language="en")
    assert card.title == "Login code"
    assert card.caption == "Valid for 5 minutes"
    assert card.body.startswith("# 482 913\n-# Enter it in the app.")
    assert "https://gt.example/login" in card.body
    assert "sign in with Discord" in card.body


def test_english_quiet_stats_uses_singular_for_one_each():
    card = menu_stats(
        total_seconds=0,
        per_game=[],
        pending_errors_count=1,
        review_count=1,
        language="en",
    )
    assert card.title == "Stats"
    assert card.caption == "Last 7 days"
    assert "1 session that needs a fix" in card.body
    assert "1 game is waiting for confirmation" in card.body
    assert "/stats" not in card.body


def test_english_stats_duration_and_plural_counts():
    card = menu_stats(
        total_seconds=12 * 3600 + 40 * 60,
        per_game=[{"game_name": "Hades II", "total_seconds": 6 * 3600 + 10 * 60}],
        pending_errors_count=3,
        review_count=0,
        language="en",
    )
    assert card.body.startswith("# 12 h 40 min\n-# Total")
    assert "Hades II — 6 h 10 min" in card.body
    assert "You have 3 sessions that need a fix" in card.body


def test_english_recent_and_logout():
    card = menu_recent(
        sessions=[{
            "status": "COMPLETED",
            "game": {"primary_name": "Hades II"},
            "start_time": "2026-11-07T20:40:00+00:00",
            "duration_seconds": 7800,
        }],
        user_timezone="UTC",
        language="en",
    )
    assert card.title == "Recent sessions"
    assert "- **Hades II** — 07.11 20:40 (2 h 10 min)" in card.body
    assert "See more in the app." in card.body

    empty = menu_recent(sessions=[], user_timezone="UTC", language="en")
    assert "Nothing to show here yet" in empty.body
    assert "See more" not in empty.body

    logged_out = menu_logout(count=1, language="en")
    assert logged_out.caption == "App disconnected"
    assert "-# 1 token" in logged_out.body
    assert "1 tokens" not in logged_out.body

    missing = menu_logout_unregistered(language="en")
    assert missing.body == "You are not registered."


def test_english_loading_failure_and_waits():
    assert menu_loading("Stats", language="en") == MenuCard(
        title="Stats", caption="One moment", body="Loading…", accent_body=""
    )
    failed = menu_failed("Stats", language="en")
    assert failed.body == MENU_ACTION_FAILED_EN
    assert failed.accent_body == MENU_ACTION_FAILED_EN
    assert code_wait(12, language="en") == "Wait 12 s before asking for a new code."
    assert action_wait(4, language="en") == "Wait 4 s before using this again."
    assert followup_busy(language="en") == "Too many requests at once. Try again in a moment."

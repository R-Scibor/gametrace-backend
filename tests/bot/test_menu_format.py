from app.bot.replies import (
    LINK_CODES_UNCONFIGURED,
    MENU_ACTION_FAILED,
    NOT_REGISTERED,
    MenuCard,
    menu_code,
    menu_failed,
    menu_loading,
    menu_logout,
    menu_logout_unregistered,
    menu_recent,
    menu_stats,
    menu_text,
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
    assert "https://gt.example/welcome" in card.body


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
    assert "1 sesje wymagające poprawy" in card.body


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

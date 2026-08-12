"""Demo account for the Google Play reviewer login (permanent, non-admin)."""
import hmac

from app.core.config import settings

DEMO_DISCORD_ID = "1"
DEMO_USERNAME = "GameTrace Reviewer"
DEMO_TIMEZONE = "Europe/Warsaw"  # matches default_timezone
DEMO_LANGUAGE = "en"  # Play review instructions are English; model default is pl
DEMO_MAX_TOKENS = 5


def is_demo_code(code: str) -> bool:
    """True when `code` matches the configured reviewer login code.

    Short-circuits on an empty setting (feature off) before comparing, so an
    empty submitted code can never match an empty/unconfigured setting.
    """
    configured = settings.demo_link_code.strip()
    if not configured:
        return False
    return hmac.compare_digest(configured, code.strip())

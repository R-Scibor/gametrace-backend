from typing import Any

import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration

from app.core.config import settings


def _before_send(event: dict, hint: dict) -> dict:
    request = event.get("request")
    if not isinstance(request, dict):
        return event

    headers = request.get("headers")
    if isinstance(headers, dict):
        for key in list(headers.keys()):
            if key.lower() == "authorization":
                headers[key] = "[scrubbed]"

    qs = request.get("query_string")
    if isinstance(qs, str) and "token=" in qs:
        request["query_string"] = "[scrubbed]"

    return event


def init_sentry(component: str) -> None:
    if not settings.sentry_dsn:
        return

    integrations: list[Any] = []
    if component == "celery":
        integrations.append(CeleryIntegration(monitor_beat_tasks=True))

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        traces_sample_rate=0.0,
        send_default_pii=False,
        before_send=_before_send,
        integrations=integrations,
    )
    sentry_sdk.set_tag("component", component)

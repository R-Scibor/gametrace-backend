from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging

from app.core.config import settings
from app.core.logging import configure_logging
from app.core.observability import init_sentry

configure_logging(settings.log_component or "celery", settings.log_level)
init_sentry("celery")


@setup_logging.connect
def _on_setup_logging(**_kwargs):
    # Own logging entirely: connecting to setup_logging disables Celery's
    # default config so our JSON formatter is not overwritten on worker boot.
    configure_logging(settings.log_component or "celery", settings.log_level)

celery_app = Celery(
    "gametrace",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.enrichment",
        "app.tasks.weekly_report",
        "app.tasks.cleanup",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    worker_hijack_root_logger=False,
    beat_schedule={
        "weekly_report": {
            "task": "tasks.weekly_report",
            "schedule": crontab(day_of_week="mon", hour=9, minute=0),
        },
        "hard_delete_sweep": {
            "task": "tasks.hard_delete_sweep",
            "schedule": crontab(hour=3, minute=30),
        },
        "purge_flicker_sessions": {
            "task": "tasks.purge_flicker_sessions",
            "schedule": crontab(hour=4, minute=0),
        },
    },
)

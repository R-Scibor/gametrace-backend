from celery import Celery
from celery.schedules import crontab

from app.core.config import settings
from app.core.observability import init_sentry

init_sentry("celery")

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
    beat_schedule={
        "weekly_report": {
            "task": "tasks.weekly_report",
            "schedule": crontab(day_of_week="mon", hour=9, minute=0),
        },
        "hard_delete_sweep": {
            "task": "tasks.hard_delete_sweep",
            "schedule": crontab(hour=3, minute=30),
        },
    },
)

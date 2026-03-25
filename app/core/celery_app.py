from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "gametrace",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.enrichment"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
)

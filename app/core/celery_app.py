import structlog
from celery import Celery
from celery.schedules import crontab
from celery.signals import before_task_publish, setup_logging, task_postrun, task_prerun

from app.core.config import settings
from app.core.logging import configure_logging, new_trace_id
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
        "purge_deleted_accounts": {
            "task": "tasks.purge_deleted_accounts",
            "schedule": crontab(hour=3, minute=45),
        },
    },
)


@before_task_publish.connect
def _attach_trace_header(headers=None, **_):
    if headers is None:
        return
    ctx = structlog.contextvars.get_contextvars()
    trace_id = ctx.get("trace_id") or ctx.get("request_id")
    if trace_id:
        headers["trace_id"] = trace_id


@task_prerun.connect
def _bind_task_trace(task=None, **_):
    request = getattr(task, "request", None)
    headers = getattr(request, "headers", None) or {}
    trace_id = headers.get("trace_id")
    if not trace_id:
        trace_id = f"task-{getattr(request, 'id', new_trace_id())}"
    structlog.contextvars.bind_contextvars(trace_id=trace_id)


@task_postrun.connect
def _clear_task_trace(**_):
    structlog.contextvars.clear_contextvars()

"""Central logging configuration.

The application keeps using stdlib ``logging``. This module installs one
structlog ``ProcessorFormatter`` on the root logger so every record — stdlib or
otherwise — renders as a single JSON line, merges any ``structlog.contextvars``
(e.g. request_id / trace_id), and surfaces ``extra={...}`` fields.
"""
import logging
import sys
import uuid

import structlog


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


def _add_component(component: str):
    def processor(logger, method_name, event_dict):
        event_dict["component"] = component
        return event_dict

    return processor


# Framework loggers that install their own handlers; we clear those and let them
# propagate to the root handler so their output is JSON too.
_FOREIGN_LOGGERS = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "celery",
    "celery.worker",
    "gunicorn",
    "gunicorn.error",
    "gunicorn.access",
)


def _tame_foreign_loggers() -> None:
    for name in _FOREIGN_LOGGERS:
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True


def configure_logging(component: str, level: str = "INFO") -> None:
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_component(component),
    ]

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[*shared_processors, structlog.stdlib.ExtraAdder()],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    _tame_foreign_loggers()
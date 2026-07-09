from types import SimpleNamespace

import structlog

from app.core.celery_app import (
    _attach_trace_header,
    _bind_task_trace,
    _clear_task_trace,
)


def test_publish_copies_bound_trace_into_headers():
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(trace_id="bot-123")
    try:
        headers = {}
        _attach_trace_header(headers=headers)
        assert headers["trace_id"] == "bot-123"
    finally:
        structlog.contextvars.clear_contextvars()


def test_publish_falls_back_to_request_id():
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="req-9")
    try:
        headers = {}
        _attach_trace_header(headers=headers)
        assert headers["trace_id"] == "req-9"
    finally:
        structlog.contextvars.clear_contextvars()


def test_prerun_binds_header_trace():
    structlog.contextvars.clear_contextvars()
    task = SimpleNamespace(request=SimpleNamespace(headers={"trace_id": "carried-42"}))
    _bind_task_trace(task=task)
    assert structlog.contextvars.get_contextvars()["trace_id"] == "carried-42"
    _clear_task_trace()
    assert "trace_id" not in structlog.contextvars.get_contextvars()


def test_prerun_synthesizes_when_no_header():
    structlog.contextvars.clear_contextvars()
    task = SimpleNamespace(request=SimpleNamespace(id="beat-task-id", headers={}))
    _bind_task_trace(task=task)
    assert structlog.contextvars.get_contextvars()["trace_id"] == "task-beat-task-id"
    _clear_task_trace()
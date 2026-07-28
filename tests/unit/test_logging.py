import json
import logging

import structlog

from app.core.logging import configure_logging, new_trace_id
from app.core.observability import log_admin_action


def _install(caplog, component: str, level: str = "INFO") -> None:
    """Configure JSON logging and preserve pytest caplog capture.

    ``configure_logging`` clears root handlers (including caplog's), so re-attach
    caplog's handler and give it the same formatter as our StreamHandler.
    """
    configure_logging(component, level)
    root = logging.getLogger()
    json_handler = root.handlers[0]
    caplog.handler.setFormatter(json_handler.formatter)
    if caplog.handler not in root.handlers:
        root.addHandler(caplog.handler)


def _capture(caplog):
    """Return the JSON-rendered line for the single captured record."""
    record = caplog.records[-1]
    assert caplog.handler.formatter is not None
    return json.loads(caplog.handler.formatter.format(record))


def test_configure_logging_renders_json_with_core_keys(caplog):
    _install(caplog, "api")
    logging.getLogger("app.demo").info("hello_world")
    line = _capture(caplog)
    assert line["event"] == "hello_world"
    assert line["level"] == "info"
    assert line["component"] == "api"
    assert line["logger"] == "app.demo"
    assert "timestamp" in line


def test_extra_fields_surface_as_json(caplog):
    _install(caplog, "worker")
    logging.getLogger("app.demo").warning("enrich.failed", extra={"game_id": 42})
    line = _capture(caplog)
    assert line["event"] == "enrich.failed"
    assert line["game_id"] == 42


def test_contextvars_merge_into_output(caplog):
    _install(caplog, "api")
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="abc123")
    try:
        logging.getLogger("app.demo").info("in_request")
        line = _capture(caplog)
        assert line["request_id"] == "abc123"
    finally:
        structlog.contextvars.clear_contextvars()


def test_new_trace_id_is_short_and_unique():
    a, b = new_trace_id(), new_trace_id()
    assert a != b
    assert len(a) == 12


def test_log_admin_action_emits_detail(caplog):
    _install(caplog, "api")
    log_admin_action(
        admin_id="1",
        action="delete_report",
        resource="report:42",
        detail="preview of deleted message",
    )
    line = _capture(caplog)
    assert line["event"] == "admin_action"
    assert line["detail"] == "preview of deleted message"


def test_log_admin_action_without_detail_still_logs(caplog):
    _install(caplog, "api")
    log_admin_action(admin_id="1", action="enrich_requeue", resource="game:5")
    line = _capture(caplog)
    assert line["event"] == "admin_action"
    assert line["detail"] is None

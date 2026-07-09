import logging

from app.core.logging import _tame_foreign_loggers


def test_tame_clears_foreign_handlers_and_enables_propagation():
    uv = logging.getLogger("uvicorn.access")
    uv.addHandler(logging.StreamHandler())
    uv.propagate = False

    _tame_foreign_loggers()

    assert uv.handlers == []
    assert uv.propagate is True


def test_celery_does_not_hijack_root_logger():
    from app.core.celery_app import celery_app

    assert celery_app.conf.worker_hijack_root_logger is False
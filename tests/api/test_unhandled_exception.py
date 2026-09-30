"""The catch-all 500 must keep a traceback the operator can match to the client."""
import json

from starlette.testclient import TestClient

from app.main import app


async def _boom():
    raise RuntimeError("secret boom")


def test_unhandled_exception_logs_request_id_and_hides_the_detail(capsys):
    # Lifespan calls configure_logging, which replaces the root handlers, so
    # caplog never sees this line. The JSON on stdout is the API log.
    app.add_api_route("/__test_unhandled", _boom, methods=["GET"])
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                "/__test_unhandled", headers={"X-Request-ID": "req-deadbeef"}
            )
        captured = capsys.readouterr()
    finally:
        app.router.routes[:] = [
            route
            for route in app.router.routes
            if getattr(route, "path", None) != "/__test_unhandled"
        ]

    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal server error"}
    assert "secret boom" not in resp.text
    assert resp.headers["x-request-id"] == "req-deadbeef"

    payload = next(
        json.loads(line)
        for line in captured.out.splitlines()
        if '"unhandled_exception"' in line
    )
    assert payload["request_id"] == "req-deadbeef"
    assert payload["logger"] == "app.main"
    assert payload["path"] == "/__test_unhandled"
    assert "secret boom" in "".join(payload["exc_info"])

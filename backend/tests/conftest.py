"""Explicit CI suites: fail closed on missing PostgreSQL and unexpected skips."""

import os

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

REAL_TESTS = {
    "test_real_semantic_retrieval_opt_in",
    "test_real_cohere_chinese_contract",
}
_skipped = []


def pytest_addoption(parser):
    parser.addoption("--ci-suite", choices=("backend", "e2e"), default=None)


def pytest_configure(config):
    _skipped.clear()
    if not config.getoption("--ci-suite"):
        return
    if any(os.getenv(key) for key in ("OPENAI_API_KEY", "COHERE_API_KEY")) or any(
        os.getenv(key) == "1" for key in ("RUN_REAL_RETRIEVAL", "RUN_REAL_RERANK")
    ):
        raise pytest.UsageError("CI must not receive real model credentials/flags")
    # Validate above first; only normalize genuinely empty optional values.
    for key in ("OPENAI_API_KEY", "COHERE_API_KEY"):
        if os.environ.get(key) == "":
            del os.environ[key]
    url = os.getenv("TEST_POSTGRES_ADMIN_URL", "")
    engine = None
    try:
        if make_url(url).get_backend_name() != "postgresql":
            raise ValueError("PostgreSQL required")
        engine = create_engine(url, connect_args={"connect_timeout": 3})
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_available_extensions "
                        "WHERE name='vector'"
                    )
                )
                == 1
            )
    except Exception:
        raise pytest.UsageError(
            "CI requires a reachable disposable PostgreSQL+pgvector "
            "TEST_POSTGRES_ADMIN_URL (never SQLite)"
        ) from None
    finally:
        if engine is not None:
            engine.dispose()


def pytest_collection_modifyitems(config, items):
    suite = config.getoption("--ci-suite")
    if not suite:
        return
    selected, excluded = [], []
    for item in items:
        browser = item.path.name.startswith("test_frontend_")
        wanted = browser if suite == "e2e" else not browser
        (
            selected if wanted and item.originalname not in REAL_TESTS else excluded
        ).append(item)
    items[:] = selected
    config.hook.pytest_deselected(items=excluded)


def pytest_runtest_logreport(report):
    if report.skipped:
        _skipped.append(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    if session.config.getoption("--ci-suite") and _skipped:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter:
            reporter.write_sep("!", "CI rejected unexpected skipped tests")
            for nodeid in _skipped:
                reporter.write_line(nodeid)


@pytest.fixture(autouse=True)
def ci_local_http_only(request, monkeypatch):
    if not request.config.getoption("--ci-suite"):
        return
    original = httpx.HTTPTransport.handle_request
    original_async = httpx.AsyncHTTPTransport.handle_async_request

    def check(req):
        if req.url.host not in {"localhost", "127.0.0.1", "::1"}:
            raise AssertionError("CI forbids external model HTTP; use MockTransport")

    def send(self, req):
        check(req)
        return original(self, req)

    async def send_async(self, req):
        check(req)
        return await original_async(self, req)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", send)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", send_async)


@pytest.fixture(autouse=True)
def controlled_jwt_clock(request, monkeypatch):
    """Keep signing and verification on one controllable clock in process tests.

    WSL wall-clock corrections can move backwards between otherwise valid requests.
    Browser subprocesses keep their real clock; production JWT validation is intact.
    """
    if request.node.path.name.startswith("test_frontend_"):
        return
    from datetime import datetime, timezone

    import jwt.api_jwt

    from app.services import auth

    now = datetime.now(timezone.utc)

    class ClockMeta(type):
        def __instancecheck__(cls, instance):
            # PyJWT also uses isinstance(value, datetime) when encoding claims.
            return isinstance(instance, datetime)

    class Clock(datetime, metaclass=ClockMeta):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(auth, "datetime", Clock)
    monkeypatch.setattr(jwt.api_jwt, "datetime", Clock)

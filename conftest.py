"""
Shared pytest fixtures.

Fixture chain:
    b2b_session (session-scoped) -- logs in via UI ONCE per test run
                                     (pauses for human captcha solve if the
                                     cached session is missing/stale)
        -> api_client (function-scoped) -- fresh RoutechAPIClient per test,
                                            built from that one session

This means: run `pytest`, solve the captcha once when prompted, and every
test after that reuses the same session purely via API calls.
"""
import pytest

from core.auth import ensure_logged_in
from api.client import RoutechAPIClient


@pytest.fixture(scope="session")
def b2b_session():
    """Path to a fresh (or reused-if-still-valid) storage_state for the B2B account."""
    return ensure_logged_in(account_type="b2b")


@pytest.fixture(scope="session")
def c2c_session():
    """Path to a fresh (or reused-if-still-valid) storage_state for the C2C account."""
    return ensure_logged_in(account_type="c2c")


@pytest.fixture
def api_client(b2b_session):
    """A RoutechAPIClient authenticated as the B2B account, closed after each test."""
    client = RoutechAPIClient(b2b_session)
    yield client
    client.close()


@pytest.fixture
def c2c_api_client(c2c_session):
    """A RoutechAPIClient authenticated as the C2C account, closed after each test."""
    client = RoutechAPIClient(c2c_session)
    yield client
    client.close()

"""Shared fixtures and CLI options for integration tests."""

from __future__ import annotations

import os
import pytest


def pytest_addoption(parser):
    parser.addoption("--api-url",   default=None, help="Base URL of the API server")
    parser.addoption("--api-key",   default=None, help="Valid user API key")
    parser.addoption("--admin-key", default=None, help="Valid admin API key")


@pytest.fixture(scope="session")
def base_url(request):
    return (
        request.config.getoption("--api-url")
        or os.getenv("API_URL", "http://localhost:5000")
    ).rstrip("/")


@pytest.fixture(scope="session")
def api_key(request):
    return (
        request.config.getoption("--api-key")
        or os.getenv("API_KEY", "")
    )


@pytest.fixture(scope="session")
def admin_key(request):
    return (
        request.config.getoption("--admin-key")
        or os.getenv("ADMIN_KEY", "")
    )


@pytest.fixture(scope="session")
def user_headers(api_key):
    if api_key:
        return {"X-API-Key": api_key}
    return {}  # open dev mode

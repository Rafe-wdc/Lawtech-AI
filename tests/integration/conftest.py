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
    """Base URL including the route prefix.

    Locally the app serves on /pyapi, but behind nginx the public prefix
    is /pyapiv2 (rewritten to /pyapi).  Set API_PREFIX to override.
    Default: /pyapi  (matches local dev).
    """
    origin = (
        request.config.getoption("--api-url")
        or os.getenv("API_URL", "http://localhost:5000")
    ).rstrip("/")
    prefix = os.getenv("API_PREFIX", "/pyapi").strip("/")
    return f"{origin}/{prefix}" if prefix else origin


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

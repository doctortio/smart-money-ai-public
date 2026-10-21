from __future__ import annotations

import socket

import pytest


BLOCKED_HOST_MARKERS = (
    "api.mercadopago.com",
    "mercadopago.com",
    "supabase.co",
    "smart-money-ai-4.streamlit.app",
)


@pytest.fixture(autouse=True)
def deny_external_network(monkeypatch: pytest.MonkeyPatch):
    def _blocked_socket(*args, **kwargs):
        raise AssertionError("NETWORK_DENIED_BY_CRITICAL_REGRESSION_SUITE")

    def _blocked_create_connection(address, *args, **kwargs):
        raise AssertionError(f"NETWORK_DENIED:{address}")

    monkeypatch.setattr(socket, "socket", _blocked_socket)
    monkeypatch.setattr(socket, "create_connection", _blocked_create_connection)

    try:
        import requests

        def _blocked_request(self, method, url, *args, **kwargs):
            url_s = str(url)
            if any(marker in url_s for marker in BLOCKED_HOST_MARKERS):
                raise AssertionError(f"REAL_EXTERNAL_CALL_BLOCKED:{url_s}")
            raise AssertionError(f"NETWORK_DENIED:{url_s}")

        monkeypatch.setattr(requests.sessions.Session, "request", _blocked_request)
        monkeypatch.setattr(requests, "request", lambda method, url, *a, **k: _blocked_request(None, method, url, *a, **k))
    except Exception:
        pass

"""Python release tests must never contact OCI, a database or tracing services."""
import socket
import ipaddress

import pytest


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    original_connect = socket.socket.connect
    original_create = socket.create_connection
    def allow_loopback(address):
        # Windows implements asyncio's internal socketpair through loopback TCP.
        try:
            if ipaddress.ip_address(address[0]).is_loopback:
                return
        except (ValueError, TypeError, IndexError):
            pass
        raise AssertionError("External network is forbidden in offline release tests")
    def connect(sock, address):
        allow_loopback(address)
        return original_connect(sock, address)
    def create(address, *args, **kwargs):
        allow_loopback(address)
        return original_create(address, *args, **kwargs)
    monkeypatch.setattr(socket, "create_connection", create)
    monkeypatch.setattr(socket.socket, "connect", connect)

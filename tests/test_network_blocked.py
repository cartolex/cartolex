# SPDX-License-Identifier: MIT
"""The test suite must never reach the network."""

from __future__ import annotations

import socket

import pytest


def test_outbound_connection_is_refused() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(RuntimeError, match="network access is blocked"):
            sock.connect(("192.0.2.1", 443))  # TEST-NET-1, never routable


def test_loopback_stays_open() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.connect(("127.0.0.1", port))

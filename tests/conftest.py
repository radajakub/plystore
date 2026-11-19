from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
import uvicorn

from plystore.server.app import create_app
from plystore.server.db import Database, open_databases

# The service tests run the real service (in a thread of the test process) on a real Postgres: the server PLYSTORE_TEST_DATABASE_URL names,
# e.g. the local compose one (make test-postgres). The database plystore_test there is emptied before every test.

TEST_DATABASE = "plystore_test"
TOKEN = "test-token"


@dataclass
class Service:
    url: str
    database: Database


@pytest.fixture(scope="session")
def service_session() -> Iterator[Service]:
    server_url = os.environ.get("PLYSTORE_TEST_DATABASE_URL")
    if not server_url:
        pytest.skip("needs PostgreSQL: set PLYSTORE_TEST_DATABASE_URL (make test-postgres)")
    databases = open_databases(server_url, [TEST_DATABASE])
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(databases, TOKEN), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    yield Service(f"http://127.0.0.1:{port}", databases[TEST_DATABASE])
    server.should_exit = True
    thread.join()


@pytest.fixture
def service(service_session: Service) -> Service:
    service_session.database.truncate()
    return service_session

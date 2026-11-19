from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from plystore.postgres import migrations
from plystore.postgres.store import PostgresStore, engine_for

# The Postgres tests run on a real server: the database PLYSTORE_TEST_DATABASE_URL names, e.g. the local compose one (make test-postgres).
# They use the schema plystore_test there, migrated once and emptied before every test.

SCHEMA = "plystore_test"


@pytest.fixture(scope="session")
def postgres_url() -> str:
    url = os.environ.get("PLYSTORE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("needs PostgreSQL: set PLYSTORE_TEST_DATABASE_URL (make test-postgres)")
    return url


@pytest.fixture(scope="session")
def postgres_session(postgres_url: str) -> Iterator[PostgresStore]:
    engine = engine_for(postgres_url, SCHEMA)
    with engine.begin() as conn:
        migrations.upgrade(conn, SCHEMA)
    engine.dispose()
    store = PostgresStore(postgres_url, SCHEMA)
    yield store
    store.engine.dispose()


@pytest.fixture
def postgres(postgres_session: PostgresStore) -> PostgresStore:
    postgres_session.truncate()
    return postgres_session

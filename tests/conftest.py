import os

import pytest

os.environ.setdefault("IMMO_DISABLE_CLAUDE", "1")  # jamais d'appel réseau à Claude pendant les tests
os.environ.pop("IMMO_PASSWORD", None)
os.environ.pop("DATABASE_URL", None)

from immoscope.db import Database, set_db  # noqa: E402

# Pour lancer toute la suite sur PostgreSQL : IMMO_TEST_DATABASE_URL=postgresql://… pytest
TEST_PG_URL = os.getenv("IMMO_TEST_DATABASE_URL")


@pytest.fixture()
def db():
    database = Database(url=TEST_PG_URL) if TEST_PG_URL else Database(":memory:")
    if TEST_PG_URL:
        database.clear()
    set_db(database)
    yield database
    set_db(None)
    database.close()

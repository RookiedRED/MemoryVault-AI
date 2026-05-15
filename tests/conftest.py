import importlib

import pytest
from fastapi.testclient import TestClient

from app.database import get_connection, init_db


@pytest.fixture
def tmp_db(tmp_path):
    """Fresh on-disk DB for each test (sqlite-vec requires a real file)."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    return db_path


@pytest.fixture
def conn(tmp_db):
    """Configured sqlite3 connection to the temp DB."""
    c = get_connection(tmp_db)
    yield c
    c.close()


@pytest.fixture
def client(tmp_db, monkeypatch):
    """
    FastAPI TestClient backed by a fresh per-test DB.

    Reload order matters: config first, then route modules (so their
    module-level DB_PATH captures the new value), then main.
    """
    monkeypatch.setenv("DB_PATH", tmp_db)

    import app.config as cfg
    importlib.reload(cfg)

    import app.database as db_mod
    importlib.reload(db_mod)

    # Reload every route module so their `from app.config import DB_PATH`
    # re-executes against the freshly reloaded config.
    import app.routes.ask as ask_mod
    import app.routes.audit as audit_mod
    import app.routes.privacy as privacy_mod
    import app.routes.queries as queries_mod
    import app.routes.vault as vault_mod
    for mod in (ask_mod, vault_mod, privacy_mod, audit_mod, queries_mod):
        importlib.reload(mod)

    import app.main as main_mod
    importlib.reload(main_mod)

    with TestClient(main_mod.app) as c:
        yield c

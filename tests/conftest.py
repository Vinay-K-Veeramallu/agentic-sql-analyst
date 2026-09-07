import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import db as db_module  # noqa: E402


@pytest.fixture(scope="session")
def db_path():
    path = os.path.join(ROOT, "data", "analytics.db")
    if not os.path.exists(path):
        pytest.skip("data/analytics.db missing - run: python scripts/seed_db.py")
    return path


@pytest.fixture
def conn(db_path):
    connection = db_module.connect(db_path)
    yield connection
    connection.close()

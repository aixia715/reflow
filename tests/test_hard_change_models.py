import pytest
from app.db import connect, init_db
from app import models


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    return c


def test_hard_change_tables_exist(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "hard_changes" in names
    assert "hard_change_images" in names

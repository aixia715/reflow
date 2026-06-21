"""数据迁移测试：occurred_at 时区转换。"""
import sqlite3
from app.migrations import to_utc_from_singapore, migrate_occurred_at
from app.db import init_db


def test_naive_singapore_to_utc():
    # 新加坡 +08:00：09:10 → UTC 01:10
    assert to_utc_from_singapore("2026-06-13T09:10") == "2026-06-13T01:10:00+00:00"


def test_already_offset_is_idempotent():
    s = "2026-06-13T01:10:00+00:00"
    assert to_utc_from_singapore(s) == s
    assert to_utc_from_singapore(to_utc_from_singapore("2026-06-13T09:10")) \
        == "2026-06-13T01:10:00+00:00"


def test_z_suffix_left_untouched():
    s = "2026-06-13T01:10:00Z"
    assert to_utc_from_singapore(s) == s


def test_migrate_occurred_at_end_to_end(tmp_path):
    db = tmp_path / "m.sqlite"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    init_db(conn)            # 建表
    conn.execute("INSERT INTO hard_changes(board_id,title,description,occurred_at,created_at)"
                 " VALUES(1,'a','',?,?)", ("2026-06-13T09:10", "2026-06-13T01:00:00+00:00"))
    conn.execute("INSERT INTO hard_changes(board_id,title,description,occurred_at,created_at)"
                 " VALUES(1,'b','',?,?)", ("2026-06-14T00:00:00+00:00", "2026-06-13T01:00:00+00:00"))
    conn.commit()
    n = migrate_occurred_at(conn)
    assert n == 1   # 仅无偏移那条被转换
    vals = [r["occurred_at"] for r in conn.execute("SELECT occurred_at FROM hard_changes ORDER BY id")]
    assert vals == ["2026-06-13T01:10:00+00:00", "2026-06-14T00:00:00+00:00"]
    assert migrate_occurred_at(conn) == 0   # 幂等：再跑无变化


def test_migrate_occurred_at_end_to_end(tmp_path):
    db = tmp_path / "m.sqlite"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    init_db(conn)            # 建表
    conn.execute("INSERT INTO hard_changes(board_id,title,description,occurred_at,created_at)"
                 " VALUES(1,'a','',?,?)", ("2026-06-13T09:10", "2026-06-13T01:00:00+00:00"))
    conn.execute("INSERT INTO hard_changes(board_id,title,description,occurred_at,created_at)"
                 " VALUES(1,'b','',?,?)", ("2026-06-14T00:00:00+00:00", "2026-06-13T01:00:00+00:00"))
    conn.commit()
    n = migrate_occurred_at(conn)
    assert n == 1   # 仅无偏移那条被转换
    vals = [r["occurred_at"] for r in conn.execute("SELECT occurred_at FROM hard_changes ORDER BY id")]
    assert vals == ["2026-06-13T01:10:00+00:00", "2026-06-14T00:00:00+00:00"]
    assert migrate_occurred_at(conn) == 0   # 幂等：再跑无变化

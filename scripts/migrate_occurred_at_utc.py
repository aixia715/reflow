"""把 hard_changes.occurred_at 旧的无偏移本地时间按新加坡时区补成 UTC。

用法： REFLOW_DB=reflow.sqlite python scripts/migrate_occurred_at_utc.py
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.migrations import migrate_occurred_at   # noqa: E402


def main():
    db = os.environ.get("REFLOW_DB", "reflow.sqlite")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    n = migrate_occurred_at(conn)
    print(f"已转换 {n} 条 occurred_at → UTC（库：{db}）")


if __name__ == "__main__":
    main()

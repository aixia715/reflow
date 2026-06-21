"""一次性数据迁移的纯逻辑：把无偏移的本地 occurred_at 转为 canonical UTC。"""
from datetime import datetime, timedelta, timezone

SGT = timezone(timedelta(hours=8))   # 新加坡，固定 +08:00，无夏令时


def to_utc_from_singapore(value: str) -> str:
    """无偏移本地时间（视为新加坡 +08:00）→ canonical UTC（YYYY-MM-DDTHH:MM:SS+00:00）。
    已带偏移或 Z 后缀的原样返回（幂等）。"""
    if not value:
        return value
    if value.endswith("Z") or "+" in value or value.count("-") > 2:
        return value
    dt = datetime.fromisoformat(value).replace(tzinfo=SGT)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def migrate_occurred_at(conn) -> int:
    """把 hard_changes.occurred_at 中无偏移的旧值转为 UTC，返回转换条数。"""
    rows = conn.execute("SELECT id, occurred_at FROM hard_changes").fetchall()
    n = 0
    for r in rows:
        new = to_utc_from_singapore(r["occurred_at"])
        if new != r["occurred_at"]:
            conn.execute("UPDATE hard_changes SET occurred_at=? WHERE id=?",
                         (new, r["id"]))
            n += 1
    conn.commit()
    return n

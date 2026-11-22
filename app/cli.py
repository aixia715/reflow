"""命令行：创建第一个管理员、补认领无主单板。

`create-user` 留到按单板授权那一阶段再挂上。阶段 1 登录之后权限仍与单人使用相同，
提前造普通用户等于再发一批超级用户。

密码只从 getpass 读，不进 argv，也不进 shell 历史。
"""

import argparse
import logging
import os
import sys

from app.auth import normalize_username
from app.db import connect, init_db
from app import models

_log = logging.getLogger("reflow.auth")
_warned: set[str] = set()


def _warn_once(message: str) -> None:
    """引导失败每个原因只打一次。get_conn 每个请求都会进来，不能刷屏。"""
    if message in _warned:
        return
    _warned.add(message)
    _log.warning(message)


def bootstrap_admin_from_env(conn) -> bool:
    """两个引导变量都在、用户表为空、用户名合法、密码不少于 8 位时创建管理员。

    任一条件不满足返回 False，不抛，让进程继续（/healthz 仍要通）。
    库里已有用户时不改密码，也不打日志。
    """
    username = os.environ.get("REFLOW_BOOTSTRAP_USERNAME", "")
    password = os.environ.get("REFLOW_BOOTSTRAP_PASSWORD", "")
    if username == "" and password == "":
        return False
    if username == "" or password == "":
        _warn_once("引导环境变量不完整，未创建管理员")
        return False
    existing = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    if existing:
        return False
    if len(password) < 8:
        _warn_once("引导密码短于 8 位，未创建管理员")
        return False
    try:
        name = normalize_username(username)
    except ValueError:
        _warn_once("引导用户名不合法，未创建管理员")
        return False
    try:
        models.create_user(conn, name, password, is_admin=True)
    except Exception:
        _warn_once("引导创建管理员失败")
        return False
    _log.info("已根据环境变量创建第一个管理员 %s", name)
    return True


def _open():
    conn = connect(os.environ.get("REFLOW_DB", "reflow.sqlite"))
    init_db(conn)
    return conn


def _read_new_password() -> str | None:
    import getpass
    first = getpass.getpass("密码：")
    second = getpass.getpass("再输入一次密码：")
    if first != second:
        print("两次输入的密码不一致", file=sys.stderr)
        return None
    return first


def _create_admin(username: str) -> int:
    password = _read_new_password()
    if password is None:
        return 2
    conn = _open()
    try:
        if conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]:
            print("已经有用户，create-admin 只用于创建第一个管理员", file=sys.stderr)
            return 1
        user_id = models.create_user(conn, username, password, is_admin=True)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    owned = conn.execute(
        "SELECT COUNT(*) AS n FROM board_members WHERE user_id=? AND role='owner'",
        (user_id,),
    ).fetchone()["n"]
    print(f"已创建管理员 {models.get_user_by_username(conn, username)['username']}，认领了 {owned} 块尚无所有者的单板")
    return 0


def _assign_orphans(username: str) -> int:
    """只补还没有所有者的单板。BOM 版本负责人在后续阶段再补，这里不动。"""
    conn = _open()
    user = models.get_user_by_username(conn, username)
    if user is None:
        print("没有这个用户", file=sys.stderr)
        return 1
    if not user["is_admin"]:
        print("只能指定系统管理员", file=sys.stderr)
        return 1
    n = models.assign_boards_without_owner(conn, user["id"])
    print(f"已认领 {n} 块尚无所有者的单板")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    create = sub.add_parser("create-admin", help="在空库上创建第一个系统管理员")
    create.add_argument("--username", required=True)

    orphans = sub.add_parser("assign-orphans", help="把尚无所有者的单板认领给指定管理员")
    orphans.add_argument("--username", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "create-admin":
        return _create_admin(args.username)
    if args.cmd == "assign-orphans":
        return _assign_orphans(args.username)
    parser.error(f"未知命令 {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

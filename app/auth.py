"""登录相关的纯逻辑。不 import FastAPI，不碰数据库。"""

import base64
import hashlib
import hmac
import os
import re
import secrets

_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")
USERNAME_ERROR = "登录名需为 3–32 位小写字母、数字、下划线或连字符，且以字母或数字开头"

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32

_ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def hash_password(password: str) -> str:
    """返回 `scrypt$n$r$p$<salt>$<hash>`。参数写在串里，以后调高成本不用迁移旧密码。"""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode(), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
    )
    return (
        f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}"
        f"${_b64encode(salt)}${_b64encode(dk)}"
    )


# 用户不存在时再算一次，避免响应时间分成「无此用户 / 密码错误」两截。
# 明文不对应任何账号。每次进程启动换 salt，只为消耗同样的 scrypt。
DUMMY_PASSWORD_HASH = hash_password("reflow-dummy-password")


def verify_password(password: str, stored: str) -> bool:
    """校验密码。格式拆不开时返回 False，不抛。n/r/p 从哈希串读取，不写死。"""
    try:
        kind, n_s, r_s, p_s, salt_s, hash_s = stored.split("$")
        if kind != "scrypt":
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = _b64decode(salt_s)
        expected = _b64decode(hash_s)
        got = hashlib.scrypt(
            password.encode(), salt=salt, n=n, r=r, p=p, dklen=len(expected),
        )
        return hmac.compare_digest(got, expected)
    except Exception:
        return False


def password_ok(password: str, stored: str | None) -> bool:
    """无论用户是否存在都跑一次 scrypt。stored 为空时用固定的 dummy 哈希。"""
    if stored:
        return verify_password(password, stored)
    verify_password(password, DUMMY_PASSWORD_HASH)
    return False


def tokens_equal(a: str, b: str) -> bool:
    """常量时间比较 CSRF 令牌。空值或长度不同直接失败，compare_digest 不接受不等长。"""
    if not a or not b:
        return False
    a_b, b_b = a.encode(), b.encode()
    if len(a_b) != len(b_b):
        return False
    return hmac.compare_digest(a_b, b_b)


def normalize_username(raw: str) -> str:
    """去掉首尾空白并折成小写。不合法时抛 ValueError，消息给页面和 CLI 用。"""
    name = (raw or "").strip().lower()
    if not _USERNAME_RE.fullmatch(name):
        raise ValueError(USERNAME_ERROR)
    return name


def role_at_least(have: str | None, need: str) -> bool:
    """owner ≥ editor ≥ viewer。have 为空或角色名不认识时什么都不满足。"""
    if have not in _ROLE_RANK or need not in _ROLE_RANK:
        return False
    return _ROLE_RANK[have] >= _ROLE_RANK[need]


def safe_next(raw: str | None) -> str:
    """只接受以单个 / 开头的站内路径。开放重定向一律回首页。"""
    if not raw:
        return "/"
    value = raw.strip()
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    if "\\" in value or "://" in value:
        return "/"
    if any(ord(ch) < 32 or ch == " " for ch in value):
        return "/"
    return value


def session_days() -> int:
    """滑动过期的天数。非法或非正数时回到默认 14。"""
    raw = os.environ.get("REFLOW_SESSION_DAYS", "14")
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return 14
    return days if days > 0 else 14


def cookie_secure() -> bool:
    """默认关闭。当前部署是局域网 HTTP，Secure 会让浏览器丢掉 cookie。"""
    return os.environ.get("REFLOW_COOKIE_SECURE", "0") == "1"

import hashlib

import pytest

from app.auth import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    normalize_username,
    password_ok,
    role_at_least,
    safe_next,
    verify_password,
)


def _b64(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def test_password_roundtrip_and_wrong_password():
    stored = hash_password("correct-horse")
    assert stored.startswith("scrypt$16384$8$1$")
    assert verify_password("correct-horse", stored)
    assert not verify_password("wrong-password", stored)


def test_verify_reads_cost_from_the_hash_string():
    """n/r/p 写在哈希串里。校验必须用串里的参数，不能忽略前缀、写死 16384。"""
    salt = b"0123456789abcdef"
    n, r, p = 1024, 8, 1
    dk = hashlib.scrypt(b"correct-horse", salt=salt, n=n, r=r, p=p, dklen=32)
    stored = f"scrypt${n}${r}${p}${_b64(salt)}${_b64(dk)}"
    assert verify_password("correct-horse", stored)
    assert not verify_password("nope-nope-nope", stored)


def test_verify_rejects_malformed_hash():
    assert not verify_password("correct-horse", "not-a-hash")
    assert not verify_password("correct-horse", "scrypt$nope")


def test_missing_user_still_hashes(monkeypatch):
    calls = []
    real = verify_password

    def wrapped(password, stored):
        calls.append(stored)
        return real(password, stored)

    monkeypatch.setattr("app.auth.verify_password", wrapped)
    assert password_ok("whatever-password", None) is False
    assert calls == [DUMMY_PASSWORD_HASH]


def test_normalize_username_folds_case_and_rejects_bad():
    assert normalize_username("  HuaQing ") == "huaqing"
    assert normalize_username("ab1") == "ab1"
    assert normalize_username("a" * 32) 
    with pytest.raises(ValueError, match="登录名需为"):
        normalize_username("")
    with pytest.raises(ValueError, match="登录名需为"):
        normalize_username("ab")
    with pytest.raises(ValueError, match="登录名需为"):
        normalize_username("a" * 33)
    with pytest.raises(ValueError, match="登录名需为"):
        normalize_username("-abc")
    with pytest.raises(ValueError, match="登录名需为"):
        normalize_username("has space")


def test_role_at_least():
    assert role_at_least("owner", "editor")
    assert role_at_least("owner", "viewer")
    assert role_at_least("editor", "editor")
    assert not role_at_least("viewer", "editor")
    assert not role_at_least(None, "viewer")
    assert not role_at_least("stranger", "viewer")


def test_safe_next():
    assert safe_next("/board/1") == "/board/1"
    assert safe_next("/board/1/node/2") == "/board/1/node/2"
    assert safe_next("/board/1?x=2") == "/board/1?x=2"
    assert safe_next("//evil") == "/"
    assert safe_next("https://evil") == "/"
    assert safe_next("https://evil.example") == "/"
    assert safe_next("/\\evil") == "/"
    assert safe_next(None) == "/"
    assert safe_next("") == "/"
    assert safe_next("board/1") == "/"

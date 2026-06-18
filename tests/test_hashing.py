"""节点哈希纯逻辑测试（零 Web/DB 依赖）。"""
from app import hashing


def test_node_hash_is_40_hex_and_deterministic():
    h1 = hashing.node_hash(7)
    h2 = hashing.node_hash(7)
    assert h1 == h2
    assert len(h1) == 40
    assert all(c in "0123456789abcdef" for c in h1)


def test_hard_change_hash_differs_from_node_for_same_id():
    """同一个数字 id，节点哈希与硬更改哈希必须不同（带类型前缀）。"""
    assert hashing.node_hash(3) != hashing.hard_change_hash(3)


def test_different_ids_yield_different_hashes():
    assert hashing.node_hash(1) != hashing.node_hash(2)
    assert hashing.hard_change_hash(1) != hashing.hard_change_hash(2)


def test_short_is_prefix_of_full():
    full = hashing.node_hash(42)
    short = hashing.node_short(42)
    assert len(short) == hashing.SHORT_LEN
    assert full.startswith(short)


def test_short_helper_matches_full_prefix():
    full = hashing.hard_change_hash(99)
    assert hashing.hard_change_short(99) == full[: hashing.SHORT_LEN]

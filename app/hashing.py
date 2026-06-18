"""节点哈希纯逻辑：为每个 BOM 节点 / 硬更改算一个稳定的、类 git 的哈希。

哈希基于稳定的 surrogate key（节点 id / 硬更改 id）而非 BOM 内容——历史编辑会改
内容但 id 不变，这样哈希才能像「指代某次提交」的永久引用一样稳定可分享。
类型前缀（node:/hard:）保证两类对象即使 id 相同也不会撞哈希。
长哈希为完整 40 位 sha1，短哈希取前 SHORT_LEN 位（类比 git 的短哈希）。
"""
import hashlib

SHORT_LEN = 8


def _full(kind: str, ident: int) -> str:
    return hashlib.sha1(f"{kind}:{ident}".encode()).hexdigest()


def node_hash(node_id: int) -> str:
    """节点（BOM 变更）的完整哈希。"""
    return _full("node", node_id)


def hard_change_hash(hc_id: int) -> str:
    """硬更改的完整哈希。"""
    return _full("hard", hc_id)


def short(full: str) -> str:
    """取完整哈希的短形式（前 SHORT_LEN 位）。"""
    return full[:SHORT_LEN]


def node_short(node_id: int) -> str:
    return short(node_hash(node_id))


def hard_change_short(hc_id: int) -> str:
    return short(hard_change_hash(hc_id))

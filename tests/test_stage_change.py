"""插入页暂存条目的换算：把「新值」换算成相对父节点的 op / 是否该留痕。

插入页的改动暂存在浏览器里，保存时整批写成新节点的 changeset——changeset 是
**相对父节点**的，所以 op 必须按父节点算，与「相对当前屏幕」无关；值回到父节点
原样的条目一律不留（否则就是一条改前改后相同的空转记录）。

单条编辑（/insert/check）与批量 CSV 导入用的是同一套判定，故抽成纯逻辑。
"""
from app.bom_engine import stage_change


PARENT = {"R1": "10k", "C1": "100nF"}


def test_new_reference_is_add():
    assert stage_change(PARENT, "D9", "1uF") == ("set", "add", "1uF")


def test_existing_reference_with_new_value_is_modify():
    assert stage_change(PARENT, "R1", "47k") == ("set", "modify", "47k")


def test_existing_reference_back_to_parent_value_drops():
    assert stage_change(PARENT, "R1", "10k")[0] == "drop"


def test_removing_an_existing_reference_is_remove():
    assert stage_change(PARENT, "R1", None) == ("set", "remove", None)


def test_removing_a_reference_absent_upstream_drops():
    """本页刚 add 出来的位号又被设为不贴 → 两步抵消，落库不该留痕。"""
    assert stage_change(PARENT, "D9", None)[0] == "drop"


def test_drop_keeps_reference_reported():
    """drop 也要带回位号——调用方据此把这条从暂存里删掉。"""
    action, op, part = stage_change(PARENT, "R1", "10k")
    assert action == "drop"
    assert (op, part) == ("modify", "10k")


def test_parent_bom_is_not_mutated():
    before = dict(PARENT)
    stage_change(PARENT, "D9", "1uF")
    stage_change(PARENT, "R1", None)
    assert PARENT == before

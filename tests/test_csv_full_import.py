"""issue #129：全量模式导入——把完整目标 BOM 求差为修改清单（纯逻辑）。"""
from app.csv_import import PlannedChange, plan_full_changes

CUR = {"R1": "10k", "C1": "100nF"}


def test_diff_covers_add_modify_remove_sorted():
    # target：R1 改值、C1 消失、R9 新增
    changes, problems = plan_full_changes(CUR, {"R1": "47k", "R9": "1uF"})
    assert problems == []
    assert changes == [
        PlannedChange("C1", "remove", None),
        PlannedChange("R1", "modify", "47k"),
        PlannedChange("R9", "add", "1uF"),
    ]


def test_identical_target_yields_no_changes():
    changes, problems = plan_full_changes(CUR, {"R1": "10k", "C1": "100nF"})
    assert changes == [] and problems == []


def test_same_part_skipped_only_diff_kept():
    changes, _ = plan_full_changes(CUR, {"R1": "10k", "C1": "220nF"})
    assert changes == [PlannedChange("C1", "modify", "220nF")]


def test_empty_target_removes_everything():
    changes, problems = plan_full_changes(CUR, {})
    assert problems == []
    assert changes == [
        PlannedChange("C1", "remove", None),
        PlannedChange("R1", "remove", None),
    ]


def test_empty_current_adds_everything():
    changes, _ = plan_full_changes({}, {"R1": "10k"})
    assert changes == [PlannedChange("R1", "add", "10k")]


def test_does_not_mutate_inputs():
    cur, tgt = dict(CUR), {"R1": "47k"}
    plan_full_changes(cur, tgt)
    assert cur == CUR and tgt == {"R1": "47k"}

from app.compare import diff_boms, hard_changes_between


def _hc(i, ts):
    return {"id": i, "occurred_at": ts, "title": f"hc{i}"}


def test_between_inclusive_and_sorted():
    hcs = [
        _hc(1, "2026-06-10T00:00:00+00:00"),  # 早于区间
        _hc(2, "2026-06-12T06:30:00+00:00"),  # 区间内
        _hc(3, "2026-06-13T01:10:00+00:00"),  # 恰为右端点
        _hc(4, "2026-06-20T00:00:00+00:00"),  # 晚于区间
    ]
    lo = "2026-06-11T00:00:00+00:00"
    hi = "2026-06-13T01:10:00+00:00"
    got = hard_changes_between(hcs, lo, hi)
    assert [h["id"] for h in got] == [2, 3]


def test_between_symmetric_lo_hi_order():
    hcs = [_hc(2, "2026-06-12T06:30:00+00:00")]
    a = "2026-06-11T00:00:00+00:00"
    b = "2026-06-13T00:00:00+00:00"
    assert hard_changes_between(hcs, a, b) == hard_changes_between(hcs, b, a)


def test_between_empty_when_none_in_range():
    hcs = [_hc(1, "2026-06-01T00:00:00+00:00")]
    assert hard_changes_between(hcs, "2026-06-10T00:00:00+00:00",
                                "2026-06-12T00:00:00+00:00") == []


def test_diff_add_modify_remove_same_sorted():
    left = {"R1": "10k", "R5": "10k", "D2": "LED红"}
    right = {"R1": "10k", "R5": "4.7k", "C12": "100nF"}
    rows = diff_boms(left, right)
    assert rows == [
        {"reference": "C12", "left": None, "right": "100nF", "kind": "add"},
        {"reference": "D2", "left": "LED红", "right": None, "kind": "remove"},
        {"reference": "R1", "left": "10k", "right": "10k", "kind": "same"},
        {"reference": "R5", "left": "10k", "right": "4.7k", "kind": "modify"},
    ]


def test_diff_identical_all_same():
    bom = {"R1": "10k", "C1": "100nF"}
    rows = diff_boms(bom, dict(bom))
    assert all(r["kind"] == "same" for r in rows)
    assert [r["reference"] for r in rows] == ["C1", "R1"]


def test_diff_empty_both():
    assert diff_boms({}, {}) == []

from app.compare import diff_boms


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

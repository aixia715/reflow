from app.bom_engine import fold_bom, resolve_reference


def test_inherits_initial_when_no_changes():
    initial = {"R1": "10k"}
    chain = [[], [], []]
    assert fold_bom(initial, chain) == {"R1": "10k"}
    assert resolve_reference(initial, chain, "R1") == "10k"


def test_modify_overrides_inherited_value():
    initial = {"R1": "10k"}
    chain = [[], [{"reference": "R1", "op": "modify", "part": "47k"}]]
    assert fold_bom(initial, chain)["R1"] == "47k"
    assert resolve_reference(initial, chain, "R1") == "47k"


def test_add_introduces_new_reference():
    chain = [[], [{"reference": "C9", "op": "add", "part": "100nF"}]]
    assert fold_bom({}, chain) == {"C9": "100nF"}


def test_remove_means_not_placed():
    initial = {"R1": "10k"}
    chain = [[], [{"reference": "R1", "op": "remove", "part": None}]]
    assert "R1" not in fold_bom(initial, chain)
    assert resolve_reference(initial, chain, "R1") is None


def test_latest_explicit_op_wins_along_chain():
    initial = {"R1": "10k"}
    chain = [
        [],
        [{"reference": "R1", "op": "modify", "part": "47k"}],
        [{"reference": "R1", "op": "modify", "part": "22k"}],
    ]
    assert resolve_reference(initial, chain, "R1") == "22k"

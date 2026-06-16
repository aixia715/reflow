import pytest
from app.db import connect, init_db
from app import models
from app.csv_import import CsvEntry


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    return c


def _seed(conn, name="MB", pcb="v1", bom="bomA", uid="SN1"):
    models.create_bom_version(conn, name, pcb, bom, [CsvEntry("R1", "10k")])
    return models.create_board(conn, name, pcb, bom, uid)


# ── rename_board_name ───────────────────────────────────────────────

def test_rename_board_name_updates_both_tables(conn):
    _seed(conn, "Old")
    models.rename_board_name(conn, "Old", "New")
    assert models.list_boards(conn, "New", "v1", "bomA")
    assert models.get_initial_bom(conn, "New", "v1", "bomA") == {"R1": "10k"}
    assert not models.list_boards(conn, "Old", "v1", "bomA")


def test_rename_board_name_cascades_across_versions(conn):
    _seed(conn, "Old", "v1", "bomA", "SN1")
    _seed(conn, "Old", "v2", "bomB", "SN2")
    models.rename_board_name(conn, "Old", "New")
    assert models.list_boards(conn, "New", "v1", "bomA")
    assert models.list_boards(conn, "New", "v2", "bomB")


def test_rename_board_name_conflict_rejected(conn):
    _seed(conn, "A")
    _seed(conn, "B")
    with pytest.raises(ValueError, match="已存在"):
        models.rename_board_name(conn, "A", "B")
    assert models.list_boards(conn, "A", "v1", "bomA")


def test_rename_board_name_noop_when_unchanged(conn):
    _seed(conn, "Same")
    models.rename_board_name(conn, "Same", "Same")
    assert models.list_boards(conn, "Same", "v1", "bomA")


# ── rename_pcb_version ──────────────────────────────────────────────

def test_rename_pcb_version_cascades_under_board(conn):
    _seed(conn, "MB", "p1", "bomA", "SN1")
    _seed(conn, "MB", "p1", "bomB", "SN2")
    models.rename_pcb_version(conn, "MB", "p1", "p2")
    assert models.list_boards(conn, "MB", "p2", "bomA")
    assert models.list_boards(conn, "MB", "p2", "bomB")
    assert models.get_initial_bom(conn, "MB", "p2", "bomA") == {"R1": "10k"}


def test_rename_pcb_version_conflict_rejected(conn):
    _seed(conn, "MB", "p1", "bomA", "SN1")
    _seed(conn, "MB", "p2", "bomA", "SN2")
    with pytest.raises(ValueError, match="已存在"):
        models.rename_pcb_version(conn, "MB", "p1", "p2")


# ── rename_bom_version ──────────────────────────────────────────────

def test_rename_bom_version_updates_triple(conn):
    _seed(conn, "MB", "v1", "b1", "SN1")
    models.rename_bom_version(conn, "MB", "v1", "b1", "b2")
    assert models.list_boards(conn, "MB", "v1", "b2")
    assert models.get_initial_bom(conn, "MB", "v1", "b2") == {"R1": "10k"}
    assert not models.get_initial_bom(conn, "MB", "v1", "b1")


def test_rename_bom_version_conflict_rejected(conn):
    _seed(conn, "MB", "v1", "b1", "SN1")
    _seed(conn, "MB", "v1", "b2", "SN2")
    with pytest.raises(ValueError, match="已存在"):
        models.rename_bom_version(conn, "MB", "v1", "b1", "b2")


# ── rename_board_uid ────────────────────────────────────────────────

def test_rename_board_uid_updates_row(conn):
    bid = _seed(conn, "MB", "v1", "bomA", "SN1")
    models.rename_board_uid(conn, bid, "SN9")
    assert models.get_board(conn, bid)["board_uid"] == "SN9"


def test_rename_board_uid_conflict_within_version_rejected(conn):
    bid = _seed(conn, "MB", "v1", "bomA", "SN1")
    # 同一 BOM 版本下第二块板，不需再建 BOM 版本
    models.create_board(conn, "MB", "v1", "bomA", "SN2")
    with pytest.raises(ValueError, match="已存在"):
        models.rename_board_uid(conn, bid, "SN2")


def test_rename_board_uid_same_uid_other_version_ok(conn):
    bid = _seed(conn, "MB", "v1", "bomA", "SN1")
    _seed(conn, "MB", "v1", "bomB", "SN9")
    models.rename_board_uid(conn, bid, "SN9")
    assert models.get_board(conn, bid)["board_uid"] == "SN9"

import pytest
from app.db import connect, init_db
from app import models, audit
from app.csv_import import CsvEntry


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    models.create_bom_version(c, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    bid = models.create_board(c, "B", "v1", "bomA", "3")
    return c, bid


def test_append_only_never_overwrites(conn):
    c, bid = conn
    node_id = models.list_nodes(c, bid)[1]["id"]
    for new in ("47k", "22k", "33k"):
        audit.record_edit(c, node_id, "R1", old_part="10k", new_part=new,
                          op="modify", source="direct")
    rows = audit.list_log(c, node_id)
    assert len(rows) == 3
    assert [r["new_part"] for r in rows] == ["47k", "22k", "33k"]


def test_source_marking(conn):
    c, bid = conn
    node_id = models.list_nodes(c, bid)[1]["id"]
    audit.record_edit(c, node_id, "R1", "10k", "47k", "modify", "direct")
    audit.record_edit(c, node_id, "R1", "47k", "22k", "modify", "propagated")
    rows = audit.list_log(c, node_id)
    assert {r["source"] for r in rows} == {"direct", "propagated"}

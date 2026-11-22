"""Synthetic evidence in a disposable database only."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_execution_evidence_postgres import connection, engine, TEST_URL
from test_scalp_order_observation import args
from scalp_order_observation import observe, persist
from pathlib import Path

pytestmark = pytest.mark.skipif(not TEST_URL, reason="disposable PostgreSQL required")

def test_scalp_namespace_append_only(connection):
    c = connection
    c.execution_options(isolation_level="AUTOCOMMIT")
    c.exec_driver_sql((Path(__file__).resolve().parents[1] /
                      "database/migrations/027_scalp_order_observations.sql").read_text())
    c.commit()
    c.execution_options(isolation_level="READ COMMITTED")
    events = []
    observe(lambda: {}, sink=events.append, **args())
    with c.begin():
        for event in events:
            persist(event, c)
    assert c.execute(text("SELECT count(*) FROM scalp_order_observations")).scalar() == 2
    assert c.execute(text("SELECT count(*) FROM signals")).scalar() == 0
    c.commit()
    for sql in ("DELETE FROM scalp_order_observations",
                "UPDATE scalp_order_observations SET training_eligible=true"):
        with pytest.raises(DBAPIError):
            with c.begin():
                c.execute(text(sql))
    with pytest.raises(DBAPIError):
        with c.begin():
            persist(events[0], c)
    assert c.execute(text("SELECT bool_and(NOT training_eligible) FROM scalp_order_observations")).scalar()
    c.commit()
    with pytest.raises(ValueError, match="INVALID_SCALP_EVIDENCE"):
        with c.begin():
            persist({**events[0], "network": "mainnet"}, c)
    with pytest.raises(ValueError, match="INVALID_SCALP_EVIDENCE"):
        with c.begin():
            persist({**events[0], "training_eligible": True}, c)

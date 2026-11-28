"""Synthetic evidence in a disposable database only."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_execution_evidence_postgres import connection, engine, TEST_URL
from test_scalp_order_observation import args
from scalp_order_observation import observe, persist
from pathlib import Path

pytestmark = pytest.mark.skipif(not TEST_URL, reason="disposable PostgreSQL required")

@pytest.fixture
def scalp_connection(connection):
    c = connection
    c.execution_options(isolation_level="AUTOCOMMIT")
    c.exec_driver_sql((Path(__file__).resolve().parents[1] /
                      "database/migrations/027_scalp_order_observations.sql").read_text())
    c.commit()
    c.execution_options(isolation_level="READ COMMITTED")
    return c

def test_scalp_namespace_append_only(scalp_connection):
    c = scalp_connection
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


@pytest.mark.parametrize("fail_batch", [False, True])
def test_buffer_flush_is_atomic_after_protection(scalp_connection, monkeypatch, fail_batch):
    """A later row failure must not leave an opening intent committed alone."""
    import hashlib
    import hl_account
    import scalp_order_observation as observation

    c = scalp_connection
    address = "0x" + "1" * 40
    monkeypatch.setattr(hl_account, "account_address", lambda: address)
    monkeypatch.setattr(hl_account, "_env", lambda: "testnet")
    order = []
    scope = args() | {"account_hash": hashlib.sha256(address.encode()).hexdigest()}

    def wrappers(**kw):
        return lambda: observe(lambda: {}, sink=kw["sink"], **scope)

    monkeypatch.setattr(observation, "order_wrappers", wrappers)

    def flush(batch):
        assert order == ["orders", "protection_complete"]
        order.append("flush")
        with c.begin():
            for index, event in enumerate(batch):
                # Trigger a database constraint failure on the second insert.
                persist(event | ({"phase": "INVALID"} if fail_batch and index == 1 else {}), c)

    capture = observation.BufferedCapture(flush=flush, account_hash=scope["account_hash"])

    def workflow(wrapper):
        order.append("orders")
        wrapper()
        order.append("protection_complete")
        return {"protection": "synthetic_complete"}

    result = capture.run(workflow, account_state=dict(
        configured=True, env="testnet", address=address))
    assert result == {"protection": "synthetic_complete"}
    assert order == ["orders", "protection_complete", "flush"]
    assert capture.flush_status == ("FAILED" if fail_batch else "FLUSHED")
    assert c.execute(text("SELECT count(*) FROM scalp_order_observations")).scalar() == (0 if fail_batch else 2)
    c.commit()

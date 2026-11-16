import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from test_hl_autoexec import _row, _table, _cfg
import published_order_recorder as recorder
import hl_autoexec as ax


def test_one_signal_only_and_network_boundary(monkeypatch):
    addr = "0x" + "ab"*20
    sid = str(uuid4())
    now = datetime.now(timezone.utc)
    scope = dict(signal_id=sid, account_hash=hashlib.sha256(addr.encode()).hexdigest(),
                 starts_at=(now-timedelta(minutes=1)).isoformat(),
                 ends_at=(now+timedelta(minutes=10)).isoformat())
    monkeypatch.setenv("HL_TESTNET_CAPTURE_SCOPE", json.dumps(scope))
    monkeypatch.setenv("SIGNAL_ENVIRONMENT", "production")
    acct = dict(env="testnet", address=addr)
    assert recorder.prepare(dict(acct, env="mainnet"), addr, []) is None
    assert recorder.prepare(acct, "0x"+"cd"*20, []) is None
    capture = recorder.prepare(acct, addr, [])
    signals = [dict(ax.to_signal(_row("BTC", 90)), id=i) for i in (sid,str(uuid4()))]
    for network in ("mainnet", "testnet"):
        observed = []
        def opening(sig, **kw):
            observed.append(kw.get("evidence_observer"))
            return dict(ok=True, coin="BTC", size=1)
        def exits(*args, **kw):
            observed.append(kw.get("evidence_observer"))
            if kw.get("evidence_observer"):
                assert kw["evidence_signal"]["id"] == sid
            return {}
        ax.execute(signals, account_state={}, table=_table(), cfg=_cfg(),
                   env=network, open_fn=opening, exit_fn=exits, capture=capture)
        expected = capture[1] if network == "testnet" else None
        assert observed == [expected, expected, None, None]

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import historical_pilot as h

START = h.utc_ms("2026-08-01T00:00:00Z")
END = START + 14*24*h.HOUR
NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def candle(ts):
    return {"timestamp": ts, "open": 100., "high": 102., "low": 98.,
            "close": 101., "volume": 50.}


def page(symbol, cursor):
    return [candle(ts) for ts in range(cursor-100*h.HOUR, cursor, h.HOUR)]


def test_download_complete_hashes_resume_without_network(tmp_path):
    path = tmp_path/"archive.json"
    out = h.download(path, ["BTC", "ETH"], START, END, page=page, now=NOW)
    assert out["complete"]
    expected = (END-START+h.WARMUP+h.TAIL)//h.HOUR
    assert len(out["series"]["BTC"]["candles"]) == expected
    assert h.load_archive(path) == out
    def forbidden(*args):
        raise AssertionError("must reuse completed history")
    assert h.download(path, ["BTC", "ETH"], START, END, page=forbidden, now=NOW)["complete"]


def test_resume_partial_checkpoint(tmp_path):
    path = tmp_path/"archive.json"
    with pytest.raises(ValueError, match="Incomplete"):
        h.download(path, ["BTC"], START, END, page=page, now=NOW, max_pages=1)
    assert not json.loads(path.read_text())["complete"]
    cursors = []
    def record(symbol, cursor):
        cursors.append(cursor)
        return page(symbol, cursor)
    assert h.download(path, ["BTC"], START, END, page=record, now=NOW)["complete"]
    assert cursors[0] == END+h.TAIL-100*h.HOUR


def test_gap_and_cursor_stall_fail(tmp_path):
    def gap(symbol, cursor):
        return page(symbol, cursor)[:-1]
    with pytest.raises(ValueError, match="Incomplete"):
        h.download(tmp_path/"a", ["BTC"], START, END, page=gap, now=NOW)
    with pytest.raises(ValueError, match="progress"):
        h.download(tmp_path/"b", ["BTC"], START, END,
                   page=lambda s,c: [candle(c)], now=NOW)


@pytest.mark.parametrize("change", [lambda r: r.__setitem__(8,"0"),
    lambda r: r.__setitem__(2,"NaN"), lambda r: r.__setitem__(3,"200")])
def test_unconfirmed_or_invalid_provider_bars_rejected(change):
    row = [str(START),"100","102","98","101","50","50","5000","1"]
    change(row)
    with pytest.raises(ValueError):
        h.parse_page({"code":"0", "data":[row]})


def test_parse_sort_duplicate_and_provider_error():
    row = [str(START),"100","102","98","101","50","50","5000","1"]
    assert h.parse_page({"code":"0", "data":[row]}) == [candle(START)]
    with pytest.raises(ValueError):
        h.parse_page({"code":"0", "data":[row,row]})
    with pytest.raises(ValueError):
        h.parse_page({"code":"500", "data":[]})


def test_bad_ranges_and_future_tail_rejected(tmp_path):
    for start,end in [(START+1,END),(END,START),(START,START+32*24*h.HOUR)]:
        with pytest.raises(ValueError):
            h.download(tmp_path/"a",["BTC"],start,end,page=page,now=NOW)
    with pytest.raises(ValueError, match="96 hours"):
        h.download(tmp_path/"a",["BTC"],START,END,page=page,
                   now=datetime.fromtimestamp(END/1000,tz=timezone.utc))
    with pytest.raises(ValueError):
        h.utc_ms("2026-08-01")


def test_hash_mismatch_and_incomplete_archive_rejected(tmp_path):
    path=tmp_path/"a"
    out=h.download(path,["BTC"],START,END,page=page,now=NOW)
    out["series"]["BTC"]["candles"][0]["open"] = 99
    h.save(path,out)
    with pytest.raises(ValueError,match="hash"):
        h.load_archive(path)
    with pytest.raises(ValueError,match="hash"):
        h.download(path,["BTC"],START,END,page=page,now=NOW)


def test_aggregation_and_partial_group():
    rows=[candle(START+i*h.HOUR) for i in range(4)]
    rows[-1]["close"]=102
    result=h.aggregate_hours(rows,4)
    assert result[0]["close"] == 102 and result[0]["volume"] == 200
    with pytest.raises(ValueError):
        h.aggregate_hours(rows[:-1],4)


def test_weighted_rr_both_directions_not_tp2_only():
    assert h.weighted_target_rr({"entry":100,"sl":95,"direction":"LONG",
                                "tp_targets":[105,110,115]}) == pytest.approx(1.7)
    assert h.weighted_target_rr({"entry":100,"sl":105,"direction":"SHORT",
                                "tp_targets":[95,90,85]}) == pytest.approx(1.7)


def test_sizing_uses_stop_risk_caps_notional_excludes_unfinished():
    base={"filled":True,"closed_at":10,"return_pct":2.,"risk_pct":2.}
    r=h.summarize([base,{**base,"closed_at":None,"return_pct":999}])
    assert r["mean_capped_risk_contribution_pct"] == .5
    assert r["closed_filled"] == 1 and r["unfinished"] == 1
    assert h.summarize([{**base,"risk_pct":.1}])["mean_capped_risk_contribution_pct"] == 2


def test_pilot_uses_shared_engine_and_purges_boundary(monkeypatch,tmp_path):
    import portfolio_backtest as pbt
    archive=h.download(tmp_path/"a",["BTC","ETH"],START,END,page=page,now=NOW)
    split=START+8*24*h.HOUR
    rec={"symbol":"ETH","direction":"LONG","entry":100,"sl":95,
         "tp_targets":[105,110,115],"strength":70,"avg_tf_strength":70,
         "quality_score":70,"rr_ratio":2,"rank":1}
    seen={}
    def generate(market,**kw):
        seen.update(kw)
        assert max(c["timestamp"] for c in market["ETH"]["1H"]) == END-h.HOUR
        return {"parity":{"result_kind":"subset_price_only"},"published":[
            {**rec,"slot_ms":s,"slot":str(s),"id":str(s)}
            for s in (START,split-h.HOUR*4,split,END)]}
    monkeypatch.setattr(pbt,"replay",generate)
    out=h.replay_pilot(archive,split)
    assert seen["execute"] is False
    assert out["discovery"]["baseline"]["published"] == 1
    assert out["holdout"]["baseline"]["published"] == 1
    assert out["purged_recommendations"] == 1
    assert len(out["trades"]) == 3
    assert all(t["cost_pct"]>0 for t in out["trades"])


def test_http_retry_is_bounded_and_redirects_rejected():
    calls=[]
    class Response:
        status_code=503
    def get(*args,**kw):
        calls.append(kw)
        return Response()
    with pytest.raises(h.requests.RequestException):
        h.get_page("BTC",START,get=get,sleep=lambda s:None)
    assert len(calls)==3 and calls[0]["allow_redirects"] is False
    Response.status_code=302
    calls.clear()
    with pytest.raises(ValueError,match="302"):
        h.get_page("BTC",START,get=get,sleep=lambda s:None)
    assert len(calls)==1


def test_long_history_requires_explicit_opt_in(tmp_path):
    end=START+90*24*h.HOUR
    now=datetime(2027,1,1,tzinfo=timezone.utc)
    with pytest.raises(ValueError,match="31 days"):
        h.download(tmp_path/"a",["BTC"],START,end,page=page,now=now)
    out=h.download(tmp_path/"a",["BTC"],START,end,page=page,now=now,max_days=90)
    assert out["complete"]
    assert len(out["series"]["BTC"]["candles"]) == (90+40+4)*24
    assert h.load_archive(tmp_path/"a")["complete"]


@pytest.mark.parametrize("limit", [0,367,True,1.5])
def test_invalid_range_ceiling(tmp_path,limit):
    with pytest.raises(ValueError,match="max_days"):
        h.download(tmp_path/"a",["BTC"],START,END,page=page,now=NOW,max_days=limit)


@pytest.mark.parametrize("field,value", [("source","binance"),
    ("history_start_ms",START), ("history_end_ms",END),
    ("symbols",["ETH"]), ("symbols",["BTC","BTC"])])
def test_tampered_archive_metadata_rejected(tmp_path,field,value):
    path=tmp_path/"a"
    out=h.download(path,["BTC"],START,END,page=page,now=NOW)
    out["spec"][field]=value
    h.save(path,out)
    with pytest.raises(ValueError,match="Invalid archive"):
        h.load_archive(path)

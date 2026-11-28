import rsi3_forward as r3

D = 86_400_000


def day(i, c):
    return {"timestamp": i * D, "open": c, "high": c, "low": c, "close": c, "volume": 1}


def series(tail):
    closes = [100 + i * 0.5 for i in range(260)] + list(tail)
    return [day(i, c) for i, c in enumerate(closes)]


DIP = [229, 226, 222, 217, 211]


def test_entry_on_the_dip_then_exit_on_the_bounce():
    book = []
    got = r3.step(book, "BTC", series(DIP))
    assert len(got["opened"]) == 1 and book[0]["state"] == "open"
    assert r3.step(book, "BTC", series(DIP))["opened"] == []          # same day: no second trade
    out = r3.step(book, "BTC", series(DIP + [216, 222, 228, 234]))
    assert len(out["closed"]) == 1 and book[0]["state"] == "closed" and book[0]["ret"] > 0
    s = r3.summary(book)
    assert s["closed"] == 1 and s["to_check"] == r3.VERDICT_N - 1


def test_no_entry_without_the_dip_or_below_the_200_day():
    assert r3.step([], "BTC", series([231, 232]))["opened"] == []
    down = [day(i, 300 - i * 0.5) for i in range(260)] + [day(260 + k, c) for k, c in enumerate([168, 165, 161, 156, 150])]
    assert r3.step([], "BTC", down)["opened"] == []


def test_short_history_does_nothing():
    assert r3.read([day(i, 100) for i in range(50)]) is None


def test_notes():
    book = []
    r3.step(book, "ETH", series(DIP))
    assert "Triple RSI" in r3.note("opened", book[0], r3.summary(book))


def test_app_daily_pass_books_and_alerts(monkeypatch):
    import app
    store = {}
    import kv
    monkeypatch.setattr(kv, "get_value", lambda k: store.get(k))
    monkeypatch.setattr(kv, "set_value", lambda k, v, ttl_seconds=None: store.__setitem__(k, v))
    monkeypatch.setattr(kv, "claim", lambda k, ttl_seconds=None: True)
    sent = []
    monkeypatch.setattr(app, "_scalp_private", sent.append)
    res = app._rsi3_daily(fetch_fn=lambda sym: series(DIP))
    assert res["ran"] and res["opened"] >= 1 and sent
    assert app._rsi3_view()["summary"]["open"] == res["opened"]

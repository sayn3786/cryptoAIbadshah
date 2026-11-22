import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import reversal_1d_test as rv                                        # noqa: E402
import strength_tier_test as st                                      # noqa: E402


def test_books():
    assert st.books_for(61.9) == []
    assert st.books_for(62) == ["62-68", "62+"]
    assert st.books_for(68.9) == ["62-68", "62+"]
    assert st.books_for(69) == ["69+", "62+"]


def test_scan_and_era():
    D = rv.DAY_MS
    daily = [{"timestamp": i * D, "open": 100, "high": 104, "low": 99, "close": 100}
             for i in range(rv.LOOKBACK + 50)]
    sigs = iter([{"direction": "LONG", "strength": 75 if k % 2 else 64} for k in range(1000)])
    evs = st.scan(daily, "X", {}, signal_fn=lambda w, s, f: next(sigs))
    assert {e["book"] for e in evs} == {"62-68", "69+", "62+"}

    def ev(b, win, n, r):
        return [{"book": b, "x": {"result": "target" if k < win else "stop", "r": r if k < win else -1.0}}
                for k in range(n)]
    good = st.era(ev("69+", 70, 100, 1.0) + ev("62-68", 52, 100, 1.0))
    assert good["passes"]
    weak = st.era(ev("69+", 54, 100, 1.0) + ev("62-68", 54, 100, 1.0))
    assert not weak["passes"]

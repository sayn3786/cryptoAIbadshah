from typing import List, Dict, Tuple

TOL = 0.10  # 10% tolerance for Fibonacci ratios


def _ratio(a: float, b: float) -> float:
    return abs(a) / abs(b) if b != 0 else 0.0


def _near(r: float, target: float, tol: float = TOL) -> bool:
    return abs(r - target) <= tol


def _in_range(r: float, lo: float, hi: float) -> bool:
    return lo <= r <= hi


# Each pattern: AB_XA, BC_AB, CD_BC, D_XA — None means range check
HARMONIC_SPECS: Dict[str, Dict] = {
    "Gartley": {
        "AB_XA": (0.618, None),
        "BC_AB": (0.382, 0.886),
        "CD_BC": (1.272, 1.618),
        "D_XA": (0.786, None),
    },
    "Butterfly": {
        "AB_XA": (0.786, None),
        "BC_AB": (0.382, 0.886),
        "CD_BC": (1.618, 2.618),
        "D_XA": (1.272, 1.618),
    },
    "Bat": {
        "AB_XA": (0.382, 0.500),
        "BC_AB": (0.382, 0.886),
        "CD_BC": (1.618, 2.618),
        "D_XA": (0.886, None),
    },
    "Crab": {
        "AB_XA": (0.382, 0.618),
        "BC_AB": (0.382, 0.886),
        "CD_BC": (2.618, 3.618),
        "D_XA": (1.618, None),
    },
    "Shark": {
        "AB_XA": (0.446, 0.618),
        "BC_AB": (1.130, 1.618),
        "CD_BC": (0.500, 0.886),
        "D_XA": (0.886, 1.130),
    },
}


def _check(ratio: float, spec: Tuple) -> bool:
    lo, hi = spec
    if hi is None:
        return _near(ratio, lo)
    return _in_range(ratio, lo, hi)


def detect_harmonics(
    pivot_highs: List[Dict],
    pivot_lows: List[Dict],
    current_price: float,
) -> List[Dict]:
    all_pivots = sorted(
        [{"t": "H", **p} for p in pivot_highs] + [{"t": "L", **p} for p in pivot_lows],
        key=lambda p: p["index"],
    )

    if len(all_pivots) < 5:
        return []

    found = []

    for i in range(len(all_pivots) - 4):
        pts = all_pivots[i : i + 5]
        prices = [p["price"] for p in pts]
        X, A, B, C, D = prices

        XA = A - X
        AB = B - A
        BC = C - B
        CD = D - C

        # Legs must strictly alternate direction
        if not (XA * AB < 0 and AB * BC < 0 and BC * CD < 0):
            continue

        AB_XA = _ratio(AB, XA)
        BC_AB = _ratio(BC, AB)
        CD_BC = _ratio(CD, BC)
        D_XA_ratio = _ratio(D - X, XA)

        for name, spec in HARMONIC_SPECS.items():
            if (
                _check(AB_XA, spec["AB_XA"])
                and _check(BC_AB, spec["BC_AB"])
                and _check(CD_BC, spec["CD_BC"])
                and _check(D_XA_ratio, spec["D_XA"])
            ):
                direction = "bullish" if XA < 0 else "bearish"
                prz_lo = D * (1 - TOL / 2)
                prz_hi = D * (1 + TOL / 2)
                at_prz = prz_lo <= current_price <= prz_hi

                found.append(
                    {
                        "pattern": name,
                        "direction": direction,
                        "X": round(X, 6),
                        "A": round(A, 6),
                        "B": round(B, 6),
                        "C": round(C, 6),
                        "D": round(D, 6),
                        "PRZ_low": round(prz_lo, 6),
                        "PRZ_high": round(prz_hi, 6),
                        "at_completion": at_prz,
                        "ratios": {
                            "AB_XA": round(AB_XA, 3),
                            "BC_AB": round(BC_AB, 3),
                            "CD_BC": round(CD_BC, 3),
                            "D_XA": round(D_XA_ratio, 3),
                        },
                        "timestamp": pts[4]["timestamp"],
                    }
                )

    return found[-6:]


def analyze_elliott_wave(
    candles: List[Dict],
    pivot_highs: List[Dict],
    pivot_lows: List[Dict],
) -> Dict:
    all_pivots = sorted(
        [{"type": "H", **p} for p in pivot_highs] + [{"type": "L", **p} for p in pivot_lows],
        key=lambda p: p["index"],
    )

    if len(all_pivots) < 5:
        return {
            "wave_count": "Insufficient data",
            "current_wave": None,
            "bias": "neutral",
            "trend": "neutral",
            "description": "Need more pivot data for wave analysis.",
            "targets": [],
        }

    recent = all_pivots[-12:]
    prices = [p["price"] for p in recent]
    trend = "bullish" if prices[-1] > prices[0] else "bearish"

    # Count swing alternations
    swings = sum(
        1
        for i in range(1, len(recent))
        if recent[i]["type"] != recent[i - 1]["type"]
    )

    _wave_labels = {
        1: ("Wave 1", "Impulse start — early entry for smart money"),
        2: ("Wave 2", "Corrective pullback — watch for reversal"),
        3: ("Wave 3", "Strongest impulse — ideal trend-following entry"),
        4: ("Wave 4", "Consolidation — prepare for Wave 5"),
        5: ("Wave 5", "Final push — consider taking profits"),
        6: ("Wave A", "Correction starts — reduce longs"),
        7: ("Wave B", "Dead-cat bounce — potential short entry"),
        8: ("Wave C", "Final corrective leg — accumulation zone"),
    }

    pos = (swings % 8) + 1
    label, desc = _wave_labels.get(pos, ("Unknown", "Unclear wave structure"))

    # Bias
    bullish_waves = {1, 3, 5, 7} if trend == "bullish" else {2, 4, 6, 8}
    bias = "bullish" if pos in bullish_waves else "bearish"

    # Fibonacci extension targets — based on current market price, not last pivot,
    # so targets are always forward-looking from where price is right now.
    current_price = candles[-1]["close"] if candles else prices[-1]
    targets = []
    if len(prices) >= 2:
        last_swing = min(abs(prices[-1] - prices[-2]), current_price * 0.25)
        mults = [0.618, 1.000, 1.618]
        for m in mults:
            if bias == "bullish":
                t = round(current_price + last_swing * m, 6)
                if t > current_price:
                    targets.append(t)
            else:
                t = round(max(current_price * 0.001, current_price - last_swing * m), 6)
                if t < current_price:
                    targets.append(t)

    return {
        "wave_count": label,
        "current_wave": pos,
        "bias": bias,
        "trend": trend,
        "description": desc,
        "targets": targets,
        "pivot_count": len(all_pivots),
    }

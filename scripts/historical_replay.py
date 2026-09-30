"""Explicit local artifacts only; no credentials or database required."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import historical_pilot as pilot


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("download")
    fetch.add_argument("--symbols", default="BTC,ETH")
    fetch.add_argument("--start", required=True)
    fetch.add_argument("--end", required=True)
    fetch.add_argument("--archive", required=True)
    fetch.add_argument("--max-days", type=int, default=31,
                       help="Explicit range ceiling, 1–366 days; default 31")
    replay = sub.add_parser("replay")
    replay.add_argument("--archive", required=True)
    replay.add_argument("--split", required=True)
    replay.add_argument("--output", required=True)
    replay.add_argument("--fee-bps", type=float, default=6.0)
    replay.add_argument("--slippage-bps", type=float, default=2.0)
    replay.add_argument("--rr-floor", type=float, default=1.0)
    args = parser.parse_args(argv)
    if args.command == "download":
        archive = pilot.download(args.archive, args.symbols.upper().split(","),
                                 pilot.utc_ms(args.start), pilot.utc_ms(args.end),
                                 max_days=args.max_days)
        print(json.dumps({"complete": archive["complete"], "candles": {
            s: len(v["candles"]) for s, v in archive["series"].items()}}))
    else:
        if Path(args.output).resolve() == Path(args.archive).resolve():
            parser.error("Output must not overwrite the input archive")
        report = pilot.replay_pilot(pilot.load_archive(args.archive), pilot.utc_ms(args.split),
            fee_bps=args.fee_bps, slippage_bps=args.slippage_bps, rr_floor=args.rr_floor)
        pilot.save(args.output, report)
        print(json.dumps({k: report[k] for k in ("result_kind", "discovery", "holdout")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

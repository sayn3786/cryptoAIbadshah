#!/usr/bin/env python3
"""Explicit observational collector. Dry-run by default; no order-placement path."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import execution_evidence as evidence


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--network', required=True, choices=('testnet','mainnet'))
    p.add_argument('--start-ms', required=True, type=int)
    p.add_argument('--end-ms', required=True, type=int)
    p.add_argument('--candidates', type=Path, help='JSON list of signal_id, candle_ts, intent, instrument')
    p.add_argument('--write', action='store_true')
    p.add_argument('--account-evidence', action='store_true',
                   help='also observe funding and current first-perp-dex positions; requires migration 021 for writes')
    a = p.parse_args()
    # No implicit dotenv load, migration, scheduling or production enablement.
    if a.write and os.getenv('EXECUTION_EVIDENCE_ENABLED') != 'true':
        p.error('writes require EXECUTION_EVIDENCE_ENABLED=true and migration 020')
    try:
        candidates = json.loads(a.candidates.read_text()) if a.candidates else []
        batch = evidence.collect(network=a.network,
            account=os.getenv('HYPERLIQUID_ACCOUNT_ADDRESS',''),
            start_ms=a.start_ms,end_ms=a.end_ms,candidates=candidates)
        summary = evidence.inventory(batch)
        if a.account_evidence:
            from execution_account_evidence import collect_account_evidence
            batch = collect_account_evidence(batch, os.getenv('HYPERLIQUID_ACCOUNT_ADDRESS',''))
            summary.update(funding_events=len(batch['funding']),
                           position_snapshot_captured=True,
                           position_scope='first_perp_dex', funding_history_complete=False)
        if summary['instrument_mismatches']:
            raise evidence.EvidenceError('FILL_LINK_INSTRUMENT_MISMATCH')
        if a.write:
            from db import session_scope
            with session_scope() as session:
                evidence.persist(batch,session)
                if a.account_evidence:
                    from execution_account_evidence import persist_account_evidence
                    persist_account_evidence(batch, session)
        print(json.dumps({'ok':True,'mode':'write' if a.write else 'dry_run',**summary}))
        return 0
    except Exception:
        # Never expose provider payloads, account identifiers, DSNs or secrets.
        print(json.dumps({'ok':False,'error':'EXECUTION_EVIDENCE_COLLECTION_FAILED'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

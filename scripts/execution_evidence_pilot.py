#!/usr/bin/env python3
"""Foreground, testnet-only 24-hour pilot. Does not install a scheduler."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

INTERVAL = 900
DURATION = 86400
ROOT = Path(__file__).resolve().parents[1]


def run(start, invoke, *, now=time.time, sleep=time.sleep, emit=print):
    """96 bounded windows. Late starts/gaps are reported, never backfilled.

    Failed/ambiguous writes are never automatically retried. Restarting this
    runner is unsupported: operators must reconcile the ledger first.
    """
    failures = 0
    for slot in range(1, 97):
        deadline = start + slot * INTERVAL
        while now() < deadline:
            sleep(min(30, deadline - now()))
        current = now()
        if current > start + DURATION + 60:
            emit(json.dumps({'event': 'expired', 'remaining_slots': 97-slot}))
            return 1
        if current - deadline > 60:
            failures += 1
            emit(json.dumps({'event': 'missed_slot', 'slot': slot}))
            continue
        try:
            result = invoke((deadline-INTERVAL)*1000, deadline*1000)
            ok = result.get('ok') is True
        except Exception:
            # Never print provider responses, DSNs, or exception strings.
            result, ok = {'error': 'COLLECTION_FAILED_OR_COMMIT_UNKNOWN'}, False
        failures += not ok
        emit(json.dumps({'event': 'observation', 'slot': slot, 'ok': ok,
                         'result': result}))
        if not ok:
            emit(json.dumps({'event': 'stopped_after_failure', 'slot': slot}))
            return 1
    emit(json.dumps({'event': 'complete', 'failed_or_missed_slots': failures}))
    return int(bool(failures))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--account', required=True)
    p.add_argument('--start-utc-seconds', required=True, type=int,
                   help='Explicit prospective start, at most 15 minutes ahead')
    p.add_argument('--write', action='store_true')
    p.add_argument('--env-file', type=Path)
    a = p.parse_args()
    if not re.fullmatch(r'0x[0-9a-fA-F]{40}', a.account):
        p.error('invalid public account address')
    if not time.time() <= a.start_utc_seconds <= time.time()+900:
        p.error('start must be prospective and within 15 minutes')
    env = os.environ.copy()
    if a.env_file:
        from dotenv import dotenv_values
        value = dotenv_values(a.env_file).get('DATABASE_URL')
        if value:
            env['DATABASE_URL'] = value
    if a.write and (env.get('EXECUTION_EVIDENCE_ENABLED') != 'true'
                    or not env.get('DATABASE_URL')):
        p.error('writes require explicit EXECUTION_EVIDENCE_ENABLED=true and DATABASE_URL')
    env['HYPERLIQUID_ACCOUNT_ADDRESS'] = a.account

    def invoke(start, end):
        cmd = [sys.executable, str(ROOT/'scripts/collect_execution_evidence.py'),
               '--network', 'testnet', '--start-ms', str(start),
               '--end-ms', str(end), '--account-evidence']
        if a.write:
            cmd.append('--write')
        process = subprocess.run(cmd, env=env, capture_output=True, text=True,
                                 timeout=60, cwd=ROOT)
        if process.returncode:
            raise RuntimeError('collector failed')
        result = json.loads(process.stdout)
        # Allowlist counts, never relay arbitrary subprocess output.
        keys = ('ok', 'mode', 'fills', 'order_links', 'unlinked_fills',
                'instrument_mismatches', 'unresolved_links', 'funding_events',
                'position_snapshot_captured', 'training_eligible')
        return {k: result[k] for k in keys if k in result}

    return run(a.start_utc_seconds, invoke)


if __name__ == '__main__':
    raise SystemExit(main())

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('pilot', Path(__file__).resolve().parents[1]/'scripts/execution_evidence_pilot.py')
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


def test_96_windows_and_expiry():
    clock = [1000]
    calls = []
    def sleep(seconds):
        assert seconds <= 30
        clock[0] += seconds
    def invoke(start, end):
        calls.append((start, end))
        return {'ok': True}
    assert pilot.run(1000, invoke, now=lambda: clock[0], sleep=sleep, emit=lambda _: None) == 0
    assert len(calls) == 96
    assert calls[0] == (1000000, 1900000)
    assert calls[-1][1] == (1000+86400)*1000
    assert all(a[1] == b[0] for a,b in zip(calls,calls[1:]))


def test_expired_does_not_fetch():
    assert pilot.run(1000, lambda *_: (_ for _ in ()).throw(AssertionError()),
                     now=lambda: 1000+86461, emit=lambda _: None) == 1


def test_failure_stops_without_retry_or_secret_output():
    logs, calls = [], []
    def fail(*args):
        calls.append(args)
        raise RuntimeError('secret-database-url')
    assert pilot.run(1000, fail, now=lambda: 1900, emit=logs.append) == 1
    assert len(calls) == 1
    assert 'secret' not in ''.join(logs)


def test_missed_slot_is_reported_not_backfilled():
    clock = [1961]
    logs, calls = [], []
    def sleep(s): clock[0] += s
    def invoke(*args):
        calls.append(args)
        return {'ok': False}
    assert pilot.run(1000, invoke, now=lambda: clock[0], sleep=sleep, emit=logs.append) == 1
    assert calls == [(1900000,2800000)]
    assert 'missed_slot' in logs[0]

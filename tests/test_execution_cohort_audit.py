from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import execution_cohort_audit as module
from execution_evidence import EvidenceError


def args():
    return dict(boundaries=[dict(signal_id=str(uuid4()),opening_run_id=str(uuid4()),
                                closing_run_id=str(uuid4()))],environment='research',
                network='testnet',account_hash='a'*64,
                observed_from=datetime(2026,9,1,tzinfo=timezone.utc),
                observed_before=datetime(2026,9,2,tzinfo=timezone.utc))


class Result:
    def __init__(self, rows): self.rows=rows
    def mappings(self): return self
    def scalars(self): return self
    def all(self): return self.rows


class Connection:
    def __init__(self, scope, links): self.results=iter([scope,links])
    def execute(self,*_): return Result(next(self.results))


@pytest.mark.parametrize('bad', [[], [{}], [{'signal_id':'invalid'}]])
def test_invalid_cohort_never_loads(bad):
    with pytest.raises(EvidenceError):
        module.audit_cohort(None,**{**args(),'boundaries':bad})


@pytest.mark.parametrize('case', ['outside_window','wrong_account','missing_candidate','audit_error'])
def test_exclusions_fail_closed(monkeypatch,case):
    a=args(); sid=a['boundaries'][0]['signal_id']; calls=[]
    loaded={'candidates':[{'signal_id':sid,'order_id':'1'}],'excluded':[]}
    if case=='missing_candidate':
        loaded={'candidates':[],'excluded':[{'signal_id':sid,'reason':'NO_SCOPED_PERSISTED_ORDER_IDENTITY'}]}
    monkeypatch.setattr(module,'load_candidates',lambda *_,**kw:loaded)
    def audit(*_,**kw):
        calls.append(kw)
        raise EvidenceError('MISSING_BOUNDARY_SNAPSHOT')
    monkeypatch.setattr(module,'load_and_audit',audit)
    scope=[dict(network='testnet',account_hash='a'*64)]*2
    if case=='wrong_account': scope=[dict(network='testnet',account_hash='b'*64)]*2
    c=Connection(scope,['1','2'] if case=='outside_window' else ['1'])
    result=module.audit_cohort(c,**a)
    assert not result['reports'] and len(result['excluded'])==1
    assert not result['training_eligible'] and not result['attribution_verified']
    assert bool(calls)==(case=='audit_error')

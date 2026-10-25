from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
import execution_pilot_cloud as pilot

START = 1800000000
ENV = {'VERCEL_ENV': 'production', 'EXECUTION_PILOT_ENABLED': 'true',
       'EXECUTION_PILOT_START_UTC_SECONDS': str(START),
       'EXECUTION_PILOT_TESTNET_ACCOUNT': '0x'+'1'*40}


@pytest.mark.parametrize('delta', [0,899,1021,86400+121,87300])
def test_outside_window(delta):
    with pytest.raises(ValueError, match='OUTSIDE'):
        pilot.configuration(ENV,START+delta)


def test_all_96_slots_and_stable_identity():
    identities = set()
    for n in range(1,97):
        account,start,slot,identity = pilot.configuration(ENV,START+n*900+60)
        assert slot == n
        identities.add(identity)
    assert len(identities)==1


@pytest.mark.parametrize('key,value', [('VERCEL_ENV','preview'),
    ('EXECUTION_PILOT_ENABLED','false'),('EXECUTION_PILOT_TESTNET_ACCOUNT','bad'),
    ('EXECUTION_PILOT_START_UTC_SECONDS','not-a-number'),
    ('EXECUTION_PILOT_START_UTC_SECONDS',str(START+1))])
def test_config_fail_closed(key,value):
    with pytest.raises(ValueError):
        pilot.configuration({**ENV,key:value},START+960)


class Session:
    def __init__(self, rows=(), locked=True):
        self.rows=list(rows); self.locked=locked; self.inserts=[]
    def execute(self, sql, params):
        q=str(sql)
        if q.startswith('INSERT'): self.inserts.append(params)
        return self
    def scalar_one(self): return self.locked
    def all(self): return self.rows


def test_claim_first_and_duplicate():
    s=Session()
    assert pilot.claim(s,'pilot',1,'run')
    assert len(s.inserts)==1
    s=Session([(1,'COMPLETE')])
    assert not pilot.claim(s,'pilot',1,'run')
    assert not s.inserts


@pytest.mark.parametrize('rows', [[],[(1,'STARTED')],[(1,'FAILED')]])
def test_missing_or_unknown_previous_blocks(rows):
    s=Session(rows)
    with pytest.raises(ValueError,match='REQUIRES_REVIEW'):
        pilot.claim(s,'pilot',2,'run')
    assert not s.inserts


def test_concurrent_claim_busy():
    with pytest.raises(ValueError,match='BUSY'):
        pilot.claim(Session(locked=False),'pilot',1,'run')


def test_endpoint_auth_and_no_caller_scope(monkeypatch):
    import app, auth
    monkeypatch.setattr(auth,'enforcement_enabled',lambda:True)
    monkeypatch.setenv('EXECUTION_PILOT_TOKEN','pilot-test-token')
    c=app.app.test_client()
    path='/api/research/execution-pilot/tick'
    headers={'x-execution-pilot-token':'pilot-test-token'}
    assert c.post(path).status_code==401
    assert c.post(path,headers=headers,json={'network':'mainnet'}).status_code==400
    monkeypatch.setattr(pilot,'tick',lambda:{'ok':True,'slot':1})
    r=c.post(path,headers=headers)
    assert r.status_code==200
    assert r.headers['Cache-Control']=='no-store'
    monkeypatch.delenv('EXECUTION_PILOT_TOKEN')
    assert c.post(path,headers=headers).status_code==401

import pytest
from test_execution_evidence import ACCOUNT, NOW, END, collect
from execution_evidence import EvidenceError
from execution_account_evidence import collect_account_evidence, normalize_funding, normalize_positions


def funding(**delta):
    return {'time':END-500, 'hash':'0x'+'a'*64,
            'delta':{'type':'funding','coin':'BTC','usdc':'-0.01',
                     'szi':'0.5','fundingRate':'0.0001','nSamples':None,**delta}}


def position(**updates):
    return {'time':END, 'assetPositions':[{'position':{'coin':'BTC','szi':'0.5'}}], **updates}


def supplement(rows=None, snapshot=None):
    rows = [funding()] if rows is None else rows
    snapshot = position() if snapshot is None else snapshot
    def read(body):
        assert body['type'] in ('userFunding','clearinghouseState')
        if body['type']=='userFunding':
            assert body['endTime']==END
            return rows
        assert body['dex']==''
        return snapshot
    return collect_account_evidence(collect([]), ACCOUNT, read=read, clock=lambda:NOW)


def test_capture_preserves_sign_and_deduplicates():
    batch = supplement([funding(),funding()])
    assert len(batch['funding'])==1
    assert batch['funding'][0]['payload']['usdc']=='-0.01'
    o = batch['account_observation']
    assert o['positions']=={'BTC':'0.5'}
    assert o['venue_time_ms']==END
    assert not o['training_eligible'] and not o['funding_history_complete']


def test_shared_hash_different_assets_kept():
    assert len(supplement([funding(),funding(coin='ETH')])['funding'])==2


def test_changed_amount_fails_closed():
    with pytest.raises(EvidenceError,match='CONFLICTING_FUNDING'):
        supplement([funding(),funding(usdc='0.2')])


@pytest.mark.parametrize('rows',[{},[funding()]*500])
def test_invalid_or_capped_response_rejected(rows):
    with pytest.raises(EvidenceError,match='SATURATED_OR_INVALID'):
        supplement(rows)


@pytest.mark.parametrize('delta',[{'usdc':'NaN'},{'type':'deposit'},{'szi':None},
                                 {'fundingRate':True},{'coin':''},{'nSamples':-1}])
def test_invalid_funding_rejected(delta):
    with pytest.raises(EvidenceError): supplement([funding(**delta)])


def test_bad_hash_and_window_rejected():
    for row in ({**funding(),'hash':'invalid'},{**funding(),'time':END+1}):
        with pytest.raises(EvidenceError): normalize_funding(row,END-1000,END)


@pytest.mark.parametrize('at',[END+1, END-60001, 0])
def test_stale_future_position_rejected(at):
    with pytest.raises(EvidenceError,match='STALE_OR_FUTURE'):
        supplement(snapshot=position(time=at))


def test_empty_positions_observed_not_inferred_from_fills():
    b = supplement([],position(assetPositions=[]))
    assert b['account_observation']['positions']=={}
    assert b['account_observation']['funding_count']==0
    assert not b['account_observation']['funding_history_complete']


def test_position_payload_omits_pnl():
    response = position(assetPositions=[{'position':{
        'coin':'BTC','szi':'-0.5','unrealizedPnl':'123','entryPx':'100'}}])
    assert normalize_positions(response,NOW)['positions']=={'BTC':'-0.5'}


def test_duplicate_position_and_other_dex_rejected():
    for rows in ([{'position':{'coin':'BTC','szi':'1'}}]*2,
                 [{'position':{'coin':'xyz:ABC','szi':'1'}}]):
        with pytest.raises(EvidenceError): supplement(snapshot=position(assetPositions=rows))


def test_wrong_account_rejected_before_network():
    with pytest.raises(EvidenceError,match='MISMATCH'):
        collect_account_evidence(collect([]),'0x'+'2'*40,
            read=lambda body:pytest.fail('unexpected network call'),clock=lambda:NOW)


def test_current_snapshot_is_not_backdated_to_historical_window():
    batch = collect([])
    batch.update(start_ms=END-10000, end_ms=END-5000)
    def read(body):
        return [] if body['type']=='userFunding' else position()
    result = collect_account_evidence(batch, ACCOUNT, read=read,clock=lambda:NOW)
    assert result['account_observation']['venue_time_ms'] > result['end_ms']
    assert result['account_observation']['venue_time_ms'] == END

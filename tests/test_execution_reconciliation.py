import pytest
from test_execution_evidence import fill, END
from execution_evidence import EvidenceError
from execution_reconciliation import audit_position_chain


def audit(rows, opening='0', closing='0'):
    return audit_position_chain(instrument='BTC', start_ms=END-1000,
        end_ms=END, opening_position=opening, closing_position=closing, fills=rows)


@pytest.mark.parametrize('side,exit_side,position', [('B','A','1'), ('A','B','-1')])
def test_long_short_partial_exit(side, exit_side, position):
    half = '0.5' if position == '1' else '-0.5'
    rows = [fill(tid=1, time=END-900, side=side, sz='1', startPosition='0'),
            fill(tid=2, time=END-500, side=exit_side, sz='0.5', startPosition=position),
            fill(tid=3, time=END-100, side=exit_side, sz='0.5', startPosition=half)]
    result = audit(rows)
    assert result['position_chain_consistent']
    assert not result['training_eligible']
    assert 'FUNDING_RECONCILIATION_REQUIRED' in result['quarantine_reasons']


def test_exact_duplicates_do_not_double_count():
    result = audit([fill(), fill()], closing='0.5')
    assert result['position_chain_consistent']
    assert result['fills'] == 1 and result['duplicates'] == 1


def test_missing_fill_and_endpoint_mismatch():
    result = audit([fill(startPosition='1')], closing='0')
    assert result['position_issues'] == ['CLOSING_POSITION_MISMATCH', 'POSITION_CHAIN_GAP']


def test_equal_timestamps_are_not_ordered_by_id():
    result = audit([fill(), fill(tid=2, side='A', startPosition='0.5')])
    assert result['position_issues'] == ['AMBIGUOUS_FILL_ORDER']


def test_zero_net_missing_round_trip_is_not_certified():
    result = audit([])
    assert result['position_chain_consistent']
    assert not result['training_eligible']
    assert 'INDEPENDENT_HISTORY_COVERAGE_REQUIRED' in result['quarantine_reasons']


@pytest.mark.parametrize('bad', [{'coin':'ETH'}, {'time':END-1000}, {'time':END+1}])
def test_wrong_instrument_or_boundary_rejected(bad):
    with pytest.raises(EvidenceError): audit([fill(**bad)])


def test_conflicting_duplicate_rejected():
    with pytest.raises(EvidenceError, match='CONFLICTING_EXECUTION_ID'):
        audit([fill(), fill(sz='0.6')])


def test_non_quote_fee_requires_conversion():
    result = audit([fill(feeToken='HYPE')], closing='0.5')
    assert 'FEE_CURRENCY_CONVERSION_REQUIRED' in result['position_issues']

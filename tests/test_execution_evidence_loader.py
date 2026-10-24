from datetime import timedelta
import pytest
from test_execution_evidence import NOW, END
from execution_evidence_loader import publication_timing


@pytest.mark.parametrize('outcome,message,offset,expected',[
    ('ACCEPTED',1,-1,True),('ACCEPTED',0,-1,False),
    ('REJECTED',1,-1,False),('UNKNOWN',None,-1,False),
    ('ACCEPTED',1,0,False),('ACCEPTED',1,1,False)])
def test_receipt_before_fill_is_strict(outcome,message,offset,expected):
    r={'attempt_id':'a','outcome':outcome,'provider_message_id':message,
       'request_started_at':NOW-timedelta(seconds=2),
       'response_received_at':NOW+timedelta(seconds=offset)}
    result=publication_timing([r],END)
    assert result['recorded_publication_before_fill'] is expected
    assert not result['cross_clock_verified']


def test_reversed_receipt_time_rejected():
    r={'attempt_id':'a','outcome':'ACCEPTED','provider_message_id':1,
       'request_started_at':NOW,'response_received_at':NOW-timedelta(seconds=1)}
    assert not publication_timing([r],END)['recorded_publication_before_fill']
